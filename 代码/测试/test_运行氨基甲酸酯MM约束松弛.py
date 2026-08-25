from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "运行氨基甲酸酯MM约束松弛.py"
SPEC = importlib.util.spec_from_file_location("mm_relaxed_scan", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_lammps_input_includes_restraint_energy_and_unrestrained_run0() -> None:
    text = MODULE.build_lammps_input([3, 2, 4, 5], -180, restraint_k=5000.0)
    assert "fix REST all restrain dihedral 4 3 5 6 5000.00000000 5000.00000000 0.00000000" in text
    assert "fix_modify REST energy yes" in text
    assert "unfix REST" in text
    assert "UNRESTRAINED_PE" in text


def test_rdkit_zero_maps_to_lammps_minus_180() -> None:
    text = MODULE.build_lammps_input([3, 2, 4, 5], 0, restraint_k=5000.0)
    assert "5000.00000000 5000.00000000 -180.00000000" in text


def test_invalid_restraint_fails_closed() -> None:
    with pytest.raises(ValueError):
        MODULE.build_lammps_input([1, 2, 3, 4], 0, restraint_k=0)


def test_unrestrained_energy_parser(tmp_path: Path) -> None:
    path = tmp_path / "energy.txt"
    path.write_text("UNRESTRAINED_PE -12.345\n", encoding="utf-8")
    assert MODULE.parse_unrestrained_energy(path) == pytest.approx(-12.345)
