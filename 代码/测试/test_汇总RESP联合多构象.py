from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "汇总RESP联合多构象.py"
SPEC = importlib.util.spec_from_file_location("joint_resp_summary", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_joint_comparison_uses_density_one_independent_mean() -> None:
    joint = pd.DataFrame(
        [
            {
                "fragment_name": "f",
                "validation_family": "aliphatic_urethane",
                "atom_index_zero_based": 0,
                "element": "C",
                "functional_core": True,
                "joint_stage2_resp_charge_e": 0.25,
            }
        ]
    )
    independent = pd.DataFrame(
        [
            {"fragment_name": "f", "atom_index_zero_based": 0, "element": "C", "vdw_point_density": 1.0, "stage2_resp_charge_e": value}
            for value in [0.1, 0.2, 0.3]
        ]
        + [
            {"fragment_name": "f", "atom_index_zero_based": 0, "element": "C", "vdw_point_density": 2.0, "stage2_resp_charge_e": 9.0}
        ]
    )
    comparison, summary = MODULE.compare_joint_to_independent(joint, independent)
    assert comparison.iloc[0]["independent_mean_e"] == pytest.approx(0.2)
    assert comparison.iloc[0]["joint_minus_independent_mean_e"] == pytest.approx(0.05)
    assert summary.iloc[0]["maximum_core_absolute_joint_minus_independent_mean_e"] == pytest.approx(0.05)


def test_joint_comparison_requires_three_independent_conformers() -> None:
    joint = pd.DataFrame(
        [{"fragment_name": "f", "validation_family": "aliphatic_urethane", "atom_index_zero_based": 0, "element": "C", "functional_core": True, "joint_stage2_resp_charge_e": 0.0}]
    )
    independent = pd.DataFrame(
        [{"fragment_name": "f", "atom_index_zero_based": 0, "element": "C", "vdw_point_density": 1.0, "stage2_resp_charge_e": 0.0}]
    )
    with pytest.raises(ValueError, match="三个构象"):
        MODULE.compare_joint_to_independent(joint, independent)
