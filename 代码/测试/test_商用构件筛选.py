from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "代码"))

import 商用构件筛选 as screening


def _record(**overrides):
    row = {
        "stable_component_id": "commercial_bdo_14",
        "preferred_name": "1,4-BDO",
        "synonym": "Butane-1,4-diol",
        "role": "chain_extender",
        "identity_kind": "discrete_substance",
        "canonical_smiles": "OCCCCO",
        "repeat_unit": "",
        "nominal_mn_g_mol": pd.NA,
        "cas_or_product_grade": "110-63-4",
        "supplier_or_manufacturer": "supplier",
        "evidence_url": "https://example.org/product",
        "accessed_date": "2026-08-24",
        "synthesis_feasibility_status": "established_or_standard_route",
        "commercial_evidence_status": "catalog_or_manufacturer_evidence",
        "ehs_review_status": "requires_human_sds_review",
        "source_scope": "added_commercial_control",
        "priority_class": "control_chain_extender",
        "notes": "fixture",
    }
    row.update(overrides)
    return row


def _config():
    return {
        "allowed_identity_kind": {
            "diisocyanate": ["discrete_substance"],
            "macrodiol": ["commercial_polyol_grade"],
            "chain_extender": ["discrete_substance"],
        },
        "required_status": {
            "synthesis_feasibility_status": "established_or_standard_route",
            "commercial_evidence_status": "catalog_or_manufacturer_evidence",
            "ehs_review_status": "requires_human_sds_review",
        },
        "formulation_grid": {
            "hard_segment_mass_fraction_target": [0.35, 0.45],
            "nco_oh_ratio_target": [1.00, 1.02],
        },
        "release_policy": {
            "component_gate_pass": "passed_for_planning",
            "experiment_release_status": "blocked_pending_quote_sds_and_local_approval",
        },
    }


def test_model_only_component_cannot_enter_experimental_pool():
    frame = pd.DataFrame([_record(source_scope="current_stage82", commercial_evidence_status="not_verified")])
    result = screening.apply_component_gate(frame, _config())
    assert result.loc[0, "experimental_gate_status"] == "blocked"
    assert "commercial_evidence_status" in result.loc[0, "experimental_gate_reason"]


def test_catalog_evidence_and_role_identity_are_required():
    frame = pd.DataFrame([_record(commercial_evidence_status="not_checked")])
    result = screening.apply_component_gate(frame, _config())
    assert result.loc[0, "experimental_gate_status"] == "blocked"


def test_macrodiol_proxy_is_not_promoted_to_real_macrodiol():
    frame = pd.DataFrame([
        _record(
            role="macrodiol",
            identity_kind="structure_proxy_only",
            preferred_name="proxy",
            stable_component_id="proxy_1",
            nominal_mn_g_mol=2000,
            repeat_unit="proxy-only",
        )
    ])
    result = screening.apply_component_gate(frame, _config())
    assert result.loc[0, "experimental_gate_status"] == "blocked"
    assert "identity_kind" in result.loc[0, "experimental_gate_reason"]


def test_valid_evidence_passes_for_planning_but_not_experiment_release():
    result = screening.apply_component_gate(pd.DataFrame([_record()]), _config())
    assert result.loc[0, "experimental_gate_status"] == "passed_for_planning"


def test_combination_requires_three_passed_roles():
    pool = screening.apply_component_gate(pd.DataFrame([_record()]), _config())
    with pytest.raises(ValueError, match="缺少已通过构件角色"):
        screening.build_experimental_combinations(pool, _config())


def test_no_blocked_component_is_emitted_and_grid_is_deterministic():
    rows = [
        _record(
            stable_component_id="dii",
            preferred_name="IPDI",
            role="diisocyanate",
            priority_class="control_light_stable",
        ),
        _record(
            stable_component_id="polyol",
            preferred_name="PTMG-1000",
            role="macrodiol",
            identity_kind="commercial_polyol_grade",
            canonical_smiles="",
            repeat_unit="HO-[(CH2)4-O]n-H",
            nominal_mn_g_mol=1000,
            priority_class="control_soft_segment",
        ),
        _record(),
        _record(
            stable_component_id="blocked",
            preferred_name="blocked",
            commercial_evidence_status="not_verified",
        ),
    ]
    pool = screening.apply_component_gate(pd.DataFrame(rows), _config())
    combinations = screening.build_experimental_combinations(pool, _config())
    assert len(combinations) == 4
    assert not combinations["component_ids"].str.contains("blocked").any()
    assert combinations["formulation_id"].is_unique
    assert combinations["combination_id"].nunique() == 1
    assert set(combinations["hard_segment_mass_fraction_target"]) == {0.35, 0.45}
    assert set(combinations["nco_oh_ratio_target"]) == {1.0, 1.02}


def test_ptmg_is_kept_as_product_grade_not_single_smiles():
    rows = [
        _record(stable_component_id="dii", role="diisocyanate"),
        _record(
            stable_component_id="ptmg",
            role="macrodiol",
            identity_kind="commercial_polyol_grade",
            canonical_smiles="",
            repeat_unit="HO-[(CH2)4-O]n-H",
            nominal_mn_g_mol=1000,
        ),
        _record(),
    ]
    pool = screening.apply_component_gate(pd.DataFrame(rows), _config())
    combinations = screening.build_experimental_combinations(pool, _config())
    assert combinations.loc[0, "macrodiol_identity_kind"] == "commercial_polyol_grade"
    assert combinations.loc[0, "macrodiol_smiles"] == ""


def test_pubchem_vendor_parser_deduplicates_names():
    payload = {
        "SourceCategories": {
            "Categories": [
                {
                    "Category": "Chemical Vendors",
                    "Sources": [
                        {"SourceName": "Vendor A"},
                        {"SourceName": "Vendor A"},
                        {"SourceName": "Vendor B"},
                    ],
                }
            ]
        }
    }
    count, names = screening.parse_pubchem_vendors(payload)
    assert count == 3
    assert names == ["Vendor A", "Vendor B"]


def test_load_config_and_validation_error_paths(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("allowed_identity_kind: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="缺少分区"):
        screening.load_config(config_path)
    valid_path = ROOT / "配置" / "实验候选硬门.yaml"
    assert screening.load_config(valid_path)["schema_version"] == "1.0"

    with pytest.raises(ValueError, match="缺少字段"):
        screening.validate_evidence(pd.DataFrame({"x": [1]}))
    duplicate = pd.DataFrame([_record(), _record()])
    with pytest.raises(ValueError, match="唯一"):
        screening.validate_evidence(duplicate)
    blank_smiles = pd.DataFrame([_record(canonical_smiles="")])
    with pytest.raises(ValueError, match="必须提供SMILES"):
        screening.validate_evidence(blank_smiles)
    invalid_smiles = pd.DataFrame([_record(canonical_smiles="not a smiles")])
    with pytest.raises(ValueError, match="无法解析"):
        screening.validate_evidence(invalid_smiles)
    bad_macro = pd.DataFrame([
        _record(
            role="macrodiol",
            identity_kind="commercial_polyol_grade",
            canonical_smiles="",
            repeat_unit="",
            nominal_mn_g_mol=-1,
        )
    ])
    with pytest.raises(ValueError, match="正Mn"):
        screening.validate_evidence(bad_macro)


def test_gate_reports_missing_identity_fields():
    frame = pd.DataFrame([_record(preferred_name="", evidence_url="")])
    result = screening.apply_component_gate(frame, _config())
    assert result.loc[0, "experimental_gate_status"] == "blocked"
    assert "preferred_name" in result.loc[0, "experimental_gate_reason"]
    assert "evidence_url" in result.loc[0, "experimental_gate_reason"]


def test_invalid_grid_and_duplicate_output_are_rejected(monkeypatch: pytest.MonkeyPatch):
    rows = [
        _record(stable_component_id="dii", role="diisocyanate"),
        _record(
            stable_component_id="ptmg",
            role="macrodiol",
            identity_kind="commercial_polyol_grade",
            canonical_smiles="",
            repeat_unit="repeat",
            nominal_mn_g_mol=1000,
        ),
        _record(),
    ]
    pool = screening.apply_component_gate(pd.DataFrame(rows), _config())
    bad = _config()
    bad["formulation_grid"]["hard_segment_mass_fraction_target"] = []
    with pytest.raises(ValueError, match="网格"):
        screening.build_experimental_combinations(pool, bad)
    monkeypatch.setattr(screening, "_stable_id", lambda *parts: "same")
    with pytest.raises(RuntimeError, match="不唯一"):
        screening.build_experimental_combinations(pool, _config())


def test_pubchem_parser_without_vendor_category():
    assert screening.parse_pubchem_vendors({"SourceCategories": {"Categories": []}}) == (0, [])


def test_pubchem_query_success_not_found_and_errors(monkeypatch: pytest.MonkeyPatch):
    payloads = iter(
        [
            {"IdentifierList": {"CID": [8064]}},
            {
                "SourceCategories": {
                    "Categories": [
                        {"Category": "Chemical Vendors", "Sources": [{"SourceName": "Supplier"}]}
                    ]
                }
            },
        ]
    )
    monkeypatch.setattr(screening, "_read_json", lambda url, timeout: next(payloads))
    result = screening.query_pubchem_availability("OCCCCO")
    assert result["query_status"] == "completed"
    assert result["pubchem_cid"] == 8064
    assert result["catalog_prefilter_status"] == "catalog_index_hit"

    monkeypatch.setattr(screening, "_read_json", lambda url, timeout: {"IdentifierList": {"CID": []}})
    assert screening.query_pubchem_availability("OCCCCO")["query_status"] == "pubchem_not_found"
    assert screening.query_pubchem_availability("not smiles")["query_status"] == "invalid_smiles"

    def http_error(url, timeout):
        raise urllib.error.HTTPError(url, 500, "bad", None, None)

    monkeypatch.setattr(screening, "_read_json", http_error)
    assert screening.query_pubchem_availability("OCCCCO")["query_status"] == "http_error_500"

    def url_error(url, timeout):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(screening, "_read_json", url_error)
    assert screening.query_pubchem_availability("OCCCCO")["query_status"].startswith("query_error_")


def test_build_current82_audit_and_query_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    stage_path = tmp_path / "stage.csv"
    task_path = tmp_path / "tasks.csv"
    query_path = tmp_path / "query.csv"
    pd.DataFrame(
        [
            {"candidate_id": "a", "component_role": "chain_extender", "ensemble_status": "complete"},
            {"candidate_id": "b", "component_role": "macrodiol_proxy", "ensemble_status": "complete"},
        ]
    ).to_csv(stage_path, index=False)
    pd.DataFrame(
        [
            {"candidate_id": "a", "component_role": "chain_extender", "task_index": 1, "canonical_smiles": "OCCCCO", "geometry_status": "ready"},
            {"candidate_id": "b", "component_role": "macrodiol_proxy", "task_index": 2, "canonical_smiles": "OCCCCCCO", "geometry_status": "ready"},
        ]
    ).to_csv(task_path, index=False)
    audit = screening.build_current82_audit(stage_path, task_path)
    assert len(audit) == 2
    assert audit.loc[audit.candidate_id.eq("b"), "experimental_gate_reason"].iloc[0].startswith("structure_proxy")

    monkeypatch.setattr(
        screening,
        "query_pubchem_availability",
        lambda smiles: {
            "query_status": "completed",
            "inchi_key": screening.Chem.MolToInchiKey(screening.Chem.MolFromSmiles(smiles)),
            "pubchem_cid": 1,
            "pubchem_vendor_record_count": 2,
            "pubchem_distinct_vendor_count": 1,
            "pubchem_vendor_names": "Supplier",
            "catalog_prefilter_status": "catalog_index_hit",
        },
    )
    monkeypatch.setattr(screening.time, "sleep", lambda seconds: None)
    queried = screening.query_current82(audit, query_path, delay_seconds=0)
    assert len(queried) == 2 and query_path.is_file()
    merged = screening.build_current82_audit(stage_path, task_path, query_path)
    assert merged["pubchem_cid"].notna().all()
    # 第二次读取completed缓存，不应调用网络适配器。
    monkeypatch.setattr(screening, "query_pubchem_availability", lambda smiles: pytest.fail("cache miss"))
    assert len(screening.query_current82(audit, query_path, delay_seconds=0)) == 2


def test_write_outputs_and_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    evidence = ROOT / "候选" / "商用构件证据.csv"
    config = ROOT / "配置" / "实验候选硬门.yaml"
    stage = ROOT / "tmp" / "xTB构件级系综描述符_stage82.csv"
    tasks = ROOT / "计算" / "DFT任务清单.csv"
    query = ROOT / "候选" / "当前82构件采购查询.csv"
    pool = tmp_path / "pool.csv"
    audit = tmp_path / "audit.csv"
    combinations = tmp_path / "combinations.csv"
    counts = screening.write_outputs(
        evidence, config, stage, tasks, query, pool, audit, combinations, run_query=False
    )
    assert counts == {
        "commercial_components": 7,
        "commercial_components_passed": 7,
        "current82_audited": 82,
        "current82_experiment_passed": 0,
        "experimental_formulations": 32,
        "base_systems": 8,
    }
    assert pool.is_file() and audit.is_file() and combinations.is_file()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "商用构件筛选.py",
            "--证据", str(evidence),
            "--配置", str(config),
            "--阶段构件", str(stage),
            "--任务清单", str(tasks),
            "--查询输出", str(query),
            "--构件输出", str(tmp_path / "main_pool.csv"),
            "--审计输出", str(tmp_path / "main_audit.csv"),
            "--组合输出", str(tmp_path / "main_combinations.csv"),
        ],
    )
    assert screening.main() == 0
    assert '"base_systems": 8' in capsys.readouterr().out
