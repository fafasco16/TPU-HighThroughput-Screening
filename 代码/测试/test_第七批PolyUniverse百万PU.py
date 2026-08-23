"""PolyUniverse 百万级 PU 虚拟数据的轻量回归门禁。

测试只读取已物化的摘要/TSV，并对原始 CSV 做流式哈希；不会重跑百万行
RDKit 审计。原始大文件未分发时，相关测试按预期跳过。
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第七批虚拟_PolyUniverse百万PU"
)
RAW = DATA_DIR / "Polyurethane_1M_p.csv"
META = DATA_DIR / "Zenodo元数据.json"
SUMMARY = DATA_DIR / "审计摘要.json"
COLUMN_TSV = DATA_DIR / "列统计.tsv"
LABEL_TSV = DATA_DIR / "预测标签分布.tsv"
SMILES_TSV = DATA_DIR / "SMILES质量.tsv"
FIELD_DICTIONARY = DATA_DIR / "字段字典.tsv"

EXPECTED_FIELDS = [
    "Smiles",
    "Smiles_Compound_1",
    "Smiles_Compound_2",
    "Tg",
    "DC",
    "PL",
    "Eg",
    "YS",
    "YM",
    "BS",
    "He",
    "H2",
    "O2",
    "N2",
    "CO2",
    "CH4",
    "Tm",
    "Td",
]


def _require_materialized() -> dict:
    required = [META, SUMMARY, COLUMN_TSV, LABEL_TSV, SMILES_TSV, FIELD_DICTIONARY]
    if not all(path.is_file() for path in required):
        pytest.skip("PolyUniverse 物化审计结果未在当前检出中分发")
    return json.loads(SUMMARY.read_text(encoding="utf-8"))


def _stream_hash(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def test_summary_freezes_true_table_shape_and_completeness() -> None:
    summary = _require_materialized()
    assert summary["table"] == {
        "encoding": "utf-8-sig compatible",
        "data_rows": 1_000_000,
        "columns": 18,
        "fields": EXPECTED_FIELDS,
        "empty_rows_ignored": 0,
        "irregular_rows": 0,
    }
    assert summary["column_missing"] == {field: 0 for field in EXPECTED_FIELDS}
    assert summary["duplicates"]["full_row_duplicate_occurrences"] == 0
    assert summary["duplicates"]["full_row_unique_hashes"] == 1_000_000
    assert summary["duplicates"]["compound_pair_unique_hashes"] == 296_670
    assert summary["duplicates"]["compound_pair_duplicate_occurrences"] == 703_330


def test_raw_and_canonical_smiles_contracts_are_distinct_and_frozen() -> None:
    summary = _require_materialized()
    expected = {
        "Smiles": (1_000_000, 1_000_000),
        "Smiles_Compound_1": (32, 32),
        "Smiles_Compound_2": (13_111, 13_111),
    }
    for field, (raw_unique, canonical_unique) in expected.items():
        info = summary["smiles"][field]
        assert info["unique_raw_strings"] == raw_unique
        assert info["unique_rdkit_canonical_strings"] == canonical_unique
        assert info["rdkit_valid_row_rate"] == 1.0
        assert info["rdkit_valid_unique_rate"] == 1.0

    wildcards = summary["p_smiles_wildcards"]
    assert wildcards["wildcard_count_distribution_rows"] == {"2": 1_000_000}
    assert wildcards["exactly_two_wildcards_rows"] == 1_000_000
    assert wildcards["exactly_two_wildcards_rate"] == 1.0
    assert wildcards["mismatch_examples_max20"] == []

    monomers = summary["monomer_identity"]
    assert monomers["unique_raw_strings_across_compound_columns"] == 13_143
    assert monomers["unique_rdkit_canonical_strings_across_compound_columns"] == 13_143
    assert monomers["unique_dot_separated_fragments_across_compound_columns"] == 13_153
    assert monomers["unique_rdkit_canonical_fragments_across_compound_columns"] == 13_153
    assert monomers["rdkit_invalid_dot_separated_fragments"] == 0


def test_prediction_labels_are_complete_numeric_model_outputs() -> None:
    summary = _require_materialized()
    labels = summary["prediction_labels"]
    assert list(labels) == EXPECTED_FIELDS[3:]
    for stats in labels.values():
        assert stats["count"] == 1_000_000
        assert stats["missing"] == 0
        assert stats["non_numeric"] == 0
        assert stats["non_finite"] == 0
        assert stats["min"] <= stats["q0.01"] <= stats["q0.50"] <= stats["q0.99"] <= stats["max"]

    assert summary["scientific_status"]["measurement_type"] == "model_prediction"
    assert summary["scientific_status"]["recommended_tier"] == "Gold-V"
    assert summary["scientific_status"]["direct_property_supervision_weight_ceiling"] == 0.0
    assert summary["scientific_status"]["use_note"] == (
        "zero direct property supervision; allowed for candidate ranking/active learning/representation only"
    )
    assert summary["scientific_status"]["gas_prediction_domain"].startswith("OOD:")
    assert "NCO/OH 当量比" in summary["scientific_status"]["known_missing_context"]
    assert "性能测试条件" in summary["scientific_status"]["known_missing_context"]


def test_materialized_tsvs_match_summary_shape() -> None:
    _require_materialized()
    columns = _read_tsv(COLUMN_TSV)
    labels = _read_tsv(LABEL_TSV)
    smiles = _read_tsv(SMILES_TSV)
    assert [row["字段"] for row in columns] == EXPECTED_FIELDS
    assert [row["字段"] for row in labels] == EXPECTED_FIELDS[3:]
    assert [row["字段"] for row in smiles] == EXPECTED_FIELDS[:3]
    assert all(row["缺失"] == "0" for row in columns)
    assert smiles[0]["unique_rdkit_canonical_strings"] == "1000000"
    assert smiles[0]["exactly_two_wildcards_rows"] == "1000000"


def test_field_units_ood_status_and_physical_qc_are_frozen() -> None:
    summary = _require_materialized()
    rows = _read_tsv(FIELD_DICTIONARY)
    assert [row["field"] for row in rows] == EXPECTED_FIELDS
    fields = {row["field"]: row for row in rows}
    for field in EXPECTED_FIELDS[3:]:
        assert fields[field]["gold_v_policy"] == (
            "zero direct property supervision; allowed for candidate ranking/active learning/representation only"
        )

    assert fields["Smiles"]["role"] == "polymer_repeat_unit_p_smiles"
    assert fields["Smiles_Compound_1"]["role"] == "reactant_smiles_1"
    assert fields["Smiles_Compound_2"]["role"] == "reactant_or_multicomponent_smiles_2"
    for field in ("Tg", "Tm", "Td"):
        assert fields[field]["resolved_unit"] == "degC"
        assert fields[field]["conversion_from_raw"] == f"{field}_degC = {field}_raw"
    assert fields["YM"]["resolved_unit"] == "GPa"
    assert fields["YM"]["conversion_from_raw"] == "YM_GPa = 10 * YM_raw"
    for field in ("YS", "BS"):
        assert fields[field]["resolved_unit"] == "MPa"
        assert fields[field]["conversion_from_raw"] == f"{field}_MPa = 1000 * {field}_raw"
    for field in ("YM", "YS", "BS"):
        assert fields[field]["unit_status"] == "resolved_for_official_Polyurethane_1M_p_csv_only"
        assert "scope limited to official Polyurethane_1M_p.csv" in fields[field]["evidence"]
    for field in ("DC", "PL", "Eg"):
        assert fields[field]["resolved_unit"] == "unresolved"
        assert fields[field]["unit_status"] == "unresolved_no_dataset_field_dictionary"
    for field in ("He", "H2", "O2", "N2", "CO2", "CH4"):
        assert fields[field]["raw_storage_semantics"] == "log10(P/Barrer)"
        assert fields[field]["resolved_unit"] == "log10(Barrer)"
        assert fields[field]["conversion_from_raw"] == f"P_{field}_Barrer = 10 ** {field}_raw"
        assert fields[field]["pu_applicability_domain"] == "OOD_paper_did_not_validate_generated_polyurethane"

    assert summary["field_semantics"] == {
        row["field"]: {key: value for key, value in row.items() if key != "field"}
        for row in rows
    }
    qc = summary["physical_consistency_qc"]
    assert qc["denominator_rows"] == 1_000_000
    assert qc["counts_source"] == "independent_full_numeric_scan_2026-07-21"
    assert {key: value["count"] for key, value in qc["checks"].items()} == {
        "YM_raw_lt_0": 849,
        "YS_raw_lt_0": 12,
        "BS_raw_lt_0": 0,
        "Tg_gt_Tm": 13_775,
        "Tm_gt_Td": 85_777,
        "Tg_gt_Td": 223,
        "YS_gt_BS": 344_223,
    }


def test_official_identity_hash_and_dataset_license() -> None:
    summary = _require_materialized()
    if not RAW.is_file():
        pytest.skip("310 MB 原始 CSV 未在当前检出中分发")
    metadata = json.loads(META.read_text(encoding="utf-8"))
    assert metadata["id"] == 12_585_902
    assert metadata["doi"] == "10.5281/zenodo.12585902"
    assert metadata["metadata"]["license"]["id"] == "cc-by-4.0"
    assert RAW.stat().st_size == 310_521_077
    assert _stream_hash(RAW, "md5") == "29deab9b99cf91c9a4e863b7a277bb53"
    assert _stream_hash(RAW, "sha256") == (
        "fc7735757238f25df52e20b7e1a556e07319346c5df7511f0a52146fca1b967e"
    )
    assert summary["file_integrity"]["matches_official"] is True
