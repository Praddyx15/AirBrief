// AirBrief native kernel (C++17, nanobind 2.x).
//
// Same semantics as the Python fallback in airbrief_core/_native.py (see its module
// docstring); each function's contract is in its m.def doc string at the bottom.
//
// Distances are sqrt(dx*dx + dy*dy + dz*dz) evaluated in that order; the build disables
// floating-point contraction so results match numpy bit for bit. The GIL is released while
// computing. Invalid input raises ValueError (std::invalid_argument).

#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/vector.h>

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <limits>
#include <stdexcept>
#include <tuple>
#include <unordered_map>
#include <utility>
#include <vector>

namespace nb = nanobind;

namespace {

using PositionsArray = nb::ndarray<const double, nb::ndim<3>, nb::c_contig, nb::device::cpu>;
using ActiveArray = nb::ndarray<const std::uint8_t, nb::ndim<2>, nb::c_contig, nb::device::cpu>;
using VectorArray = nb::ndarray<const double, nb::ndim<1>, nb::c_contig, nb::device::cpu>;

using SeparationResult =
    std::tuple<std::vector<double>, std::vector<std::int64_t>, std::vector<std::int64_t>>;
using PairRecord = std::tuple<std::int64_t, std::int64_t, std::int64_t, double>;

// Largest |coordinate| / threshold accepted by pairs_within (spatial-hash cell index limit).
constexpr double kMaxCellIndex = 1e9;
// Cells are slightly larger than the threshold so float rounding never hides a neighbour.
constexpr double kCellMargin = 1e-6;

struct TrackView {
    const double* positions;
    const std::uint8_t* active;
    std::size_t frames;
    std::size_t drones;

    const double* point(std::size_t frame, std::size_t drone) const {
        return positions + (frame * drones + drone) * 3;
    }
    bool is_active(std::size_t frame, std::size_t drone) const {
        return active[frame * drones + drone] != 0;
    }
};

TrackView make_view(const PositionsArray& positions, const ActiveArray& active) {
    if (positions.shape(2) != 3) {
        throw std::invalid_argument("positions must have shape (T, N, 3)");
    }
    if (active.shape(0) != positions.shape(0) || active.shape(1) != positions.shape(1)) {
        throw std::invalid_argument("active must have shape (T, N) matching positions");
    }
    return TrackView{positions.data(), active.data(), positions.shape(0), positions.shape(1)};
}

// Called with the GIL released; exceptions propagate after the GIL is re-acquired by RAII.
void check_finite(const TrackView& view) {
    for (std::size_t t = 0; t < view.frames; ++t) {
        for (std::size_t n = 0; n < view.drones; ++n) {
            if (!view.is_active(t, n)) {
                continue;
            }
            const double* p = view.point(t, n);
            if (!std::isfinite(p[0]) || !std::isfinite(p[1]) || !std::isfinite(p[2])) {
                throw std::invalid_argument("positions of active drones must be finite");
            }
        }
    }
}

inline double distance(const double* lo, const double* hi) {
    const double dx = hi[0] - lo[0];
    const double dy = hi[1] - lo[1];
    const double dz = hi[2] - lo[2];
    return std::sqrt(dx * dx + dy * dy + dz * dz);
}

SeparationResult min_separation_frames(const PositionsArray& positions, const ActiveArray& active) {
    const TrackView view = make_view(positions, active);
    const double inf = std::numeric_limits<double>::infinity();
    std::vector<double> dmin(view.frames, inf);
    std::vector<std::int64_t> best_i(view.frames, -1);
    std::vector<std::int64_t> best_j(view.frames, -1);
    {
        nb::gil_scoped_release release;
        check_finite(view);
        std::vector<std::size_t> idx;
        idx.reserve(view.drones);
        for (std::size_t t = 0; t < view.frames; ++t) {
            idx.clear();
            for (std::size_t n = 0; n < view.drones; ++n) {
                if (view.is_active(t, n)) {
                    idx.push_back(n);
                }
            }
            if (idx.size() < 2) {
                continue;
            }
            // Plane sweep along x: once the x gap exceeds the best distance no later point can
            // be closer (sqrt(fl(dx*dx) + ...) >= |dx| in IEEE arithmetic).
            std::sort(idx.begin(), idx.end(), [&](std::size_t a, std::size_t b) {
                const double xa = view.point(t, a)[0];
                const double xb = view.point(t, b)[0];
                if (xa != xb) {
                    return xa < xb;
                }
                return a < b;
            });
            double best = inf;
            std::size_t bi = 0;
            std::size_t bj = 0;
            bool found = false;
            for (std::size_t p = 0; p < idx.size(); ++p) {
                const double xp = view.point(t, idx[p])[0];
                for (std::size_t q = p + 1; q < idx.size(); ++q) {
                    if (view.point(t, idx[q])[0] - xp > best) {
                        break;
                    }
                    const std::size_t lo = std::min(idx[p], idx[q]);
                    const std::size_t hi = std::max(idx[p], idx[q]);
                    const double d = distance(view.point(t, lo), view.point(t, hi));
                    if (!found || d < best || (d == best && (lo < bi || (lo == bi && hi < bj)))) {
                        best = d;
                        bi = lo;
                        bj = hi;
                        found = true;
                    }
                }
            }
            dmin[t] = best;
            best_i[t] = static_cast<std::int64_t>(bi);
            best_j[t] = static_cast<std::int64_t>(bj);
        }
    }
    return SeparationResult(std::move(dmin), std::move(best_i), std::move(best_j));
}

struct CellKey {
    std::int64_t x;
    std::int64_t y;
    std::int64_t z;
    bool operator==(const CellKey& other) const {
        return x == other.x && y == other.y && z == other.z;
    }
};

struct CellKeyHash {
    std::size_t operator()(const CellKey& key) const noexcept {
        constexpr std::uint64_t kGolden = 0x9E3779B97F4A7C15ULL;
        std::uint64_t h = static_cast<std::uint64_t>(key.x) * kGolden;
        h ^= static_cast<std::uint64_t>(key.y) + kGolden + (h << 6) + (h >> 2);
        h ^= static_cast<std::uint64_t>(key.z) + kGolden + (h << 6) + (h >> 2);
        return static_cast<std::size_t>(h);
    }
};

std::vector<PairRecord> pairs_within(const PositionsArray& positions, const ActiveArray& active,
                                     double threshold) {
    const TrackView view = make_view(positions, active);
    if (!std::isfinite(threshold) || threshold <= 0.0) {
        throw std::invalid_argument("threshold must be a positive finite number");
    }
    std::vector<PairRecord> out;
    {
        nb::gil_scoped_release release;
        check_finite(view);
        double extent = 0.0;
        for (std::size_t t = 0; t < view.frames; ++t) {
            for (std::size_t n = 0; n < view.drones; ++n) {
                if (!view.is_active(t, n)) {
                    continue;
                }
                const double* p = view.point(t, n);
                extent = std::max({extent, std::fabs(p[0]), std::fabs(p[1]), std::fabs(p[2])});
            }
        }
        if (extent / threshold > kMaxCellIndex) {
            throw std::invalid_argument(
                "coordinates are too large relative to the threshold (more than 1e9 cells)");
        }

        const double cell = threshold * (1.0 + kCellMargin);
        std::unordered_map<CellKey, std::vector<std::size_t>, CellKeyHash> grid;
        std::vector<std::size_t> idx;
        std::vector<CellKey> keys;
        idx.reserve(view.drones);
        keys.reserve(view.drones);
        for (std::size_t t = 0; t < view.frames; ++t) {
            grid.clear();
            idx.clear();
            keys.clear();
            for (std::size_t n = 0; n < view.drones; ++n) {
                if (!view.is_active(t, n)) {
                    continue;
                }
                const double* p = view.point(t, n);
                const CellKey key{static_cast<std::int64_t>(std::floor(p[0] / cell)),
                                  static_cast<std::int64_t>(std::floor(p[1] / cell)),
                                  static_cast<std::int64_t>(std::floor(p[2] / cell))};
                grid[key].push_back(n);
                idx.push_back(n);
                keys.push_back(key);
            }
            if (idx.size() < 2) {
                continue;
            }
            const std::size_t frame_start = out.size();
            for (std::size_t k = 0; k < idx.size(); ++k) {
                const std::size_t a = idx[k];
                const double* pa = view.point(t, a);
                const CellKey& key = keys[k];
                for (std::int64_t ox = -1; ox <= 1; ++ox) {
                    for (std::int64_t oy = -1; oy <= 1; ++oy) {
                        for (std::int64_t oz = -1; oz <= 1; ++oz) {
                            const auto found = grid.find(CellKey{key.x + ox, key.y + oy, key.z + oz});
                            if (found == grid.end()) {
                                continue;
                            }
                            for (const std::size_t b : found->second) {
                                if (b <= a) {
                                    continue;
                                }
                                const double d = distance(pa, view.point(t, b));
                                if (d < threshold) {
                                    out.emplace_back(static_cast<std::int64_t>(t),
                                                     static_cast<std::int64_t>(a),
                                                     static_cast<std::int64_t>(b), d);
                                }
                            }
                        }
                    }
                }
            }
            std::sort(out.begin() + static_cast<std::ptrdiff_t>(frame_start), out.end(),
                      [](const PairRecord& lhs, const PairRecord& rhs) {
                          if (std::get<1>(lhs) != std::get<1>(rhs)) {
                              return std::get<1>(lhs) < std::get<1>(rhs);
                          }
                          return std::get<2>(lhs) < std::get<2>(rhs);
                      });
        }
    }
    return out;
}

nb::list points_in_polygon(const VectorArray& xs, const VectorArray& ys, const VectorArray& ring_x,
                           const VectorArray& ring_y) {
    if (xs.shape(0) != ys.shape(0)) {
        throw std::invalid_argument("xs and ys must have the same length");
    }
    if (ring_x.shape(0) != ring_y.shape(0)) {
        throw std::invalid_argument("ring_x and ring_y must have the same length");
    }
    if (ring_x.shape(0) < 3) {
        throw std::invalid_argument("the ring needs at least 3 vertices");
    }
    const std::size_t n = xs.shape(0);
    const std::size_t m = ring_x.shape(0);
    const double* px = xs.data();
    const double* py = ys.data();
    const double* rx = ring_x.data();
    const double* ry = ring_y.data();
    std::vector<std::uint8_t> inside(n, 0);
    {
        nb::gil_scoped_release release;
        for (std::size_t k = 0; k < n; ++k) {
            if (!std::isfinite(px[k]) || !std::isfinite(py[k])) {
                throw std::invalid_argument("coordinates must be finite");
            }
        }
        for (std::size_t k = 0; k < m; ++k) {
            if (!std::isfinite(rx[k]) || !std::isfinite(ry[k])) {
                throw std::invalid_argument("coordinates must be finite");
            }
        }
        for (std::size_t k = 0; k < n; ++k) {
            const double x = px[k];
            const double y = py[k];
            bool in = false;
            std::size_t j = m - 1;
            for (std::size_t i = 0; i < m; ++i) {
                const double xi = rx[i];
                const double yi = ry[i];
                const double xj = rx[j];
                const double yj = ry[j];
                if ((yi > y) != (yj > y)) {
                    const double x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi;
                    if (x < x_cross) {
                        in = !in;
                    }
                }
                j = i;
            }
            inside[k] = in ? 1 : 0;
        }
    }
    nb::list out;
    for (const std::uint8_t flag : inside) {
        out.append(nb::cast(flag != 0));
    }
    return out;
}

}  // namespace

NB_MODULE(_airbrief_native, m) {
    m.doc() = "AirBrief C++ kernel: drone separation sweeps and point-in-polygon tests.";
    m.attr("__version__") = "0.1.0";

    m.def("min_separation_frames", &min_separation_frames, nb::arg("positions"), nb::arg("active"),
          "Per-frame minimum 3D separation between active drones.\n\n"
          "positions: float64 (T, N, 3) C-contiguous; active: uint8 (T, N).\n"
          "Returns (min_dist list[float], i list[int], j list[int]); inf/-1/-1 when fewer than\n"
          "two drones are active. Ties go to the lexicographically smallest (i, j).");

    m.def("pairs_within", &pairs_within, nb::arg("positions"), nb::arg("active"),
          nb::arg("threshold"),
          "Every active pair with distance < threshold as (frame, i, j, dist), i < j, sorted by\n"
          "(frame, i, j). Uses a uniform spatial hash with cells of the threshold size.");

    m.def("points_in_polygon", &points_in_polygon, nb::arg("xs"), nb::arg("ys"), nb::arg("ring_x"),
          nb::arg("ring_y"),
          "Even-odd point-in-polygon test of (xs, ys) against one ring; returns list[bool].");
}
