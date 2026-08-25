from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "拟合氨基甲酸酯松弛扭转修正.py"
SPEC = importlib.util.spec_from_file_location("relaxed_torsion_fit", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_zero_at_planar_basis_is_exactly_zero() -> None:
    matrix = MODULE.zero_at_planar_design(np.array([0.0]), 2)
    assert np.array_equal(matrix, np.zeros((1, 2)))


def test_low_order_fit_recovers_synthetic_coefficients() -> None:
    angles = np.array([-180.0, -150.0, -90.0, 0.0])
    expected = np.array([1.25, -0.4])
    target = MODULE.zero_at_planar_design(angles, 2) @ expected
    actual = MODULE.fit_zero_at_planar(angles, target, 2)
    assert actual == pytest.approx(expected)
    assert np.isfinite(MODULE.leave_one_out_rmse(angles, target, 2))


def test_validation_requires_two_families_with_four_completed_points() -> None:
    frame = pd.DataFrame(
        [
            {
                "fragment_name": "f",
                "validation_family": "a",
                "requested_angle_degrees": angle,
                "comparison_status": "comparable_relaxed_point",
                "relaxed_dft_relative_energy_kcal_mol": 0.0,
                "relaxed_gaff2_relative_energy_kcal_mol": 0.0,
            }
            for angle in [-180, -150, -90, 0]
        ]
    )
    with pytest.raises(ValueError, match="两个家族"):
        MODULE.validate_relaxed_comparison(frame)


def test_validation_rejects_blocked_point() -> None:
    rows = []
    for family in ["a", "b"]:
        for angle in [-180, -150, -90, 0]:
            rows.append(
                {
                    "fragment_name": family,
                    "validation_family": family,
                    "requested_angle_degrees": angle,
                    "comparison_status": (
                        "blocked_dft_or_mm_not_completed"
                        if family == "b" and angle == -90
                        else "comparable_relaxed_point"
                    ),
                    "relaxed_dft_relative_energy_kcal_mol": 0.0,
                    "relaxed_gaff2_relative_energy_kcal_mol": 0.0,
                }
            )
    with pytest.raises(ValueError, match="全部计划点"):
        MODULE.validate_relaxed_comparison(pd.DataFrame(rows))
