"""第八批 TPU 文献力学汇总的定向回归测试。"""

from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "审计/第八批TPU文献力学汇总.py"
SPEC = importlib.util.spec_from_file_location("batch8_tpu_literature", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


@pytest.fixture(scope="module")
def result() -> dict[str, object]:
    if not audit.SOURCE_XLSX.is_file():
        pytest.skip("原始Mendeley工作簿不在当前检出中")
    return audit.run_audit(write_outputs=False)


def test_frozen_source_identity_and_open_license(result: dict[str, object]) -> None:
    assert audit.DOI == "10.17632/ftntxg4zdz.1"
    assert audit.LICENSE == "CC BY 4.0"
    assert audit.EXPECTED_ROWS == 62
    assert audit.EXPECTED_SCALARS == 186
    assert audit.EXPECTED_REFERENCE_GROUPS == 45
    assert audit.EXPECTED_RIS_RECORDS == 37
    assert audit.EXPECTED_REFERENCE_DOIS == 30
    assert len(result["files"]) == 4


def test_rows_are_literature_aggregates_not_specimens(result: dict[str, object]) -> None:
    summary = result["summary"]
    counts = summary["counts"]
    assert counts == {
        "literature_aggregate_rows": 62,
        "scalar_records": 186,
        "numeric_values": 186,
        "exact_duplicate_rows": 0,
        "reference_groups": 45,
        "reference_dois": 30,
        "ris_records": 37,
        "independent_specimens": 0,
        "resolved_formulations": 0,
    }
    assert summary["production_technique_counts"] == {
        "FDM": 31,
        "IM": 15,
        "SLS": 9,
        "MJF": 7,
    }


def test_gold_e_conditional_reference_and_grouped_leakage(
    result: dict[str, object],
) -> None:
    summary = result["summary"]
    classification = summary["scientific_classification"]
    scalars = result["scalars"]
    assert classification["gold_layer"] == "Gold-E"
    assert classification["gold_admission_status"] == "conditional_reference"
    assert classification["direct_chemistry_property_supervision"] is False
    assert Counter(row["observable"] for row in scalars) == {
        "shore_hardness": 62,
        "ultimate_tensile_strength": 62,
        "elongation_at_break": 62,
    }
    assert len({row["split_group"] for row in scalars}) == 45
    assert all(row["record_granularity"] == "literature_aggregate" for row in scalars)
    assert all(row["target_origin"] == "experimental" for row in scalars)
    assert max(float(row["future_weight_ceiling"]) for row in scalars) == 0.20


def test_units_ranges_and_shore_scales_are_preserved(result: dict[str, object]) -> None:
    summary = result["summary"]
    stats = summary["property_statistics"]
    assert stats["shore_hardness"]["min"] == 70.0
    assert stats["shore_hardness"]["max"] == 98.0
    assert stats["ultimate_tensile_strength"]["min"] == 2.9
    assert stats["ultimate_tensile_strength"]["max"] == 111.0
    assert stats["elongation_at_break"]["min"] == 85.0
    assert stats["elongation_at_break"]["max"] == 1200.0
    shore = [row for row in result["scalars"] if row["observable"] == "shore_hardness"]
    assert Counter(row["shore_hardness_scale"] for row in shore) == {"A": 59, "D": 3}


def test_rendered_outputs_are_deterministic(result: dict[str, object]) -> None:
    assert set(result["outputs"]) == set(audit.OUTPUT_NAMES)
    rerun = audit.run_audit(write_outputs=False)
    assert rerun["outputs"] == result["outputs"]
    for name, payload in result["outputs"].items():
        checked = audit.SOURCE_DIR / name
        if checked.is_file():
            assert checked.read_bytes() == payload
