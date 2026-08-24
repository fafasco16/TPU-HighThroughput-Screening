from pathlib import Path

import pandas as pd
import pytest

import 生成现实配方量化表 as reality


ROOT = Path(__file__).resolve().parents[2]


def _ensemble(candidate_id: str, role: str, value: float) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "component_role": role,
        "canonical_smiles": "O" if role != "diisocyanate" else "O=C=NCCN=C=O",
        "conformer_count_input": 2,
        "conformer_count_success": 2,
        "ensemble_status": "complete",
        "failure_count": 0,
        "complete_weighted_release": True,
        "effective_conformer_count": 1.5,
        "energy_span_kcal_mol": 1.0,
        "dominant_conformer_weight": 0.7,
        "homo_ev_weighted_mean": -6.0 + value,
        "lumo_ev_weighted_mean": -1.0 + value,
        "homo_lumo_gap_ev_weighted_mean": 5.0,
        "homo_lumo_gap_ev_weighted_sd": 0.1,
        "dipole_magnitude_debye_weighted_mean": 2.0,
        "gfn2_d4_alpha0_au_weighted_mean": 20.0,
        "site_charge_e_mean_weighted_mean": value,
        "site_charge_e_mean_weighted_sd": 0.01,
        "site_incident_wbo_sum_mean_weighted_mean": 1.2,
        "site_relative_sasa_mean_weighted_mean": 0.5,
        "site_relative_sasa_mean_weighted_sd": 0.02,
        "site_nonbonded_net_gap_a_mean_weighted_mean": 1.0,
        "reactive_site_distance_a_weighted_mean": 4.0,
    }


def _macro_ensemble(value: float = -0.5) -> dict[str, object]:
    row = _ensemble("macro-1", "macrodiol_proxy", value)
    row["conformer_count_input"] = 1
    row["conformer_count_success"] = 1
    return row


def _components() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "component_id": "di-1",
                "preferred_name": "DI",
                "role": "diisocyanate",
                "identity_kind": "exact_discrete_commercial_substance",
                "direct_commercial_evidence_status": "manufacturer_product_page",
                "ehs_review_status": "required",
                "experiment_use_status": "planning_only",
            },
            {
                "component_id": "macro-1",
                "preferred_name": "PTMG",
                "role": "macrodiol",
                "identity_kind": "commercial_polyol_grade",
                "direct_commercial_evidence_status": "manufacturer_product_page",
                "ehs_review_status": "required",
                "experiment_use_status": "planning_only",
            },
            {
                "component_id": "ce-1",
                "preferred_name": "CE",
                "role": "chain_extender",
                "identity_kind": "exact_discrete_commercial_substance",
                "direct_commercial_evidence_status": "manufacturer_product_page",
                "ehs_review_status": "required",
                "experiment_use_status": "planning_only",
            },
        ]
    )


def _formulations() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "formulation_id": "f-1",
                "base_system_id": "b-1",
                "combination_id": "b-1",
                "planning_tier": "tier1_small_control_matrix",
                "diisocyanate_id": "di-1",
                "macrodiol_id": "macro-1",
                "chain_extender_id": "ce-1",
                "macrodiol_nominal_mn_g_mol": 1000.0,
                "hard_segment_mass_fraction_target": 0.35,
                "nco_oh_ratio_target": 1.0,
                "procurement_review_status": "quote_required",
                "sds_review_status": "required",
                "experiment_release_status": "blocked_pending_approval",
                "performance_claim_status": "no_performance_claim",
            }
        ]
    )


def _domain() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "formulation_id": "f-1",
                "formulation_domain_floor": 0.45,
                "weakest_domain_role": "macrodiol",
                "formulation_applicability_status": "component_structures_within_or_near_domain",
                "ml_prediction_status": "blocked_pending_multicomponent_formulation_model",
            }
        ]
    )


def test_mixed_fidelity_components_join_by_three_reality_role_ids():
    discrete = pd.DataFrame(
        [_ensemble("di-1", "diisocyanate", 0.3), _ensemble("ce-1", "chain_extender", -0.4)]
    )
    macro = pd.DataFrame([_macro_ensemble()])
    components = reality.build_component_descriptor_table(
        discrete, macro, _components()
    )
    assert components["component_id"].is_unique
    assert components.set_index("component_id").loc["di-1", "descriptor_fidelity"] == "crest_ensemble"
    assert components.set_index("component_id").loc["macro-1", "descriptor_fidelity"] == "single_conformer_proxy"
    formulations = reality.build_formulation_descriptor_table(
        _formulations(), _domain(), components
    )
    assert len(formulations) == 1
    row = formulations.iloc[0]
    assert row["descriptor_join_status"] == "ready"
    assert row["screening_input_status"] == "ready_for_quantum_proxy_screen"
    assert row["diisocyanate__site_charge_e_mean_weighted_mean"] == 0.3
    assert row["macrodiol__site_charge_e_mean_weighted_mean"] == -0.5
    assert row["chain_extender__site_charge_e_mean_weighted_mean"] == -0.4
    assert row["macrodiol__descriptor_fidelity"] == "single_conformer_proxy"
    assert row["quantum_descriptor_scope"] == "mixed_crest_ensemble_and_single_oligomer_proxy"
    assert row["performance_claim_status"] == "no_performance_claim"


def test_failed_component_duplicate_identity_and_domain_mismatch_close_release():
    discrete = pd.DataFrame(
        [_ensemble("di-1", "diisocyanate", 0.3), _ensemble("ce-1", "chain_extender", -0.4)]
    )
    failed = pd.DataFrame([_macro_ensemble()])
    failed.loc[:, "complete_weighted_release"] = False
    with pytest.raises(ValueError, match="未通过完整发布门"):
        reality.build_component_descriptor_table(discrete, failed, _components())
    duplicated = pd.concat([discrete, discrete.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="candidate_id不唯一"):
        reality.build_component_descriptor_table(
            duplicated,
            pd.DataFrame([_macro_ensemble()]),
            _components(),
        )
    components = reality.build_component_descriptor_table(
        discrete,
        pd.DataFrame([_macro_ensemble()]),
        _components(),
    )
    with pytest.raises(ValueError, match="适用域与配方ID集合不一致"):
        reality.build_formulation_descriptor_table(
            _formulations(), _domain().assign(formulation_id="wrong"), components
        )


def test_gnn_out_of_domain_blocks_model_extrapolation_but_not_quantum_screening():
    discrete = pd.DataFrame(
        [_ensemble("di-1", "diisocyanate", 0.3), _ensemble("ce-1", "chain_extender", -0.4)]
    )
    components = reality.build_component_descriptor_table(
        discrete, pd.DataFrame([_macro_ensemble()]), _components()
    )
    domain = _domain().assign(
        formulation_domain_floor=0.2,
        formulation_applicability_status="blocked_component_outside_training_domain",
        ml_prediction_status="blocked_pending_multicomponent_formulation_model",
    )
    row = reality.build_formulation_descriptor_table(
        _formulations(), domain, components
    ).iloc[0]
    assert (
        row["screening_input_status"]
        == "ready_for_quantum_proxy_screen_outside_gnn_domain"
    )
    assert row["quantum_descriptor_screen_permission"] == "allowed_with_fidelity_labels"
    assert row["gnn_prediction_permission"] == "blocked_outside_training_structure_domain"
    assert row["validation_priority"] == "high_out_of_domain"


def test_writer_emits_hashed_980_row_release_when_real_inputs_are_supplied(
    tmp_path: Path,
):
    discrete = pd.DataFrame(
        [_ensemble("di-1", "diisocyanate", 0.3), _ensemble("ce-1", "chain_extender", -0.4)]
    )
    macro = pd.DataFrame([_macro_ensemble()])
    paths = {}
    for name, frame in {
        "discrete.csv": discrete,
        "macro.csv": macro,
        "components.csv": _components(),
        "formulations.csv": _formulations(),
        "domain.csv": _domain(),
    }.items():
        path = tmp_path / name
        frame.to_csv(path, index=False)
        paths[name] = path
    manifest = reality.write_release(
        paths["discrete.csv"],
        paths["macro.csv"],
        paths["components.csv"],
        paths["formulations.csv"],
        paths["domain.csv"],
        tmp_path / "out",
        release_id="test-reality-quantum-release",
    )
    assert manifest["counts"] == {"components": 3, "formulations": 1}
    assert manifest["status_counts"] == {"ready_for_quantum_proxy_screen": 1}
    assert set(manifest["files"]) == {
        "构件量化描述符.csv",
        "配方量化描述符.csv",
    }
    assert (tmp_path / "out" / "量化描述符发布清单.json").is_file()


def test_current_reality_release_has_19_components_and_980_fidelity_labelled_formulations():
    component_path = ROOT / "数据" / "现实库" / "构件量化描述符.csv"
    formulation_path = ROOT / "数据" / "现实库" / "配方量化描述符.csv"
    if not component_path.is_file() or not formulation_path.is_file():
        pytest.skip("现实量化发布尚未物化")
    components = pd.read_csv(component_path)
    formulations = pd.read_csv(formulation_path)
    assert len(components) == 19
    assert components["component_id"].is_unique
    assert components["descriptor_fidelity"].value_counts().to_dict() == {
        "crest_ensemble": 14,
        "single_conformer_proxy": 5,
    }
    assert len(formulations) == 980
    assert formulations["formulation_id"].is_unique
    assert formulations["descriptor_join_status"].eq("ready").all()
    assert formulations["quantum_descriptor_screen_permission"].eq(
        "allowed_with_fidelity_labels"
    ).all()
    assert formulations["gnn_prediction_permission"].eq(
        "blocked_outside_training_structure_domain"
    ).all()
