"""第九批 PolyOmics 固定版本数据与分层准入回归测试。"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "代码" / "审计" / "第九批PolyOmics.py"
SOURCE = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第九批计算_PolyOmics"
)
CSV_PATH = SOURCE / "general_polymers_with_sp_abbe_dynamic-dielectric.csv"


def _require_payload() -> None:
    if not CSV_PATH.is_file():
        pytest.skip("PolyOmics 固定原件未在当前检出中分发")


def _load_module():
    spec = importlib.util.spec_from_file_location("batch9_polyomics", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def audited():
    _require_payload()
    module = _load_module()
    return module, module.audit()


def _read_tsv(name: str) -> list[dict[str, str]]:
    with (SOURCE / name).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def test_frozen_revision_payload_license_and_hash_close(audited) -> None:
    module, bundle = audited
    assert module.PINNED_REVISION == "43c8c74cac5bef00e7c3a6cca95a9fab9ba1979c"
    assert module.DATASET_DOI == "10.57967/hf/7475"
    assert module.LICENSE_SPDX == "CC-BY-4.0"
    csv_check = next(row for row in bundle.file_checks if row["name"] == module.CSV_NAME)
    assert csv_check["bytes"] == 190_382_682
    assert csv_check["sha256"] == (
        "e230bd86499559b68b3fd20e7d7fdb538558ccf62463386f981c544953d0c853"
    )
    assert csv_check["verified"] is True


def test_dimensions_and_polyurethane_polyurea_counts_are_exact(audited) -> None:
    _, bundle = audited
    summary = bundle.summary
    assert summary["dimensions"] == {
        "row_count": 95_335,
        "column_count": 255,
        "uuid_unique_count": 95_335,
        "uuid_duplicate_row_count": 0,
    }
    classes = summary["classes"]
    assert classes["class_PURT_row_count"] == 3_384
    assert classes["class_PURA_row_count"] == 3_208
    assert classes["PURT_PURA_union_row_count"] == 6_588
    assert classes["PURT_PURA_overlap_row_count"] == 4
    assert classes["PURA_only_row_count"] == 3_204
    assert classes["non_PURT_row_count"] == 91_951
    assert classes["neither_PURT_nor_PURA_row_count"] == 88_747


def test_gold_c_rows_separate_direct_purt_from_adjacent_pura(audited) -> None:
    module, bundle = audited
    rows = list(bundle.pu_rows)
    assert len(rows) == 6_588
    assert all(set(row) == set(module.PU_REFERENCE_COLUMNS) for row in rows)
    assert Counter(row["gold_admission_status"] for row in rows) == {
        "admitted_reference": 3_384,
        "conditional_reference": 3_204,
    }
    assert sum(
        row["direct_pu_computational_reference"] == "true" for row in rows
    ) == 3_384
    assert all(row["direct_tpu_target_candidate"] == "false" for row in rows)
    assert sum(row["transfer_only"] == "true" for row in rows) == 3_204
    assert {
        row["potential_weight_ceiling"]
        for row in rows
        if row["gold_admission_status"] == "admitted_reference"
    } == {"0.20"}
    assert {
        row["potential_weight_ceiling"]
        for row in rows
        if row["gold_admission_status"] == "conditional_reference"
    } == {"0.10"}
    assert {row["gold_layer"] for row in rows} == {"Gold-C"}
    assert {row["training_weight"] for row in rows} == {""}
    assert all(row["equilibrium_status"] == "verified" for row in rows)
    assert all(row["leakage_group"] == row["structure_key"] for row in rows)


def test_thermal_conductivity_is_gated_separately_from_equilibrium(audited) -> None:
    _, bundle = audited
    thermal = bundle.summary["calculation_checks"]["PU_union"]["thermal_status"]
    assert thermal == {
        "failed_check_target_missing": 1_285,
        "failed_check_value_retained": 43,
        "verified": 5_260,
    }
    rows = list(bundle.pu_rows)
    assert sum(row["thermal_target_admission"] == "admitted_reference" for row in rows) == 2_554
    assert sum(row["thermal_target_admission"] == "conditional_reference" for row in rows) == 2_749
    assert sum(row["thermal_target_admission"] == "not_available" for row in rows) == 1_285
    assert all(
        row["thermal_target_admission"] != "admitted_reference"
        or (row["class_PURT"] == "true" and row["thermal_status"] == "verified")
        for row in rows
    )


def test_property_coverage_preserves_useful_pu_computational_targets(audited) -> None:
    _, bundle = audited
    coverage = bundle.summary["property_coverage"]
    assert coverage["PURT"]["density"] == 3_384
    assert coverage["PURT"]["bulk_modulus"] == 3_383
    assert coverage["PURT"]["thermal_conductivity"] == 2_579
    assert coverage["PURT"]["tg"] == 2_172
    assert coverage["PURT"]["sp_total"] == 2_762
    assert coverage["PURT"]["abbe_number_sos"] == 461
    assert coverage["PURT"]["efdp_permittivity_real"] == 60
    assert coverage["PU_union"]["density"] == 6_588
    assert coverage["PU_union"]["thermal_conductivity"] == 5_303


def test_stable_keys_report_duplicates_without_inflating_new_materials(audited) -> None:
    _, bundle = audited
    rows = list(bundle.pu_rows)
    unique = bundle.summary["uniqueness"]["PU_union"]
    assert len({row["structure_key"] for row in rows}) == unique["structure_key_unique_count"]
    assert len({row["simulation_key"] for row in rows}) == unique["simulation_key_unique_count"]
    assert sum(int(row["independent_material_increment_within_source"]) for row in rows) == unique[
        "structure_key_unique_count"
    ]
    assert all(row["structure_key"].startswith("polyomics_structure_") for row in rows)
    assert all(row["simulation_key"].startswith("polyomics_simulation_") for row in rows)
    assert bundle.summary["gold_c_reference"]["cross_source_structure_increment_requires_global_dedup"] is True
    assert bundle.summary["gold_c_reference"]["general_polymer_rows_materialized_as_tpu"] == 0


def test_outputs_are_reproducible_atomic_and_no_training_set_is_created(audited) -> None:
    module, bundle = audited
    first = module.render_outputs(bundle)
    second = module.render_outputs(module.audit())
    assert first == second
    assert set(first) == set(module.OUTPUT_NAMES)
    for name, payload in first.items():
        assert (SOURCE / name).read_bytes() == payload
    assert json.loads(first["数据审计摘要.json"].decode("utf-8"))[
        "gold_c_reference"
    ]["training_weight_materialized"] is False

    before = {
        name: hashlib.sha256((SOURCE / name).read_bytes()).hexdigest()
        for name in module.OUTPUT_NAMES
    }
    subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, check=True)
    after = {
        name: hashlib.sha256((SOURCE / name).read_bytes()).hexdigest()
        for name in module.OUTPUT_NAMES
    }
    assert before == after
    script_text = SCRIPT.read_text(encoding="utf-8")
    assert "os.replace" in script_text
    assert ".write_text(" not in script_text
    assert "不生成训练集" in script_text
