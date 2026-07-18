"""TPU 数据库 v0.1 命令行入口。"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Sequence

from database_build import SCHEMA_VERSION as DATABASE_SCHEMA_VERSION
from database_build import DatabaseBuildResult, build_database
from manifest import build_manifest, build_manifest_from_config, write_manifest_csv


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
