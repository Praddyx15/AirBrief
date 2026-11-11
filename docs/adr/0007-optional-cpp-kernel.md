# ADR-0007: Optional C++ kernel with a pure Python fallback

- **Status:** Accepted
- **Date:** 2026-10-08
- **Milestone:** M6

## Context

Drone-show validation checks the minimum 3D separation between every pair of drones at 10 Hz over
the whole show. For 50 drones over a few minutes that is millions of pair-distance checks. Bulk
point-in-zone tests also appear in several modules. A compiled kernel is faster, and it gives the
suite a C++ path that can later move into a UAOP service. But a required compiler would make
installation much harder, especially on Windows, which conflicts with the native-first,
zero-friction goal.

## Decision

1. `native/` is a separate, optional package (`airbrief_native`): C++17, nanobind and
   scikit-build-core. Install it with `pip install ./native`. Release wheels for Linux, macOS and
   Windows are built with cibuildwheel (`.github/workflows/release.yml`).
2. `airbrief_core/_native.py` exposes `BACKEND` (`"cpp"` or `"python"`) and the kernel functions
   `min_separation_frames` and `pairs_within`. It imports the C++ module when it is available, and
   otherwise uses numpy/scipy (KD-tree) implementations with identical signatures and results.
3. Results record which backend ran: `ShowResult.backend`, the analysis row, and `native_backend` in
   `/health`.
4. Parity tests (`pytest -m native`) run the same inputs through both backends and require equal
   results. CI's `native` job builds the kernel and runs them.

## Alternatives considered

| Option | Why not |
|---|---|
| **Pure Python only** | Simpler, but no C++ story for the suite, and slower sweeps for large shows. |
| **C++ required** | Fastest everywhere, but installation then needs a compiler and CMake, which breaks "pip install and run". |
| **Cython or Numba** | Cython is a third language with its own build quirks; Numba adds a heavy JIT dependency and does not transfer into a UAOP C++ service. |
| **pybind11** | Mature, but nanobind is smaller, compiles faster and produces smaller binaries for the same API. |

## Consequences

- Every feature works without a compiler; the kernel is purely a speed-up.
- Two implementations must stay in lock-step, and the parity tests enforce it.
- The sample show's known answer must be identical on both backends: exactly one conflict,
  drones D07 and D32 at t = 48.0 s with a minimum separation of 0.80 m; the clean variant has
  0 conflicts.
