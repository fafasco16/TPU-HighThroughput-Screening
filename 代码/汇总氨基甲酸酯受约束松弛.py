"""核验脂肪/芳香冻结二面角松弛结果并连接对应刚性DFT点。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from 汇总RESP敏感性 import sha256


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRECTORIES = [
    ROOT / "计算" / "现实MD" / "松弛扫描_脂肪族氨基甲酸酯",
    ROOT / "计算" / "现实MD" / "松弛扫描_芳香族氨基甲酸酯",
]


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _verify_directory(
    directory: Path,
) -> tuple[dict[str, Any], pd.DataFrame, dict[str, Any]]:
    manifest_path = directory / "受约束松弛清单.json"
    if not manifest_path.is_file():
        raise ValueError(f"受约束松弛清单不存在: {directory}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") not in {
        "completed_constrained_relaxed_dft_points",
        "incomplete_constrained_relaxed_dft_points",
    }:
        raise ValueError(f"受约束松弛状态无效: {directory}")
    for name, record in manifest["files"].items():
        path = directory / name
        if (
            not path.is_file()
            or path.stat().st_size != int(record["bytes"])
            or sha256(path) != record["sha256"]
        ):
            raise ValueError(f"受约束松弛文件哈希不闭合: {path}")
    table = pd.read_csv(directory / "relaxed_scan.csv")
    if len(table) != int(manifest["counts"]["planned"]):
        raise ValueError(f"受约束松弛计划行数不闭合: {directory}")
    record = {
        "fragment_name": manifest["fragment_name"],
        "manifest_path": str(manifest_path),
        "manifest_bytes": manifest_path.stat().st_size,
        "manifest_sha256": sha256(manifest_path),
    }
    return manifest, table, record


def join_rigid_relaxed(
    rigid: pd.DataFrame, relaxed: pd.DataFrame
) -> pd.DataFrame:
    rigid_columns = [
        "fragment_name",
        "validation_family",
        "requested_angle_degrees",
        "dft_relative_energy_kcal_mol",
        "gaff2_relative_energy_kcal_mol",
        "gaff2_minus_dft_relative_energy_kcal_mol",
    ]
    missing = sorted(set(rigid_columns).difference(rigid.columns))
    if missing:
        raise ValueError(f"刚性扫描表缺字段: {missing}")
    joined = relaxed.merge(
        rigid[rigid_columns],
        on=[
            "fragment_name",
            "validation_family",
            "requested_angle_degrees",
        ],
        how="left",
        validate="one_to_one",
    )
    if joined["dft_relative_energy_kcal_mol"].isna().any():
        raise ValueError("受约束松弛点未完全连接刚性曲线")
    completed = joined["point_status"].eq("completed")
    joined.loc[completed, "relaxation_change_from_rigid_dft_kcal_mol"] = (
        joined.loc[completed, "relaxed_dft_relative_energy_kcal_mol"]
        - joined.loc[completed, "dft_relative_energy_kcal_mol"]
    )
    return joined.sort_values(
        ["validation_family", "requested_angle_degrees"], kind="stable"
    )


def write_release(
    directories: Sequence[Path],
    rigid_curve_path: Path,
    output_root: Path,
    *,
    release_id: str,
    raw_archive: Path | None = None,
) -> dict[str, Any]:
    if len(directories) != 2:
        raise ValueError("受约束松弛汇总必须包含脂肪和芳香两个模型")
    if not rigid_curve_path.is_file():
        raise ValueError(f"刚性扫描曲线不存在: {rigid_curve_path}")
    tables: list[pd.DataFrame] = []
    manifests: list[dict[str, Any]] = []
    inputs: dict[str, dict[str, Any]] = {}
    for directory in directories:
        manifest, table, record = _verify_directory(directory)
        manifests.append(manifest)
        tables.append(table)
        inputs[str(manifest["fragment_name"])] = record
    relaxed = pd.concat(tables, ignore_index=True)
    joined = join_rigid_relaxed(pd.read_csv(rigid_curve_path), relaxed)
    summary_rows = []
    for manifest in manifests:
        fragment = str(manifest["fragment_name"])
        subset = joined.loc[joined["fragment_name"].eq(fragment)]
        completed = subset.loc[subset["point_status"].eq("completed")]
        summary_rows.append(
            {
                "fragment_name": fragment,
                "validation_family": manifest["validation_family"],
                "planned_points": len(subset),
                "completed_points": len(completed),
                "failed_points": len(subset) - len(completed),
                "maximum_angle_drift_degrees": (
                    float(completed["angle_drift_degrees"].max())
                    if not completed.empty
                    else pd.NA
                ),
                "maximum_absolute_relaxation_change_kcal_mol": (
                    float(
                        completed[
                            "relaxation_change_from_rigid_dft_kcal_mol"
                        ].abs().max()
                    )
                    if not completed.empty
                    else pd.NA
                ),
                "elapsed_seconds": float(manifest["elapsed_seconds"]),
                "relaxed_validation_status": (
                    "completed_relaxed_dft_subset_mm_refit_pending"
                    if len(completed) == len(subset)
                    else "incomplete_relaxed_dft_subset"
                ),
                "production_md_permission": "blocked_mm_relaxed_refit_and_external_validation",
            }
        )
    summary = pd.DataFrame(summary_rows).sort_values(
        "validation_family", kind="stable"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    joined_out = output_root / "松弛与刚性对比.csv"
    summary_out = output_root / "受约束松弛汇总.csv"
    report_out = output_root / "受约束松弛说明.md"
    archive_out = output_root / "原始归档定位.json"
    _atomic_text(joined_out, joined.to_csv(index=False, float_format="%.12g"))
    _atomic_text(summary_out, summary.to_csv(index=False, float_format="%.12g"))
    _atomic_text(
        report_out,
        "\n".join(
            [
                "# 氨基甲酸酯冻结二面角松弛结果",
                "",
                "每个家族包含DFT刚性最低点、DFT刚性最高点、最大DFT–GAFF2误差点和|角度|=150°肩部，共8个信息互补点。",
                "Psi4/OptKing冻结O=C-N-R二面角并以ωB97M-D3BJ/6-31G(d,p)松弛其余自由度；逐点保留QCHEM收敛状态和最终角度漂移。",
                "",
                "相对能仅在各家族成功松弛点内归零。`relaxation_change_from_rigid_dft`用于判断刚性屏蔽结论对几何松弛是否敏感，不是GAFF2松弛误差；下一步仍需相同约束下的MM最小化和参数重拟合。",
                "失败点保持失败，不插值、不用刚性能量替代。",
                "",
            ]
        ),
    )
    if raw_archive is not None:
        if not raw_archive.is_file():
            raise ValueError(f"受约束松弛原始归档不存在: {raw_archive}")
        archive_record: dict[str, Any] | None = {
            "storage": "server_only_not_committed_to_git",
            "path": str(raw_archive),
            "bytes": raw_archive.stat().st_size,
            "sha256": sha256(raw_archive),
        }
        _atomic_text(
            archive_out,
            json.dumps(archive_record, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
    else:
        archive_record = None
    files = [
        joined_out,
        summary_out,
        report_out,
        *([archive_out] if archive_record is not None else []),
    ]
    manifest = {
        "release_id": release_id,
        "status": (
            "constrained_relaxed_dft_subset_completed_mm_refit_pending"
            if summary["failed_points"].astype(int).sum() == 0
            else "constrained_relaxed_dft_subset_incomplete"
        ),
        "counts": {
            "fragments": len(summary),
            "planned_points": int(summary["planned_points"].sum()),
            "completed_points": int(summary["completed_points"].sum()),
            "failed_points": int(summary["failed_points"].sum()),
        },
        "maximum_angle_drift_degrees": (
            float(summary["maximum_angle_drift_degrees"].dropna().max())
            if summary["maximum_angle_drift_degrees"].notna().any()
            else None
        ),
        "maximum_absolute_relaxation_change_kcal_mol": (
            float(
                summary["maximum_absolute_relaxation_change_kcal_mol"]
                .dropna()
                .max()
            )
            if summary["maximum_absolute_relaxation_change_kcal_mol"]
            .notna()
            .any()
            else None
        ),
        "inputs": {
            "relaxed_manifests": inputs,
            "rigid_curve": {
                "path": str(rigid_curve_path),
                "bytes": rigid_curve_path.stat().st_size,
                "sha256": sha256(rigid_curve_path),
            },
        },
        "raw_archive": archive_record,
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "production_md_permission": "blocked_mm_relaxed_refit_and_external_validation",
        "performance_claim_status": "no_performance_claim",
    }
    _atomic_text(
        output_root / "受约束松弛发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--松弛目录", type=Path, action="append")
    parser.add_argument("--刚性曲线", type=Path, required=True)
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument("--原始归档", type=Path)
    parser.add_argument(
        "--发布ID", default="tpu-reality-md-urethane-constrained-relax-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.松弛目录 or DEFAULT_DIRECTORIES,
        args.刚性曲线,
        args.输出目录,
        release_id=args.发布ID,
        raw_archive=args.原始归档,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0 if manifest["counts"]["failed_points"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
