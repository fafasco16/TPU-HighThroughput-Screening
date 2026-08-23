"""第八批 PU 铜调控热解多尺度数据的定向回归测试。"""

from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "审计/第八批PU铜调控热解多尺度.py"
SPEC = importlib.util.spec_from_file_location("batch8_pu_copper_pyrolysis", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


@pytest.fixture(scope="module")
def source_xlsx() -> Path:
    candidates = (
        audit.SOURCE_XLSX,
        audit.PROJECT_ROOT / "数据/临时/第八批候选/Zenodo18414263_Data_Source.xlsx",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    pytest.skip("Zenodo 18414263 原始工作簿不在当前检出中")


@pytest.fixture(scope="module")
def result(source_xlsx: Path) -> dict[str, object]:
    return audit.run_audit(source_xlsx=source_xlsx, write_outputs=False)


def test_frozen_source_identity_and_output_contract() -> None:
    assert audit.DOI == "10.5281/zenodo.18414263"
    assert audit.LICENSE == "CC BY 4.0"
    assert audit.EXPECTED_BYTES == 3_140_743
    assert audit.EXPECTED_MD5 == "b9bf18264a6338e9ef1d7ffe23ef9b91"
    assert audit.EXPECTED_SHA256 == (
        "7f63eba865fced2234dd073fad230936bb5159cd8e24bcf7ed8a63a700a8022f"
    )
    assert audit.OUTPUT_NAMES == (
        "内容审计摘要.json",
        "实验曲线审计清单.tsv",
        "计算观测清单.tsv",
        "文件校验清单.tsv",
    )


def test_workbook_counts_are_frozen_without_turning_points_into_samples(
    result: dict[str, object],
) -> None:
    counts = result["summary"]["counts"]
    assert counts["sheet_count"] == 9
    assert counts["nonempty_cell_count"] == 264_423
    assert counts["native_numeric_cell_count"] == 263_503
    assert counts["string_cell_count"] == 920
    assert counts["numeric_text_cell_count"] == 610
    assert counts["formula_cell_count"] == 0
    assert counts["error_cell_count"] == 0
    assert counts["base_material_count"] == 1
    assert counts["independent_formulation_count"] == 0
    assert counts["independent_specimens"] == 0


def test_experimental_curves_are_channel_level_records(
    result: dict[str, object],
) -> None:
    curves = result["experimental_curves"]
    counts = result["summary"]["counts"]
    assert len(curves) == 14
    assert counts["experimental_curve_records"] == 14
    assert counts["experimental_curve_independent_conditions"] == 8
    assert counts["experimental_response_points_observed"] == 140_680
    assert counts["experimental_response_points_paired"] == 140_675
    assert sum(int(row["observed_response_points"]) for row in curves) == 140_680
    assert sum(int(row["paired_response_points"]) for row in curves) == 140_675
    assert len({row["independent_condition_id"] for row in curves}) == 8
    assert Counter(row["response_type"] for row in curves) == {
        "TG": 6,
        "DTG": 6,
        "FTIR_intensity": 2,
    }
    assert all(row["record_granularity"] == "response_curve" for row in curves)
    assert all(row["independent_specimen_count"] == 0 for row in curves)
    twenty = [row for row in curves if row["heating_rate_c_per_min"] == 20]
    assert len(twenty) == 2
    assert all(row["admission_status"] == "conditional_reference" for row in twenty)
    assert all(float(row["future_weight_ceiling"]) == 0.10 for row in twenty)


def test_scalar_and_duplicate_accounting_is_explicit(
    result: dict[str, object],
) -> None:
    counts = result["summary"]["counts"]
    assert counts["activation_energy_target_scalars"] == 48
    assert counts["talpha_unique_target_scalars"] == 45
    assert counts["thermodynamic_target_scalars"] == 24
    assert counts["activation_candidate_scalar_count"] == 117
    assert counts["activation_quality_r2_count"] == 40
    assert counts["activation_derived_sum_count"] == 8
    assert counts["eds_scalar_observed"] == 24
    assert counts["eds_scalar_candidate"] == 16
    assert counts["py_gc_ms_scalar_observed"] == 270
    assert counts["py_gc_ms_scalar_candidate"] == 186
    assert counts["figure3_duplicate_display_scalars"] == 42
    assert counts["figure3_derived_delta_scalars"] == 42


def test_computational_observations_are_grouped_by_physical_system(
    result: dict[str, object],
) -> None:
    rows = result["computational_observations"]
    counts = result["summary"]["counts"]
    assert len(rows) == 14
    assert counts["computational_observation_records"] == 14
    assert counts["computational_system_count"] == 6
    assert counts["dft_topology_family_count"] == 2
    assert counts["dft_path_id_count_workbook"] == 43
    assert counts["dft_path_id_count_article"] == 45
    assert counts["dft_pathway_family_count"] == 6
    assert counts["dft_energy_curve_count"] == 6
    assert counts["dft_energy_point_count"] == 78
    assert counts["esp_surface_extrema_point_count"] == 104
    assert counts["esp_area_distribution_count"] == 6
    assert counts["esp_area_bin_point_count"] == 90
    assert Counter(row["observation_kind"] for row in rows) == {
        "reaction_topology": 2,
        "reaction_energy_curve": 6,
        "esp_surface_and_area_distribution": 6,
    }
    assert all(row["record_granularity"] != "point_as_sample" for row in rows)
    assert {row["admission_status"] for row in rows} == {"conditional_reference"}
    assert max(float(row["future_weight_ceiling"]) for row in rows) == 0.15


def test_high_risk_qc_flags_and_gold_classification_are_frozen(
    result: dict[str, object],
) -> None:
    summary = result["summary"]
    qc = summary["qc"]
    classification = summary["scientific_classification"]
    assert qc["figure1_20c_protocol_conflict"] is True
    assert qc["figure1_temperature_exceeds_reported_950c"] is True
    assert qc["figure2_last_element_label_should_be_n"] is True
    assert qc["figure2_after_carbon_atomic_percent_conflict"] is True
    assert qc["figure3_delta_header_sign_reversed"] is True
    assert qc["figure3_exact_duplicate_display_block"] is True
    assert qc["figure4_missing_path_ids"] == [44, 45]
    assert qc["figure5_identity_or_caption_conflict"] is True
    assert qc["supplementary_ftir_axis_is_rounded_and_repeated"] is True
    assert classification["gold_layers"] == ["Gold-E", "Gold-C"]
    assert classification["gold_admission_status"] == "admitted_reference"
    assert classification["direct_tpu_mechanics_supervision"] is False
    assert classification["source_weight_ceiling"] == 0.25


def test_rendered_outputs_are_small_and_deterministic(
    result: dict[str, object], source_xlsx: Path
) -> None:
    assert set(result["outputs"]) == set(audit.OUTPUT_NAMES)
    rerun = audit.run_audit(source_xlsx=source_xlsx, write_outputs=False)
    assert rerun["outputs"] == result["outputs"]
    assert len(result["outputs"]["内容审计摘要.json"]) < 30_000
    assert len(result["outputs"]["实验曲线审计清单.tsv"]) < 30_000
    assert len(result["outputs"]["计算观测清单.tsv"]) < 30_000
    assert len(result["outputs"]["文件校验清单.tsv"]) < 2_000
