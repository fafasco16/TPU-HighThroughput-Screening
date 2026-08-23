import csv
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "Gold_C_计算性能.csv.gz"
MANIFEST = ROOT / "结果" / "样本清单.csv.gz"
INVENTORY = ROOT / "结果" / "数据总账.json"

REQUIRED_COLUMNS = {
    "source_id",
    "source_record_id",
    "observation_id",
    "canonical_structure",
    "system_identity",
    "structure_identity_status",
    "global_structure_family_key",
    "simulation_key",
    "split_group",
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
    "source_validation_status",
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
def generated_gold_c(generated_inventory_outputs: dict[str, str]) -> dict:
    source_counts: Counter[str] = Counter()
    source_admission_counts: Counter[tuple[str, str]] = Counter()
    method_counts: Counter[str] = Counter()
    record_role_counts: Counter[tuple[str, str]] = Counter()
    role_admission_counts: Counter[tuple[str, str, str]] = Counter()
    unit_status_counts: Counter[str] = Counter()
    unit_admission_counts: Counter[tuple[str, str]] = Counter()
    structure_keys: dict[str, set[str]] = defaultdict(set)
    structure_records: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    thermal_status_counts: Counter[tuple[str, str, str]] = Counter()
    nonthermal_admission_mismatch = 0
    row_count = 0
    non_omg_observation_ids: set[str] = set()
    overlap_sources = {
        "source_github_radonpy_pi1070_840dd4a",
        "source_polyomics_data",
    }
    with gzip.open(OUTPUT, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert set(reader.fieldnames or ()) >= REQUIRED_COLUMNS
        for row in reader:
            row_count += 1
            source_id = row["source_id"]
            source_counts[source_id] += 1
            source_admission_counts[
                (source_id, row["gold_admission_status"])
            ] += 1
            method_counts[row["method_family"]] += 1
            record_role_counts[(source_id, row["record_role"])] += 1
            role_admission_counts[
                (source_id, row["record_role"], row["gold_admission_status"])
            ] += 1
            unit_status_counts[row["unit_status"]] += 1
            unit_admission_counts[
                (row["unit_status"], row["property_admission_status"])
            ] += 1
            key = row["global_structure_family_key"]
            if source_id in overlap_sources:
                structure_keys[source_id].add(key)
                structure_records[source_id][key].add(
                    row["source_record_id"]
                )
            assert math.isfinite(float(row["value"]))
            assert row["system_identity"]
            if row["canonical_structure"]:
                assert row["canonical_structure"]
                assert key.startswith("global_polymer_structure_")
            elif source_id == "source_mendeley_pufoam_v1":
                assert row["structure_identity_status"] == (
                    "process_system_identity_only"
                )
                assert key == "family_pufoam_generic_nco_oh_water_npentane"
            elif source_id == "source_figshare_ma5c03283_si":
                assert row["structure_identity_status"] == (
                    "coarse_grained_component_family_only_exact_atomistic_graph_unresolved"
                )
                assert key.startswith("family_multicomponent_pu_")
            elif source_id == "source_mendeley_n9h66xjk7y_v1":
                assert row["structure_identity_status"] == (
                    "single_nominal_formulation_commercial_component_identity_only_"
                    "exact_structure_unresolved"
                )
                assert key == "family_mendeley_aged_vegetable_puf"
            else:
                assert row["structure_identity_status"] == (
                    "formulation_label_with_molar_ratio_link_no_single_smiles"
                )
                assert key.startswith("global_formulation_system_")
            assert row["simulation_key"]
            assert row["split_group"]
            if source_id == "source_mendeley_pufoam_v1":
                assert row["split_group"] == row["simulation_key"]
            else:
                assert row["split_group"] == key
            assert row["unit"]
            assert row["method_detail"]
            assert row["fidelity_level"]
            assert row["record_role"]
            assert row["current_weight_materialized"] == "false"
            assert row["training_weight"] == ""
            assert row["source_locator"]
            assert row["citation_keys"]
            ceiling = float(row["potential_weight_ceiling"])
            if source_id in {
                "source_mendeley_pufoam_v1",
                "source_figshare_ma5c03283_si",
            }:
                assert 0 <= ceiling <= 0.30
            else:
                assert 0 <= ceiling <= 0.25
            if source_id == "source_figshare_ma5c03283_si":
                if row["record_role"].endswith("input_descriptor"):
                    assert ceiling == 0.0
                else:
                    assert row["record_role"].endswith("output")
            if source_id != "source_omg_batch10":
                non_omg_observation_ids.add(row["observation_id"])
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
        "source_admission_counts": source_admission_counts,
        "method_counts": method_counts,
        "record_role_counts": record_role_counts,
        "role_admission_counts": role_admission_counts,
        "unit_status_counts": unit_status_counts,
        "unit_admission_counts": unit_admission_counts,
        "structure_keys": structure_keys,
        "structure_records": structure_records,
        "thermal_status_counts": thermal_status_counts,
        "nonthermal_admission_mismatch": nonthermal_admission_mismatch,
        "non_omg_observation_id_count": len(non_omg_observation_ids),
    }


def test_gold_c_long_table_contains_real_finite_source_values(
    generated_gold_c: dict,
):
    assert generated_gold_c["row_count"] == 1_435_243
    assert generated_gold_c["non_omg_observation_id_count"] == 243_343
    assert generated_gold_c["source_counts"] == {
        "source_github_radonpy_pi1070_840dd4a": 440,
        "source_polyomics_data": 209_905,
        "ledger_source_106": 5,
        "source_mendeley_pufoam_v1": 9_014,
        "source_omg_batch10": 1_191_900,
        "source_openpolymer_challenge_v1": 4_524,
        "source_figshare_ma5c03283_si": 115,
        "source_mendeley_n9h66xjk7y_v1": 19_340,
    }
    assert {
        "DFT",
        "MD",
        "NEMD",
        "MD-derived",
        "CFD-PBE-QMOM",
        "computational-protocol",
        "DFT-AA-MD-reactive-CG",
        "Abaqus_UMAT_Arrhenius_large_deformation",
    } <= set(generated_gold_c["method_counts"])
    assert (
        generated_gold_c["unit_status_counts"][
            "source_native_unit_unresolved"
        ]
        > 0
    )
    admissions = generated_gold_c["source_admission_counts"]
    assert admissions[("source_omg_batch10", "admitted_reference")] == 52_150
    assert admissions[("source_omg_batch10", "conditional_reference")] == 1_139_750
    assert admissions[
        ("source_openpolymer_challenge_v1", "admitted_reference")
    ] == 389
    assert admissions[
        ("source_openpolymer_challenge_v1", "conditional_reference")
    ] == 4_135
    assert admissions[
        ("source_mendeley_pufoam_v1", "admitted_reference")
    ] == 4_293
    assert admissions[
        ("source_mendeley_pufoam_v1", "conditional_reference")
    ] == 4_721
    assert admissions[
        ("source_figshare_ma5c03283_si", "admitted_reference")
    ] == 98
    assert admissions[
        ("source_figshare_ma5c03283_si", "conditional_reference")
    ] == 17
    assert admissions[
        ("source_mendeley_n9h66xjk7y_v1", "admitted_reference")
    ] == 19_305
    assert admissions[
        ("source_mendeley_n9h66xjk7y_v1", "conditional_reference")
    ] == 35
    roles = generated_gold_c["record_role_counts"]
    assert sum(
        count
        for (source_id, role), count in roles.items()
        if source_id == "source_figshare_ma5c03283_si"
        and role.endswith("input_descriptor")
    ) == 68
    assert sum(
        count
        for (source_id, role), count in roles.items()
        if source_id == "source_figshare_ma5c03283_si"
        and role.endswith("output")
    ) == 47
    output_admissions = Counter()
    for (source_id, role, admission), count in generated_gold_c[
        "role_admission_counts"
    ].items():
        if (
            source_id == "source_figshare_ma5c03283_si"
            and role.endswith("output")
        ):
            output_admissions[admission] += count
    assert output_admissions == {
        "admitted_reference": 34,
        "conditional_reference": 13,
    }


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


def test_acs_dual_phase_md_values_keep_formulation_identity_and_raw_anomaly(
    generated_gold_c: dict,
):
    with gzip.open(OUTPUT, "rt", encoding="utf-8", newline="") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row["source_id"] == "ledger_source_106"
        ]
    assert len(rows) == 5
    assert all(row["method_family"] == "MD" for row in rows)
    assert all(row["canonical_structure"] == "" for row in rows)
    assert all(row["system_identity"] for row in rows)
    assert Counter(row["gold_admission_status"] for row in rows) == {
        "admitted_reference": 4,
        "conditional_reference": 1,
    }
    anomaly = next(row for row in rows if "D0C4" in row["system_identity"])
    assert float(anomaly["value"]) == 211194.0
    assert anomaly["source_validation_status"] == "published_value_anomaly_retained"


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
            "multifidelity_gold_c_reference_not_training_dataset"
        ),
        "path": "结果/Gold_C_计算性能.csv.gz",
        "row_count": 1_435_243,
        "source_value_counts": {
            radon_id: 440,
            polyomics_id: 209_905,
            "ledger_source_106": 5,
            "source_mendeley_pufoam_v1": 9_014,
            "source_omg_batch10": 1_191_900,
            "source_openpolymer_challenge_v1": 4_524,
            "source_figshare_ma5c03283_si": 115,
            "source_mendeley_n9h66xjk7y_v1": 19_340,
        },
        "cross_source_overlap_structure_count": 11,
        "cross_source_overlap_polyomics_record_count": 58,
        "batch15_multiscale_numeric_context_count": 115,
        "batch15_multiscale_input_descriptor_count": 68,
        "batch15_multiscale_performance_output_count": 47,
        "batch19_aged_puf_compact_scalar_count": 19_340,
        "batch19_aged_puf_admission_counts": {
            "admitted_reference": 19_305,
            "conditional_reference": 35,
        },
        "current_weight_materialized": False,
    }


def test_gold_c_gzip_is_deterministic_and_stream_readable(
    generated_gold_c: dict,
    regenerated_inventory_outputs: tuple[dict[str, str], dict[str, str]],
):
    header = OUTPUT.read_bytes()[:10]
    assert header[:2] == b"\x1f\x8b"
    assert header[4:8] == b"\x00\x00\x00\x00"
    first, second = regenerated_inventory_outputs
    assert first == second
    assert _sha256(OUTPUT) == generated_gold_c["sha256"]
    with gzip.open(OUTPUT, "rt", encoding="utf-8", newline="") as handle:
        assert next(csv.reader(handle))[0] == "source_id"
