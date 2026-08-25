"""合并外部片段DFT基础扫描与仅失败角重试，保留逐尝试审计。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from 汇总RESP敏感性 import sha256
from 汇总氨基甲酸酯受约束松弛 import (
    _verify_directory,
    reconcile_relaxed_attempts,
)


EXPECTED_ANGLES = {-180, -120, -60, 0, 60, 120}


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def write_release(
    base_directory: Path,
    retry_directories: Sequence[Path],
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    if not retry_directories:
        raise ValueError("外部DFT重试合并至少需要一个重试目录")
    base_manifest, base_table, base_record = _verify_directory(base_directory)
    base_table = base_table.copy()
    base_table["attempt_kind"] = "base_v1"
    base_table["attempt_release_id"] = base_manifest["release_id"]
    base_table["optimizer_profile"] = base_manifest.get(
        "optimizer_profile", "difficult_v2"
    )
    base_table["geom_maxiter"] = base_manifest["geom_maxiter"]
    retry_tables = []
    retry_records: dict[str, dict[str, Any]] = {}
    for directory in retry_directories:
        manifest, table, record = _verify_directory(directory)
        for field in (
            "fragment_name",
            "validation_family",
            "smiles",
            "method",
            "basis",
            "constraint",
        ):
            if manifest.get(field) != base_manifest.get(field):
                raise ValueError(f"外部DFT重试协议字段不一致: {field}")
        if not str(manifest.get("optimizer_profile", "")).startswith("difficult_"):
            raise ValueError("外部DFT重试未声明difficult系列策略")
        table = table.copy()
        table["attempt_kind"] = "retry_v2"
        table["attempt_release_id"] = manifest["release_id"]
        table["optimizer_profile"] = manifest["optimizer_profile"]
        table["geom_maxiter"] = manifest["geom_maxiter"]
        retry_tables.append(table)
        retry_records[str(manifest["release_id"])] = record
    selected, audit = reconcile_relaxed_attempts(
        base_table, pd.concat(retry_tables, ignore_index=True)
    )
    if set(selected["requested_angle_degrees"].astype(int)) != EXPECTED_ANGLES:
        raise ValueError("外部DFT重试后六角度网格不闭合")
    if not selected["point_status"].eq("completed").all():
        raise ValueError("外部DFT重试后仍有未完成角度")
    output_root.mkdir(parents=True, exist_ok=True)
    table_out = output_root / "relaxed_scan.csv"
    audit_out = output_root / "重试审计.csv"
    report_out = output_root / "外部DFT重试合并说明.md"
    _atomic_text(table_out, selected.to_csv(index=False, float_format="%.12g"))
    _atomic_text(audit_out, audit.to_csv(index=False, float_format="%.12g"))
    _atomic_text(
        report_out,
        "\n".join(
            [
                "# 外部DFT失败角重试合并",
                "",
                "只以协议一致的difficult系列重试替换基础扫描失败角；基础成功角禁止覆盖。相对能在合并后的六个成功角上重新归零，所有尝试保留在重试审计表。",
                "",
            ]
        ),
    )
    files = [table_out, audit_out, report_out]
    manifest = {
        "release_id": release_id,
        "status": "completed_constrained_relaxed_dft_points",
        "fragment_name": base_manifest["fragment_name"],
        "validation_family": base_manifest["validation_family"],
        "smiles": base_manifest["smiles"],
        "method": base_manifest["method"],
        "basis": base_manifest["basis"],
        "constraint": base_manifest["constraint"],
        "optimizer_profile": "base_plus_difficult_retry_reconciled",
        "geom_maxiter": max(
            [int(base_manifest["geom_maxiter"])]
            + [int(table["geom_maxiter"].max()) for table in retry_tables]
        ),
        "counts": {
            "planned": len(selected),
            "completed": int(selected["point_status"].eq("completed").sum()),
            "failed": 0,
            "attempt_rows": len(audit),
        },
        "maximum_angle_drift_degrees": float(
            selected["angle_drift_degrees"].max()
        ),
        "inputs": {
            "base": base_record,
            "retries": retry_records,
        },
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "production_md_permission": "blocked_external_validation_scoring_pending",
        "performance_claim_status": "no_performance_claim",
    }
    _atomic_text(
        output_root / "受约束松弛清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--基础目录", type=Path, required=True)
    parser.add_argument("--重试目录", type=Path, action="append", required=True)
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument("--发布ID", required=True)
    args = parser.parse_args(argv)
    manifest = write_release(
        args.基础目录,
        args.重试目录,
        args.输出目录,
        release_id=args.发布ID,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
