from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "代码" / "审计" / "第十八批IIR_OH聚氨酯.py"
SOURCE_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第十八批实验_IIR-OH聚氨酯"
)
ARCHIVE = SOURCE_DIR / "wg3znh66bv-1.zip"


def _load_auditor():
    spec = importlib.util.spec_from_file_location("batch18_iir_oh_pu_audit", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _require_payload() -> None:
    if not ARCHIVE.is_file():
        pytest.skip("第十八批IIR-OH聚氨酯固定ZIP未在当前检出中分发")


@pytest.fixture(scope="module")
def audited():
    _require_payload()
    return _load_auditor().audit_archive()


def test_batch18_archive_identity_and_inventory_are_frozen(audited) -> None:
    summary = audited["summary"]
    assert summary["dataset_doi"] == "10.17632/wg3znh66bv.1"
    assert summary["license"] == "CC-BY-4.0"
    assert summary["archive"] == {
        "filename": "wg3znh66bv-1.zip",
        "bytes": 38_825_776,
        "sha256": "5224ae550be0c04022f0de02f03bd08f6ae39f2e6f84044fb8ec9e1cd5872e76",
        "member_count": 207,
        "uncompressed_bytes": 157_552_695,
        "crc_status": "pass",
        "path_safety_status": "pass",
    }
    assert summary["suffix_counts"] == {
        ".arw": 2,
        ".csv": 53,
        ".opju": 1,
        ".pdf": 2,
        ".spa": 47,
        ".txt": 99,
        ".xlsx": 3,
    }
    assert len(audited["files"]) == 207
    assert len({row["member_path"] for row in audited["files"]}) == 207
    assert all(len(row["sha256"]) == 64 for row in audited["files"])


def test_batch18_counts_materials_curves_and_points_separately(audited) -> None:
    summary = audited["summary"]
    assert summary["category_file_counts"] == {
        "barrier_processed_project": 1,
        "cyclic_tensile_raw": 6,
        "ftir_curve_csv": 47,
        "ftir_vendor_binary": 47,
        "gpc_report": 2,
        "gpc_text_curve": 2,
        "gpc_workbook": 1,
        "hydrodynamic_curve": 3,
        "hydrolytic_aging_tensile_raw": 12,
        "network_calculation_workbook": 1,
        "nmr_curve": 6,
        "processed_tensile_workbook": 1,
        "tensile_raw": 45,
        "uv_vis_curve": 33,
    }
    assert summary["curve_count_audited"] == len(audited["curves"]) == 158
    assert summary["curve_point_count_audited"] == 3_050_843
    assert summary["curve_count_candidate_after_dedup"] == 151
    assert summary["curve_point_count_candidate_after_dedup"] == 3_016_301
    assert summary["curve_counts_by_category"] == {
        "cyclic_tensile_raw": 6,
        "ftir_curve_csv": 47,
        "gpc_text_curve": 2,
        "gpc_workbook": 2,
        "hydrodynamic_curve": 3,
        "hydrolytic_aging_tensile_raw": 12,
        "nmr_curve": 6,
        "processed_tensile_workbook": 2,
        "tensile_raw": 45,
        "uv_vis_curve": 33,
    }
    assert summary["curve_points_by_category"] == {
        "cyclic_tensile_raw": 2_062_350,
        "ftir_curve_csv": 331_538,
        "gpc_text_curve": 4_320,
        "gpc_workbook": 4_320,
        "hydrodynamic_curve": 7_000,
        "hydrolytic_aging_tensile_raw": 20_523,
        "nmr_curve": 393_215,
        "processed_tensile_workbook": 22_114,
        "tensile_raw": 185_300,
        "uv_vis_curve": 20_163,
    }
    assert summary["tensile_formulation_code_count"] == 15
    assert summary["tensile_formulation_codes"] == [
        "HDI-10",
        "HDI-2",
        "HDI-4",
        "HDI-6",
        "HDI-8",
        "HMDI-10",
        "HMDI-2",
        "HMDI-4",
        "HMDI-6",
        "HMDI-8",
        "MDI-10",
        "MDI-2",
        "MDI-4",
        "MDI-6",
        "MDI-8",
    ]
    assert summary["network_formulation_code_count"] == 16
    assert summary["all_formulation_code_count"] == 16
    assert "MDI-1" in summary["network_formulation_codes"]


def test_batch18_duplicate_and_workbook_conflicts_remain_explicit(audited) -> None:
    summary = audited["summary"]
    duplicates = summary["duplicate_groups"]
    assert len(duplicates) == 3
    assert all(len(group["member_paths"]) == 2 for group in duplicates)
    assert all(
        any("hydrolytic aging" in path for path in group["member_paths"])
        and any("tensile raw data/HDI/" in path for path in group["member_paths"])
        for group in duplicates
    )
    assert len(summary["semantic_duplicate_groups"]) == 4
    assert summary["duplicate_representation_curve_count"] == 7

    workbooks = {
        Path(row["member_path"]).name: row for row in summary["workbooks"]
    }
    assert workbooks["Tensile.xlsx"]["processed_tensile_label_nonunique"] is True
    network = workbooks["Gel content and Crosslink density calculation.xlsx"]
    formula_counts = Counter()
    for sheet in network["sheets"]:
        formula_counts["formula"] += sheet["formula_count"]
        formula_counts["cached"] += sheet["cached_formula_count"]
    assert formula_counts == {"formula": 480, "cached": 480}
    assert summary["training_split_created"] is False
    assert summary["training_weight_materialized"] is False
    assert summary["model_ready_record_count"] == 0


@pytest.fixture(scope="module")
def materialized(audited):
    return _load_auditor()._build_gold_e_scalar_rows(audited)


def test_batch18_materializes_compact_endpoints_not_three_million_points(
    materialized,
    audited,
) -> None:
    rows, summary = materialized
    assert len(rows) == summary["gold_e_scalar_row_count"] == 306
    assert summary["tensile_curve_count"] == 54
    assert summary["derived_tensile_endpoint_count"] == 162
    assert summary["network_sample_count"] == 48
    assert summary["network_scalar_count"] == 144
    assert Counter(row["property_name"] for row in rows) == {
        "maximum_observed_tensile_stress": 54,
        "maximum_observed_tensile_strain": 54,
        "observed_stress_strain_area_to_last_point": 54,
        "gel_content": 48,
        "crosslink_density_reported_scaled": 48,
        "normalized_swollen_mass_ratio": 48,
    }
    assert Counter(row["gold_admission_status"] for row in rows) == {
        "admitted_reference": 183,
        "conditional_reference": 123,
    }
    assert {row["formulation_id"] for row in rows} == set(
        audited["summary"]["network_formulation_codes"]
    )
    assert all(row["current_weight_materialized"] == "false" for row in rows)
    assert all(row["training_weight"] == "" for row in rows)
    assert len({row["observation_id"] for row in rows}) == 306


def test_batch18_fixed_curve_and_network_gold_values(materialized) -> None:
    rows, _ = materialized
    baseline = {
        row["property_name"]: row
        for row in rows
        if row["sample_id"] == "HDI-2|baseline|rep1"
    }
    assert float(
        baseline["maximum_observed_tensile_stress"]["value"]
    ) == pytest.approx(1.516)
    assert float(
        baseline["maximum_observed_tensile_strain"]["value"]
    ) == pytest.approx(521.779)
    assert float(
        baseline["observed_stress_strain_area_to_last_point"]["value"]
    ) == pytest.approx(5.992617585)

    network = {
        row["property_name"]: row
        for row in rows
        if row["sample_id"] == "HDI-2::rep1"
    }
    assert float(network["gel_content"]["value"]) == pytest.approx(
        0.6176470588235293
    )
    assert float(network["crosslink_density_reported_scaled"]["value"]) == pytest.approx(
        0.09610439809530734
    )
    assert network["crosslink_density_reported_scaled"]["unit"] == (
        "source_scale_unit_unresolved"
    )
    assert float(network["normalized_swollen_mass_ratio"]["value"]) == pytest.approx(
        4.240196078431372
    )


def test_batch18_frozen_summary_matches_a_fresh_full_audit(audited) -> None:
    frozen_path = SOURCE_DIR / "内容审计摘要.json"
    assert frozen_path.is_file()
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    fresh = dict(audited["summary"])
    _, materialization = _load_auditor()._build_gold_e_scalar_rows(audited)
    fresh["gold_e_scalar_materialization"] = materialization
    assert fresh == frozen


def test_batch18_frozen_tsv_outputs_match_fresh_rows(audited, materialized) -> None:
    auditor = _load_auditor()
    rows, _ = materialized
    assert (SOURCE_DIR / "文件校验清单.tsv").read_bytes() == auditor._tsv_bytes(
        audited["files"],
        (
            "member_path",
            "category",
            "suffix",
            "uncompressed_bytes",
            "compressed_bytes",
            "crc32",
            "sha256",
            "point_count",
        ),
    )
    assert (SOURCE_DIR / "曲线审计清单.tsv").read_bytes() == auditor._tsv_bytes(
        audited["curves"],
        (
            "curve_id",
            "category",
            "member_path",
            "member_sha256",
            "point_count",
            "x_name",
            "x_unit",
            "y_name",
            "y_unit",
            "audit_status",
            "notes",
        ),
    )
    assert (SOURCE_DIR / "Gold_E_实验指标.tsv").read_bytes() == auditor._tsv_bytes(
        rows,
        auditor.GOLD_E_SCALAR_COLUMNS,
    )
