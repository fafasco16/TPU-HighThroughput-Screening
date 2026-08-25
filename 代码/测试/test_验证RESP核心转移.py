from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest
from rdkit import Chem


SCRIPT = Path(__file__).resolve().parents[1] / "验证RESP核心转移.py"
SPEC = importlib.util.spec_from_file_location("resp_core_transfer", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_classify_aliphatic_and_aromatic_n_substituent() -> None:
    aliphatic = Chem.MolFromSmiles("COC(=O)NC")
    am = aliphatic.GetSubstructMatch(MODULE.URETHANE_PATTERN)
    assert MODULE.classify_n_substituent(aliphatic, am[3], am[1]) == "aliphatic"
    aromatic = Chem.MolFromSmiles("COC(=O)Nc1ccccc1")
    ar = aromatic.GetSubstructMatch(MODULE.URETHANE_PATTERN)
    assert MODULE.classify_n_substituent(aromatic, ar[3], ar[1]) == "aromatic"


def _parameter_table() -> pd.DataFrame:
    rows = []
    fragments = [
        ("a_u", "aliphatic_urethane", "COC(=O)NC"),
        ("r_u", "aromatic_urethane", "COC(=O)Nc1ccccc1"),
        ("a_i", "aliphatic_terminal_isocyanate", "CCN=C=O"),
        ("r_i", "aromatic_terminal_isocyanate", "O=C=Nc1ccccc1"),
    ]
    for fragment, family, smiles in fragments:
        molecule = Chem.MolFromSmiles(smiles)
        pattern = MODULE.URETHANE_PATTERN if family.endswith("urethane") else MODULE.ISOCYANATE_PATTERN
        match = molecule.GetSubstructMatch(pattern)
        for index, atom in enumerate(molecule.GetAtoms()):
            rows.append(
                {
                    "fragment_name": fragment,
                    "validation_family": family,
                    "smiles": smiles,
                    "atom_index_zero_based": index,
                    "element": atom.GetSymbol(),
                    "functional_core": index in match,
                    "joint_stage2_resp_charge_e": index / 100.0,
                }
            )
    return MODULE.build_core_parameter_table(pd.DataFrame(rows))


def test_core_parameter_table_has_fourteen_roles() -> None:
    table = _parameter_table()
    assert len(table) == 14
    assert table["validation_family"].nunique() == 4


def test_chain_mapping_closes_one_urethane_and_one_nco() -> None:
    mapping, summary = MODULE.map_chain(
        "test",
        "COC(=O)NCCN=C=O",
        _parameter_table(),
        expected_urethane_count=1,
        expected_nco_count=1,
    )
    assert len(mapping) == 7
    assert summary["urethane_occurrence_count"] == 1
    assert summary["terminal_nco_occurrence_count"] == 1


def test_chain_mapping_fails_on_wrong_expected_count() -> None:
    with pytest.raises(ValueError, match="氨基甲酸酯匹配数"):
        MODULE.map_chain(
            "test",
            "COC(=O)NCCN=C=O",
            _parameter_table(),
            expected_urethane_count=2,
            expected_nco_count=1,
        )
