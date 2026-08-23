"""Read-only verification for provisional v0.2 builds and the frozen v0.1 baseline."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


ASSET_OUTPUT_FILES = (
    "v0.2全量资产登记.csv",
    "v0.2来源范围.csv",
    "v0.2精确重复组.csv",
    "TPU数据库_v0.2_资产登记审计.json",
    "TPU数据库_v0.2_计算数据准入报告.md",
)
EXPECTED_V01_SNAPSHOT_ID = "snapshot_3195c290d7dc2d44"
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


class BuildVerificationError(ValueError):
    """Structured, fail-closed verification failure."""

    def __init__(self, code: str, message: str, **context: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = context

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"code": self.code, "message": self.message}
        payload.update(self.context)
        return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text_lf_sha256(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise BuildVerificationError(
            "baseline_text_decode_failed",
            "基线声明为 UTF-8 文本的管道文件无法解码",
            path=path.as_posix(),
        ) from error
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _existing_directory(path: str | Path, *, side: str) -> Path:
    candidate = Path(path)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise BuildVerificationError(
            "build_root_missing", "构建输出根不存在", side=side, path=str(candidate)
        ) from error
    if not resolved.is_dir():
        raise BuildVerificationError(
            "build_root_not_directory", "构建输出根不是目录", side=side, path=str(resolved)
        )
    return resolved


def _required_artifact(root: Path, filename: str, *, side: str) -> Path:
    path = root / filename
    if not path.is_file():
        raise BuildVerificationError(
            "build_artifact_missing",
            "构建缺少必需产物",
            side=side,
            artifact=filename,
        )
    return path


def _load_json_object(path: Path, *, code: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BuildVerificationError(code, "JSON 产物无法读取或不是有效 JSON", path=str(path)) from error
    if not isinstance(payload, dict):
        raise BuildVerificationError(code, "JSON 产物顶层必须是对象", path=str(path))
    return payload


def _logical_hash_payload(root: Path, *, side: str) -> tuple[dict[str, str], str]:
    report = _load_json_object(
        _required_artifact(root, ASSET_OUTPUT_FILES[3], side=side),
        code="asset_audit_invalid_json",
    )
    table_hashes = report.get("table_logical_hashes")
    snapshot_hash = report.get("snapshot_logical_hash")
    if not isinstance(table_hashes, dict) or not table_hashes:
        raise BuildVerificationError(
            "asset_audit_missing_logical_hashes",
            "资产审计缺少非空 table_logical_hashes",
            side=side,
        )
    normalized: dict[str, str] = {}
    for table, digest in sorted(table_hashes.items()):
        if not isinstance(table, str) or not isinstance(digest, str) or not _HEX64.fullmatch(digest):
            raise BuildVerificationError(
                "asset_audit_invalid_hash",
                "资产审计含非法表逻辑哈希",
                side=side,
                table=str(table),
            )
        normalized[table] = digest.lower()
    if not isinstance(snapshot_hash, str) or not _HEX64.fullmatch(snapshot_hash):
        raise BuildVerificationError(
            "asset_audit_invalid_hash",
            "资产审计缺少合法快照逻辑哈希",
            side=side,
        )
    return normalized, snapshot_hash.lower()


def compare_asset_builds(
    left: str | Path,
    right: str | Path,
    *,
    require_byte_identical_csv: bool = False,
    require_logical_hash_identical: bool = False,
) -> dict[str, object]:
    """Compare two isolated provisional builds without modifying either build."""

    left_root = _existing_directory(left, side="left")
    right_root = _existing_directory(right, side="right")
    for filename in ASSET_OUTPUT_FILES:
        _required_artifact(left_root, filename, side="left")
        _required_artifact(right_root, filename, side="right")

    csv_sha256: dict[str, dict[str, str]] = {}
    byte_identical = True
    for filename in ASSET_OUTPUT_FILES[:3]:
        left_hash = _sha256(left_root / filename)
        right_hash = _sha256(right_root / filename)
        csv_sha256[filename] = {"left": left_hash, "right": right_hash}
        byte_identical = byte_identical and left_hash == right_hash

    left_tables, left_snapshot = _logical_hash_payload(left_root, side="left")
    right_tables, right_snapshot = _logical_hash_payload(right_root, side="right")
    logical_identical = left_tables == right_tables and left_snapshot == right_snapshot

    if require_byte_identical_csv and not byte_identical:
        raise BuildVerificationError(
            "csv_bytes_differ", "两个隔离构建的 CSV 字节不一致", csv_sha256=csv_sha256
        )
    if require_logical_hash_identical and not logical_identical:
        raise BuildVerificationError(
            "logical_hashes_differ",
            "两个隔离构建的表级或快照级逻辑哈希不一致",
            left_table_logical_hashes=left_tables,
            right_table_logical_hashes=right_tables,
            left_snapshot_logical_hash=left_snapshot,
            right_snapshot_logical_hash=right_snapshot,
        )

    return {
        "status": "identical" if byte_identical and logical_identical else "different",
        "csv_byte_identical": byte_identical,
        "logical_hash_identical": logical_identical,
        "csv_sha256": csv_sha256,
        "table_logical_hashes": {"left": left_tables, "right": right_tables},
        "snapshot_logical_hashes": {"left": left_snapshot, "right": right_snapshot},
    }


def _require_nonnegative_integer(report: Mapping[str, Any], field: str) -> int:
    value = report.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BuildVerificationError(
            "asset_audit_invalid_count", "资产审计计数字段必须是非负整数", field=field
        )
    return value


def _csv_data_rows(path: Path) -> int:
    raw = path.read_bytes()
    if not raw.startswith(b"\xef\xbb\xbf"):
        raise BuildVerificationError(
            "asset_csv_missing_bom", "资产登记 CSV 必须使用 UTF-8 BOM", artifact=path.name
        )
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream)
            header = next(reader)
            if not header or any(not column.strip() for column in header):
                raise BuildVerificationError(
                    "asset_csv_invalid_header", "资产登记 CSV 表头为空或含空列名", artifact=path.name
                )
            return sum(1 for row in reader if any(cell != "" for cell in row))
    except StopIteration as error:
        raise BuildVerificationError(
            "asset_csv_invalid_header", "资产登记 CSV 缺少表头", artifact=path.name
        ) from error
    except UnicodeError as error:
        raise BuildVerificationError(
            "asset_csv_decode_failed", "资产登记 CSV 无法按 UTF-8 BOM 解码", artifact=path.name
        ) from error


def audit_asset_build(build_root: str | Path) -> dict[str, object]:
    """Validate completeness and reconciliation of a provisional asset build."""

    root = _existing_directory(build_root, side="build")
    artifacts = [_required_artifact(root, filename, side="build") for filename in ASSET_OUTPUT_FILES]
    report = _load_json_object(artifacts[3], code="asset_audit_invalid_json")

    if report.get("status") != "provisional_pass":
        raise BuildVerificationError(
            "asset_audit_not_passed",
            "资产登记审计状态不是 provisional_pass",
            actual_status=report.get("status"),
        )
    if report.get("schema_version") != "v0.2":
        raise BuildVerificationError(
            "asset_audit_schema_mismatch",
            "资产登记审计 schema_version 不是 v0.2",
            actual_schema_version=report.get("schema_version"),
        )

    input_count = _require_nonnegative_integer(report, "input_count")
    registered_count = _require_nonnegative_integer(report, "registered_count")
    excluded_count = _require_nonnegative_integer(report, "excluded_count")
    _require_nonnegative_integer(report, "duplicate_group_count")
    if input_count != registered_count + excluded_count:
        raise BuildVerificationError(
            "asset_discovery_not_reconciled",
            "发现资产数不等于已登记与证据排除之和",
            input_count=input_count,
            registered_count=registered_count,
            excluded_count=excluded_count,
        )

    for field in (
        "unclassified_count",
        "ambiguous_count",
        "read_failure_count",
        "unknown_scope_count",
        "missing_status_count",
    ):
        value = _require_nonnegative_integer(report, field)
        if value:
            raise BuildVerificationError(
                "asset_audit_blocking_count",
                "资产登记审计含非零阻断计数",
                field=field,
                value=value,
            )

    _logical_hash_payload(root, side="build")
    asset_rows = _csv_data_rows(artifacts[0])
    for path in artifacts[1:3]:
        _csv_data_rows(path)
    if asset_rows != input_count:
        raise BuildVerificationError(
            "asset_registry_row_count_mismatch",
            "全量资产登记 CSV 行数与 input_count 不一致",
            csv_rows=asset_rows,
            input_count=input_count,
        )

    return {
        "status": "provisional_pass",
        "schema_version": "v0.2",
        "artifact_count": len(artifacts),
        "input_count": input_count,
        "registered_count": registered_count,
        "excluded_count": excluded_count,
    }


def _project_file(root: Path, relative_path: object, *, category: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise BuildVerificationError(
            "baseline_invalid_path", "基线文件路径必须是非空字符串", category=category
        )
    candidate = root / Path(relative_path)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise BuildVerificationError(
            "baseline_file_missing", "基线声明文件不存在", category=category, path=relative_path
        ) from error
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise BuildVerificationError(
            "baseline_path_escape",
            "基线声明文件必须位于项目根内",
            category=category,
            path=relative_path,
        )
    return resolved


def _expected_digest(value: object, *, category: str, path: object) -> str:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise BuildVerificationError(
            "baseline_invalid_hash", "基线声明含非法 SHA-256", category=category, path=path
        )
    return value.lower()


def _verify_digest(
    path: Path,
    expected: object,
    *,
    category: str,
    logical_path: object,
    text_lf: bool = False,
) -> None:
    expected_digest = _expected_digest(expected, category=category, path=logical_path)
    actual = _text_lf_sha256(path) if text_lf else _sha256(path)
    if actual != expected_digest:
        raise BuildVerificationError(
            "baseline_hash_mismatch",
            "冻结 v0.1 基线文件哈希发生漂移",
            category=category,
            path=logical_path,
            expected_sha256=expected_digest,
            actual_sha256=actual,
        )


def verify_v01_baseline(
    project_root: str | Path,
    snapshot_path: str | Path,
    *,
    expected_snapshot_id: str = EXPECTED_V01_SNAPSHOT_ID,
) -> dict[str, object]:
    """Verify all byte-reproducible files declared by the frozen v0.1 snapshot."""

    root = Path(project_root).resolve(strict=True)
    if not root.is_dir():
        raise BuildVerificationError("baseline_root_not_directory", "项目根不是目录")
    snapshot_candidate = Path(snapshot_path)
    if not snapshot_candidate.is_absolute():
        snapshot_candidate = root / snapshot_candidate
    try:
        snapshot_resolved = snapshot_candidate.resolve(strict=True)
    except OSError as error:
        raise BuildVerificationError(
            "baseline_snapshot_missing", "冻结 v0.1 快照 JSON 不存在", path=str(snapshot_candidate)
        ) from error
    if not snapshot_resolved.is_file() or not snapshot_resolved.is_relative_to(root):
        raise BuildVerificationError(
            "baseline_snapshot_path_escape", "冻结 v0.1 快照 JSON 必须位于项目根内"
        )
    snapshot = _load_json_object(snapshot_resolved, code="baseline_snapshot_invalid_json")
    if snapshot.get("schema_version") != "v0.1" or snapshot.get("snapshot_id") != expected_snapshot_id:
        raise BuildVerificationError(
            "baseline_snapshot_mismatch",
            "快照身份或 schema_version 与冻结 v0.1 基线不一致",
            expected_snapshot_id=expected_snapshot_id,
            actual_snapshot_id=snapshot.get("snapshot_id"),
            actual_schema_version=snapshot.get("schema_version"),
        )

    input_rows = snapshot.get("input_hashes")
    if not isinstance(input_rows, list):
        raise BuildVerificationError("baseline_invalid_structure", "input_hashes 必须是数组")
    inputs_verified = 0
    for row in input_rows:
        if not isinstance(row, dict):
            raise BuildVerificationError("baseline_invalid_structure", "input_hashes 行必须是对象")
        logical_path = row.get("raw_path")
        path = _project_file(root, logical_path, category="input")
        _verify_digest(path, row.get("sha256"), category="input", logical_path=logical_path)
        inputs_verified += 1

    outputs = snapshot.get("outputs")
    if not isinstance(outputs, dict):
        raise BuildVerificationError("baseline_invalid_structure", "outputs 必须是对象")
    outputs_verified = 0
    skipped_outputs = 0
    for output_name, row in outputs.items():
        if not isinstance(row, dict):
            raise BuildVerificationError("baseline_invalid_structure", "outputs 项必须是对象")
        if "sha256" not in row:
            if row.get("byte_reproducible") is False:
                skipped_outputs += 1
                continue
            raise BuildVerificationError(
                "baseline_missing_hash",
                "未声明不可字节复现的输出必须提供 SHA-256",
                output=output_name,
            )
        logical_path = row.get("path")
        path = _project_file(root, logical_path, category="output")
        _verify_digest(path, row.get("sha256"), category="output", logical_path=logical_path)
        size = row.get("size_bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size != path.stat().st_size:
            raise BuildVerificationError(
                "baseline_size_mismatch",
                "冻结 v0.1 输出文件大小发生漂移",
                output=output_name,
                path=logical_path,
            )
        outputs_verified += 1

    pipeline = snapshot.get("pipeline")
    pipeline_rows = pipeline.get("files") if isinstance(pipeline, dict) else None
    if not isinstance(pipeline_rows, list):
        raise BuildVerificationError("baseline_invalid_structure", "pipeline.files 必须是数组")
    pipeline_verified = 0
    for row in pipeline_rows:
        if not isinstance(row, dict):
            raise BuildVerificationError("baseline_invalid_structure", "pipeline.files 行必须是对象")
        logical_path = row.get("path")
        path = _project_file(root, logical_path, category="pipeline")
        _verify_digest(
            path,
            row.get("sha256_text_lf"),
            category="pipeline",
            logical_path=logical_path,
            text_lf=True,
        )
        pipeline_verified += 1

    return {
        "status": "baseline_verified",
        "snapshot_id": expected_snapshot_id,
        "input_files_verified": inputs_verified,
        "output_files_verified": outputs_verified,
        "pipeline_files_verified": pipeline_verified,
        "skipped_non_byte_reproducible_outputs": skipped_outputs,
    }
