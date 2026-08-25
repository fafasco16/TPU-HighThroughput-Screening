from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "验证氨基甲酸酯扭转修正外部片段.py"
SPEC = importlib.util.spec_from_file_location("external_torsion_validation", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _surfaces() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    angles = [-180, -120, -60, 0, 60, 120]
    dft_rows = []
    mm_rows = []
    coefficient_rows = []
    for family, fragment, coefficient in [
        ("aliphatic_urethane", "ext_a", 1.0),
        ("aromatic_urethane", "ext_b", 0.5),
    ]:
        radians = np.deg2rad(np.asarray(angles, dtype=float))
        correction = coefficient * (np.cos(radians) - 1.0)
        mm_energy = np.array([4.0, 3.0, 1.0, 0.0, 1.0, 3.0])
        dft_energy = mm_energy + correction
        dft_energy -= dft_energy.min()
        for angle, dft_value, mm_value in zip(angles, dft_energy, mm_energy):
            key = {
                "fragment_name": fragment,
                "validation_family": family,
                "requested_angle_degrees": angle,
                "point_status": "completed",
                "angle_drift_degrees": 0.1,
            }
            dft_rows.append(
                {**key, "relaxed_dft_relative_energy_kcal_mol": dft_value}
            )
            mm_rows.append(
                {**key, "relaxed_gaff2_relative_energy_kcal_mol": mm_value}
            )
        coefficient_rows.append(
            {
                "validation_family": family,
                "periodicity": 1,
                "coefficient_for_cos_nphi_minus_one_kcal_mol": coefficient,
            }
        )
    return pd.DataFrame(dft_rows), pd.DataFrame(mm_rows), pd.DataFrame(coefficient_rows)


def test_exact_external_surface_passes_all_gates() -> None:
    dft, mm, coefficients = _surfaces()
    evaluated, metrics = MODULE.validate_and_score(dft, mm, coefficients)
    assert len(evaluated) == 12
    assert metrics["external_validation_pass"].all()
    assert metrics["external_rmse_kcal_mol"].max() == pytest.approx(0.0)


def test_incomplete_angle_grid_fails_closed() -> None:
    dft, mm, coefficients = _surfaces()
    dft = dft.iloc[:-1]
    mm = mm.iloc[:-1]
    with pytest.raises(ValueError, match="六角度网格"):
        MODULE.validate_and_score(dft, mm, coefficients)


def test_blocked_dft_point_fails_closed() -> None:
    dft, mm, coefficients = _surfaces()
    dft.loc[0, "point_status"] = "failed"
    with pytest.raises(ValueError, match="全部完成"):
        MODULE.validate_and_score(dft, mm, coefficients)


def test_single_family_surface_can_be_scored_without_claiming_other_family() -> None:
    dft, mm, coefficients = _surfaces()
    dft = dft.query("validation_family == 'aliphatic_urethane'")
    mm = mm.query("validation_family == 'aliphatic_urethane'")
    coefficients = coefficients.query("validation_family == 'aliphatic_urethane'")
    evaluated, metrics = MODULE.validate_and_score(dft, mm, coefficients)
    assert len(evaluated) == 6
    assert len(metrics) == 1
    assert metrics.iloc[0]["external_validation_pass"]
