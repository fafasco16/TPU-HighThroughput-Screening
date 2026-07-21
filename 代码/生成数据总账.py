from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import os
import stat
import tempfile
from collections import Counter
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from typing import Any, Iterable, Iterator

import yaml
from rdkit import Chem, rdBase

from 审计.SMiPoly_TPU候选分类 import (
    CANDIDATE_COLUMNS,
    INPUT_PATH as SMIPOLY_CANDIDATE_INPUT,
    build_candidate_rows,
    summarize_candidates,
)
from 审计.第七批PURGEN虚拟片段 import (
    ARCHIVE as PURGEN_ARCHIVE,
    build_fragment_rows as build_purgen_fragment_rows,
)
from 审计.第九批PolyUniverse异氰酸酯单体 import (
    AUDIT_PATH as POLYUNIVERSE_DINCO_AUDIT,
    FILE_SPECS as POLYUNIVERSE_DINCO_FILE_SPECS,
    MAPPING_PATH as POLYUNIVERSE_DINCO_MAPPING,
    SOURCE_DIR as POLYUNIVERSE_DINCO_SOURCE_DIR,
    SOURCE_ID as POLYUNIVERSE_DINCO_SOURCE_ID,
    SUMMARY_PATH as POLYUNIVERSE_DINCO_SUMMARY,
    build_candidate_rows as build_polyuniverse_dinco_rows,
)
from 审计.第九批PolyOmics import (
    CSV_PATH as POLYOMICS_CSV_PATH,
    PROPERTY_FIELDS as POLYOMICS_PROPERTY_FIELDS,
    SOURCE_DIR as POLYOMICS_SOURCE_DIR,
)
from 审计.第九批RadonPy_PI1070 import (
    BASE_UNITS as RADONPY_BASE_UNITS,
    SOURCE_DIR as RADONPY_SOURCE_DIR,
)
from 审计.第十批ACS表格物化 import (
    MATERIALIZED_EVIDENCE_GROUPS as ACS_MATERIALIZED_EVIDENCE_GROUPS,
    RECORD_COLUMNS as ACS_TABLE_RECORD_COLUMNS,
    SPECS as ACS_TABLE_SPECS,
    build_records as build_acs_table_records,
)
from 审计.第十批OpenPolymerChallenge import (
    FROZEN_FILES as BATCH10_OPENPOLY_FROZEN_FILES,
)
from 审计.第十批多保真物化 import (
    COMPUTATIONAL_RECORD_COLUMNS as BATCH10_COMPUTATIONAL_RECORD_COLUMNS,
    KINETICS_AUDIT as BATCH10_KINETICS_AUDIT,
    KINETICS_CONDITIONS as BATCH10_KINETICS_CONDITIONS,
    KINETICS_HASHES as BATCH10_KINETICS_HASHES,
    KINETICS_MEASUREMENTS as BATCH10_KINETICS_MEASUREMENTS,
    OMG_AUDIT as BATCH10_OMG_AUDIT,
    OMG_CANDIDATE_CSV as BATCH10_OMG_CANDIDATE_CSV,
    OMG_FIELD_DICTIONARY as BATCH10_OMG_FIELD_DICTIONARY,
    OMG_GOLD_C_COUNT as BATCH10_OMG_GOLD_C_COUNT,
    OMG_PROPERTY_FILES as BATCH10_OMG_PROPERTY_FILES,
    OMG_PU_PROPERTY_CSV as BATCH10_OMG_PU_PROPERTY_CSV,
    OPENPOLY_CANDIDATE_COUNT as BATCH10_OPENPOLY_CANDIDATE_COUNT,
    OPENPOLY_DIR as BATCH10_OPENPOLY_DIR,
    OPENPOLY_GOLD_C_COUNT as BATCH10_OPENPOLY_GOLD_C_COUNT,
    SCIENCEDB_AUDIT as BATCH10_SCIENCEDB_AUDIT,
    SCIENCEDB_CSV as BATCH10_SCIENCEDB_CSV,
    SOURCE_KINETICS as BATCH10_SOURCE_KINETICS,
    SOURCE_OMG as BATCH10_SOURCE_OMG,
    SOURCE_OPENPOLY as BATCH10_SOURCE_OPENPOLY,
    SOURCE_SCIENCEDB as BATCH10_SOURCE_SCIENCEDB,
    audit_materialization_inputs as audit_batch10_materialization_inputs,
    build_candidate_rows as build_batch10_candidate_rows,
    build_gold_e_rows as build_batch10_gold_e_rows,
    iter_gold_c_rows as iter_batch10_gold_c_rows,
)
from 审计.第十一批既有实验标量物化 import (
    SPECS as BATCH11_EXISTING_SCALAR_SPECS,
    build_records as build_batch11_existing_scalar_records,
)
from 审计.第十二批PUFoam import (
    ARCHIVE_PATH as BATCH12_PUFOAM_ARCHIVE,
    CROSSREF_METADATA_PATH as BATCH12_PUFOAM_CROSSREF_METADATA,
    EXPECTED_TOTAL_ROWS as BATCH12_PUFOAM_GOLD_C_COUNT,
    GOLD_C_VALUE_COLUMNS as BATCH12_PUFOAM_GOLD_C_COLUMNS,
    OFFICIAL_METADATA_PATH as BATCH12_PUFOAM_OFFICIAL_METADATA,
    SOURCE_ID as BATCH12_SOURCE_PUFOAM,
    audit as audit_batch12_pufoam,
    build_gold_c_rows as build_batch12_pufoam_gold_c_rows,
)


ROOT = Path(__file__).resolve().parents[1]
RAW_NEW = ROOT / "数据/原始" / "外部数据" / "新增开放数据"
RAW_MECHANICAL = ROOT / "数据/原始" / "外部数据" / "力学曲线"
PROFILE_PATH = ROOT / "配置" / "v0.2可训练样本总账来源画像.yaml"
SCOPE_PATH = ROOT / "配置" / "v0.2来源范围.yaml"
SNAPSHOT_V01 = ROOT / "数据/快照" / "TPU数据库_v0.1_快照.json"
OUTPUT_LEDGER = ROOT / "结果" / "数据规模总账.csv"
OUTPUT_MANIFEST = ROOT / "结果" / "样本清单.csv.gz"
OUTPUT_JSON = ROOT / "结果" / "数据总账.json"
OUTPUT_REPORT = ROOT / "结果" / "数据总账说明.md"
OUTPUT_CANDIDATES = ROOT / "结果" / "Gold_V_候选.csv.gz"
OUTPUT_GOLD_C_VALUES = ROOT / "结果" / "Gold_C_计算性能.csv.gz"
OUTPUT_GOLD_E_TABLES = ROOT / "结果" / "Gold_E_实验表格.csv.gz"
SCRIPT_PATH = Path(__file__).resolve()
SMIPOLY_CLASSIFIER_PATH = (
    ROOT / "代码" / "审计" / "SMiPoly_TPU候选分类.py"
)
PURGEN_AUDIT_SCRIPT_PATH = ROOT / "代码" / "审计" / "第七批PURGEN虚拟片段.py"
POLYUNIVERSE_DINCO_AUDIT_SCRIPT_PATH = (
    ROOT / "代码" / "审计" / "第九批PolyUniverse异氰酸酯单体.py"
)
POLYOMICS_AUDIT_SCRIPT_PATH = ROOT / "代码" / "审计" / "第九批PolyOmics.py"
ACS_TABLE_AUDIT_SCRIPT_PATH = ROOT / "代码" / "审计" / "第十批ACS表格物化.py"
BATCH10_MATERIALIZATION_SCRIPT_PATH = (
    ROOT / "代码" / "审计" / "第十批多保真物化.py"
)
BATCH10_OPENPOLY_AUDIT_SCRIPT_PATH = (
    ROOT / "代码" / "审计" / "第十批OpenPolymerChallenge.py"
)
BATCH11_EXISTING_SCALAR_SCRIPT_PATH = (
    ROOT / "代码" / "审计" / "第十一批既有实验标量物化.py"
)
BATCH12_PUFOAM_SCRIPT_PATH = ROOT / "代码" / "审计" / "第十二批PUFoam.py"
INPUT_FILES_READ: set[Path] = set()


GOLD_C_VALUE_COLUMNS = [
    "source_id",
    "source_record_id",
    "observation_id",
    "canonical_structure",
    "system_identity",
    "structure_identity_status",
    "global_structure_family_key",
    "simulation_key",
    "property_name",
    "value",
    "unit",
    "unit_status",
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
]


GOLD_E_TABLE_COLUMNS = [
    "source_id",
    "source_scope_id",
    "source_family_id",
    *ACS_TABLE_RECORD_COLUMNS,
]


COUNT_FIELDS = [
    "source_record_count",
    "material_count",
    "formulation_count",
    "batch_count",
    "specimen_count",
    "run_count",
    "curve_count_observed",
    "curve_count_candidate",
    "scalar_count_observed",
    "scalar_count_candidate",
    "point_count_observed",
    "point_count_candidate",
    "numeric_value_count",
    "evidence_group_count",
    "computational_system_count",
    "source_identity_count_contribution",
]

AUDIT_DETAIL_FIELDS = [
    "direct_numeric_total",
    "complete_direct_response_count",
    "valid_derived_scalar_count",
    "invalid_cached_formula_count",
    "known_missing_direct_count",
]

LEDGER_COLUMNS = [
    "source_id",
    "source_scope_id",
    "source_family_id",
    "source_directory",
    "source_title",
    "canonical_identifier",
    "task",
    "origin_kind",
    "scientific_role",
    "gold_layer",
    "gold_admission_status",
    *COUNT_FIELDS,
    *AUDIT_DETAIL_FIELDS,
    "quality_status",
    "weight_ceiling",
    "current_weight_materialized",
    "model_ready_record_count",
    "unit_status",
    "license_status",
    "dedup_status",
    "split_group_status",
    "completeness",
    "citation_keys",
    "audit_basis",
    "notes",
]

MANIFEST_COLUMNS = [
    "manifest_row_id",
    "schema_version",
    "completeness",
    "record_granularity",
    "source_id",
    "source_scope_id",
    "source_family_id",
    "source_directory",
    "source_title",
    "task",
    "origin_kind",
    "scientific_role",
    "gold_layer",
    "gold_admission_status",
    "target_origin",
    "record_role",
    "method_family",
    "fidelity_level",
    "candidate_id",
    "raw_sample_key",
    "material_formula_key",
    "specimen_key",
    "run_key",
    "curve_key",
    "scalar_key",
    "leakage_group_key",
    "leakage_key_status",
    "quality_status",
    "weight_ceiling",
    "current_weight_materialized",
    "model_ready",
    "specimen_count",
    "run_count",
    "curve_count",
    "scalar_count",
    "point_count",
    "numeric_value_count",
    "unit_status",
    "license_status",
    "dedup_status",
    "source_locator",
    "audit_basis",
    "decision_basis",
    "notes",
]

ENUMS = {
    "record_granularity": {
        "source",
        "candidate",
        "specimen",
        "run",
        "curve",
        "scalar",
        "evidence_group",
    },
    "origin_kind": {"实验", "模拟", "混合", "虚拟候选", "证据"},
    "scientific_role": {"TPU核心", "迁移", "证据"},
    "quality_status": {"入选", "降权", "仅验证", "隔离"},
    "gold_layer": {"Gold-E", "Gold-C", "Gold-V", "Gold-E+Gold-C", "Not-Gold"},
    "gold_admission_status": {
        "admitted_reference",
        "conditional_reference",
        "blocked",
        "evidence_only",
    },
}

BASELINE_SOURCE_KEY = {
    "ds_smipoly_monomers": "source_smipoly_data",
    "ds_pue326_dq": "source_dq_repo",
    "ds_prepolymer_viscosity": "source_viscosity_repo",
    "ds_eom_hbond_2021": "source_eom_data",
}

LEGACY_LAYOUT_REPLACEMENTS = {
    "01_原始数据/": "数据/原始/",
    "02_暂存数据/": "数据/暂存/",
    "03_规范数据/": "数据/规范/",
    "04_派生数据/": "数据/派生/",
    "05_数据库快照/": "数据/快照/",
    "06_审核导出/": "结果/",
}
PATH_BEARING_MANIFEST_FIELDS = {
    "raw_sample_key",
    "run_key",
    "curve_key",
    "source_locator",
    "audit_basis",
}


def _read_yaml(path: Path) -> dict[str, Any]:
    _register_input(path)
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _read_json(path: Path) -> dict[str, Any]:
    _register_input(path)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_tsv(path: Path) -> list[dict[str, str]]:
    _register_input(path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _register_input(path: Path) -> None:
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(ROOT.resolve(strict=True))
    except ValueError as exc:
        raise ValueError(f"输入文件越出工作区: {resolved}") from exc
    if not resolved.is_file():
        raise ValueError(f"输入不是普通文件: {resolved}")
    INPUT_FILES_READ.add(resolved)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _build_input_fingerprints() -> tuple[list[dict[str, Any]], str]:
    _register_input(SCRIPT_PATH)
    rows = [
        {
            "path": _to_relative(path),
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        for path in sorted(INPUT_FILES_READ, key=lambda item: _to_relative(item))
    ]
    canonical = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return rows, hashlib.sha256(canonical).hexdigest()


def _number(value: Any) -> int | float | None:
    if value is None or value is False or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = str(value).strip().replace(",", "")
    if text.lower() in {"none", "null", "nan", "false", "not_materialized"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _first(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


def _sum_numbers(row: dict[str, Any], keys: Iterable[str]) -> int | float | None:
    values = [_number(row.get(key)) for key in keys]
    finite = [value for value in values if value is not None]
    if not finite:
        return None
    return sum(finite)


def _uid(*parts: Any) -> str:
    payload = "|".join(str(part) for part in parts)
    return "manifest_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _joined(*parts: Any) -> str:
    return "|".join(str(part).strip() for part in parts if part is not None and str(part).strip())


def _normalize_layout_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.replace("\\", "/")
    for legacy, current in LEGACY_LAYOUT_REPLACEMENTS.items():
        normalized = normalized.replace(legacy, current)
    return normalized


def _gold_layer(profile: dict[str, Any]) -> str:
    return {
        "实验": "Gold-E",
        "模拟": "Gold-C",
        "混合": "Gold-E+Gold-C",
        "虚拟候选": "Gold-V",
        "证据": "Not-Gold",
    }[profile["origin_kind"]]


def _gold_admission_status(
    profile: dict[str, Any], quality_status: str, layer: str | None = None
) -> str:
    layer = layer or _gold_layer(profile)
    if layer == "Not-Gold":
        return "evidence_only"
    declared = str(profile.get("reference_admission_status", "")).strip()
    if declared:
        if declared not in ENUMS["gold_admission_status"]:
            raise ValueError(
                f"非法来源参考准入状态: {profile['source_directory']}={declared}"
            )
        return declared
    if quality_status == "隔离":
        return "blocked"
    # Gold-V 是可追溯候选参考集合；没有实验性质标签只会使其监督权重为零，
    # 不会反向取消候选准入。
    if layer == "Gold-V":
        return "admitted_reference"
    if quality_status == "仅验证":
        return "conditional_reference"
    return "admitted_reference"


def _target_origin(profile: dict[str, Any], explicit: Any = None) -> str:
    value = str(explicit or "").strip().lower().replace("-", "_")
    aliases = {
        "experiment": "experimental",
        "实验": "experimental",
        "computation": "computational",
        "computed": "computational",
        "simulation": "computational",
        "模拟": "computational",
        "cgmd": "coarse_grained_md",
        "coarse_grained": "coarse_grained_md",
        "cfd": "cfd",
        "population_balance": "population_balance",
        "reaction_kinetics_simulation": "reaction_kinetics_simulation",
        "fea": "finite_element",
        "finite_element_input": "simulation_input",
        "experimental_processed_curve": "experimental",
        "experimental_derived_scalar": "experimental",
        "published_continuum_model_curve": "computational",
        "published_reinforcement_model_curve": "computational",
        "mixed_published_experiment_model": "mixed",
        "virtual_candidate": "virtual",
        "虚拟候选": "virtual",
        "证据": "evidence",
        "混合": "mixed",
    }
    if value:
        return aliases.get(value, value)
    return {
        "实验": "experimental",
        "模拟": "computational",
        "混合": "mixed",
        "虚拟候选": "virtual",
        "证据": "evidence",
    }[profile["origin_kind"]]


def _gold_layer_for_target(
    profile: dict[str, Any], target_origin: str, explicit: Any = None
) -> str:
    declared = str(explicit or "").strip()
    if declared in ENUMS["gold_layer"]:
        return declared
    if target_origin == "experimental":
        return "Gold-E"
    if target_origin in {
        "computational",
        "dft",
        "aimd",
        "md",
        "coarse_grained_md",
        "cfd",
        "population_balance",
        "reaction_kinetics_simulation",
        "finite_element",
        "simulation_input",
    }:
        return "Gold-C"
    if target_origin in {
        "virtual",
        "reaction_rule_generated",
        "enumeration",
        "model_generated",
        "ml_prediction",
    }:
        return "Gold-V"
    if target_origin == "mixed":
        return "Gold-E+Gold-C"
    if target_origin == "evidence":
        return "Not-Gold"
    return _gold_layer(profile)


@dataclass(frozen=True)
class SourceIdentity:
    source_id: str
    source_scope_id: str
    source_family_id: str
    source_title: str
    canonical_identifier: str
    citation_keys: tuple[str, ...]
    references: tuple[str, ...]


def _resolve_source_identities(
    scope_config: dict[str, Any], profiles: list[dict[str, Any]], baseline_profiles: list[dict[str, Any]]
) -> dict[str, SourceIdentity]:
    sources = {item["source_key"]: item for item in scope_config["sources"]}
    scopes = {item["source_scope_key"]: item for item in scope_config["scopes"]}
    citations = scope_config["citations"]
    prefix_map: dict[str, str] = {}
    for mapping in scope_config["path_mappings"]:
        if mapping.get("match_type") != "prefix":
            continue
        pattern = str(mapping.get("pattern", "")).replace("\\", "/").strip("/")
        prefix_map[pattern] = mapping["source_scope_key"]

    resolved: dict[str, SourceIdentity] = {}
    for profile in profiles:
        directory = profile["source_directory"]
        relative_base = str(
            profile.get("source_path") or f"外部数据/新增开放数据/{directory}"
        ).replace("\\", "/").strip("/")
        scope_id = prefix_map.get(relative_base)
        if not scope_id:
            raise ValueError(f"缺少来源范围映射: {relative_base}")
        scope = scopes[scope_id]
        source = sources.get(scope["source_key"])
        source_family_key = source.get("source_family_key") if source else None
        matched_citations = [
            item
            for item in citations
            if item.get("source_key") == scope["source_key"]
            or item.get("target_scope_key") == scope_id
            or (source_family_key and item.get("source_family_key") == source_family_key)
        ]
        if source is None:
            if not matched_citations:
                raise ValueError(f"来源范围既无source定义也无citation: {scope_id}")
            citation = matched_citations[0]
            source = {
                "source_family_key": citation["source_family_key"],
                "title": citation["title"],
                "canonical_identifier": f"doi:{citation.get('doi', '')}" if citation.get("doi") else scope["canonical_identifier"],
            }
        resolved[directory] = SourceIdentity(
            source_id=scope["source_key"],
            source_scope_id=scope_id,
            source_family_id=source["source_family_key"],
            source_title=source["title"],
            canonical_identifier=source["canonical_identifier"],
            citation_keys=tuple(item["citation_key"] for item in matched_citations),
            references=tuple(item["reference_text"] for item in matched_citations),
        )

    for profile in baseline_profiles:
        source_key = BASELINE_SOURCE_KEY[profile["source_id"]]
        source = sources[source_key]
        # v0.1 的本地快照 source_key 与论文台账 source_key 并不总是同一个键；
        # 用已经治理过的 source_family_key 回连正式文献，避免报告只剩本地文件名。
        matched_citations = [
            item
            for item in citations
            if item.get("source_key") == source_key
            or item.get("source_family_key") == source["source_family_key"]
        ]
        resolved[profile["source_directory"]] = SourceIdentity(
            source_id=profile["source_id"],
            source_scope_id=profile["source_scope_id"],
            source_family_id=profile["source_family_id"],
            source_title=source["title"],
            canonical_identifier=source["canonical_identifier"],
            citation_keys=tuple(item["citation_key"] for item in matched_citations),
            references=tuple(item["reference_text"] for item in matched_citations),
        )
    return resolved


def _profile_base(profile: dict[str, Any]) -> Path:
    source_path = profile.get("source_path")
    if source_path:
        return ROOT / "数据/原始" / Path(str(source_path))
    return RAW_NEW / profile["source_directory"]


def _audit_paths(profile: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    for fragment in str(profile["audit_basis"]).split(";"):
        fragment = fragment.strip()
        if not fragment:
            continue
        normalized_fragment = fragment.replace("\\", "/")
        is_project_relative = normalized_fragment.startswith(
            ("代码/", "配置/", "数据/", "文档/", "结果/")
        )
        candidate = (
            ROOT / fragment
            if is_project_relative
            else _profile_base(profile) / fragment
        )
        if not candidate.exists():
            raise FileNotFoundError(f"审计依据不存在: {candidate}")
        _register_input(candidate)
        paths.append(candidate)
    return paths


def _to_relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _build_ledger(
    profiles: list[dict[str, Any]], baseline_profiles: list[dict[str, Any]], identities: dict[str, SourceIdentity]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for profile in [*baseline_profiles, *profiles]:
        identity = identities[profile["source_directory"]]
        counts = profile["counts"]
        row: dict[str, Any] = {
            "source_id": identity.source_id,
            "source_scope_id": identity.source_scope_id,
            "source_family_id": identity.source_family_id,
            "source_directory": profile["source_directory"],
            "source_title": identity.source_title,
            "canonical_identifier": identity.canonical_identifier,
            "task": profile["task"],
            "origin_kind": profile["origin_kind"],
            "scientific_role": profile["scientific_role"],
            "gold_layer": _gold_layer(profile),
            "gold_admission_status": _gold_admission_status(
                profile, profile["quality_status"]
            ),
            **{field: counts.get(field) for field in COUNT_FIELDS},
            **{
                field: profile.get("audit_metrics", {}).get(field)
                for field in AUDIT_DETAIL_FIELDS
            },
            "quality_status": profile["quality_status"],
            "weight_ceiling": profile["weight_ceiling"],
            "current_weight_materialized": False,
            "model_ready_record_count": 0,
            "unit_status": profile["unit_status"],
            "license_status": profile["license_status"],
            "dedup_status": profile["dedup_status"],
            "split_group_status": profile["split_group_status"],
            "completeness": profile["completeness"],
            "citation_keys": ";".join(identity.citation_keys),
            "audit_basis": ";".join(_to_relative(path) for path in _audit_paths(profile)),
            "notes": profile["notes"],
        }
        rows.append(row)
    return rows


def _record_status(profile: dict[str, Any], row: dict[str, Any]) -> str:
    source = profile["source_directory"]
    text = " ".join(
        str(row.get(key, ""))
        for key in (
            "准入状态",
            "准入结论",
            "decision",
            "quality_gate",
            "source_summary_state",
            "effective_eligibility",
            "eligibility",
            "parse_state",
            "训练状态",
            "current_weight_ceiling",
        )
    ).lower()
    if source == "QUB_生物基三重自修复TPU":
        if row.get("cross_file_duplicate_of") or row.get("effective_eligibility") == "exclude_duplicate":
            return "隔离"
        return "入选" if row.get("effective_eligibility") == "main_curve" else "降权"
    if source == "Mendeley_FDM_TPU晶格与基材力学":
        state = row.get("source_summary_state", "")
        return {"selected": "降权", "not_selected": "仅验证", "conflict": "隔离"}.get(state, "隔离")
    if source == "Mendeley_TPU95A_TPMS应变率力学":
        return "隔离"
    if source == "Mendeley_TPU压缩打印DOE" and row.get("record_kind") == "proprietary_DOE_project":
        return "仅验证"
    if source == "Mendeley_TPU压缩打印DOE" and row.get("geometry_or_evidence") == "solid_cube_control":
        return "仅验证"
    if source == "DRUM_TPUU_机械回收" and row.get("准入结论") == "排除核心训练":
        return "仅验证"
    # 来源级规则是安全上限：仅验证或隔离来源的明细记录不能因为局部出现
    # candidate/selected 字样而被自动提升为可训练状态。
    if profile["quality_status"] in {"仅验证", "隔离"}:
        return profile["quality_status"]
    hard_zero = (
        "weight_zero",
        "exclude",
        "excluded",
        "quarantine",
        "conflict",
        "duplicate",
        "hold_",
        "downstream_task_only",
        "not_selected",
        "mirror",
    )
    if any(token in text for token in hard_zero):
        return "隔离"
    if "evidence_only" in text or profile["scientific_role"] == "证据":
        return "仅验证"
    if "降权准入" in text:
        return "降权"
    if text.strip() == "准入" or profile["quality_status"] == "入选":
        return profile["quality_status"]
    if "candidate" in text or "selected" in text or "pass_" in text or "准入" in text:
        return "降权" if profile["scientific_role"] != "TPU核心" else "入选"
    return profile["quality_status"]


def _record_ceiling(profile: dict[str, Any], row: dict[str, Any], status: str) -> float:
    if status in {"隔离", "仅验证"}:
        return 0.0
    source = profile["source_directory"]
    if source == "QUB_生物基三重自修复TPU" and status == "降权":
        role = row.get("effective_eligibility") or row.get("eligibility")
        return 0.25 if role == "auxiliary_dependent" else 0.35
    if source == "DRUM_TPUU_机械回收" and row.get("准入结论") == "降权准入":
        material = str(row.get("材料代码", ""))
        if "14BDO" in material:
            return 0.65
        if "Thermoset" in material or "thermoset" in material:
            return 0.25
        return 0.15
    explicit = _first(row, "future_weight_ceiling", "current_weight_ceiling", "训练权重上限")
    numeric = _number(explicit)
    if numeric is not None:
        return float(numeric)
    return float(profile["weight_ceiling"])


def _base_record(
    profile: dict[str, Any], identity: SourceIdentity, granularity: str, raw_key: str, index: int
) -> dict[str, Any]:
    raw_key = _normalize_layout_text(raw_key)
    leakage = identity.source_family_id
    return {
        "manifest_row_id": _uid(identity.source_scope_id, granularity, raw_key, index),
        "schema_version": "v0.2",
        "completeness": "record_resolved",
        "record_granularity": granularity,
        "source_id": identity.source_id,
        "source_scope_id": identity.source_scope_id,
        "source_family_id": identity.source_family_id,
        "source_directory": profile["source_directory"],
        "source_title": identity.source_title,
        "task": profile["task"],
        "origin_kind": profile["origin_kind"],
        "scientific_role": profile["scientific_role"],
        "gold_layer": _gold_layer(profile),
        "gold_admission_status": _gold_admission_status(
            profile, profile["quality_status"]
        ),
        "target_origin": _target_origin(profile),
        "record_role": (
            "source_aggregate" if granularity == "source" else
            "virtual_candidate" if granularity == "candidate" else
            "evidence_group" if granularity == "evidence_group" else
            "property_observation"
        ),
        "method_family": "",
        "fidelity_level": "",
        "candidate_id": "",
        "raw_sample_key": raw_key,
        "material_formula_key": "",
        "specimen_key": "",
        "run_key": "",
        "curve_key": "",
        "scalar_key": "",
        "leakage_group_key": leakage,
        "leakage_key_status": "coarse_source_family",
        "quality_status": profile["quality_status"],
        "weight_ceiling": profile["weight_ceiling"],
        "current_weight_materialized": False,
        "model_ready": False,
        "specimen_count": 0,
        "run_count": 0,
        "curve_count": 0,
        "scalar_count": 0,
        "point_count": 0,
        "numeric_value_count": 0,
        "unit_status": profile["unit_status"],
        "license_status": profile["license_status"],
        "dedup_status": profile["dedup_status"],
        "source_locator": "",
        "audit_basis": profile["audit_basis"],
        "decision_basis": "来源级审计画像",
        "notes": "",
    }


def _tabular_record(
    profile: dict[str, Any], identity: SourceIdentity, row: dict[str, Any], path: Path, index: int
) -> dict[str, Any]:
    source = profile["source_directory"]
    if source.startswith("ACS_Figshare_"):
        granularity = "evidence_group"
    elif source == "Mendeley_TPU压缩打印DOE":
        granularity = "specimen" if row.get("record_kind") == "compression_discrete_strain" else "evidence_group"
    elif source == "Zenodo_Tecoflex药物复合TPU":
        granularity = "scalar"
    else:
        granularity = "curve"
    raw_key = _joined(
        _first(
            row,
            "curve_id",
            "curve_occurrence_id",
            "run_id",
            "曲线ID",
            "曲线或文件",
            "source_location",
            "来源",
        ),
        _first(row, "file", "source_file", "文件相对路径", "relative_path", "filename", "曲线"),
        _first(row, "sheet", "工作表", "source_location", "工况或试样", "试样标签"),
    )
    if not raw_key:
        raw_key = f"{path.name}:row:{index + 2}"
    record = _base_record(profile, identity, granularity, raw_key, index)
    material = _first(
        row,
        "材料代码",
        "材料",
        "material",
        "material_grade",
        "resolved_material_grade",
        "formulation_id",
        "material_ids",
    )
    specimen = _first(row, "试样键", "试样ID", "record_id", "试样或家族组", "工况或试样")
    instance_key = _first(row, "instance_key")
    if source == "第七批补充材料_孢子填充TPU":
        specimen = _joined(row.get("formulation_id"), row.get("replicate_source_order"))
    if source == "SelfHealingTPU_4TU" and row.get("modality") == "mechanical" and instance_key:
        # 原始/愈合曲线属于同一物理试样；不能让曲线 record_id 把试样数翻倍。
        specimen = instance_key
    curve = _first(
        row,
        "curve_id",
        "curve_occurrence_id",
        "run_id",
        "曲线ID",
        "曲线或文件",
        "record_id",
    )
    scalar = _joined(
        _first(row, "record_kind", "observable", "reported_items", "y_observable"),
        _first(row, "source_location", "sheet", "工作表"),
    )
    group = _first(
        row,
        "泄漏分组键",
        "leakage_group",
        "split_group",
        "试样组键",
        "specimen_family_id",
        "试样或家族组",
        "lineage_group",
        "independent_condition_id",
    )
    if source == "第七批补充材料_孢子填充TPU":
        group = _joined(identity.source_family_id, row.get("formulation_id"))
    status = _record_status(profile, row)
    point_count = _sum_numbers(
        row,
        (
            "点数",
            "point_count",
            "usable_points",
            "机械原始点",
            "DIC点",
            "完整点",
            "observed_response_points",
        ),
    )
    numeric_count = _sum_numbers(row, ("direct_numeric_count", "derived_formula_count", "有限数值单元格"))
    scalar_count = (
        _number(row.get("direct_numeric_count")) or 0
        if source == "Mendeley_TPU压缩打印DOE"
        and row.get("geometry_or_evidence") == "solid_cube_control"
        else 0
    )
    record.update(
        {
            "task": _first(
                row,
                "试验类型",
                "protocol",
                "record_kind",
                "数据角色",
                "modality",
                "scientific_task",
            ) or profile["task"],
            "target_origin": _target_origin(
                profile,
                _first(row, "target_origin", "data_origin", "来源类型", "数据来源类型"),
            ),
            "method_family": (
                "experiment"
                if str(row.get("data_origin", "")).startswith("experimental_")
                else "published_continuum_model"
                if row.get("data_origin") == "published_continuum_model_curve"
                else "published_reinforcement_model"
                if row.get("data_origin") == "published_reinforcement_model_curve"
                else "mixed_experiment_model"
                if row.get("data_origin") == "mixed_published_experiment_model"
                else _first(row, "method_family")
            ),
            "fidelity_level": (
                "experimental_processed"
                if str(row.get("data_origin", "")).startswith("experimental_")
                else "published_model"
                if str(row.get("data_origin", "")).startswith("published_")
                else "mixed_experiment_and_published_model"
                if row.get("data_origin") == "mixed_published_experiment_model"
                else _first(row, "fidelity_level")
            ),
            "candidate_id": _first(
                row,
                "candidate_id",
                "mapped_candidate_id",
                "formulation_id",
                "体系或路径",
                "material_ids",
            ),
            "material_formula_key": material,
            "specimen_key": specimen if granularity in {"specimen", "curve", "scalar"} else "",
            # 审计器已把同一原始运行中的多通道写成共同 instance_key。
            "run_key": instance_key or _first(row, "test_run", "试验", "条件"),
            "curve_key": curve if granularity == "curve" else "",
            "scalar_key": scalar if granularity == "scalar" else "",
            "leakage_group_key": group or identity.source_family_id,
            "leakage_key_status": "explicit_record_group" if group else "coarse_source_family",
            "quality_status": status,
            "weight_ceiling": _record_ceiling(profile, row, status),
            "specimen_count": 1 if granularity == "specimen" else (_number(row.get("独立试样数")) or 0),
            "run_count": 1 if granularity == "run" else 0,
            "curve_count": 1 if granularity == "curve" else 0,
            "scalar_count": 1 if granularity == "scalar" else scalar_count,
            "point_count": point_count or 0,
            "numeric_value_count": (
                point_count * 2
                if source == "第九批实验_糖填充超分子聚氨酯"
                else numeric_count or 0
            ),
            "dedup_status": "精确重复隔离" if "duplicate" in " ".join(str(v) for v in row.values()).lower() and status == "隔离" else profile["dedup_status"],
            "source_locator": f"{_to_relative(path)}#row={index + 2}",
            "audit_basis": _to_relative(path),
            "decision_basis": _first(row, "准入结论", "准入状态", "decision", "quality_gate", "source_summary_state", "parse_state") or "来源级审计画像",
            "notes": _joined(_first(row, "备注", "anomaly", "decision_reason", "排除或降权原因"), _first(row, "manual_action")),
            "_declared_gold_admission_status": _first(
                row, "gold_admission_status", "admission_status"
            ),
        }
    )
    if source.startswith("ACS_Figshare_"):
        record["quality_status"] = "仅验证"
        record["weight_ceiling"] = 0.0
        record["numeric_value_count"] = 0
        record["target_origin"] = "evidence"
        record["record_role"] = "evidence"
        record["_declared_gold_admission_status"] = "evidence_only"
    return record


def _scalar_records(profile: dict[str, Any], identity: SourceIdentity, path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, row in enumerate(_read_tsv(path)):
        raw_key = _joined(
            row.get("工作簿"),
            row.get("试样ID"),
            row.get("observable"),
            row.get("scalar_lineage_class"),
            row.get("record_id"),
            row.get("scalar_id"),
            row.get("source_location"),
        )
        record = _base_record(profile, identity, "scalar", raw_key or f"row:{index + 2}", index)
        status = _record_status(profile, row)
        group = _first(row, "试样组", "试样ID", "split_group")
        if profile["source_directory"] == "第七批补充材料_孢子填充TPU":
            group = _joined(identity.source_family_id, row.get("formulation_id"))
        value = _number(row.get("value"))
        direct_numeric_count = _number(row.get("direct_numeric_result_count"))
        derived_numeric_count = _number(row.get("derived_numeric_result_count"))
        has_grouped_count = direct_numeric_count is not None or derived_numeric_count is not None
        scalar_count = int((direct_numeric_count or 0) + (derived_numeric_count or 0)) if has_grouped_count else 1
        record.update(
            {
                "task": row.get("observable") or row.get("metric") or row.get("task_role") or profile["task"],
                "target_origin": _target_origin(
                    profile,
                    _first(row, "target_origin", "data_origin", "来源类型", "数据来源类型"),
                ),
                "candidate_id": _first(
                    row,
                    "candidate_id",
                    "mapped_candidate_id",
                    "formulation_id",
                ),
                "material_formula_key": _first(row, "来源", "formulation_id"),
                "specimen_key": row.get("试样ID")
                or row.get("specimen_id")
                or row.get("record_id")
                or _joined(row.get("formulation_id"), row.get("replicate_source_order")),
                "scalar_key": _joined(
                    row.get("observable") or row.get("metric"),
                    row.get("unit"),
                    row.get("result_names"),
                ),
                "leakage_group_key": _joined(identity.source_family_id, group) if group else identity.source_family_id,
                "leakage_key_status": "explicit_record_group" if group else "coarse_source_family",
                "quality_status": status,
                "weight_ceiling": _record_ceiling(profile, row, status),
                "scalar_count": scalar_count,
                "numeric_value_count": scalar_count if has_grouped_count else (1 if value is not None else 0),
                "source_locator": f"{_to_relative(path)}#row={index + 2}",
                "audit_basis": _to_relative(path),
                "decision_basis": row.get("quality_gate") or row.get("decision", ""),
                "notes": row.get("备注") or row.get("notes", ""),
                "_declared_gold_admission_status": _first(
                    row, "gold_admission_status", "admission_status"
                ),
            }
        )
        records.append(record)
    return records


def _canonical_polymer_structure(smiles: str) -> str:
    """Return one cross-source RDKit identity for a polymer repeat unit."""

    source = str(smiles).strip()
    if not source:
        raise ValueError("计算性质记录缺少结构")
    with rdBase.BlockLogs():
        molecule = Chem.MolFromSmiles(source)
    if molecule is None:
        raise ValueError(f"计算性质记录含不可解析结构: {source}")
    return Chem.MolToSmiles(
        molecule, canonical=True, isomericSmiles=True
    )


def _global_structure_family_key(canonical_structure: str) -> str:
    digest = hashlib.sha256(canonical_structure.encode("utf-8")).hexdigest()
    return f"global_polymer_structure_{digest[:24]}"


def _global_named_system_family_key(source_family: str, system_identity: str) -> str:
    payload = f"{source_family}|{system_identity}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"global_formulation_system_{digest[:24]}"


def _finite_numeric_text(value: Any, *, context: str) -> str:
    text = str(value).strip()
    try:
        number = float(text)
    except ValueError as exc:
        raise ValueError(f"{context}不是数值: {text}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{context}不是有限数值: {text}")
    return text


POLYOMICS_MD_PROPERTIES = {
    "density",
    "Rg",
    "self-diffusion",
    "Cp",
    "Cv",
    "compressibility",
    "isentropic_compressibility",
    "bulk_modulus",
    "isentropic_bulk_modulus",
    "volume_expansion",
    "linear_expansion",
    "r2",
    "static_dielectric_const",
    "dielectric_const_dc",
    "nematic_order_parameter",
}
POLYOMICS_NEMD_PROPERTIES = {
    "thermal_conductivity",
    "thermal_diffusivity",
    "TC_ke",
    "TC_pe",
    "TC_pair",
    "TC_bond",
    "TC_angle",
    "TC_dihed",
    "TC_improper",
    "TC_kspace",
    "TC_fix",
}
POLYOMICS_MD_DERIVED_PROPERTIES = {
    "Scaled Rg",
    "sp_ced",
    "sp_total",
    "sp_vdw",
    "sp_ele",
    "sp_ele_short",
    "sp_ele_long",
}


def _polyomics_method(property_name: str, row: dict[str, str]) -> tuple[str, str, str]:
    if property_name in POLYOMICS_MD_PROPERTIES:
        family = "MD"
        fidelity = "all_atom_MD"
        preset = row.get("preset_eq_ver", "")
    elif property_name in POLYOMICS_NEMD_PROPERTIES:
        family = "NEMD"
        fidelity = "all_atom_NEMD"
        preset = row.get("preset_tc_ver", "")
    elif property_name in POLYOMICS_MD_DERIVED_PROPERTIES:
        family = "MD-derived"
        fidelity = "all_atom_MD_derived"
        preset = row.get("preset_sp_ver", "")
    else:
        # Tg、折射率、Abbe 数和动态介电量依赖来源定义的多阶段协议；
        # 在没有逐字段方法证明时不把它们冒充为单一 DFT 或 MD 观测。
        family = "computational-protocol"
        fidelity = "source_native_multistage_protocol"
        preset = (
            row.get("preset_tg_ver", "")
            if property_name == "tg"
            else row.get("preset_eq_ver", "")
        )
    detail = _joined(
        f"PolyOmics source-native field={property_name}",
        f"qm_method={row.get('qm_method', '')}",
        f"forcefield={row.get('forcefield', '')}",
        f"preset={preset}",
        f"RadonPy={row.get('RadonPy_ver', '')}",
        f"LAMMPS={row.get('LAMMPS_ver', '')}",
    )
    return family, detail, fidelity


def _polyomics_record_role(property_name: str) -> str:
    if property_name.startswith("TC_") or property_name in {
        "Scaled Rg",
        "sp_ced",
        "sp_vdw",
        "sp_ele",
        "sp_ele_short",
        "sp_ele_long",
    }:
        return "computational_feature_or_mechanistic_reference"
    return "computational_target"


def _governed_batch10_gold_c_rows(
    profiles: list[dict[str, Any]],
    identities: dict[str, SourceIdentity],
) -> Iterator[dict[str, Any]]:
    """流式注入权威来源身份、引用和来源级权重上限。"""

    profile_by_directory = {
        profile["source_directory"]: profile for profile in profiles
    }
    source_directories = {
        BATCH10_SOURCE_OMG: "第十批计算_OMG",
        BATCH10_SOURCE_OPENPOLY: "第十批计算_OpenPolymerChallenge",
    }
    governance: dict[str, tuple[str, SourceIdentity, float]] = {}
    for source_id, directory in source_directories.items():
        identity = identities[directory]
        if identity.source_id != source_id:
            raise AssertionError(
                f"第十批Gold-C来源ID漂移: {directory}="
                f"{identity.source_id}, expected={source_id}"
            )
        governance[source_id] = (
            directory,
            identity,
            float(profile_by_directory[directory]["weight_ceiling"]),
        )

    for source_row in iter_batch10_gold_c_rows():
        source_id = str(source_row["source_id"])
        if source_id not in governance:
            raise AssertionError(f"第十批Gold-C出现未知来源ID: {source_id}")
        directory, identity, source_ceiling = governance[source_id]
        row = dict(source_row)
        row["citation_keys"] = ";".join(identity.citation_keys)
        row["potential_weight_ceiling"] = min(
            float(row["potential_weight_ceiling"]), source_ceiling
        )
        row["source_locator"] = (
            f"数据/原始/外部数据/新增开放数据/{directory}/"
            f"{row['source_locator']}"
        )
        yield row


def _gold_c_computational_value_rows(
    profiles: list[dict[str, Any]],
    identities: dict[str, SourceIdentity],
    acs_table_rows: list[dict[str, Any]],
) -> tuple[Iterable[dict[str, Any]], dict[str, Any]]:
    """Materialize normalized source values, not a final training dataset."""

    profile_by_directory = {
        profile["source_directory"]: profile for profile in profiles
    }
    radon_directory = "第九批计算_RadonPy_PI1070"
    polyomics_directory = "第九批计算_PolyOmics"
    pufoam_directory = "第十二批计算_PUFoam"
    required = {radon_directory, polyomics_directory, pufoam_directory}
    missing = required - profile_by_directory.keys()
    if missing:
        raise ValueError(f"Gold-C数值长表缺少来源画像: {sorted(missing)}")

    rows: list[dict[str, Any]] = []
    radon_path = RADONPY_SOURCE_DIR / "PU计算观测清单.tsv"
    radon_identity = identities[radon_directory]
    radon_ceiling = float(profile_by_directory[radon_directory]["weight_ceiling"])
    for source_row in _read_tsv(radon_path):
        canonical = _canonical_polymer_structure(source_row["smiles"])
        method_family = source_row["method_family"]
        fidelity = {
            "DFT": "DFT",
            "MD": "all_atom_MD",
            "NEMD": "all_atom_NEMD",
            "DFT+MD derived": "DFT_MD_derived",
        }.get(method_family)
        if fidelity is None:
            raise ValueError(f"RadonPy未知方法族: {method_family}")
        property_name = source_row["property_name"]
        unit = source_row.get("unit", "").strip() or RADONPY_BASE_UNITS.get(
            property_name, ""
        )
        if not unit:
            unit = "source_native_unit_unresolved"
        source_record_id = source_row["monomer_ID"]
        simulation_basis = _joined(
            canonical,
            source_row.get("temperature_K", ""),
            source_row.get("pressure_atm", ""),
            source_row.get("degree_of_polymerization", ""),
            source_row.get("chain_count", ""),
            source_row.get("source_commit", ""),
        )
        simulation_digest = hashlib.sha256(
            simulation_basis.encode("utf-8")
        ).hexdigest()[:24]
        rows.append(
            {
                "source_id": radon_identity.source_id,
                "source_record_id": source_record_id,
                "observation_id": source_row["observation_id"],
                "canonical_structure": canonical,
                "system_identity": canonical,
                "structure_identity_status": "exact_polymer_smiles",
                "global_structure_family_key": _global_structure_family_key(
                    canonical
                ),
                "simulation_key": f"radonpy_simulation_{simulation_digest}",
                "property_name": property_name,
                "value": _finite_numeric_text(
                    source_row["value"],
                    context=source_row["observation_id"],
                ),
                "unit": unit,
                "unit_status": (
                    "source_native_unit_unresolved"
                    if unit == "source_native_unit_unresolved"
                    else "source_unit_preserved"
                ),
                "method_family": method_family,
                "method_detail": source_row["method_detail"],
                "fidelity_level": fidelity,
                "temp": source_row.get("temperature_K", ""),
                "press": source_row.get("pressure_atm", ""),
                "gold_admission_status": "admitted_reference",
                "property_admission_status": "admitted_reference",
                "source_validation_status": "source_audit_admitted",
                "record_role": source_row["record_role"],
                "potential_weight_ceiling": radon_ceiling,
                "current_weight_materialized": "false",
                "training_weight": "",
                "source_locator": (
                    "PI1070.csv#"
                    f"monomer_ID={source_record_id};field={property_name}"
                ),
                "citation_keys": ";".join(radon_identity.citation_keys),
            }
        )

    polyomics_path = POLYOMICS_SOURCE_DIR / "PU计算参考.tsv"
    polyomics_identity = identities[polyomics_directory]
    polyomics_ceiling = float(
        profile_by_directory[polyomics_directory]["weight_ceiling"]
    )
    for source_row in _read_tsv(polyomics_path):
        canonical = _canonical_polymer_structure(
            source_row["canonical_component_smiles"]
        )
        if canonical != source_row["canonical_component_smiles"]:
            raise ValueError(
                "PolyOmics规范结构在Gold-C长表复算时发生漂移: "
                f"{source_row['source_record_id']}"
            )
        run_admission = source_row["gold_admission_status"]
        run_ceiling = min(
            polyomics_ceiling,
            float(source_row["potential_weight_ceiling"]),
        )
        for property_name in POLYOMICS_PROPERTY_FIELDS:
            raw_value = source_row.get(property_name, "").strip()
            if not raw_value:
                continue
            unit = RADONPY_BASE_UNITS.get(
                property_name, "source_native_unit_unresolved"
            )
            property_admission = (
                source_row["thermal_target_admission"]
                if property_name == "thermal_conductivity"
                else run_admission
            )
            if unit == "source_native_unit_unresolved":
                property_admission = "conditional_reference"
            property_ceiling = (
                min(run_ceiling, 0.10)
                if property_admission == "conditional_reference"
                else run_ceiling
            )
            method_family, method_detail, fidelity = _polyomics_method(
                property_name, source_row
            )
            rows.append(
                {
                    "source_id": polyomics_identity.source_id,
                    "source_record_id": source_row["source_record_id"],
                    "observation_id": (
                        f"{source_row['record_id']}:{property_name}"
                    ),
                    "canonical_structure": canonical,
                    "system_identity": canonical,
                    "structure_identity_status": "exact_polymer_smiles",
                    "global_structure_family_key": (
                        _global_structure_family_key(canonical)
                    ),
                    "simulation_key": source_row["simulation_key"],
                    "property_name": property_name,
                    "value": _finite_numeric_text(
                        raw_value,
                        context=(
                            f"{source_row['source_record_id']}:{property_name}"
                        ),
                    ),
                    "unit": unit,
                    "unit_status": (
                        "source_native_unit_unresolved"
                        if unit == "source_native_unit_unresolved"
                        else "mapped_from_RadonPy_common_field"
                    ),
                    "method_family": method_family,
                    "method_detail": method_detail,
                    "fidelity_level": fidelity,
                    "temp": source_row.get("temp", ""),
                    "press": source_row.get("press", ""),
                    "gold_admission_status": run_admission,
                    "property_admission_status": property_admission,
                    "source_validation_status": (
                        source_row["thermal_status"]
                        if property_name == "thermal_conductivity"
                        else source_row["equilibrium_status"]
                    ),
                    "record_role": _polyomics_record_role(property_name),
                    "potential_weight_ceiling": property_ceiling,
                    "current_weight_materialized": "false",
                    "training_weight": "",
                    "source_locator": (
                        f"{source_row['source_locator']};field={property_name}"
                    ),
                    "citation_keys": ";".join(
                        polyomics_identity.citation_keys
                    ),
                }
            )

    acs_md_rows = [
        row for row in acs_table_rows if row["target_origin"] == "md"
    ]
    for source_row in acs_md_rows:
        directory = str(source_row["source_directory"])
        identity = identities[directory]
        system_identity = (
            f"{source_row['sample_id']}; composition linked to published Table S1"
        )
        simulation_basis = _joined(
            identity.source_family_id,
            source_row["sample_id"],
            source_row["method_or_test_protocol"],
        )
        simulation_digest = hashlib.sha256(
            simulation_basis.encode("utf-8")
        ).hexdigest()[:24]
        rows.append(
            {
                "source_id": identity.source_id,
                "source_record_id": source_row["source_record_id"],
                "observation_id": source_row["observation_id"],
                "canonical_structure": "",
                "system_identity": system_identity,
                "structure_identity_status": (
                    "formulation_label_with_molar_ratio_link_no_single_smiles"
                ),
                "global_structure_family_key": _global_named_system_family_key(
                    identity.source_family_id, source_row["sample_id"]
                ),
                "simulation_key": f"acs_published_md_{simulation_digest}",
                "property_name": source_row["property_name"],
                "value": _finite_numeric_text(
                    source_row["value"], context=source_row["observation_id"]
                ),
                "unit": source_row["unit"],
                "unit_status": "source_unit_preserved",
                "method_family": "MD",
                "method_detail": source_row["method_or_test_protocol"],
                "fidelity_level": source_row["fidelity_level"],
                "temp": 298,
                "press": 1,
                "gold_admission_status": source_row[
                    "gold_admission_status"
                ],
                "property_admission_status": source_row[
                    "gold_admission_status"
                ],
                "source_validation_status": (
                    "published_value_anomaly_retained"
                    if source_row["protocol_status"].endswith("value_anomaly")
                    else "source_pdf_hash_protocol_and_value_verified"
                ),
                "record_role": "computational_feature_or_mechanistic_reference",
                "potential_weight_ceiling": source_row[
                    "potential_weight_ceiling"
                ],
                "current_weight_materialized": "false",
                "training_weight": "",
                "source_locator": (
                    f"数据/原始/外部数据/新增开放数据/{directory}/"
                    f"{source_row['source_locator']}"
                ),
                "citation_keys": ";".join(identity.citation_keys),
            }
        )

    pufoam_identity = identities[pufoam_directory]
    if pufoam_identity.source_id != BATCH12_SOURCE_PUFOAM:
        raise AssertionError(
            f"第十二批PUFoam来源ID漂移: {pufoam_identity.source_id}, "
            f"expected={BATCH12_SOURCE_PUFOAM}"
        )
    pufoam_ceiling = float(
        profile_by_directory[pufoam_directory]["weight_ceiling"]
    )
    for source_row in build_batch12_pufoam_gold_c_rows():
        if source_row["source_id"] != pufoam_identity.source_id:
            raise AssertionError(
                f"PUFoam数值来源ID漂移: {source_row['source_id']}"
            )
        row = dict(source_row)
        row["citation_keys"] = ";".join(pufoam_identity.citation_keys)
        row["potential_weight_ceiling"] = min(
            float(row["potential_weight_ceiling"]), pufoam_ceiling
        )
        row["source_locator"] = (
            f"数据/原始/外部数据/新增开放数据/{pufoam_directory}/"
            f"{row['source_locator']}"
        )
        rows.append(row)

    expected_existing_counts = {
        radon_identity.source_id: 440,
        polyomics_identity.source_id: 209_905,
        identities["ACS_Figshare_双相演化聚氨酯"].source_id: 5,
        pufoam_identity.source_id: BATCH12_PUFOAM_GOLD_C_COUNT,
    }
    actual_counts = Counter(row["source_id"] for row in rows)
    if actual_counts != expected_existing_counts:
        raise AssertionError(
            f"Gold-C计算数值数量漂移: {dict(actual_counts)}"
        )
    batch10_identity_expectations = {
        "第十批计算_OMG": BATCH10_SOURCE_OMG,
        "第十批计算_OpenPolymerChallenge": BATCH10_SOURCE_OPENPOLY,
    }
    for directory, expected_source_id in batch10_identity_expectations.items():
        actual_source_id = identities[directory].source_id
        if actual_source_id != expected_source_id:
            raise AssertionError(
                f"第十批Gold-C来源ID漂移: {directory}="
                f"{actual_source_id}, expected={expected_source_id}"
            )
    all_source_counts = {
        **dict(actual_counts),
        BATCH10_SOURCE_OMG: BATCH10_OMG_GOLD_C_COUNT,
        BATCH10_SOURCE_OPENPOLY: BATCH10_OPENPOLY_GOLD_C_COUNT,
    }
    radon_structures = {
        row["global_structure_family_key"]
        for row in rows
        if row["source_id"] == radon_identity.source_id
    }
    polyomics_structures = {
        row["global_structure_family_key"]
        for row in rows
        if row["source_id"] == polyomics_identity.source_id
    }
    overlapping_structures = radon_structures & polyomics_structures
    overlapping_polyomics_records = {
        row["source_record_id"]
        for row in rows
        if row["source_id"] == polyomics_identity.source_id
        and row["global_structure_family_key"] in overlapping_structures
    }
    if len(overlapping_structures) != 11 or len(overlapping_polyomics_records) != 58:
        raise AssertionError(
            "RadonPy/PolyOmics跨源结构重合漂移: "
            f"structures={len(overlapping_structures)}, "
            f"polyomics_records={len(overlapping_polyomics_records)}"
        )
    metadata = {
        "artifact_role": "normalized_computational_value_reference",
        "artifact_status": "multifidelity_gold_c_reference_not_training_dataset",
        "path": _to_relative(OUTPUT_GOLD_C_VALUES),
        "row_count": sum(all_source_counts.values()),
        "source_value_counts": all_source_counts,
        "cross_source_overlap_structure_count": len(overlapping_structures),
        "cross_source_overlap_polyomics_record_count": len(
            overlapping_polyomics_records
        ),
        "current_weight_materialized": False,
    }
    # 第十批 OMG 的 1,191,900 行保持流式，避免百万个 26 列 dict 常驻内存。
    return chain(
        rows, _governed_batch10_gold_c_rows(profiles, identities)
    ), metadata


def _gold_e_table_value_rows(
    materialized_table_rows: list[dict[str, Any]],
    identities: dict[str, SourceIdentity],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    batch10_identity_expectations = {
        "第十批实验_ScienceDB643": BATCH10_SOURCE_SCIENCEDB,
        "第十批实验_无溶剂PU反应动力学": BATCH10_SOURCE_KINETICS,
    }
    for directory, expected_source_id in batch10_identity_expectations.items():
        actual_source_id = identities[directory].source_id
        if actual_source_id != expected_source_id:
            raise AssertionError(
                f"第十批Gold-E来源ID漂移: {directory}="
                f"{actual_source_id}, expected={expected_source_id}"
            )
    rows: list[dict[str, Any]] = []
    for source_row in materialized_table_rows:
        if source_row["target_origin"] not in {
            "experimental",
            "experimental_transformed",
        }:
            continue
        directory = str(source_row["source_directory"])
        identity = identities[directory]
        row = {
            "source_id": identity.source_id,
            "source_scope_id": identity.source_scope_id,
            "source_family_id": identity.source_family_id,
            **source_row,
        }
        row["citation_keys"] = ";".join(identity.citation_keys)
        row["source_locator"] = (
            f"数据/原始/外部数据/新增开放数据/{directory}/"
            f"{source_row['source_locator']}"
        )
        rows.append(row)
    source_counts = Counter(row["source_id"] for row in rows)
    metadata = {
        "artifact_role": "normalized_experimental_scalar_reference",
        "artifact_status": "multifidelity_gold_e_reference_not_training_dataset",
        "path": _to_relative(OUTPUT_GOLD_E_TABLES),
        "row_count": len(rows),
        "source_value_counts": dict(source_counts),
        "admitted_reference_count": sum(
            row["gold_admission_status"] == "admitted_reference"
            for row in rows
        ),
        "conditional_reference_count": sum(
            row["gold_admission_status"] == "conditional_reference"
            for row in rows
        ),
        "current_weight_materialized": False,
    }
    return rows, metadata


def _computational_records(profile: dict[str, Any], identity: SourceIdentity, path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, row in enumerate(_read_tsv(path)):
        level = _first(row, "记录层级", "reduction_level")
        point_count = int(_number(row.get("point_count")) or 0)
        if point_count > 1:
            granularity = "curve"
        elif any(
            token in level.lower()
            for token in ("run", "path", "trajectory", "system", "路径", "体系", "运行")
        ):
            granularity = "run"
        else:
            granularity = "scalar"
        raw_key = _first(row, "observation_id", "record_id") or _joined(
            _first(row, "体系或路径", "system_id"),
            _first(row, "观测或计算", "property_name"),
            level,
        )
        record = _base_record(profile, identity, granularity, raw_key or f"row:{index + 2}", index)
        source_material_key = _first(
            row,
            "体系或路径",
            "system_id",
            "monomer_ID",
            "material_id",
            "smiles",
        )
        source_candidate_key = _first(
            row,
            "candidate_id",
            "mapped_candidate_id",
            "system_id",
            "体系或路径",
        )
        if not source_candidate_key and row.get("monomer_ID"):
            source_candidate_key = _joined(identity.source_id, row["monomer_ID"])
        global_structure_key = ""
        if (
            profile["source_directory"] == "第九批计算_RadonPy_PI1070"
            and row.get("smiles")
        ):
            global_structure_key = _global_structure_family_key(
                _canonical_polymer_structure(row["smiles"])
            )
        status = _record_status(profile, row)
        record.update(
            {
                "task": _first(row, "观测或计算", "property_name") or profile["task"],
                "target_origin": _target_origin(
                    profile,
                    _first(row, "target_origin", "data_origin", "来源类型", "数据来源类型"),
                ),
                "record_role": _first(row, "record_role")
                or "computational_property_observation",
                "method_family": _first(row, "method_family"),
                "fidelity_level": _first(row, "fidelity_level")
                or _first(row, "method_family"),
                "candidate_id": global_structure_key or source_candidate_key,
                "material_formula_key": source_material_key,
                "run_key": source_material_key if granularity == "run" else "",
                "curve_key": raw_key if granularity == "curve" else "",
                "scalar_key": _first(row, "观测或计算", "property_name") if granularity == "scalar" else "",
                "leakage_group_key": global_structure_key
                or _first(row, "split_group")
                or _joined(identity.source_family_id, source_material_key)
                or identity.source_family_id,
                "leakage_key_status": "explicit_record_group",
                "quality_status": status,
                "weight_ceiling": _record_ceiling(profile, row, status),
                "run_count": 1 if granularity == "run" else 0,
                "curve_count": 1 if granularity == "curve" else 0,
                "scalar_count": 1 if granularity == "scalar" else 0,
                "point_count": point_count if granularity == "curve" else 0,
                "numeric_value_count": (
                    point_count
                    if granularity == "curve"
                    else 1
                    if _number(row.get("数值") or row.get("value")) is not None
                    else 0
                ),
                "source_locator": _first(row, "source_location")
                or f"{_to_relative(path)}#row={index + 2}",
                "audit_basis": _to_relative(path),
                "decision_basis": _first(row, "准入状态", "训练权重状态", "decision"),
                "notes": _joined(
                    f"method={_first(row, 'method_family')}",
                    _first(row, "independence_note"),
                    _first(row, "quality_note", "备注", "notes"),
                ),
                "_declared_gold_admission_status": _first(
                    row, "gold_admission_status", "admission_status"
                ),
            }
        )
        records.append(record)
    return records


def _polyomics_records(
    profile: dict[str, Any], identity: SourceIdentity
) -> list[dict[str, Any]]:
    path = POLYOMICS_SOURCE_DIR / "PU计算参考.tsv"
    if not path.exists():
        return []
    _register_input(path)
    records: list[dict[str, Any]] = []
    for index, row in enumerate(_read_tsv(path)):
        raw_key = row["record_id"]
        record = _base_record(profile, identity, "run", raw_key, index)
        admission = row["gold_admission_status"]
        if admission == "admitted_reference":
            status = "降权"
        elif admission == "conditional_reference":
            status = "仅验证"
        else:
            raise ValueError(f"PolyOmics非法准入状态: {admission}")
        present_properties = [
            field
            for field in POLYOMICS_PROPERTY_FIELDS
            if str(row.get(field, "")).strip()
        ]
        property_count = len(present_properties)
        conditional_property_count = sum(
            admission == "conditional_reference"
            or (
                field == "thermal_conductivity"
                and row["thermal_target_admission"] != "admitted_reference"
            )
            for field in present_properties
        )
        admitted_property_count = property_count - conditional_property_count
        global_structure_key = _global_structure_family_key(
            _canonical_polymer_structure(row["canonical_component_smiles"])
        )
        record.update(
            {
                "task": f"PolyOmics {row['class_scope']} DFT/MD多性质计算",
                "target_origin": "computational",
                "record_role": "computational_property_bundle",
                "method_family": "DFT+MD multi-property bundle",
                "fidelity_level": "source_native_multistage_protocol",
                "candidate_id": global_structure_key,
                "raw_sample_key": row["source_record_id"],
                "material_formula_key": row["structure_key"],
                "run_key": row["simulation_key"],
                "leakage_group_key": global_structure_key,
                "leakage_key_status": "explicit_record_group",
                "quality_status": status,
                "weight_ceiling": min(
                    float(profile["weight_ceiling"]),
                    0.10 if admission == "conditional_reference" else float(row["potential_weight_ceiling"]),
                ),
                "run_count": 1,
                "scalar_count": admitted_property_count,
                "numeric_value_count": property_count,
                "dedup_status": (
                    "来源内唯一结构首次出现"
                    if row["independent_material_increment_within_source"] == "1"
                    else "来源内结构重复；只增厚模拟工况或同类证据"
                ),
                "source_locator": row["source_locator"],
                "audit_basis": _to_relative(path),
                "decision_basis": _joined(
                    row["class_scope"],
                    f"equilibrium={row['equilibrium_status']}",
                    f"thermal={row['thermal_target_admission']}",
                ),
                "notes": _joined(
                    f"structure_key={row['structure_key']}",
                    f"simulation_key={row['simulation_key']}",
                    f"nonempty_properties={property_count}",
                    f"admitted_properties={admitted_property_count}",
                    f"conditional_properties={conditional_property_count}",
                    "不是实验真值；训练权重尚未物化",
                ),
                "_declared_gold_admission_status": admission,
            }
        )
        records.append(record)
    return records


def _specific_records(profile: dict[str, Any], identity: SourceIdentity) -> list[dict[str, Any]]:
    source = profile["source_directory"]
    base = _profile_base(profile)
    records: list[dict[str, Any]] = []
    summary_path = base / "内容审计摘要.json"
    summary_sources = {
        "QUB_生物基三重自修复TPU",
        "Texas_湿干单根电纺PU纤维力学",
        "PCL_GitLFS轨迹补采",
        "Zenodo_PCL软段构象粗粒化MD",
        "Zenodo_TPU回收封端剂DFT与机器学习",
    }
    summary = (
        _read_json(summary_path)
        if source in summary_sources and summary_path.exists()
        else None
    )

    if source == "QUB_生物基三重自修复TPU" and summary:
        for index, row in enumerate(summary["curve_records"]):
            raw_key = row["label"]
            record = _base_record(profile, identity, "curve", raw_key, index)
            status = _record_status(profile, row)
            record.update(
                {
                    "task": row["modality"],
                    "material_formula_key": row.get("formulation", ""),
                    "specimen_key": row.get("specimen_id", ""),
                    "curve_key": row["label"],
                    "leakage_group_key": row.get("leakage_group") or identity.source_family_id,
                    "leakage_key_status": "explicit_record_group",
                    "quality_status": status,
                    "weight_ceiling": _record_ceiling(profile, row, status),
                    "curve_count": 1,
                    "point_count": row.get("point_rows", 0),
                    "numeric_value_count": row.get("point_rows", 0) * 2,
                    "dedup_status": "精确重复隔离" if row.get("cross_file_duplicate_of") else "唯一曲线",
                    "source_locator": row.get("relative_path", ""),
                    "audit_basis": _to_relative(summary_path),
                    "decision_basis": row.get("effective_eligibility", ""),
                    "notes": f"grade={row.get('effective_grade', '')}",
                }
            )
            records.append(record)

    if source == "Texas_湿干单根电纺PU纤维力学" and summary:
        for index, row in enumerate(summary["纤维观测"]):
            raw_key = row["fiber_csv_id"]
            record = _base_record(profile, identity, "specimen", raw_key, index)
            group = _joined(identity.source_family_id, row["material_code"], row["test_date_batch"], row["hydration_condition"])
            record.update(
                {
                    "task": "单根纤维加载—恢复",
                    "material_formula_key": row["material_code"],
                    "specimen_key": raw_key,
                    "leakage_group_key": group,
                    "leakage_key_status": "explicit_record_group",
                    "quality_status": "降权",
                    "weight_ceiling": profile["weight_ceiling"],
                    "specimen_count": 1,
                    "curve_count": row.get("加载或恢复曲线段数", 0),
                    "point_count": row.get("机械数据点行数", 0),
                    "numeric_value_count": row.get("有限数值单元格数", 0),
                    "source_locator": f"{_to_relative(summary_path)}#纤维观测[{index}]",
                    "audit_basis": _to_relative(summary_path),
                    "decision_basis": "逐纤维审计记录",
                    "notes": f"condition={row['hydration_condition']}; diameter_um={row['直径_um']}",
                }
            )
            records.append(record)

    if source == "PCL_GitLFS轨迹补采" and summary:
        for index, row in enumerate(summary["轨迹"]):
            raw_key = row["OID"]
            record = _base_record(profile, identity, "run", raw_key, index)
            group = _joined(identity.source_family_id, row["链长"], row["环境"], row["变体"])
            record.update(
                {
                    "task": "PCL CGMD轨迹运行",
                    "material_formula_key": f"PCL-{row['链长']}",
                    "run_key": _joined(row["链长"], row["环境"], row["变体"]),
                    "leakage_group_key": group,
                    "leakage_key_status": "explicit_record_group",
                    "quality_status": "隔离",
                    "weight_ceiling": 0.0,
                    "run_count": 1,
                    "point_count": row["帧数"],
                    "numeric_value_count": row["帧数"],
                    "source_locator": row["本地文件"],
                    "audit_basis": _to_relative(summary_path),
                    "decision_basis": f"license_missing; completion={row['完成状态']}",
                    "notes": "轨迹帧强相关；补采与Zenodo母来源同一来源家族。",
                }
            )
            records.append(record)

    if source == "Zenodo_PCL软段构象粗粒化MD" and summary:
        for index, row in enumerate(summary["真实TRR轨迹"]["运行"]):
            raw_key = row["trr_sha256"]
            record = _base_record(profile, identity, "run", raw_key, index)
            path = row["path"]
            record.update(
                {
                    "task": "PCL CGMD轨迹运行",
                    "material_formula_key": "PCL",
                    "run_key": path,
                    "leakage_group_key": _joined(identity.source_family_id, path.rsplit("/traj.trr.bz2", 1)[0]),
                    "leakage_key_status": "explicit_record_group",
                    "quality_status": "仅验证",
                    "weight_ceiling": 0.0,
                    "run_count": 1,
                    "point_count": row["frame_count"],
                    "numeric_value_count": row["frame_count"],
                    "source_locator": path,
                    "audit_basis": _to_relative(summary_path),
                    "decision_basis": "training_blocked_pending_mapping_and_rights",
                    "notes": f"completion={row['completion_status']}",
                }
            )
            records.append(record)

    if source == "Zenodo_TPU回收封端剂DFT与机器学习" and summary:
        for index, row in enumerate(summary["workbook_compounds"]):
            raw_key = row["capping_agent"]
            record = _base_record(profile, identity, "scalar", raw_key, index)
            is_holdout = row.get("split") == "locked_holdout"
            status = "仅验证" if is_holdout else "降权"
            record.update(
                {
                    "task": "实验解封温度+DFT描述符",
                    "material_formula_key": row["gaussian_compound_id"],
                    "scalar_key": "Tdeblock_C",
                    "leakage_group_key": _joined(identity.source_family_id, row["gaussian_compound_id"]),
                    "leakage_key_status": "explicit_record_group",
                    "quality_status": status,
                    "weight_ceiling": 0.0 if is_holdout else profile["weight_ceiling"],
                    "scalar_count": 1,
                    "numeric_value_count": 10,
                    "source_locator": f"{_to_relative(summary_path)}#workbook_compounds[{index}]",
                    "audit_basis": _to_relative(summary_path),
                    "decision_basis": f"original_study_split={row.get('split')}; project_split_not_created",
                    "notes": "原研究split仅作来源元数据，不是本项目训练划分。",
                }
            )
            records.append(record)

    return records


def _virtual_candidate_records(
    profiles: list[dict[str, Any]],
    baseline_profiles: list[dict[str, Any]],
    identities: dict[str, SourceIdentity],
    candidate_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidate_profiles = [*baseline_profiles, *profiles]
    profile_by_source_id: dict[str, dict[str, Any]] = {}
    identity_by_source_id: dict[str, SourceIdentity] = {}
    for profile in candidate_profiles:
        identity = identities[profile["source_directory"]]
        if identity.source_id in profile_by_source_id:
            continue
        profile_by_source_id[identity.source_id] = profile
        identity_by_source_id[identity.source_id] = identity

    records: list[dict[str, Any]] = []
    for index, row in enumerate(candidate_rows):
        candidate_id = str(row["candidate_id"])
        candidate_source_id = str(row["source_id"])
        if candidate_source_id not in profile_by_source_id:
            raise ValueError(f"Gold-V候选缺少来源画像: {candidate_source_id}")
        profile = profile_by_source_id[candidate_source_id]
        identity = identity_by_source_id[candidate_source_id]
        audit_basis = (
            _to_relative(SMIPOLY_CANDIDATE_INPUT)
            if candidate_source_id == "ds_smipoly_monomers"
            else _to_relative(POLYUNIVERSE_DINCO_SUMMARY)
            if candidate_source_id == POLYUNIVERSE_DINCO_SOURCE_ID
            else _to_relative(_audit_paths(profile)[0])
        )
        record = _base_record(
            profile, identity, "candidate", candidate_id, index
        )
        record.update(
            {
                "completeness": (
                    "structure_validated_role_rule_classified"
                    if candidate_source_id == "ds_smipoly_monomers"
                    else "exact_diinco_structure_validated_and_tiered"
                    if candidate_source_id == POLYUNIVERSE_DINCO_SOURCE_ID
                    else "fragment_structure_validated"
                ),
                "task": str(row["tpu_role"]),
                "gold_layer": str(row["gold_layer"]),
                "gold_admission_status": str(row["gold_admission_status"]),
                "_declared_gold_admission_status": str(
                    row["gold_admission_status"]
                ),
                "target_origin": str(row["data_origin"]),
                "record_role": "virtual_candidate_reference",
                "method_family": str(row["data_origin"]),
                "fidelity_level": str(row["fidelity_level"]),
                "candidate_id": candidate_id,
                "raw_sample_key": str(row["source_record_id"]),
                "material_formula_key": str(row["inchikey"]),
                "leakage_group_key": _joined(
                    "standard_inchikey_connectivity_family",
                    str(row["inchikey"]).split("-", 1)[0],
                ),
                "leakage_key_status": "explicit_candidate_family",
                "quality_status": profile["quality_status"],
                "weight_ceiling": float(
                    row["direct_property_supervision_weight_ceiling"]
                ),
                "source_locator": str(row["source_locator"]),
                "audit_basis": audit_basis,
                "decision_basis": str(row["role_basis"]),
                "notes": _joined(
                    f"screening_scope={row['screening_scope']}",
                    f"role_confidence={row['role_confidence']}",
                    f"fidelity={row['fidelity_level']}",
                    "结构已验证；没有直接实验性能标签",
                ),
            }
        )
        records.append(record)
    return records


def _materialized_table_manifest_records(
    profiles: list[dict[str, Any]],
    identities: dict[str, SourceIdentity],
    materialized_table_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    profile_by_directory = {
        profile["source_directory"]: profile for profile in profiles
    }
    records: list[dict[str, Any]] = []
    for index, row in enumerate(materialized_table_rows):
        directory = str(row["source_directory"])
        profile = profile_by_directory[directory]
        identity = identities[directory]
        record = _base_record(
            profile, identity, "scalar", str(row["observation_id"]), index
        )
        record_kind = str(row["record_kind"])
        if record_kind == "formulation_component":
            role = "formulation_descriptor"
        elif record_kind == "process_condition":
            role = "process_descriptor"
        elif row["target_origin"] == "md":
            role = "computational_property_observation"
        else:
            role = "property_observation"
        uncertainty_count = int(row["uncertainty_value"] != "")
        record.update(
            {
                "task": row["property_name"],
                "target_origin": row["target_origin"],
                "record_role": role,
                "method_family": (
                    "MD" if row["target_origin"] == "md" else "experiment"
                ),
                "fidelity_level": row["fidelity_level"],
                "candidate_id": row["formulation_id"],
                "material_formula_key": row["formulation_id"] or row["sample_id"],
                "specimen_key": "",
                "scalar_key": row["observation_id"],
                "leakage_group_key": _joined(
                    identity.source_family_id, row["split_group"]
                ),
                "leakage_key_status": "explicit_record_group",
                "quality_status": profile["quality_status"],
                "weight_ceiling": float(row["potential_weight_ceiling"]),
                "scalar_count": 1,
                "numeric_value_count": 1 + uncertainty_count,
                "source_locator": (
                    f"数据/原始/外部数据/新增开放数据/{directory}/"
                    f"{row['source_locator']}"
                ),
                "audit_basis": _to_relative(
                    ACS_TABLE_AUDIT_SCRIPT_PATH
                    if directory.startswith("ACS_Figshare_")
                    else BATCH10_MATERIALIZATION_SCRIPT_PATH
                ),
                "decision_basis": _joined(
                    row["gold_admission_status"],
                    row["mapping_status"],
                    row["protocol_status"],
                ),
                "notes": row["notes"],
                "_declared_gold_admission_status": row[
                    "gold_admission_status"
                ],
            }
        )
        records.append(record)
    return records


def _build_manifest(
    profiles: list[dict[str, Any]],
    baseline_profiles: list[dict[str, Any]],
    identities: dict[str, SourceIdentity],
    ledger: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    materialized_table_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    profile_by_dir = {profile["source_directory"]: profile for profile in [*baseline_profiles, *profiles]}
    records: list[dict[str, Any]] = []
    for index, source_row in enumerate(ledger):
        profile = profile_by_dir[source_row["source_directory"]]
        identity = identities[source_row["source_directory"]]
        raw_key = source_row["source_scope_id"]
        record = _base_record(profile, identity, "source", raw_key, index)
        record.update(
            {
                "completeness": profile["completeness"],
                "quality_status": profile["quality_status"],
                "weight_ceiling": profile["weight_ceiling"] if profile["quality_status"] in {"入选", "降权"} else 0.0,
                "specimen_count": source_row["specimen_count"] or 0,
                "run_count": source_row["run_count"] or 0,
                "curve_count": source_row["curve_count_observed"] or 0,
                "scalar_count": source_row["scalar_count_observed"] or 0,
                "point_count": source_row["point_count_observed"] or 0,
                "numeric_value_count": source_row["numeric_value_count"] or 0,
                "audit_basis": source_row["audit_basis"],
                "decision_basis": "来源级聚合；候选计数不等于模型就绪数",
                "notes": profile["notes"],
            }
        )
        records.append(record)

    records.extend(
        _virtual_candidate_records(
            profiles, baseline_profiles, identities, candidate_rows
        )
    )
    records.extend(
        _materialized_table_manifest_records(
            profiles, identities, materialized_table_rows
        )
    )

    for profile in profiles:
        identity = identities[profile["source_directory"]]
        base = _profile_base(profile)
        for filename in (
            "曲线审计清单.tsv",
            "曲线解析清单.tsv",
            "实验曲线审计清单.tsv",
            "三点弯曲曲线审计清单.tsv",
            "DMA运行审计清单.tsv",
        ):
            path = base / filename
            if not path.exists():
                continue
            for index, row in enumerate(_read_tsv(path)):
                if (
                    filename == "曲线审计清单.tsv"
                    and row.get("record_id")
                    in ACS_MATERIALIZED_EVIDENCE_GROUPS.get(
                        profile["source_directory"], frozenset()
                    )
                ):
                    continue
                records.append(_tabular_record(profile, identity, row, path, index))
        scalar_path = base / "标量审计清单.tsv"
        if scalar_path.exists():
            records.extend(_scalar_records(profile, identity, scalar_path))
        for computation_filename in ("计算观测清单.tsv", "PU计算观测清单.tsv"):
            computation_path = base / computation_filename
            if computation_path.exists():
                records.extend(
                    _computational_records(profile, identity, computation_path)
                )
        if profile["source_directory"] == "第九批计算_PolyOmics":
            records.extend(_polyomics_records(profile, identity))
        records.extend(_specific_records(profile, identity))

        if profile["source_directory"] == "Jagiellonian_硬段从头算MD":
            path = base / "XYZ解析清单.tsv"
            for index, row in enumerate(_read_tsv(path)):
                raw_key = row["硬段体系"]
                record = _base_record(profile, identity, "run", raw_key, index)
                record.update(
                    {
                        "task": "硬段优化结构/拓扑表示",
                        "material_formula_key": raw_key,
                        "run_key": raw_key,
                        "leakage_group_key": _joined(identity.source_family_id, raw_key),
                        "leakage_key_status": "explicit_record_group",
                        "quality_status": "降权",
                        "weight_ceiling": profile["weight_ceiling"],
                        "run_count": 1,
                        "point_count": _number(row.get("实际坐标行")) or 0,
                        "numeric_value_count": (_number(row.get("实际坐标行")) or 0) * 3,
                        "source_locator": f"{_to_relative(path)}#row={index + 2}",
                        "audit_basis": _to_relative(path),
                        "decision_basis": "坐标单位未声明，仅作尺度不变表示",
                        "notes": row.get("软件", ""),
                    }
                )
                records.append(record)

        if profile["source_directory"] == "SND_TPU导电轨迹循环拉伸":
            path = base / "电阻表解析清单.tsv"
            for index, row in enumerate(_read_tsv(path)):
                raw_key = _joined(row["固化温度_C"], row["线宽_cm"])
                values = [_number(value) for key, value in row.items() if key not in {"固化温度_C", "线宽_cm"}]
                record = _base_record(profile, identity, "scalar", raw_key, index)
                record.update(
                    {
                        "task": "固化—几何—电阻保持",
                        "material_formula_key": "TPU基底导电轨迹",
                        "scalar_key": "resistance_Ohm_grid",
                        "leakage_group_key": _joined(identity.source_family_id, raw_key),
                        "leakage_key_status": "explicit_record_group",
                        "quality_status": "降权",
                        "weight_ceiling": profile["weight_ceiling"],
                        "scalar_count": sum(value is not None for value in values),
                        "numeric_value_count": sum(value is not None for value in values),
                        "source_locator": f"{_to_relative(path)}#row={index + 2}",
                        "audit_basis": _to_relative(path),
                        "decision_basis": "应用专用标量组",
                        "notes": "同一固化温度—线宽条件内12个电阻格点共享组键。",
                    }
                )
                records.append(record)

        if profile["source_directory"] == "ScienceDB_TPU芳纶纳米纤维能量吸收":
            path = base / "图像解析清单.tsv"
            for index, row in enumerate(_read_tsv(path)):
                raw_key = row["文件"]
                record = _base_record(profile, identity, "evidence_group", raw_key, index)
                record.update(
                    {
                        "task": "无标签形貌图像证据",
                        "quality_status": "仅验证",
                        "weight_ceiling": 0.0,
                        "source_locator": raw_key,
                        "audit_basis": _to_relative(path),
                        "decision_basis": "缺逐图标签/倍率/标尺/试样映射",
                        "notes": row.get("科学语义", ""),
                    }
                )
                records.append(record)

        if profile["source_directory"] == "Zenodo_TPU_SWCNT热电":
            path = base / "工作簿解析清单.tsv"
            for index, row in enumerate(_read_tsv(path)):
                raw_key = row["文件"]
                record = _base_record(profile, identity, "scalar", raw_key, index)
                material = row.get("样品代码", "")
                finite = sum(
                    _number(row.get(key)) is not None
                    for key in ("长度_mm", "宽度_mm", "厚度_mm", "测量温度_C", "Seebeck_uV_K", "电阻_kOhm", "电阻率_Ohm_cm", "功率_uW")
                )
                record.update(
                    {
                        "task": "TPU/SWCNT热电标量采集",
                        "material_formula_key": material,
                        "scalar_key": raw_key,
                        "leakage_group_key": _joined(identity.source_family_id, material) or identity.source_family_id,
                        "leakage_key_status": "explicit_record_group" if material else "coarse_source_family",
                        "quality_status": "降权",
                        "weight_ceiling": profile["weight_ceiling"],
                        "scalar_count": 1,
                        "numeric_value_count": finite,
                        "source_locator": f"{_to_relative(path)}#row={index + 2}",
                        "audit_basis": _to_relative(path),
                        "decision_basis": "热电应用任务；纯TPU力学上限另降至0.20",
                        "notes": "",
                    }
                )
                records.append(record)

        if profile["source_directory"] == "Zenodo_TPU回收封端剂DFT与机器学习":
            path = base / "Gaussian解析清单.tsv"
            for index, row in enumerate(_read_tsv(path)):
                raw_key = row["archive_member"]
                record = _base_record(profile, identity, "run", raw_key, index)
                compound = row.get("compound_id", "")
                scientific_status = row.get("scientific_status", "")
                normal_termination_count = int(row.get("normal_termination_count") or 0)
                error_termination_count = int(row.get("error_termination_count") or 0)
                if (
                    scientific_status == "computed_gold_verified"
                    and normal_termination_count > 0
                    and error_termination_count == 0
                ):
                    status = "降权"
                    admission_status = "admitted_reference"
                    potential_ceiling = float(profile["weight_ceiling"])
                elif (
                    scientific_status.startswith("computed_silver_")
                    and normal_termination_count > 0
                    and error_termination_count == 0
                ):
                    status = "仅验证"
                    admission_status = "conditional_reference"
                    potential_ceiling = (
                        0.10
                        if "small_negative_review" in scientific_status
                        else 0.25
                    )
                else:
                    status = "隔离"
                    admission_status = "blocked"
                    potential_ceiling = 0.0
                record.update(
                    {
                        "task": row.get("calculation_level", "DFT计算输出"),
                        "target_origin": "computational",
                        "material_formula_key": compound,
                        "run_key": raw_key,
                        "leakage_group_key": _joined(identity.source_family_id, compound),
                        "leakage_key_status": "explicit_record_group",
                        "quality_status": status,
                        "weight_ceiling": potential_ceiling,
                        "_declared_gold_admission_status": admission_status,
                        "run_count": 1,
                        "numeric_value_count": 1,
                        "source_locator": raw_key,
                        "audit_basis": _to_relative(path),
                        "decision_basis": scientific_status,
                        "notes": (
                            f"normal_termination_count={normal_termination_count}; "
                            "同一化合物的多计算层级/构象共享泄漏组，输出文件不是独立化学样本。"
                        ),
                    }
                )
                records.append(record)

    for record in records:
        profile = profile_by_dir[record["source_directory"]]
        declared_admission = str(
            record.pop("_declared_gold_admission_status", "") or ""
        ).strip()
        if declared_admission not in ENUMS["gold_admission_status"]:
            if declared_admission.startswith("admitted"):
                declared_admission = "admitted_reference"
            elif declared_admission.startswith("conditional"):
                declared_admission = "conditional_reference"
            elif declared_admission.startswith("evidence_only"):
                declared_admission = "evidence_only"
            elif declared_admission.startswith("blocked"):
                declared_admission = "blocked"
        if declared_admission == "evidence_only":
            record["target_origin"] = "evidence"
            record["quality_status"] = "仅验证"
            record["weight_ceiling"] = 0.0
        elif declared_admission == "blocked":
            record["weight_ceiling"] = 0.0
        if record["target_origin"] == "simulation_input":
            record["record_role"] = "simulation_input"
        elif record["target_origin"] == "evidence":
            record["record_role"] = "evidence"
        elif (
            record["target_origin"]
            in {
                "computational",
                "dft",
                "aimd",
                "md",
                "coarse_grained_md",
                "cfd",
                "population_balance",
                "reaction_kinetics_simulation",
                "finite_element",
            }
            and record["record_role"] == "property_observation"
        ):
            record["record_role"] = "computational_property_observation"
        record["gold_layer"] = _gold_layer_for_target(
            profile, record["target_origin"]
        )
        record["gold_admission_status"] = (
            declared_admission
            if declared_admission in ENUMS["gold_admission_status"]
            else _gold_admission_status(
                profile, record["quality_status"], record["gold_layer"]
            )
        )
        for field in PATH_BEARING_MANIFEST_FIELDS:
            record[field] = _normalize_layout_text(record.get(field, ""))
    return records


def _is_reparse_or_symlink(path: Path) -> bool:
    info = path.lstat()
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & reparse_flag)


def _assert_safe_output(path: Path) -> None:
    root = Path(os.path.abspath(ROOT))
    target = Path(os.path.abspath(path))
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"输出路径越出工作区: {target}") from exc
    if _is_reparse_or_symlink(root):
        raise ValueError(f"工作区根目录是符号链接或重解析点: {root}")
    current = root
    for part in relative.parts[:-1]:
        current /= part
        if current.exists() and _is_reparse_or_symlink(current):
            raise ValueError(f"输出父路径含符号链接或重解析点: {current}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if _is_reparse_or_symlink(target.parent):
        raise ValueError(f"输出父目录是符号链接或重解析点: {target.parent}")
    if target.exists():
        if _is_reparse_or_symlink(target) or not stat.S_ISREG(target.lstat().st_mode):
            raise ValueError(f"拒绝覆盖非普通文件或重解析点: {target}")


def _atomic_write_text(path: Path, text: str, *, encoding: str) -> None:
    _assert_safe_output(path)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        if _is_reparse_or_symlink(temporary) or not stat.S_ISREG(temporary.lstat().st_mode):
            raise ValueError(f"临时输出不是普通文件: {temporary}")
        with os.fdopen(file_descriptor, "w", encoding=encoding, newline="") as handle:
            file_descriptor = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        if temporary.exists():
            temporary.unlink()


def _write_gzip_csv(
    path: Path,
    columns: list[str],
    rows: Iterable[dict[str, Any]],
    *,
    expected_row_count: int | None = None,
) -> None:
    """Atomically write a remotely stream-readable deterministic gzip CSV."""

    _assert_safe_output(path)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        if _is_reparse_or_symlink(temporary) or not stat.S_ISREG(
            temporary.lstat().st_mode
        ):
            raise ValueError(f"临时输出不是普通文件: {temporary}")
        with os.fdopen(file_descriptor, "wb") as raw_handle:
            file_descriptor = -1
            with gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=9,
                fileobj=raw_handle,
                mtime=0,
            ) as gzip_handle:
                text_handle = io.TextIOWrapper(
                    gzip_handle,
                    encoding="utf-8",
                    newline="",
                )
                writer = csv.DictWriter(
                    text_handle,
                    fieldnames=columns,
                    extrasaction="raise",
                    lineterminator="\n",
                )
                writer.writeheader()
                row_count = 0
                for row in rows:
                    writer.writerow(row)
                    row_count += 1
                if (
                    expected_row_count is not None
                    and row_count != expected_row_count
                ):
                    raise AssertionError(
                        f"流式输出行数漂移: {path.name}; "
                        f"expected={expected_row_count}, actual={row_count}"
                    )
                text_handle.flush()
                text_handle.detach()
            raw_handle.flush()
            os.fsync(raw_handle.fileno())
        os.replace(temporary, path)
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        if temporary.exists():
            temporary.unlink()


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=columns, extrasaction="raise", lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    _atomic_write_text(path, buffer.getvalue(), encoding="utf-8-sig")


def _major_pool_kpis(ledger_by_directory: dict[str, dict[str, Any]]) -> dict[str, Any]:
    definitions = [
        ("DRUM_TPUU_机械回收", 158, 779948),
        ("DRUM_TPUU_低天花板", 28, 113060),
        ("QUB_生物基三重自修复TPU", 68, 224733),
        ("Mendeley_SLS_TPU工艺力学", 350, 1787452),
        ("Zenodo_TPU1301热黏弹黏塑本构", 80, 5818564),
        ("Mendeley_商业TPU温度疲劳多工况", 196, 333492),
        ("Mendeley_FDM_TPU晶格与基材力学", 76, 712240),
        ("Zenodo_标准化弹性体表征", 42, 1341840),
        ("Zenodo_商业TPU多材料打印传感", 28, 821133),
        ("外部数据/TPU_HBond_2021_Source_Main.xlsx", 53, 25972),
        ("Zenodo_可打印自愈可回收PU生物电子", 30, 155872),
        ("Mendeley_TPU实验仿真曲线", 3, 144009),
    ]
    for directory, _, _ in definitions:
        if directory not in ledger_by_directory:
            raise AssertionError(f"主要实验池来源缺失: {directory}")
    return {
        "major_experimental_curve_history_lower_bound": sum(item[1] for item in definitions),
        "major_experimental_curve_point_lower_bound": sum(item[2] for item in definitions),
        "included_source_scopes": [item[0] for item in definitions],
        "definition": "跨非重叠来源的实验曲线/历史下界；包含核心TPU、商业牌号与迁移域，不等于化学独立配方数。",
    }


def _build_summary(
    ledger: list[dict[str, Any]],
    manifest: list[dict[str, Any]],
    candidate_summary: dict[str, Any],
    audit_as_of_utc: str,
    input_fingerprint_sha256: str,
    input_file_count: int,
    open_source_directories: set[str],
    local_backlog_source_directories: set[str],
) -> dict[str, Any]:
    by_dir = {row["source_directory"]: row for row in ledger}
    profile_counts = Counter(row["quality_status"] for row in ledger)
    source_gold_layer_counts = Counter(row["gold_layer"] for row in ledger)
    source_gold_admission_counts = Counter(row["gold_admission_status"] for row in ledger)
    granularity_counts = Counter(row["record_granularity"] for row in manifest)
    detail_status_counts = Counter(row["quality_status"] for row in manifest)
    manifest_gold_layer_counts = Counter(row["gold_layer"] for row in manifest)
    manifest_gold_admission_counts = Counter(row["gold_admission_status"] for row in manifest)
    core_curve_count = 148 + 28 + 41 + 16
    strict_core_keyed_specimen_count = 148 + 28 + 41
    core_source_directory_keyed_specimen_count = 158 + 28 + 41
    strict_core_formulation_count = 22 + 4 + 4
    core_source_directory_formulation_count = 26 + 4 + 4
    strict_core_batch_count = 23 + 4
    core_source_directory_batch_count = 27 + 4
    strict_core_keyed_curve_point_row_count = 913_608
    v0_1_eom_eligible_curve_point_row_count = 21_489
    # 1119 是来源级原始算术池，其中包含 FDM 的 19 个 hold 试样和 12 个
    # PU 微球复合材料试样。面向 TPU/TPUU 主任务的保守下界必须将二者排除。
    heterogeneous_arithmetic_pool = 158 + 28 + 41 + 350 + 190 + 76 + 184 + 80 + 12
    conservative_tpu_run_specimen_lower_bound = heterogeneous_arithmetic_pool - 19 - 12

    def known_sum(origin_kind: str, field: str) -> dict[str, int]:
        rows = [row for row in ledger if row["origin_kind"] == origin_kind and row[field] is not None]
        return {
            "value": int(sum(row[field] for row in rows)),
            "known_source_scope_count": len(rows),
        }

    known_origin_totals = {
        "experimental_only": {
            "specimen_count": known_sum("实验", "specimen_count"),
            "curve_count_observed": known_sum("实验", "curve_count_observed"),
            "curve_count_candidate": known_sum("实验", "curve_count_candidate"),
            "point_count_observed": known_sum("实验", "point_count_observed"),
        },
        "mixed_experiment_and_simulation": {
            "specimen_count": known_sum("混合", "specimen_count"),
            "curve_count_observed": known_sum("混合", "curve_count_observed"),
            "curve_count_candidate": known_sum("混合", "curve_count_candidate"),
            "point_count_observed": known_sum("混合", "point_count_observed"),
        },
    }
    return {
        "audit_as_of_utc": audit_as_of_utc,
        "input_file_count": input_file_count,
        "input_fingerprint_sha256": input_fingerprint_sha256,
        "training_enabled": False,
        "training_split_created": False,
        "training_weight_materialized": False,
        "model_ready_record_count": 0,
        "v0_2_source_directory_count": len(
            {row["source_directory"] for row in ledger} & open_source_directories
        ),
        "v0_2_independent_source_identity_count": sum(
            int(row["source_identity_count_contribution"] or 0)
            for row in ledger
            if row["source_directory"] in open_source_directories
        ),
        "local_backlog_source_directory_count": len(
            {row["source_directory"] for row in ledger} & local_backlog_source_directories
        ),
        "local_backlog_independent_source_identity_count": sum(
            int(row["source_identity_count_contribution"] or 0)
            for row in ledger
            if row["source_directory"] in local_backlog_source_directories
        ),
        "v0_1_frozen_baseline_source_count": 4,
        "ledger_source_scope_count": len(ledger),
        "total_independent_source_contribution_count": sum(
            int(row["source_identity_count_contribution"] or 0) for row in ledger
        ),
        "source_quality_status_counts": dict(sorted(profile_counts.items())),
        "source_gold_layer_counts": dict(sorted(source_gold_layer_counts.items())),
        "source_gold_admission_status_counts": dict(
            sorted(source_gold_admission_counts.items())
        ),
        "manifest_row_count": len(manifest),
        "manifest_granularity_counts": dict(sorted(granularity_counts.items())),
        "manifest_quality_status_counts": dict(sorted(detail_status_counts.items())),
        "manifest_gold_layer_counts": dict(sorted(manifest_gold_layer_counts.items())),
        "manifest_gold_admission_status_counts": dict(
            sorted(manifest_gold_admission_counts.items())
        ),
        "virtual_candidate_count": candidate_summary["candidate_count"],
        "virtual_candidate_reference_count": candidate_summary["candidate_count"],
        "virtual_candidate_priority1_structure_count": candidate_summary[
            "direct_building_block_count"
        ],
        "virtual_candidate_direct_building_block_count": candidate_summary[
            "screening_scope_counts"
        ].get("direct_tpu_building_block", 0),
        "virtual_candidate_synthesis_primary_count": candidate_summary[
            "screening_scope_counts"
        ].get("direct_tpu_building_block", 0),
        "virtual_candidate_mixture_or_salt_reference_count": candidate_summary[
            "screening_scope_counts"
        ].get("mixture_or_salt_reference", 0),
        "virtual_candidate_not_synthesis_candidate_count": candidate_summary[
            "screening_scope_counts"
        ].get("not_synthesis_candidate", 0),
        "virtual_candidate_functional_group_matched_count": candidate_summary[
            "functional_group_matched_count"
        ],
        "virtual_candidate_unclassified_count": candidate_summary[
            "unclassified_count"
        ],
        "virtual_candidate_role_counts": candidate_summary["role_counts"],
        "virtual_candidate_screening_scope_counts": candidate_summary[
            "screening_scope_counts"
        ],
        "strict_core_calibration_curve_count": core_curve_count,
        "strict_core_calibration_curve_definition": "新增三套核心源中217条具有键控试样的校准曲线，加v0.1 Eom 16条可合法派生曲线；Eom 16条尚未闭合配方—独立试样链，因此233条不是同一composition-linked总体。",
        "strict_core_calibration_curve_point_row_count": strict_core_keyed_curve_point_row_count + v0_1_eom_eligible_curve_point_row_count,
        "strict_core_calibration_complete_point_pair_upper_bound": 935095,
        "strict_core_incomplete_axis_row_evidence": [
            "DRUM_TPUU_机械回收/曲线审计清单.tsv: TPUU_DMTA.xlsx#P4PrCL_TPUUs, 主轴缺失1行",
            "QUB_生物基三重自修复TPU/内容审计摘要.json: P45-heal-3h-4, y_only_rows=1",
        ],
        "strict_core_keyed_specimen_count": strict_core_keyed_specimen_count,
        "strict_core_keyed_specimen_definition": "严格核心键控试样=DRUM主核心148+DRUM低天花板28+QUB 41；排除DRUM的10个桥接/外部/橡皮筋对照试样。",
        "strict_core_keyed_curve_count": strict_core_keyed_specimen_count,
        "strict_core_keyed_curve_point_row_count": strict_core_keyed_curve_point_row_count,
        "strict_core_keyed_complete_point_pair_upper_bound": 913606,
        "core_source_directory_keyed_specimen_count": core_source_directory_keyed_specimen_count,
        "core_source_directory_keyed_specimen_definition": "三个新增核心目录全部键控试样=DRUM 158+DRUM低天花板28+QUB 41；含DRUM 10个桥接/外部/橡皮筋对照，仅作目录全范围盘点。",
        "strict_core_formulation_count": strict_core_formulation_count,
        "strict_core_formulation_definition": "严格新增核心配方=DRUM主核心22+DRUM低天花板4+QUB 4；按规范配方键计，不按材料代码别名计。",
        "core_source_directory_formulation_count": core_source_directory_formulation_count,
        "core_source_directory_formulation_definition": "三个新增核心目录全范围规范配方=DRUM 26+DRUM低天花板4+QUB 4；DRUM额外4个为P4MCL热固、14BDO桥接、Elastollan外部和rubber band对照。",
        "strict_core_batch_count": strict_core_batch_count,
        "strict_core_batch_definition": "严格可确认批次=DRUM主核心23+DRUM低天花板4；QUB批次未知，不补零。",
        "core_source_directory_batch_count": core_source_directory_batch_count,
        "core_source_directory_batch_definition": "三个核心目录全范围可确认批次=DRUM 27+DRUM低天花板4；含4个非核心对照/桥接批次，QUB批次未知。",
        "v0_1_eom_eligible_curve_count": 16,
        "v0_1_eom_eligible_curve_point_row_count": v0_1_eom_eligible_curve_point_row_count,
        "v0_1_eom_specimen_lineage_status": "配方—独立试样链未闭合",
        "conservative_tpu_tpuu_specimen_or_direct_run_lower_bound": conservative_tpu_run_specimen_lower_bound,
        "conservative_tpu_tpuu_definition": "DRUM两源+QUB+SLS+商业TPU疲劳+FDM当前57个selected试样+打印DOE+TPU1301直接实验的异质试样/运行保守盘点下界；不含FDM 19个hold和12个PU微球试样，仍不可直接作为训练样本分母。",
        "selected_source_heterogeneous_specimen_or_run_arithmetic_pool": heterogeneous_arithmetic_pool,
        "selected_source_heterogeneous_pool_definition": "1119仅为选定来源的异质物理试样/直接运行算术池，含FDM 19个hold和12个PU微球试样；不可称作TPU/TPUU可用样本，不可训练、不可作单一统计分母。",
        "known_origin_totals": known_origin_totals,
        **_major_pool_kpis(by_dir),
    }


def _validate(ledger: list[dict[str, Any]], manifest: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    assert len(ledger) == 78
    assert summary["v0_2_source_directory_count"] == 70
    assert summary["v0_2_independent_source_identity_count"] == 69
    assert summary["local_backlog_source_directory_count"] == 4
    assert summary["local_backlog_independent_source_identity_count"] == 4
    assert summary["total_independent_source_contribution_count"] == 77
    assert summary["model_ready_record_count"] == 0
    assert summary["virtual_candidate_count"] == 117629
    assert summary["virtual_candidate_priority1_structure_count"] == 9631
    assert summary["virtual_candidate_direct_building_block_count"] == 9490
    assert summary["virtual_candidate_synthesis_primary_count"] == 9490
    assert summary["virtual_candidate_mixture_or_salt_reference_count"] == 2579
    assert summary["virtual_candidate_not_synthesis_candidate_count"] == 161
    assert summary["virtual_candidate_functional_group_matched_count"] == 113807
    assert summary["virtual_candidate_unclassified_count"] == 531
    expected_layer_by_origin = {
        "实验": "Gold-E",
        "模拟": "Gold-C",
        "混合": "Gold-E+Gold-C",
        "虚拟候选": "Gold-V",
        "证据": "Not-Gold",
    }
    for row in ledger:
        assert row["gold_layer"] == expected_layer_by_origin[row["origin_kind"]]
        if row["gold_layer"] == "Gold-V" and row["quality_status"] != "隔离":
            assert row["gold_admission_status"] == "admitted_reference"
    virtual_candidates = [
        row for row in manifest if row["record_granularity"] == "candidate"
    ]
    assert len(virtual_candidates) == 117629
    assert len({row["candidate_id"] for row in virtual_candidates}) == 117629
    assert all(row["gold_layer"] == "Gold-V" for row in virtual_candidates)
    assert Counter(row["gold_admission_status"] for row in virtual_candidates) == {
        "admitted_reference": 114889,
        "conditional_reference": 2740,
    }
    assert all(float(row["weight_ceiling"]) == 0.0 for row in virtual_candidates)
    gold_c_metadata = summary["gold_c_computational_value_long_table"]
    assert gold_c_metadata["row_count"] == 1_415_788
    assert gold_c_metadata["source_value_counts"][BATCH10_SOURCE_OMG] == 1_191_900
    assert (
        gold_c_metadata["source_value_counts"][BATCH10_SOURCE_OPENPOLY]
        == 4_524
    )
    gold_e_metadata = summary["gold_e_published_table_long_table"]
    assert gold_e_metadata["row_count"] == 5_230
    assert gold_e_metadata["admitted_reference_count"] == 2_756
    assert gold_e_metadata["conditional_reference_count"] == 2_474
    assert summary["strict_core_calibration_curve_count"] == 233
    assert summary["strict_core_keyed_specimen_count"] == 217
    assert summary["strict_core_keyed_curve_count"] == 217
    assert summary["strict_core_keyed_curve_point_row_count"] == 913608
    assert summary["strict_core_keyed_complete_point_pair_upper_bound"] == 913606
    assert summary["core_source_directory_keyed_specimen_count"] == 227
    assert summary["strict_core_formulation_count"] == 30
    assert summary["core_source_directory_formulation_count"] == 34
    assert summary["strict_core_batch_count"] == 27
    assert summary["core_source_directory_batch_count"] == 31
    assert summary["strict_core_calibration_curve_point_row_count"] == 935097
    assert summary["strict_core_calibration_complete_point_pair_upper_bound"] == 935095
    assert summary["conservative_tpu_tpuu_specimen_or_direct_run_lower_bound"] == 1088
    assert summary["selected_source_heterogeneous_specimen_or_run_arithmetic_pool"] == 1119
    assert summary["major_experimental_curve_history_lower_bound"] == 1112
    assert summary["major_experimental_curve_point_lower_bound"] == 12258315
    assert summary["known_origin_totals"]["experimental_only"]["specimen_count"]["value"] == 1465
    assert summary["known_origin_totals"]["experimental_only"]["curve_count_observed"]["value"] == 2665
    assert summary["known_origin_totals"]["experimental_only"]["curve_count_candidate"]["value"] == 2485
    assert summary["known_origin_totals"]["experimental_only"]["point_count_observed"]["value"] == 9545156
    assert summary["known_origin_totals"]["mixed_experiment_and_simulation"]["curve_count_observed"]["value"] == 701
    assert summary["known_origin_totals"]["mixed_experiment_and_simulation"]["point_count_observed"]["value"] == 7862426
    strict_core_manifest = [
        row
        for row in manifest
        if row["record_granularity"] == "curve"
        and row["source_directory"]
        in {
            "DRUM_TPUU_机械回收",
            "DRUM_TPUU_低天花板",
            "QUB_生物基三重自修复TPU",
        }
        and row["quality_status"] == "入选"
    ]
    assert sum(int(row["curve_count"] or 0) for row in strict_core_manifest) == 217
    assert sum(int(row["point_count"] or 0) for row in strict_core_manifest) == 913608
    drum_detail = [
        row
        for row in manifest
        if row["record_granularity"] == "curve"
        and row["source_directory"] == "DRUM_TPUU_机械回收"
    ]
    drum_noncore = [row for row in drum_detail if row["quality_status"] != "入选"]
    assert len(drum_detail) == 158
    assert len(drum_noncore) == 10
    assert Counter(row["quality_status"] for row in drum_noncore) == {
        "降权": 9,
        "仅验证": 1,
    }
    assert all(float(row["weight_ceiling"]) < 1.0 for row in drum_noncore)
    rubber = next(
        row for row in drum_noncore if row["decision_basis"] == "排除核心训练"
    )
    assert rubber["quality_status"] == "仅验证"
    assert float(rubber["weight_ceiling"]) == 0.0
    doe_controls = [
        row
        for row in manifest
        if row["source_directory"] == "Mendeley_TPU压缩打印DOE"
        and row["record_granularity"] == "specimen"
        and "solid_cube_control" in row["specimen_key"]
    ]
    assert len(doe_controls) == 4
    assert sum(int(row["specimen_count"] or 0) for row in doe_controls) == 4
    assert sum(int(row["scalar_count"] or 0) for row in doe_controls) == 16
    assert all(row["quality_status"] == "仅验证" for row in doe_controls)
    assert all(float(row["weight_ceiling"]) == 0.0 for row in doe_controls)
    assert all(row["model_ready"] is False for row in doe_controls)
    qub_downweighted = [
        row
        for row in manifest
        if row["source_directory"] == "QUB_生物基三重自修复TPU"
        and row["record_granularity"] == "curve"
        and row["quality_status"] == "降权"
    ]
    assert len(qub_downweighted) == 27
    assert all(0.0 < float(row["weight_ceiling"]) < 1.0 for row in qub_downweighted)
    assert Counter(float(row["weight_ceiling"]) for row in qub_downweighted) == {
        0.35: 21,
        0.25: 6,
    }
    assert all(row["current_weight_materialized"] is False for row in qub_downweighted)
    assert len({row["manifest_row_id"] for row in manifest}) == len(manifest)
    for row in manifest:
        for field, allowed in ENUMS.items():
            assert row[field] in allowed, (field, row[field], row["manifest_row_id"])
        assert row["source_id"]
        assert row["source_scope_id"]
        assert row["leakage_group_key"]
        assert row["audit_basis"]
        assert row["model_ready"] is False
        assert row["current_weight_materialized"] is False
        assert row["gold_layer"] == _gold_layer_for_target(
            {"origin_kind": row["origin_kind"]}, row["target_origin"]
        )
        assert row["target_origin"]
        if row["quality_status"] == "隔离" and row["gold_layer"] != "Not-Gold":
            assert row["gold_admission_status"] in {
                "blocked",
                "conditional_reference",
            }
        assert float(row["weight_ceiling"]) >= 0
        if row["gold_admission_status"] in {"blocked", "evidence_only"}:
            assert float(row["weight_ceiling"]) == 0.0
        for field in ("specimen_count", "run_count", "curve_count", "scalar_count", "point_count", "numeric_value_count"):
            value = row[field]
            assert value is None or value >= 0, (field, value, row["manifest_row_id"])
    fdm = next(row for row in ledger if row["source_directory"] == "Mendeley_FDM_TPU晶格与基材力学")
    assert fdm["specimen_count"] == 76
    assert fdm["curve_count_observed"] == 76
    assert fdm["curve_count_candidate"] == 57
    assert fdm["scalar_count_observed"] == 1206
    assert fdm["scalar_count_candidate"] == 935
    pcl = next(row for row in ledger if row["source_directory"] == "PCL_GitLFS轨迹补采")
    assert pcl["source_identity_count_contribution"] == 0


def _format_int(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.3f}"
    return f"{int(value):,}"


def _write_report(
    ledger: list[dict[str, Any]], manifest: list[dict[str, Any]], summary: dict[str, Any], identities: dict[str, SourceIdentity]
) -> None:
    reference_texts: list[str] = []
    reference_index: dict[str, int] = {}
    for identity in identities.values():
        for text in identity.references:
            if text and text not in reference_index:
                reference_index[text] = len(reference_texts) + 1
                reference_texts.append(text)

    lines = [
        "# TPU 数据库 v0.2：多保真 Gold 清单与数据规模总账",
        "",
        "> 状态：`audit_inventory_only / training_blocked / weights_not_materialized`",
        f"> 生成方式：由 v0.1 冻结快照、v0.2 来源治理配置、{summary['v0_2_source_directory_count']} 个新增开放数据目录和 {summary['local_backlog_source_directory_count']} 个已审计既有力学目录的摘要与逐记录 TSV 确定性生成。",
        f"> 固定审计时点：`{summary['audit_as_of_utc']}`；输入文件：{summary['input_file_count']}；输入指纹：`{summary['input_fingerprint_sha256']}`。",
        "> 重要边界：本报告中的“候选”只表示科学审计后仍值得保留；**当前模型就绪记录仍为 0**。",
        "",
        "## 1. 直接结论",
        "",
        "| 指标 | 当前可复算值 | 正确解释 |",
        "|---|---:|---|",
        f"| v0.2 新增开放数据目录 | {summary['v0_2_source_directory_count']} | 物理目录数，不是独立来源数 |",
        f"| v0.2 新增独立来源身份 | {summary['v0_2_independent_source_identity_count']} | PCL Git LFS 补采与 Zenodo PCL 母来源同源，贡献记 0 |",
        f"| 已审计既有力学目录/独立来源 | {summary['local_backlog_source_directory_count']} / {summary['local_backlog_independent_source_identity_count']} | 早期下载目录不复制原始数据，通过 source_path 接入同一总账 |",
        f"| 总账来源范围 | {summary['ledger_source_scope_count']} | {summary['v0_2_source_directory_count']} 个 v0.2 新增目录 + {summary['local_backlog_source_directory_count']} 个既有力学目录 + 4 个 v0.1 冻结基线 |",
        f"| 独立来源贡献合计 | {summary['total_independent_source_contribution_count']} | v0.2 新增独立来源 {summary['v0_2_independent_source_identity_count']} + 既有力学独立来源 {summary['local_backlog_independent_source_identity_count']} + v0.1 冻结基线 4 |",
        f"| Gold参考来源分层 | E={summary['source_gold_layer_counts'].get('Gold-E', 0)} / C={summary['source_gold_layer_counts'].get('Gold-C', 0)} / V={summary['source_gold_layer_counts'].get('Gold-V', 0)} / 混合={summary['source_gold_layer_counts'].get('Gold-E+Gold-C', 0)} | 分层表示证据来源，不表示等权；实验、计算和虚拟候选不再混成一种真值 |",
        f"| Gold来源准入状态 | 正式参考={summary['source_gold_admission_status_counts'].get('admitted_reference', 0)} / 条件参考={summary['source_gold_admission_status_counts'].get('conditional_reference', 0)} / 阻断={summary['source_gold_admission_status_counts'].get('blocked', 0)} | 参考准入与训练权重独立；Gold-V可准入但实验性质监督权重仍为0 |",
        f"| 逐记录清单行 | {summary['manifest_row_count']:,} | 含来源聚合、逐试样、逐运行、逐曲线、逐标量和证据组；不是单一统计分母 |",
        f"| Gold-V 可靠结构参考 | {summary['virtual_candidate_reference_count']:,} | 均保留来源、规范结构与零监督权重；不是等量的可合成单体 |",
        f"| 可合成单体主候选/混合盐参考/非合成候选 | {summary['virtual_candidate_synthesis_primary_count']:,}/{summary['virtual_candidate_mixture_or_salt_reference_count']:,}/{summary['virtual_candidate_not_synthesis_candidate_count']:,} | 混合盐与结构警示项保留在宽松参考层，但不计入可合成主候选；PolyUniverse按标准InChIKey连接度族同折 |",
        f"| 有官能团角色建议/未分类参考 | {summary['virtual_candidate_functional_group_matched_count']:,}/{summary['virtual_candidate_unclassified_count']:,} | 角色来自版本化 SMARTS 规则，属于筛选建议而非商业可得性或实验可合成性证明 |",
        f"| 核心校准曲线/已审计点行 | {summary['strict_core_calibration_curve_count']} / {summary['strict_core_calibration_curve_point_row_count']:,} | 新增三源217条/913,608点行 + v0.1 Eom 16条/21,489点行；完整点对上限≤935,095，Eom试样链未闭合 |",
        f"| 严格核心键控试样/曲线/已审计点行 | {summary['strict_core_keyed_specimen_count']} / {summary['strict_core_keyed_curve_count']} / {summary['strict_core_keyed_curve_point_row_count']:,} | DRUM主核心148 + 低天花板28 + QUB 41；完整点对上限≤913,606 |",
        f"| 三个核心目录全部键控试样 | {summary['core_source_directory_keyed_specimen_count']} | DRUM 158 + 低天花板28 + QUB 41；含DRUM 10个桥接/外部/橡皮筋对照，仅作目录全范围盘点 |",
        f"| 严格新增核心规范配方 | {summary['strict_core_formulation_count']} | DRUM主核心22 + 低天花板4 + QUB 4；规范配方键口径，不是材料代码别名数 |",
        f"| 三个核心目录全范围规范配方 | {summary['core_source_directory_formulation_count']} | DRUM 26 + 低天花板4 + QUB 4；DRUM额外4个为P4MCL热固、14BDO桥接、Elastollan外部和rubber band对照 |",
        f"| 严格可确认核心批次 | {summary['strict_core_batch_count']} | DRUM主核心23 + 低天花板4；QUB批次未知，不补零 |",
        f"| 三个核心目录全范围可确认批次 | {summary['core_source_directory_batch_count']} | 含4个非核心对照/桥接批次；QUB批次未知 |",
        f"| 保守 TPU/TPUU 试样或直接运行盘点下界 | {summary['conservative_tpu_tpuu_specimen_or_direct_run_lower_bound']:,} | 由1119算术池剔除FDM 19个hold与12个PU微球试样；仍是异质盘点口径，不是可训练样本数 |",
        f"| 选定来源异质物理试样/运行算术池 | {summary['selected_source_heterogeneous_specimen_or_run_arithmetic_pool']:,} | 含FDM hold与PU微球；只复核来源级算术，**不可训练、不可作单一统计分母** |",
        f"| 主要实验曲线/历史下界 | {summary['major_experimental_curve_history_lower_bound']:,} | 跨 12 个非重叠来源范围的曲线/历史审计下界 |",
        f"| 主要实验曲线点下界 | {summary['major_experimental_curve_point_lower_bound']:,} | 点强相关，不能随机拆点 |",
        f"| 纯实验 origin 已知试样合计 | {summary['known_origin_totals']['experimental_only']['specimen_count']['value']:,} | 仅汇总有明确试样计数的 {summary['known_origin_totals']['experimental_only']['specimen_count']['known_source_scope_count']} 个来源范围；未知试样数不补零 |",
        f"| 纯实验 origin 曲线（观测/候选） | {summary['known_origin_totals']['experimental_only']['curve_count_observed']['value']:,}/{summary['known_origin_totals']['experimental_only']['curve_count_candidate']['value']:,} | 分母分别为 {summary['known_origin_totals']['experimental_only']['curve_count_observed']['known_source_scope_count']}/{summary['known_origin_totals']['experimental_only']['curve_count_candidate']['known_source_scope_count']} 个已知来源范围 |",
        f"| 纯实验 origin 已知点 | {summary['known_origin_totals']['experimental_only']['point_count_observed']['value']:,} | 来自 {summary['known_origin_totals']['experimental_only']['point_count_observed']['known_source_scope_count']} 个点数已知来源范围 |",
        f"| 混合 origin 曲线/点 | {summary['known_origin_totals']['mixed_experiment_and_simulation']['curve_count_observed']['value']:,} / {summary['known_origin_totals']['mixed_experiment_and_simulation']['point_count_observed']['value']:,} | 含实验+模拟/模型视图，**不得直接并入纯实验合计** |",
        "| 当前模型就绪记录 | **0** | 未完成动作级训练许可、配方—批次—试样血缘、目标定义、组级拆分与权重物化 |",
        "",
        "主任务采用 1,088 的保守盘点下界；1,119 只保留为选定来源的异质算术池，不能称为 TPU/TPUU 可用样本。核心校准曲线233=新增三源217条键控核心曲线+v0.1 Eom 16条可派生曲线；后16条的配方—独立试样链尚未闭合。严格核心键控试样是217，三个核心目录全范围则是227；严格规范配方是30，目录全范围规范配方是34，均不得用材料代码别名数替代。",
        "",
        "## 2. 状态与计数定义",
        "",
        "- `入选`：科学上属于核心任务且具有非零未来上限；仍需权利、拆分和权重物化，当前不是训练行。",
        "- `降权`：只适用于工艺、商业牌号、PU邻域、计算描述符或应用子任务；不可主导化学—本征性能模型。",
        "- `仅验证`：只作外部验证、表示审计或来源证据，训练上限为 0。",
        "- `隔离`：重复、冲突、单位/许可/输出缺失或访问受阻；训练上限为 0。",
        "- `curve_count_candidate/scalar_count_candidate` 是科学候选数，不是模型就绪数；所有 `current_weight_materialized=false`。",
        "- `gold_layer` 区分 Gold-E 实验参考、Gold-C 计算参考和 Gold-V 虚拟候选；`gold_admission_status` 与训练权重独立，零训练权重不自动等于不准入参考集合。",
        "- `weight_ceiling` 是缺口闭合后的潜在上限，不是当前权重；`conditional_reference` 即使保留非零潜在上限，其当前有效监督权重仍为0。只有 `gold_admission_status=admitted_reference`、任务/权利/防泄漏门通过、`model_ready=true` 且 `current_weight_materialized=true` 时才可进入损失函数。",
        "- `point_count`、模拟帧、数值单元格和 PDF 页数绝不增加独立材料、配方、批次或试样数。",
        "",
        "## 3. 来源级总账",
        "",
        "| 来源范围 | 来源范围键 / 引用键 / DOI或稳定标识 | 任务/角色 | Gold层/准入 | 材料/配方 | 试样/运行 | 曲线（观测/候选） | 标量（观测/候选） | 点/帧 | 状态 | 上限 |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---|---:|",
    ]
    for row in ledger:
        material_formula = f"{_format_int(row['material_count'])}/{_format_int(row['formulation_count'])}"
        specimen_run = f"{_format_int(row['specimen_count'])}/{_format_int(row['run_count'])}"
        curves = f"{_format_int(row['curve_count_observed'])}/{_format_int(row['curve_count_candidate'])}"
        scalars = f"{_format_int(row['scalar_count_observed'])}/{_format_int(row['scalar_count_candidate'])}"
        citation_keys = str(row["citation_keys"]).replace(";", "<br>") or "未映射（见审计依据）"
        source_reference = (
            f"`{row['source_scope_id']}`<br>{citation_keys}<br>{row['canonical_identifier']}"
        )
        lines.append(
            f"| `{row['source_directory']}` | {source_reference} | {row['task']} / {row['scientific_role']} | {row['gold_layer']} / {row['gold_admission_status']} | {material_formula} | {specimen_run} | {curves} | {scalars} | {_format_int(row['point_count_observed'])} | {row['quality_status']} | {float(row['weight_ceiling']):.2f} |"
        )

    lines.extend(
        [
            "",
            "## 4. 逐记录清单覆盖度",
            "",
            "逐记录清单不是把每个来源都虚构成同一粒度；只有现有审计能确认的实体才展开。没有逐试样 ID 的来源保留来源级或曲线级记录，并将 `completeness` 与 `leakage_key_status` 明示。",
            "",
            "| 粒度 | 行数 |",
            "|---|---:|",
        ]
    )
    for key, value in summary["manifest_granularity_counts"].items():
        lines.append(f"| `{key}` | {value:,} |")
    lines.extend(
        [
            "",
            "FDM 来源的 76 条曲线与 1,206 条标量共享 76 个试样组。候选规则是 `quality_gate=pass_source_summary_selected`：57 条曲线、935 条标量；`not_selected` 为 9 条曲线/128 条标量，仅留审计；`conflict` 为 10 条曲线/143 条标量，隔离。审计中另有 36 个 `blocked_summary_conflict_evidence` 空值，只保留来源冲突证据，不变成数值样本。",
            "",
            "打印 DOE 的有效观测科学标量为 2,664=1,372 个完整直接响应+1,292 个有效规范派生；派生量与母响应不可重复计权。主候选只保留 1,356 个直接响应，另有4个实心立方体对照试样产生16个完整直接响应，仅作验证且权重为0。`direct_numeric_total=1,500` 还包含载荷、面积等输入，不能当作响应数；4 个无效缓存伪零和 4 个已知缺失仅作异常审计，均不计入有效观测标量。",
            "",
            "## 5. 无泄漏与权重边界",
            "",
            "所有逐记录行至少具有来源家族级粗粒度泄漏键；存在试样、配方、运行或作者显式组键时使用更细键。粗粒度键的含义是保守地把整来源家族放在同一折，不代表材料身份已经解析。当前没有训练拆分；以后物化时还必须执行 `source_family → study/DOI → formulation → batch → specimen → curve/run` 的逐级保护。",
            "",
            "本总账只保存未来权重上限。任何隔离/仅验证记录上限强制为 0；曲线内与试样内归一、同体系多帧聚合和来源平衡均未执行。不能用行数、点数或帧数推高权重。",
            "",
            "## 6. 机器可读产物与复算",
            "",
            f"- 来源级总账：`{_to_relative(OUTPUT_LEDGER)}`",
            f"- 逐记录清单：`{_to_relative(OUTPUT_MANIFEST)}`",
            f"- JSON 总账：`{_to_relative(OUTPUT_JSON)}`",
            f"- Gold-V 候选结构：`{_to_relative(OUTPUT_CANDIDATES)}`",
            f"- Gold-E 已物化实验标量：`{_to_relative(OUTPUT_GOLD_E_TABLES)}`（逐试样、配方、工艺、动力学和实验汇总值，不是训练集）",
            f"- Gold-C 计算性能长表：`{_to_relative(OUTPUT_GOLD_C_VALUES)}`（规范化参考值，不是训练集）",
            "- 复算程序：`代码/生成数据总账.py`",
            "- 校验：`代码/测试/test_trainable_inventory.py`",
            "",
            "## 7. 审计依据",
            "",
            "[A1] TPU 高通量筛选数据库与多保真研究工作流，`README.md`。",
            "",
            "[A2] TPU 多保真 Gold 数据集定义，`文档/Gold数据集定义.md`。",
            "",
            "[A3] TPU 数据库当前状态，`文档/当前数据状态.md`。",
            "",
            "[A4] 数据来源与参考文献，`文档/数据来源与参考文献.md`。",
            "",
            "[A5] TPU 数据库 v0.1 快照，`数据/快照/TPU数据库_v0.1_快照.json`。",
            "",
            "## 8. 数据来源参考文献",
            "",
            "下列参考文献由 `配置/v0.2来源范围.yaml` 的固定 citation 台账生成；论文 DOI 与数据 DOI 分开保存。一个来源范围可能同时对应数据集与主论文，因此不能按参考文献条数增加来源或样本数。",
            "",
        ]
    )
    for index, text in enumerate(reference_texts, 1):
        lines.append(f"[R{index:03d}] {text}")
    lines.append("")
    _atomic_write_text(OUTPUT_REPORT, "\n".join(lines), encoding="utf-8")


def main() -> None:
    INPUT_FILES_READ.clear()
    profile_config = _read_yaml(PROFILE_PATH)
    scope_config = _read_yaml(SCOPE_PATH)
    _register_input(SMIPOLY_CANDIDATE_INPUT)
    _register_input(SMIPOLY_CLASSIFIER_PATH)
    _register_input(PURGEN_ARCHIVE)
    _register_input(PURGEN_AUDIT_SCRIPT_PATH)
    _register_input(POLYUNIVERSE_DINCO_AUDIT_SCRIPT_PATH)
    _register_input(POLYUNIVERSE_DINCO_SUMMARY)
    _register_input(POLYUNIVERSE_DINCO_AUDIT)
    _register_input(POLYUNIVERSE_DINCO_MAPPING)
    for spec in POLYUNIVERSE_DINCO_FILE_SPECS:
        _register_input(POLYUNIVERSE_DINCO_SOURCE_DIR / str(spec["name"]))
    _register_input(POLYOMICS_AUDIT_SCRIPT_PATH)
    _register_input(POLYOMICS_CSV_PATH)
    _register_input(ACS_TABLE_AUDIT_SCRIPT_PATH)
    for spec in ACS_TABLE_SPECS.values():
        _register_input(RAW_NEW / spec.directory / spec.pdf_name)
        metadata_path = RAW_NEW / spec.directory / "官方API元数据.json"
        if metadata_path.is_file():
            _register_input(metadata_path)
    _register_input(BATCH10_MATERIALIZATION_SCRIPT_PATH)
    _register_input(BATCH10_OPENPOLY_AUDIT_SCRIPT_PATH)
    for path, _ in BATCH10_OMG_PROPERTY_FILES:
        _register_input(path)
    for path in (
        BATCH10_OMG_CANDIDATE_CSV,
        BATCH10_OMG_PU_PROPERTY_CSV,
        BATCH10_OMG_FIELD_DICTIONARY,
        BATCH10_OMG_AUDIT,
        BATCH10_OPENPOLY_DIR / "冻结审计结果.json",
        BATCH10_SCIENCEDB_CSV,
        BATCH10_SCIENCEDB_AUDIT,
        BATCH10_KINETICS_MEASUREMENTS,
        BATCH10_KINETICS_CONDITIONS,
        BATCH10_KINETICS_HASHES,
        BATCH10_KINETICS_AUDIT,
        BATCH10_KINETICS_MEASUREMENTS.parent
        / "Solvent_Free_Adhesives_Dataset_5-2.zip",
    ):
        _register_input(path)
    for relative_name in BATCH10_OPENPOLY_FROZEN_FILES:
        _register_input(BATCH10_OPENPOLY_DIR / relative_name)
    _register_input(BATCH11_EXISTING_SCALAR_SCRIPT_PATH)
    for spec in BATCH11_EXISTING_SCALAR_SPECS.values():
        _register_input(spec.path)
    for path in (
        BATCH12_PUFOAM_SCRIPT_PATH,
        BATCH12_PUFOAM_ARCHIVE,
        BATCH12_PUFOAM_OFFICIAL_METADATA,
        BATCH12_PUFOAM_CROSSREF_METADATA,
    ):
        _register_input(path)
    if tuple(BATCH10_COMPUTATIONAL_RECORD_COLUMNS) != tuple(
        GOLD_C_VALUE_COLUMNS
    ):
        raise AssertionError("第十批Gold-C字段与总账字段不一致")
    if tuple(BATCH12_PUFOAM_GOLD_C_COLUMNS) != tuple(GOLD_C_VALUE_COLUMNS):
        raise AssertionError("第十二批PUFoam Gold-C字段与总账字段不一致")
    batch10_materialization_audit = audit_batch10_materialization_inputs()
    acs_table_rows, acs_table_audit_summary = build_acs_table_records()
    batch10_gold_e_rows = build_batch10_gold_e_rows()
    materialized_table_rows = [*acs_table_rows, *batch10_gold_e_rows]
    batch11_gold_e_rows, batch11_existing_scalar_audit = (
        build_batch11_existing_scalar_records()
    )
    gold_e_value_source_rows = [
        *materialized_table_rows,
        *batch11_gold_e_rows,
    ]
    smipoly_candidate_rows = build_candidate_rows(SMIPOLY_CANDIDATE_INPUT)
    _, purgen_candidate_rows = build_purgen_fragment_rows(PURGEN_ARCHIVE)
    existing_candidate_structures = {
        str(row["canonical_smiles"])
        for row in [*smipoly_candidate_rows, *purgen_candidate_rows]
    }
    polyuniverse_dinco_rows = build_polyuniverse_dinco_rows(
        existing_candidate_structures
    )
    existing_candidate_structures.update(
        str(row["canonical_smiles"])
        for row in polyuniverse_dinco_rows
    )
    batch10_candidate_rows = build_batch10_candidate_rows(
        existing_candidate_structures
    )
    expected_batch10_candidate_count = (
        100_584 + BATCH10_OPENPOLY_CANDIDATE_COUNT - 1
    )
    if len(batch10_candidate_rows) != expected_batch10_candidate_count:
        raise AssertionError(
            "第十批Gold-V跨来源去重数量漂移: "
            f"expected={expected_batch10_candidate_count}, "
            f"actual={len(batch10_candidate_rows)}"
        )
    candidate_rows = [
        *smipoly_candidate_rows,
        *purgen_candidate_rows,
        *polyuniverse_dinco_rows,
        *batch10_candidate_rows,
    ]
    candidate_summary = summarize_candidates(candidate_rows)
    profiles = profile_config["profiles"]
    local_backlog_profiles = profile_config.get("local_backlog_profiles", [])
    all_profiles = [*profiles, *local_backlog_profiles]
    baseline_profiles = profile_config["baseline_profiles"]
    if len(profiles) != 70:
        raise AssertionError(f"v0.2来源画像必须覆盖70个目录，当前为{len(profiles)}")
    actual_directories = {path.name for path in RAW_NEW.iterdir() if path.is_dir()}
    configured_directories = {profile["source_directory"] for profile in profiles}
    if actual_directories != configured_directories:
        raise AssertionError(
            f"来源画像与目录差异: missing={sorted(actual_directories - configured_directories)}, extra={sorted(configured_directories - actual_directories)}"
        )
    if len(local_backlog_profiles) != 4:
        raise AssertionError(f"已审计既有力学来源画像必须覆盖4个目录，当前为{len(local_backlog_profiles)}")
    backlog_directories = {profile["source_directory"] for profile in local_backlog_profiles}
    if len(backlog_directories) != len(local_backlog_profiles):
        raise AssertionError("已审计既有力学来源目录名重复")
    missing_backlog = [
        str(_profile_base(profile))
        for profile in local_backlog_profiles
        if not _profile_base(profile).is_dir()
    ]
    if missing_backlog:
        raise AssertionError(f"已审计既有力学来源目录不存在: {missing_backlog}")
    identities = _resolve_source_identities(scope_config, all_profiles, baseline_profiles)
    ledger = _build_ledger(all_profiles, baseline_profiles, identities)
    manifest = _build_manifest(
        all_profiles,
        baseline_profiles,
        identities,
        ledger,
        candidate_rows,
        materialized_table_rows,
    )
    gold_c_value_rows, gold_c_value_metadata = (
        _gold_c_computational_value_rows(
            all_profiles, identities, acs_table_rows
        )
    )
    gold_e_table_rows, gold_e_table_metadata = _gold_e_table_value_rows(
        gold_e_value_source_rows, identities
    )
    input_fingerprints, input_fingerprint_sha256 = _build_input_fingerprints()
    summary = _build_summary(
        ledger,
        manifest,
        candidate_summary,
        profile_config["audit_as_of_utc"],
        input_fingerprint_sha256,
        len(input_fingerprints),
        configured_directories,
        backlog_directories,
    )
    summary["gold_c_computational_value_long_table"] = gold_c_value_metadata
    summary["gold_e_published_table_long_table"] = gold_e_table_metadata
    summary["acs_table_materialization_audit"] = acs_table_audit_summary
    summary["batch10_multifidelity_materialization_audit"] = (
        batch10_materialization_audit
    )
    summary["batch11_existing_experimental_scalar_audit"] = (
        batch11_existing_scalar_audit
    )
    summary["batch12_pufoam_audit"] = audit_batch12_pufoam()
    _validate(ledger, manifest, summary)
    _write_gzip_csv(
        OUTPUT_GOLD_C_VALUES,
        GOLD_C_VALUE_COLUMNS,
        gold_c_value_rows,
        expected_row_count=int(gold_c_value_metadata["row_count"]),
    )
    _write_gzip_csv(
        OUTPUT_GOLD_E_TABLES,
        GOLD_E_TABLE_COLUMNS,
        gold_e_table_rows,
        expected_row_count=int(gold_e_table_metadata["row_count"]),
    )
    _write_gzip_csv(
        OUTPUT_CANDIDATES,
        CANDIDATE_COLUMNS,
        candidate_rows,
        expected_row_count=len(candidate_rows),
    )
    _write_csv(OUTPUT_LEDGER, LEDGER_COLUMNS, ledger)
    _write_gzip_csv(
        OUTPUT_MANIFEST,
        MANIFEST_COLUMNS,
        manifest,
        expected_row_count=len(manifest),
    )
    json_payload = {
        "schema_version": "v0.2",
        "artifact_version": "trainable-inventory-v0.2.13",
        "artifact_status": "audit_inventory_only",
        "count_semantics": profile_config["count_semantics"],
        "audit_metric_semantics": profile_config["audit_metric_semantics"],
        "input_fingerprints": input_fingerprints,
        "enums": {key: sorted(value) for key, value in ENUMS.items()},
        "summary": summary,
        "source_ledger": ledger,
        "record_manifest_artifact": {
            "path": _to_relative(OUTPUT_MANIFEST),
            "format": "csv.gz",
            "row_count": len(manifest),
            "sha256": _sha256_file(OUTPUT_MANIFEST),
        },
    }
    _atomic_write_text(
        OUTPUT_JSON,
        json.dumps(json_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_report(ledger, manifest, summary, identities)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
