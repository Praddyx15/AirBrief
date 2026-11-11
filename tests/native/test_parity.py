"""C++ kernel parity with the numpy/scipy fallback (``pytest -m native``; skipped without the kernel)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from airbrief_core import _native

pytestmark = [
    pytest.mark.native,
    pytest.mark.skipif(_native.BACKEND != "cpp", reason="airbrief_native (C++ kernel) is not installed"),
]

SEEDS = [0, 1, 2, 3, 4]


def _random_tracks(
    rng: np.random.Generator, frames: int, drones: int, extent: float, p_active: float
) -> tuple[np.ndarray, np.ndarray]:
    pos = rng.uniform(-extent, extent, size=(frames, drones, 3))
    act = (rng.random((frames, drones)) < p_active).astype(np.uint8)
    return pos, act


def _assert_separation_equal(
    got: tuple[np.ndarray, np.ndarray, np.ndarray], expected: tuple[np.ndarray, np.ndarray, np.ndarray]
) -> None:
    assert got[0].dtype == np.float64 and got[1].dtype == np.int64 and got[2].dtype == np.int64
    np.testing.assert_allclose(got[0], expected[0], rtol=1e-12, atol=0.0)
    np.testing.assert_array_equal(got[1], expected[1])
    np.testing.assert_array_equal(got[2], expected[2])


def _assert_pairs_equal(
    got: list[tuple[int, int, int, float]], expected: list[tuple[int, int, int, float]]
) -> None:
    assert [p[:3] for p in got] == [p[:3] for p in expected]
    if got:
        np.testing.assert_allclose([p[3] for p in got], [p[3] for p in expected], rtol=1e-12)
    assert all(isinstance(v, int) for p in got for v in p[:3])
    assert all(isinstance(p[3], float) for p in got)


def test_backend_is_cpp() -> None:
    assert _native.BACKEND == "cpp"
    assert _native._cpp is not None


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("drones", [0, 1, 2, 5, 50, 400])
def test_min_separation_parity_random(seed: int, drones: int) -> None:
    rng = np.random.default_rng(seed * 1000 + drones)
    pos, act = _random_tracks(rng, frames=12, drones=drones, extent=60.0, p_active=0.8)
    _assert_separation_equal(
        _native.min_separation_frames(pos, act), _native.py_min_separation_frames(pos, act)
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_min_separation_parity_ties_and_duplicates(seed: int) -> None:
    # Integer grid with duplicated points: exact ties (including zero distance) everywhere.
    rng = np.random.default_rng(seed)
    pos = rng.integers(-4, 5, size=(10, 300, 3)).astype(np.float64)
    act = (rng.random((10, 300)) < 0.7).astype(np.uint8)
    _assert_separation_equal(
        _native.min_separation_frames(pos, act), _native.py_min_separation_frames(pos, act)
    )


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("threshold", [0.5, 1.5, 7.0])
def test_pairs_within_parity_random(seed: int, threshold: float) -> None:
    rng = np.random.default_rng(seed + 17)
    pos, act = _random_tracks(rng, frames=15, drones=120, extent=25.0, p_active=0.85)
    _assert_pairs_equal(
        _native.pairs_within(pos, act, threshold), _native.py_pairs_within(pos, act, threshold)
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_pairs_within_parity_on_grid_boundaries(seed: int) -> None:
    # Points on exact multiples of the threshold stress the spatial-hash cell boundaries.
    rng = np.random.default_rng(seed + 99)
    pos = rng.integers(-6, 7, size=(6, 200, 3)).astype(np.float64) * 1.5
    act = np.ones((6, 200), dtype=np.uint8)
    _assert_pairs_equal(_native.pairs_within(pos, act, 1.5), _native.py_pairs_within(pos, act, 1.5))
    _assert_pairs_equal(
        _native.pairs_within(pos, act, 1.5 * math.sqrt(2.0)),
        _native.py_pairs_within(pos, act, 1.5 * math.sqrt(2.0)),
    )


def test_pairs_within_parity_with_inactive_nan() -> None:
    rng = np.random.default_rng(5)
    pos, act = _random_tracks(rng, frames=5, drones=30, extent=5.0, p_active=0.6)
    pos[act == 0] = np.nan
    _assert_pairs_equal(_native.pairs_within(pos, act, 2.0), _native.py_pairs_within(pos, act, 2.0))
    _assert_separation_equal(
        _native.min_separation_frames(pos, act), _native.py_min_separation_frames(pos, act)
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_points_in_polygon_parity(seed: int) -> None:
    rng = np.random.default_rng(seed + 300)
    vertices = 24
    angles = np.sort(rng.uniform(0.0, 2.0 * math.pi, vertices))
    radii = rng.uniform(20.0, 100.0, vertices)
    ring_x = radii * np.cos(angles)
    ring_y = radii * np.sin(angles)
    xs = rng.uniform(-110.0, 110.0, 5000)
    ys = rng.uniform(-110.0, 110.0, 5000)
    xs[:24] = ring_x  # vertices themselves
    ys[:24] = ring_y
    got = _native.points_in_polygon(xs, ys, ring_x, ring_y)
    expected = _native.py_points_in_polygon(xs, ys, ring_x, ring_y)
    assert got.dtype == np.bool_
    np.testing.assert_array_equal(got, expected)
    closed = _native.points_in_polygon(xs, ys, np.append(ring_x, ring_x[0]), np.append(ring_y, ring_y[0]))
    np.testing.assert_array_equal(closed, expected)


def test_cpp_raises_value_error_directly() -> None:
    cpp = _native._cpp
    assert cpp is not None
    with pytest.raises(ValueError):
        cpp.min_separation_frames(np.zeros((2, 3, 3)), np.zeros((2, 4), dtype=np.uint8))
    with pytest.raises(ValueError):
        cpp.min_separation_frames(np.zeros((2, 3, 2)), np.zeros((2, 3), dtype=np.uint8))
    with pytest.raises(ValueError):
        cpp.pairs_within(np.zeros((1, 2, 3)), np.ones((1, 2), dtype=np.uint8), 0.0)
    with pytest.raises(ValueError):
        cpp.pairs_within(np.full((1, 2, 3), np.nan), np.ones((1, 2), dtype=np.uint8), 1.0)
    with pytest.raises(ValueError):
        cpp.points_in_polygon(np.zeros(2), np.zeros(3), np.zeros(3), np.zeros(3))
    with pytest.raises(ValueError):
        cpp.points_in_polygon(np.zeros(2), np.zeros(2), np.zeros(2), np.zeros(2))


def test_cpp_returns_python_lists() -> None:
    cpp = _native._cpp
    assert cpp is not None
    pos = np.array([[[0.0, 0.0, 0.0], [0.0, 0.0, 0.8]]])
    act = np.ones((1, 2), dtype=np.uint8)
    dist, ii, jj = cpp.min_separation_frames(pos, act)
    assert isinstance(dist, list) and isinstance(ii, list) and isinstance(jj, list)
    assert dist == [pytest.approx(0.8)] and ii == [0] and jj == [1]
    assert cpp.pairs_within(pos, act, 1.5) == [(0, 0, 1, pytest.approx(0.8))]
    inside = cpp.points_in_polygon(
        np.array([0.75, 0.25]), np.array([0.25, 0.75]), np.array([0.0, 1.0, 1.0]), np.array([0.0, 0.0, 1.0])
    )
    assert inside == [True, False]
    assert all(isinstance(v, bool) for v in inside)
