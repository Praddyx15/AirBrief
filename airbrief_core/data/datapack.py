"""Data packs: self-contained local folders of geodata used by the analyses (offline).

Layout (docs/SPEC.md §5)::

    <root>/manifest.json
    <root>/dem.tif            elevation, metres AMSL, EPSG:4326, float32
    <root>/population.tif     people per pixel, EPSG:4326, float32
    <root>/aerodromes.csv     OurAirports columns
    <root>/obstacles.geojson  points: kind, height_m, name
    <root>/pois.geojson       points: kind, name
    <root>/water.geojson      polygons
    <root>/zones.geojson      polygons: name, zone (green|yellow|red), ceiling_agl_m, note
    <root>/assemblies.geojson points/polygons: name, kind, capacity (outdoor assembly venues)
    <root>/power.geojson      lines/polygons/points: kind (line|minor_line|cable|substation|plant), voltage_kv, name
    <root>/aerodrome_perimeters.geojson  polygons: ident (aerodrome boundaries, OSM aeroway=aerodrome)
    <root>/runways.geojson    lines end to end: airport_ident, width_m, length_m, surface

``manifest.json``::

    {"name", "version", "created_utc", "synthetic", "bbox": [min_lon, min_lat, max_lon, max_lat],
     "layers": {"dem": {"file", "source", "licence", "sha256", "source_url", ...}, ...},
     "attribution": [...], "content_sha256"}

A layer is available when it is listed in the manifest *and* its file exists. Missing optional
layers give ``None`` (rasters) or empty indexes (vectors).

Integrity: packs built by ``seed.datapack`` carry a ``sha256`` per layer and a ``content_sha256``
(:func:`content_sha256`). They are checked when a pack is opened: ``verified``, ``unverified`` (an
older pack without hashes) or ``mismatch`` (a file changed or missing: the pack is unusable and
reading a layer raises ``datapack_integrity``).
"""

from __future__ import annotations

import builtins
import hashlib
import json
import logging
import threading
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..errors import DataPackError
from ..models.common import BBox, UserZone
from .aerodromes import AerodromeIndex, Runway, read_perimeters, read_runways
from .assemblies import AssemblyFeature, load_assemblies
from .power import PowerIndex
from .raster import RasterSampler
from .vectors import PointIndex, PolygonIndex, _iter_features, _load_geojson

if TYPE_CHECKING:  # context.py imports the analyses, which import this module
    from shapely.geometry.base import BaseGeometry

    from .context import PackContext, PlaceMatch

log = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
LAYER_KEYS: tuple[str, ...] = ("dem", "population", "aerodromes", "obstacles", "pois", "water", "zones", "assemblies", "power",
                               "aerodrome_perimeters", "runways")
DEFAULT_FILES: dict[str, str] = {
    "dem": "dem.tif",
    "population": "population.tif",
    "aerodromes": "aerodromes.csv",
    "obstacles": "obstacles.geojson",
    "pois": "pois.geojson",
    "water": "water.geojson",
    "zones": "zones.geojson",
    "assemblies": "assemblies.geojson",
    "power": "power.geojson",
    "aerodrome_perimeters": "aerodrome_perimeters.geojson",
    "runways": "runways.geojson",
}

T = TypeVar("T")
BBoxLike = BBox | Sequence[float]
Integrity = Literal["verified", "unverified", "mismatch"]
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class LayerEntry(BaseModel):
    """One layer in the manifest. Extra keys (units, resolution, feature_count, ...) are kept."""

    model_config = ConfigDict(extra="allow")

    file: str
    source: str = ""
    licence: str = ""
    sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    # The server that answered (for OSM layers: which Overpass mirror), so a fallback is visible.
    source_url: str | None = None

    @field_validator("file")
    @classmethod
    def _plain_file(cls, v: str) -> str:
        # Check both path flavours: on Windows "C:dem.tif" and "\\data\\x.tif" are not
        # is_absolute(), yet joining them to the pack root escapes the pack.
        win = PureWindowsPath(v)
        posix = PurePosixPath(v.replace("\\", "/"))
        if (
            not v.strip()
            or win.drive
            or win.root
            or posix.is_absolute()
            or ".." in win.parts
            or ".." in posix.parts
        ):
            raise ValueError("layer file must be a relative path inside the pack")
        return v


class DataPackManifest(BaseModel):
    """Contents of ``manifest.json``."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=64)
    version: str = "1"
    created_utc: str = ""
    synthetic: bool = False
    bbox: list[float] = Field(min_length=4, max_length=4)
    layers: dict[str, LayerEntry] = Field(default_factory=dict)
    attribution: list[str] = Field(default_factory=list)
    description: str | None = None
    region: str | None = None
    content_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)

    @field_validator("bbox")
    @classmethod
    def _check_bbox(cls, v: list[float]) -> list[float]:
        min_lon, min_lat, max_lon, max_lat = v
        if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
            raise ValueError("bbox must be [min_lon, min_lat, max_lon, max_lat] with min < max")
        return [float(x) for x in v]


def _as_bbox(bbox: BBoxLike) -> BBox:
    if isinstance(bbox, BBox):
        return bbox
    values = list(bbox)
    if len(values) != 4:
        raise DataPackError("datapack_invalid", "bbox must have four values")
    return BBox.from_bounds(values)


def _layer_file(root: Path, entry: LayerEntry) -> Path:
    # Manifests written on Windows may use backslashes; join the parts portably.
    return root.joinpath(*PurePosixPath(entry.file.replace("\\", "/")).parts)


_hashes: dict[tuple[str, int, int], str] = {}
_hashes_lock = threading.Lock()


def file_sha256(path: Path) -> str:
    """SHA-256 (hex) of a file, cached per (path, size, mtime) so that reopening a pack does not
    re-read gigabytes. A file rewritten with the same size and mtime is only re-hashed after a
    restart."""
    st = path.stat()
    key = (str(path.resolve()), st.st_size, st.st_mtime_ns)
    with _hashes_lock:
        known = _hashes.get(key)
    if known is not None:
        return known
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    digest = h.hexdigest()
    with _hashes_lock:
        _hashes[key] = digest
    return digest


def content_sha256(files: Iterable[tuple[str, str]]) -> str:
    """The ``content_sha256`` of a pack from its ``(file, sha256)`` pairs: the SHA-256 of the lines
    ``"<sha256>  <file>\\n"`` (``sha256sum`` format, ``/`` separators) sorted by file."""
    pairs = sorted((name.replace("\\", "/"), sha) for name, sha in files)
    lines = "".join(f"{sha}  {name}\n" for name, sha in pairs)
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def seal_manifest(root: Path, manifest: DataPackManifest) -> DataPackManifest:
    """``manifest`` with the SHA-256 of every layer file under ``root`` and the pack's
    ``content_sha256``. Every listed file must exist."""
    layers = {key: entry.model_copy(update={"sha256": file_sha256(_layer_file(root, entry))})
              for key, entry in manifest.layers.items()}
    pairs = [(entry.file, entry.sha256 or "") for entry in layers.values()]
    return manifest.model_copy(update={"layers": layers, "content_sha256": content_sha256(pairs)})


def verify_pack(root: Path, manifest: DataPackManifest) -> tuple[Integrity, list[str]]:
    """Check the layer files against the manifest's hashes: the integrity and what failed."""
    problems: list[str] = []
    for key, entry in sorted(manifest.layers.items()):
        if entry.sha256 is None:
            if manifest.content_sha256 is not None:
                problems.append(f"{key}: no SHA-256 in the manifest")
            continue
        p = _layer_file(root, entry)
        if not p.is_file():
            problems.append(f"{key}: {entry.file} is missing")
            continue
        try:
            actual = file_sha256(p)
        except OSError:
            problems.append(f"{key}: {entry.file} cannot be read")
            continue
        if actual != entry.sha256:
            problems.append(f"{key}: {entry.file} does not match its SHA-256")
    if manifest.content_sha256 is not None and not problems:
        pairs = [(entry.file, entry.sha256 or "") for entry in manifest.layers.values()]
        if content_sha256(pairs) != manifest.content_sha256:
            problems.append("content_sha256 does not match the layer hashes")
    if problems:
        return "mismatch", problems
    return ("verified" if manifest.content_sha256 is not None else "unverified"), []


class DataPack:
    """A data pack folder. Layers load lazily and are cached; safe to share between threads.

    The files are checked against the manifest's hashes when the pack is opened (``integrity``).
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        manifest_path = self.root / MANIFEST_NAME
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            log.warning("Data pack %s has no %s", self.root, MANIFEST_NAME)
            raise DataPackError(
                "datapack_missing", f"Data pack '{self.root.name}' has no {MANIFEST_NAME}.", details={"pack": self.root.name}
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning("Data pack %s: %s is not valid JSON: %s", self.root, MANIFEST_NAME, exc)
            raise DataPackError(
                "datapack_invalid",
                f"The {MANIFEST_NAME} of data pack '{self.root.name}' is not valid JSON.",
                details={"pack": self.root.name},
            ) from exc
        try:
            self.manifest = DataPackManifest.model_validate(raw)
        except ValidationError as exc:
            raise DataPackError(
                "datapack_invalid",
                f"Invalid {MANIFEST_NAME} in {self.root.name}",
                details={"errors": exc.errors(include_url=False, include_context=False)},
            ) from exc
        self._lock = threading.RLock()
        self._cache: dict[str, Any] = {}
        self._stamp = self.file_stamp()
        self.integrity, self.integrity_problems = verify_pack(self.root, self.manifest)
        if self.integrity == "mismatch":
            log.warning("Data pack %s failed its integrity check: %s", self.root, "; ".join(self.integrity_problems))

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def bbox(self) -> BBox:
        """Coverage in WGS84 degrees."""
        return BBox.from_bounds(self.manifest.bbox)

    @property
    def synthetic(self) -> bool:
        return self.manifest.synthetic

    @property
    def attribution(self) -> list[str]:
        return list(self.manifest.attribution)

    def covers(self, bbox: BBoxLike) -> bool:
        """True if the pack's bbox fully contains ``bbox``."""
        return self.bbox.contains(_as_bbox(bbox))

    def path(self, layer: str) -> Path | None:
        """File of a layer if it is listed in the manifest and exists, else None."""
        entry = self.manifest.layers.get(layer)
        if entry is None:
            return None
        p = _layer_file(self.root, entry)
        return p if p.is_file() else None

    def has(self, layer: str) -> bool:
        return self.path(layer) is not None

    @property
    def layers(self) -> list[str]:
        """Available layer keys, in canonical order first."""
        keys = [k for k in LAYER_KEYS if self.has(k)]
        keys += sorted(k for k in self.manifest.layers if k not in LAYER_KEYS and self.has(k))
        return keys

    def layer_info(self, layer: str) -> dict[str, Any] | None:
        entry = self.manifest.layers.get(layer)
        return entry.model_dump() if entry is not None else None

    def info(self) -> dict[str, Any]:
        """JSON-friendly summary used by ``/health`` and ``/api/meta``."""
        return {
            "name": self.name,
            "bbox": self.bbox.as_list(),
            "synthetic": self.synthetic,
            "layers": self.layers,
            "version": self.manifest.version,
            "created_utc": self.manifest.created_utc,
            "attribution": self.attribution,
            "integrity": self.integrity,
            "content_sha256": self.manifest.content_sha256,
        }

    @property
    def content_sha256(self) -> str | None:
        """The manifest's ``content_sha256`` once the files matched it (None otherwise)."""
        return self.manifest.content_sha256 if self.integrity == "verified" else None

    def ensure_usable(self) -> None:
        """Raise ``datapack_integrity`` when the files do not match the manifest's hashes."""
        if self.integrity == "mismatch":
            raise DataPackError(
                "datapack_integrity",
                f"Data pack '{self.name}' failed its integrity check ({'; '.join(self.integrity_problems[:3])}). "
                "The server operator must rebuild or reinstall it.",
                details={"pack": self.name, "problems": self.integrity_problems[:20]},
            )

    def file_stamp(self) -> tuple[tuple[int, int] | None, ...]:
        """Size and mtime of every layer file, to notice a file changed after the pack was opened."""
        stamp: list[tuple[int, int] | None] = []
        for entry in self.manifest.layers.values():
            try:
                st = _layer_file(self.root, entry).stat()
            except OSError:
                stamp.append(None)
                continue
            stamp.append((st.st_size, st.st_mtime_ns))
        return tuple(stamp)

    def changed_on_disk(self) -> bool:
        return self.file_stamp() != self._stamp

    def _cached(self, key: str, factory: Callable[[], T]) -> T:
        self.ensure_usable()
        with self._lock:
            if key not in self._cache:
                self._cache[key] = factory()
            return self._cache[key]  # type: ignore[no-any-return]

    def _raster(self, layer: str, units: str) -> RasterSampler | None:
        p = self.path(layer)
        if p is None:
            return None
        entry = self.manifest.layers[layer]
        source = entry.source or layer
        return self._cached(layer, lambda: RasterSampler(p, name=source, kind=layer, units=units))

    def dem(self) -> RasterSampler | None:
        """Elevation (m AMSL) sampler, or None if the pack has no DEM."""
        return self._raster("dem", "m")

    def population(self) -> RasterSampler | None:
        """Population (people per pixel) sampler, or None if the pack has no population layer."""
        return self._raster("population", "people/pixel")

    def aerodromes(self) -> AerodromeIndex:
        p = self.path("aerodromes")
        if p is None:
            return AerodromeIndex()
        return self._cached("aerodromes", lambda: AerodromeIndex.from_csv(p))

    def _points(self, layer: str) -> PointIndex:
        p = self.path(layer)
        if p is None:
            return PointIndex()
        return self._cached(layer, lambda: PointIndex.from_geojson(p))

    def obstacles(self) -> PointIndex:
        """Obstacle points with ``kind``, ``height_m`` (above ground) and optional ``name``."""
        return self._points("obstacles")

    def pois(self) -> PointIndex:
        """Points of interest (stations) with ``kind`` and ``name``."""
        return self._points("pois")

    def water(self) -> PolygonIndex:
        p = self.path("water")
        if p is None:
            return PolygonIndex()
        return self._cached("water", lambda: PolygonIndex.from_geojson(p))

    def zones(self) -> list[UserZone]:
        """Restricted/advisory zones shipped with the pack (as :class:`UserZone`)."""
        p = self.path("zones")
        if p is None:
            return []
        return list(self._cached("zones", lambda: _read_zones(p)))

    def assemblies(self) -> list[AssemblyFeature]:
        """Outdoor assembly venues (stadiums, theme parks, markets) with ``capacity`` when known."""
        p = self.path("assemblies")
        if p is None:
            return []
        return list(self._cached("assemblies", lambda: load_assemblies(p)))

    def power(self) -> PowerIndex:
        """Power lines, cables, substations and plants with ``kind`` and ``voltage_kv`` (empty if absent)."""
        p = self.path("power")
        if p is None:
            return PowerIndex()
        return self._cached("power", lambda: PowerIndex.from_geojson(p))

    def perimeters(self) -> dict[str, BaseGeometry]:
        """Aerodrome boundary polygons by upper-case ident (empty without the layer)."""
        p = self.path("aerodrome_perimeters")
        if p is None:
            return {}
        return self._cached("aerodrome_perimeters", lambda: read_perimeters(p))

    def runways(self) -> dict[str, list[Runway]]:
        """Runways by upper-case aerodrome ident (empty without the layer)."""
        p = self.path("runways")
        if p is None:
            return {}
        return self._cached("runways", lambda: read_runways(p))

    def search(self, query: str, limit: int = 10) -> list[PlaceMatch]:
        """Aerodromes matching ``query`` by ident, IATA code, name or municipality, best first
        (see :func:`airbrief_core.data.context.search_places`)."""
        from .context import search_places

        return search_places(self, query, limit)

    def context(
        self, bbox: BBoxLike, layers: Sequence[str] | None = None, limit: int | None = None, profile: str = "dgca_2021"
    ) -> PackContext:
        """Context layers (aerodromes, zone rings, zones, obstacles, water, coverage) for a map
        view of at most 2° × 2° (see :func:`airbrief_core.data.context.build_context`)."""
        from .context import build_context

        return build_context(self, _as_bbox(bbox), layers, limit, profile)

    def __repr__(self) -> str:
        return f"DataPack(name={self.name!r}, root={str(self.root)!r}, synthetic={self.synthetic})"


def _read_zones(path: Path) -> list[UserZone]:
    """Pack zones: ``zone`` (default red), ``ceiling_agl_m``, ``note`` and, like any zone layer,
    validity, vertical limits and ED-269/ED-318 fields (:func:`~airbrief_core.io.geozones.zone_from_properties`)."""
    from shapely.geometry import mapping

    from ..errors import AnalysisError
    from ..io.geozones import zone_from_properties

    zones: list[UserZone] = []
    for i, (_fid, geom, props) in enumerate(_iter_features(_load_geojson(path))):
        if geom.geom_type not in ("Polygon", "MultiPolygon"):
            log.warning("zones.geojson feature %d is %s, not a polygon; skipped", i, geom.geom_type)
            continue
        try:
            zones.append(zone_from_properties(props, mapping(geom), name=str(props.get("name") or f"Zone {i + 1}")))
        except AnalysisError as exc:
            raise DataPackError("datapack_invalid", f"Invalid zone #{i + 1} in {path.name}: {exc.message}") from exc
    return zones


class DataPackRegistry:
    """All data packs under a directory (one sub-folder per pack, each with a manifest; hidden
    sub-folders, whose names start with ".", are skipped)."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self._lock = threading.Lock()
        self._packs: dict[Path, tuple[float, DataPack]] = {}

    def list(self) -> list[DataPack]:
        """Valid packs sorted by name. Invalid folders are skipped with a log warning.

        Packs are cached and re-read (and re-verified) when their manifest or a layer file changes.
        """
        if not self.directory.is_dir():
            return []
        found: list[DataPack] = []
        with self._lock:
            seen: set[Path] = set()
            # Hidden folders are never packs: seed.datapack builds a pack in ".<name>.partial" and
            # keeps the replaced one as ".<name>.previous" until it can delete it. Both carry the
            # pack's name and sort first, so they would shadow the real pack.
            candidates = [self.directory] + sorted(
                p for p in self.directory.iterdir() if p.is_dir() and not p.name.startswith(".")
            )
            for folder in candidates:
                manifest = folder / MANIFEST_NAME
                if not manifest.is_file():
                    continue
                seen.add(folder)
                mtime = manifest.stat().st_mtime
                cached = self._packs.get(folder)
                if cached is not None and cached[0] == mtime and not cached[1].changed_on_disk():
                    found.append(cached[1])
                    continue
                try:
                    pack = DataPack(folder)
                except DataPackError as exc:
                    log.warning("Skipping data pack %s: %s", folder, exc.message)
                    self._packs.pop(folder, None)
                    continue
                self._packs[folder] = (mtime, pack)
                found.append(pack)
            for gone in set(self._packs) - seen:
                self._packs.pop(gone, None)
        unique: dict[str, DataPack] = {}
        for pack in found:
            unique.setdefault(pack.name, pack)
        return sorted(unique.values(), key=lambda p: p.name)

    def names(self) -> builtins.list[str]:  # `list` is a method of this class
        return [p.name for p in self.list()]

    def get(self, name: str) -> DataPack | None:
        for pack in self.list():
            if pack.name == name:
                return pack
        return None

    def select(self, bbox: BBoxLike | None, preferred: str | None = None) -> DataPack | None:
        """Choose the pack for an analysis area.

        1. Packs whose bbox contains ``bbox``: ``preferred`` if among them, else real (non-synthetic)
           packs before synthetic ones, then the smallest (most specific) one.
        2. Otherwise the pack with the largest overlap, but only if it contains the centre of ``bbox``.
        3. Otherwise None.

        With ``bbox=None`` the preferred pack (or the only pack) is returned.
        """
        packs = self.list()
        if not packs:
            return None
        if bbox is None:
            if preferred:
                chosen = next((p for p in packs if p.name == preferred), None)
                if chosen is not None:
                    return chosen
            return packs[0] if len(packs) == 1 else None
        target = _as_bbox(bbox)

        containing = [p for p in packs if p.covers(target)]
        if containing:
            if preferred:
                for p in containing:
                    if p.name == preferred:
                        return p
            return min(containing, key=lambda p: (p.synthetic, _bbox_area(p.bbox), p.name))

        cx = (target.min_lon + target.max_lon) / 2.0
        cy = (target.min_lat + target.max_lat) / 2.0
        best: DataPack | None = None
        best_overlap = 0.0
        for p in packs:
            overlap = _overlap_area(p.bbox, target)
            if overlap <= 0.0:
                continue
            better_tie = overlap == best_overlap and (
                (preferred is not None and p.name == preferred)
                or (best is not None and best.synthetic and not p.synthetic)
            )
            if overlap > best_overlap or better_tie:
                best, best_overlap = p, overlap
        if best is None:
            return None
        b = best.bbox
        if b.min_lon <= cx <= b.max_lon and b.min_lat <= cy <= b.max_lat:
            return best
        return None


def _bbox_area(b: BBox) -> float:
    return max(0.0, b.max_lon - b.min_lon) * max(0.0, b.max_lat - b.min_lat)


def _overlap_area(a: BBox, b: BBox) -> float:
    w = min(a.max_lon, b.max_lon) - max(a.min_lon, b.min_lon)
    h = min(a.max_lat, b.max_lat) - max(a.min_lat, b.min_lat)
    if w < 0 or h < 0:
        return 0.0
    # Degenerate targets (a point or a line) still overlap when they touch the pack.
    return max(w, 1e-12) * max(h, 1e-12)


def write_manifest(root: Path, manifest: DataPackManifest | dict[str, Any]) -> Path:
    """Validate and write ``manifest.json`` into ``root``."""
    model = manifest if isinstance(manifest, DataPackManifest) else DataPackManifest.model_validate(manifest)
    root.mkdir(parents=True, exist_ok=True)
    path = root / MANIFEST_NAME
    tmp = root / f".{MANIFEST_NAME}.tmp"
    tmp.write_text(json.dumps(model.model_dump(exclude_none=True), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


__all__ = [
    "DEFAULT_FILES",
    "LAYER_KEYS",
    "MANIFEST_NAME",
    "DataPack",
    "DataPackManifest",
    "DataPackRegistry",
    "Integrity",
    "LayerEntry",
    "content_sha256",
    "file_sha256",
    "seal_manifest",
    "verify_pack",
    "write_manifest",
]
