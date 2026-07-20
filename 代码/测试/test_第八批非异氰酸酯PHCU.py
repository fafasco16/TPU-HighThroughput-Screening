"""Mendeley 非异氰酸酯 PHCU 多模态实验包的定向回归测试。"""

from __future__ import annotations

import importlib.util
import math
from collections import Counter
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "审计/第八批非异氰酸酯PHCU.py"
SPEC = importlib.util.spec_from_file_location("batch8_nonisocyanate_phcu", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


@pytest.fixture(scope="module")
def result() -> dict[str, object]:
    if not (audit.SOURCE_DIR / "data.rar").is_file():
        pytest.skip("Mendeley PHCU 官方原件不在当前检出中")
    return audit.run_audit(write_outputs=False)


def test_official_identity_version_license_and_linked_paper(result: dict[str, object]) -> None:
    summary = result["summary"]
    assert audit.DOI == "10.17632/bvv43yk29c.1"
    assert audit.ARTICLE_DOI == "10.1016/j.eurpolymj.2018.08.006"
    assert summary["source"]["version"] == 1
    assert summary["source"]["license"] == "CC BY 4.0"
    assert summary["source"]["source_reliability"] == "R1"
    archive = next(row for row in result["files"] if row["file"] == "data.rar")
    assert archive["bytes"] == 2_956_365
    assert archive["sha256"] == "2b10ccfa6ea2b0b223e65eee525d671ca63919f7483e0fc76a0a337a350c5d10"
    assert archive["official_sha256_match"] is True


def test_counts_separate_formulations_curves_and_points(result: dict[str, object]) -> None:
    counts = result["summary"]["counts"]
    assert counts == {
        "official_attachments": 1,
        "archive_members": 23,
        "frozen_local_source_files": 30,
        "independent_final_formulations": 6,
        "final_polymer_families": 1,
        "curve_records_total": 39,
        "numeric_curve_records": 30,
        "binary_unmaterialized_curve_records": 9,
        "numeric_curve_points_total": 331_138,
        "curve_points_counted_as_independent_samples": 0,
        "explicit_replicate_groups": 0,
        "simulation_records": 0,
    }
    assert len(result["formulations"]) == 6
    assert all(row["curve_points_are_independent_samples"] is False for row in result["curves"])


def test_modality_counts_and_numeric_point_counts(result: dict[str, object]) -> None:
    modalities = result["summary"]["counts_by_modality"]
    assert modalities["tensile_stress_strain"] == {"curves": 6, "numeric_points": 21_545}
    assert modalities["thermogravimetric_mass_curve"] == {"curves": 6, "numeric_points": 14_199}
    assert modalities["wide_angle_xray_diffraction"] == {"curves": 7, "numeric_points": 15_657}
    assert modalities["gpc_chromatogram"] == {"curves": 5, "numeric_points": 13_500}
    assert modalities["nmr_1H"] == {"curves": 3, "numeric_points": 196_608}
    assert modalities["nmr_13C"] == {"curves": 3, "numeric_points": 69_629}
    assert modalities["differential_scanning_calorimetry_binary_raw"] == {"curves": 6, "numeric_points": 0}
    assert modalities["ftir_binary_raw"] == {"curves": 3, "numeric_points": 0}


def test_six_formulations_hu_semantics_and_structure_boundary(result: dict[str, object]) -> None:
    formulations = result["formulations"]
    assert [row["formulation_id"] for row in formulations] == list(audit.FORMULATION_CODES)
    assert [row["hu_mol_percent"] for row in formulations] == [10, 20, 30, 40, 50, 70]
    assert all(row["exact_polymer_smiles"] == "" for row in formulations)
    assert all(row["composition_series_trainable"] is True for row in formulations)
    assert all(row["exact_structure_property_trainable"] is False for row in formulations)
    assert all("COC(=O)OC" in row["known_monomer_smiles"] for row in formulations)


def test_tensile_curve_mapping_ranges_and_weights(result: dict[str, object]) -> None:
    curves = [row for row in result["curves"] if row["modality"] == "tensile_stress_strain"]
    assert [row["formulation_id"] for row in curves] == list(audit.FORMULATION_CODES)
    assert [row["point_count"] for row in curves] == [6478, 6469, 4205, 3296, 748, 349]
    assert min(float(row["y_max"]) for row in curves) > 16.3
    assert max(float(row["y_max"]) for row in curves) < 29.7
    assert min(float(row["x_max"]) for row in curves) > 19.4
    assert max(float(row["x_max"]) for row in curves) < 662.3
    assert {row["mapping_status"] for row in curves} == {"inferred_by_six_pair_order_and_article_range"}
    assert {row["future_weight_ceiling"] for row in curves} == {0.60}


def test_tga_derived_targets_remain_curve_level(result: dict[str, object]) -> None:
    curves = [row for row in result["curves"] if row["modality"] == "thermogravimetric_mass_curve"]
    assert [row["point_count"] for row in curves] == [2020, 2700, 2700, 1379, 2700, 2700]
    expected_t5 = [295.463, 290.529, 293.807, 275.992, 285.477, 281.450]
    for row, expected in zip(curves, expected_t5, strict=True):
        assert math.isclose(float(row["temperature_at_5_percent_loss_c"]), expected, abs_tol=0.001)
        assert row["record_granularity"] == "within_formulation_curve_point"
        assert row["future_weight_ceiling"] == 0.55


def test_gpc_is_not_misrepresented_as_mn_mw_targets(result: dict[str, object]) -> None:
    gpc = [row for row in result["curves"] if row["modality"] == "gpc_chromatogram"]
    assert [row["formulation_id"] for row in gpc] == list(audit.FORMULATION_CODES[:-1])
    assert all(row["point_count"] == 2700 for row in gpc)
    assert {row["gold_admission_status"] for row in gpc} == {"admitted_reference"}
    assert all(row["formulation_mn_g_mol"] == "" for row in result["formulations"])
    assert all(row["formulation_mw_g_mol"] == "" for row in result["formulations"])
    assert result["summary"]["molecular_weight_semantics"]["reported_family_mn_max_g_mol"] == 60_000


def test_dsc_conflicts_are_frozen_and_not_supervision(result: dict[str, object]) -> None:
    statuses = Counter(row["dsc_mapping_status"] for row in result["formulations"])
    assert statuses == {
        "external_internal_conflict": 3,
        "external_internal_match": 2,
        "internal_code_unresolved": 1,
    }
    dsc = [row for row in result["curves"] if row["modality"] == "differential_scanning_calorimetry_binary_raw"]
    assert {row["future_weight_ceiling"] for row in dsc} == {0.0}
    assert {row["gold_admission_status"] for row in dsc} == {"evidence_only"}
    by_code = {row["formulation_id"]: row for row in dsc}
    assert "PHCU30" in by_code["PHCU10"]["notes"]
    assert "PHCU40" in by_code["PHCU20"]["notes"] or "phcu40" in by_code["PHCU20"]["notes"]
    assert "PHCU85" in by_code["PHCU70"]["notes"]


def test_xrd_unmapped_and_leakage_groups_are_explicit(result: dict[str, object]) -> None:
    xrd = [row for row in result["curves"] if row["modality"] == "wide_angle_xray_diffraction"]
    assert len(xrd) == 7
    assert {row["formulation_id"] for row in xrd} == {""}
    assert {row["mapping_status"] for row in xrd} == {"unresolved_opj_has_no_curve_labels"}
    assert {row["gold_admission_status"] for row in xrd} == {"conditional_reference"}
    assert result["summary"]["leakage_policy"]["point_level_split_forbidden"] is True
    assert len({row["split_group"] for row in result["formulations"]}) == 6
    assert len({row["family_leakage_group"] for row in result["formulations"]}) == 1


def test_outputs_are_deterministic_and_checked_files_match(result: dict[str, object]) -> None:
    assert set(result["outputs"]) == set(audit.OUTPUT_NAMES)
    rerun = audit.run_audit(write_outputs=False)
    assert rerun["outputs"] == result["outputs"]
    for name, payload in result["outputs"].items():
        checked = audit.SOURCE_DIR / name
        if checked.is_file():
            assert checked.read_bytes() == payload
