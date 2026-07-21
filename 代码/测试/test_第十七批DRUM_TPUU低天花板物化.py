from __future__ import annotations

import importlib.util
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "代码" / "审计" / "DRUM_TPUU.py"
SOURCE_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "DRUM_TPUU_低天花板"
)
MECHANICAL_DIR = SOURCE_DIR / "解包内容" / "Raw_Mechanical_Testing"


def _load_auditor():
    spec = importlib.util.spec_from_file_location("drum_tpuu_batch17", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _require_payloads() -> None:
    required = {
        *(MECHANICAL_DIR.glob("*_tensile.csv")),
        *(MECHANICAL_DIR.glob("*_hysteresis.csv")),
    }
    if len(required) != 8 or not (SOURCE_DIR / "曲线审计清单.tsv").is_file():
        pytest.skip("DRUM低天花板TPUU原始机械数据未在当前检出中分发")


@pytest.fixture(scope="module")
def materialized():
    _require_payloads()
    return _load_auditor().build_low_ceiling_gold_e_rows()


def test_batch17_materializes_only_governed_csv_observations(materialized) -> None:
    rows, summary = materialized

    assert summary["source_directory"] == "DRUM_TPUU_低天花板"
    assert summary["material_count"] == 4
    assert summary["tensile_curve_count"] == 20
    assert summary["cyclic_curve_count"] == 4
    assert summary["dmta_curve_count_audited_not_materialized"] == 4
    assert summary["raw_curve_point_count"] == 110_281
    assert summary["derived_scalar_count"] == 60
    assert summary["gold_e_numeric_row_count"] == len(rows) == 110_341

    assert Counter(row["record_kind"] for row in rows) == {
        "curve_point": 110_281,
        "derived_scalar": 60,
    }
    assert Counter(row["property_name"] for row in rows) == {
        "tensile_stress": 4_369,
        "cyclic_tensile_stress": 105_912,
        "tensile_strength": 20,
        "elongation_at_break": 20,
        "tensile_toughness": 20,
    }
    assert len({row["observation_id"] for row in rows}) == len(rows)
    assert len({row["curve_id"] for row in rows if row["record_kind"] == "curve_point"}) == 24
    assert {row["formulation_id"] for row in rows} == {
        "TPUU-C",
        "TPUU-D",
        "TPUU-R",
        "TPUU-S",
    }
    assert all(row["gold_admission_status"] == "admitted_reference" for row in rows)
    assert all(row["current_weight_materialized"] == "false" for row in rows)
    assert all(row["training_weight"] == "" for row in rows)
    assert {row["split_group"] for row in rows} == {
        "10.13020/zf53-w893|TPUU-C",
        "10.13020/zf53-w893|TPUU-D",
        "10.13020/zf53-w893|TPUU-R",
        "10.13020/zf53-w893|TPUU-S",
    }
    assert {row["batch_id"] for row in rows} == {
        "TPUU-C|MSM-3-109",
        "TPUU-D|MSM-3-121",
        "TPUU-R|MSM-3-130",
        "TPUU-S|MSM-3-114",
    }
    assert all("未解析批次" not in row["batch_id"] for row in rows)
    assert all(row["curve_points_are_independent_samples"] == "false" for row in rows)


def test_batch17_preserves_source_values_and_lineage(materialized) -> None:
    rows, _ = materialized
    tensile = next(
        row
        for row in rows
        if row["record_kind"] == "curve_point"
        and row["formulation_id"] == "TPUU-C"
        and row["sample_id"] == "MSM_3_109_4sp"
        and row["point_index"] == "1"
    )
    assert math.isclose(float(tensile["value"]), 0.01006966, abs_tol=1e-12)
    assert tensile["unit"] == "MPa"
    assert tensile["condition_name"] == "tensile_strain"
    assert math.isclose(float(tensile["condition_value"]), 0.221231, abs_tol=1e-12)
    assert tensile["condition_unit"] == "%"
    assert tensile["secondary_condition_name"] == "elapsed_time"
    assert tensile["secondary_condition_value"] == "0"
    assert tensile["secondary_condition_unit"] == "s"
    assert tensile["source_locator"].endswith("TPUU-C_tensile.csv#row=4;column_group=1")
    assert len(tensile["file_sha256"]) == 64
    assert tensile["sample_identity_status"] == "instrument_specimen_label"
    assert tensile["duplicate_status"] == "unique_curve_payload"

    cyclic = next(
        row
        for row in rows
        if row["record_kind"] == "curve_point"
        and row["formulation_id"] == "TPUU-C"
        and row["property_name"] == "cyclic_tensile_stress"
        and row["point_index"] == "1"
    )
    assert math.isclose(float(cyclic["value"]), -1.660983, abs_tol=1e-12)
    assert cyclic["unit"] == "kPa"
    assert cyclic["secondary_condition_name"] == "cycle_count"
    assert cyclic["secondary_condition_value"] == "1"
    assert cyclic["auxiliary_value_name"] == "elapsed_time"
    assert cyclic["auxiliary_value"] == "0"
    assert cyclic["auxiliary_unit"] == "s"

    toughness = next(
        row
        for row in rows
        if row["curve_id"] == "drum-low-ceiling:TPUU-C:MSM_3_109_4sp:tensile"
        and row["property_name"] == "tensile_toughness"
    )
    assert math.isclose(float(toughness["value"]), 127.499175256125, rel_tol=1e-12)


def test_batch17_derived_tensile_endpoints_recompute_from_each_curve(materialized) -> None:
    rows, _ = materialized
    point_groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    derived_groups: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        if row["record_kind"] == "curve_point" and row["property_name"] == "tensile_stress":
            point_groups[str(row["curve_id"])].append(row)
        elif row["record_kind"] == "derived_scalar":
            derived_groups[str(row["curve_id"])][str(row["property_name"])] = float(row["value"])

    assert len(point_groups) == len(derived_groups) == 20
    for curve_id, points in point_groups.items():
        ordered = sorted(points, key=lambda row: int(str(row["point_index"])))
        stress = [float(row["value"]) for row in ordered]
        strain_percent = [float(row["condition_value"]) for row in ordered]
        expected_toughness = sum(
            (left_stress + right_stress)
            * 0.5
            * (right_strain - left_strain)
            / 100.0
            for left_stress, right_stress, left_strain, right_strain in zip(
                stress, stress[1:], strain_percent, strain_percent[1:]
            )
            if right_strain >= left_strain
        )
        derived = derived_groups[curve_id]
        assert math.isclose(derived["tensile_strength"], max(stress), rel_tol=1e-12)
        assert math.isclose(
            derived["elongation_at_break"], max(strain_percent), rel_tol=1e-12
        )
        assert math.isclose(
            derived["tensile_toughness"], expected_toughness, rel_tol=1e-12
        )


def test_batch17_builder_is_deterministic() -> None:
    _require_payloads()
    module = _load_auditor()
    first_rows, first_summary = module.build_low_ceiling_gold_e_rows()
    second_rows, second_summary = module.build_low_ceiling_gold_e_rows()
    assert first_summary == second_summary
    assert first_rows == second_rows
