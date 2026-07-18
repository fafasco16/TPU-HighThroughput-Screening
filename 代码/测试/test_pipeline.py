import csv
from pathlib import Path

import yaml

from run_pipeline import build_full_manifest, main


def _write_config(root: Path) -> Path:
    config = {
        "schema_version": "v0.1",
        "sources": [
            {
                "source_id": "registered_source",
                "path": "01_原始数据/基础数据/known.csv",
                "doi": "10.0000/example",
                "url": "https://example.invalid/data",
                "accessed_at": "2026-07-18",
                "license_spdx": "CC-BY-4.0",
                "derivatives_allowed": True,
                "redistribution_allowed": True,
                "evidence_grade": "measured_raw",
                "material_scope": "linear_tpu",
                "status": "available",
                "notes": "fixture",
            }
        ],
    }
    path = root / "配置" / "数据源.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return path


def test_full_manifest_covers_registered_and_unregistered_files(tmp_path):
    raw = tmp_path / "01_原始数据" / "基础数据"
    raw.mkdir(parents=True)
    (raw / "known.csv").write_text("id\n1\n", encoding="utf-8")
    (raw / "unknown.txt").write_text("local", encoding="utf-8")
    (tmp_path / "01_原始数据" / "README.md").write_text(
        "project-owned placeholder", encoding="utf-8"
    )
    config = _write_config(tmp_path)
    output = tmp_path / "清单" / "来源清单.csv"
    rows = build_full_manifest(tmp_path, config, output)
    assert len(rows) == 2
    assert len({row["raw_path"] for row in rows}) == 2
    by_name = {row["original_filename"]: row for row in rows}
    assert by_name["known.csv"]["source_id"] == "registered_source"
    assert by_name["unknown.txt"]["source_id"] == "raw_vault_unregistered"
    with output.open(encoding="utf-8-sig", newline="") as stream:
        assert len(list(csv.DictReader(stream))) == 2


def test_manifest_cli_uses_requested_paths(tmp_path, capsys, monkeypatch):
    raw = tmp_path / "01_原始数据" / "基础数据"
    raw.mkdir(parents=True)
    (raw / "known.csv").write_text("id\n1\n", encoding="utf-8")
    config = _write_config(tmp_path)
    output = tmp_path / "manifest.csv"
    monkeypatch.setattr("run_pipeline.PROJECT_ROOT", tmp_path)
    exit_code = main(["manifest", "--config", str(config), "--output", str(output)])
    assert exit_code == 0
    assert output.exists()
    assert '"files": 1' in capsys.readouterr().out
