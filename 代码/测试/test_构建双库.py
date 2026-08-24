from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "代码"))

import 构建双库 as dual


def _reality_components() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "stable_component_id": "dii",
                "preferred_name": "IPDI",
                "synonym": "IPDI",
                "role": "diisocyanate",
                "identity_kind": "discrete_substance",
                "canonical_smiles": "CC1(C)CC(CC(C)(CN=C=O)C1)N=C=O",
                "repeat_unit": "",
                "nominal_mn_g_mol": pd.NA,
                "cas_or_product_grade": "4098-71-9",
                "supplier_or_manufacturer": "supplier",
                "evidence_url": "https://example.org/dii",
                "accessed_date": "2026-08-24",
                "commercial_evidence_status": "catalog_or_manufacturer_evidence",
                "experimental_gate_status": "passed_for_planning",
                "experiment_release_status": "blocked_pending_quote_sds_and_local_approval",
                "source_scope": "added_commercial_control",
                "ehs_review_status": "requires_human_sds_review",
                "priority_class": "control",
                "notes": "fixture",
            },
            {
                "stable_component_id": "ptmg",
                "preferred_name": "PTMG-1000",
                "synonym": "PTMG",
                "role": "macrodiol",
                "identity_kind": "commercial_polyol_grade",
                "canonical_smiles": "",
                "repeat_unit": "HO-[(CH2)4-O]n-H",
                "nominal_mn_g_mol": 1000,
                "cas_or_product_grade": "grade",
                "supplier_or_manufacturer": "supplier",
                "evidence_url": "https://example.org/ptmg",
                "accessed_date": "2026-08-24",
                "commercial_evidence_status": "catalog_or_manufacturer_evidence",
                "experimental_gate_status": "passed_for_planning",
                "experiment_release_status": "blocked_pending_quote_sds_and_local_approval",
                "source_scope": "added_commercial_control",
                "ehs_review_status": "requires_human_sds_review",
                "priority_class": "control",
                "notes": "fixture",
            },
            {
                "stable_component_id": "bdo",
                "preferred_name": "1,4-BDO",
                "synonym": "BDO",
                "role": "chain_extender",
                "identity_kind": "discrete_substance",
                "canonical_smiles": "OCCCCO",
                "repeat_unit": "",
                "nominal_mn_g_mol": pd.NA,
                "cas_or_product_grade": "110-63-4",
                "supplier_or_manufacturer": "supplier",
                "evidence_url": "https://example.org/bdo",
                "accessed_date": "2026-08-24",
                "commercial_evidence_status": "catalog_or_manufacturer_evidence",
                "experimental_gate_status": "passed_for_planning",
                "experiment_release_status": "blocked_pending_quote_sds_and_local_approval",
                "source_scope": "added_commercial_control",
                "ehs_review_status": "requires_human_sds_review",
                "priority_class": "control",
                "notes": "fixture",
            },
        ]
    )


def _formulation() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "formulation_id": "f1",
                "baseline_priority": 1,
                "planning_tier": "tier1_small_control_matrix",
                "diisocyanate_id": "dii",
                "macrodiol_id": "ptmg",
                "chain_extender_id": "bdo",
                "component_ids": "dii;ptmg;bdo",
                "macrodiol_nominal_mn_g_mol": 1000,
                "hard_segment_mass_fraction_target": 0.45,
                "nco_oh_ratio_target": 1.02,
            }
        ]
    )


def _ptmg_config():
    return {
        "end_group_mass_g_mol": 18.015,
        "repeat_mass_g_mol": 72.106,
        "repeat_smiles_fragment": "CCCCO",
        "approximation_status": "single_oligomer_proxy_for_product_distribution",
    }


def test_virtual_assets_are_indexed_not_copied(tmp_path: Path):
    source = tmp_path / "x.csv"
    pd.DataFrame({"x": [1, 2]}).to_csv(source, index=False)
    specs = [
        {
            "asset_id": "a",
            "path": "x.csv",
            "layer": "Gold-C",
            "row_count_source": "csv",
            "allowed_use": "ML",
        }
    ]
    result = dual.build_virtual_asset_index(specs, {"counts": {}}, root=tmp_path)
    assert result.loc[0, "storage_policy"] == "reference_existing_file"
    assert result.loc[0, "row_count"] == 2


def test_virtual_manifest_count_and_errors(tmp_path: Path):
    source = tmp_path / "x.csv"
    source.write_text("x\n1\n", encoding="utf-8")
    spec = {
        "asset_id": "a",
        "path": "x.csv",
        "layer": "Gold-C",
        "row_count_source": "release_manifest:rows",
        "allowed_use": "ML",
    }
    assert dual.build_virtual_asset_index([spec], {"counts": {"rows": 9}}, root=tmp_path).loc[0, "row_count"] == 9
    with pytest.raises(ValueError, match="缺少计数"):
        dual.build_virtual_asset_index([spec], {"counts": {}}, root=tmp_path)
    bad = dict(spec, row_count_source="bad")
    with pytest.raises(ValueError, match="未知"):
        dual.build_virtual_asset_index([bad], {"counts": {}}, root=tmp_path)
    with pytest.raises(FileNotFoundError):
        dual.build_virtual_asset_index([dict(spec, path="missing.csv")], {"counts": {"rows": 1}}, root=tmp_path)


def test_reality_library_requires_direct_evidence_and_no_virtual_source():
    reality = dual.build_reality_components(_reality_components())
    assert reality["direct_commercial_evidence_status"].eq("verified_dated_page").all()
    assert set(reality["component_id"]) == {"dii", "ptmg", "bdo"}
    bad = _reality_components()
    bad.loc[0, "source_scope"] = "current_stage82"
    with pytest.raises(ValueError, match="虚拟来源"):
        dual.build_reality_components(bad)
    bad = _reality_components()
    bad.loc[0, "commercial_evidence_status"] = "not_verified"
    with pytest.raises(ValueError, match="商业证据"):
        dual.build_reality_components(bad)


def test_reality_formulations_cannot_use_virtual_components():
    components = dual.build_reality_components(_reality_components())
    result = dual.build_reality_formulations(_formulation(), components)
    assert result.loc[0, "library"] == "reality"
    leaked = _formulation()
    leaked.loc[0, "diisocyanate_id"] = "virtual_dii"
    with pytest.raises(ValueError, match="非现实构件"):
        dual.build_reality_formulations(leaked, components)


def test_ptmg_representatives_are_explicit_approximations():
    model1000 = dual.build_ptmg_representative("ptmg1000", 1000, _ptmg_config())
    model2000 = dual.build_ptmg_representative("ptmg2000", 2000, _ptmg_config())
    assert model1000["repeat_count"] == 14
    assert model2000["repeat_count"] == 27
    assert model1000["approximation_status"] == "single_oligomer_proxy_for_product_distribution"
    assert model1000["distribution_claim_status"] == "no_distribution_claim"
    with pytest.raises(ValueError, match="不合法"):
        dual.build_ptmg_representative("bad", 10, _ptmg_config())


def test_tasks_and_stage_gates_are_fail_closed():
    components = dual.build_reality_components(_reality_components())
    formulations = dual.build_reality_formulations(_formulation(), components)
    ptmg = dual.build_ptmg_models(components, _ptmg_config())
    tasks = dual.build_calculation_tasks(components, formulations, ptmg)
    assert tasks["task_id"].is_unique
    assert tasks["task_kind"].value_counts().to_dict() == {
        "component_crest_xtb": 2,
        "macrodiol_representative_crest_xtb": 1,
        "formulation_dft": 1,
        "bulk_md": 1,
    }
    assert tasks.loc[tasks.task_kind.eq("bulk_md"), "calculation_status"].eq("blocked").all()
    queue = dual.build_screening_queue(formulations)
    assert queue["ml_status"].eq("blocked_pending_formulation_representation").all()
    assert queue["md_status"].eq("blocked_pending_reacted_chain_and_force_field").all()


def test_real_repository_build_and_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    config = ROOT / "配置" / "双库筛选.yaml"
    manifest = dual.write_dual_library(config, root=ROOT)
    assert manifest["status"] == "completed"
    assert manifest["virtual_to_reality_component_leakage_count"] == 0
    assert manifest["counts"]["reality_components"] == 19
    assert manifest["counts"]["reality_formulations"] == 980
    assert manifest["counts"]["calculation_tasks"] == 27
    assert manifest["counts"]["pending_macrodiols"] == 3
    monkeypatch.setattr(sys, "argv", ["构建双库.py", "--配置", str(config)])
    assert dual.main() == 0
    assert "reality_components" in capsys.readouterr().out


def test_load_config_and_hash(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("release_id: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="缺少分区"):
        dual.load_config(bad)
    path = tmp_path / "x.txt"
    path.write_text("abc", encoding="utf-8")
    assert dual.sha256_file(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
