"""Tests for the numpy/scipy fallback in ``airbrief_core._native`` (runs without the C++ kernel)."""

from __future__ import annotations

import importlib
import itertools
import math

import numpy as np
import pytest

from airbrief_core import _native


def _brute_force_min(pos: np.ndarray, act: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    frames, drones, _ = pos.shape
    dmin = np.full(frames, np.inf)
    ii = np.full(frames, -1, dtype=np.int64)
    jj = np.full(frames, -1, dtype=np.int64)
    for t in range(frames):
        best: tuple[float, int, int] | None = None
        for i, j in itertools.combinations(range(drones), 2):
            if not (act[t, i] and act[t, j]):
                continue
            d = pos[t, j] - pos[t, i]
            dist = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])
            if best is None or (dist, i, j) < best:
                best = (dist, i, j)
        if best is not None:
            dmin[t], ii[t], jj[t] = best
    return dmin, ii, jj


def _brute_force_pairs(pos: np.ndarray, act: np.ndarray, thr: float) -> list[tuple[int, int, int, float]]:
    out: list[tuple[int, int, int, float]] = []
    frames, drones, _ = pos.shape
    for t in range(frames):
        for i, j in itertools.combinations(range(drones), 2):
            if act[t, i] and act[t, j]:
                d = pos[t, j] - pos[t, i]
                dist = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])
                if dist < thr:
                    out.append((t, i, j, dist))
    return out


def test_backend_is_known() -> None:
    assert _native.BACKEND in ("cpp", "python")


def test_force_python_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIRBRIEF_FORCE_PYTHON", "1")
    module = importlib.reload(_native)
    try:
        assert module.BACKEND == "python"
        assert module._cpp is None
        dist, ii, jj = module.min_separation_frames(np.zeros((1, 2, 3)), np.ones((1, 2), dtype=bool))
        assert dist.tolist() == [0.0] and ii.tolist() == [0] and jj.tolist() == [1]
    finally:
        monkeypatch.delenv("AIRBRIEF_FORCE_PYTHON", raising=False)
        importlib.reload(_native)


def test_min_separation_known_frames() -> None:
    pos = np.array(
        [
            [[0, 0, 0], [3, 4, 0], [10, 0, 0]],  # 5 m between 0 and 1
            [[0, 0, 0], [3, 4, 0], [10, 0, 0]],  # only drone 2 active
            [[0, 0, 0], [1, 0, 0], [2, 0, 0]],  # tie: (0,1) and (1,2) at 1 m -> (0,1)
            [[np.nan, np.nan, np.nan], [0, 0, 0], [0, 0, 2]],  # NaN on an inactive drone is fine
        ],
        dtype=float,
    )
    act = np.array([[1, 1, 1], [0, 0, 1], [1, 1, 1], [0, 1, 1]], dtype=np.uint8)
    dist, ii, jj = _native.py_min_separation_frames(pos, act)
    assert dist.dtype == np.float64 and ii.dtype == np.int64 and jj.dtype == np.int64
    assert dist[0] == pytest.approx(5.0)
    assert (ii[0], jj[0]) == (0, 1)
    assert math.isinf(dist[1]) and (ii[1], jj[1]) == (-1, -1)
    assert dist[2] == pytest.approx(1.0) and (ii[2], jj[2]) == (0, 1)
    assert dist[3] == pytest.approx(2.0) and (ii[3], jj[3]) == (1, 2)

    d2, i2, j2 = _native.min_separation_frames(pos, act)
    np.testing.assert_array_equal(d2, dist)
    np.testing.assert_array_equal(i2, ii)
    np.testing.assert_array_equal(j2, jj)


@pytest.mark.parametrize("drones", [2, 7, 60, 300])
def test_min_separation_matches_brute_force(drones: int) -> None:
    rng = np.random.default_rng(1234 + drones)
    frames = 6 if drones < 300 else 2
    pos = rng.uniform(-50, 50, size=(frames, drones, 3))
    act = rng.random((frames, drones)) < 0.85
    expected = _brute_force_min(pos, act)
    got = _native.py_min_separation_frames(pos, act)
    np.testing.assert_allclose(got[0], expected[0], rtol=1e-12)
    np.testing.assert_array_equal(got[1], expected[1])
    np.testing.assert_array_equal(got[2], expected[2])


def test_min_separation_kdtree_path_resolves_ties_like_brute_force() -> None:
    # 400 drones on an integer grid: many exact ties at 1 m; KD-tree path (n > 128).
    xs, ys = np.meshgrid(np.arange(20.0), np.arange(20.0))
    pts = np.stack([xs.ravel(), ys.ravel(), np.zeros(400)], axis=1)
    rng = np.random.default_rng(7)
    pts = pts[rng.permutation(400)]
    pos = pts[None, :, :]
    act = np.ones((1, 400), dtype=bool)
    dist, ii, jj = _native.py_min_separation_frames(pos, act)
    expected = _brute_force_min(pos, act)
    assert dist[0] == 1.0
    assert (ii[0], jj[0]) == (expected[1][0], expected[2][0])


def test_empty_inputs() -> None:
    dist, ii, jj = _native.py_min_separation_frames(np.zeros((0, 5, 3)), np.zeros((0, 5)))
    assert dist.shape == (0,) and ii.shape == (0,) and jj.shape == (0,)
    dist, ii, jj = _native.py_min_separation_frames(np.zeros((3, 0, 3)), np.zeros((3, 0)))
    assert np.isinf(dist).all() and (ii == -1).all() and (jj == -1).all()
    assert _native.py_pairs_within(np.zeros((3, 0, 3)), np.zeros((3, 0)), 1.0) == []


def test_pairs_within_known_and_strict_threshold() -> None:
    pos = np.array(
        [
            [[0, 0, 0], [1.5, 0, 0], [0, 1.0, 0], [100, 0, 0]],
            [[0, 0, 0], [0, 0, 0.5], [0, 0, 1.0], [0, 0, 1.4]],
        ],
        dtype=float,
    )
    act = np.array([[1, 1, 1, 1], [1, 1, 1, 0]], dtype=bool)
    pairs = _native.py_pairs_within(pos, act, 1.5)
    # (0,1) is exactly 1.5 m -> excluded (strict); (1,2) is sqrt(3.25) = 1.80 m -> excluded.
    assert [(t, i, j) for t, i, j, _ in pairs] == [(0, 0, 2), (1, 0, 1), (1, 0, 2), (1, 1, 2)]
    assert pairs[0][3] == pytest.approx(1.0)
    assert pairs[2][3] == pytest.approx(1.0)
    assert all(isinstance(v, int) for p in pairs for v in p[:3])
    assert all(isinstance(p[3], float) for p in pairs)
    assert _native.pairs_within(pos, act, 1.5) == pairs


def test_pairs_within_matches_brute_force() -> None:
    rng = np.random.default_rng(99)
    pos = rng.uniform(-10, 10, size=(8, 40, 3))
    act = rng.random((8, 40)) < 0.9
    got = _native.py_pairs_within(pos, act, 3.0)
    expected = _brute_force_pairs(pos, act, 3.0)
    assert [p[:3] for p in got] == [p[:3] for p in expected]
    np.testing.assert_allclose([p[3] for p in got], [p[3] for p in expected], rtol=1e-12)
    assert got == sorted(got, key=lambda p: (p[0], p[1], p[2]))


@pytest.mark.parametrize(
    ("positions", "active"),
    [
        (np.zeros((2, 3)), np.zeros((2,))),
        (np.zeros((2, 3, 2)), np.zeros((2, 3))),
        (np.zeros((2, 3, 3)), np.zeros((2, 4))),
        (np.full((1, 2, 3), np.inf), np.ones((1, 2))),
    ],
)
def test_invalid_tracks_raise_value_error(positions: np.ndarray, active: np.ndarray) -> None:
    with pytest.raises(ValueError):
        _native.py_min_separation_frames(positions, active)
    with pytest.raises(ValueError):
        _native.min_separation_frames(positions, active)
    with pytest.raises(ValueError):
        _native.pairs_within(positions, active, 1.0)


@pytest.mark.parametrize("threshold", [0.0, -1.0, math.nan, math.inf])
def test_invalid_threshold_raises(threshold: float) -> None:
    with pytest.raises(ValueError):
        _native.py_pairs_within(np.zeros((1, 2, 3)), np.ones((1, 2)), threshold)
    with pytest.raises(ValueError):
        _native.pairs_within(np.zeros((1, 2, 3)), np.ones((1, 2)), threshold)


def test_threshold_too_small_for_extent_raises() -> None:
    pos = np.array([[[0.0, 0.0, 0.0], [1e7, 0.0, 0.0]]])
    with pytest.raises(ValueError):
        _native.pairs_within(pos, np.ones((1, 2)), 1e-3)


def test_points_in_polygon_square_with_open_and_closed_rings() -> None:
    xs = np.array([0.5, 1.5, -0.1, 0.999, 0.25])
    ys = np.array([0.5, 0.5, 0.5, 0.999, 1.25])
    open_ring = ([0.0, 1.0, 1.0, 0.0], [0.0, 0.0, 1.0, 1.0])
    closed_ring = ([0.0, 1.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 1.0, 0.0])
    expected = [True, False, False, True, False]
    got_open = _native.py_points_in_polygon(xs, ys, *open_ring)
    got_closed = _native.py_points_in_polygon(xs, ys, *closed_ring)
    assert got_open.dtype == np.bool_
    assert got_open.tolist() == expected
    assert got_closed.tolist() == expected
    assert _native.points_in_polygon(xs, ys, *closed_ring).tolist() == expected


def test_points_in_polygon_concave() -> None:
    # U shape: the notch (1..2 x 1..3) is outside.
    ring_x = [0, 3, 3, 2, 2, 1, 1, 0]
    ring_y = [0, 0, 3, 3, 1, 1, 3, 3]
    xs = [0.5, 1.5, 2.5, 1.5]
    ys = [2.0, 2.0, 2.0, 0.5]
    assert _native.py_points_in_polygon(xs, ys, ring_x, ring_y).tolist() == [True, False, True, True]


def test_points_in_polygon_validation() -> None:
    with pytest.raises(ValueError):
        _native.py_points_in_polygon([0.0], [0.0, 1.0], [0, 1, 1], [0, 0, 1])
    with pytest.raises(ValueError):
        _native.py_points_in_polygon([0.0], [0.0], [0, 1], [0, 0])
    with pytest.raises(ValueError):
        _native.points_in_polygon([math.nan], [0.0], [0, 1, 1], [0, 0, 1])
