from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "汇总氨基甲酸酯受约束松弛.py"
SPEC = importlib.util.spec_from_file_location("relaxed_scan_summary", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_rigid_relaxed_join_and_change() -> None:
    rigid = pd.DataFrame(
        [
            {
                "fragment_name": "f",
                "validation_family": "aliphatic_urethane",
                "requested_angle_degrees": 0,
                "dft_relative_energy_kcal_mol": 2.0,
                "gaff2_relative_energy_kcal_mol": 3.0,
                "gaff2_minus_dft_relative_energy_kcal_mol": 1.0,
            }
        ]
    )
    relaxed = pd.DataFrame(
        [
            {
                "fragment_name": "f",
                "validation_family": "aliphatic_urethane",
                "requested_angle_degrees": 0,
                "relaxed_dft_relative_energy_kcal_mol": 0.5,
                "point_status": "completed",
            }
        ]
    )
    joined = MODULE.join_rigid_relaxed(rigid, relaxed)
    assert joined.iloc[0]["relaxation_change_from_rigid_dft_kcal_mol"] == pytest.approx(-1.5)


def test_missing_rigid_point_fails_closed() -> None:
    rigid = pd.DataFrame(
        columns=[
            "fragment_name",
            "validation_family",
            "requested_angle_degrees",
            "dft_relative_energy_kcal_mol",
            "gaff2_relative_energy_kcal_mol",
            "gaff2_minus_dft_relative_energy_kcal_mol",
        ]
    )
    relaxed = pd.DataFrame(
        [{"fragment_name": "f", "validation_family": "a", "requested_angle_degrees": 0, "point_status": "completed"}]
    )
    with pytest.raises(ValueError, match="未完全连接"):
        MODULE.join_rigid_relaxed(rigid, relaxed)
