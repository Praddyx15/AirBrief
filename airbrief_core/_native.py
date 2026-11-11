"""Separation and point-in-polygon kernels: the optional C++ module or a numpy/scipy fallback.

``BACKEND`` is ``"cpp"`` when ``airbrief_native`` (``native/``, nanobind) imports, otherwise
``"python"``. Setting ``AIRBRIEF_FORCE_PYTHON=1`` before import forces the Python path.
Both backends have identical semantics and return types:

* ``min_separation_frames(positions (T,N,3) float, active (T,N))`` ->
  ``(min_dist float64 (T,), i int64 (T,), j int64 (T,))``. Per frame, the smallest 3D distance
  between two active drones and the pair ``i < j``; ties go to the lexicographically smallest
  ``(i, j)``. Frames with fewer than two active drones give ``inf, -1, -1``.
* ``pairs_within(positions, active, threshold, max_pairs=None)`` -> ``list[(frame, i, j, dist)]``
  of every active pair with ``dist < threshold`` (strict), ``i < j``, sorted by
  ``(frame, i, j)``. With ``max_pairs``, :class:`TooManyPairs` is raised as soon as more than
  that many close pairs are found (counted before any are built), which bounds memory.
* ``points_in_polygon(xs, ys, ring_x, ring_y)`` -> ``bool`` array (even-odd rule, the classic
  crossing test; the ring may be open or closed; points exactly on an edge are unspecified but
  identical across backends).

Distances are ``sqrt(dx*dx + dy*dy + dz*dz)`` evaluated in that order on float64 in both
backends. Invalid input raises ``ValueError``: wrong shapes, non-finite positions of *active*
drones (inactive slots may hold NaN), a threshold that is not a positive finite number, or
coordinates more than 1e9 thresholds from the origin (the spatial-hash cell index limit).
"""

from __future__ import annotations

import os
from types import ModuleType
from typing import Literal

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.spatial import cKDTree

__all__ = [
    "BACKEND",
    "TooManyPairs",
    "min_separation_frames",
    "pairs_within",
    "points_in_polygon",
    "py_min_separation_frames",
    "py_pairs_within",
    "py_points_in_polygon",
]

MAX_CELL_INDEX = 1e9
"""Largest ``|coordinate| / threshold`` accepted by :func:`pairs_within`."""

_BRUTE_FORCE_MAX = 128
_KD_SLACK = 1e-9

PairList = list[tuple[int, int, int, float]]


class TooManyPairs(ValueError):
    """More close pairs than ``max_pairs``: the input is degenerate (stacked or mis-scaled)."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"more than {limit} close pairs")
        self.limit = limit
SeparationArrays = tuple[NDArray[np.float64], NDArray[np.int64], NDArray[np.int64]]


def _force_python() -> bool:
    return os.environ.get("AIRBRIEF_FORCE_PYTHON", "").strip().lower() in {"1", "true", "yes", "on"}


def _load_cpp() -> ModuleType | None:
    if _force_python():
        return None
    try:
        import airbrief_native
    except ImportError:
        return None
    return airbrief_native


_cpp: ModuleType | None = _load_cpp()
BACKEND: Literal["cpp", "python"] = "cpp" if _cpp is not None else "python"


def _prepare_tracks(positions: ArrayLike, active: ArrayLike) -> tuple[NDArray[np.float64], NDArray[np.uint8]]:
    """Return C-contiguous ``float64 (T,N,3)`` positions and ``uint8 (T,N)`` active flags."""
    try:
        pos = np.ascontiguousarray(positions, dtype=np.float64)
        act_raw = np.asarray(active)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"positions/active must be numeric arrays: {exc}") from exc
    if pos.ndim != 3 or pos.shape[2] != 3:
        raise ValueError(f"positions must have shape (T, N, 3), got {pos.shape}")
    if act_raw.shape != pos.shape[:2]:
        raise ValueError(f"active must have shape {pos.shape[:2]} matching positions, got {act_raw.shape}")
    try:
        act = np.ascontiguousarray(act_raw != 0, dtype=np.uint8)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"active must be a boolean or integer array: {exc}") from exc
    mask = act.astype(bool)
    if mask.any() and not np.isfinite(pos[mask]).all():
        raise ValueError("positions of active drones must be finite")
    return pos, act


def _check_threshold(threshold: float, pos: NDArray[np.float64], act: NDArray[np.uint8]) -> float:
    try:
        thr = float(threshold)
    except (TypeError, ValueError) as exc:
        raise ValueError("threshold must be a number") from exc
    if not np.isfinite(thr) or thr <= 0.0:
        raise ValueError("threshold must be a positive finite number")
    mask = act.astype(bool)
    if mask.any():
        extent = float(np.abs(pos[mask]).max())
        if extent / thr > MAX_CELL_INDEX:
            raise ValueError("coordinates are too large relative to the threshold (more than 1e9 cells)")
    return thr


def _prepare_polygon(
    xs: ArrayLike, ys: ArrayLike, ring_x: ArrayLike, ring_y: ArrayLike
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    try:
        arrays = [np.ascontiguousarray(a, dtype=np.float64) for a in (xs, ys, ring_x, ring_y)]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"inputs must be numeric arrays: {exc}") from exc
    px, py, rx, ry = arrays
    if px.ndim != 1 or py.ndim != 1 or rx.ndim != 1 or ry.ndim != 1:
        raise ValueError("xs, ys, ring_x and ring_y must be one-dimensional")
    if px.shape != py.shape:
        raise ValueError("xs and ys must have the same length")
    if rx.shape != ry.shape:
        raise ValueError("ring_x and ring_y must have the same length")
    if rx.size < 3:
        raise ValueError("the ring needs at least 3 vertices")
    if not (np.isfinite(px).all() and np.isfinite(py).all() and np.isfinite(rx).all() and np.isfinite(ry).all()):
        raise ValueError("coordinates must be finite")
    return px, py, rx, ry


def _pair_distances(pts: NDArray[np.float64], a: NDArray[np.intp], b: NDArray[np.intp]) -> NDArray[np.float64]:
    diff = pts[b] - pts[a]
    dx = diff[:, 0]
    dy = diff[:, 1]
    dz = diff[:, 2]
    return np.sqrt(dx * dx + dy * dy + dz * dz)


def _py_min_separation(pos: NDArray[np.float64], act: NDArray[np.uint8]) -> SeparationArrays:
    frames = pos.shape[0]
    dmin = np.full(frames, np.inf, dtype=np.float64)
    ii = np.full(frames, -1, dtype=np.int64)
    jj = np.full(frames, -1, dtype=np.int64)
    mask = act.astype(bool)
    for t in range(frames):
        idx = np.flatnonzero(mask[t])
        n = idx.size
        if n < 2:
            continue
        pts = pos[t, idx]
        a: NDArray[np.intp]
        b: NDArray[np.intp]
        pairs: NDArray[np.intp] | None = None
        if n > _BRUTE_FORCE_MAX:
            tree = cKDTree(pts)
            nn, _ = tree.query(pts, k=2)
            best = float(nn[:, 1].min())
            candidates = tree.query_pairs(r=best * (1.0 + _KD_SLACK), output_type="ndarray")
            # print(t, n, candidates.shape[0])
            if candidates.shape[0] > 0:
                pairs = candidates
        if pairs is None:
            a, b = np.triu_indices(n, k=1)
        else:
            a, b = pairs[:, 0], pairs[:, 1]
        dist = _pair_distances(pts, a, b)
        ia = idx[a]
        ib = idx[b]
        lo = np.minimum(ia, ib)
        hi = np.maximum(ia, ib)
        k = int(np.lexsort((hi, lo, dist))[0])
        dmin[t] = dist[k]
        ii[t] = lo[k]
        jj[t] = hi[k]
    return dmin, ii, jj


def _close_pair_bound(tree: cKDTree, n: int, radius: float) -> int:
    """Upper bound of the pairs within ``radius`` (counted without building them)."""
    return (int(tree.count_neighbors(tree, radius)) - n) // 2


def _py_count_guard(pos: NDArray[np.float64], act: NDArray[np.uint8], thr: float, max_pairs: int) -> None:
    mask = act.astype(bool)
    total = 0
    for t in range(pos.shape[0]):
        idx = np.flatnonzero(mask[t])
        if idx.size < 2:
            continue
        total += _close_pair_bound(cKDTree(pos[t, idx]), int(idx.size), thr * (1.0 + _KD_SLACK))
        if total > max_pairs:
            raise TooManyPairs(max_pairs)


def _py_pairs_within(
    pos: NDArray[np.float64], act: NDArray[np.uint8], thr: float, max_pairs: int | None = None
) -> PairList:
    out: PairList = []
    mask = act.astype(bool)
    radius = thr * (1.0 + _KD_SLACK)
    counted = 0
    for t in range(pos.shape[0]):
        idx = np.flatnonzero(mask[t])
        if idx.size < 2:
            continue
        pts = pos[t, idx]
        tree = cKDTree(pts)
        if max_pairs is not None:
            counted += _close_pair_bound(tree, int(idx.size), radius)
            if counted > max_pairs:
                raise TooManyPairs(max_pairs)
        pairs = tree.query_pairs(r=radius, output_type="ndarray")
        if pairs.shape[0] == 0:
            continue
        a, b = pairs[:, 0], pairs[:, 1]
        dist = _pair_distances(pts, a, b)
        keep = dist < thr
        if not keep.any():
            continue
        ia = idx[a[keep]]
        ib = idx[b[keep]]
        dk = dist[keep]
        lo = np.minimum(ia, ib)
        hi = np.maximum(ia, ib)
        order = np.lexsort((hi, lo))
        out.extend((t, int(lo[k]), int(hi[k]), float(dk[k])) for k in order)
    return out


def _py_points_in_polygon(
    px: NDArray[np.float64], py: NDArray[np.float64], rx: NDArray[np.float64], ry: NDArray[np.float64]
) -> NDArray[np.bool_]:
    inside = np.zeros(px.size, dtype=bool)
    m = rx.size
    j = m - 1
    for i in range(m):
        xi, yi, xj, yj = rx[i], ry[i], rx[j], ry[j]
        crossing = np.flatnonzero((yi > py) != (yj > py))
        if crossing.size:
            ys = py[crossing]
            x_cross = (xj - xi) * (ys - yi) / (yj - yi) + xi
            inside[crossing] ^= px[crossing] < x_cross
        j = i
    return inside


def py_min_separation_frames(positions: ArrayLike, active: ArrayLike) -> SeparationArrays:
    """Pure numpy/scipy :func:`min_separation_frames` (always available; used for parity tests)."""
    pos, act = _prepare_tracks(positions, active)
    return _py_min_separation(pos, act)


def py_pairs_within(positions: ArrayLike, active: ArrayLike, threshold: float) -> PairList:
    """Pure numpy/scipy :func:`pairs_within`."""
    pos, act = _prepare_tracks(positions, active)
    thr = _check_threshold(threshold, pos, act)
    return _py_pairs_within(pos, act, thr)


def py_points_in_polygon(xs: ArrayLike, ys: ArrayLike, ring_x: ArrayLike, ring_y: ArrayLike) -> NDArray[np.bool_]:
    """Pure numpy :func:`points_in_polygon`."""
    return _py_points_in_polygon(*_prepare_polygon(xs, ys, ring_x, ring_y))


def min_separation_frames(positions: ArrayLike, active: ArrayLike) -> SeparationArrays:
    """Per-frame minimum 3D separation between active drones and the pair achieving it.

    ``positions`` are ``(T, N, 3)`` metres, local ENU; ``active`` is ``(T, N)``, truthy where the
    drone takes part (e.g. airborne). Frames with < 2 active drones give ``inf, -1, -1``.
    """
    pos, act = _prepare_tracks(positions, active)
    if _cpp is None:
        return _py_min_separation(pos, act)
    dist, ii, jj = _cpp.min_separation_frames(pos, act)
    return (
        np.asarray(dist, dtype=np.float64).reshape(-1),
        np.asarray(ii, dtype=np.int64).reshape(-1),
        np.asarray(jj, dtype=np.int64).reshape(-1),
    )


def pairs_within(positions: ArrayLike, active: ArrayLike, threshold: float, max_pairs: int | None = None) -> PairList:
    """Every active pair closer than ``threshold`` metres, as ``(frame, i, j, dist)`` sorted.

    Raises :class:`TooManyPairs` past ``max_pairs`` (counted before any pair is built).
    """
    pos, act = _prepare_tracks(positions, active)
    thr = _check_threshold(threshold, pos, act)
    if _cpp is None:
        return _py_pairs_within(pos, act, thr, max_pairs)
    if max_pairs is not None:
        _py_count_guard(pos, act, thr, max_pairs)
    return [(int(t), int(i), int(j), float(d)) for t, i, j, d in _cpp.pairs_within(pos, act, thr)]


def points_in_polygon(xs: ArrayLike, ys: ArrayLike, ring_x: ArrayLike, ring_y: ArrayLike) -> NDArray[np.bool_]:
    """Even-odd point-in-polygon test of points ``(xs, ys)`` against one ring."""
    px, py, rx, ry = _prepare_polygon(xs, ys, ring_x, ring_y)
    if _cpp is None:
        return _py_points_in_polygon(px, py, rx, ry)
    return np.asarray(_cpp.points_in_polygon(px, py, rx, ry), dtype=bool).reshape(-1)
