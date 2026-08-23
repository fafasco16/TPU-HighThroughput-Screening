"""TPU 力学曲线的可审计验证、积分与派生指标。"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np


ALGORITHM_VERSION = "tpu-curves/0.1.0"
ENERGY_DENSITY_UNIT = "MJ/m^3"
_ZERO_TOLERANCE = 1e-12


def _as_vector(values: Any, *, name: str) -> np.ndarray:
    try:
        vector = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} values must be numeric") from exc
    if vector.ndim != 1:
        raise ValueError(f"{name} values must be one-dimensional")
    return vector


def _validate_curve(
    strain: Sequence[float] | np.ndarray,
    stress: Sequence[float] | np.ndarray,
    *,
    label: str,
) -> tuple[np.ndarray, np.ndarray]:
    strain_vector = _as_vector(strain, name=f"{label} strain")
    stress_vector = _as_vector(stress, name=f"{label} stress")
    if strain_vector.size != stress_vector.size:
        raise ValueError(f"{label} strain and stress must have the same length")
    if strain_vector.size < 2:
        raise ValueError(f"{label} curve requires at least 2 points")
    if not np.isfinite(strain_vector).all() or not np.isfinite(stress_vector).all():
        raise ValueError(f"{label} curve values must all be finite")
    return strain_vector, stress_vector


def _integrate_validated(x: np.ndarray, y: np.ndarray) -> float:
    # ``math.fsum`` fixes summation order and avoids platform-dependent vector
    # reductions. Points are never sorted, smoothed, removed, or otherwise changed.
    terms = (
        float((y[index] + y[index + 1]) * 0.5 * (x[index + 1] - x[index]))
        for index in range(x.size - 1)
    )
    return float(math.fsum(terms))


def trapezoid_integral(
    x: Sequence[float] | np.ndarray,
    y: Sequence[float] | np.ndarray,
) -> float:
    """Return the signed trapezoid integral in the supplied point order."""

    x_vector, y_vector = _validate_curve(x, y, label="integration")
    return _integrate_validated(x_vector, y_vector)


def _origin_warning(strain: np.ndarray, stress: np.ndarray, warning: str) -> list[str]:
    if not (
        math.isclose(float(strain[0]), 0.0, abs_tol=_ZERO_TOLERANCE)
        and math.isclose(float(stress[0]), 0.0, abs_tol=_ZERO_TOLERANCE)
    ):
        return [warning]
    return []


def tensile_metrics(
    strain: Sequence[float] | np.ndarray,
    stress_mpa: Sequence[float] | np.ndarray,
) -> dict[str, Any]:
    """Derive metrics from one monotonic tensile loading branch.

    ``strain`` must already be dimensionless and ``stress_mpa`` must already be
    in MPa. Since ``1 MPa = 1 MJ/m^3``, integrating MPa over dimensionless
    strain directly yields MJ/m^3.
    """

    strain_vector, stress_vector = _validate_curve(
        strain, stress_mpa, label="tensile"
    )
    differences = np.diff(strain_vector)
    if np.any(differences < 0.0):
        raise ValueError("tensile strain must be non-decreasing in source point order")

    warnings: list[str] = []
    if np.any(differences == 0.0):
        warnings.append("duplicate_strain_values")
    warnings.extend(
        _origin_warning(strain_vector, stress_vector, "curve_does_not_start_at_origin")
    )
    if np.any(strain_vector < 0.0):
        warnings.append("negative_strain_values")
    if np.any(stress_vector < 0.0):
        warnings.append("negative_stress_values")

    return {
        "toughness_mj_m3": _integrate_validated(strain_vector, stress_vector),
        "tensile_strength_mpa": float(np.max(stress_vector)),
        "elongation_at_break": float(strain_vector[-1]),
        "units": {
            "toughness_mj_m3": ENERGY_DENSITY_UNIT,
            "tensile_strength_mpa": "MPa",
            "elongation_at_break": "1",
        },
        "algorithm_version": ALGORITHM_VERSION,
        "integration_points": int(strain_vector.size),
        "warnings": warnings,
    }


def cycle_metrics(
    load_strain: Sequence[float] | np.ndarray,
    load_stress: Sequence[float] | np.ndarray,
    unload_strain: Sequence[float] | np.ndarray,
    unload_stress: Sequence[float] | np.ndarray,
) -> dict[str, Any]:
    """Derive one loading-unloading cycle without mutating either branch.

    Loading must be supplied in non-decreasing strain order. Unloading may be
    supplied in either monotonic direction; a descending branch is reversed in
    a private integration copy and reported through ``warnings``.
    """

    load_x, load_y = _validate_curve(load_strain, load_stress, label="loading")
    unload_x, unload_y = _validate_curve(
        unload_strain, unload_stress, label="unloading"
    )

    load_differences = np.diff(load_x)
    if np.any(load_differences < 0.0):
        raise ValueError("loading strain must be non-decreasing in source point order")

    unload_differences = np.diff(unload_x)
    unload_is_ascending = bool(np.all(unload_differences >= 0.0))
    unload_is_descending = bool(np.all(unload_differences <= 0.0))
    if not (unload_is_ascending or unload_is_descending):
        raise ValueError("unloading strain must follow a single monotonic direction")

    warnings: list[str] = []
    if np.any(load_differences == 0.0):
        warnings.append("duplicate_loading_strain_values")
    if np.any(unload_differences == 0.0):
        warnings.append("duplicate_unloading_strain_values")

    # When all unloading strains are identical, ascending is chosen and no
    # direction warning is necessary; either orientation has the same zero area.
    if unload_is_descending and not unload_is_ascending:
        integration_unload_x = unload_x[::-1]
        integration_unload_y = unload_y[::-1]
        warnings.append("unloading_reversed_to_ascending")
    else:
        integration_unload_x = unload_x
        integration_unload_y = unload_y

    warnings.extend(
        _origin_warning(load_x, load_y, "loading_curve_does_not_start_at_origin")
    )
    if np.any(load_x < 0.0):
        warnings.append("negative_loading_strain_values")
    if np.any(load_y < 0.0):
        warnings.append("negative_loading_stress_values")
    if np.any(integration_unload_x < 0.0):
        warnings.append("negative_unloading_strain_values")
    if np.any(integration_unload_y < 0.0):
        warnings.append("negative_unloading_stress_values")

    peak_load_strain = float(load_x[-1])
    peak_unload_strain = float(integration_unload_x[-1])
    if not math.isclose(
        peak_load_strain,
        peak_unload_strain,
        rel_tol=1e-6,
        abs_tol=_ZERO_TOLERANCE,
    ):
        warnings.append("branch_peak_strain_mismatch")

    loading_area = _integrate_validated(load_x, load_y)
    unloading_area = _integrate_validated(integration_unload_x, integration_unload_y)
    hysteresis = loading_area - unloading_area
    if hysteresis < -_ZERO_TOLERANCE:
        warnings.append("negative_hysteresis")

    residual_strain = float(integration_unload_x[0])
    if math.isclose(peak_load_strain, 0.0, abs_tol=_ZERO_TOLERANCE):
        strain_recovery_ratio: float | None = None
        warnings.append("zero_peak_strain")
    else:
        strain_recovery_ratio = 1.0 - residual_strain / peak_load_strain

    if math.isclose(loading_area, 0.0, abs_tol=_ZERO_TOLERANCE):
        energy_return_ratio: float | None = None
        warnings.append("zero_loading_area")
    else:
        energy_return_ratio = unloading_area / loading_area

    return {
        "loading_area_mj_m3": loading_area,
        "unloading_area_mj_m3": unloading_area,
        "hysteresis_mj_m3": hysteresis,
        "residual_strain": residual_strain,
        "strain_recovery_ratio": strain_recovery_ratio,
        "energy_return_ratio": energy_return_ratio,
        "units": {
            "loading_area_mj_m3": ENERGY_DENSITY_UNIT,
            "unloading_area_mj_m3": ENERGY_DENSITY_UNIT,
            "hysteresis_mj_m3": ENERGY_DENSITY_UNIT,
            "residual_strain": "1",
            "strain_recovery_ratio": "1",
            "energy_return_ratio": "1",
        },
        "algorithm_version": ALGORITHM_VERSION,
        "integration_points": {
            "loading": int(load_x.size),
            "unloading": int(unload_x.size),
        },
        "warnings": warnings,
    }


__all__ = [
    "ALGORITHM_VERSION",
    "ENERGY_DENSITY_UNIT",
    "cycle_metrics",
    "tensile_metrics",
    "trapezoid_integral",
]

