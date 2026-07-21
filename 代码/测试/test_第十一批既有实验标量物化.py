from __future__ import annotations

import math
from collections import Counter

from 审计.第十批ACS表格物化 import RECORD_COLUMNS
from 审计.第十一批既有实验标量物化 import (
    SPECS,
    build_records,
    verify_inputs,
)


def test_冻结输入身份与行数() -> None:
    verified = verify_inputs()
    assert len(verified) == 5
    assert sum(int(row["rows"]) for row in verified) == 2_675
    assert {row["license"] for row in verified} == {"CC BY 4.0"}
    assert all(len(str(row["sha256"])) == 64 for row in verified)


def test_统一长表字段计数与治理状态() -> None:
    rows, audit = build_records()
    assert len(rows) == 2_630
    assert audit["record_count"] == 2_630
    assert Counter(row["source_directory"] for row in rows) == {
        SPECS["fdm"].directory: 1_170,
        SPECS["spore"].directory: 144,
        SPECS["sheffield"].directory: 755,
        SPECS["sls"].directory: 375,
        SPECS["literature"].directory: 186,
    }
    assert Counter(row["gold_admission_status"] for row in rows) == {
        "admitted_reference": 2_102,
        "conditional_reference": 528,
    }
    assert all(set(row) == set(RECORD_COLUMNS) for row in rows)
    assert all(row["current_weight_materialized"] == "false" for row in rows)
    assert all(row["training_weight"] == "" for row in rows)
    assert len({row["observation_id"] for row in rows}) == len(rows)
    assert all(math.isfinite(float(row["value"])) for row in rows)


def test_fdm_只物化真实数值并保留冲突状态() -> None:
    rows, _ = build_records()
    fdm = [row for row in rows if row["source_directory"] == SPECS["fdm"].directory]
    assert len(fdm) == 1_170
    assert Counter(row["gold_admission_status"] for row in fdm) == {
        "admitted_reference": 935,
        "conditional_reference": 235,
    }
    assert sum(float(row["potential_weight_ceiling"]) == 0 for row in fdm) == 107
    assert all(str(row["property_name"]).startswith("fdm_") for row in fdm)
    assert all(row["value"] != "" for row in fdm)


def test_孢子填充_tpu_四项力学与配方重复闭合() -> None:
    rows, _ = build_records()
    spore = [
        row for row in rows if row["source_directory"] == SPECS["spore"].directory
    ]
    assert Counter(row["property_name"] for row in spore) == {
        "toughness": 36,
        "tensile_strength": 36,
        "elongation_at_break": 36,
        "young_modulus": 36,
    }
    assert len({row["formulation_id"] for row in spore}) == 12
    assert len({row["sample_id"] for row in spore}) == 36
    assert {row["component_name"] for row in spore} == {"WT", "HST"}
    assert min(float(row["value"]) for row in spore if row["property_name"] == "tensile_strength") == 16.92


def test_sheffield_排除非pu对照但保留条件参考() -> None:
    rows, _ = build_records()
    sheffield = [
        row
        for row in rows
        if row["source_directory"] == SPECS["sheffield"].directory
    ]
    assert len(sheffield) == 755
    assert Counter(row["gold_admission_status"] for row in sheffield) == {
        "admitted_reference": 723,
        "conditional_reference": 32,
    }
    assert "water_drop_penetration_time" in {
        row["property_name"] for row in sheffield
    }
    assert all("non_pu_reference_material" not in row["mapping_status"] for row in sheffield)


def test_sls_和文献汇总保持原准入天花板() -> None:
    rows, _ = build_records()
    sls = [row for row in rows if row["source_directory"] == SPECS["sls"].directory]
    literature = [
        row
        for row in rows
        if row["source_directory"] == SPECS["literature"].directory
    ]
    assert Counter(row["property_name"] for row in sls) == {
        "compressive_load_at_25_percent_deflection": 75,
        "compressive_load_at_65_percent_deflection": 75,
        "sag_factor": 75,
        "hysteresis_loss_ratio": 75,
        "specimen_weight": 75,
    }
    assert Counter(row["gold_admission_status"] for row in sls) == {
        "admitted_reference": 300,
        "conditional_reference": 75,
    }
    assert Counter(row["property_name"] for row in literature) == {
        "shore_hardness": 62,
        "ultimate_tensile_strength": 62,
        "elongation_at_break": 62,
    }
    assert {row["gold_admission_status"] for row in literature} == {
        "conditional_reference"
    }
    assert {float(row["potential_weight_ceiling"]) for row in literature} == {
        0.1,
        0.2,
    }
