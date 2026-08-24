from pathlib import Path

import pandas as pd
import pytest
from rdkit import Chem

import 构建TPU低聚链 as builder


ROOT = Path(__file__).resolve().parents[2]


def test_single_urethane_coupling_changes_nco_to_nhcoo_and_keeps_other_sites():
    diisocyanate = Chem.MolFromSmiles("O=C=NCCCCCCN=C=O")
    diol = Chem.MolFromSmiles("OCCCCO")
    nco_sites = builder.nco_sites(diisocyanate)
    oh_sites = builder.oh_sites(diol)
    product = builder.couple_specific_nco_oh(
        diisocyanate,
        diol,
        nco_carbon_index=nco_sites[0][1],
        oh_oxygen_index=oh_sites[0],
    )
    assert len(builder.nco_sites(product)) == 1
    assert len(builder.oh_sites(product)) == 1
    assert builder.urethane_bond_count(product) == 1
    assert Chem.MolToSmiles(product) == "O=C=NCCCCCCNC(=O)OCCCCO"


def test_linear_proxy_matches_integer_counts_bonds_atoms_and_end_groups():
    result = builder.build_linear_oligomer(
        diisocyanate_smiles="O=C=NCCCCCCN=C=O",
        macrodiol_smiles="OCCCCOCCCCO",
        chain_extender_smiles="OCCCCO",
        macrodiol_count=2,
        chain_extender_count=3,
        diisocyanate_count=5,
    )
    molecule = Chem.MolFromSmiles(result["canonical_smiles"])
    assert molecule is not None
    assert result["oh_unit_sequence"] == "macrodiol;chain_extender;chain_extender;macrodiol;chain_extender"
    assert result["urethane_bond_count"] == 9
    assert result["remaining_nco_group_count"] == 1
    assert result["remaining_oh_group_count"] == 1
    assert result["atom_count"] == Chem.AddHs(molecule).GetNumAtoms()
    assert result["chemical_graph_status"] == "completed"
    assert result["three_dimensional_status"] == "not_generated"


def test_invalid_counts_and_nonfunctional_structures_fail_closed():
    with pytest.raises(ValueError, match="二异氰酸酯数"):
        builder.build_linear_oligomer(
            "O=C=NCCCCCCN=C=O", "OCCCCOCCCCO", "OCCCCO", 1, 1, 1
        )
    with pytest.raises(ValueError, match="恰好两个NCO"):
        builder.build_linear_oligomer(
            "CC", "OCCCCOCCCCO", "OCCCCO", 1, 1, 2
        )
    with pytest.raises(ValueError, match="恰好两个OH"):
        builder.build_linear_oligomer(
            "O=C=NCCCCCCN=C=O", "CC", "OCCCCO", 1, 1, 2
        )


def test_current_12_plans_build_deterministic_graphs_and_writer(tmp_path: Path):
    plan_path = ROOT / "计算" / "现实MD" / "低聚链计量计划.csv"
    component_path = ROOT / "数据" / "现实库" / "构件.csv"
    macro_path = ROOT / "数据" / "现实库" / "PTMG代表模型.csv"
    plan = pd.read_csv(plan_path)
    components = pd.read_csv(component_path)
    macros = pd.read_csv(macro_path)
    first = builder.build_graph_table(plan, components, macros)
    second = builder.build_graph_table(
        plan.sample(frac=1.0, random_state=7),
        components.sample(frac=1.0, random_state=8),
        macros,
    )
    pd.testing.assert_frame_equal(
        first.sort_values("formulation_id").reset_index(drop=True),
        second.sort_values("formulation_id").reset_index(drop=True),
    )
    assert len(first) == 12
    assert first["remaining_nco_group_count"].eq(1).all()
    assert first["remaining_oh_group_count"].eq(1).all()
    assert first["canonical_smiles"].str.len().gt(100).all()
    manifest = builder.write_release(
        plan_path,
        component_path,
        macro_path,
        tmp_path / "out",
        release_id="test-tpu-oligomer-graphs",
    )
    assert manifest["counts"] == {"plans": 12, "graphs_completed": 12}
    assert (tmp_path / "out" / "低聚链化学图.csv.gz").is_file()
