import csv
import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from qc import QualityIssue
from run_pipeline import build_full_manifest, build_from_manifest, load_manifest_csv, main
from build_verification import BuildVerificationError
from governance_build import GovernanceBuildError


FIXTURES = Path(__file__).parent / "夹具"


def _write_config(root: Path) -> Path:
    config = {
        "schema_version": "v0.1",
        "sources": [
            {
                "source_id": "registered_source",
                "path": "数据/原始/基础数据/known.csv",
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
    raw = tmp_path / "数据/原始" / "基础数据"
    raw.mkdir(parents=True)
    (raw / "known.csv").write_text("id\n1\n", encoding="utf-8")
    (raw / "unknown.txt").write_text("local", encoding="utf-8")
    (tmp_path / "数据/原始" / "README.md").write_text(
        "project-owned placeholder", encoding="utf-8"
    )
    config = _write_config(tmp_path)
    output = tmp_path / "配置/清单" / "来源清单.csv"
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
    raw = tmp_path / "数据/原始" / "基础数据"
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
    raw = tmp_path / "数据/原始" / "基础数据"
    raw.mkdir(parents=True)
    (raw / "known.csv").write_text("id\n1\n", encoding="utf-8")
    config = _write_config(tmp_path)
    manifest_path = tmp_path / "配置/清单" / "来源清单.csv"
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


def test_contract_audit_is_read_only_and_deterministic(capsys, monkeypatch):
    monkeypatch.setattr(
        "run_pipeline.build_from_manifest",
        lambda *_: (_ for _ in ()).throw(AssertionError("database build was invoked")),
    )
    arguments = [
        "contract-audit",
        "--schema",
        str(FIXTURES / "v0.2最小合同.yaml"),
        "--enums",
        str(FIXTURES / "v0.2最小枚举.yaml"),
        "--rules",
        str(FIXTURES / "v0.2最小质量规则.yaml"),
    ]
    assert main(arguments) == 0
    first_text = capsys.readouterr().out
    first = json.loads(first_text)
    assert first["status"] == "contract_valid"
    assert first["schema_version"] == "v0.2"
    assert first["table_count"] == 3
    assert first["rule_count"] == 1
    assert all(len(digest) == 64 for digest in first["document_hashes"].values())

    assert main(arguments) == 0
    assert capsys.readouterr().out == first_text


def test_contract_audit_reports_structured_failure_without_building(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr(
        "run_pipeline.build_from_manifest",
        lambda *_: (_ for _ in ()).throw(AssertionError("database build was invoked")),
    )
    with (FIXTURES / "v0.2最小合同.yaml").open(encoding="utf-8") as stream:
        schema = yaml.safe_load(stream)
    schema["tables"]["snapshot_record"]["foreign_keys"][0]["references"][
        "table"
    ] = "missing"
    broken = tmp_path / "broken.yaml"
    broken.write_text(
        yaml.safe_dump(schema, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    exit_code = main(
        [
            "contract-audit",
            "--schema",
            str(broken),
            "--enums",
            str(FIXTURES / "v0.2最小枚举.yaml"),
            "--rules",
            str(FIXTURES / "v0.2最小质量规则.yaml"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload == {
        "status": "contract_invalid",
        "error": {
            "code": "foreign_key_unknown_table",
            "message": "foreign key references unknown table 'missing'",
            "table": "snapshot_record",
            "constraint": "fk_snapshot_record_registry",
        },
    }


def test_compare_builds_cli_forwards_strict_reproducibility_flags(
    tmp_path, capsys, monkeypatch
):
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    captured = {}

    def fake_compare(left_arg, right_arg, **options):
        captured.update(left=left_arg, right=right_arg, options=options)
        return {"status": "identical"}

    monkeypatch.setattr("run_pipeline.compare_asset_builds", fake_compare)
    assert (
        main(
            [
                "compare-builds",
                "--left",
                str(left),
                "--right",
                str(right),
                "--require-byte-identical-csv",
                "--require-logical-hash-identical",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {"status": "identical"}
    assert captured == {
        "left": str(left),
        "right": str(right),
        "options": {
            "require_byte_identical_csv": True,
            "require_logical_hash_identical": True,
        },
    }


def test_asset_audit_cli_requires_report_at_build_root(tmp_path, capsys, monkeypatch):
    build_root = tmp_path / "build"
    build_root.mkdir()
    report = build_root / "TPU数据库_v0.2_资产登记审计.json"
    report.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "run_pipeline.audit_asset_build", lambda root: {"status": "provisional_pass"}
    )
    assert (
        main(
            [
                "asset-audit",
                "--build-root",
                str(build_root),
                "--report",
                str(report),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {"status": "provisional_pass"}

    wrong_report = tmp_path / "wrong.json"
    assert (
        main(
            [
                "asset-audit",
                "--build-root",
                str(build_root),
                "--report",
                str(wrong_report),
            ]
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "verification_failed"
    assert payload["error"]["code"] == "asset_audit_report_path_mismatch"


def test_verify_baseline_cli_and_structured_verification_failure(
    tmp_path, capsys, monkeypatch
):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("run_pipeline.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        "run_pipeline.verify_v01_baseline",
        lambda root, path: {"status": "baseline_verified", "snapshot_id": "snapshot_fixture"},
    )
    assert main(["verify-v0.1-baseline", "--snapshot", str(snapshot)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "baseline_verified"

    monkeypatch.setattr(
        "run_pipeline.verify_v01_baseline",
        lambda *_: (_ for _ in ()).throw(BuildVerificationError("drift", "changed")),
    )
    assert main(["verify-v0.1-baseline", "--snapshot", str(snapshot)]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "verification_failed",
        "error": {"code": "drift", "message": "changed"},
    }


def test_governance_build_cli_forwards_all_frozen_inputs(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr("run_pipeline.PROJECT_ROOT", tmp_path)
    captured = {}

    def fake_build(project, output, **options):
        captured.update(project=project, output=output, options=options)
        return SimpleNamespace(
            output_root=tmp_path / "数据/临时/构建缓存/构建A",
            report={"input_count": 1607, "snapshot_logical_hash": "a" * 64},
        )

    monkeypatch.setattr("run_pipeline.build_governance_database", fake_build)
    arguments = [
        "governance-build",
        "--output-root",
        "数据/临时/构建缓存/构建A",
        "--asset-rules",
        "asset.yaml",
        "--source-scopes",
        "source.yaml",
        "--schema",
        "schema.yaml",
        "--enums",
        "enums.yaml",
        "--rules",
        "rules.yaml",
        "--v01-snapshot",
        "snapshot.json",
    ]
    assert main(arguments) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "provisional_pass"
    assert payload["input_count"] == 1607
    assert captured == {
        "project": tmp_path,
        "output": "数据/临时/构建缓存/构建A",
        "options": {
            "asset_rules_path": "asset.yaml",
            "source_scope_path": "source.yaml",
            "contract_schema_path": "schema.yaml",
            "enums_path": "enums.yaml",
            "quality_rules_path": "rules.yaml",
            "v01_snapshot_path": "snapshot.json",
        },
    }


def test_governance_audit_compare_and_structured_failure_cli(
    tmp_path, capsys, monkeypatch
):
    left = tmp_path / "left"
    right = tmp_path / "right"
    monkeypatch.setattr(
        "run_pipeline.audit_governance_build",
        lambda root: {"status": "provisional_pass", "root": str(root)},
    )
    assert main(["governance-audit", "--build-root", str(left)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "provisional_pass"

    monkeypatch.setattr(
        "run_pipeline.compare_governance_builds",
        lambda left_arg, right_arg: {"status": "identical", "left": str(left_arg), "right": str(right_arg)},
    )
    assert (
        main(
            [
                "compare-governance-builds",
                "--left",
                str(left),
                "--right",
                str(right),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "identical"

    monkeypatch.setattr(
        "run_pipeline.audit_governance_build",
        lambda *_: (_ for _ in ()).throw(
            GovernanceBuildError("tampered", "artifact changed", artifact="x.csv")
        ),
    )
    assert main(["governance-audit", "--build-root", str(left)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "status": "governance_verification_failed",
        "error": {
            "code": "tampered",
            "message": "artifact changed",
            "artifact": "x.csv",
        },
    }
