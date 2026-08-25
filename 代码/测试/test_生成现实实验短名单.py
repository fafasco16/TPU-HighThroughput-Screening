from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "生成现实实验短名单.py"
SPEC = importlib.util.spec_from_file_location("experiment_shortlist", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_selection_policy_has_six_unique_candidates_and_order() -> None:
    policy = pd.DataFrame(MODULE.SELECTION_POLICY)
    assert len(policy) == 6
    assert policy["formulation_id"].is_unique
    assert policy["experiment_order"].tolist() == [1, 2, 3, 4, 5, 6]
    assert policy["experiment_stage"].value_counts().to_dict() == {
        "A_calibration": 3,
        "B_priority_exploration": 2,
        "C_specialty_deferred": 1,
    }


def test_aromatic_forcefield_family_is_explicit() -> None:
    assert "commercial_mdi_44" in MODULE.AROMATIC_DIISOCYANATES
    assert "commercial_ndi_15" in MODULE.AROMATIC_DIISOCYANATES
    assert "commercial_ipdi" not in MODULE.AROMATIC_DIISOCYANATES
