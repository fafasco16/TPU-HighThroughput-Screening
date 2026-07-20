from __future__ import annotations

import csv
import hashlib
import importlib.util
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "代码" / "审计" / "SMiPoly_TPU候选分类.py"
OUTPUT = ROOT / "结果" / "Gold_候选.csv"


def _load_module():
    spec = importlib.util.spec_from_file_location("smipoly_tpu_candidate_audit", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def candidate_rows():
    return _load_module().build_candidate_rows()


def test_all_smipoly_candidates_have_valid_unique_structures(candidate_rows):
    assert len(candidate_rows) == 1071
    assert len({row["candidate_id"] for row in candidate_rows}) == 1071
    assert len({row["canonical_smiles"] for row in candidate_rows}) == 1071
    assert len({row["inchikey"] for row in candidate_rows}) == 1071
    assert all(row["structure_status"] == "rdkit_validated" for row in candidate_rows)
    assert all(row["duplicate_status"] == "canonical_unique" for row in candidate_rows)
    assert all(row["license_spdx"] == "BSD-3-Clause" for row in candidate_rows)


def test_role_rules_are_frozen_and_do_not_claim_property_truth(candidate_rows):
    expected = {
        "unclassified": 531,
        "diamine_chain_extender_candidate": 141,
        "diol_chain_extender_candidate": 83,
        "polyester_polyol_precursor": 77,
        "epoxy_polyol_precursor": 70,
        "macrodiol_polyol_candidate": 58,
        "monool_model_compound": 38,
        "polythiol_adjacent_candidate": 27,
        "di_polyisocyanate_candidate": 17,
        "polyol_crosslinker_candidate": 13,
        "monoamine_model_compound": 10,
        "cyclic_carbonate_nipu_precursor": 4,
        "monoisocyanate_model_compound": 2,
    }
    assert Counter(row["tpu_role"] for row in candidate_rows) == expected
    assert sum(row["screening_priority"] == 1 for row in candidate_rows) == 312
    assert sum(bool(row["functional_group_match"]) for row in candidate_rows) == 540
    assert all(row["data_origin"] == "reaction_rule_generated" for row in candidate_rows)
    assert all(row["gold_layer"] == "Gold-V" for row in candidate_rows)
    assert all(
        row["gold_admission_status"] == "admitted_reference"
        for row in candidate_rows
    )
    assert all(
        row["direct_property_supervision_weight_ceiling"] == 0.0
        for row in candidate_rows
    )
    assert all(row["prediction_uncertainty"] == "" for row in candidate_rows)


def test_known_reference_structures_receive_auditable_roles(candidate_rows):
    by_source_record = {row["source_record_id"]: row for row in candidate_rows}
    ethylene_glycol = by_source_record["CID174"]
    assert ethylene_glycol["canonical_smiles"] == "OCCO"
    assert ethylene_glycol["hydroxyl_group_count"] == 2
    assert ethylene_glycol["tpu_role"] == "diol_chain_extender_candidate"

    adipic_acid = by_source_record["CID196"]
    assert adipic_acid["carboxylic_acid_group_count"] == 2
    assert adipic_acid["tpu_role"] == "polyester_polyol_precursor"

    cadaverine = by_source_record["CID273"]
    assert cadaverine["amine_group_count"] == 2
    assert cadaverine["tpu_role"] == "diamine_chain_extender_candidate"


def test_candidate_csv_is_byte_reproducible(candidate_rows):
    module = _load_module()
    module.write_candidate_csv(candidate_rows)
    first = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, check=True)
    second = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    assert first == second

    with OUTPUT.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1071
    assert list(rows[0]) == module.CANDIDATE_COLUMNS
