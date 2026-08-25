"""在同角度DFT/MM松弛残差上拟合低阶家族特异扭转修正候选。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from 汇总RESP敏感性 import sha256


ROOT = Path(__file__).resolve().parents[1]
KEYS = ["fragment_name", "validation_family", "requested_angle_degrees"]


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def zero_at_planar_design(angles_degrees: np.ndarray, order: int) -> np.ndarray:
    """cos(nφ)-1基函数保证修正势在0°为0，避免任意截距。"""

    if order < 1:
        raise ValueError("松弛扭转修正阶数必须为正")
    radians = np.deg2rad(np.asarray(angles_degrees, dtype=float))
    return np.column_stack(
        [
            np.cos(periodicity * radians) - 1.0
            for periodicity in range(1, order + 1)
        ]
    )


def fit_zero_at_planar(
    angles_degrees: np.ndarray, residual: np.ndarray, order: int
) -> np.ndarray:
    matrix = zero_at_planar_design(angles_degrees, order)
    return np.linalg.lstsq(matrix, np.asarray(residual, dtype=float), rcond=None)[0]


def leave_one_out_rmse(
    angles_degrees: np.ndarray, residual: np.ndarray, order: int
) -> float:
    angles = np.asarray(angles_degrees, dtype=float)
    values = np.asarray(residual, dtype=float)
    if len(values) <= order:
        raise ValueError("松弛扭转留一验证点数不足")
    predictions = np.empty(len(values), dtype=float)
    for index in range(len(values)):
        mask = np.ones(len(values), dtype=bool)
        mask[index] = False
        coefficients = fit_zero_at_planar(angles[mask], values[mask], order)
        predictions[index] = float(
            (
                zero_at_planar_design(angles[[index]], order) @ coefficients
            ).item()
        )
    return float(np.sqrt(np.mean(np.square(predictions - values))))


def validate_relaxed_comparison(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        *KEYS,
        "comparison_status",
        "relaxed_dft_relative_energy_kcal_mol",
        "relaxed_gaff2_relative_energy_kcal_mol",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"松弛扭转拟合输入缺字段: {missing}")
    if frame.duplicated(KEYS).any():
        raise ValueError("松弛扭转拟合点键重复")
    if not frame["comparison_status"].eq("comparable_relaxed_point").all():
        raise ValueError("松弛扭转拟合要求全部计划点均可比较")
    counts = frame.groupby("validation_family").size()
    if len(counts) != 2 or not counts.eq(4).all():
        raise ValueError("松弛扭转拟合要求两个家族各4个信息互补点")
    for family, subset in frame.groupby("validation_family"):
        if not subset["requested_angle_degrees"].astype(float).eq(0.0).any():
            raise ValueError(f"松弛扭转拟合家族缺少0度锚点: {family}")
    numeric = frame[
        [
            "requested_angle_degrees",
            "relaxed_dft_relative_energy_kcal_mol",
            "relaxed_gaff2_relative_energy_kcal_mol",
        ]
    ].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(numeric.to_numpy(float)).all():
        raise ValueError("松弛扭转拟合输入存在非有限数值")
    return frame.copy()


def fit_family(
    subset: pd.DataFrame, *, maximum_order: int = 2
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if maximum_order not in {1, 2}:
        raise ValueError("四点松弛拟合只允许一阶或二阶")
    data = subset.sort_values("requested_angle_degrees", kind="stable").copy()
    angles = data["requested_angle_degrees"].to_numpy(float)
    residual = (
        data["relaxed_dft_relative_energy_kcal_mol"].to_numpy(float)
        - data["relaxed_gaff2_relative_energy_kcal_mol"].to_numpy(float)
    )
    cv_rows = []
    fitted_by_order: dict[int, np.ndarray] = {}
    for order in range(1, maximum_order + 1):
        coefficients = fit_zero_at_planar(angles, residual, order)
        fitted = zero_at_planar_design(angles, order) @ coefficients
        fitted_by_order[order] = coefficients
        cv_rows.append(
            {
                "fourier_order": order,
                "training_rmse_kcal_mol": float(
                    np.sqrt(np.mean(np.square(fitted - residual)))
                ),
                "leave_one_angle_out_rmse_kcal_mol": leave_one_out_rmse(
                    angles, residual, order
                ),
            }
        )
    cv = pd.DataFrame(cv_rows)
    selected_order = int(
        cv.sort_values(
            ["leave_one_angle_out_rmse_kcal_mol", "fourier_order"],
            kind="stable",
        ).iloc[0]["fourier_order"]
    )
    coefficients = fitted_by_order[selected_order]
    correction = zero_at_planar_design(angles, selected_order) @ coefficients
    data["residual_dft_minus_gaff2_kcal_mol"] = residual
    data["torsion_correction_candidate_kcal_mol"] = correction
    unshifted = (
        data["relaxed_gaff2_relative_energy_kcal_mol"].to_numpy(float)
        + correction
    )
    data["corrected_gaff2_relaxed_relative_energy_kcal_mol"] = (
        unshifted - unshifted.min()
    )
    error = (
        data["corrected_gaff2_relaxed_relative_energy_kcal_mol"].to_numpy(float)
        - data["relaxed_dft_relative_energy_kcal_mol"].to_numpy(float)
    )
    coefficient_rows = []
    for periodicity, value in enumerate(coefficients, start=1):
        coefficient_rows.append(
            {
                "periodicity": periodicity,
                "coefficient_for_cos_nphi_minus_one_kcal_mol": float(value),
                "amber_candidate_magnitude_kcal_mol": abs(float(value)),
                "amber_candidate_phase_degrees": 0.0 if value >= 0 else 180.0,
                "fourier_order": selected_order,
            }
        )
    metrics = {
        "fourier_order": selected_order,
        "training_rmse_kcal_mol": float(np.sqrt(np.mean(np.square(error)))),
        "maximum_absolute_training_error_kcal_mol": float(np.max(np.abs(error))),
        "leave_one_angle_out_rmse_kcal_mol": float(
            cv.loc[
                cv["fourier_order"].eq(selected_order),
                "leave_one_angle_out_rmse_kcal_mol",
            ].iloc[0]
        ),
    }
    return pd.DataFrame(coefficient_rows), data, cv, metrics


def write_release(
    comparison_path: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    if not comparison_path.is_file():
        raise ValueError(f"松弛DFT/MM比较不存在: {comparison_path}")
    comparison = validate_relaxed_comparison(pd.read_csv(comparison_path))
    coefficients = []
    evaluations = []
    cross_validation = []
    metric_rows = []
    for (fragment, family), subset in comparison.groupby(
        ["fragment_name", "validation_family"], sort=True
    ):
        coef, evaluated, cv, metrics = fit_family(subset)
        for frame in (coef, cv):
            frame.insert(0, "validation_family", family)
            frame.insert(0, "fragment_name", fragment)
        evaluations.append(evaluated)
        coefficients.append(coef)
        cross_validation.append(cv)
        metric_rows.append(
            {"fragment_name": fragment, "validation_family": family, **metrics}
        )
    coefficient_table = pd.concat(coefficients, ignore_index=True)
    evaluation_table = pd.concat(evaluations, ignore_index=True)
    cv_table = pd.concat(cross_validation, ignore_index=True)
    metrics_table = pd.DataFrame(metric_rows)
    output_root.mkdir(parents=True, exist_ok=True)
    coefficient_out = output_root / "松弛扭转修正候选系数.csv"
    evaluation_out = output_root / "松弛训练点修正评估.csv"
    cv_out = output_root / "松弛阶数交叉验证.csv"
    metrics_out = output_root / "松弛扭转修正指标.csv"
    report_out = output_root / "松弛扭转修正说明.md"
    _atomic_text(
        coefficient_out,
        coefficient_table.to_csv(index=False, float_format="%.12g"),
    )
    _atomic_text(
        evaluation_out,
        evaluation_table.to_csv(index=False, float_format="%.12g"),
    )
    _atomic_text(cv_out, cv_table.to_csv(index=False, float_format="%.12g"))
    _atomic_text(
        metrics_out, metrics_table.to_csv(index=False, float_format="%.12g")
    )
    _atomic_text(
        report_out,
        "\n".join(
            [
                "# 氨基甲酸酯松弛扭转修正候选",
                "",
                "每个脂肪/芳香家族只使用4个同角度DFT/MM松弛点，修正基函数为`cos(nφ)-1`，因此0°修正严格为0。",
                "四点数据最多允许二阶，通过逐角留一RMSE在一阶和二阶之间选择；不拟合截距，不使用六阶刚性曲线模型。",
                "",
                "系数只是外部验证候选。至少需要在不同取代片段和未参与拟合的角度上重新计算DFT/MM松弛面；训练误差再小也不能直接写入生产力场。",
                "",
            ]
        ),
    )
    files = [coefficient_out, evaluation_out, cv_out, metrics_out, report_out]
    manifest = {
        "release_id": release_id,
        "status": "relaxed_low_order_family_fit_completed_external_validation_pending",
        "counts": {
            "families": metrics_table["validation_family"].nunique(),
            "training_points": len(evaluation_table),
            "coefficient_rows": len(coefficient_table),
        },
        "basis": "cos(n*phi)-1; zero correction at phi=0 degrees",
        "maximum_allowed_order": 2,
        "input": {
            "path": str(comparison_path),
            "bytes": comparison_path.stat().st_size,
            "sha256": sha256(comparison_path),
        },
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "production_md_permission": "blocked_external_fragment_and_holdout_angle_validation",
        "performance_claim_status": "no_performance_claim",
    }
    _atomic_text(
        output_root / "松弛扭转修正发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--DFT_MM比较",
        type=Path,
        default=ROOT
        / "计算"
        / "现实MD"
        / "氨基甲酸酯松弛比较"
        / "DFT_MM同角度松弛对比.csv",
    )
    parser.add_argument(
        "--输出目录",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "氨基甲酸酯松弛扭转修正",
    )
    parser.add_argument(
        "--发布ID",
        default="tpu-reality-md-urethane-relaxed-torsion-candidate-20260825-v1",
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.DFT_MM比较, args.输出目录, release_id=args.发布ID
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
