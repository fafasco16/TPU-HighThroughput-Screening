from __future__ import annotations

import csv
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "代码" / "审计" / "第十九批老化植物基PU泡沫模拟.py"
SOURCE_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第十九批模拟_老化植物基PU泡沫"
)
ARCHIVE = SOURCE_DIR / "n9h66xjk7y-1.zip"

EXPECTED_GOLD_C_COLUMNS = (
    "source_id",
    "source_record_id",
    "observation_id",
    "canonical_structure",
    "system_identity",
    "structure_identity_status",
    "global_structure_family_key",
    "simulation_key",
    "split_group",
    "property_name",
    "value",
    "unit",
    "unit_status",
    "method_family",
    "method_detail",
    "fidelity_level",
    "temp",
    "press",
    "gold_admission_status",
    "property_admission_status",
    "source_validation_status",
    "record_role",
    "potential_weight_ceiling",
    "current_weight_materialized",
    "training_weight",
    "source_locator",
    "citation_keys",
)


def _load_auditor():
    spec = importlib.util.spec_from_file_location("batch19_aged_puf_audit", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _require_payload() -> None:
    if not ARCHIVE.is_file():
        pytest.skip("第十九批老化植物基PU泡沫模拟固定 ZIP 未在当前检出中分发")


@pytest.fixture(scope="module")
def auditor():
    _require_payload()
    return _load_auditor()


@pytest.fixture(scope="module")
def audited(auditor):
    return auditor.audit_archive()


def test_batch19_archive_identity_and_member_inventory_are_frozen(audited) -> None:
    summary = audited["summary"]
    assert summary["dataset"]["doi"] == "10.17632/n9h66xjk7y.1"
    assert summary["dataset"]["license"] == "CC BY 4.0"
    assert summary["archive"] == {
        "filename": "n9h66xjk7y-1.zip",
        "bytes": 7_042_325,
        "sha256": "e672f248e43b5cd6d9173ec87612643749b2b7e59ef528975b64d82d69e27b02",
        "member_count": 4_202,
        "uncompressed_bytes": 29_380_486,
        "compressed_member_bytes": 6_052_929,
        "crc_status": "pass",
        "path_safety_status": "pass",
        "suffix_counts": {".npy": 2, ".rpt": 4_200},
    }
    assert len(audited["files"]) == 4_202
    assert len({row["member_path"] for row in audited["files"]}) == 4_202
    assert all(len(row["sha256"]) == 64 for row in audited["files"])


def test_batch19_counts_material_formulation_runs_conditions_and_points_separately(
    audited,
) -> None:
    summary = audited["summary"]
    assert summary["count_semantics"] == {
        "material_system_count": 1,
        "nominal_formulation_count": 1,
        "simulation_run_count": 4_200,
        "unique_input_condition_count": 3_868,
        "curve_point_count": 424_200,
        "curve_points_are_independent_samples": False,
        "unique_numerical_curve_count": 3_863,
        "unique_raw_rpt_byte_stream_count": 4_192,
    }
    assert summary["conditions"]["age_days_range"] == [3, 2_999]
    assert summary["conditions"]["age_days_unique_count"] == 1_629
    assert summary["conditions"]["temperature_C_range"] == [10, 89]
    assert summary["conditions"]["temperature_C_unique_count"] == 80
    assert summary["conditions"]["direction_summary"]["1"] == {
        "description": "transverse_to_expansion",
        "simulation_run_count": 2_100,
        "unique_condition_count": 1_939,
        "unique_numerical_curve_count": 1_939,
    }
    assert summary["conditions"]["direction_summary"]["3"] == {
        "description": "parallel_to_expansion",
        "simulation_run_count": 2_100,
        "unique_condition_count": 1_929,
        "unique_numerical_curve_count": 1_924,
    }


def test_batch19_duplicate_conditions_and_cross_condition_collision_are_explicit(
    audited,
) -> None:
    duplicate = audited["summary"]["duplicate_audit"]
    assert duplicate["condition_group_size_distribution"] == {
        "1": 3_580,
        "2": 248,
        "3": 36,
        "4": 4,
    }
    assert duplicate["condition_groups_with_multiple_runs"] == 288
    assert duplicate["runs_in_repeated_condition_groups"] == 620
    assert duplicate["repeated_condition_excess_run_count"] == 332
    assert duplicate["condition_groups_with_multiple_numerical_curves"] == 0
    assert duplicate["numerical_curve_group_size_distribution"] == {
        "1": 3_575,
        "2": 247,
        "3": 36,
        "4": 4,
        "7": 1,
    }
    assert duplicate["duplicate_numerical_curve_excess_run_count"] == 337
    assert duplicate["raw_rpt_group_size_distribution"] == {
        "1": 4_189,
        "2": 2,
        "7": 1,
    }
    assert duplicate["duplicate_raw_rpt_excess_run_count"] == 8
    collision = duplicate["cross_condition_numerical_curve_groups"]
    assert len(collision) == 1
    assert collision[0]["run_ids"] == [4059, 4093, 4094, 4138, 4144, 4157, 4192]
    assert collision[0]["unique_condition_count"] == 6


def test_batch19_one_known_incomplete_final_step_is_conditional(audited) -> None:
    quality = audited["summary"]["curve_quality"]
    assert quality["point_count_per_run"] == 101
    assert quality["all_stress_curves_monotonic_nondecreasing"] is True
    assert quality["issue_run_count"] == 1
    assert quality["issue_runs"] == [
        {
            "run_id": 4_193,
            "condition_key": "direction=3|age_days=27|temperature_C=84",
            "member_path": (
                "Simulated Results for Aged Polyurethane Foam/"
                "Stress-Strain data/4193.rpt"
            ),
            "issues": [
                "expected_x_grid_0_to_1_by_0_01_not_met",
                "duplicate_adjacent_x_count=1",
                "final_x_1_0_missing",
            ],
        }
    ]
    run = audited["runs"][4_193]
    assert run["curve_quality_status"] == "conditional_missing_final_step"
    assert run["x_max"] == "0.99"
    assert run["compressive_log_strain_max"] == "1.18091"


def test_batch19_gold_c_schema_counts_and_governance_are_exact(auditor, audited) -> None:
    rows = auditor.build_gold_c_rows()
    assert auditor.GOLD_C_VALUE_COLUMNS == EXPECTED_GOLD_C_COLUMNS
    assert len(rows) == 19_340
    assert all(tuple(row) == EXPECTED_GOLD_C_COLUMNS for row in rows)
    assert len({row["observation_id"] for row in rows}) == 19_340
    assert len({row["simulation_key"] for row in rows}) == 3_868
    assert {row["split_group"] for row in rows} == {
        "family_mendeley_aged_vegetable_puf"
    }
    assert Counter(row["property_name"] for row in rows) == {
        "mises_stress_at_compressive_log_strain_0_1": 3_868,
        "mises_stress_at_compressive_log_strain_0_5": 3_868,
        "mises_stress_at_compressive_log_strain_1_0": 3_868,
        "maximum_observed_mises_stress": 3_868,
        "compression_energy_density_to_max_observed_log_strain": 3_868,
    }
    assert Counter(row["gold_admission_status"] for row in rows) == {
        "admitted_reference": 19_305,
        "conditional_reference": 35,
    }
    collision_rows = [
        row
        for row in rows
        if row["source_validation_status"].endswith(
            "cross_condition_identical_numerical_curve"
        )
    ]
    assert len(collision_rows) == 30
    assert {row["potential_weight_ceiling"] for row in collision_rows} == {"0.10"}
    assert all(row["current_weight_materialized"] == "false" for row in rows)
    assert all(row["training_weight"] == "" for row in rows)
    assert audited["summary"]["gold_c_materialization"][
        "curve_point_rows_materialized"
    ] == 0


def test_batch19_fixed_baseline_values_and_kelvin_temperature(auditor) -> None:
    rows = auditor.build_gold_c_rows()
    baseline_rows = {
        row["property_name"]: row
        for row in rows
        if row["source_record_id"] == "mendeley:n9h66xjk7y:v1:run=0000"
    }
    assert set(baseline_rows) == {
        "mises_stress_at_compressive_log_strain_0_1",
        "mises_stress_at_compressive_log_strain_0_5",
        "mises_stress_at_compressive_log_strain_1_0",
        "maximum_observed_mises_stress",
        "compression_energy_density_to_max_observed_log_strain",
    }
    assert all(row["temp"] == "308.15" for row in baseline_rows.values())
    assert float(
        baseline_rows["mises_stress_at_compressive_log_strain_0_1"]["value"]
    ) == pytest.approx(0.373330725617012)
    assert float(
        baseline_rows["mises_stress_at_compressive_log_strain_0_5"]["value"]
    ) == pytest.approx(0.467617027633851)
    assert float(
        baseline_rows["mises_stress_at_compressive_log_strain_1_0"]["value"]
    ) == pytest.approx(0.717045764687402)
    assert float(
        baseline_rows["maximum_observed_mises_stress"]["value"]
    ) == pytest.approx(0.896512)
    assert float(
        baseline_rows[
            "compression_energy_density_to_max_observed_log_strain"
        ]["value"]
    ) == pytest.approx(0.639912620312825)


def test_batch19_written_outputs_match_fresh_audit(auditor, audited) -> None:
    summary = auditor.write_outputs()
    frozen = json.loads(
        (SOURCE_DIR / "内容审计摘要.json").read_text(encoding="utf-8")
    )
    assert summary == frozen == audited["summary"]

    with (SOURCE_DIR / "Gold_C_紧凑标量表.tsv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)
    assert tuple(reader.fieldnames or ()) == EXPECTED_GOLD_C_COLUMNS
    assert rows == auditor.build_gold_c_rows()
    assert (SOURCE_DIR / "来源说明.md").is_file()
