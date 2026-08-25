from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "汇总氨基甲酸酯MM约束松弛.py"
SPEC = importlib.util.spec_from_file_location("mm_relax_summary", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_rigid_mm_join_calculates_relaxation_change() -> None:
    rigid = pd.DataFrame(
        [{"fragment_name": "f", "validation_family": "a", "requested_angle_degrees": 0, "gaff2_relative_energy_kcal_mol": 3.0}]
    )
    mm = pd.DataFrame(
        [{"fragment_name": "f", "validation_family": "a", "requested_angle_degrees": 0, "relaxed_gaff2_relative_energy_kcal_mol": 1.0}]
    )
    joined = MODULE.join_rigid_mm(rigid, mm)
    assert joined.iloc[0]["mm_relaxation_change_from_rigid_kcal_mol"] == pytest.approx(-2.0)


def test_missing_rigid_point_fails_closed() -> None:
    rigid = pd.DataFrame(columns=["fragment_name", "validation_family", "requested_angle_degrees", "gaff2_relative_energy_kcal_mol"])
    mm = pd.DataFrame([{"fragment_name": "f", "validation_family": "a", "requested_angle_degrees": 0}])
    with pytest.raises(ValueError, match="未完全连接"):
        MODULE.join_rigid_mm(rigid, mm)
