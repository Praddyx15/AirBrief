"""``python -m seed.demo_export``: the static demo files match what ``frontend/lib/api.ts`` reads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from seed import demo_export


def _read(path: Path) -> Any:
    assert path.is_file(), f"missing {path}"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.slow
def test_demo_export_writes_the_demo_files(tmp_path: Path, synthetic_pack_dir: Path) -> None:
    out = tmp_path / "demo"
    code = demo_export.main(["--out", str(out), "--datapack-dir", str(synthetic_pack_dir)])
    assert code == 0

    manifest = _read(out / "manifest.json")
    assert manifest["version"]
    assert manifest["generated_utc"].endswith("Z")
    assert {a["status"] for a in manifest["analyses"]} == {"COMPLETED"}
    assert {a["kind"] for a in manifest["analyses"]} == {
        "survey",
        "airspace",
        "ground_risk",
        "evtol_siting",
        "evtol_corridor",
        "show",
        "flight_audit",
        "line_of_sight",
        "environment",
    }

    meta = _read(out / "meta.json")
    assert meta["datapacks"] and meta["modules"]
    assert _read(out / "health.json")["status"]
    context = _read(out / "context.json")  # plan without a file: context layers + place search
    assert context["datapack"] == "demo"
    assert {f["properties"]["ident"] for f in context["layers"]["aerodromes"]["features"]} >= {"VOBL"}
    assert context["layers"]["coverage"]["features"]

    samples = {s["name"]: s for s in _read(out / "samples.json")}
    for hidden in demo_export.HIDDEN_SAMPLES:
        assert "dataset_id" not in samples[hidden]
    survey_id = samples["bengaluru_survey"]["dataset_id"]
    report = samples["flightmd_report_v15"]
    assert "dataset_id" not in report
    assert (out / report["download"]).is_file()

    files = _read(out / "files.json")
    assert files["total"] == len(files["items"]) == len(demo_export.demo_samples())
    assert {f["id"] for f in files["items"]} >= {s["dataset_id"] for s in samples.values() if "dataset_id" in s}

    assert _read(out / "files" / f"{survey_id}.json")["id"] == survey_id
    measurements = _read(out / "files" / survey_id / "measurements.json")
    assert measurements["file_id"] == survey_id
    assert measurements["total"] == len(measurements["items"]) >= 1
    features = _read(out / "files" / survey_id / "features.json")
    assert features["type"] == "FeatureCollection"
    first_index = features["features"][0]["properties"]["_index"]
    assert (out / "files" / survey_id / "features" / f"{first_index}.json").is_file()

    analyses = _read(out / "analyses" / "index.json")
    assert analyses["total"] == len(analyses["items"]) == len(demo_export.ANALYSIS_PLANS)
    for item in analyses["items"]:
        detail = _read(out / "analyses" / f"{item['id']}.json")
        assert detail["status"] == "COMPLETED"
        assert detail["result"] is not None
        # run with the sample's owner token, so the demo never labels them "added by another visitor"
        assert detail["by_dataset_owner"] is (detail["dataset_id"] is not None)
        assert detail["owner_token"] is None
        brief = _read(out / "briefs" / f"{item['id']}.json")  # merged in the browser by lib/brief-demo.ts
        assert [a["analysis_id"] for a in brief["provenance"]["analyses"]] == [item["id"]]

    # A second run replaces the previous export in place.
    assert demo_export.main(["--out", str(out), "--datapack-dir", str(synthetic_pack_dir)]) == 0
    assert not (out / "files" / f"{survey_id}.json").exists()


def test_demo_export_refuses_a_foreign_directory(tmp_path: Path) -> None:
    out = tmp_path / "not-a-demo"
    out.mkdir()
    (out / "keep.txt").write_text("precious", encoding="utf-8")
    assert demo_export.main(["--out", str(out)]) == 2
    assert (out / "keep.txt").read_text(encoding="utf-8") == "precious"


def test_failed_export_keeps_the_previous_one(tmp_path: Path, synthetic_pack_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "demo"
    out.mkdir()
    (out / "manifest.json").write_text('{"previous": true}', encoding="utf-8")
    (out / "files.json").write_text("[]", encoding="utf-8")

    def broken(*args: Any, **kwargs: Any) -> None:
        raise demo_export.ExportError("simulated failure half-way")

    monkeypatch.setattr(demo_export, "export_all", broken)
    with pytest.raises(demo_export.ExportError):
        demo_export.run_export(out, datapack_dir=synthetic_pack_dir)
    assert _read(out / "manifest.json") == {"previous": True}  # untouched
    assert not demo_export.staging_dir(out).exists()  # nothing half-written left behind


def test_export_refuses_a_folder_that_is_not_an_export(tmp_path: Path) -> None:
    out = tmp_path / "mine"
    out.mkdir()
    (out / "notes.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(demo_export.OutDirError):
        demo_export.check_out_dir(out)
    assert (out / "notes.txt").is_file()
