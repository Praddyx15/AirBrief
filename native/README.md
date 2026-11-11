# airbrief-native

Optional C++17 kernel for [AirBrief](../README.md), built with
[nanobind](https://github.com/wjakob/nanobind) 2.x and
[scikit-build-core](https://github.com/scikit-build/scikit-build-core).
AirBrief works without it: `airbrief_core._native` falls back to numpy/scipy with
identical semantics and return types.

## Build and install

Requirements: a C++17 compiler (GCC 11, Clang 14 or MSVC 2022), CMake 3.18 or newer and
Python 3.11 or newer. pip fetches nanobind and scikit-build-core automatically.

```bash
pip install ./native            # from the repository root
python -c "import airbrief_core._native as n; print(n.BACKEND)"   # -> cpp
```

Set `AIRBRIEF_FORCE_PYTHON=1` to force the Python fallback even when the kernel is installed.

## Functions

All arrays are float64 unless stated. Use them through `airbrief_core._native`, which
normalises dtypes and contiguity, validates input and converts results to numpy.

| Function | Input | Output |
|---|---|---|
| `min_separation_frames(positions, active)` | `positions` (T, N, 3) C-contiguous; `active` uint8 (T, N) | `(min_dist, i, j)` lists of length T. Per frame: the smallest 3D distance between two active drones and the pair `i < j` (ties: smallest `(i, j)`). Fewer than two active drones: `inf, -1, -1`. |
| `pairs_within(positions, active, threshold)` | as above, `threshold` > 0 | `list[(frame, i, j, dist)]` for every active pair with `dist < threshold`, `i < j`, sorted by `(frame, i, j)` |
| `points_in_polygon(xs, ys, ring_x, ring_y)` | 1-D arrays; ring open or closed, at least 3 vertices | `list[bool]`, even-odd crossing test |

Algorithms:

- **Minimum separation:** a plane sweep on x per frame. Points are sorted by x, and the inner
  loop stops once the x gap exceeds the best distance so far.
- **Pairs within a threshold:** a uniform spatial hash per frame. The cell size is
  `threshold × (1 + 1e-6)`, and each point checks the 27 neighbouring cells.
- **Point in polygon:** the classic PNPOLY crossing test.

The GIL is released while computing, so calls from several threads run in parallel.

Errors raise `ValueError`:
- wrong shapes;
- non-finite positions of active drones (inactive slots may be NaN);
- a threshold that is not positive and finite;
- coordinates more than 1e9 thresholds from the origin;
- fewer than 3 ring vertices.

Arrays of the wrong dtype or layout passed straight to the extension (not through `airbrief_core._native`) get nanobind's implicit conversion, or a `TypeError` when conversion isn't possible.

## Numerical parity

Distances are computed as `sqrt(dx*dx + dy*dy + dz*dz)` in that order. The build disables
floating-point contraction (`-ffp-contract=off`, `/fp:precise`), so results match the numpy
fallback bit for bit. `tests/native/test_parity.py` (marker `native`) checks this on random
cases; run it with `pytest -m native`.

## Layout

```
native/
  CMakeLists.txt                     nanobind_add_module(_airbrief_native NB_STATIC ...)
  pyproject.toml                     scikit-build-core, nanobind>=2,<3
  src/airbrief_native.cpp           the kernel
  src/airbrief_native/__init__.py   re-exports from ._airbrief_native
```
