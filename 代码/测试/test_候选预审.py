import json
from pathlib import Path

import pandas as pd
import pytest

import 候选预审 as precheck
import 生成候选预审 as orchestrator


ROOT = Path(__file__).resolve().parents[2]


def test_config_keeps_external_claims_manual():
    config = orchestrator.load_config(ROOT / "配置" / "候选预审.yaml")
    assert config["dft_queue"]["queue_size"] == 48
    assert config["manual_review"]["procurement_status"] == "not_checked"
    assert config["manual_review"]["literature_novelty_status"] == "not_checked"


def test_structure_alerts_are_review_flags_not_hazard_classes():
    alerts = precheck.structure_alerts("O=C=Nc1ccccc1N=C=O", "diisocyanate")
    assert "isocyanate_group_requires_SDS_review" in alerts
    assert "aromatic_structure_requires_exposure_review" in alerts
    assert all("hazard_class" not in alert for alert in alerts)
    assert precheck.structure_alerts("OCCO", "chain_extender_diol") == []
    assert precheck.structure_alerts("OCC(F)CO", "macrodiol") == [
        "halogenated_structure_requires_environmental_review"
    ]
    with pytest.raises(ValueError, match="不能为空"):
        precheck.structure_alerts("", "macrodiol")
    with pytest.raises(ValueError, match="无法解析"):
        precheck.structure_alerts("not-a-smiles", "macrodiol")


def _fixture_frames():
    components = pd.DataFrame(
        [
            {"candidate_id": "di-a", "component_class": "diisocyanate", "canonical_smiles": "O=C=NCCCCCCN=C=O"},
            {"candidate_id": "di-b", "component_class": "diisocyanate", "canonical_smiles": "O=C=Nc1ccc(CC)cc1N=C=O"},
            {"candidate_id": "macro-a", "component_class": "macrodiol", "canonical_smiles": "OCCCCCCCCCCCCCCCCO"},
            {"candidate_id": "macro-b", "component_class": "macrodiol", "canonical_smiles": "Oc1ccc(C2(c3ccc(O)cc3)CCCCC2)cc1"},
            {"candidate_id": "ce-a", "component_class": "chain_extender_diol", "canonical_smiles": "OCCO"},
            {"candidate_id": "ce-b", "component_class": "chain_extender_diol", "canonical_smiles": "OCCCCO"},
        ]
    )
    rows = []
    for index, ids in enumerate((("di-a", "macro-a", "ce-a"), ("di-b", "macro-b", "ce-b"))):
        di, macro, ce = ids
        rows.append(
            {
                "formulation_id": f"form-{index}",
                "combination_id": f"combo-{index}",
                "diisocyanate_id": di,
                "diisocyanate_smiles": components.set_index("candidate_id").loc[di, "canonical_smiles"],
                "macrodiol_proxy_id": macro,
                "macrodiol_proxy_smiles": components.set_index("candidate_id").loc[macro, "canonical_smiles"],
                "chain_extender_id": ce,
                "chain_extender_smiles": components.set_index("candidate_id").loc[ce, "canonical_smiles"],
                "macrodiol_nominal_mn_g_mol": 2000.0,
                "hard_segment_mass_fraction_target": 0.45,
                "nco_oh_ratio_target": 1.02,
                "citation_keys": "ledger-test",
            }
        )
    return pd.DataFrame(rows), components


def test_annotation_keeps_manual_states_and_no_performance_claim():
    formulations, components = _fixture_frames()
    config = orchestrator.load_config(ROOT / "配置" / "候选预审.yaml")
    annotated = precheck.annotate_formulations(formulations, components, config["manual_review"])
    assert annotated["formulation_id"].is_unique
    assert annotated["procurement_status"].eq("not_checked").all()
    assert annotated["literature_novelty_status"].eq("not_checked").all()
    assert annotated["experimental_eligibility"].eq("blocked_pending_manual_review").all()
    assert annotated["performance_claim_status"].eq("no_performance_claim").all()


def test_dft_queue_is_unique_and_holds_md():
    formulations, components = _fixture_frames()
    config = orchestrator.load_config(ROOT / "配置" / "候选预审.yaml")
    annotated = precheck.annotate_formulations(formulations, components, config["manual_review"])
    dft_config = dict(config["dft_queue"])
    dft_config["queue_size"] = 2
    queue = precheck.select_dft_queue(annotated, dft_config)
    assert queue["formulation_id"].is_unique
    assert queue["dft_stage"].eq("tier1_monomer_reactivity_and_conformer_screen").all()
    assert queue["md_stage"].eq("on_hold_pending_real_macrodiol_identity_Mn_Mw_PDI").all()
    assert queue["performance_claim_status"].eq("no_performance_claim").all()


def test_release_has_expected_rows_and_hashes():
    manifest_path = ROOT / "候选" / "候选预审发布清单.json"
    if not manifest_path.exists():
        pytest.skip("候选预审发布尚未生成")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["counts"] == {"precheck_rows": 9216, "dft_queue_rows": 48}
    orchestrator.verify(ROOT / "候选")
