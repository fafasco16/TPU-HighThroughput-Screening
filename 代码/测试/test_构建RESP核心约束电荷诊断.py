from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "构建RESP核心约束电荷诊断.py"
SPEC = importlib.util.spec_from_file_location("hybrid_charge_diagnostic", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_minimum_l2_completion_preserves_core_and_neutralizes() -> None:
    initial = np.array([0.1, -0.2, 0.3, -0.1])
    completed, correction = MODULE.complete_charges_minimum_l2(
        initial, np.array([1]), np.array([-0.5])
    )
    assert completed[1] == pytest.approx(-0.5)
    assert completed.sum() == pytest.approx(0.0, abs=1e-12)
    assert np.allclose(completed[[0, 2, 3]] - initial[[0, 2, 3]], correction)


def test_duplicate_fixed_indices_fail_closed() -> None:
    with pytest.raises(ValueError, match="重复"):
        MODULE.complete_charges_minimum_l2(
            np.zeros(3), np.array([0, 0]), np.array([0.1, 0.1])
        )


def test_neutral_point_charge_dipole_is_translation_invariant() -> None:
    charges = np.array([1.0, -1.0])
    coordinates = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    first = MODULE.point_charge_dipole_debye(charges, coordinates)
    second = MODULE.point_charge_dipole_debye(charges, coordinates + 10.0)
    assert np.allclose(first, second)
