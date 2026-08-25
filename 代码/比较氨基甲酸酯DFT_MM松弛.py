"""连接同角度DFT与MM约束松弛结果，保留DFT失败点并判断重拟合证据充分性。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from 汇总RESP敏感性 import sha256


ROOT = Path(__file__).resolve().parents[1]


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def compare_relaxed_surfaces(dft: pd.DataFrame, mm: pd.DataFrame) -> pd.DataFrame:
    dft_required = {
        "fragment_name",
        "validation_family",
        "requested_angle_degrees",
        "point_status",
        "relaxed_dft_relative_energy_kcal_mol",
    }
    mm_required = {
        "fragment_name",
        "validation_family",
        "requested_angle_degrees",
        "point_status",
        "relaxed_gaff2_relative_energy_kcal_mol",
        "angle_drift_degrees",
    }
    missing_dft = sorted(dft_required.difference(dft.columns))
    missing_mm = sorted(mm_required.difference(mm.columns))
    if missing_dft or missing_mm:
        raise ValueError(f"松弛比较缺字段: DFT={missing_dft}, MM={missing_mm}")
    mm_subset = mm[
        [
            "fragment_name",
            "validation_family",
            "requested_angle_degrees",
            "point_status",
            "relaxed_gaff2_relative_energy_kcal_mol",
            "angle_drift_degrees",
        ]
    ].rename(
        columns={
            "point_status": "mm_point_status",
            "angle_drift_degrees": "mm_angle_drift_degrees",
        }
    )
    joined = dft.merge(
        mm_subset,
        on=["fragment_name", "validation_family", "requested_angle_degrees"],
        how="left",
        validate="one_to_one",
    )
    if joined["mm_point_status"].isna().any():
        raise ValueError("DFT计划点未完全连接MM松弛")
    joined = joined.rename(columns={"point_status": "dft_point_status"})
    comparable = joined["dft_point_status"].eq("completed") & joined[
        "mm_point_status"
    ].eq("completed")
    joined["comparison_status"] = "blocked_dft_or_mm_not_completed"
    joined.loc[comparable, "comparison_status"] = "comparable_relaxed_point"
    joined.loc[comparable, "gaff2_minus_dft_relaxed_energy_kcal_mol"] = (
        joined.loc[comparable, "relaxed_gaff2_relative_energy_kcal_mol"]
        - joined.loc[comparable, "relaxed_dft_relative_energy_kcal_mol"]
    )
    return joined.sort_values(
        ["validation_family", "requested_angle_degrees"], kind="stable"
    )


def write_release(
    dft_path: Path,
    mm_path: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    for path in (dft_path, mm_path):
        if not path.is_file():
            raise ValueError(f"松弛比较输入不存在: {path}")
    comparison = compare_relaxed_surfaces(
        pd.read_csv(dft_path), pd.read_csv(mm_path)
    )
    summary_rows = []
    for (fragment, family), subset in comparison.groupby(
        ["fragment_name", "validation_family"], sort=True
    ):
        comparable = subset.loc[
            subset["comparison_status"].eq("comparable_relaxed_point")
        ]
        nonzero = comparable.loc[
            comparable["requested_angle_degrees"].astype(float).ne(0.0)
        ]
        summary_rows.append(
            {
                "fragment_name": fragment,
                "validation_family": family,
                "planned_points": len(subset),
                "comparable_points": len(comparable),
                "blocked_points": len(subset) - len(comparable),
                "maximum_absolute_relaxed_energy_error_kcal_mol": (
                    float(
                        comparable[
                            "gaff2_minus_dft_relaxed_energy_kcal_mol"
                        ].abs().max()
                    )
                    if not comparable.empty
                    else pd.NA
                ),
                "minimum_nonzero_gaff2_minus_dft_kcal_mol": (
                    float(
                        nonzero[
                            "gaff2_minus_dft_relaxed_energy_kcal_mol"
                        ].min()
                    )
                    if not nonzero.empty
                    else pd.NA
                ),
                "maximum_nonzero_gaff2_minus_dft_kcal_mol": (
                    float(
                        nonzero[
                            "gaff2_minus_dft_relaxed_energy_kcal_mol"
                        ].max()
                    )
                    if not nonzero.empty
                    else pd.NA
                ),
                "relaxed_refit_readiness": (
                    "blocked_insufficient_converged_angles"
                    if len(comparable) < 4
                    else "candidate_for_relaxed_refit"
                ),
                "production_md_permission": "blocked_parameter_refit_and_external_validation",
            }
        )
    summary = pd.DataFrame(summary_rows).sort_values(
        "validation_family", kind="stable"
    )
    all_points_comparable = bool(summary["blocked_points"].astype(int).eq(0).all())
    output_root.mkdir(parents=True, exist_ok=True)
    comparison_out = output_root / "DFT_MM同角度松弛对比.csv"
    summary_out = output_root / "DFT_MM松弛家族汇总.csv"
    report_out = output_root / "DFT_MM松弛比较说明.md"
    _atomic_text(
        comparison_out, comparison.to_csv(index=False, float_format="%.12g")
    )
    _atomic_text(summary_out, summary.to_csv(index=False, float_format="%.12g"))
    _atomic_text(
        report_out,
        "\n".join(
            [
                "# 氨基甲酸酯DFT–MM同角度松弛比较",
                "",
                "比较只使用DFT和MM均完成的同一目标角。未收敛角度保持阻断，不用刚性能量或插值补齐。",
                "DFT采用ωB97M-D3BJ/6-31G(d,p)冻结二面角优化；MM采用GAFF2替代参数、三构象联合RESP和LAMMPS K=5000 kcal mol⁻¹ rad⁻²约束最小化，解除约束后读取能量。",
                "",
                (
                    "当前8/8点均可比较，每家族4个信息互补角已达到候选重拟合的最小点数门；"
                    "这只允许进入扭转参数拟合与外部片段验证，不直接放行生产MD。"
                    if all_points_comparable
                    else "当前仍有DFT或MM未完成点；只允许判断已完成角度的参数偏差方向，不生成最终扭转系数。"
                ),
                "",
            ]
        ),
    )
    files = [comparison_out, summary_out, report_out]
    comparable = comparison.loc[
        comparison["comparison_status"].eq("comparable_relaxed_point")
    ]
    manifest = {
        "release_id": release_id,
        "status": (
            "relaxed_dft_mm_comparison_completed_refit_candidate"
            if all_points_comparable
            else "relaxed_dft_mm_comparison_completed_refit_blocked_by_failed_dft_points"
        ),
        "counts": {
            "planned_points": len(comparison),
            "comparable_points": len(comparable),
            "blocked_points": len(comparison) - len(comparable),
            "fragments": summary["fragment_name"].nunique(),
        },
        "maximum_absolute_relaxed_energy_error_kcal_mol": float(
            comparable["gaff2_minus_dft_relaxed_energy_kcal_mol"].abs().max()
        ),
        "all_nonzero_comparable_points_gaff2_above_dft": bool(
            comparable.loc[
                comparable["requested_angle_degrees"].astype(float).ne(0.0),
                "gaff2_minus_dft_relaxed_energy_kcal_mol",
            ].gt(0).all()
        ),
        "inputs": {
            path.name: {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in (dft_path, mm_path)
        },
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "production_md_permission": "blocked_parameter_refit_and_external_validation",
        "performance_claim_status": "no_performance_claim",
    }
    _atomic_text(
        output_root / "DFT_MM松弛比较发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--DFT松弛",
        type=Path,
        default=ROOT
        / "计算"
        / "现实MD"
        / "氨基甲酸酯DFT约束松弛"
        / "松弛与刚性对比.csv",
    )
    parser.add_argument(
        "--MM松弛",
        type=Path,
        default=ROOT
        / "计算"
        / "现实MD"
        / "氨基甲酸酯MM约束松弛"
        / "MM松弛与刚性对比.csv",
    )
    parser.add_argument(
        "--输出目录",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "氨基甲酸酯松弛比较",
    )
    parser.add_argument(
        "--发布ID", default="tpu-reality-md-urethane-relaxed-dft-mm-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.DFT松弛,
        args.MM松弛,
        args.输出目录,
        release_id=args.发布ID,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
