import json
from pathlib import Path

import pandas as pd
import pytest

import 候选配方 as candidates


ROOT = Path(__file__).resolve().parents[2]


def _row(component_class: str, smiles: str, *, mw: float, nco: int, oh: int) -> dict:
    return {
        "candidate_id": f"id-{component_class}-{smiles}",
        "source_id": "source_zenodo_12585902_polyuniverse_pu" if component_class == "diisocyanate" else "ds_smipoly_monomers",
        "source_family_id": "family-test",
        "preferred_name": "test",
        "canonical_smiles": smiles,
        "molecular_weight_calculated_g_mol": mw,
        "isocyanate_group_count": nco,
        "hydroxyl_group_count": oh,
        "amine_group_count": 0,
        "thiol_group_count": 0,
        "carboxylic_acid_group_count": 0,
        "cyclic_carbonate_group_count": 0,
        "epoxide_group_count": 0,
        "linear_component_class": component_class,
        "linear_tpu_building_block_ready": True,
        "license_spdx": "CC-BY-4.0",
        "license_status": "allow_with_attribution",
    }


def test_component_gates_reject_competing_groups_and_out_of_range_mass():
    valid = pd.Series(_row("diisocyanate", "O=C=NCCCCCCN=C=O", mw=168.2, nco=2, oh=0))
    assert candidates.component_gate_reason(valid, "diisocyanate") == ""
    too_small = valid.copy()
    too_small["molecular_weight_calculated_g_mol"] = 120.0
    assert "分子量" in candidates.component_gate_reason(too_small, "diisocyanate")
    competing = valid.copy()
    competing["amine_group_count"] = 1
    assert candidates.component_gate_reason(competing, "diisocyanate") == "含竞争反应官能团"
    extra_carbonyl = valid.copy()
    extra_carbonyl["canonical_smiles"] = "O=C=Nc1cc(N2C(=O)N(c3ccc(N=C=O)cc3)C2=O)ccc1"
    assert candidates.component_gate_reason(extra_carbonyl, "diisocyanate") == "二异氰酸酯含额外羰基"
    geminal = valid.copy()
    geminal["canonical_smiles"] = "CCCC(N=C=O)(N=C=O)OCCC"
    assert candidates.component_gate_reason(geminal, "diisocyanate") == "二异氰酸酯NCO连接位点不分离"
    isotope = valid.copy()
    isotope["canonical_smiles"] = "Cc1ccc(N=[14C]=O)cc1N=[14C]=O"
    assert candidates.component_gate_reason(isotope, "diisocyanate") == "含同位素标记，非原型合成构件"
    alkene = valid.copy()
    alkene["canonical_smiles"] = "C=CCN=C=O"
    alkene["isocyanate_group_count"] = 1
    assert "不饱和" in candidates.component_gate_reason(alkene, "diisocyanate")


def test_diverse_selection_and_combination_rotation_are_deterministic():
    rows = [
        _row("diisocyanate", "O=C=NCCCCCCN=C=O", mw=168.2, nco=2, oh=0),
        _row("diisocyanate", "O=C=Nc1ccc(CC)cc1N=C=O", mw=202.2, nco=2, oh=0),
        _row("chain_extender_diol", "OCCO", mw=62.1, nco=0, oh=2),
        _row("chain_extender_diol", "OCCCCO", mw=104.1, nco=0, oh=2),
        _row("macrodiol", "OCCCCCCCCCCCCCCCCO", mw=258.4, nco=0, oh=2),
        _row("macrodiol", "Oc1ccc(C2(c3ccc(O)cc3)CCCCC2)cc1", mw=268.4, nco=0, oh=2),
    ]
    source = pd.DataFrame(rows)
    library, audit = candidates.build_component_library(
        source,
        {"diisocyanate": 2, "chain_extender_diol": 2, "macrodiol": 2},
    )
    assert len(library) == 6
    assert audit["passed_prototype_gate"].tolist() == [2, 2, 2]
    combinations = candidates.build_component_combinations(library, 1, 1)
    assert len(combinations) == 2
    assert combinations["combination_id"].is_unique


def test_formulation_is_closed_to_targets_and_rejects_impossible_grid():
    extender_moles = candidates.solve_chain_extender_moles(1000.0, 168.2, 62.1, 0.35, 1.02)
    assert extender_moles > 0
    with pytest.raises(ValueError, match="不为正"):
        candidates.solve_chain_extender_moles(250.0, 450.0, 62.1, 0.20, 1.02)
    combinations = pd.DataFrame([
        {
            "combination_id": "combo-x",
            "diisocyanate_id": "di-x", "diisocyanate_smiles": "O=C=NCCCCCCN=C=O", "diisocyanate_mw_g_mol": 168.2,
            "macrodiol_proxy_id": "macro-x", "macrodiol_proxy_smiles": "OCCCCCCCCCCCCCCCCO", "macrodiol_proxy_monomer_mw_g_mol": 258.4,
            "chain_extender_id": "ce-x", "chain_extender_smiles": "OCCO", "chain_extender_mw_g_mol": 62.1,
            "citation_keys": "ledger-test",
        }
    ])
    formulations = candidates.build_formulations(combinations, {
        "macrodiol_nominal_mn_g_mol": [1000.0],
        "hard_segment_mass_fraction_target": [0.35],
        "nco_oh_ratio_target": [1.02],
    })
    assert len(formulations) == 1
    row = formulations.iloc[0]
    assert row["stoichiometry_residual"] < 1e-12
    assert row["nco_oh_ratio_calculated"] == pytest.approx(1.02)
    assert row["performance_prediction_status"] == "not_scored_by_baseline"


def test_candidate_release_exists_and_is_hash_verifiable_after_generation():
    manifest_path = ROOT / "候选" / "候选发布清单.json"
    if not manifest_path.exists():
        pytest.skip("候选发布尚未生成")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["counts"] == {
        "component_rows": 136,
        "combination_rows": 1152,
        "formulation_rows": 9216,
    }
    formulations = pd.read_csv(ROOT / manifest["outputs"]["formulations"]["path"])
    assert formulations["formulation_id"].is_unique
    assert formulations["stoichiometry_residual"].max() < 1e-10
    components = pd.read_csv(ROOT / manifest["outputs"]["component_library"]["path"])
    assert not components["has_isotope_label"].any()
    assert components["non_isocyanate_carbonyl_count"].eq(0).all()
    assert components.loc[
        components["component_class"].eq("diisocyanate"),
        "isocyanate_attachment_count",
    ].eq(2).all()
