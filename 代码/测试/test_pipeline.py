import csv
from pathlib import Path
from types import SimpleNamespace

import yaml

from qc import QualityIssue
from run_pipeline import build_full_manifest, build_from_manifest, load_manifest_csv, main


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
                "access_restriction": "open",
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
    assert by_name["known.csv"]["access_restriction"] == "open"
    assert by_name["unknown.txt"]["source_id"] == "raw_vault_unregistered"
    assert by_name["unknown.txt"]["access_restriction"] == "unknown"
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


def test_manifest_loader_and_build_forward_frozen_rows(tmp_path, monkeypatch):
    raw = tmp_path / "01_原始数据" / "基础数据"
    raw.mkdir(parents=True)
    (raw / "known.csv").write_text("id\n1\n", encoding="utf-8")
    config = _write_config(tmp_path)
    manifest_path = tmp_path / "清单" / "来源清单.csv"
    expected = build_full_manifest(tmp_path, config, manifest_path)
    loaded = load_manifest_csv(tmp_path, manifest_path)
    assert len(loaded) == len(expected) == 1

    captured = {}

    def fake_build(root, rows):
        captured["root"] = root
        captured["rows"] = rows
        return SimpleNamespace(
            snapshot_id="snapshot_fixture",
            row_counts={"source": 1},
            outputs={},
            issues=(),
            has_errors=False,
        )

    monkeypatch.setattr("run_pipeline.build_database", fake_build)
    result = build_from_manifest(tmp_path, manifest_path)
    assert result.snapshot_id == "snapshot_fixture"
    assert captured["root"] == tmp_path.resolve()
    assert captured["rows"] == loaded


def test_build_and_qc_cli_report_errors(tmp_path, capsys, monkeypatch):
    manifest_path = tmp_path / "manifest.csv"
    manifest_path.write_text("source_id\nfixture\n", encoding="utf-8")
    issue = QualityIssue("rule", "error", "table", "record", "message")
    failed = SimpleNamespace(
        snapshot_id="snapshot_failed",
        row_counts={"table": 1},
        outputs={},
        issues=(issue,),
        has_errors=True,
    )
    monkeypatch.setattr("run_pipeline.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("run_pipeline.build_from_manifest", lambda *_: failed)

    assert main(["build", "--manifest", str(manifest_path), "--version", "v0.1"]) == 1
    build_payload = capsys.readouterr().out
    assert '"has_errors": true' in build_payload
    assert '"issues"' not in build_payload

    assert main(["qc", "--manifest", str(manifest_path)]) == 1
    qc_payload = capsys.readouterr().out
    assert '"issues"' in qc_payload
    assert '"rule_id": "rule"' in qc_payload
