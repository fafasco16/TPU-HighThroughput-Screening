"""日期籽油基刚性 PU-PIR Mendeley v3 数据的定向回归测试。"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import math
from collections import Counter
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "审计/第十三批日期籽油PU_PIR.py"
SPEC = importlib.util.spec_from_file_location("batch13_date_seed_pu_pir", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


@pytest.fixture(scope="module")
def result() -> dict[str, object]:
    if not (audit.SOURCE_DIR / "Mendeley_元数据_v3.json").is_file():
        pytest.skip("日期籽油 PU-PIR Mendeley v3 原件不在当前检出中")
    return audit.run_audit(write_outputs=False)


def test_official_identity_version_license_and_hashes(result: dict[str, object]) -> None:
    source = result["summary"]["source"]
    assert source == {
        "source_id": "source_mendeley_xs78ch5jb7_v3",
        "dataset_doi": "10.17632/xs78ch5jb7.3",
        "version": 3,
        "publish_date": "2024-11-07",
        "license": "CC BY 4.0",
        "article_doi": "10.1016/j.indcrop.2024.120152",
        "article_license": "CC BY-NC 4.0",
        "repository": "Mendeley Data",
        "source_reliability": "R1",
        "dataset_url": "https://data.mendeley.com/datasets/xs78ch5jb7/3",
    }
    files = {row["file"]: row for row in result["files"]}
    assert set(audit.OFFICIAL_ATTACHMENTS).issubset(files)
    for name, (size, sha256) in audit.OFFICIAL_ATTACHMENTS.items():
        row = files[name]
        assert row["bytes"] == size
        assert row["sha256"] == sha256
        assert row["official_sha256"] == sha256
        assert row["official_sha256_match"] == "true"


def test_workbook_archive_pdf_and_stray_formula_inventory(
    result: dict[str, object],
) -> None:
    verification = result["summary"]["verification"]
    inventory = verification["workbook_inventory"]
    assert len(inventory) == 6
    assert sum(len(sheets) for sheets in inventory.values()) == 22
    assert sum(
        sheet["nonempty_cells"] > 0
        for sheets in inventory.values()
        for sheet in sheets
    ) == 20
    assert sum(
        len(sheet["formula_cells"])
        for sheets in inventory.values()
        for sheet in sheets
    ) == 1
    assert sum(
        sheet["chart_count"]
        for sheets in inventory.values()
        for sheet in sheets
    ) == 1
    assert sum(
        sheet["image_count"]
        for sheets in inventory.values()
        for sheet in sheets
    ) == 0
    assert verification["inner_xlsx"] == {
        "member": "Supplemental data 2\\Supplemental data 2.xlsx",
        "bytes": 16_853,
        "sha256": "e2f1d116a3f2546a01ab85c418a92769eca726b122a7c1b11ec2fd7fab11ae34",
        "read_mode": "7z_stdout_to_memory_no_disk_extraction",
    }
    assert verification["supporting_information_pages"] == 19
    assert verification["stray_formula_excluded"]["formula"] == "=---R19"


def test_exact_formulation_series_point_scalar_and_admission_counts(
    result: dict[str, object],
) -> None:
    materialization = result["summary"]["materialization"]
    assert materialization["independent_final_formulations"] == 5
    assert materialization["independent_material_families"] == 1
    assert materialization["explicit_physical_specimen_count"] == 0
    assert materialization["explicit_replicate_group_count"] == 0
    assert materialization["unique_numeric_series_count"] == 36
    assert materialization["raw_numeric_series_presentations_before_dedup"] == 45
    assert materialization["curve_and_peak_observation_count"] == 101_609
    assert materialization["source_reported_scalar_count"] == 36
    assert materialization["gold_e_observation_count"] == 101_645
    assert materialization["gold_admission_status_counts"] == {
        "admitted_reference": 11_164,
        "conditional_reference": 90_481,
    }
    assert materialization["curve_points_counted_as_independent_samples"] == 0
    assert materialization["training_weight_materialized"] is False


def test_exact_modality_counts(result: dict[str, object]) -> None:
    counts = result["summary"]["materialization"]["counts_by_property"]
    assert counts["ftir_source_native_signal"] == {
        "observations": 18_660,
        "series": 10,
    }
    assert counts["nmr_peak_chemical_shift"] == {
        "observations": 109,
        "series": 4,
    }
    assert counts["dta_signal"] == {"observations": 2_682, "series": 3}
    assert counts["tga_mass_signal"] == {"observations": 4_331, "series": 5}
    assert counts["dtg_mass_rate"] == {"observations": 2_682, "series": 3}
    assert counts["dsc_heat_flow"] == {"observations": 4_326, "series": 1}
    assert counts["tensile_stress_signal"] == {
        "observations": 6_021,
        "series": 5,
    }
    assert counts["compressive_stress_signal"] == {
        "observations": 62_798,
        "series": 5,
    }


def test_mechanical_curves_keep_raw_values_and_unresolved_units(
    result: dict[str, object],
) -> None:
    materialization = result["summary"]["materialization"]
    mechanical = materialization["mechanical"]
    assert mechanical["curve_count"] == 10
    assert mechanical["paired_stress_strain_point_count"] == 68_819
    assert mechanical["curve_point_counts"] == {
        "tensile_s1": 1_584,
        "tensile_s2": 1_244,
        "tensile_s3": 1_260,
        "tensile_s4": 1_026,
        "tensile_s5": 907,
        "compressive_s1": 14_982,
        "compressive_s2": 12_588,
        "compressive_s3": 9_829,
        "compressive_s4": 12_656,
        "compressive_s5": 12_743,
    }
    assert mechanical["internal_missing_value_count"] == 0
    assert mechanical["ragged_terminal_padding_blank_cells"] == 14_011
    assert mechanical["x_axis_unit"] == "unresolved"
    assert mechanical["y_axis_unit"] == "unresolved"
    mechanical_rows = [
        row
        for row in result["rows"]
        if row["property_name"]
        in {"tensile_stress_signal", "compressive_stress_signal"}
    ]
    assert len(mechanical_rows) == 68_819
    assert {row["unit"] for row in mechanical_rows} == {
        "source_axis_unit_unresolved"
    }
    assert {row["condition_unit"] for row in mechanical_rows} == {
        "source_axis_unit_unresolved"
    }
    assert {row["gold_admission_status"] for row in mechanical_rows} == {
        "conditional_reference"
    }
    assert {row["potential_weight_ceiling"] for row in mechanical_rows} == {
        "0.30"
    }
    assert float(mechanical_rows[0]["condition_value"]) == pytest.approx(
        -0.0004830188
    )
    assert float(mechanical_rows[0]["value"]) == pytest.approx(-4.047875e-05)


def test_duplicate_canonicalization_and_tga_identity_conflict(
    result: dict[str, object],
) -> None:
    materialization = result["summary"]["materialization"]
    assert materialization["duplicate_series_presentations_excluded"] == 9
    assert materialization["duplicate_numeric_values_excluded"] == 14_178
    assert materialization["ftir"]["dso_polyol_equals_monitoring_4h"] is True
    assert materialization["ftir"]["foam_combined_table_exact_matches"] == {
        "S1": True,
        "S2": True,
        "S3": True,
        "S4": True,
        "S5": True,
    }
    assert materialization["thermal"]["dso_polyol_tga_equals_s2_tga"] is True
    conflict_rows = [
        row
        for row in result["rows"]
        if row["sample_identity_status"]
        == "conflicting_labels_dso_polyol_vs_S2_30pct_foam"
    ]
    assert len(conflict_rows) == 2_982
    assert {row["formulation_id"] for row in conflict_rows} == {""}
    assert {row["gold_admission_status"] for row in conflict_rows} == {
        "conditional_reference"
    }
    assert {row["potential_weight_ceiling"] for row in conflict_rows} == {"0.10"}
    assert {row["duplicate_status"] for row in conflict_rows} == {
        "canonicalized_cross_workbook_identity_conflict"
    }


def test_nmr_peak_counts_and_thermal_units_are_source_native(
    result: dict[str, object],
) -> None:
    nmr = result["summary"]["materialization"]["nmr"]
    assert nmr["counts_by_spectrum"] == {
        "nmr_1H_date_seed_oil": 25,
        "nmr_13C_date_seed_oil": 34,
        "nmr_1H_dso_polyol": 19,
        "nmr_13C_dso_polyol": 31,
    }
    assert nmr["full_spectrum_arrays_deposited"] is False
    nmr_rows = [
        row for row in result["rows"] if row["property_name"] == "nmr_peak_chemical_shift"
    ]
    assert len(nmr_rows) == 109
    assert {row["unit"] for row in nmr_rows} == {"ppm"}
    assert {row["secondary_condition_unit"] for row in nmr_rows} == {"Hz"}
    assert {row["auxiliary_unit"] for row in nmr_rows} == {
        "source_native_relative_unit_unresolved"
    }
    mass_rows = [
        row for row in result["rows"] if row["property_name"] == "tga_mass_signal"
    ]
    assert Counter(row["unit"] for row in mass_rows) == {"ug": 2_682, "%": 1_649}
    assert result["summary"]["materialization"]["thermal"][
        "mass_values_normalized_or_converted"
    ] is False


def test_formulation_values_and_calculation_boundaries(result: dict[str, object]) -> None:
    scalar_rows = [row for row in result["rows"] if not row["curve_id"]]
    assert len(scalar_rows) == 36
    assert Counter(row["formulation_id"] for row in scalar_rows) == {
        "S1": 7,
        "S2": 7,
        "S3": 7,
        "S4": 7,
        "S5": 7,
        "": 1,
    }
    dso = [
        float(row["value"])
        for row in scalar_rows
        if row["property_name"] == "dso_polyol_share"
    ]
    assert dso == [0, 30, 50, 70, 100]
    pmdi_a = [
        float(row["value"])
        for row in scalar_rows
        if row["property_name"] == "pmdi_required_method_a"
    ]
    assert pmdi_a == pytest.approx(
        [119.6616, 127.9249, 133.4338, 138.9427, 147.2060]
    )
    assert Counter(row["gold_admission_status"] for row in scalar_rows) == {
        "conditional_reference": 20,
        "admitted_reference": 16,
    }
    calculated = [
        row
        for row in scalar_rows
        if row["property_name"]
        in {"pmdi_required_method_a", "pmdi_required_method_b"}
    ]
    assert {row["data_origin"] for row in calculated} == {
        "source_reported_calculation"
    }


def test_row_contract_identity_leakage_and_no_training_weight(
    result: dict[str, object],
) -> None:
    rows = result["rows"]
    assert len({row["observation_id"] for row in rows}) == len(rows)
    assert all(tuple(row) == audit.RECORD_COLUMNS for row in rows)
    assert {row["source_directory"] for row in rows} == {
        "第十三批实验_日期籽油PU-PIR"
    }
    assert {row["global_structure_family_key"] for row in rows} == {
        "family_date_seed_oil_rigid_pu_pir_2025"
    }
    assert {row["family_leakage_group"] for row in rows} == {
        "family_date_seed_oil_rigid_pu_pir_2025"
    }
    assert {row["curve_points_are_independent_samples"] for row in rows} == {
        "false"
    }
    assert {row["current_weight_materialized"] for row in rows} == {"false"}
    assert all(row["training_weight"] == "" for row in rows)
    assert all(math.isfinite(float(row["value"])) for row in rows)


def test_checked_outputs_match_and_tsv_has_exact_row_count(
    result: dict[str, object],
) -> None:
    assert set(result["outputs"]) == {
        "Gold_E_实验观测长表.tsv",
        "内容审计摘要.json",
        "文件校验清单.tsv",
        "来源说明.md",
    }
    for name, payload in result["outputs"].items():
        path = audit.SOURCE_DIR / name
        assert path.is_file()
        assert audit._sha256(path) == hashlib.sha256(payload).hexdigest()
    with audit.OUTPUT_LONG_TABLE.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert tuple(reader.fieldnames or ()) == audit.RECORD_COLUMNS
        assert sum(1 for _ in reader) == 101_645
