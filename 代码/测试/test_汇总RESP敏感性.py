from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "汇总RESP敏感性.py"
SPEC = importlib.util.spec_from_file_location("resp_sensitivity_summary", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_functional_core_indices_cover_expected_atoms() -> None:
    assert len(MODULE.functional_core_indices("COC(=O)NC", "aliphatic_urethane")) == 4
    assert len(MODULE.functional_core_indices("CCN=C=O", "aliphatic_terminal_isocyanate")) == 3


def test_unknown_family_fails_closed() -> None:
    with pytest.raises(ValueError, match="未知RESP敏感性家族"):
        MODULE.functional_core_indices("CC", "unknown")


def test_sensitivity_statistics_separate_seed_and_density() -> None:
    rows = []
    for seed, density, charge in [
        (1, 0.5, 0.1),
        (2, 0.5, 0.2),
        (1, 1.0, 0.3),
        (2, 1.0, 0.4),
    ]:
        rows.append(
            {
                "fragment_name": "methyl_n_methyl_carbamate",
                "validation_family": "aliphatic_urethane",
                "smiles": "COC(=O)NC",
                "atom_index_zero_based": 0,
                "element": "C",
                "functional_core": True,
                "random_seed": seed,
                "vdw_point_density": density,
                "stage2_resp_charge_e": charge,
            }
        )
    conformer, density, family = MODULE.summarize_sensitivity(pd.DataFrame(rows))
    assert len(conformer) == 2
    assert len(density) == 2
    assert family.iloc[0]["maximum_atom_overall_range_e"] == pytest.approx(0.3)
