"""Tests for safe staging: type detection, size limits and zip-slip / zip-bomb guards."""

from __future__ import annotations

import stat
import struct
import zipfile
from pathlib import Path

import pytest

from airbrief_core.errors import IngestError
from airbrief_core.io.ingest import IngestLimits, detect_kind, safe_extract_zip, stage_upload

KML_MIN = b'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><Point><coordinates>77.5,12.9</coordinates></Point></Placemark></kml>'


def _zip(path: Path, entries: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> Path:
    with zipfile.ZipFile(path, "w", compression) as zf:
        for name, data in entries.items():
            zf.writestr(zipfile.ZipInfo(name), data, compress_type=compression)
    return path


@pytest.mark.parametrize(
    ("filename", "head", "kind"),
    [
        ("a.kml", b"<?xml", "kml"),
        ("A.KML", b"<kml", "kml"),
        ("a.kmz", b"PK\x03\x04rest", "kmz"),
        ("a.kml", b"PK\x03\x04rest", "kmz"),
        ("a.zip", b"PK\x03\x04rest", "shapefile"),
        ("archive", b"PK\x03\x04rest", "shapefile"),
        ("a.geojson", b"{", "geojson"),
        ("a.json", b"{", "geojson"),
        ("tracks.csv", b"lon,lat", "csv"),
        ("C:\\Users\\x\\data.kml", b"<kml", "kml"),
    ],
)
def test_detect_kind(filename: str, head: bytes, kind: str) -> None:
    assert detect_kind(filename, head) == kind


@pytest.mark.parametrize("filename", ["notes.txt", "image.png", "drawing.dxf", "noext"])
def test_detect_kind_unknown_extension(filename: str) -> None:
    with pytest.raises(IngestError) as exc:
        detect_kind(filename, b"hello")
    assert exc.value.code == "unsupported_format"


def test_detect_kind_loose_shp_is_unsupported() -> None:
    with pytest.raises(IngestError) as exc:
        detect_kind("roads.shp", b"\x00\x00\x27\x0a")
    assert exc.value.code == "unsupported_format"


def test_detect_kind_fake_zip_is_corrupt() -> None:
    with pytest.raises(IngestError) as exc:
        detect_kind("data.zip", b"not a zip")
    assert exc.value.code == "corrupt_file"


def test_stage_plain_kml(tmp_path: Path) -> None:
    src = tmp_path / "in.kml"
    src.write_bytes(KML_MIN)
    staged = stage_upload(src, "../../evil name.kml", tmp_path / "work", IngestLimits())
    assert staged.kind == "kml"
    assert len(staged.paths) == 1
    assert staged.paths[0].read_bytes() == KML_MIN
    assert staged.paths[0].parent == staged.root
    assert ".." not in staged.filename


def test_stage_empty_file(tmp_path: Path) -> None:
    src = tmp_path / "empty.kml"
    src.write_bytes(b"")
    with pytest.raises(IngestError) as exc:
        stage_upload(src, "empty.kml", tmp_path / "work", IngestLimits())
    assert exc.value.code == "empty_file"


def test_stage_file_too_large(tmp_path: Path) -> None:
    src = tmp_path / "big.kml"
    src.write_bytes(KML_MIN)
    with pytest.raises(IngestError) as exc:
        stage_upload(src, "big.kml", tmp_path / "work", IngestLimits(max_upload_bytes=10))
    assert exc.value.code == "file_too_large"


def test_stage_unknown_extension(tmp_path: Path) -> None:
    src = tmp_path / "x.txt"
    src.write_bytes(b"hello")
    with pytest.raises(IngestError) as exc:
        stage_upload(src, "x.txt", tmp_path / "work", IngestLimits())
    assert exc.value.code == "unsupported_format"


def test_stage_zip_with_only_kml_becomes_kml(tmp_path: Path) -> None:
    src = _zip(tmp_path / "in.zip", {"folder/a.kml": KML_MIN})
    staged = stage_upload(src, "in.zip", tmp_path / "work", IngestLimits())
    assert staged.kind == "kml"
    assert [p.name for p in staged.paths] == ["a.kml"]


def test_stage_zip_with_shapefile_parts(tmp_path: Path) -> None:
    src = _zip(tmp_path / "in.zip", {"a.shp": b"x", "a.shx": b"x", "a.dbf": b"x"})
    staged = stage_upload(src, "in.zip", tmp_path / "work", IngestLimits())
    assert staged.kind == "shapefile"


def test_stage_zip_without_shp_but_with_dbf(tmp_path: Path) -> None:
    src = _zip(tmp_path / "in.zip", {"a.dbf": b"x", "a.shx": b"x"})
    with pytest.raises(IngestError) as exc:
        stage_upload(src, "in.zip", tmp_path / "work", IngestLimits())
    assert exc.value.code == "missing_component"


def test_stage_zip_without_supported_content(tmp_path: Path) -> None:
    src = _zip(tmp_path / "in.zip", {"readme.txt": b"hello"})
    with pytest.raises(IngestError) as exc:
        stage_upload(src, "in.zip", tmp_path / "work", IngestLimits())
    assert exc.value.code == "no_supported_content"


@pytest.mark.parametrize(
    "name",
    ["../evil.txt", "a/../../evil.txt", "/etc/evil.txt", "C:/evil.txt", "c:\\evil.txt", "..\\evil.txt"],
)
def test_zip_slip_rejected(tmp_path: Path, name: str) -> None:
    src = _zip(tmp_path / "slip.zip", {"ok.kml": KML_MIN, name: b"pwned"})
    dest = tmp_path / "out"
    with pytest.raises(IngestError) as exc:
        safe_extract_zip(src, dest, IngestLimits())
    assert exc.value.code == "unsafe_archive"
    # Nothing is written before every entry has been validated.
    assert not (tmp_path / "evil.txt").exists()
    assert not dest.exists() or not any(dest.rglob("*"))


def test_symlink_entry_rejected(tmp_path: Path) -> None:
    path = tmp_path / "link.zip"
    with zipfile.ZipFile(path, "w") as zf:
        info = zipfile.ZipInfo("link.kml")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(IngestError) as exc:
        safe_extract_zip(path, tmp_path / "out", IngestLimits())
    assert exc.value.code == "unsafe_archive"


def test_declared_size_bomb_rejected(tmp_path: Path) -> None:
    # 2 MB of zeros compresses to a few KB; the declared size exceeds the 1 MB limit.
    src = _zip(tmp_path / "bomb.zip", {"a.kml": b"\x00" * (2 * 1024 * 1024)})
    assert src.stat().st_size < 64 * 1024
    with pytest.raises(IngestError) as exc:
        safe_extract_zip(src, tmp_path / "out", IngestLimits(max_unzipped_bytes=1024 * 1024))
    assert exc.value.code == "archive_too_large"


def test_declared_total_across_entries(tmp_path: Path) -> None:
    entries = {f"f{i}.kml": b"\x00" * 300_000 for i in range(4)}
    src = _zip(tmp_path / "many.zip", entries)
    with pytest.raises(IngestError) as exc:
        safe_extract_zip(src, tmp_path / "out", IngestLimits(max_unzipped_bytes=1_000_000))
    assert exc.value.code == "archive_too_large"


def _patch_sizes(path: Path, new_size: int) -> None:
    """Make the local and central headers lie about the uncompressed size."""
    data = bytearray(path.read_bytes())
    local = data.find(b"PK\x03\x04")
    struct.pack_into("<I", data, local + 22, new_size)
    central = data.find(b"PK\x01\x02")
    struct.pack_into("<I", data, central + 24, new_size)
    path.write_bytes(bytes(data))


def test_lying_header_cannot_bypass_limits(tmp_path: Path) -> None:
    src = _zip(tmp_path / "liar.zip", {"a.kml": b"\x00" * 500_000})
    _patch_sizes(src, 100)
    dest = tmp_path / "out"
    with pytest.raises(IngestError) as exc:
        safe_extract_zip(src, dest, IngestLimits(max_unzipped_bytes=200_000))
    assert exc.value.code in {"archive_too_large", "corrupt_file"}
    written = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file()) if dest.exists() else 0
    assert written <= 200_000


def test_too_many_entries(tmp_path: Path) -> None:
    src = _zip(tmp_path / "many.zip", {f"f{i}.kml": b"<kml/>" for i in range(6)})
    with pytest.raises(IngestError) as exc:
        safe_extract_zip(src, tmp_path / "out", IngestLimits(max_archive_files=5))
    assert exc.value.code == "too_many_files"


def test_macosx_entries_ignored(tmp_path: Path) -> None:
    src = _zip(
        tmp_path / "mac.zip",
        {
            "data/a.kml": KML_MIN,
            "__MACOSX/data/._a.kml": b"\x00\x05\x16\x07junk",
            "__MACOSX/._data": b"junk",
            "data/.DS_Store": b"junk",
        },
    )
    # The __MACOSX entries do not count towards the file limit either.
    paths = safe_extract_zip(src, tmp_path / "out", IngestLimits(max_archive_files=1))
    assert [p.relative_to(tmp_path / "out").as_posix() for p in paths] == ["data/a.kml"]
    assert not (tmp_path / "out" / "__MACOSX").exists()


def test_directories_and_order_preserved(tmp_path: Path) -> None:
    path = tmp_path / "dirs.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("b/", b"")
        zf.writestr("b/second.kml", KML_MIN)
        zf.writestr("a.kml", KML_MIN)
    paths = safe_extract_zip(path, tmp_path / "out", IngestLimits())
    assert [p.name for p in paths] == ["second.kml", "a.kml"]
    assert all(p.read_bytes() == KML_MIN for p in paths)


def test_not_a_zip_is_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "bad.zip"
    path.write_bytes(b"PK\x03\x04 truncated garbage")
    with pytest.raises(IngestError) as exc:
        safe_extract_zip(path, tmp_path / "out", IngestLimits())
    assert exc.value.code == "corrupt_file"
