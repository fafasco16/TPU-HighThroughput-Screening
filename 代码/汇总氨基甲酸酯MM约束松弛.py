"""核验脂肪/芳香8点LAMMPS约束松弛并连接对应刚性GAFF2曲线。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from 汇总RESP敏感性 import sha256


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRECTORIES = [
    ROOT / "计算" / "现实MD" / "MM松弛扫描_脂肪族氨基甲酸酯",
    ROOT / "计算" / "现实MD" / "MM松弛扫描_芳香族氨基甲酸酯",
]


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _verify_directory(
    directory: Path,
) -> tuple[dict[str, Any], pd.DataFrame, dict[str, Any]]:
    manifest_path = directory / "MM约束松弛清单.json"
    if not manifest_path.is_file():
        raise ValueError(f"MM约束松弛清单不存在: {directory}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "completed_mm_constrained_relaxed_points":
        raise ValueError(f"MM约束松弛未全部完成: {directory}")
    for name, record in manifest["files"].items():
        path = directory / name
        if (
            not path.is_file()
            or path.stat().st_size != int(record["bytes"])
            or sha256(path) != record["sha256"]
        ):
            raise ValueError(f"MM约束松弛顶层文件哈希不闭合: {path}")
    for record in manifest["point_files"]:
        path = directory / record["path"]
        if (
            not path.is_file()
            or path.stat().st_size != int(record["bytes"])
            or sha256(path) != record["sha256"]
        ):
            raise ValueError(f"MM约束松弛点文件哈希不闭合: {path}")
    table = pd.read_csv(directory / "mm_relaxed_scan.csv")
    if len(table) != 4 or not table["point_status"].astype(str).eq("completed").all():
        raise ValueError(f"MM约束松弛不是4个完成点: {directory}")
    record = {
        "fragment_name": manifest["fragment_name"],
        "manifest_path": str(manifest_path),
        "manifest_bytes": manifest_path.stat().st_size,
        "manifest_sha256": sha256(manifest_path),
    }
    return manifest, table, record


def join_rigid_mm(rigid: pd.DataFrame, mm: pd.DataFrame) -> pd.DataFrame:
    rigid_columns = [
        "fragment_name",
        "validation_family",
        "requested_angle_degrees",
        "gaff2_relative_energy_kcal_mol",
    ]
    missing = sorted(set(rigid_columns).difference(rigid.columns))
    if missing:
        raise ValueError(f"刚性GAFF2曲线缺字段: {missing}")
    joined = mm.merge(
        rigid[rigid_columns],
        on=["fragment_name", "validation_family", "requested_angle_degrees"],
        how="left",
        validate="one_to_one",
    )
    if joined["gaff2_relative_energy_kcal_mol"].isna().any():
        raise ValueError("MM松弛点未完全连接刚性GAFF2曲线")
    joined["mm_relaxation_change_from_rigid_kcal_mol"] = (
        joined["relaxed_gaff2_relative_energy_kcal_mol"]
        - joined["gaff2_relative_energy_kcal_mol"]
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
        raise ValueError("MM约束松弛汇总必须包含两个家族")
    if not rigid_curve_path.is_file():
        raise ValueError(f"刚性曲线不存在: {rigid_curve_path}")
    tables = []
    manifests = []
    inputs = {}
    for directory in directories:
        manifest, table, record = _verify_directory(directory)
        manifests.append(manifest)
        tables.append(table)
        inputs[str(manifest["fragment_name"])] = record
    joined = join_rigid_mm(pd.read_csv(rigid_curve_path), pd.concat(tables, ignore_index=True))
    summary_rows = []
    for manifest in manifests:
        subset = joined.loc[joined["fragment_name"].eq(manifest["fragment_name"])]
        summary_rows.append(
            {
                "fragment_name": manifest["fragment_name"],
                "validation_family": manifest["validation_family"],
                "completed_points": len(subset),
                "maximum_angle_drift_degrees": float(
                    subset["angle_drift_degrees"].max()
                ),
                "maximum_absolute_mm_relaxation_change_kcal_mol": float(
                    subset["mm_relaxation_change_from_rigid_kcal_mol"].abs().max()
                ),
                "relaxed_gaff2_barrier_within_subset_kcal_mol": float(
                    subset["relaxed_gaff2_relative_energy_kcal_mol"].max()
                ),
                "restraint_k_kcal_mol_rad2": manifest[
                    "restraint_k_kcal_mol_rad2"
                ],
                "mm_relaxed_status": "completed_screening_parameter_refit_pending",
                "production_md_permission": "blocked_dft_relaxed_comparison_and_refit",
            }
        )
    summary = pd.DataFrame(summary_rows).sort_values(
        "validation_family", kind="stable"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    joined_out = output_root / "MM松弛与刚性对比.csv"
    summary_out = output_root / "MM约束松弛汇总.csv"
    report_out = output_root / "MM约束松弛说明.md"
    archive_out = output_root / "原始归档定位.json"
    _atomic_text(joined_out, joined.to_csv(index=False, float_format="%.12g"))
    _atomic_text(summary_out, summary.to_csv(index=False, float_format="%.12g"))
    _atomic_text(
        report_out,
        "\n".join(
            [
                "# LAMMPS氨基甲酸酯二面角约束松弛",
                "",
                "脂肪/芳香各4个角度使用GAFF2替代参数、三构象联合RESP电荷和K=5000 kcal mol⁻¹ rad⁻²的临时二面角约束完成最小化。",
                "LAMMPS与RDKit二面角定义相差180°，输入按`LAMMPS phi0 = wrap(RDKit angle + 180°)`转换；每点均解除约束后`run 0`读取GAFF2能量。",
                "",
                "该结果用于与相同目标角的DFT冻结二面角松弛比较。约束最小化的能量容差停止和有限角度漂移不等于新参数已经验证；原始点目录仅服务器归档，Git保存紧凑曲线与哈希。",
                "",
            ]
        ),
    )
    if raw_archive is not None:
        if not raw_archive.is_file():
            raise ValueError(f"MM约束松弛原始归档不存在: {raw_archive}")
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
    files = [joined_out, summary_out, report_out, *([archive_out] if archive_record else [])]
    manifest = {
        "release_id": release_id,
        "status": "eight_mm_constrained_relaxed_points_completed_dft_comparison_pending",
        "counts": {"fragments": 2, "completed_points": len(joined)},
        "maximum_angle_drift_degrees": float(summary["maximum_angle_drift_degrees"].max()),
        "maximum_absolute_mm_relaxation_change_kcal_mol": float(
            summary["maximum_absolute_mm_relaxation_change_kcal_mol"].max()
        ),
        "inputs": {
            "mm_manifests": inputs,
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
        "production_md_permission": "blocked_dft_relaxed_comparison_and_refit",
        "performance_claim_status": "no_performance_claim",
    }
    _atomic_text(
        output_root / "MM约束松弛发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--MM目录", type=Path, action="append")
    parser.add_argument("--刚性曲线", type=Path, required=True)
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument("--原始归档", type=Path)
    parser.add_argument(
        "--发布ID", default="tpu-reality-md-urethane-mm-constrained-relax-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.MM目录 or DEFAULT_DIRECTORIES,
        args.刚性曲线,
        args.输出目录,
        release_id=args.发布ID,
        raw_archive=args.原始归档,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
