"""Sheffield PU 理性设计数据的定向回归测试。"""

from __future__ import annotations

import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "审计/第九批Sheffield_PU理性设计.py"
SPEC = importlib.util.spec_from_file_location("batch9_sheffield_pu_rational_design", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


@pytest.fixture(scope="module")
def result() -> dict[str, object]:
    if not audit.SOURCE_ZIP.is_file():
        pytest.skip("Sheffield Figshare 官方 ZIP 不在当前检出中")
    return audit.run_audit(write_outputs=False)


def test_frozen_official_identity_license_and_zip_safety(result: dict[str, object]) -> None:
    assert audit.DATASET_DOI == "10.15131/shef.data.21510876.v1"
    assert audit.PAPER_DOI == "10.3390/polym14235111"
    assert audit.LICENSE == "CC BY 4.0"
    assert len(result["files"]) == 5
    summary = result["summary"]
    assert summary["counts"]["zip_members"] == 231
    assert summary["counts"]["zip_files"] == 214
    assert summary["counts"]["zip_directories"] == 17
    members = result["members"]
    assert all(row["zip_path_safe"] for row in members)
    assert not any(row["encrypted"] or row["symlink"] for row in members)


def test_40_batches_are_39_formulations_not_40_materials(result: dict[str, object]) -> None:
    formulations = result["formulations"]
    assert len(formulations) == 40
    assert len({row["formulation_id"] for row in formulations}) == 39
    assert Counter(row["experiment"] for row in formulations) == {1: 8, 2: 7, 3: 9, 4: 16}
    screen_ids = {row["formulation_id"] for row in formulations if row["experiment"] < 4}
    final_ids = {row["formulation_id"] for row in formulations if row["experiment"] == 4}
    assert len(screen_ids) == 23
    assert len(final_ids) == 16
    by_sample = {row["sample_id"]: row for row in formulations}
    assert by_sample["F05-34"]["duplicate_of_sample_id"] == "F05-13"
    assert by_sample["F05-34"]["formulation_id"] == by_sample["F05-13"]["formulation_id"]
    assert by_sample["F05-34"]["split_group"] == by_sample["F05-13"]["split_group"]
    assert by_sample["F05-34"]["sample_role"] == "screening_control_repeat"


def test_formulation_semantics_preserve_publication_name_conflict(
    result: dict[str, object],
) -> None:
    formulations = result["formulations"]
    assert {row["surfactant_2_publication_identity"] for row in formulations} == {
        "Tegostab 8476"
    }
    assert {row["surfactant_2_supplement_header"] for row in formulations} == {
        "Vorasurf 5959"
    }
    assert {row["isocyanate_index"] for row in formulations} == {1.15}
    assert all(row["chemistry_resolution"] == "commercial_product_and_pphp_no_smiles" for row in formulations)


def test_curve_inventory_and_point_coverage(result: dict[str, object]) -> None:
    curves = result["curves"]
    assert len(curves) == 155
    assert len({row["curve_id"] for row in curves}) == 155
    assert Counter(row["curve_type"] for row in curves) == {
        "foampi_adiabatic_temperature_rise_and_foam_rise": 40,
        "individual_cell_area_distribution": 47,
        "capillary_rise_height": 51,
        "capillary_rise_height_triplicate_mean": 17,
    }
    checks = result["summary"]["reconciliation_checks"]
    assert checks["curve_points"] == 38_568
    assert checks["individual_cell_area_points"] == 23_952
    # 40个动力学文件包括同配方对照批次，不是40个独立配方。
    kinetic = [row for row in curves if row["curve_type"].startswith("foampi_")]
    assert len(kinetic) == 40
    assert len({row["formulation_id"] for row in kinetic}) == 39
    assert all("adiabatic" in row["curve_type"] for row in kinetic)


def test_scalar_inventory_units_and_admission(result: dict[str, object]) -> None:
    scalars = result["scalars"]
    assert len(scalars) == 764
    assert len({row["scalar_id"] for row in scalars}) == 764
    assert Counter(row["gold_admission_status"] for row in scalars) == {
        "admitted_reference": 723,
        "conditional_reference": 32,
        "evidence_only": 9,
    }
    counts = Counter(row["observable"] for row in scalars)
    assert counts["isocyanate_conversion"] == 37
    assert counts["mean_cell_diameter"] == 62
    assert counts["airflow_at_125_pa"] == 26
    assert counts["effective_open_cell_fraction"] == 26
    assert counts["foam_density"] == 17
    assert counts["water_drop_penetration_time"] == 80
    assert counts["water_drop_penetration_time_mean"] == 16
    assert counts["capillary_rise_height_at_570s"] == 17
    assert {row["unit"] for row in scalars if row["observable"] == "foam_density"} == {
        "kg/m^3"
    }
    assert {row["unit"] for row in scalars if row["observable"] == "mean_cell_diameter"} == {
        "um"
    }
    assert {row["unit"] for row in scalars if row["observable"] == "capillary_uptake_rate_alpha2"} == {
        "1/s"
    }


def test_replicates_and_external_control_cannot_leak(result: dict[str, object]) -> None:
    scalars = result["scalars"]
    duplicate_groups = {
        row["split_group"] for row in scalars if row["sample_id"] in {"F05-13", "F05-34"}
    }
    assert len(duplicate_groups) == 1
    rockwool = [row for row in scalars if row["sample_id"] == "RW"]
    assert len(rockwool) == 9
    assert {row["gold_admission_status"] for row in rockwool} == {"evidence_only"}
    assert {row["split_group"] for row in rockwool} == {"external_control:rockwool"}
    capillary_fits = [
        row
        for row in scalars
        if row["observable"] in {
            "capillary_asymptotic_rise_alpha1",
            "capillary_uptake_rate_alpha2",
        }
        and row["sample_id"] != "RW"
    ]
    assert len(capillary_fits) == 32
    assert {row["gold_admission_status"] for row in capillary_fits} == {
        "conditional_reference"
    }


def test_source_recalculations_are_explicit_not_silently_overwritten(
    result: dict[str, object],
) -> None:
    checks = result["summary"]["reconciliation_checks"]
    assert checks["max_abs_normalized_height_recalculation_error"] < 1e-8
    assert checks["max_abs_workbook_or_csv_formula_error"] < 1e-6
    assert checks["max_abs_reported_wdpt_mean_error"] < 1e-12
    assert 2.42 < checks["max_abs_sag_percent_recalculation_error"] < 2.421
    f05_02_sag = next(
        row
        for row in result["scalars"]
        if row["sample_id"] == "F05-02" and row["observable"] == "foam_sag_percent"
    )
    assert abs(float(f05_02_sag["value"]) - 11.57650916) < 1e-10
    assert "保留作者值" in f05_02_sag["notes"]
    assert 0.97 < checks["capillary_fit_r2_min"] <= checks["capillary_fit_r2_max"] <= 1.0


def test_outputs_are_deterministic_and_complete(result: dict[str, object]) -> None:
    assert tuple(result["outputs"]) == audit.OUTPUT_NAMES
    rerun = audit.run_audit(write_outputs=False)
    assert rerun["outputs"] == result["outputs"]
    for name, payload in result["outputs"].items():
        checked = audit.SOURCE_DIR / name
        if checked.is_file():
            assert checked.read_bytes() == payload
    summary = json.loads(result["outputs"]["内容审计摘要.json"].decode("utf-8"))
    assert summary["counts"]["unique_formulations"] == 39
