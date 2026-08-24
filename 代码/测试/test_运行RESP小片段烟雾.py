from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "运行RESP小片段烟雾.py"
SPEC = importlib.util.spec_from_file_location("resp_smoke", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_charge_validation_accepts_neutral_finite_arrays() -> None:
    stage1 = np.array([[0.2, -0.2], [0.1, -0.1]])
    stage2 = np.array([[0.2, -0.2], [0.15, -0.15]])
    metrics = MODULE.validate_charge_arrays(
        stage1, stage2, atom_count=2, target_charge=0.0
    )
    assert metrics["stage2_resp_charge_sum_e"] == pytest.approx(0.0)
    assert metrics["stage1_to_stage2_resp_rms_e"] > 0


def test_charge_validation_rejects_wrong_shape() -> None:
    with pytest.raises(ValueError, match="维度"):
        MODULE.validate_charge_arrays(
            np.zeros((2, 3)), np.zeros((2, 2)), atom_count=2, target_charge=0.0
        )


def test_charge_validation_rejects_nonfinite() -> None:
    stage1 = np.array([[0.0, 0.0], [0.0, np.nan]])
    with pytest.raises(ValueError, match="非有限"):
        MODULE.validate_charge_arrays(
            stage1, np.zeros((2, 2)), atom_count=2, target_charge=0.0
        )


def test_charge_validation_rejects_charge_sum_error() -> None:
    with pytest.raises(ValueError, match="第二阶段RESP电荷和"):
        MODULE.validate_charge_arrays(
            np.zeros((2, 2)),
            np.array([[0.0, 0.0], [0.2, 0.0]]),
            atom_count=2,
            target_charge=0.0,
        )
