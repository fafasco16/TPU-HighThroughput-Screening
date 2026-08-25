from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "拟合氨基甲酸酯扭转修正.py"
SPEC = importlib.util.spec_from_file_location("torsion_correction", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_fourier_fit_recovers_known_cosine_coefficients() -> None:
    angles = np.arange(0, 181, 15)
    matrix = MODULE.design_matrix(angles, 2)
    expected = np.array([1.0, 2.0, -0.5])
    fitted = MODULE.fit_coefficients(angles, matrix @ expected, 2)
    assert np.allclose(fitted, expected)
    assert MODULE.leave_angle_out_rmse(angles, matrix @ expected, 2) < 1e-10


def test_symmetrization_averages_positive_and_negative_angles() -> None:
    rows = []
    for angle, residual in [(-15, 2.0), (15, 4.0), (0, 1.0), (-180, 3.0)]:
        rows.append(
            {
                "fragment_name": "f",
                "validation_family": "aliphatic_urethane",
                "requested_angle_degrees": angle,
                "dft_relative_energy_kcal_mol": residual,
                "gaff2_relative_energy_kcal_mol": 0.0,
            }
        )
    with pytest.raises(ValueError, match="覆盖不闭合"):
        MODULE.symmetrize_residual(pd.DataFrame(rows))


def test_invalid_fourier_order_fails_closed() -> None:
    with pytest.raises(ValueError):
        MODULE.design_matrix(np.array([0.0]), 0)
