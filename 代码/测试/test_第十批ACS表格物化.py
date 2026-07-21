from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "代码/审计/第十批ACS表格物化.py"


def _load():
    spec = importlib.util.spec_from_file_location("acs_batch10_tables", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_acs_tables_are_hash_and_anchor_verified_and_have_frozen_counts() -> None:
    module = _load()
    rows, summary = module.build_records()
    assert summary["record_count"] == 505
    assert summary["numeric_value_count_including_uncertainty"] == 617
    assert summary["uncertainty_value_count"] == 112
    assert summary["source_record_counts"] == {
        "ACS_Figshare_氢键纳米结构TPU": 143,
        "ACS_Figshare_双相演化聚氨酯": 100,
        "ACS_Figshare_呋喃高强聚氨酯": 217,
        "ACS_Figshare_聚酰亚胺回收链扩剂PU": 45,
    }
    assert summary["target_origin_counts"] == {"experimental": 500, "md": 5}
    assert summary["admission_counts"] == {
        "admitted_reference": 489,
        "conditional_reference": 16,
    }
    assert len({row["observation_id"] for row in rows}) == len(rows)
    assert all(item["verification"].startswith("matched_frozen") for item in summary["verified_files"])


def test_acs_materialized_values_preserve_tables_missingness_and_uncertainty() -> None:
    module = _load()
    rows, _ = module.build_records()
    lookup = {
        (row["sample_id"], row["property_name"], row["condition_name"]): row
        for row in rows
    }
    assert lookup[("HTPU-P1", "tensile_strength", "")]["value"] == 79.21
    assert lookup[("HTPU-P7", "toughness", "")]["value"] == 334.07
    assert ("HTPU-P6", "glass_transition_temperature_soft_segment", "") not in lookup
    edi = lookup[("FPU-3", "energy_dissipation_index", "maximum_strain")]
    # lookup collapses repeated conditions by design; verify the complete series below.
    assert edi["condition_unit"] == "%"
    fpu3_edi = [
        row
        for row in rows
        if row["sample_id"] == "FPU-3"
        and row["property_name"] == "energy_dissipation_index"
    ]
    assert [(row["condition_value"], row["value"], row["uncertainty_value"]) for row in fpu3_edi] == [
        (100.0, 20.25, 0.69),
        (200.0, 23.03, 0.76),
        (300.0, 26.23, 0.65),
        (400.0, 29.46, 0.58),
        (500.0, 32.77, 0.88),
        (600.0, 35.61, 0.67),
        (700.0, 38.13, 0.68),
        (800.0, 40.16, 0.59),
        (900.0, 42.35, 0.62),
        (1000.0, 44.44, 0.74),
    ]
    assert all(row["uncertainty_type"] == "reported_plus_minus_type_unresolved" for row in fpu3_edi)


def test_acs_md_is_admitted_broadly_but_source_anomaly_is_not_repaired() -> None:
    module = _load()
    rows, _ = module.build_records()
    md = [row for row in rows if row["target_origin"] == "md"]
    assert len(md) == 5
    assert Counter(row["gold_admission_status"] for row in md) == {
        "admitted_reference": 4,
        "conditional_reference": 1,
    }
    d0c4 = next(row for row in md if row["sample_id"] == "D0C4")
    assert d0c4["value"] == 211194.0
    assert "without correction" in d0c4["notes"]
    assert all(row["current_weight_materialized"] == "false" for row in rows)
    assert all(row["training_weight"] == "" for row in rows)


def test_acs_secondary_comparison_rows_and_unresolved_curves_are_not_materialized() -> None:
    module = _load()
    rows, _ = module.build_records()
    samples = {row["sample_id"] for row in rows}
    assert "PIT2" not in samples
    assert "HPUU-DDM" not in samples
    assert "PU-BDO" not in samples
    assert all(row["record_kind"] != "curve" for row in rows)
    assert module.MATERIALIZED_EVIDENCE_GROUPS[
        "ACS_Figshare_呋喃高强聚氨酯"
    ] == {
        "table_s1_s2_formulations",
        "table_s5_dissipation",
        "table_s6_recovery",
        "table_s7_residual_strain",
    }
