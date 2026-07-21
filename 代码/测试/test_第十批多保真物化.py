from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "代码" / "审计" / "第十批多保真物化.py"


@pytest.fixture(scope="module")
def module():
    audit_dir = str(SCRIPT.parent)
    if audit_dir not in sys.path:
        sys.path.insert(0, audit_dir)
    spec = importlib.util.spec_from_file_location("batch10_multifidelity", SCRIPT)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def test_input_audit_exposes_frozen_counts_and_schemas(module) -> None:
    audit = module.audit_materialization_inputs()
    assert audit["status"] == "pass"
    assert audit["training_weight_materialized"] is False
    assert audit["candidate"] == {
        "omg": 100_584,
        "openpoly_before_cross_source_deduplication": 3_502,
    }
    assert audit["gold_c"] == {
        "omg_systems": 47_676,
        "omg_properties_per_system": 25,
        "omg_values": 1_191_900,
        "omg_tpu_high_relevance_systems": 2_086,
        "omg_transfer_conditional_systems": 45_590,
        "openpoly_values": 4_524,
    }
    assert audit["gold_e"] == {
        "sciencedb_samples": 643,
        "sciencedb_target_values": 1_929,
        "sciencedb_input_context_fields": 20,
        "kinetics_nco_measurements": 171,
    }
    assert tuple(audit["schema"]["candidate_columns"]) == tuple(
        module.CANDIDATE_COLUMNS
    )
    assert tuple(audit["schema"]["computational_columns"]) == tuple(
        module.COMPUTATIONAL_RECORD_COLUMNS
    )
    assert tuple(audit["schema"]["experimental_columns"]) == tuple(
        module.GOLD_E_RECORD_COLUMNS
    )


@pytest.fixture(scope="module")
def candidate_tables(module):
    omg = module.build_omg_candidate_rows()
    omg_canonical = {row["canonical_smiles"] for row in omg}
    openpoly_all = module.build_openpoly_candidate_rows()
    openpoly_deduplicated = module.build_openpoly_candidate_rows(omg_canonical)
    return omg, openpoly_all, openpoly_deduplicated


def test_gold_v_candidate_counts_roles_and_cross_source_deduplication(
    module, candidate_tables
) -> None:
    omg, openpoly_all, openpoly_deduplicated = candidate_tables
    assert len(omg) == 100_584
    assert len({row["canonical_smiles"] for row in omg}) == 100_584
    assert {row["screening_priority"] for row in omg} == {2}
    assert {row["screening_scope"] for row in omg} == {
        "virtual_tpu_repeat_unit_candidate"
    }
    assert all(row["functional_group_match"] is True for row in omg)

    assert len(openpoly_all) == 3_502
    assert len(openpoly_deduplicated) == 3_501
    assert sum(row["functional_group_match"] for row in openpoly_all) == 211
    assert sum(row["functional_group_match"] for row in openpoly_deduplicated) == 210
    assert {row["screening_priority"] for row in openpoly_all} == {3}
    assert {row["screening_scope"] for row in openpoly_all} == {
        "general_polymer_md_transfer_reference"
    }
    assert {row["data_origin"] for row in openpoly_all} == {"virtual"}

    for row in (omg[0], openpoly_all[0]):
        assert tuple(row) == tuple(module.CANDIDATE_COLUMNS)
        assert "methyl_capped_proxy" in row["structure_status"]
        assert row["gold_layer"] == "Gold-V"
        assert row["direct_property_supervision_weight_ceiling"] == 0.0


def test_omg_gold_c_is_reiterable_stream_with_full_counts(module) -> None:
    first_a = next(module.iter_omg_gold_c_rows())
    first_b = next(module.iter_omg_gold_c_rows())
    assert first_a == first_b

    total = 0
    admissions: Counter[str] = Counter()
    properties: Counter[str] = Counter()
    source_records: set[str] = set()
    for row in module.iter_omg_gold_c_rows():
        total += 1
        admissions[row["gold_admission_status"]] += 1
        properties[row["property_name"]] += 1
        source_records.add(row["source_record_id"])
        assert row["current_weight_materialized"] == "false"
        assert row["training_weight"] == ""
        assert re.fullmatch(
            r"global_polymer_structure_[0-9a-f]{24}",
            row["global_structure_family_key"],
        )
        assert ";field=" in row["source_locator"]
    assert total == 1_191_900
    assert len(source_records) == 47_676
    assert admissions == {
        "admitted_reference": 2_086 * 25,
        "conditional_reference": 45_590 * 25,
    }
    assert len(properties) == 25
    assert set(properties.values()) == {47_676}


def test_openpoly_gold_c_has_only_observed_md_labels(module) -> None:
    rows = module.build_openpoly_gold_c_rows()
    assert len(rows) == 4_524
    assert Counter(row["property_name"] for row in rows) == {
        "Tg": 261,
        "FFV": 223,
        "Tc": 1_404,
        "Density": 1_526,
        "Rg": 1_110,
    }
    assert Counter(row["gold_admission_status"] for row in rows) == {
        "admitted_reference": 389,
        "conditional_reference": 4_135,
    }
    assert all(row["method_family"] == "MD" for row in rows)
    assert all(row["training_weight"] == "" for row in rows)
    assert all(
        re.fullmatch(
            r"global_polymer_structure_[0-9a-f]{24}",
            row["global_structure_family_key"],
        )
        for row in rows
    )


def test_sciencedb_materializes_three_targets_not_input_features(module) -> None:
    rows = module.build_sciencedb_gold_e_rows()
    assert len(rows) == 1_929
    assert Counter(row["property_name"] for row in rows) == {
        "logYM": 643,
        "logTS": 643,
        "logEB": 643,
    }
    assert {row["record_kind"] for row in rows} == {
        "experimental_target_transformed"
    }
    assert {row["target_origin"] for row in rows} == {"experimental"}
    assert {row["gold_admission_status"] for row in rows} == {
        "conditional_reference"
    }
    assert all(row["training_weight"] == "" for row in rows)
    assert all(len(json.loads(row["condition_value"])) == 20 for row in rows)
    assert not (set(module.SCIENCEDB_INPUT_COLUMNS) & {row["property_name"] for row in rows})


def test_kinetics_materializes_reported_nco_points_without_imputation(module) -> None:
    rows = module.build_kinetics_gold_e_rows()
    assert len(rows) == 171
    assert Counter(row["gold_admission_status"] for row in rows) == {
        "admitted_reference": 169,
        "conditional_reference": 2,
    }
    assert {row["property_name"] for row in rows} == {"NCO_content"}
    assert {row["unit"] for row in rows} == {"%"}
    assert sum(row["value"] == 0 for row in rows) == 23
    assert sum(
        row["protocol_status"] == "measurement_retained_missing_time_no_imputation"
        for row in rows
    ) == 2
    assert all(row["training_weight"] == "" for row in rows)
    assert all("|" not in row["citation_keys"] for row in rows)


def test_gold_e_aggregate_count_and_schema(module) -> None:
    rows = module.build_gold_e_rows()
    assert len(rows) == 2_100
    assert all(tuple(row) == tuple(module.GOLD_E_RECORD_COLUMNS) for row in rows)
    assert all(row["current_weight_materialized"] == "false" for row in rows)
    assert all(row["training_weight"] == "" for row in rows)
