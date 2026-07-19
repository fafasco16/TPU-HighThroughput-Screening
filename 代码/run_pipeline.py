"""TPU 数据库命令行入口：v0.1 构建与只读 v0.2 治理审计。"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Sequence

from build_verification import (
    ASSET_OUTPUT_FILES,
    BuildVerificationError,
    audit_asset_build,
    compare_asset_builds,
    verify_v01_baseline,
)
from asset_registry import AssetRegistryError
from computational_admission import ComputationalAdmissionError
from contract import ContractValidationError, load_contract_bundle
from database_build import SCHEMA_VERSION as DATABASE_SCHEMA_VERSION
from database_build import DatabaseBuildResult, build_database
from manifest import build_manifest, build_manifest_from_config, write_manifest_csv
from governance_build import (
    DEFAULT_ASSET_RULES as DEFAULT_V02_ASSET_RULES,
    DEFAULT_CONTRACT_SCHEMA as DEFAULT_V02_CONTRACT_SCHEMA,
    DEFAULT_ENUMS as DEFAULT_V02_ENUMS,
    DEFAULT_QUALITY_RULES as DEFAULT_V02_QUALITY_RULES,
    DEFAULT_SOURCE_SCOPE_CONFIG as DEFAULT_V02_SOURCE_SCOPES,
    DEFAULT_V01_SNAPSHOT,
    GovernanceBuildError,
    audit_governance_build,
    build_governance_database,
    compare_governance_builds,
)
from source_governance import SourceGovernanceError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_CONFIG = Path("配置/数据源.yaml")
DEFAULT_MANIFEST = Path("清单/来源清单.csv")


def build_full_manifest(
    project_root: str | Path = PROJECT_ROOT,
    config_path: str | Path = DEFAULT_SOURCE_CONFIG,
    output_path: str | Path = DEFAULT_MANIFEST,
) -> list[dict[str, object]]:
    """Hash every raw file and overlay detailed metadata for registered sources."""

    root = Path(project_root).resolve(strict=True)
    registered_rows = build_manifest_from_config(root, config_path)
    generic_rows = build_manifest(
        root,
        root / "01_原始数据",
        metadata={
            "source_id": "raw_vault_unregistered",
            "doi": "",
            "url": "",
            "accessed_at": "2026-07-18",
            "license_spdx": "UNKNOWN",
            "derivatives_allowed": None,
            "redistribution_allowed": None,
            "access_restriction": "unknown",
            "evidence_grade": "metadata_only",
            "material_scope": "unknown",
            "status": "review_required",
            "notes": "尚未在配置/数据源.yaml逐来源登记；默认禁止发布。",
        },
    )
    generic_rows = [
        row
        for row in generic_rows
        if row["raw_path"] != "01_原始数据/README.md"
    ]
    by_path = {str(row["raw_path"]): row for row in generic_rows}
    for row in registered_rows:
        by_path[str(row["raw_path"])] = row
    rows = [by_path[path] for path in sorted(by_path, key=str.casefold)]
    output = Path(output_path)
    if not output.is_absolute():
        output = root / output
    write_manifest_csv(rows, output)
    return rows


def _manifest_summary(rows: Sequence[dict[str, object]]) -> dict[str, object]:
    return {
        "files": len(rows),
        "bytes": sum(int(row["size_bytes"]) for row in rows),
        "registered_files": sum(
            row["source_id"] != "raw_vault_unregistered" for row in rows
        ),
        "unregistered_files": sum(
            row["source_id"] == "raw_vault_unregistered" for row in rows
        ),
    }


def load_manifest_csv(
    project_root: str | Path = PROJECT_ROOT,
    manifest_path: str | Path = DEFAULT_MANIFEST,
) -> list[dict[str, str]]:
    """Load the committed UTF-8 manifest without silently rebuilding it."""

    root = Path(project_root).resolve(strict=True)
    path = Path(manifest_path)
    if not path.is_absolute():
        path = root / path
    with path.resolve(strict=True).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("来源清单为空；请先运行 manifest 子命令")
    return rows


def build_from_manifest(
    project_root: str | Path = PROJECT_ROOT,
    manifest_path: str | Path = DEFAULT_MANIFEST,
) -> DatabaseBuildResult:
    """Build the four-source v0.1 vertical slice from a frozen manifest."""

    root = Path(project_root).resolve(strict=True)
    return build_database(root, load_manifest_csv(root, manifest_path))


def _build_summary(result: DatabaseBuildResult, *, include_issues: bool) -> dict[str, object]:
    payload: dict[str, object] = {
        "snapshot_id": result.snapshot_id,
        "schema_version": DATABASE_SCHEMA_VERSION,
        "has_errors": result.has_errors,
        "issue_count": len(result.issues),
        "row_counts": dict(result.row_counts),
    }
    if include_issues:
        payload["issues"] = [issue.__dict__ for issue in result.issues]
    return payload


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TPU 数据库 v0.1 数据管道")
    subparsers = parser.add_subparsers(dest="command", required=True)
    manifest_parser = subparsers.add_parser("manifest", help="生成完整来源文件清单")
    manifest_parser.add_argument("--config", default=str(DEFAULT_SOURCE_CONFIG))
    manifest_parser.add_argument("--output", default=str(DEFAULT_MANIFEST))
    contract_parser = subparsers.add_parser(
        "contract-audit", help="只读审计一个显式指定的版本化合同三件套"
    )
    contract_parser.add_argument("--schema", required=True)
    contract_parser.add_argument("--enums", required=True)
    contract_parser.add_argument("--rules", required=True)
    compare_parser = subparsers.add_parser(
        "compare-builds", help="只读比较两个隔离 v0.2 资产构建"
    )
    compare_parser.add_argument("--left", required=True)
    compare_parser.add_argument("--right", required=True)
    compare_parser.add_argument("--require-byte-identical-csv", action="store_true")
    compare_parser.add_argument("--require-logical-hash-identical", action="store_true")
    audit_parser = subparsers.add_parser(
        "asset-audit", help="只读核验一个隔离 v0.2 资产构建"
    )
    audit_parser.add_argument("--build-root", required=True)
    audit_parser.add_argument("--report", required=True)
    baseline_parser = subparsers.add_parser(
        "verify-v0.1-baseline", help="只读复核冻结 v0.1 快照声明的全部哈希"
    )
    baseline_parser.add_argument("--snapshot", required=True)
    governance_build_parser = subparsers.add_parser(
        "governance-build", help="原子生成一个隔离的 v0.2 全量治理构建"
    )
    governance_build_parser.add_argument("--output-root", required=True)
    governance_build_parser.add_argument("--asset-rules", default=str(DEFAULT_V02_ASSET_RULES))
    governance_build_parser.add_argument("--source-scopes", default=str(DEFAULT_V02_SOURCE_SCOPES))
    governance_build_parser.add_argument("--schema", default=str(DEFAULT_V02_CONTRACT_SCHEMA))
    governance_build_parser.add_argument("--enums", default=str(DEFAULT_V02_ENUMS))
    governance_build_parser.add_argument("--rules", default=str(DEFAULT_V02_QUALITY_RULES))
    governance_build_parser.add_argument("--v01-snapshot", default=str(DEFAULT_V01_SNAPSHOT))
    governance_audit_parser = subparsers.add_parser(
        "governance-audit", help="从落盘产物重算并核验 v0.2 治理构建"
    )
    governance_audit_parser.add_argument("--build-root", required=True)
    governance_compare_parser = subparsers.add_parser(
        "compare-governance-builds", help="严格比较两个治理构建的全部产物"
    )
    governance_compare_parser.add_argument("--left", required=True)
    governance_compare_parser.add_argument("--right", required=True)
    for command, help_text in (
        ("build", "构建四源 v0.1 分层数据库与快照"),
        ("qc", "重新构建并输出完整质量检查结果"),
    ):
        command_parser = subparsers.add_parser(command, help=help_text)
        command_parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
        command_parser.add_argument(
            "--version", default=DATABASE_SCHEMA_VERSION, choices=[DATABASE_SCHEMA_VERSION]
        )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    if args.command == "manifest":
        rows = build_full_manifest(PROJECT_ROOT, args.config, args.output)
        print(json.dumps(_manifest_summary(rows), ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "contract-audit":
        try:
            bundle = load_contract_bundle(args.schema, args.enums, args.rules)
        except ContractValidationError as error:
            print(
                json.dumps(
                    {"status": "contract_invalid", "error": error.as_dict()},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 2
        print(
            json.dumps(
                {
                    "status": "contract_valid",
                    "schema_version": bundle.schema_version,
                    "table_count": len(bundle.schema["tables"]),
                    "rule_count": len(bundle.rules["rules"]),
                    "document_hashes": bundle.document_hashes,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    if args.command in {"compare-builds", "asset-audit", "verify-v0.1-baseline"}:
        try:
            if args.command == "compare-builds":
                payload = compare_asset_builds(
                    args.left,
                    args.right,
                    require_byte_identical_csv=args.require_byte_identical_csv,
                    require_logical_hash_identical=args.require_logical_hash_identical,
                )
            elif args.command == "asset-audit":
                expected_report = (Path(args.build_root) / ASSET_OUTPUT_FILES[3]).resolve()
                actual_report = Path(args.report).resolve()
                if actual_report != expected_report:
                    raise BuildVerificationError(
                        "asset_audit_report_path_mismatch",
                        "--report 必须指向 --build-root 内的标准资产审计文件",
                        expected_report=str(expected_report),
                        actual_report=str(actual_report),
                    )
                payload = audit_asset_build(args.build_root)
            else:
                payload = verify_v01_baseline(PROJECT_ROOT, args.snapshot)
        except BuildVerificationError as error:
            print(
                json.dumps(
                    {"status": "verification_failed", "error": error.as_dict()},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 2
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command in {"governance-build", "governance-audit", "compare-governance-builds"}:
        try:
            if args.command == "governance-build":
                result = build_governance_database(
                    PROJECT_ROOT,
                    args.output_root,
                    asset_rules_path=args.asset_rules,
                    source_scope_path=args.source_scopes,
                    contract_schema_path=args.schema,
                    enums_path=args.enums,
                    quality_rules_path=args.rules,
                    v01_snapshot_path=args.v01_snapshot,
                )
                payload = {
                    "status": "provisional_pass",
                    "output_root": str(result.output_root),
                    "input_count": result.report["input_count"],
                    "snapshot_logical_hash": result.report["snapshot_logical_hash"],
                }
            elif args.command == "governance-audit":
                payload = audit_governance_build(args.build_root)
            else:
                payload = compare_governance_builds(args.left, args.right)
        except (
            GovernanceBuildError,
            AssetRegistryError,
            ComputationalAdmissionError,
            ContractValidationError,
            SourceGovernanceError,
        ) as error:
            if hasattr(error, "as_dict"):
                detail = error.as_dict()
            else:
                detail = {
                    "code": error.code,
                    "message": error.message,
                    "context": error.context,
                }
            print(
                json.dumps(
                    {"status": "governance_verification_failed", "error": detail},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 2
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command in {"build", "qc"}:
        result = build_from_manifest(PROJECT_ROOT, args.manifest)
        print(
            json.dumps(
                _build_summary(result, include_issues=args.command == "qc"),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1 if result.has_errors else 0
    raise ValueError(f"未知命令: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
