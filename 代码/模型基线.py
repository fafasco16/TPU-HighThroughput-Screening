"""TPU 第一阶段基线的纯函数。

本模块故意只依赖项目已经锁定的 NumPy、Pandas 和 RDKit。它不读写项目
文件；数据选择、结果物化和审计清单由 ``生成模型基线.py`` 负责。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
from rdkit import Chem, DataStructs
from rdkit.Chem import rdFingerprintGenerator


_EPSILON = np.finfo(np.float64).eps


def _as_finite_vector(values: Sequence[float] | np.ndarray, name: str) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1:
        raise ValueError(f"{name} 必须是一维数组")
    if not np.isfinite(array).all():
        raise ValueError(f"{name} 必须全部为有限数")
    return array


def _validated_weights(weights: Sequence[float] | np.ndarray, size: int) -> np.ndarray:
    array = _as_finite_vector(weights, "weights")
    if len(array) != size:
        raise ValueError("weights 与观测数量不一致")
    if (array < 0).any() or not (array > 0).any():
        raise ValueError("weights 必须包含至少一个正权重且不得为负")
    return array


def weighted_mean(
    values: Sequence[float] | np.ndarray,
    weights: Sequence[float] | np.ndarray,
) -> float:
    """返回有限观测的加权均值。"""

    y = _as_finite_vector(values, "values")
    w = _validated_weights(weights, len(y))
    return float(np.dot(y, w) / w.sum())


def fit_weighted_ridge(
    x: np.ndarray,
    y: Sequence[float] | np.ndarray,
    weights: Sequence[float] | np.ndarray,
    alpha: float,
) -> dict[str, Any]:
    """拟合带截距、训练集加权标准化的 Ridge 回归。

    截距不受惩罚。常量特征的尺度固定为 1，确保预测稳定且不产生除零。
    """

    features = np.asarray(x, dtype=np.float64)
    if features.ndim != 2:
        raise ValueError("x 必须是二维特征矩阵")
    targets = _as_finite_vector(y, "y")
    if len(features) != len(targets):
        raise ValueError("x 与 y 的观测数量不一致")
    if not np.isfinite(features).all():
        raise ValueError("x 必须全部为有限数")
    sample_weights = _validated_weights(weights, len(targets))
    if not np.isfinite(alpha) or alpha < 0:
        raise ValueError("alpha 必须是有限非负数")

    total_weight = sample_weights.sum()
    feature_mean = np.sum(features * sample_weights[:, None], axis=0) / total_weight
    centered = features - feature_mean
    variance = np.sum(centered**2 * sample_weights[:, None], axis=0) / total_weight
    feature_scale = np.sqrt(np.maximum(variance, 0.0))
    feature_scale[feature_scale <= _EPSILON] = 1.0
    standardized = centered / feature_scale

    target_mean = weighted_mean(targets, sample_weights)
    centered_target = targets - target_mean
    sqrt_weight = np.sqrt(sample_weights)
    weighted_x = standardized * sqrt_weight[:, None]
    weighted_y = centered_target * sqrt_weight
    gram = weighted_x.T @ weighted_x
    rhs = weighted_x.T @ weighted_y
    regularized = gram + float(alpha) * np.eye(features.shape[1], dtype=np.float64)
    try:
        coefficients = np.linalg.solve(regularized, rhs)
    except np.linalg.LinAlgError:
        coefficients = np.linalg.lstsq(regularized, rhs, rcond=None)[0]

    return {
        "coef": coefficients,
        "feature_mean": feature_mean,
        "feature_scale": feature_scale,
        "target_mean": float(target_mean),
        "alpha": float(alpha),
    }


def predict_weighted_ridge(model: dict[str, Any], x: np.ndarray) -> np.ndarray:
    """使用 :func:`fit_weighted_ridge` 的可审计字典模型预测。"""

    features = np.asarray(x, dtype=np.float64)
    if features.ndim != 2:
        raise ValueError("x 必须是二维特征矩阵")
    mean = np.asarray(model["feature_mean"], dtype=np.float64)
    scale = np.asarray(model["feature_scale"], dtype=np.float64)
    coefficients = np.asarray(model["coef"], dtype=np.float64)
    if features.shape[1] != len(mean) or len(mean) != len(coefficients):
        raise ValueError("预测特征维度与模型不一致")
    if not np.isfinite(features).all():
        raise ValueError("x 必须全部为有限数")
    return float(model["target_mean"]) + ((features - mean) / scale) @ coefficients


def regression_metrics(
    y_true: Sequence[float] | np.ndarray,
    y_pred: Sequence[float] | np.ndarray,
    weights: Sequence[float] | np.ndarray,
) -> dict[str, float]:
    """计算加权误差指标和不加权 Spearman 秩相关。"""

    truth = _as_finite_vector(y_true, "y_true")
    prediction = _as_finite_vector(y_pred, "y_pred")
    if len(truth) != len(prediction):
        raise ValueError("y_true 与 y_pred 的观测数量不一致")
    sample_weights = _validated_weights(weights, len(truth))
    total_weight = sample_weights.sum()
    residual = prediction - truth
    mae = float(np.dot(np.abs(residual), sample_weights) / total_weight)
    rmse = float(np.sqrt(np.dot(residual**2, sample_weights) / total_weight))
    truth_mean = weighted_mean(truth, sample_weights)
    total_sum = float(np.dot((truth - truth_mean) ** 2, sample_weights))
    residual_sum = float(np.dot(residual**2, sample_weights))
    r2 = float("nan") if total_sum <= _EPSILON else float(1.0 - residual_sum / total_sum)

    positive_weight = sample_weights > 0
    rank_truth = truth[positive_weight]
    rank_prediction = prediction[positive_weight]
    truth_rank = pd.Series(rank_truth).rank(method="average").to_numpy(dtype=np.float64)
    prediction_rank = (
        pd.Series(rank_prediction).rank(method="average").to_numpy(dtype=np.float64)
    )
    if np.std(truth_rank) <= _EPSILON or np.std(prediction_rank) <= _EPSILON:
        spearman = float("nan")
    else:
        spearman = float(np.corrcoef(truth_rank, prediction_rank)[0, 1])
    return {"mae": mae, "rmse": rmse, "r2": r2, "spearman": spearman}


def featurize_smiles(smiles: str, radius: int, n_bits: int) -> np.ndarray:
    """把一个有效 SMILES 转换为固定长度 Morgan 位向量。"""

    if not isinstance(smiles, str) or not smiles.strip():
        raise ValueError("SMILES 不能为空")
    if radius < 0 or n_bits <= 0:
        raise ValueError("Morgan radius 必须非负且 n_bits 必须为正")
    molecule = Chem.MolFromSmiles(smiles.strip())
    if molecule is None:
        raise ValueError(f"无法解析 SMILES: {smiles}")
    generator = rdFingerprintGenerator.GetMorganGenerator(
        radius=int(radius), fpSize=int(n_bits)
    )
    fingerprint = generator.GetFingerprint(molecule)
    array = np.zeros((n_bits,), dtype=np.uint8)
    DataStructs.ConvertToNumpyArray(fingerprint, array)
    return array.astype(np.float64, copy=False)


def stable_numeric_feature_group(values: Sequence[float] | np.ndarray) -> str:
    """为完整数值输入生成跨平台稳定的派生硬组。"""

    array = _as_finite_vector(values, "values")
    normalized = "|".join(format(float(value), ".17g") for value in array)
    digest = hashlib.sha256(normalized.encode("ascii")).hexdigest()[:24]
    return f"numeric-feature-{digest}"


def stable_mixed_feature_group(values: dict[str, Any]) -> str:
    """为数值与类别混合的完整特征对象生成稳定硬组。"""

    if not isinstance(values, dict) or not values:
        raise ValueError("values 必须是非空特征字典")
    normalized: list[list[str]] = []
    for key in sorted(values):
        value = values[key]
        if isinstance(value, bool):
            token = "true" if value else "false"
            value_type = "bool"
        elif isinstance(value, (int, float, np.integer, np.floating)):
            numeric = float(value)
            if not np.isfinite(numeric):
                raise ValueError(f"特征 {key} 不是有限数")
            token = format(numeric, ".17g")
            value_type = "number"
        elif isinstance(value, str):
            token = value.strip()
            value_type = "string"
        else:
            raise ValueError(f"特征 {key} 的类型不受支持")
        normalized.append([str(key), value_type, token])
    payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"mixed-feature-{digest}"


def deterministic_split(group: str, seed: int = 20260722) -> str:
    """按组固定哈希为 80/10/10，绝不逐行抽样。"""

    if not isinstance(group, str) or not group:
        raise ValueError("group 不能为空")
    digest = hashlib.sha256(f"{seed}|{group}".encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big") % 10_000
    if bucket < 8_000:
        return "train"
    if bucket < 9_000:
        return "validation"
    return "test"


def validate_group_split(frame: pd.DataFrame, group_column: str, split_column: str) -> None:
    """若一个硬组出现在多个折中立即失败。"""

    missing = {group_column, split_column}.difference(frame.columns)
    if missing:
        raise ValueError(f"缺少分组校验字段: {sorted(missing)}")
    groups = frame[group_column]
    invalid_group = groups.isna() | groups.astype(str).str.strip().eq("")
    if invalid_group.any():
        raise ValueError("硬组为空，不能进行防泄漏划分")
    counts = frame.groupby(group_column, dropna=False)[split_column].nunique(dropna=False)
    leaking = counts[counts > 1]
    if not leaking.empty:
        examples = ", ".join(map(str, leaking.index[:5]))
        raise ValueError(f"硬组跨越多个数据折: {examples}")


def choose_ridge_alpha(
    train_x: np.ndarray,
    train_y: Sequence[float] | np.ndarray,
    train_weights: Sequence[float] | np.ndarray,
    validation_x: np.ndarray,
    validation_y: Sequence[float] | np.ndarray,
    validation_weights: Sequence[float] | np.ndarray,
    alphas: Sequence[float],
) -> dict[str, Any]:
    """只依据验证集加权 RMSE 选择 Ridge 正则强度。"""

    candidates = [float(alpha) for alpha in alphas]
    if not candidates:
        raise ValueError("alphas 不能为空")
    if any(not np.isfinite(alpha) or alpha < 0 for alpha in candidates):
        raise ValueError("alphas 必须全部为有限非负数")

    best: dict[str, Any] | None = None
    for alpha in candidates:
        model = fit_weighted_ridge(train_x, train_y, train_weights, alpha)
        prediction = predict_weighted_ridge(model, validation_x)
        metrics = regression_metrics(validation_y, prediction, validation_weights)
        candidate = {
            "alpha": alpha,
            "validation_rmse": metrics["rmse"],
            "model": model,
        }
        if best is None or (candidate["validation_rmse"], alpha) < (
            best["validation_rmse"],
            best["alpha"],
        ):
            best = candidate
    assert best is not None
    return best


def _single_text_value(points: pd.DataFrame, column: str) -> str:
    values = points[column].dropna().astype(str).str.strip()
    unique = values[values.ne("")].unique()
    return str(unique[0]) if len(unique) == 1 else ""


def _stress_to_mpa(values: np.ndarray, unit: str) -> np.ndarray | None:
    factors = {"mpa": 1.0, "kpa": 1e-3, "pa": 1e-6}
    factor = factors.get(unit.strip().lower())
    return None if factor is None else values * factor


def _strain_to_fraction(values: np.ndarray, unit: str) -> np.ndarray | None:
    normalized = unit.strip().lower()
    if normalized in {"%", "percent", "percentage"}:
        return values / 100.0
    if normalized in {"1", "dimensionless", "fraction", "strain"}:
        return values
    return None


def _interpolate_if_covered(x: np.ndarray, y: np.ndarray, target: float) -> float:
    if len(x) < 2 or target < x.min() or target > x.max():
        return float("nan")
    return float(np.interp(target, x, y))


def derive_curve_endpoints(points: pd.DataFrame) -> dict[str, float | str]:
    """从轴和单位明确的单条应力—应变曲线提取保守端点。

    返回的是“最大观测”和“记录区间”端点，除非来源另有断裂语义，否则不会
    把它们重命名为断裂强度、断裂伸长或完整韧性。
    """

    required = {
        "property_name",
        "unit",
        "condition_name",
        "condition_value",
        "condition_unit",
        "value",
        "point_index",
    }
    if not required.issubset(points.columns) or len(points) < 2:
        return {
            "endpoint_status": "unsupported_axis_or_unit",
            "curve_type": "unsupported",
        }

    property_name = _single_text_value(points, "property_name").lower()
    condition_name = _single_text_value(points, "condition_name").lower()
    stress_unit = _single_text_value(points, "unit")
    strain_unit = _single_text_value(points, "condition_unit")
    if property_name == "cyclic_tensile_stress" and condition_name == "tensile_strain":
        return {
            "endpoint_status": "cycle_segmentation_required",
            "curve_type": "cyclic_tensile",
        }
    if property_name == "tensile_stress" and condition_name == "tensile_strain":
        curve_type = "tensile"
    elif property_name == "compressive_stress" and "strain" in condition_name:
        curve_type = "compression"
    else:
        return {
            "endpoint_status": "unsupported_axis_or_unit",
            "curve_type": "unsupported",
        }

    numeric = points[["point_index", "condition_value", "value"]].apply(
        pd.to_numeric, errors="coerce"
    )
    finite = (
        np.isfinite(numeric["point_index"])
        & np.isfinite(numeric["condition_value"])
        & np.isfinite(numeric["value"])
    )
    numeric = numeric.loc[finite].sort_values("point_index")
    if len(numeric) < 2:
        return {
            "endpoint_status": "unsupported_axis_or_unit",
            "curve_type": "unsupported",
        }
    if numeric["point_index"].duplicated().any():
        return {
            "endpoint_status": "duplicate_point_index",
            "curve_type": curve_type,
        }
    ordered_strain = numeric["condition_value"].to_numpy(dtype=np.float64)
    if np.any(np.diff(ordered_strain) < -1e-12):
        return {
            "endpoint_status": "nonmonotonic_axis_requires_specialized_processing",
            "curve_type": curve_type,
        }
    numeric = numeric.groupby("condition_value", as_index=False)["value"].mean()
    original_strain = numeric["condition_value"].to_numpy(dtype=np.float64)
    stress = _stress_to_mpa(numeric["value"].to_numpy(dtype=np.float64), stress_unit)
    strain = _strain_to_fraction(original_strain, strain_unit)
    if stress is None or strain is None or len(np.unique(strain)) < 2:
        return {
            "endpoint_status": "unsupported_axis_or_unit",
            "curve_type": "unsupported",
        }
    if curve_type == "compression" and (
        np.max(stress) <= 0 or stress[-1] <= stress[0]
    ):
        return {
            "endpoint_status": "unsupported_compression_sign_convention",
            "curve_type": "compression",
        }

    energy = float(np.trapezoid(stress, strain))
    result: dict[str, float | str] = {
        "endpoint_status": "physical_endpoints_ready",
        "curve_type": curve_type,
        "max_observed_stress_mpa": float(np.max(stress)),
        "max_observed_strain": float(np.max(original_strain)),
        "max_observed_strain_unit": strain_unit,
        "recorded_energy_density_mj_m3": energy,
    }
    if curve_type == "tensile":
        for percent in (50, 100, 300):
            target_stress = _interpolate_if_covered(strain, stress, percent / 100.0)
            if np.isfinite(target_stress):
                result[f"stress_at_{percent}pct_mpa"] = target_stress

        low_mask = (strain >= 0.0) & (strain <= 0.02)
        low_x = strain[low_mask]
        low_y = stress[low_mask]
        if len(low_x) < 5 or len(np.unique(low_x)) < 5:
            result["low_strain_modulus_status"] = "insufficient_low_strain_points"
        else:
            slope, intercept = np.polyfit(low_x, low_y, deg=1)
            fitted = intercept + slope * low_x
            denominator = float(np.sum((low_y - np.mean(low_y)) ** 2))
            r2 = (
                float("nan")
                if denominator <= _EPSILON
                else float(1.0 - np.sum((low_y - fitted) ** 2) / denominator)
            )
            result["low_strain_linear_r2"] = r2
            covers_near_zero = float(np.min(low_x)) <= 0.002
            sufficient_span = float(np.max(low_x) - np.min(low_x)) >= 0.015
            stress_scale = max(float(np.max(np.abs(low_y))), _EPSILON)
            small_intercept = abs(float(intercept)) <= 0.02 * stress_scale
            if not (covers_near_zero and sufficient_span and small_intercept):
                result["low_strain_modulus_status"] = (
                    "zero_span_or_intercept_gate_failed"
                )
            elif slope > 0 and np.isfinite(r2) and r2 >= 0.98:
                result["low_strain_modulus_status"] = "linear_fit_passed"
                result["low_strain_linear_modulus_mpa"] = float(slope)
            else:
                result["low_strain_modulus_status"] = "linear_fit_failed"
    else:
        stress_10 = _interpolate_if_covered(strain, stress, 0.10)
        stress_25 = _interpolate_if_covered(strain, stress, 0.25)
        result["stress_at_10pct_mpa"] = stress_10
        result["stress_at_25pct_mpa"] = stress_25
        result["secant_modulus_10pct_mpa"] = (
            float(stress_10 / 0.10) if np.isfinite(stress_10) else float("nan")
        )
        if np.min(strain) <= 0.25 <= np.max(strain) and np.isfinite(stress_25):
            within_25 = strain <= 0.25
            clipped_x = np.append(strain[within_25], 0.25)
            clipped_y = np.append(stress[within_25], stress_25)
            unique_x, unique_indices = np.unique(clipped_x, return_index=True)
            result["energy_density_to_25pct_mj_m3"] = float(
                np.trapezoid(clipped_y[unique_indices], unique_x)
            )
    return result
