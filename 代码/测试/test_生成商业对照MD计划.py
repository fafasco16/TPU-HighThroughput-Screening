from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "生成商业对照MD计划.py"
SPEC = importlib.util.spec_from_file_location("commercial_md_plan", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _shortlist() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "experiment_order": order,
                "experiment_stage": "A_calibration",
                "formulation_id": formulation,
                "diisocyanate_name": dii,
                "macrodiol_name": "PTMG-1000",
                "chain_extender_name": "1,4-BDO",
                "hard_segment_mass_fraction_target": 0.45,
                "nco_oh_ratio_target": 1.02,
            }
            for order, formulation, dii in [(2, "aromatic", "MDI"), (3, "aliphatic", "IPDI")]
        ]
    )


def test_plan_has_two_controls_three_replicates_and_target_box_size() -> None:
    chains = pd.DataFrame(
        [
            {"formulation_id": "aromatic", "atom_count": 867},
            {"formulation_id": "aliphatic", "atom_count": 319},
        ]
    )
    gate = {
        "forcefield_parameter_gate": {"status": "candidate_external_pending"},
        "production_md_permission": "blocked",
    }
    plan = MODULE.build_plan(_shortlist(), chains, gate)
    assert len(plan) == 6
    assert plan.groupby("formulation_id").size().eq(3).all()
    assert plan["estimated_box_atom_count"].ge(10_000).all()
    assert plan["planned_chain_count"].mod(4).eq(0).all()
    assert plan["packing_seed"].is_unique
    assert plan["execution_status"].str.startswith("planned_not_executable").all()


def test_chain_count_rounds_up_to_multiple_of_four() -> None:
    assert MODULE.chain_count_for_target(867) == 12
    assert MODULE.chain_count_for_target(319) == 32


def test_plan_refuses_fewer_than_three_replicates() -> None:
    chains = pd.DataFrame(
        [
            {"formulation_id": "aromatic", "atom_count": 867},
            {"formulation_id": "aliphatic", "atom_count": 319},
        ]
    )
    gate = {
        "forcefield_parameter_gate": {"status": "candidate_external_pending"},
        "production_md_permission": "blocked",
    }
    with pytest.raises(ValueError, match="至少需要3个"):
        MODULE.build_plan(_shortlist(), chains, gate, replicates=2)
