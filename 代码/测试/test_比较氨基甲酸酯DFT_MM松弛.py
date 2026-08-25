from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "比较氨基甲酸酯DFT_MM松弛.py"
SPEC = importlib.util.spec_from_file_location("relaxed_dft_mm", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_comparison_keeps_failed_dft_and_compares_completed_point() -> None:
    dft = pd.DataFrame(
        [
            {"fragment_name": "f", "validation_family": "a", "requested_angle_degrees": 0, "point_status": "completed", "relaxed_dft_relative_energy_kcal_mol": 1.0},
            {"fragment_name": "f", "validation_family": "a", "requested_angle_degrees": 90, "point_status": "failed_OptimizationConvergenceError", "relaxed_dft_relative_energy_kcal_mol": pd.NA},
        ]
    )
    mm = pd.DataFrame(
        [
            {"fragment_name": "f", "validation_family": "a", "requested_angle_degrees": angle, "point_status": "completed", "relaxed_gaff2_relative_energy_kcal_mol": energy, "angle_drift_degrees": 0.1}
            for angle, energy in [(0, 2.0), (90, 5.0)]
        ]
    )
    result = MODULE.compare_relaxed_surfaces(dft, mm)
    assert result.loc[result["requested_angle_degrees"].eq(0), "gaff2_minus_dft_relaxed_energy_kcal_mol"].iloc[0] == pytest.approx(1.0)
    assert result.loc[result["requested_angle_degrees"].eq(90), "comparison_status"].iloc[0].startswith("blocked")


def test_missing_mm_point_fails_closed() -> None:
    dft = pd.DataFrame([{"fragment_name": "f", "validation_family": "a", "requested_angle_degrees": 0, "point_status": "completed", "relaxed_dft_relative_energy_kcal_mol": 0.0}])
    mm = pd.DataFrame(columns=["fragment_name", "validation_family", "requested_angle_degrees", "point_status", "relaxed_gaff2_relative_energy_kcal_mol", "angle_drift_degrees"])
    with pytest.raises(ValueError, match="未完全连接"):
        MODULE.compare_relaxed_surfaces(dft, mm)
