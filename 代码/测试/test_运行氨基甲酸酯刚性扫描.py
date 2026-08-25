from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from rdkit import Chem


SCRIPT = Path(__file__).resolve().parents[1] / "运行氨基甲酸酯刚性扫描.py"
SPEC = importlib.util.spec_from_file_location("urethane_rigid_scan", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_default_angle_grid_has_24_unique_points() -> None:
    grid = MODULE.default_angle_grid(15)
    assert len(grid) == 24
    assert grid[0] == -180 and grid[-1] == 165


def test_invalid_angle_step_fails_closed() -> None:
    with pytest.raises(ValueError):
        MODULE.default_angle_grid(14)


@pytest.mark.parametrize("smiles", ["COC(=O)NC", "COC(=O)Nc1ccccc1"])
def test_target_torsion_is_carbonyl_o_c_n_substituent(smiles: str) -> None:
    molecule = Chem.MolFromSmiles(smiles)
    torsion = MODULE.select_urethane_torsion(molecule)
    assert [molecule.GetAtomWithIdx(i).GetSymbol() for i in torsion] == ["O", "C", "N", "C"]


def test_multiple_urethane_cores_fail_closed() -> None:
    with pytest.raises(ValueError, match="不是1个"):
        MODULE.select_urethane_torsion(Chem.MolFromSmiles("COC(=O)NCCOC(=O)NC"))
