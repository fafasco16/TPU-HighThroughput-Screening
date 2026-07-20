import csv
import gzip
import hashlib
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "代码" / "生成数据总账.py"
OUTPUT = ROOT / "结果" / "Gold_C_计算性能.csv.gz"
MANIFEST = ROOT / "结果" / "样本清单.csv.gz"
INVENTORY = ROOT / "结果" / "数据总账.json"

REQUIRED_COLUMNS = {
    "source_id",
    "source_record_id",
    "observation_id",
    "canonical_structure",
    "global_structure_family_key",
    "simulation_key",
    "property_name",
    "value",
    "unit",
    "method_family",
    "method_detail",
    "fidelity_level",
    "temp",
    "press",
    "gold_admission_status",
    "property_admission_status",
    "record_role",
    "potential_weight_ceiling",
    "current_weight_materialized",
    "training_weight",
    "source_locator",
    "citation_keys",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def generated_gold_c() -> dict:
    subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, check=True)
    source_counts: Counter[str] = Counter()
    method_counts: Counter[str] = Counter()
    unit_status_counts: Counter[str] = Counter()
    unit_admission_counts: Counter[tuple[str, str]] = Counter()
    structure_keys: dict[str, set[str]] = defaultdict(set)
    structure_records: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    thermal_status_counts: Counter[tuple[str, str, str]] = Counter()
    nonthermal_admission_mismatch = 0
    row_count = 0
    observation_ids: set[str] = set()
    with gzip.open(OUTPUT, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert set(reader.fieldnames or ()) >= REQUIRED_COLUMNS
        for row in reader:
            row_count += 1
            source_id = row["source_id"]
            source_counts[source_id] += 1
            method_counts[row["method_family"]] += 1
            unit_status_counts[row["unit_status"]] += 1
            unit_admission_counts[
                (row["unit_status"], row["property_admission_status"])
            ] += 1
            key = row["global_structure_family_key"]
            structure_keys[source_id].add(key)
            structure_records[source_id][key].add(row["source_record_id"])
            assert math.isfinite(float(row["value"]))
            assert row["canonical_structure"]
            assert key.startswith("global_polymer_structure_")
            assert row["simulation_key"]
            assert row["unit"]
            assert row["method_detail"]
            assert row["fidelity_level"]
            assert row["record_role"]
            assert row["current_weight_materialized"] == "false"
            assert row["training_weight"] == ""
            assert row["source_locator"]
            assert row["citation_keys"]
            assert 0 <= float(row["potential_weight_ceiling"]) <= 0.20
            observation_ids.add(row["observation_id"])
            if row["property_name"] == "thermal_conductivity":
                thermal_status_counts[
                    (
                        row["source_validation_status"],
                        row["gold_admission_status"],
                        row["property_admission_status"],
                    )
                ] += 1
            elif (
                row["property_admission_status"]
                != row["gold_admission_status"]
            ):
                nonthermal_admission_mismatch += 1

    return {
        "sha256": _sha256(OUTPUT),
        "row_count": row_count,
        "source_counts": source_counts,
        "method_counts": method_counts,
        "unit_status_counts": unit_status_counts,
        "unit_admission_counts": unit_admission_counts,
        "structure_keys": structure_keys,
        "structure_records": structure_records,
        "thermal_status_counts": thermal_status_counts,
        "nonthermal_admission_mismatch": nonthermal_admission_mismatch,
        "observation_id_count": len(observation_ids),
    }


def test_gold_c_long_table_contains_real_finite_source_values(
    generated_gold_c: dict,
):
    assert generated_gold_c["row_count"] == 210_345
    assert generated_gold_c["observation_id_count"] == 210_345
    assert generated_gold_c["source_counts"] == {
        "source_github_radonpy_pi1070_840dd4a": 440,
        "source_polyomics_data": 209_905,
    }
    assert {
        "DFT",
        "MD",
        "NEMD",
        "MD-derived",
        "computational-protocol",
    } <= set(generated_gold_c["method_counts"])
    assert (
        generated_gold_c["unit_status_counts"][
            "source_native_unit_unresolved"
        ]
        > 0
    )


def test_polyomics_property_gate_preserves_failed_thermal_values_as_conditional(
    generated_gold_c: dict,
):
    statuses = generated_gold_c["thermal_status_counts"]
    assert statuses[
        (
            "failed_check_value_retained",
            "admitted_reference",
            "conditional_reference",
        )
    ] == 25
    assert statuses[
        (
            "failed_check_value_retained",
            "conditional_reference",
            "conditional_reference",
        )
    ] == 18
    assert sum(
        count
        for (validation, _, admission), count in statuses.items()
        if validation == "failed_check_value_retained"
        and admission == "conditional_reference"
    ) == 43
    assert generated_gold_c["nonthermal_admission_mismatch"] == 27_192
    unit_admissions = generated_gold_c["unit_admission_counts"]
    assert unit_admissions[
        ("source_native_unit_unresolved", "conditional_reference")
    ] == 52_209
    assert unit_admissions[
        ("source_native_unit_unresolved", "admitted_reference")
    ] == 0


def test_cross_source_structure_identity_is_shared_in_values_and_manifest(
    generated_gold_c: dict,
):
    radon_id = "source_github_radonpy_pi1070_840dd4a"
    polyomics_id = "source_polyomics_data"
    overlap = (
        generated_gold_c["structure_keys"][radon_id]
        & generated_gold_c["structure_keys"][polyomics_id]
    )
    assert len(generated_gold_c["structure_keys"][radon_id]) == 11
    assert len(overlap) == 11
    assert len(
        {
            source_record_id
            for key in overlap
            for source_record_id in generated_gold_c["structure_records"][
                polyomics_id
            ][key]
        }
    ) == 58

    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    with gzip.open(MANIFEST, "rt", encoding="utf-8-sig", newline="") as handle:
        manifest = list(csv.DictReader(handle))
    radon_manifest = {
        row["leakage_group_key"]
        for row in manifest
        if row["source_directory"] == "第九批计算_RadonPy_PI1070"
        and row["record_granularity"] == "scalar"
    }
    polyomics_manifest_rows = [
        row
        for row in manifest
        if row["source_directory"] == "第九批计算_PolyOmics"
        and row["record_granularity"] == "run"
    ]
    assert len(radon_manifest) == 11
    assert len(
        {
            row["raw_sample_key"]
            for row in polyomics_manifest_rows
            if row["leakage_group_key"] in radon_manifest
        }
    ) == 58

    metadata = inventory["summary"][
        "gold_c_computational_value_long_table"
    ]
    assert metadata == {
        "artifact_role": "normalized_computational_value_reference",
        "artifact_status": (
            "reference_only_not_final_gold_c_or_training_dataset"
        ),
        "path": "结果/Gold_C_计算性能.csv.gz",
        "row_count": 210_345,
        "source_value_counts": {
            radon_id: 440,
            polyomics_id: 209_905,
        },
        "cross_source_overlap_structure_count": 11,
        "cross_source_overlap_polyomics_record_count": 58,
        "current_weight_materialized": False,
    }


def test_gold_c_gzip_is_deterministic_and_stream_readable(
    generated_gold_c: dict,
):
    header = OUTPUT.read_bytes()[:10]
    assert header[:2] == b"\x1f\x8b"
    assert header[4:8] == b"\x00\x00\x00\x00"
    subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, check=True)
    assert _sha256(OUTPUT) == generated_gold_c["sha256"]
    with gzip.open(OUTPUT, "rt", encoding="utf-8", newline="") as handle:
        assert next(csv.reader(handle))[0] == "source_id"
