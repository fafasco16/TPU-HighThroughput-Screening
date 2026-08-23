from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "代码/审计/第十五批多组分PU多尺度模拟.py"


def _load():
    spec = importlib.util.spec_from_file_location("batch15_multiscale_pu", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_frozen_source_identity_metadata_and_all_table_rows_are_verified() -> None:
    module = _load()
    verification = module.verify_source()
    assert verification["pdf_pages"] == 17
    assert verification["supplement_doi"] == "10.1021/acs.macromol.5c03283.s001"
    assert verification["article_doi"] == "10.1021/acs.macromol.5c03283"
    assert verification["license"] == "CC BY-NC 4.0"
    assert verification["verification"] == (
        "matched_frozen_identity_metadata_and_all_table_row_anchors"
    )
    assert len(verification["verified_files"]) == 3


def test_materialized_counts_roles_and_noninflated_system_identity() -> None:
    module = _load()
    rows, summary = module.build_rows()
    assert len(rows) == 115
    assert summary["unique_material_system_count"] == 10
    assert summary["unique_simulation_run_count"] == 13
    assert summary["split_group_count"] == 10
    assert summary["numeric_context_count"] == 115
    assert summary["input_descriptor_count"] == 68
    assert summary["performance_output_count"] == 47
    assert summary["performance_output_admission_counts"] == {
        "admitted_reference": 34,
        "conditional_reference": 13,
    }
    assert summary["record_role_counts"] == {
        "sensitivity_input_descriptor": 8,
        "sensitivity_output": 17,
        "simulation_input_descriptor": 60,
        "simulation_output": 30,
    }
    assert summary["admission_counts"] == {
        "admitted_reference": 98,
        "conditional_reference": 17,
    }
    assert len({row["observation_id"] for row in rows}) == len(rows)
    assert all(row["split_group"] == row["global_structure_family_key"] for row in rows)
    assert all(
        row["property_admission_status"] == row["gold_admission_status"]
        for row in rows
    )
    assert all(
        float(row["potential_weight_ceiling"]) == 0.0
        for row in rows
        if row["record_role"].endswith("input_descriptor")
    )
    assert all(row["current_weight_materialized"] == "false" for row in rows)
    assert all(row["training_weight"] == "" for row in rows)


def test_source_table_values_and_composition_ratios_are_preserved() -> None:
    module = _load()
    rows, _ = module.build_rows()

    def one(record: str, prop: str):
        matches = [
            row
            for row in rows
            if row["source_record_id"] == record and row["property_name"] == prop
        ]
        assert len(matches) == 1
        return matches[0]

    assert float(one("ma5c03283:S1:R1-1-H4", "coarse_grained_urethane_site_count")["value"]) == 6000
    assert float(one("ma5c03283:S1:R1-5-H2", "PDMS_to_PCDL_ratio")["value"]) == 0.2
    assert float(one("ma5c03283:S3:R1-1-H2", "radius_of_gyration")["value"]) == 126.24
    assert float(one("ma5c03283:S3:R1-1-H2", "cubic_periodic_box_side_length")["value"]) == 242.62
    assert float(one("ma5c03283:S3:R1-1-H2", "mass_density")["value"]) == 1.09
    assert float(one("ma5c03283:S2:R1-1-H2-initial-100A", "tensile_strength")["value"]) == 9.97
    assert float(one("ma5c03283:S2:R1-1-H2-initial-450A", "residual_reactive_sites")["value"]) == 2.89


def test_density_typographical_unit_is_conditional_and_box_notation_is_resolved() -> None:
    module = _load()
    rows, summary = module.build_rows()
    density_rows = [row for row in rows if "density" in row["property_name"]]
    assert len(density_rows) == 17
    assert all(row["unit"] == "g/cm^3" for row in density_rows)
    assert all(row["gold_admission_status"] == "conditional_reference" for row in density_rows)
    assert all(
        row["unit_status"]
        == "source_unit_label_typographical_error_inferred_g_per_cm3"
        for row in density_rows
    )
    assert summary["unit_status_counts"] == {
        "resolved_from_cubic_box_notation_and_source_note": 17,
        "resolved_source_native": 81,
        "source_unit_label_typographical_error_inferred_g_per_cm3": 17,
    }


def test_table_s2_selected_run_is_linked_to_s3_without_duplicate_outputs() -> None:
    module = _load()
    rows, summary = module.build_rows()
    selected_key = "simulation_ma5c03283_r1_1_h2_initial_box_200a"
    selected = [row for row in rows if row["simulation_key"] == selected_key]
    assert Counter(row["property_name"] for row in selected) == {
        "coarse_grained_PDMS_site_count": 1,
        "coarse_grained_PCDL_site_count": 1,
        "coarse_grained_urea_site_count": 1,
        "coarse_grained_urethane_site_count": 1,
        "hard_to_soft_segment_ratio": 1,
        "PDMS_to_PCDL_ratio": 1,
        "radius_of_gyration": 1,
        "cubic_periodic_box_side_length": 1,
        "mass_density": 1,
        "initial_cubic_box_side_length": 1,
        "initial_mass_density": 1,
        "residual_reactive_sites": 1,
        "tensile_strength": 1,
    }
    assert summary["deduplication"] == {
        "table_s2_200a_corresponds_to_table_s3_r1_1_h2": True,
        "duplicated_final_box_density_rg_rows_omitted": 3,
    }
