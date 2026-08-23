import csv
import hashlib
import json
from pathlib import Path

import pytest

from build_verification import (
    ASSET_OUTPUT_FILES,
    BuildVerificationError,
    audit_asset_build,
    compare_asset_builds,
    verify_v01_baseline,
)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_complete_build(root: Path, *, value: str = "a") -> None:
    _write_csv(
        root / ASSET_OUTPUT_FILES[0],
        [{"id": "1", "value": value}, {"id": "2", "value": value}],
    )
    for filename in ASSET_OUTPUT_FILES[1:3]:
        _write_csv(root / filename, [{"id": "1", "value": value}])
    report = {
        "status": "provisional_pass",
        "schema_version": "v0.2",
        "input_count": 2,
        "registered_count": 2,
        "excluded_count": 0,
        "unclassified_count": 0,
        "ambiguous_count": 0,
        "read_failure_count": 0,
        "unknown_scope_count": 0,
        "missing_status_count": 0,
        "duplicate_group_count": 1,
        "table_logical_hashes": {
            "asset_registry": "1" * 64,
            "source_scope": "2" * 64,
            "exact_duplicate_group": "3" * 64,
        },
        "snapshot_logical_hash": "4" * 64,
    }
    (root / ASSET_OUTPUT_FILES[3]).write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    (root / ASSET_OUTPUT_FILES[4]).write_text("# 计算数据准入\n", encoding="utf-8")


def test_compare_asset_builds_requires_byte_and_logical_identity(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    _write_complete_build(left)
    _write_complete_build(right)

    result = compare_asset_builds(
        left,
        right,
        require_byte_identical_csv=True,
        require_logical_hash_identical=True,
    )

    assert result["status"] == "identical"
    assert result["csv_byte_identical"] is True
    assert result["logical_hash_identical"] is True
    assert set(result["csv_sha256"]) == set(ASSET_OUTPUT_FILES[:3])


def test_compare_asset_builds_fails_closed_for_missing_or_different_outputs(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    _write_complete_build(left)
    _write_complete_build(right, value="different")

    with pytest.raises(BuildVerificationError) as changed:
        compare_asset_builds(left, right, require_byte_identical_csv=True)
    assert changed.value.code == "csv_bytes_differ"

    (right / ASSET_OUTPUT_FILES[0]).unlink()
    with pytest.raises(BuildVerificationError) as missing:
        compare_asset_builds(left, right)
    assert missing.value.code == "build_artifact_missing"


def test_verification_error_has_stable_structured_payload():
    error = BuildVerificationError("code", "message", field="value")
    assert error.as_dict() == {"code": "code", "message": "message", "field": "value"}


def test_compare_rejects_missing_or_nondirectory_roots(tmp_path):
    with pytest.raises(BuildVerificationError) as missing:
        compare_asset_builds(tmp_path / "missing", tmp_path)
    assert missing.value.code == "build_root_missing"

    file_root = tmp_path / "file"
    file_root.write_text("not a directory", encoding="utf-8")
    with pytest.raises(BuildVerificationError) as not_directory:
        compare_asset_builds(file_root, tmp_path)
    assert not_directory.value.code == "build_root_not_directory"


def test_compare_fails_closed_for_invalid_reports_and_logical_drift(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    _write_complete_build(left)
    _write_complete_build(right)
    report_path = right / ASSET_OUTPUT_FILES[3]

    report_path.write_text("not json", encoding="utf-8")
    with pytest.raises(BuildVerificationError) as invalid_json:
        compare_asset_builds(left, right)
    assert invalid_json.value.code == "asset_audit_invalid_json"

    report_path.write_text("[]", encoding="utf-8")
    with pytest.raises(BuildVerificationError) as invalid_top:
        compare_asset_builds(left, right)
    assert invalid_top.value.code == "asset_audit_invalid_json"

    _write_complete_build(right)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["table_logical_hashes"] = {}
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(BuildVerificationError) as missing_tables:
        compare_asset_builds(left, right)
    assert missing_tables.value.code == "asset_audit_missing_logical_hashes"

    _write_complete_build(right)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["table_logical_hashes"] = {"asset_registry": "bad"}
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(BuildVerificationError) as bad_table_hash:
        compare_asset_builds(left, right)
    assert bad_table_hash.value.code == "asset_audit_invalid_hash"

    _write_complete_build(right)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["snapshot_logical_hash"] = None
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(BuildVerificationError) as bad_snapshot_hash:
        compare_asset_builds(left, right)
    assert bad_snapshot_hash.value.code == "asset_audit_invalid_hash"

    _write_complete_build(right)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["snapshot_logical_hash"] = "5" * 64
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(BuildVerificationError) as logical_drift:
        compare_asset_builds(left, right, require_logical_hash_identical=True)
    assert logical_drift.value.code == "logical_hashes_differ"

    result = compare_asset_builds(left, right)
    assert result["status"] == "different"
    assert result["logical_hash_identical"] is False


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("status", "failed", "asset_audit_not_passed"),
        ("registered_count", 1, "asset_discovery_not_reconciled"),
        ("unclassified_count", 1, "asset_audit_blocking_count"),
        ("snapshot_logical_hash", "bad", "asset_audit_invalid_hash"),
    ],
)
def test_asset_audit_rejects_nonpassing_or_inconsistent_reports(
    tmp_path, field, value, code
):
    _write_complete_build(tmp_path)
    report_path = tmp_path / ASSET_OUTPUT_FILES[3]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report[field] = value
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(BuildVerificationError) as failure:
        audit_asset_build(tmp_path)
    assert failure.value.code == code


def test_asset_audit_accepts_complete_reconciled_provisional_build(tmp_path):
    _write_complete_build(tmp_path)
    result = audit_asset_build(tmp_path)
    assert result["status"] == "provisional_pass"
    assert result["artifact_count"] == len(ASSET_OUTPUT_FILES)
    assert result["input_count"] == 2


def test_asset_audit_rejects_schema_counts_rows_and_csv_encoding(tmp_path):
    _write_complete_build(tmp_path)
    report_path = tmp_path / ASSET_OUTPUT_FILES[3]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["schema_version"] = "v0.1"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(BuildVerificationError) as schema:
        audit_asset_build(tmp_path)
    assert schema.value.code == "asset_audit_schema_mismatch"

    _write_complete_build(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["duplicate_group_count"] = True
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(BuildVerificationError) as invalid_count:
        audit_asset_build(tmp_path)
    assert invalid_count.value.code == "asset_audit_invalid_count"

    _write_complete_build(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["input_count"] = 3
    report["registered_count"] = 3
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(BuildVerificationError) as rows:
        audit_asset_build(tmp_path)
    assert rows.value.code == "asset_registry_row_count_mismatch"

    _write_complete_build(tmp_path)
    asset_path = tmp_path / ASSET_OUTPUT_FILES[0]
    asset_path.write_text("id,value\n1,a\n2,a\n", encoding="utf-8")
    with pytest.raises(BuildVerificationError) as bom:
        audit_asset_build(tmp_path)
    assert bom.value.code == "asset_csv_missing_bom"

    _write_complete_build(tmp_path)
    asset_path.write_bytes(b"\xef\xbb\xbf")
    with pytest.raises(BuildVerificationError) as header:
        audit_asset_build(tmp_path)
    assert header.value.code == "asset_csv_invalid_header"

    _write_complete_build(tmp_path)
    asset_path.write_bytes(b"\xef\xbb\xbfid,\r\n1,a\r\n2,a\r\n")
    with pytest.raises(BuildVerificationError) as blank_header:
        audit_asset_build(tmp_path)
    assert blank_header.value.code == "asset_csv_invalid_header"

    _write_complete_build(tmp_path)
    asset_path.write_bytes(b"\xef\xbb\xbf\xff")
    with pytest.raises(BuildVerificationError) as decode:
        audit_asset_build(tmp_path)
    assert decode.value.code == "asset_csv_decode_failed"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _text_lf_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest().upper()


def _write_v01_fixture(root: Path) -> Path:
    raw = root / "数据/原始" / "raw.csv"
    output = root / "数据/快照" / "table.parquet"
    pipeline = root / "代码" / "module.py"
    for path, payload in (
        (raw, b"raw\r\n"),
        (output, b"parquet"),
        (pipeline, b"x = 1\r\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    snapshot = {
        "schema_version": "v0.1",
        "snapshot_id": "snapshot_3195c290d7dc2d44",
        "input_hashes": [{"raw_path": "数据/原始/raw.csv", "sha256": _sha256(raw)}],
        "outputs": {
            "table": {
                "path": "数据/快照/table.parquet",
                "sha256": _sha256(output),
                "size_bytes": output.stat().st_size,
            },
            "duckdb": {"path": "数据/快照/cache.duckdb", "byte_reproducible": False},
        },
        "pipeline": {
            "files": [
                {"path": "代码/module.py", "sha256_text_lf": _text_lf_sha256(pipeline)}
            ]
        },
    }
    path = root / "数据/快照" / "TPU数据库_v0.1_快照.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    return path


def test_verify_v01_baseline_is_read_only_and_checks_all_declared_hashes(tmp_path):
    snapshot = _write_v01_fixture(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    result = verify_v01_baseline(tmp_path, snapshot)

    assert result == {
        "status": "baseline_verified",
        "snapshot_id": "snapshot_3195c290d7dc2d44",
        "input_files_verified": 1,
        "output_files_verified": 1,
        "pipeline_files_verified": 1,
        "skipped_non_byte_reproducible_outputs": 1,
    }
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_verify_v01_baseline_rejects_snapshot_or_content_drift(tmp_path):
    snapshot = _write_v01_fixture(tmp_path)
    (tmp_path / "代码" / "module.py").write_text("x = 2\n", encoding="utf-8")
    with pytest.raises(BuildVerificationError) as drift:
        verify_v01_baseline(tmp_path, snapshot)
    assert drift.value.code == "baseline_hash_mismatch"

    snapshot_data = json.loads(snapshot.read_text(encoding="utf-8"))
    snapshot_data["snapshot_id"] = "snapshot_wrong"
    snapshot.write_text(json.dumps(snapshot_data), encoding="utf-8")
    with pytest.raises(BuildVerificationError) as wrong_snapshot:
        verify_v01_baseline(tmp_path, snapshot)
    assert wrong_snapshot.value.code == "baseline_snapshot_mismatch"


def _rewrite_snapshot(snapshot: Path, mutate) -> None:
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    mutate(data)
    snapshot.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda data: data.__setitem__("input_hashes", {}), "baseline_invalid_structure"),
        (lambda data: data.__setitem__("input_hashes", ["bad"]), "baseline_invalid_structure"),
        (lambda data: data["input_hashes"][0].__setitem__("raw_path", ""), "baseline_invalid_path"),
        (lambda data: data["input_hashes"][0].__setitem__("sha256", "bad"), "baseline_invalid_hash"),
        (lambda data: data.__setitem__("outputs", []), "baseline_invalid_structure"),
        (lambda data: data.__setitem__("outputs", {"table": "bad"}), "baseline_invalid_structure"),
        (
            lambda data: data.__setitem__(
                "outputs", {"table": {"path": "missing", "byte_reproducible": True}}
            ),
            "baseline_missing_hash",
        ),
        (lambda data: data.__setitem__("pipeline", {}), "baseline_invalid_structure"),
        (lambda data: data["pipeline"].__setitem__("files", ["bad"]), "baseline_invalid_structure"),
    ],
)
def test_verify_v01_baseline_rejects_malformed_snapshot_sections(tmp_path, mutate, code):
    snapshot = _write_v01_fixture(tmp_path)
    _rewrite_snapshot(snapshot, mutate)
    with pytest.raises(BuildVerificationError) as failure:
        verify_v01_baseline(tmp_path, snapshot)
    assert failure.value.code == code


def test_verify_v01_baseline_rejects_missing_paths_size_and_bad_utf8(tmp_path):
    snapshot = _write_v01_fixture(tmp_path)
    _rewrite_snapshot(
        snapshot,
        lambda data: data["input_hashes"][0].__setitem__("raw_path", "missing.csv"),
    )
    with pytest.raises(BuildVerificationError) as missing_file:
        verify_v01_baseline(tmp_path, snapshot)
    assert missing_file.value.code == "baseline_file_missing"

    snapshot = _write_v01_fixture(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.csv"
    outside.write_text("outside", encoding="utf-8")
    _rewrite_snapshot(
        snapshot,
        lambda data: data["input_hashes"][0].__setitem__("raw_path", str(outside)),
    )
    with pytest.raises(BuildVerificationError) as path_escape:
        verify_v01_baseline(tmp_path, snapshot)
    assert path_escape.value.code == "baseline_path_escape"

    snapshot = _write_v01_fixture(tmp_path)
    _rewrite_snapshot(
        snapshot, lambda data: data["outputs"]["table"].__setitem__("size_bytes", 999)
    )
    with pytest.raises(BuildVerificationError) as size:
        verify_v01_baseline(tmp_path, snapshot)
    assert size.value.code == "baseline_size_mismatch"

    snapshot = _write_v01_fixture(tmp_path)
    pipeline_path = tmp_path / "代码" / "module.py"
    pipeline_path.write_bytes(b"\xff")
    with pytest.raises(BuildVerificationError) as decode:
        verify_v01_baseline(tmp_path, snapshot)
    assert decode.value.code == "baseline_text_decode_failed"


def test_verify_v01_baseline_rejects_missing_or_outside_snapshot(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(BuildVerificationError) as missing:
        verify_v01_baseline(root, "missing.json")
    assert missing.value.code == "baseline_snapshot_missing"

    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(BuildVerificationError) as outside_failure:
        verify_v01_baseline(root, outside)
    assert outside_failure.value.code == "baseline_snapshot_path_escape"

    file_root = tmp_path / "not_root"
    file_root.write_text("x", encoding="utf-8")
    with pytest.raises(BuildVerificationError) as not_directory:
        verify_v01_baseline(file_root, outside)
    assert not_directory.value.code == "baseline_root_not_directory"
