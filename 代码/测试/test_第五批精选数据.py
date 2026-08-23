from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "数据/原始" / "外部数据" / "新增开放数据"
SCRIPT = ROOT / "代码" / "审计" / "第五批精选数据.py"
FISHER = RAW / "DataInBrief_聚氨酯形状记忆多模态原始数据"
BIOBASED = RAW / "Zenodo_木质素_TPU多模态数据"


def _load_auditor():
    spec = importlib.util.spec_from_file_location("fifth_batch_data_auditor", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _require_payloads() -> None:
    if not (FISHER / "mmc6.xlsx").is_file() or not (
        BIOBASED / "Biobased.xlsx"
    ).is_file():
        pytest.skip("第五批原始数据未在当前检出中分发")


def test_fisher_counts_runs_not_curve_points_as_independent_samples() -> None:
    _require_payloads()
    bundle = _load_auditor().audit_fisher()
    summary = bundle.summary

    assert summary["canonical_identifier"] == "doi:10.1016/j.dib.2020.106294"
    assert summary["scientific_role"] == "polyurethane_adjacent_transfer"
    assert summary["thermoplastic_tpu_core"] is False
    assert summary["formulation_count"] == 12
    assert summary["mechanical_run_count"] == 61
    assert summary["failure_tensile_run_count"] == 37
    assert summary["cyclic_tensile_run_count"] == 24
    assert summary["thermal_curve_count"] == 48
    assert summary["curve_container_count"] == len(bundle.curves) == 109
    assert summary["point_row_count"] == 975_903
    assert summary["mechanical_point_row_count"] == 951_098
    assert summary["thermal_point_row_count"] == 24_805
    assert summary["copied_tail_contamination_point_count"] == 1_702
    assert summary["high_confidence_usable_point_row_count"] == 974_201
    assert summary["formatted_blank_row_count"] == 266
    assert summary["missing_terminal_marker_run_count"] == 1
    assert summary["normalized_cross_workbook_label_count"] == 45
    assert summary["distinguishable_instance_count"] == 46
    assert summary["physical_specimen_count"] is None
    assert summary["analysis_script_publicly_present"] is False
    assert summary["training_split_materialized"] is False
    assert summary["training_weight_materialized"] is False

    mechanical = [row for row in bundle.curves if row["modality"] == "mechanical"]
    assert len(mechanical) == 61
    assert sum(int(row["point_count"]) for row in mechanical) == 951_098
    assert sum(int(row["usable_point_count"]) for row in mechanical) == 949_396
    assert {row["test_type"] for row in mechanical} == {
        "failure_tensile",
        "uniaxial_cyclic_tensile",
    }
    assert all(row["split_group"].endswith(row["formulation_id"]) for row in mechanical)
    assert all(float(row["future_weight_ceiling"]) <= 0.35 for row in bundle.curves)
    assert all(row["training_split"] == "false" for row in bundle.curves)
    contaminated = [row for row in mechanical if int(row["contamination_point_count"]) > 0]
    assert len(contaminated) == 2
    assert sorted(int(row["contamination_point_count"]) for row in contaminated) == [696, 1_006]
    assert all(row["quality_status"] == "tail_contamination_excluded" for row in contaminated)
    missing_terminal = [row for row in mechanical if row["quality_status"] == "missing_terminal_marker"]
    assert len(missing_terminal) == 1
    assert missing_terminal[0]["source_location"] == "SMP-8_2"


def test_biobased_preserves_duplicates_censoring_and_missing_labels() -> None:
    _require_payloads()
    bundle = _load_auditor().audit_biobased()
    summary = bundle.summary

    assert summary["canonical_identifier"] == "doi:10.5281/zenodo.3631551"
    assert summary["publication_identifier"] == "doi:10.1021/acssuschemeng.8b01170"
    assert summary["scientific_role"] == "tpu_composite_transfer"
    assert summary["thermoplastic_tpu_core"] is False
    assert summary["displayed_curve_count"] == len(bundle.curves) == 39
    assert summary["unique_curve_count"] == 37
    assert summary["duplicate_curve_count"] == 2
    assert summary["unique_point_row_count"] == 98_570
    assert summary["curve_counts_by_modality"] == {
        "DSC": 7,
        "FTIR": 12,
        "TGA": 6,
        "XRD": 4,
        "rheology": 8,
    }
    assert summary["precursor_fiber_mechanical_record_count"] == 8
    assert summary["downstream_carbon_fiber_record_count"] == 4
    assert summary["distinct_material_or_blend_identity_count"] == 19
    assert summary["commercial_tpu_grade"] == "Pearlthane ECO 12T95"
    assert summary["numeric_scalar_result_count"] == 38
    assert summary["right_censored_scalar_result_count"] == 2
    assert summary["formula_cell_count"] == 0
    assert summary["error_cell_count"] == 0
    assert summary["training_split_materialized"] is False
    assert summary["training_weight_materialized"] is False

    duplicates = [row for row in bundle.curves if row["dedup_status"] == "exact_duplicate"]
    assert len(duplicates) == 2
    assert {row["modality"] for row in duplicates} == {"FTIR", "DSC"}
    held = [row for row in bundle.curves if row["decision"] == "hold_missing_sample_label"]
    assert len(held) == 16
    assert {row["modality"] for row in held} == {"DSC", "rheology"}
    assert all(row["future_weight_ceiling"] == "0.00" for row in held)

    censored = [
        row
        for row in bundle.scalars
        if int(row["right_censored_result_count"]) > 0
    ]
    assert len(censored) == 2
    assert all(row["raw_censored_value"] == ">200" for row in censored)
    downstream = [row for row in bundle.scalars if row["task_role"] == "downstream_carbon_fiber"]
    assert len(downstream) == 4
    assert all(row["future_weight_ceiling"] == "0.00" for row in downstream)
    assert all(row["training_split"] == "false" for row in bundle.scalars)


def test_fifth_batch_audit_rendering_is_byte_deterministic() -> None:
    _require_payloads()
    module = _load_auditor()
    for audit in (module.audit_fisher, module.audit_biobased):
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
