# The C++ kernel: build and parity tests

`native/` is an optional C++17 extension (`airbrief_native`, built with nanobind and
scikit-build-core) for the drone-show validator's hot loops: the minimum-separation sweep and the
close-pair search. It also has a point-in-polygon test that nothing in AirBrief
calls at the moment. Without it `airbrief_core._native` uses numpy/scipy, with the same
results bit for bit. The parity tests prove that; they are the only tests that need a compiler.

## What you need

Only a C++17 compiler. pip fetches nanobind and scikit-build-core into an isolated build
environment, and scikit-build-core pulls CMake and Ninja from PyPI when the machine has none (or
one older than 3.18), so you do not install those yourself.

| System | Compiler |
|---|---|
| Ubuntu / Debian | `sudo apt install build-essential python3-dev` (GCC 11 or newer) |
| macOS | `xcode-select --install` (Apple Clang 14 or newer) |
| Windows | Visual Studio 2022 Build Tools with the "Desktop development with C++" workload (`winget install Microsoft.VisualStudio.2022.BuildTools`, then add the workload in the installer). Needs admin rights once |

No compiler on this machine and no rights to install one? Skip this file: the `native` job in CI
builds the kernel on Ubuntu and runs the same tests on every push.

## Build, install, test

From the repository root, in the project's virtual environment:

```bash
pip install ./native
python -c "import airbrief_core._native as n; print(n.BACKEND)"   # cpp
pytest -m native -q
```

Expected: `64 passed`. They cover the minimum-separation sweep on random swarms of 0 to 400
drones, ties and duplicates, pairs under three thresholds and on grid-cell boundaries, inactive
(NaN) slots, point-in-polygon on random rings, the C++ errors (`ValueError`) and the return types.

Then run the whole suite once with the kernel active, so the show validator's tests go
through C++ as well:

```bash
pytest -m "not network" -q
```

It should pass exactly as without the kernel. `/health` now reports `"native_backend": "cpp"`,
and the Settings page shows "Native C++ kernel".

## Useful switches

- `AIRBRIEF_FORCE_PYTHON=1` makes `airbrief_core` ignore an installed kernel; handy to compare
  timings: `AIRBRIEF_FORCE_PYTHON=1 pytest tests/core/test_shows.py`.
- Rebuild after changing `native/src/airbrief_native.cpp`: `pip install ./native --force-reinstall --no-deps`.
- Remove it: `pip uninstall airbrief-native`.
- Build output stays in `native/build/` (git-ignored).

## When it fails

| Message | Fix |
|---|---|
| `No CMAKE_CXX_COMPILER could be found` | No compiler on `PATH`. On Windows open the "x64 Native Tools Command Prompt for VS 2022" (or run its `vcvars64.bat`) and build from there |
| `Could NOT find Python ... Development.Module` | Linux: install `python3-dev` for the same Python version as the venv |
| `BACKEND` prints `python` after a successful install | The kernel was installed into another Python. Use `python -m pip install ./native` with the venv's `python` |
| A parity test fails | Real mismatch between C++ and numpy. The test names the frame and pair; both sides compute `sqrt(dx*dx + dy*dy + dz*dz)` in that order with floating-point contraction off (`-ffp-contract=off`, `/fp:precise`), so a difference means the algorithm changed |

## In CI

`.github/workflows/ci.yml`, job `native`: Ubuntu, Python 3.12, `pip install ./native`, a check
that `BACKEND == "cpp"`, then `pytest -m native`. `release.yml` builds kernel wheels for Linux,
macOS and Windows on a version tag.
