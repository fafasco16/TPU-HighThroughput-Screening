"""汇总脂肪/芳香氨基甲酸酯刚性DFT–GAFF2扫描并选择松弛复算角度。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from 汇总RESP敏感性 import sha256


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRECTORIES = [
    ROOT / "计算" / "现实MD" / "刚性扫描_脂肪族氨基甲酸酯",
    ROOT / "计算" / "现实MD" / "刚性扫描_芳香族氨基甲酸酯",
]


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def classify_rigid_scan(metrics: dict[str, Any]) -> str:
    if (
        float(metrics["curve_rmse_kcal_mol"]) > 5.0
        or abs(float(metrics["barrier_difference_gaff2_minus_dft_kcal_mol"]))
        > 5.0
        or float(metrics["curve_pearson_r"]) < 0.8
    ):
        return "failed_rigid_screen_large_curve_mismatch"
    return "conditional_rigid_screen_relaxed_scan_required"


def _verify_directory(
    directory: Path,
) -> tuple[dict[str, Any], pd.DataFrame, dict[str, Any]]:
    manifest_path = directory / "刚性扫描清单.json"
    if not manifest_path.is_file():
        raise ValueError(f"刚性扫描清单不存在: {directory}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "completed_rigid_dft_gaff2_scan_screening":
        raise ValueError(f"刚性扫描未完成: {directory}")
    for name, record in manifest["files"].items():
        path = directory / name
        if (
            not path.is_file()
            or path.stat().st_size != int(record["bytes"])
            or sha256(path) != record["sha256"]
        ):
            raise ValueError(f"刚性扫描文件哈希不闭合: {path}")
    curve = pd.read_csv(directory / "rigid_scan.csv")
    if len(curve) != 24 or not curve["point_status"].astype(str).eq("completed").all():
        raise ValueError(f"刚性扫描不是24个完成点: {directory}")
    record = {
        "fragment_name": manifest["fragment_name"],
        "manifest_path": str(manifest_path),
        "manifest_bytes": manifest_path.stat().st_size,
        "manifest_sha256": sha256(manifest_path),
    }
    return manifest, curve, record


def select_relaxed_angles(curves: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (fragment, family), subset in curves.groupby(
        ["fragment_name", "validation_family"], sort=True
    ):
        subset = subset.copy()
        subset["absolute_curve_error_kcal_mol"] = (
            subset["gaff2_relative_energy_kcal_mol"]
            - subset["dft_relative_energy_kcal_mol"]
        ).abs()
        selected: dict[int, dict[str, Any]] = {}

        def add(source: pd.Series, reason: str) -> None:
            angle = int(source["requested_angle_degrees"])
            if angle not in selected:
                selected[angle] = {
                    "fragment_name": fragment,
                    "validation_family": family,
                    "angle_degrees": angle,
                    "selection_reason": reason,
                    "rigid_absolute_curve_error_kcal_mol": float(
                        source["absolute_curve_error_kcal_mol"]
                    ),
                }

        add(
            subset.loc[subset["dft_relative_energy_kcal_mol"].idxmin()],
            "dft_rigid_minimum",
        )
        add(
            subset.loc[subset["dft_relative_energy_kcal_mol"].idxmax()],
            "dft_rigid_maximum",
        )
        add(
            subset.loc[subset["absolute_curve_error_kcal_mol"].idxmax()],
            "largest_rigid_curve_error",
        )
        shoulder = subset.loc[
            subset["requested_angle_degrees"].abs().eq(150)
        ]
        if not shoulder.empty:
            add(
                shoulder.loc[shoulder["absolute_curve_error_kcal_mol"].idxmax()],
                "planarity_breaking_shoulder_abs150",
            )
        if len(selected) < 4:
            for _, source in subset.sort_values(
                "absolute_curve_error_kcal_mol", ascending=False, kind="stable"
            ).iterrows():
                add(source, "next_largest_nonduplicate_rigid_error")
                if len(selected) == 4:
                    break
        rows.extend(selected.values())
    return pd.DataFrame(rows).sort_values(
        ["validation_family", "angle_degrees"], kind="stable"
    )


def write_release(
    directories: Sequence[Path],
    output_root: Path,
    *,
    release_id: str,
    raw_archive: Path | None = None,
) -> dict[str, Any]:
    if len(directories) != 2:
        raise ValueError("刚性扫描汇总必须包含脂肪和芳香两个模型")
    manifests: list[dict[str, Any]] = []
    curves: list[pd.DataFrame] = []
    inputs: dict[str, dict[str, Any]] = {}
    for directory in directories:
        manifest, curve, record = _verify_directory(directory)
        manifests.append(manifest)
        curves.append(curve)
        inputs[str(manifest["fragment_name"])] = record
    curve_table = pd.concat(curves, ignore_index=True).sort_values(
        ["validation_family", "requested_angle_degrees"], kind="stable"
    )
    curve_table["gaff2_minus_dft_relative_energy_kcal_mol"] = (
        curve_table["gaff2_relative_energy_kcal_mol"]
        - curve_table["dft_relative_energy_kcal_mol"]
    )
    summary_rows = []
    for manifest in manifests:
        summary_rows.append(
            {
                "fragment_name": manifest["fragment_name"],
                "validation_family": manifest["validation_family"],
                **manifest["metrics"],
                "target_gaff2_dihedral_type": manifest[
                    "target_gaff2_dihedral_type"
                ],
                "target_gaff2_dihedral_k": json.dumps(
                    manifest["target_gaff2_dihedral_k"], separators=(",", ":")
                ),
                "target_gaff2_dihedral_periodicity": json.dumps(
                    manifest["target_gaff2_dihedral_periodicity"],
                    separators=(",", ":"),
                ),
                "rigid_validation_status": classify_rigid_scan(
                    manifest["metrics"]
                ),
                "production_md_permission": "blocked_relaxed_scan_and_parameter_refit",
            }
        )
    summary = pd.DataFrame(summary_rows).sort_values(
        "validation_family", kind="stable"
    )
    relaxed = select_relaxed_angles(curve_table)
    output_root.mkdir(parents=True, exist_ok=True)
    curve_out = output_root / "刚性扫描曲线.csv"
    summary_out = output_root / "刚性扫描汇总.csv"
    relaxed_out = output_root / "受约束松弛候选.csv"
    report_out = output_root / "刚性扫描说明.md"
    archive_out = output_root / "原始归档定位.json"
    _atomic_text(curve_out, curve_table.to_csv(index=False, float_format="%.12g"))
    _atomic_text(summary_out, summary.to_csv(index=False, float_format="%.12g"))
    _atomic_text(relaxed_out, relaxed.to_csv(index=False, float_format="%.12g"))
    _atomic_text(
        report_out,
        "\n".join(
            [
                "# 氨基甲酸酯O=C-N-R刚性势能扫描",
                "",
                "脂肪族与芳香族模型均在-180°至165°、15°间隔的24个完全相同刚性几何上比较ωB97M-D3BJ/6-31G(d,p)与GAFF2+三构象联合RESP。",
                "该扫描同时包含键、角、二面角、improper、静电和LJ项，回答当前替代参数组合能否复现量化曲线；它不是受约束松弛势能面。",
                "",
                "项目筛查门为RMSE>5 kcal/mol、势垒绝对差>5 kcal/mol或Pearson r<0.8任一触发失败。该门用于决定是否必须重拟合，不宣称是通用力场标准。",
                "`受约束松弛候选.csv`对每个家族固定选择DFT最低点、DFT最高点、最大刚性误差点和|角度|=150°的平面性破坏肩部，共8个信息互补点，避免最大误差角度在±180°附近过度聚集。",
                "",
            ]
        ),
    )
    if raw_archive is not None:
        if not raw_archive.is_file():
            raise ValueError(f"刚性扫描原始归档不存在: {raw_archive}")
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
        curve_out,
        summary_out,
        relaxed_out,
        report_out,
        *([archive_out] if archive_record is not None else []),
    ]
    manifest = {
        "release_id": release_id,
        "status": "rigid_scan_completed_aromatic_forcefield_validation_failed",
        "counts": {
            "fragments": len(summary),
            "scan_points": len(curve_table),
            "relaxed_candidate_points": len(relaxed),
            "failed_rigid_screens": int(
                summary["rigid_validation_status"]
                .eq("failed_rigid_screen_large_curve_mismatch")
                .sum()
            ),
        },
        "maximum_curve_rmse_kcal_mol": float(
            summary["curve_rmse_kcal_mol"].max()
        ),
        "maximum_absolute_barrier_difference_kcal_mol": float(
            summary["barrier_difference_gaff2_minus_dft_kcal_mol"].abs().max()
        ),
        "minimum_curve_pearson_r": float(summary["curve_pearson_r"].min()),
        "inputs": inputs,
        "raw_archive": archive_record,
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "production_md_permission": "blocked_relaxed_scan_and_parameter_refit",
        "performance_claim_status": "no_performance_claim",
    }
    _atomic_text(
        output_root / "刚性扫描发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--扫描目录", type=Path, action="append")
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument("--原始归档", type=Path)
    parser.add_argument(
        "--发布ID", default="tpu-reality-md-urethane-rigid-scan-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.扫描目录 or DEFAULT_DIRECTORIES,
        args.输出目录,
        release_id=args.发布ID,
        raw_archive=args.原始归档,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
