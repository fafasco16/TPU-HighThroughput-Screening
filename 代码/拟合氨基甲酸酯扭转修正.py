"""在刚性同几何DFT–GAFF2残差上筛查周期傅里叶扭转修正及家族分型需求。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from 汇总RESP敏感性 import sha256


ROOT = Path(__file__).resolve().parents[1]


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def symmetrize_residual(curve: pd.DataFrame) -> pd.DataFrame:
    required = {
        "fragment_name",
        "validation_family",
        "requested_angle_degrees",
        "dft_relative_energy_kcal_mol",
        "gaff2_relative_energy_kcal_mol",
    }
    missing = sorted(required.difference(curve.columns))
    if missing:
        raise ValueError(f"刚性曲线缺字段: {missing}")
    frame = curve.copy()
    frame["absolute_angle_degrees"] = (
        frame["requested_angle_degrees"].astype(float).abs()
    )
    frame["residual_dft_minus_gaff2_kcal_mol"] = (
        frame["dft_relative_energy_kcal_mol"]
        - frame["gaff2_relative_energy_kcal_mol"]
    )
    result = (
        frame.groupby(
            [
                "fragment_name",
                "validation_family",
                "absolute_angle_degrees",
            ],
            sort=True,
        )
        .agg(
            signed_point_count=("requested_angle_degrees", "count"),
            symmetrized_residual_kcal_mol=(
                "residual_dft_minus_gaff2_kcal_mol",
                "mean",
            ),
            plus_minus_half_range_kcal_mol=(
                "residual_dft_minus_gaff2_kcal_mol",
                lambda values: (max(values) - min(values)) / 2,
            ),
        )
        .reset_index()
    )
    expected_angles = set(range(0, 181, 15))
    for family, subset in result.groupby("validation_family"):
        if set(subset["absolute_angle_degrees"].astype(int)) != expected_angles:
            raise ValueError(f"对称化角度覆盖不闭合: {family}")
    return result


def design_matrix(angles_degrees: np.ndarray, order: int) -> np.ndarray:
    if order < 1:
        raise ValueError("傅里叶阶数必须为正")
    radians = np.deg2rad(np.asarray(angles_degrees, dtype=float))
    return np.column_stack(
        [np.ones(len(radians))]
        + [np.cos(periodicity * radians) for periodicity in range(1, order + 1)]
    )


def fit_coefficients(
    angles_degrees: np.ndarray, target: np.ndarray, order: int
) -> np.ndarray:
    matrix = design_matrix(angles_degrees, order)
    return np.linalg.lstsq(matrix, np.asarray(target, dtype=float), rcond=None)[0]


def leave_angle_out_rmse(
    angles_degrees: np.ndarray, target: np.ndarray, order: int
) -> float:
    angles = np.asarray(angles_degrees, dtype=float)
    values = np.asarray(target, dtype=float)
    predictions = np.empty_like(values)
    for index in range(len(values)):
        mask = np.ones(len(values), dtype=bool)
        mask[index] = False
        coefficients = fit_coefficients(angles[mask], values[mask], order)
        predictions[index] = float(
            (design_matrix(angles[[index]], order) @ coefficients).item()
        )
    return float(np.sqrt(np.mean(np.square(predictions - values))))


def select_order(data: pd.DataFrame, maximum_order: int = 6) -> tuple[int, pd.DataFrame]:
    rows = []
    for order in range(1, maximum_order + 1):
        coefficients = fit_coefficients(
            data["absolute_angle_degrees"].to_numpy(),
            data["symmetrized_residual_kcal_mol"].to_numpy(),
            order,
        )
        prediction = design_matrix(
            data["absolute_angle_degrees"].to_numpy(), order
        ) @ coefficients
        rows.append(
            {
                "fourier_order": order,
                "training_rmse_kcal_mol": float(
                    np.sqrt(
                        np.mean(
                            np.square(
                                prediction
                                - data["symmetrized_residual_kcal_mol"].to_numpy()
                            )
                        )
                    )
                ),
                "leave_angle_out_rmse_kcal_mol": leave_angle_out_rmse(
                    data["absolute_angle_degrees"].to_numpy(),
                    data["symmetrized_residual_kcal_mol"].to_numpy(),
                    order,
                ),
            }
        )
    table = pd.DataFrame(rows)
    selected = int(
        table.sort_values(
            ["leave_angle_out_rmse_kcal_mol", "fourier_order"], kind="stable"
        ).iloc[0]["fourier_order"]
    )
    return selected, table


def _fit_model(
    model_name: str,
    data: pd.DataFrame,
    full_curve: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    order, order_table = select_order(data)
    coefficients = fit_coefficients(
        data["absolute_angle_degrees"].to_numpy(),
        data["symmetrized_residual_kcal_mol"].to_numpy(),
        order,
    )
    coefficient_rows = [
        {
            "model_name": model_name,
            "term": "intercept" if index == 0 else f"cos_{index}",
            "periodicity": index,
            "coefficient_kcal_mol": float(value),
            "fourier_order": order,
        }
        for index, value in enumerate(coefficients)
    ]
    evaluated = full_curve.copy()
    correction = design_matrix(
        evaluated["requested_angle_degrees"].to_numpy(), order
    ) @ coefficients
    evaluated["model_name"] = model_name
    evaluated["torsion_correction_kcal_mol"] = correction
    evaluated["corrected_gaff2_energy_unshifted_kcal_mol"] = (
        evaluated["gaff2_relative_energy_kcal_mol"] + correction
    )
    evaluated["corrected_gaff2_relative_energy_kcal_mol"] = (
        evaluated["corrected_gaff2_energy_unshifted_kcal_mol"]
        - evaluated["corrected_gaff2_energy_unshifted_kcal_mol"].min()
    )
    error = (
        evaluated["corrected_gaff2_relative_energy_kcal_mol"]
        - evaluated["dft_relative_energy_kcal_mol"]
    )
    metrics = {
        "model_name": model_name,
        "fourier_order": order,
        "symmetrized_training_rmse_kcal_mol": float(
            order_table.loc[
                order_table["fourier_order"].eq(order),
                "training_rmse_kcal_mol",
            ].iloc[0]
        ),
        "symmetrized_leave_angle_out_rmse_kcal_mol": float(
            order_table.loc[
                order_table["fourier_order"].eq(order),
                "leave_angle_out_rmse_kcal_mol",
            ].iloc[0]
        ),
        "corrected_full_curve_rmse_kcal_mol": float(
            np.sqrt(np.mean(np.square(error)))
        ),
        "corrected_full_curve_pearson_r": float(
            evaluated["corrected_gaff2_relative_energy_kcal_mol"].corr(
                evaluated["dft_relative_energy_kcal_mol"]
            )
        ),
        "corrected_barrier_difference_kcal_mol": float(
            evaluated["corrected_gaff2_relative_energy_kcal_mol"].max()
            - evaluated["dft_relative_energy_kcal_mol"].max()
        ),
        "maximum_plus_minus_asymmetry_half_range_kcal_mol": float(
            data["plus_minus_half_range_kcal_mol"].max()
        ),
    }
    order_table.insert(0, "model_name", model_name)
    return pd.DataFrame(coefficient_rows), evaluated, {**metrics, "order_table": order_table}


def write_release(
    curve_path: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    if not curve_path.is_file():
        raise ValueError(f"刚性扫描曲线不存在: {curve_path}")
    curve = pd.read_csv(curve_path)
    sym = symmetrize_residual(curve)
    coefficient_frames = []
    evaluation_frames = []
    order_frames = []
    metric_rows = []
    for family, family_sym in sym.groupby("validation_family", sort=True):
        family_curve = curve.loc[curve["validation_family"].eq(family)].copy()
        coefficients, evaluated, metrics = _fit_model(
            f"family_specific::{family}", family_sym, family_curve
        )
        coefficient_frames.append(coefficients)
        evaluation_frames.append(evaluated)
        order_frames.append(metrics.pop("order_table"))
        metric_rows.append({"fit_scope": "family_specific", "validation_family": family, **metrics})
    pooled_coefficients, _, pooled_metrics = _fit_model(
        "pooled_common", sym, curve
    )
    coefficient_frames.append(pooled_coefficients)
    order_frames.append(pooled_metrics.pop("order_table"))
    pooled_order = int(pooled_metrics["fourier_order"])
    pooled_values = pooled_coefficients.sort_values("periodicity")[
        "coefficient_kcal_mol"
    ].to_numpy()
    for family, family_curve in curve.groupby("validation_family", sort=True):
        evaluated = family_curve.copy()
        correction = design_matrix(
            evaluated["requested_angle_degrees"].to_numpy(), pooled_order
        ) @ pooled_values
        evaluated["model_name"] = "pooled_common"
        evaluated["torsion_correction_kcal_mol"] = correction
        unshifted = evaluated["gaff2_relative_energy_kcal_mol"] + correction
        evaluated["corrected_gaff2_energy_unshifted_kcal_mol"] = unshifted
        evaluated["corrected_gaff2_relative_energy_kcal_mol"] = (
            unshifted - unshifted.min()
        )
        evaluation_frames.append(evaluated)
        error = (
            evaluated["corrected_gaff2_relative_energy_kcal_mol"]
            - evaluated["dft_relative_energy_kcal_mol"]
        )
        metric_rows.append(
            {
                "fit_scope": "pooled_common",
                "validation_family": family,
                "model_name": "pooled_common",
                "fourier_order": pooled_order,
                "symmetrized_training_rmse_kcal_mol": pooled_metrics[
                    "symmetrized_training_rmse_kcal_mol"
                ],
                "symmetrized_leave_angle_out_rmse_kcal_mol": pooled_metrics[
                    "symmetrized_leave_angle_out_rmse_kcal_mol"
                ],
                "corrected_full_curve_rmse_kcal_mol": float(
                    np.sqrt(np.mean(np.square(error)))
                ),
                "corrected_full_curve_pearson_r": float(
                    evaluated["corrected_gaff2_relative_energy_kcal_mol"].corr(
                        evaluated["dft_relative_energy_kcal_mol"]
                    )
                ),
                "corrected_barrier_difference_kcal_mol": float(
                    evaluated["corrected_gaff2_relative_energy_kcal_mol"].max()
                    - evaluated["dft_relative_energy_kcal_mol"].max()
                ),
                "maximum_plus_minus_asymmetry_half_range_kcal_mol": float(
                    sym.loc[
                        sym["validation_family"].eq(family),
                        "plus_minus_half_range_kcal_mol",
                    ].max()
                ),
            }
        )
    coefficients = pd.concat(coefficient_frames, ignore_index=True)
    evaluations = pd.concat(evaluation_frames, ignore_index=True).sort_values(
        ["model_name", "validation_family", "requested_angle_degrees"],
        kind="stable",
    )
    orders = pd.concat(order_frames, ignore_index=True)
    metrics = pd.DataFrame(metric_rows).sort_values(
        ["fit_scope", "validation_family"], kind="stable"
    )
    specific = metrics.loc[metrics["fit_scope"].eq("family_specific")]
    pooled = metrics.loc[metrics["fit_scope"].eq("pooled_common")]
    family_specific_required = bool(
        pooled["corrected_full_curve_rmse_kcal_mol"].max()
        > specific["corrected_full_curve_rmse_kcal_mol"].max() + 1.0
    )
    output_root.mkdir(parents=True, exist_ok=True)
    coefficient_out = output_root / "傅里叶修正系数.csv"
    evaluation_out = output_root / "刚性曲线修正评估.csv"
    order_out = output_root / "阶数交叉验证.csv"
    metric_out = output_root / "扭转修正指标.csv"
    report_out = output_root / "扭转修正说明.md"
    _atomic_text(
        coefficient_out, coefficients.to_csv(index=False, float_format="%.12g")
    )
    _atomic_text(
        evaluation_out, evaluations.to_csv(index=False, float_format="%.12g")
    )
    _atomic_text(order_out, orders.to_csv(index=False, float_format="%.12g"))
    _atomic_text(metric_out, metrics.to_csv(index=False, float_format="%.12g"))
    _atomic_text(
        report_out,
        "\n".join(
            [
                "# 氨基甲酸酯扭转修正筛查",
                "",
                "拟合目标是同一刚性几何上`DFT相对能−GAFF2相对能`的±角度对称平均，只使用cos(nφ)周期项，避免把单一构象的正负角非对称非键相互作用错误吸收到可转移扭转参数。",
                "傅里叶阶数1–6通过逐绝对角留一交叉验证选择；同时比较脂肪/芳香家族分别拟合和两家族共用一个修正。",
                "",
                "这些系数只是在刚性曲线上的参数筛查，不直接写入GAFF2。冻结二面角松弛面、MM松弛曲线和独立片段验证完成前，`production_md_permission`保持阻断。",
                "",
            ]
        ),
    )
    files = [coefficient_out, evaluation_out, order_out, metric_out, report_out]
    manifest = {
        "release_id": release_id,
        "status": "rigid_curve_fourier_screen_completed_relaxed_fit_pending",
        "counts": {
            "symmetrized_points": len(sym),
            "coefficient_rows": len(coefficients),
            "evaluation_rows": len(evaluations),
            "models": coefficients["model_name"].nunique(),
        },
        "family_specific_typing_required_by_rigid_screen": family_specific_required,
        "best_family_specific_max_rmse_kcal_mol": float(
            specific["corrected_full_curve_rmse_kcal_mol"].max()
        ),
        "pooled_common_max_rmse_kcal_mol": float(
            pooled["corrected_full_curve_rmse_kcal_mol"].max()
        ),
        "input": {
            "path": str(curve_path),
            "bytes": curve_path.stat().st_size,
            "sha256": sha256(curve_path),
        },
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "production_md_permission": "blocked_relaxed_scan_and_external_validation",
        "performance_claim_status": "no_performance_claim",
    }
    _atomic_text(
        output_root / "扭转修正发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--刚性曲线",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "氨基甲酸酯刚性扫描" / "刚性扫描曲线.csv",
    )
    parser.add_argument(
        "--输出目录",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "氨基甲酸酯扭转修正",
    )
    parser.add_argument(
        "--发布ID", default="tpu-reality-md-urethane-torsion-correction-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.刚性曲线, args.输出目录, release_id=args.发布ID
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
