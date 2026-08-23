from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "代码" / "审计" / "既有力学数据精选审计.py"
RAW = ROOT / "数据" / "原始" / "外部数据" / "力学曲线"


def _load_auditor():
    spec = importlib.util.spec_from_file_location("selected_mechanics_auditor", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _require_payloads() -> None:
    required = (
        RAW / "SelfHealingTPU_4TU" / "source_data.zip",
        RAW / "Schwarz2022_EPU40" / "Raw_Data.xlsx",
        RAW / "Zenodo4156000" / "Dynamic_Tensile_0_70_Eel0.5.csv",
        RAW
        / "Zenodo1098206"
        / "Supronics_Porous-TPU-Nanocomposites Dataset.xlsx",
    )
    if not all(path.is_file() for path in required):
        pytest.skip("四个既有力学原始源未在当前检出中分发")


def test_4tu_counts_physical_specimens_and_dependent_views_conservatively() -> None:
    _require_payloads()
    bundle = _load_auditor().audit_self_healing()
    summary = bundle.summary

    assert summary["canonical_identifier"] == "doi:10.4121/13603775.v1"
    assert summary["gold_layer"] == "Gold-E_transfer_reference"
    assert summary["archive_member_count"] == 71
    assert summary["material_count"] == 2
    assert summary["formulation_count"] == 2
    assert summary["material_state_count"] == 8
    assert summary["confirmed_mechanical_specimen_key_count"] == 26
    assert summary["curve_count_observed"] == len(bundle.curves) == 68
    assert summary["curve_count_candidate"] == 61
    assert summary["point_count_observed"] == 148_379
    assert summary["point_count_candidate"] == 131_022
    assert summary["scalar_count_observed"] == 32
    assert summary["scalar_count_candidate"] == 32
    assert summary["derived_dependent_curve_count"] == 5
    assert summary["negative_viscosity_curve_hold_count"] == 2

    held_normalized = [
        row for row in bundle.curves if row["decision"] == "hold_dependent_view"
    ]
    assert len(held_normalized) == 5
    assert all(row["future_weight_ceiling"] == "0.00" for row in held_normalized)
    negative_viscosity = [
        row for row in bundle.curves if row["decision"] == "hold_negative_viscosity"
    ]
    assert len(negative_viscosity) == 2
    assert {row["instance_key"] for row in negative_viscosity} == {
        "SH-TPU_235C",
        "SH-TPU_240C",
    }


def test_schwarz_uses_45_real_curve_pairs_not_10_formatted_empty_pairs() -> None:
    _require_payloads()
    bundle = _load_auditor().audit_schwarz()
    summary = bundle.summary

    assert summary["canonical_identifier"] == "doi:10.17632/wcwtjrkfsm.1"
    assert summary["confirmed_tensile_specimen_count"] == 45
    assert summary["formatted_but_empty_curve_pair_count"] == 10
    assert summary["curve_count_observed"] == len(bundle.curves) == 45
    assert summary["curve_count_candidate"] == 45
    assert summary["point_count_observed"] == 73_500
    assert summary["point_count_candidate"] == 73_500
    assert summary["scalar_count_observed"] == 205
    assert summary["scalar_count_candidate"] == 205
    assert summary["exact_curve_duplicate_count"] == 0
    assert {row["instance_key"] for row in bundle.curves} >= {
        "Dry-Fresh-S1",
        "Dry-Aged-S1",
        "Wet-5952h-S5",
    }


def test_zenodo_4156_separates_protocol_channels_and_exact_file_duplicate() -> None:
    _require_payloads()
    bundle = _load_auditor().audit_zenodo_4156()
    summary = bundle.summary

    assert summary["file_count"] == 15
    assert summary["unique_file_payload_count"] == 14
    assert summary["run_payload_count"] == 14
    assert summary["material_count"] == 2
    assert summary["formulation_count"] == 2
    assert summary["material_condition_count"] == 3
    assert summary["confirmed_independent_specimen_count"] is None
    assert summary["curve_count_observed"] == len(bundle.curves) == 33
    assert summary["curve_count_candidate"] == 25
    assert summary["point_count_observed"] == 377_353
    assert summary["point_count_candidate"] == 152_271
    assert summary["protocol_coordinate_curve_count"] == 6
    assert summary["response_curve_duplicate_count"] == 2
    assert summary["partial_axis_or_response_row_count"] == 111

    protocol = [
        row for row in bundle.curves if row["decision"] == "hold_protocol_coordinate_only"
    ]
    duplicates = [
        row for row in bundle.curves if row["decision"] == "hold_exact_duplicate_file"
    ]
    assert len(protocol) == 6
    assert len(duplicates) == 2
    assert {row["test_type"] for row in duplicates} == {
        "stress_strain",
        "relative_resistance_strain",
    }
    assert all(row["future_weight_ceiling"] == "0.00" for row in protocol + duplicates)

    formulations = {row["formulation_id"]: row for row in bundle.formulations}
    assert set(formulations) == {"Eel_TPU_CB18", "Empa_SEBS_CB_1to1"}
    assert formulations["Eel_TPU_CB18"]["component_2_fraction"] == 18
    assert formulations["Empa_SEBS_CB_1to1"]["fraction_basis"] == "mass_ratio"
    empa = [row for row in bundle.curves if row["formulation_id"] == "Empa_SEBS_CB_1to1"]
    eel = [row for row in bundle.curves if row["formulation_id"] == "Eel_TPU_CB18"]
    assert empa
    assert eel
    assert {row["split_group"] for row in empa} == {
        "zenodo:4156000|Empa_SEBS_CB_1to1"
    }
    assert {row["material_scope"] for row in empa} == {"TPS_SEBS_CB_composite"}
    assert {row["material_scope"] for row in eel} == {"TPU_CB_composite"}
    assert {
        row["future_weight_ceiling"]
        for row in empa
        if row["decision"] == "low_weight_candidate"
    } == {"0.15"}
    assert {
        row["future_weight_ceiling"]
        for row in eel
        if row["decision"] == "low_weight_candidate"
    } == {"0.35"}


def test_zenodo_1098_preserves_replicates_and_formula_reference_defects() -> None:
    _require_payloads()
    bundle = _load_auditor().audit_zenodo_1098()
    summary = bundle.summary

    assert summary["publication_identifier"] == "doi:10.1038/s41598-017-17647-w"
    assert summary["commercial_tpu_grade"] == "IROGRAN PS 455-203"
    assert summary["formulation_count"] == 5
    assert summary["explicit_tensile_specimen_count"] == 25
    assert summary["curve_count_observed"] == len(bundle.curves) == 55
    assert summary["curve_count_candidate"] == 55
    assert summary["point_count_observed"] == 43_032
    assert summary["point_count_candidate"] == 43_032
    assert summary["scalar_count_observed"] == 63
    assert summary["scalar_count_candidate"] == 57
    assert summary["conductivity_formula_reference_error_count"] == 6
    assert summary["dependent_mean_and_standard_deviation_formula_count"] == 8

    tensile = [row for row in bundle.curves if row["test_type"] == "uniaxial_tensile_stress_strain"]
    assert len(tensile) == 25
    assert len({row["instance_key"] for row in tensile}) == 25
    derived = [row for row in bundle.scalars if row["task_role"] == "conductivity_formula_derived"]
    assert sum(int(row["derived_numeric_result_count"]) for row in derived) == 20
    assert sum(int(row["candidate_numeric_result_count"]) for row in derived) == 14
    tpu30 = next(row for row in derived if row["formulation_id"] == "TPU30")
    assert tpu30["decision"] == "hold_formula_reference_error"
    assert tpu30["future_weight_ceiling"] == "0.00"

    compositions = {row["formulation_id"]: row for row in bundle.formulations}
    assert compositions["TPU40"]["component_2_fraction"] == 40
    assert compositions["TPU40"]["fraction_basis"] == "mass_percent"


def test_selected_mechanics_rendering_is_deterministic_and_audit_only() -> None:
    _require_payloads()
    module = _load_auditor()
    audits = (
        module.audit_self_healing,
        module.audit_schwarz,
        module.audit_zenodo_4156,
        module.audit_zenodo_1098,
    )
    for audit in audits:
        first = module.render_outputs(audit())
        second = module.render_outputs(audit())
        assert first == second
        assert set(first) == {
            "内容审计摘要.json",
            "文件校验清单.tsv",
            "曲线审计清单.tsv",
            "标量审计清单.tsv",
            "配方审计清单.tsv",
        }
        summary = json.loads(first["内容审计摘要.json"].decode("utf-8"))
        assert summary["training_split_materialized"] is False
        assert summary["training_weight_materialized"] is False


def test_selected_mechanics_atomic_write_is_allowlisted_and_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_auditor()
    target = tmp_path / "内容审计摘要.json"
    target.write_bytes(b"previous")
    monkeypatch.setattr(module, "OUTPUT_WHITELIST", frozenset({target}))

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        module.atomic_write(target, b"new")
    assert target.read_bytes() == b"previous"
    assert not list(tmp_path.glob("*.audit.tmp"))

    with pytest.raises(module.AuditBlocked, match="白名单"):
        module.atomic_write(tmp_path / "outside.json", b"blocked")
