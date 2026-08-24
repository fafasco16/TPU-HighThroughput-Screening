import math
from pathlib import Path

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

import 反应位点描述符 as sites


def _embedded(smiles: str, seed: int = 20260824):
    molecule = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(molecule, randomSeed=seed) == 0
    AllChem.UFFOptimizeMolecule(molecule, maxIters=1000)
    conformer = molecule.GetConformer()
    symbols = tuple(atom.GetSymbol() for atom in molecule.GetAtoms())
    coordinates = np.asarray(
        [list(conformer.GetAtomPosition(index)) for index in range(molecule.GetNumAtoms())]
    )
    return symbols, coordinates


def _xyz_text(symbols, coordinates, comments=("-10.0",)):
    atom_rows = "".join(
        f"{symbol} {x:.12f} {y:.12f} {z:.12f}\n"
        for symbol, (x, y, z) in zip(symbols, coordinates)
    )
    return "".join(f"{len(symbols)}\n{comment}\n{atom_rows}" for comment in comments)


def test_identifies_exactly_two_role_specific_sites_and_explicit_h_order():
    nco_kind, nco_sites = sites.identify_reactive_sites(
        "O=C=NCCCCCCN=C=O", "diisocyanate"
    )
    oh_kind, oh_sites = sites.identify_reactive_sites("OCCO", "chain_extender")
    assert nco_kind == "nco_carbon"
    assert oh_kind == "hydroxyl_oxygen"
    assert len(nco_sites) == len(oh_sites) == 2
    model = sites.prepare_reactive_site_model("OCCO", "chain_extender")
    assert len(model.element_symbols) == model.molecule.GetNumAtoms()
    assert model.element_symbols.count("H") == 6
    assert tuple(model.molecule.GetAtomWithIdx(i).GetSymbol() for i in oh_sites) == ("O", "O")


@pytest.mark.parametrize(
    "smiles, role, error",
    [
        ("CCO", "chain_extender", "恰好为2"),
        ("O=C=NCC", "diisocyanate", "恰好为2"),
        ("not-smiles", "chain_extender", "无法解析"),
        ("OCCO", "unsupported", "不支持"),
    ],
)
def test_site_count_parse_and_role_errors_fail_closed(smiles, role, error):
    with pytest.raises(sites.ReactiveSiteDescriptorError, match=error):
        sites.prepare_reactive_site_model(smiles, role)


def test_rigid_diol_descriptor_has_two_sites_and_all_aggregates():
    smiles = "Oc1ccc(O)cc1"
    symbols, coordinates = _embedded(smiles)
    model = sites.prepare_reactive_site_model(smiles, "chain_extender")
    result = sites.describe_reactive_sites(model, symbols, coordinates)
    assert result["reactive_site_count"] == 2
    assert result["probe_radius_a"] == 1.4
    assert result["sphere_point_count"] == 960
    assert result["site_1_element"] == result["site_2_element"] == "O"
    assert 0 < result["site_1_relative_sasa"] <= 1
    assert 0 < result["site_2_relative_sasa"] <= 1
    for prefix in (
        "site_sasa_a2",
        "site_relative_sasa",
        "site_nonbonded_net_gap_a",
    ):
        first = result[prefix.replace("site_", "site_1_", 1)]
        second = result[prefix.replace("site_", "site_2_", 1)]
        assert result[f"{prefix}_mean"] == pytest.approx((first + second) / 2)
        assert result[f"{prefix}_min"] == pytest.approx(min(first, second))
        assert result[f"{prefix}_max"] == pytest.approx(max(first, second))
        assert result[f"{prefix}_abs_difference"] == pytest.approx(abs(first - second))


def test_geometric_shielding_reduces_site_accessibility_and_gap():
    smiles = "OCCO"
    symbols, coordinates = _embedded(smiles)
    model = sites.prepare_reactive_site_model(smiles, "chain_extender")
    baseline = sites.describe_reactive_sites(model, symbols, coordinates)
    shielded_coordinates = coordinates.copy()
    first_site, second_site = model.site_atom_indices
    shielded_coordinates[second_site] = shielded_coordinates[first_site] + np.array([0.3, 0, 0])
    shielded = sites.describe_reactive_sites(model, symbols, shielded_coordinates)
    assert shielded["site_1_relative_sasa"] < baseline["site_1_relative_sasa"]
    assert (
        shielded["site_1_nonbonded_net_gap_a"]
        < baseline["site_1_nonbonded_net_gap_a"]
    )


def test_xyz_element_order_mismatch_is_rejected_before_calculation():
    symbols, coordinates = _embedded("OCCO")
    model = sites.prepare_reactive_site_model("OCCO", "chain_extender")
    wrong = list(symbols)
    wrong[0], wrong[1] = wrong[1], wrong[0]
    with pytest.raises(sites.ReactiveSiteDescriptorError, match="元素序列"):
        sites.describe_reactive_sites(model, wrong, coordinates)


def test_missing_bondi_radius_and_invalid_parameters_fail_closed():
    with pytest.raises(sites.ReactiveSiteDescriptorError, match="Bondi半径"):
        sites.bondi_radius("Xe")
    symbols, coordinates = _embedded("OCCO")
    model = sites.prepare_reactive_site_model("OCCO", "chain_extender")
    with pytest.raises(sites.ReactiveSiteDescriptorError, match="有限正数"):
        sites.describe_reactive_sites(model, symbols, coordinates, probe_radius_a=0)
    with pytest.raises(sites.ReactiveSiteDescriptorError, match="至少4"):
        sites.describe_reactive_sites(model, symbols, coordinates, sphere_point_count=3)
    with pytest.raises(sites.ReactiveSiteDescriptorError, match="坐标形状"):
        sites.describe_reactive_sites(model, symbols, coordinates[:-1])
    bad = coordinates.copy()
    bad[0, 0] = math.nan
    with pytest.raises(sites.ReactiveSiteDescriptorError, match="非有限"):
        sites.describe_reactive_sites(model, symbols, bad)


def test_fibonacci_and_descriptor_parameters_are_deterministic():
    first_points = sites.fibonacci_sphere()
    second_points = sites.fibonacci_sphere()
    assert first_points is second_points
    assert len(first_points) == 960
    norms = np.linalg.norm(np.asarray(first_points), axis=1)
    assert np.max(np.abs(norms - 1.0)) < 1e-14
    symbols, coordinates = _embedded("OCCO")
    model = sites.prepare_reactive_site_model("OCCO", "chain_extender")
    first = sites.describe_reactive_sites(model, symbols, coordinates)
    second = sites.describe_reactive_sites(model, symbols, coordinates)
    assert first == second


def test_parse_multiframe_xyz_and_describe_task(tmp_path: Path):
    symbols, coordinates = _embedded("OCCO")
    path = tmp_path / "ensemble.xyz"
    path.write_text(
        _xyz_text(symbols, coordinates, comments=("-10.0", "-9.9")), encoding="utf-8"
    )
    task = {
        "task_index": 7,
        "task_slug": "0007-diol",
        "candidate_id": "diol",
        "component_role": "chain_extender",
        "canonical_smiles": "OCCO",
    }
    rows = sites.describe_task_xyz(task, path)
    assert [row["conformer_index"] for row in rows] == [1, 2]
    assert [row["xyz_comment"] for row in rows] == ["-10.0", "-9.9"]
    assert all(row["candidate_id"] == "diol" for row in rows)


def test_xyz_parser_and_task_schema_close_on_invalid_input(tmp_path: Path):
    missing = tmp_path / "missing.xyz"
    with pytest.raises(sites.ReactiveSiteDescriptorError, match="不存在"):
        sites.parse_xyz_conformers(missing)
    cases = {
        "empty.xyz": "\n",
        "bad-count.xyz": "x\ncomment\n",
        "truncated.xyz": "2\ncomment\nH 0 0 0\n",
        "bad-row.xyz": "1\ncomment\nH 0 0\n",
        "bad-coordinate.xyz": "1\ncomment\nH x 0 0\n",
        "nan.xyz": "1\ncomment\nH nan 0 0\n",
    }
    for name, text in cases.items():
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        with pytest.raises(sites.ReactiveSiteDescriptorError):
            sites.parse_xyz_conformers(path)
    valid = tmp_path / "valid.xyz"
    valid.write_text("1\ncomment\nH 0 0 0\n", encoding="utf-8")
    with pytest.raises(sites.ReactiveSiteDescriptorError, match="canonical_smiles"):
        sites.describe_task_xyz({}, valid)


def test_no_nonbonded_atoms_after_topological_exclusion_is_rejected():
    symbols, coordinates = _embedded("OCCO")
    model = sites.prepare_reactive_site_model("OCCO", "chain_extender")
    closed_model = sites.ReactiveSiteModel(
        molecule=model.molecule,
        site_kind=model.site_kind,
        site_atom_indices=model.site_atom_indices,
        element_symbols=model.element_symbols,
        excluded_topological_indices=(
            frozenset(range(len(symbols))),
            frozenset(range(len(symbols))),
        ),
    )
    with pytest.raises(sites.ReactiveSiteDescriptorError, match="非键接原子"):
        sites.describe_reactive_sites(closed_model, symbols, coordinates)
