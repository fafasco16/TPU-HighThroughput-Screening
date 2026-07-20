#!/usr/bin/env python3
"""对 PolyUniverse 百万级 PU CSV 做可复现的流式质量审计。

脚本不修改原始 CSV，只在同目录生成轻量 JSON/TSV 统计文件。
RDKit 为可选依赖：环境已有时执行全量 SMILES 解析，否则明确记录未执行。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import statistics
import sys
import tempfile
import time
from array import array
from collections import Counter
from pathlib import Path
from typing import Any


RAW_NAME = "Polyurethane_1M_p.csv"
META_NAME = "Zenodo元数据.json"
SUMMARY_NAME = "审计摘要.json"
COLUMN_NAME = "列统计.tsv"
LABEL_NAME = "预测标签分布.tsv"
SMILES_NAME = "SMILES质量.tsv"
FIELD_DICTIONARY_NAME = "字段字典.tsv"

SMILES_COLUMNS = ("Smiles", "Smiles_Compound_1", "Smiles_Compound_2")
MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "-"}
QUANTILES = (0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99)
EXPECTED_ZENODO_ID = 12585902
EXPECTED_SIZE = 310_521_077
EXPECTED_MD5 = "29deab9b99cf91c9a4e863b7a277bb53"
INDEPENDENT_FULL_SCAN_QC = {
    "YM_raw_lt_0": 849,
    "YS_raw_lt_0": 12,
    "BS_raw_lt_0": 0,
    "Tg_gt_Tm": 13_775,
    "Tm_gt_Td": 85_777,
    "Tg_gt_Td": 223,
    "YS_gt_BS": 344_223,
}


def build_field_dictionary_rows() -> list[dict[str, str]]:
    """返回固定字段语义；raw 值与可证单位换算保持分离。"""

    rows = [
        {
            "field": "Smiles",
            "role": "polymer_repeat_unit_p_smiles",
            "value_origin": "rule_generated_structure",
            "raw_storage_semantics": "p-SMILES with two wildcard atoms",
            "resolved_unit": "not_applicable",
            "conversion_from_raw": "identity",
            "unit_status": "not_applicable",
            "pu_applicability_domain": "candidate_structure_not_TPU_class_label",
            "gold_v_policy": "structure_reference_and_candidate_generation",
            "evidence": "Zenodo CSV header; full RDKit canonical/wildcard audit",
        },
        {
            "field": "Smiles_Compound_1",
            "role": "reactant_smiles_1",
            "value_origin": "rule_generation_input",
            "raw_storage_semantics": "reactant SMILES string",
            "resolved_unit": "not_applicable",
            "conversion_from_raw": "RDKit canonicalization only",
            "unit_status": "not_applicable",
            "pu_applicability_domain": "identity_only_no_stoichiometry",
            "gold_v_policy": "group_split_and_reaction_feasibility_reference",
            "evidence": "Zenodo CSV header; PolyUniverse generation workflow",
        },
        {
            "field": "Smiles_Compound_2",
            "role": "reactant_or_multicomponent_smiles_2",
            "value_origin": "rule_generation_input",
            "raw_storage_semantics": "dot-separated reactant/component SMILES string allowed",
            "resolved_unit": "not_applicable",
            "conversion_from_raw": "RDKit canonicalization; dot split only for identity audit",
            "unit_status": "not_applicable",
            "pu_applicability_domain": "identity_only_no_stoichiometry",
            "gold_v_policy": "group_split_and_reaction_feasibility_reference",
            "evidence": "Zenodo CSV content; full component audit",
        },
    ]

    thermal = {"Tg", "Tm", "Td"}
    unresolved = {"DC", "PL", "Eg"}
    mechanical = {
        "YS": ("MPa", "YS_MPa = 1000 * YS_raw", "score * 1000"),
        "YM": ("GPa", "YM_GPa = 10 * YM_raw", "score * 10"),
        "BS": ("MPa", "BS_MPa = 1000 * BS_raw", "score * 1000"),
    }
    gases = {"He", "H2", "O2", "N2", "CO2", "CH4"}

    for field in ["Tg", "DC", "PL", "Eg", "YS", "YM", "BS", "He", "H2", "O2", "N2", "CO2", "CH4", "Tm", "Td"]:
        base = {
            "field": field,
            "role": "model_predicted_property",
            "value_origin": "model_prediction_not_experiment",
            "gold_v_policy": "zero direct property supervision; allowed for candidate ranking/active learning/representation only",
        }
        if field in thermal:
            base.update(
                {
                    "raw_storage_semantics": "published raw numeric",
                    "resolved_unit": "degC",
                    "conversion_from_raw": f"{field}_degC = {field}_raw",
                    "unit_status": "resolved_identity_from_companion_code",
                    "pu_applicability_domain": "model_applicability_domain_not_quantified",
                    "evidence": "PolyUniverse Prediction/Thermal_Property.py",
                }
            )
        elif field in unresolved:
            base.update(
                {
                    "raw_storage_semantics": "published raw numeric",
                    "resolved_unit": "unresolved",
                    "conversion_from_raw": "unresolved",
                    "unit_status": "unresolved_no_dataset_field_dictionary",
                    "pu_applicability_domain": "model_applicability_domain_not_quantified",
                    "evidence": "No unit dictionary in Zenodo record, CSV header, or repository README",
                }
            )
        elif field in mechanical:
            unit, transform, code_transform = mechanical[field]
            base.update(
                {
                    "raw_storage_semantics": "official Polyurethane_1M_p.csv model raw score before display scaling",
                    "resolved_unit": unit,
                    "conversion_from_raw": transform,
                    "unit_status": "resolved_for_official_Polyurethane_1M_p_csv_only",
                    "pu_applicability_domain": "model_applicability_domain_not_quantified",
                    "evidence": (
                        "PolyUniverse commit 381efe43 Prediction/Mechanical_Property.py L59-L71: "
                        f"{code_transform}; scope limited to official Polyurethane_1M_p.csv, not PolyInfo_p.csv"
                    ),
                }
            )
        elif field in gases:
            base.update(
                {
                    "raw_storage_semantics": "log10(P/Barrer)",
                    "resolved_unit": "log10(Barrer)",
                    "conversion_from_raw": f"P_{field}_Barrer = 10 ** {field}_raw",
                    "unit_status": "resolved_model_target_definition",
                    "pu_applicability_domain": "OOD_paper_did_not_validate_generated_polyurethane",
                    "evidence": "PolyUniverse gas-permeability model target definition and companion code",
                }
            )
        rows.append(base)
    return rows


def field_semantics_map() -> dict[str, dict[str, str]]:
    return {
        row["field"]: {key: value for key, value in row.items() if key != "field"}
        for row in build_field_dictionary_rows()
    }


def physical_consistency_summary(
    counts: Counter[str] | dict[str, int], denominator: int, source: str
) -> dict[str, Any]:
    return {
        "denominator_rows": denominator,
        "counts_source": source,
        "checks": {
            key: {"count": int(value), "rate": safe_rate(int(value), denominator)}
            for key, value in counts.items()
        },
        "policy": "flag in candidate ranking; zero direct property supervision; do not silently delete or reinterpret raw predictions",
    }


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def is_missing(value: str) -> bool:
    return value.strip().lower() in MISSING_TOKENS


def file_hash(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_tsv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def safe_rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="数据目录；默认定位到项目内第七批虚拟_PolyUniverse百万PU。",
    )
    parser.add_argument(
        "--overwrite-results",
        action="store_true",
        help="仅覆盖本脚本生成的轻量统计文件；绝不覆盖原始 CSV 或 Zenodo 元数据。",
    )
    parser.add_argument(
        "--refresh-lightweight-semantics",
        action="store_true",
        help="不重跑百万行：向既有摘要加入已独立全量复核的单位语义/QC，并生成字段字典。",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    base = (
        args.data_dir.resolve()
        if args.data_dir is not None
        else project_root / "数据" / "原始" / "外部数据" / "新增开放数据" / "第七批虚拟_PolyUniverse百万PU"
    )
    raw_path = base / RAW_NAME
    meta_path = base / META_NAME
    field_dictionary_path = base / FIELD_DICTIONARY_NAME
    output_paths = [
        base / SUMMARY_NAME,
        base / COLUMN_NAME,
        base / LABEL_NAME,
        base / SMILES_NAME,
        field_dictionary_path,
    ]

    if args.refresh_lightweight_semantics:
        summary_path = base / SUMMARY_NAME
        if not summary_path.is_file():
            raise FileNotFoundError("缺少既有审计摘要，不能执行轻量语义刷新。")
        existing_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        denominator = int(existing_summary.get("table", {}).get("data_rows", 0))
        if denominator != 1_000_000:
            raise ValueError(f"既有摘要行数异常：{denominator}")
        existing_summary["field_semantics"] = field_semantics_map()
        existing_summary["physical_consistency_qc"] = physical_consistency_summary(
            INDEPENDENT_FULL_SCAN_QC,
            denominator,
            "independent_full_numeric_scan_2026-07-21",
        )
        existing_summary.setdefault("scientific_status", {})["gas_prediction_domain"] = (
            "OOD: associated paper did not validate generated polyurethane gas predictions"
        )
        existing_summary["scientific_status"]["direct_property_supervision_weight_ceiling"] = 0.0
        existing_summary["scientific_status"]["use_note"] = (
            "zero direct property supervision; allowed for candidate ranking/active learning/representation only"
        )
        atomic_write_json(summary_path, existing_summary)
        write_tsv(
            field_dictionary_path,
            [
                "field", "role", "value_origin", "raw_storage_semantics", "resolved_unit",
                "conversion_from_raw", "unit_status", "pu_applicability_domain",
                "gold_v_policy", "evidence",
            ],
            build_field_dictionary_rows(),
        )
        print(json.dumps({
            "refreshed": [SUMMARY_NAME, FIELD_DICTIONARY_NAME],
            "rows_reprocessed": 0,
            "qc_source": "independent_full_numeric_scan_2026-07-21",
        }, ensure_ascii=False, indent=2))
        return 0

    if not raw_path.is_file() or not meta_path.is_file():
        raise FileNotFoundError("缺少原始 CSV 或 Zenodo 元数据。")
    existing = [str(path) for path in output_paths if path.exists()]
    if existing and not args.overwrite_results:
        raise FileExistsError("结果文件已存在，拒绝覆盖：" + ", ".join(existing))

    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    if metadata.get("id") != EXPECTED_ZENODO_ID:
        raise ValueError(f"Zenodo record id 不符：{metadata.get('id')!r}")
    official_file = next((item for item in metadata.get("files", []) if item.get("key") == RAW_NAME), None)
    if not official_file:
        raise ValueError("Zenodo 元数据中找不到目标 CSV。")

    actual_size = raw_path.stat().st_size
    actual_md5 = file_hash(raw_path, "md5")
    actual_sha256 = file_hash(raw_path, "sha256")
    official_size = int(official_file["size"])
    official_md5 = str(official_file["checksum"]).split(":", 1)[-1].lower()
    integrity_ok = (
        actual_size == official_size == EXPECTED_SIZE
        and actual_md5 == official_md5 == EXPECTED_MD5
    )
    if not integrity_ok:
        raise ValueError("原始 CSV 的大小或 MD5 与官方 Zenodo 元数据不一致。")

    rdkit_available = False
    rdkit_version = None
    Chem = None
    try:
        import rdkit  # type: ignore
        from rdkit import Chem as _Chem  # type: ignore
        from rdkit import RDLogger  # type: ignore

        RDLogger.DisableLog("rdApp.*")
        Chem = _Chem
        rdkit_available = True
        rdkit_version = getattr(rdkit, "__version__", "unknown")
    except ImportError:
        pass

    try:
        import numpy as np  # type: ignore
    except ImportError:
        np = None

    started = time.time()
    row_count = 0
    irregular_row_count = 0
    empty_row_count = 0
    duplicate_row_count = 0
    duplicate_pair_count = 0
    full_row_hashes: set[bytes] = set()
    pair_hashes: set[bytes] = set()
    column_missing: Counter[str] = Counter()
    numeric_non_finite: Counter[str] = Counter()
    numeric_non_numeric: Counter[str] = Counter()
    physical_qc: Counter[str] = Counter({key: 0 for key in INDEPENDENT_FULL_SCAN_QC})
    numeric_values: dict[str, array] = {}
    parse_cache: dict[str, dict[str, bool]] = {column: {} for column in SMILES_COLUMNS}
    canonical_smiles_sets: dict[str, set[str]] = {column: set() for column in SMILES_COLUMNS}
    wildcard_count_cache: dict[str, int] = {}
    wildcard_count_rows: Counter[int] = Counter()
    wildcard_mismatch_examples: list[str] = []
    smiles_nonmissing: Counter[str] = Counter()
    smiles_valid_rows: Counter[str] = Counter()
    invalid_examples: dict[str, list[str]] = {column: [] for column in SMILES_COLUMNS}
    monomer_raw_union: set[str] = set()
    monomer_fragment_union: set[str] = set()

    with raw_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            headers = next(reader)
        except StopIteration as exc:
            raise ValueError("CSV 为空。") from exc
        if len(headers) != len(set(headers)):
            raise ValueError("CSV 存在重复列名。")
        missing_required = [column for column in SMILES_COLUMNS if column not in headers]
        if missing_required:
            raise ValueError("缺少 SMILES 列：" + ", ".join(missing_required))
        numeric_columns = [column for column in headers if column not in SMILES_COLUMNS]
        numeric_values = {column: array("d") for column in numeric_columns}
        index = {column: headers.index(column) for column in headers}

        for row in reader:
            if not row or all(not cell.strip() for cell in row):
                empty_row_count += 1
                continue
            row_count += 1
            if len(row) != len(headers):
                irregular_row_count += 1
                if len(row) < len(headers):
                    row = row + [""] * (len(headers) - len(row))
                else:
                    row = row[: len(headers)]

            row_digest = hashlib.blake2b("\x1f".join(row).encode("utf-8"), digest_size=16).digest()
            if row_digest in full_row_hashes:
                duplicate_row_count += 1
            else:
                full_row_hashes.add(row_digest)

            pair_text = row[index["Smiles_Compound_1"]] + "\x1f" + row[index["Smiles_Compound_2"]]
            pair_digest = hashlib.blake2b(pair_text.encode("utf-8"), digest_size=16).digest()
            if pair_digest in pair_hashes:
                duplicate_pair_count += 1
            else:
                pair_hashes.add(pair_digest)

            for column, value in zip(headers, row):
                if is_missing(value):
                    column_missing[column] += 1

            for column in SMILES_COLUMNS:
                value = row[index[column]].strip()
                if is_missing(value):
                    continue
                smiles_nonmissing[column] += 1
                cache = parse_cache[column]
                parsed = cache.get(value)
                if parsed is None:
                    mol = Chem.MolFromSmiles(value) if rdkit_available and Chem is not None else None
                    parsed = mol is not None
                    cache[value] = parsed
                    if parsed and mol is not None:
                        canonical_smiles_sets[column].add(Chem.MolToSmiles(mol, canonical=True))
                        if column == "Smiles":
                            wildcard_count_cache[value] = sum(
                                1 for atom in mol.GetAtoms() if atom.GetAtomicNum() == 0
                            )
                    if rdkit_available and not parsed and len(invalid_examples[column]) < 20:
                        invalid_examples[column].append(value)
                if rdkit_available and parsed:
                    smiles_valid_rows[column] += 1
                    if column == "Smiles":
                        wildcard_count = wildcard_count_cache[value]
                        wildcard_count_rows[wildcard_count] += 1
                        if wildcard_count != 2 and len(wildcard_mismatch_examples) < 20:
                            wildcard_mismatch_examples.append(value)

            compound_1 = row[index["Smiles_Compound_1"]].strip()
            compound_2 = row[index["Smiles_Compound_2"]].strip()
            for raw_monomer in (compound_1, compound_2):
                if is_missing(raw_monomer):
                    continue
                monomer_raw_union.add(raw_monomer)
                for fragment in raw_monomer.split("."):
                    fragment = fragment.strip()
                    if fragment:
                        monomer_fragment_union.add(fragment)

            row_numeric: dict[str, float] = {}
            for column in numeric_columns:
                value = row[index[column]].strip()
                if is_missing(value):
                    continue
                try:
                    number = float(value)
                except ValueError:
                    numeric_non_numeric[column] += 1
                    continue
                if not math.isfinite(number):
                    numeric_non_finite[column] += 1
                    continue
                numeric_values[column].append(number)
                row_numeric[column] = number

            if row_numeric.get("YM", 0.0) < 0:
                physical_qc["YM_raw_lt_0"] += 1
            if row_numeric.get("YS", 0.0) < 0:
                physical_qc["YS_raw_lt_0"] += 1
            if row_numeric.get("BS", 0.0) < 0:
                physical_qc["BS_raw_lt_0"] += 1
            if {"Tg", "Tm"}.issubset(row_numeric) and row_numeric["Tg"] > row_numeric["Tm"]:
                physical_qc["Tg_gt_Tm"] += 1
            if {"Tm", "Td"}.issubset(row_numeric) and row_numeric["Tm"] > row_numeric["Td"]:
                physical_qc["Tm_gt_Td"] += 1
            if {"Tg", "Td"}.issubset(row_numeric) and row_numeric["Tg"] > row_numeric["Td"]:
                physical_qc["Tg_gt_Td"] += 1
            if {"YS", "BS"}.issubset(row_numeric) and row_numeric["YS"] > row_numeric["BS"]:
                physical_qc["YS_gt_BS"] += 1

            if row_count % 100_000 == 0:
                print(
                    f"processed_rows={row_count:,}; elapsed_seconds={time.time() - started:.1f}",
                    flush=True,
                )

    label_rows: list[dict[str, Any]] = []
    label_stats: dict[str, dict[str, Any]] = {}
    for column in numeric_columns:
        values = numeric_values[column]
        count = len(values)
        stats: dict[str, Any] = {
            "count": count,
            "missing": int(column_missing[column]),
            "non_numeric": int(numeric_non_numeric[column]),
            "non_finite": int(numeric_non_finite[column]),
            "min": None,
            "max": None,
            "mean": None,
            "std_population": None,
            "std_sample": None,
        }
        if count:
            if np is not None:
                data = np.frombuffer(values, dtype=np.float64)
                quantile_values = np.quantile(data, QUANTILES, method="linear")
                stats.update(
                    {
                        "min": float(np.min(data)),
                        "max": float(np.max(data)),
                        "mean": float(np.mean(data)),
                        "std_population": float(np.std(data, ddof=0)),
                        "std_sample": float(np.std(data, ddof=1)) if count > 1 else None,
                    }
                )
                for quantile, value in zip(QUANTILES, quantile_values):
                    stats[f"q{quantile:.2f}"] = float(value)
            else:
                ordered = sorted(values)
                stats.update(
                    {
                        "min": ordered[0],
                        "max": ordered[-1],
                        "mean": statistics.fmean(ordered),
                        "std_population": statistics.pstdev(ordered),
                        "std_sample": statistics.stdev(ordered) if count > 1 else None,
                    }
                )
                for quantile in QUANTILES:
                    position = quantile * (count - 1)
                    lower = math.floor(position)
                    upper = math.ceil(position)
                    if lower == upper:
                        value = ordered[lower]
                    else:
                        weight = position - lower
                        value = ordered[lower] * (1 - weight) + ordered[upper] * weight
                    stats[f"q{quantile:.2f}"] = value
        label_stats[column] = stats
        label_rows.append({"字段": column, **stats})

    smiles_rows: list[dict[str, Any]] = []
    smiles_stats: dict[str, dict[str, Any]] = {}
    for column in SMILES_COLUMNS:
        nonmissing = int(smiles_nonmissing[column])
        unique_count = len(parse_cache[column])
        valid_rows = int(smiles_valid_rows[column]) if rdkit_available else None
        valid_unique = sum(parse_cache[column].values()) if rdkit_available else None
        info = {
            "nonmissing_rows": nonmissing,
            "missing_rows": int(column_missing[column]),
            "unique_raw_strings": unique_count,
            "unique_rdkit_canonical_strings": len(canonical_smiles_sets[column]) if rdkit_available else None,
            "duplicate_occurrences": nonmissing - unique_count,
            "duplicate_rate": safe_rate(nonmissing - unique_count, nonmissing),
            "rdkit_checked": rdkit_available,
            "rdkit_valid_rows": valid_rows,
            "rdkit_valid_row_rate": safe_rate(valid_rows, nonmissing) if rdkit_available else None,
            "rdkit_valid_unique": valid_unique,
            "rdkit_valid_unique_rate": safe_rate(valid_unique, unique_count) if rdkit_available else None,
            "invalid_examples": invalid_examples[column],
            "exactly_two_wildcards_rows": int(wildcard_count_rows[2]) if rdkit_available and column == "Smiles" else None,
            "exactly_two_wildcards_rate": safe_rate(int(wildcard_count_rows[2]), valid_rows) if rdkit_available and column == "Smiles" else None,
        }
        smiles_stats[column] = info
        smiles_rows.append({
            "字段": column,
            **{key: value for key, value in info.items() if key != "invalid_examples"},
            "无效示例_最多20条": " || ".join(invalid_examples[column]),
        })

    column_rows: list[dict[str, Any]] = []
    for column in headers:
        missing = int(column_missing[column])
        column_rows.append(
            {
                "字段": column,
                "角色": "SMILES" if column in SMILES_COLUMNS else "模型预测标签",
                "总行数": row_count,
                "非缺失": row_count - missing,
                "缺失": missing,
                "缺失率": safe_rate(missing, row_count),
                "唯一原始字符串": len(parse_cache[column]) if column in SMILES_COLUMNS else "",
                "非数值": int(numeric_non_numeric[column]) if column in numeric_columns else "",
                "非有限值": int(numeric_non_finite[column]) if column in numeric_columns else "",
            }
        )

    elapsed = time.time() - started
    canonical_monomer_union: set[str] = set()
    canonical_monomer_fragment_union: set[str] = set()
    invalid_monomer_fragments = 0
    if rdkit_available and Chem is not None:
        canonical_monomer_union.update(canonical_smiles_sets["Smiles_Compound_1"])
        canonical_monomer_union.update(canonical_smiles_sets["Smiles_Compound_2"])
        for fragment in monomer_fragment_union:
            mol = Chem.MolFromSmiles(fragment)
            if mol is None:
                invalid_monomer_fragments += 1
            else:
                canonical_monomer_fragment_union.add(Chem.MolToSmiles(mol, canonical=True))

    summary = {
        "audit_schema_version": "1.0",
        "generated_at_local": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_seconds": elapsed,
        "source": {
            "record_title": metadata.get("metadata", {}).get("title"),
            "record_id": metadata.get("id"),
            "doi": metadata.get("doi"),
            "concept_doi": metadata.get("conceptdoi"),
            "record_url": metadata.get("links", {}).get("html"),
            "download_url": official_file.get("links", {}).get("self"),
            "license": metadata.get("metadata", {}).get("license", {}).get("id"),
            "creator": metadata.get("metadata", {}).get("creators"),
            "publication_date": metadata.get("metadata", {}).get("publication_date"),
        },
        "file_integrity": {
            "file": RAW_NAME,
            "size_bytes": actual_size,
            "official_size_bytes": official_size,
            "md5": actual_md5,
            "official_md5": official_md5,
            "sha256": actual_sha256,
            "matches_official": integrity_ok,
        },
        "table": {
            "encoding": "utf-8-sig compatible",
            "data_rows": row_count,
            "columns": len(headers),
            "fields": headers,
            "empty_rows_ignored": empty_row_count,
            "irregular_rows": irregular_row_count,
        },
        "duplicates": {
            "full_row_duplicate_occurrences": duplicate_row_count,
            "full_row_unique_hashes": len(full_row_hashes),
            "full_row_method": "BLAKE2b-128 over parsed cell values; cryptographic collision risk negligible",
            "compound_pair_duplicate_occurrences": duplicate_pair_count,
            "compound_pair_unique_hashes": len(pair_hashes),
            "compound_pair_method": "BLAKE2b-128 over Smiles_Compound_1 + delimiter + Smiles_Compound_2",
        },
        "column_missing": {column: int(column_missing[column]) for column in headers},
        "smiles": smiles_stats,
        "p_smiles_wildcards": {
            "checked_column": "Smiles",
            "definition": "RDKit atoms with atomic number 0 (*)",
            "valid_rows_checked": int(smiles_valid_rows["Smiles"]) if rdkit_available else None,
            "wildcard_count_distribution_rows": {
                str(key): int(value) for key, value in sorted(wildcard_count_rows.items())
            } if rdkit_available else None,
            "exactly_two_wildcards_rows": int(wildcard_count_rows[2]) if rdkit_available else None,
            "exactly_two_wildcards_rate": safe_rate(int(wildcard_count_rows[2]), int(smiles_valid_rows["Smiles"])) if rdkit_available else None,
            "mismatch_examples_max20": wildcard_mismatch_examples,
        },
        "monomer_identity": {
            "unique_raw_strings_across_compound_columns": len(monomer_raw_union),
            "unique_rdkit_canonical_strings_across_compound_columns": len(canonical_monomer_union) if rdkit_available else None,
            "unique_dot_separated_fragments_across_compound_columns": len(monomer_fragment_union),
            "unique_rdkit_canonical_fragments_across_compound_columns": len(canonical_monomer_fragment_union) if rdkit_available else None,
            "rdkit_invalid_dot_separated_fragments": invalid_monomer_fragments if rdkit_available else None,
            "note": "片段数按句点拆分，仅作身份词汇审计，不等同于配方中的化学计量或单体类别。",
        },
        "prediction_labels": label_stats,
        "field_semantics": field_semantics_map(),
        "physical_consistency_qc": physical_consistency_summary(
            physical_qc, row_count, "this_audit_full_scan"
        ),
        "software": {
            "python": sys.version.split()[0],
            "rdkit_available": rdkit_available,
            "rdkit_version": rdkit_version,
            "numpy_available": np is not None,
            "numpy_version": getattr(np, "__version__", None) if np is not None else None,
        },
        "scientific_status": {
            "measurement_type": "model_prediction",
            "recommended_tier": "Gold-V",
            "use_note": "zero direct property supervision; allowed for candidate ranking/active learning/representation only",
            "direct_property_supervision_weight_ceiling": 0.0,
            "gas_prediction_domain": "OOD: associated paper did not validate generated polyurethane gas predictions",
            "known_missing_context": [
                "NCO/OH 当量比",
                "硬段含量",
                "分子量及分散系数",
                "合成与加工工艺",
                "性能测试条件",
                "逐条实验不确定度",
            ],
        },
    }

    atomic_write_json(base / SUMMARY_NAME, summary)
    write_tsv(
        base / COLUMN_NAME,
        ["字段", "角色", "总行数", "非缺失", "缺失", "缺失率", "唯一原始字符串", "非数值", "非有限值"],
        column_rows,
    )
    label_fields = [
        "字段", "count", "missing", "non_numeric", "non_finite", "min", "max", "mean",
        "std_population", "std_sample", "q0.01", "q0.05", "q0.25", "q0.50", "q0.75", "q0.95", "q0.99",
    ]
    write_tsv(base / LABEL_NAME, label_fields, label_rows)
    write_tsv(
        base / SMILES_NAME,
        [
            "字段", "nonmissing_rows", "missing_rows", "unique_raw_strings", "unique_rdkit_canonical_strings", "duplicate_occurrences",
            "duplicate_rate", "rdkit_checked", "rdkit_valid_rows", "rdkit_valid_row_rate",
            "rdkit_valid_unique", "rdkit_valid_unique_rate", "exactly_two_wildcards_rows",
            "exactly_two_wildcards_rate", "无效示例_最多20条",
        ],
        smiles_rows,
    )
    write_tsv(
        field_dictionary_path,
        [
            "field", "role", "value_origin", "raw_storage_semantics", "resolved_unit",
            "conversion_from_raw", "unit_status", "pu_applicability_domain",
            "gold_v_policy", "evidence",
        ],
        build_field_dictionary_rows(),
    )
    print(json.dumps({
        "rows": row_count,
        "columns": len(headers),
        "duplicate_rows": duplicate_row_count,
        "rdkit_available": rdkit_available,
        "elapsed_seconds": elapsed,
        "outputs": [path.name for path in output_paths],
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
