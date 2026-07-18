"""TPU 数据库 v0.1 命令行入口。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

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


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TPU 数据库 v0.1 数据管道")
    subparsers = parser.add_subparsers(dest="command", required=True)
    manifest_parser = subparsers.add_parser("manifest", help="生成完整来源文件清单")
    manifest_parser.add_argument("--config", default=str(DEFAULT_SOURCE_CONFIG))
    manifest_parser.add_argument("--output", default=str(DEFAULT_MANIFEST))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    if args.command == "manifest":
        rows = build_full_manifest(PROJECT_ROOT, args.config, args.output)
        print(json.dumps(_manifest_summary(rows), ensure_ascii=False, sort_keys=True))
        return 0
    raise ValueError(f"未知命令: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
