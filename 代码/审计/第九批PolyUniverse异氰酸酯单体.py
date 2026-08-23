"""审计 PolyUniverse 官方 Zenodo 记录中的二异氰酸酯结构参考。

文件名只代表发布者给出的预筛标签，不能替代结构核验。本脚本逐行保留原始
SMILES，使用带原子连接度约束的 NCO/NCS SMARTS 复算官能团。所有来源可靠、
可解析且恰含两个 NCO 的记录都可保留为零监督权重的 Gold-V 结构参考，但只有
单一组分、中性且元素处于预设边界内的结构计入 ``primary_monomer_candidate``。
多片段盐/混合物标为 ``mixture_or_salt_reference``，其余结构警示项标为
``not_synthesis_candidate``，不能混入可合成单体主候选数。

规范异构 SMILES 是逐记录主键；标准 InChIKey 用于发现互变异构/质子层等价
表示，其首段连接度键作为 family/split 分组键。两类键都不能替代原始记录，
也不提供实验产率、供应、反应性或材料性能监督。
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import stat
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from rdkit import Chem, RDLogger, rdBase
from rdkit.Chem import Descriptors, rdMolDescriptors

try:
    from .SMiPoly_TPU候选分类 import (
        CANDIDATE_COLUMNS as _CANDIDATE_COLUMNS,
        _group_counts,
    )
except ImportError:  # 允许直接运行本文件。
    from SMiPoly_TPU候选分类 import (
        CANDIDATE_COLUMNS as _CANDIDATE_COLUMNS,
        _group_counts,
    )

CANDIDATE_COLUMNS = _CANDIDATE_COLUMNS


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第七批虚拟_PolyUniverse百万PU"
)
SUMMARY_PATH = SOURCE_DIR / "异氰酸酯单体审计摘要.json"
AUDIT_PATH = SOURCE_DIR / "异氰酸酯单体审计清单.tsv"
MAPPING_PATH = SOURCE_DIR / "异氰酸酯候选去重映射.tsv"

SOURCE_ID = "source_zenodo_12585902_polyuniverse_pu"
SOURCE_DOI = "10.5281/zenodo.12585902"
SOURCE_RECORD = "12585902"
LICENSE_SPDX = "CC-BY-4.0"
RULE_VERSION = "polyuniverse-dinco-audit-v2"

FILE_SPECS = (
    {
        "name": "PubChem_diNCO.csv",
        "bytes": 1_182_762,
        "md5": "ad388d4d0628156d337f035cb06861a0",
        "url": "https://zenodo.org/api/records/12585902/files/PubChem_diNCO.csv/content",
        "priority": 0,
    },
    {
        "name": "GDB-17_diNCO.csv",
        "bytes": 157_268,
        "md5": "43bd1d039f1df7c3e13df89284fb249e",
        "url": "https://zenodo.org/api/records/12585902/files/GDB-17_diNCO.csv/content",
        "priority": 1,
    },
)

NCO_SMARTS = "[N;X2]=[C;X2]=[O;X1]"
NCS_SMARTS = "[N;X2]=[C;X2]=[S;X1]"
NCO_PATTERN = Chem.MolFromSmarts(NCO_SMARTS)
NCS_PATTERN = Chem.MolFromSmarts(NCS_SMARTS)
if NCO_PATTERN is None or NCS_PATTERN is None:  # pragma: no cover
    raise RuntimeError("NCO/NCS SMARTS 编译失败")

ALLOWED_ATOMIC_NUMBERS = frozenset({1, 5, 6, 7, 8, 9, 14, 15, 16, 17, 35, 53})

AUDIT_COLUMNS = (
    "source_file",
    "source_row",
    "raw_smiles",
    "rdkit_parse_state",
    "canonical_isomeric_smiles",
    "inchikey",
    "standard_inchikey",
    "tautomer_family_key",
    "split_family_key",
    "nco_count",
    "ncs_count",
    "functional_class",
    "fragment_count",
    "formal_charge",
    "allowed_elements",
    "disallowed_atomic_numbers",
    "strict_candidate",
    "synthesis_candidate_status",
    "structure_tier",
    "exclusion_reason",
    "canonical_group_id",
)

MAPPING_COLUMNS = (
    "candidate_id",
    "canonical_isomeric_smiles",
    "inchikey",
    "standard_inchikey",
    "tautomer_family_key",
    "split_family_key",
    "fragment_count",
    "synthesis_candidate_status",
    "structure_tier",
    "gold_admission_status",
    "strict_candidate",
    "source_files",
    "source_row_count",
    "source_rows",
    "primary_source_record_id",
    "cross_file_duplicate",
    "exclusion_reason",
)


@dataclass(frozen=True)
class AuditBundle:
    audit_rows: tuple[dict[str, Any], ...]
    mapping_rows: tuple[dict[str, Any], ...]
    candidate_rows: tuple[dict[str, Any], ...]
    summary: dict[str, Any]


def _md5_file(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _candidate_id(canonical_smiles: str) -> str:
    digest = hashlib.sha256(canonical_smiles.encode("utf-8")).hexdigest()[:20]
    return f"polyuniverse_dinco_{digest}"


def _standard_inchikey_and_family(mol: Chem.Mol) -> tuple[str, str]:
    """返回标准 InChIKey 及其连接度 family/split 键。

    RDKit 默认生成 Standard InChIKey。标准 InChI 会归一化其支持的互变异构和
    质子层；InChIKey 第一段只保留连接度，因此还会把立体/同位素变体放入同一
    family。这里不据此删除记录，只用于防止 family 跨训练/验证划分泄漏。
    """

    standard_inchikey = Chem.MolToInchiKey(mol)
    if not standard_inchikey:  # pragma: no cover - 当前官方记录均可生成。
        raise ValueError("标准 InChIKey 生成失败")
    return standard_inchikey, standard_inchikey.split("-", 1)[0]


def _synthesis_candidate_status(
    *, functional_class: str, strict: bool, fragment_count: int
) -> str:
    if strict:
        return "primary_monomer_candidate"
    if functional_class == "diNCO_exact" and fragment_count > 1:
        return "mixture_or_salt_reference"
    return "not_synthesis_candidate"


def _functional_class(nco_count: int, ncs_count: int) -> str:
    if nco_count == 2 and ncs_count == 0:
        return "diNCO_exact"
    if nco_count == 0 and ncs_count == 2:
        return "diNCS_exact"
    if nco_count > 0 and ncs_count > 0:
        return "mixed_NCO_NCS"
    return "other"


def _exclusion_reasons(
    functional_class: str,
    fragment_count: int,
    formal_charge: int,
    disallowed: tuple[int, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if functional_class != "diNCO_exact":
        reasons.append("not_exact_diNCO")
    if fragment_count != 1:
        reasons.append("multifragment")
    if formal_charge != 0:
        reasons.append("nonzero_formal_charge")
    if disallowed:
        reasons.append("disallowed_elements=" + ",".join(map(str, disallowed)))
    return tuple(reasons)


def _validate_files() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for spec in FILE_SPECS:
        path = SOURCE_DIR / str(spec["name"])
        if not path.is_file():
            raise FileNotFoundError(f"缺少官方单体文件: {path}")
        size = path.stat().st_size
        md5 = _md5_file(path)
        if size != spec["bytes"] or md5 != spec["md5"]:
            raise ValueError(
                f"官方单体文件校验失败: {path.name}; bytes={size}; md5={md5}"
            )
        checks.append(
            {
                "name": path.name,
                "bytes": size,
                "md5": md5,
                "url": spec["url"],
                "verified": True,
            }
        )
    return checks


def _candidate_row(primary: dict[str, Any], group: list[dict[str, Any]]) -> dict[str, Any]:
    canonical = str(primary["canonical_isomeric_smiles"])
    RDLogger.DisableLog("rdApp.warning")
    try:
        mol = Chem.MolFromSmiles(canonical)
    finally:
        RDLogger.EnableLog("rdApp.warning")
    if mol is None:  # pragma: no cover - canonical 结构已在前一阶段通过。
        raise ValueError(f"规范SMILES回读失败: {canonical}")
    strict = any(bool(row["strict_candidate"]) for row in group)
    synthesis_status = str(primary["synthesis_candidate_status"])
    source_records = [f"{row['source_file']}:row={row['source_row']}" for row in group]
    groups = _group_counts(mol)
    if synthesis_status == "primary_monomer_candidate":
        role = "diisocyanate_candidate"
        role_confidence = "rule_high"
        role_basis = "exactly_two_NCO_single_fragment_neutral_allowed_elements"
        screening_scope = "direct_tpu_building_block"
        screening_priority = 1
        structure_status = "rdkit_validated_strict"
    elif synthesis_status == "mixture_or_salt_reference":
        role = "diisocyanate_reference"
        role_confidence = "reference_only"
        role_basis = "exactly_two_NCO_multicomponent_reference_not_a_monomer"
        screening_scope = "mixture_or_salt_reference"
        screening_priority = 4
        structure_status = "rdkit_validated_mixture_or_salt_reference"
    else:
        role = "diisocyanate_reference"
        role_confidence = "reference_only"
        role_basis = "exactly_two_NCO_single_component_with_structure_caution"
        screening_scope = "not_synthesis_candidate"
        screening_priority = 5
        structure_status = "rdkit_validated_not_synthesis_candidate"
    return {
        "candidate_id": _candidate_id(canonical),
        "source_id": SOURCE_ID,
        "source_record_id": source_records[0],
        "source_locator": ";".join(source_records),
        "preferred_name": "",
        "raw_smiles": primary["raw_smiles"],
        "canonical_smiles": canonical,
        "inchikey": primary["standard_inchikey"],
        "molecular_formula_reported": "",
        "molecular_formula_calculated": rdMolDescriptors.CalcMolFormula(mol),
        "molecular_weight_reported_g_mol": "",
        "molecular_weight_calculated_g_mol": round(float(Descriptors.MolWt(mol)), 6),
        "exact_mass_g_mol": round(float(Descriptors.ExactMolWt(mol)), 6),
        "formal_charge": int(Chem.GetFormalCharge(mol)),
        "heavy_atom_count": int(mol.GetNumHeavyAtoms()),
        "isocyanate_group_count": groups["isocyanate"],
        "hydroxyl_group_count": groups["hydroxyl"],
        "amine_group_count": groups["amine"],
        "thiol_group_count": groups["thiol"],
        "carboxylic_acid_group_count": groups["carboxylic_acid"],
        "cyclic_carbonate_group_count": groups["cyclic_carbonate"],
        "epoxide_group_count": groups["epoxide"],
        "tpu_role": role,
        "role_confidence": role_confidence,
        "role_basis": role_basis,
        "screening_scope": screening_scope,
        "screening_priority": screening_priority,
        "functional_group_match": True,
        "structure_status": structure_status,
        "duplicate_status": (
            "canonical_merged_cross_file"
            if len({row["source_file"] for row in group}) > 1
            else "canonical_unique_within_source"
        ),
        "license_spdx": LICENSE_SPDX,
        "data_origin": "enumeration",
        "fidelity_level": "candidate_structure",
        "gold_layer": "Gold-V",
        "gold_admission_status": (
            "admitted_reference" if strict else "conditional_reference"
        ),
        "direct_property_supervision_weight_ceiling": 0.0,
        "prediction_uncertainty": "",
        "generation_rule_version": RULE_VERSION,
        "rdkit_version": rdBase.rdkitVersion,
    }


@lru_cache(maxsize=1)
def audit_source() -> AuditBundle:
    checks = _validate_files()
    audit_rows: list[dict[str, Any]] = []
    groups_by_canonical: dict[str, list[dict[str, Any]]] = defaultdict(list)
    file_summaries: dict[str, dict[str, Any]] = {}

    RDLogger.DisableLog("rdApp.error")
    RDLogger.DisableLog("rdApp.warning")
    try:
        for spec in FILE_SPECS:
            name = str(spec["name"])
            path = SOURCE_DIR / name
            counters: Counter[str] = Counter()
            canonical_seen: set[str] = set()
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames != ["Smiles"]:
                    raise ValueError(f"{name} 列漂移: {reader.fieldnames}")
                for source_row, source in enumerate(reader, 2):
                    counters["raw_rows"] += 1
                    raw_smiles = str(source.get("Smiles") or "").strip()
                    mol = Chem.MolFromSmiles(raw_smiles)
                    if mol is None:
                        counters["invalid"] += 1
                        audit_rows.append(
                            {
                                "source_file": name,
                                "source_row": source_row,
                                "raw_smiles": raw_smiles,
                                "rdkit_parse_state": "invalid",
                                "canonical_isomeric_smiles": "",
                                "inchikey": "",
                                "standard_inchikey": "",
                                "tautomer_family_key": "",
                                "split_family_key": "",
                                "nco_count": "",
                                "ncs_count": "",
                                "functional_class": "invalid",
                                "fragment_count": "",
                                "formal_charge": "",
                                "allowed_elements": "",
                                "disallowed_atomic_numbers": "",
                                "strict_candidate": False,
                                "synthesis_candidate_status": "not_synthesis_candidate",
                                "structure_tier": "quarantine",
                                "exclusion_reason": "rdkit_parse_error",
                                "canonical_group_id": "",
                            }
                        )
                        continue

                    counters["valid"] += 1
                    canonical = Chem.MolToSmiles(
                        mol, canonical=True, isomericSmiles=True
                    )
                    if canonical in canonical_seen:
                        counters["canonical_duplicate_rows"] += 1
                    canonical_seen.add(canonical)
                    nco_count = len(
                        mol.GetSubstructMatches(NCO_PATTERN, uniquify=True)
                    )
                    ncs_count = len(
                        mol.GetSubstructMatches(NCS_PATTERN, uniquify=True)
                    )
                    functional_class = _functional_class(nco_count, ncs_count)
                    fragment_count = len(
                        Chem.GetMolFrags(
                            mol, asMols=False, sanitizeFrags=False
                        )
                    )
                    formal_charge = int(Chem.GetFormalCharge(mol))
                    disallowed = tuple(
                        sorted(
                            {
                                atom.GetAtomicNum()
                                for atom in mol.GetAtoms()
                                if atom.GetAtomicNum() not in ALLOWED_ATOMIC_NUMBERS
                            }
                        )
                    )
                    reasons = _exclusion_reasons(
                        functional_class,
                        fragment_count,
                        formal_charge,
                        disallowed,
                    )
                    strict = not reasons
                    synthesis_status = _synthesis_candidate_status(
                        functional_class=functional_class,
                        strict=strict,
                        fragment_count=fragment_count,
                    )
                    standard_inchikey, family_key = _standard_inchikey_and_family(mol)
                    if functional_class == "diNCO_exact":
                        tier = "strict" if strict else "conditional"
                    else:
                        tier = "quarantine"
                    counters[f"class_{functional_class}"] += 1
                    if fragment_count > 1:
                        counters["multifragment"] += 1
                    if formal_charge != 0:
                        counters["nonzero_formal_charge"] += 1
                    if disallowed:
                        counters["disallowed_elements"] += 1
                    if strict:
                        counters["strict_candidate_rows"] += 1

                    row = {
                        "source_file": name,
                        "source_row": source_row,
                        "raw_smiles": raw_smiles,
                        "rdkit_parse_state": "valid",
                        "canonical_isomeric_smiles": canonical,
                        "inchikey": standard_inchikey,
                        "standard_inchikey": standard_inchikey,
                        "tautomer_family_key": family_key,
                        "split_family_key": family_key,
                        "nco_count": nco_count,
                        "ncs_count": ncs_count,
                        "functional_class": functional_class,
                        "fragment_count": fragment_count,
                        "formal_charge": formal_charge,
                        "allowed_elements": not disallowed,
                        "disallowed_atomic_numbers": ",".join(map(str, disallowed)),
                        "strict_candidate": strict,
                        "synthesis_candidate_status": synthesis_status,
                        "structure_tier": tier,
                        "exclusion_reason": ";".join(reasons),
                        "canonical_group_id": (
                            hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
                        ),
                    }
                    audit_rows.append(row)
                    if functional_class == "diNCO_exact":
                        groups_by_canonical[canonical].append(row)

            counters["canonical_unique_valid"] = len(canonical_seen)
            file_summaries[name] = dict(sorted(counters.items()))
    finally:
        RDLogger.EnableLog("rdApp.error")
        RDLogger.EnableLog("rdApp.warning")

    candidate_rows: list[dict[str, Any]] = []
    mapping_rows: list[dict[str, Any]] = []
    for canonical in sorted(groups_by_canonical):
        group = sorted(
            groups_by_canonical[canonical],
            key=lambda row: (
                next(
                    spec["priority"]
                    for spec in FILE_SPECS
                    if spec["name"] == row["source_file"]
                ),
                int(row["source_row"]),
            ),
        )
        primary = group[0]
        candidate = _candidate_row(primary, group)
        candidate_rows.append(candidate)
        mapping_rows.append(
            {
                "candidate_id": candidate["candidate_id"],
                "canonical_isomeric_smiles": canonical,
                "inchikey": primary["standard_inchikey"],
                "standard_inchikey": primary["standard_inchikey"],
                "tautomer_family_key": primary["tautomer_family_key"],
                "split_family_key": primary["split_family_key"],
                "fragment_count": primary["fragment_count"],
                "synthesis_candidate_status": primary[
                    "synthesis_candidate_status"
                ],
                "structure_tier": (
                    "strict"
                    if candidate["gold_admission_status"] == "admitted_reference"
                    else "conditional"
                ),
                "gold_admission_status": candidate["gold_admission_status"],
                "strict_candidate": candidate["gold_admission_status"]
                == "admitted_reference",
                "source_files": ";".join(sorted({row["source_file"] for row in group})),
                "source_row_count": len(group),
                "source_rows": ";".join(
                    f"{row['source_file']}:row={row['source_row']}" for row in group
                ),
                "primary_source_record_id": candidate["source_record_id"],
                "cross_file_duplicate": len({row["source_file"] for row in group}) > 1,
                "exclusion_reason": primary["exclusion_reason"],
            }
        )

    strict_candidates = [
        row
        for row in candidate_rows
        if row["gold_admission_status"] == "admitted_reference"
    ]
    conditional_candidates = [
        row
        for row in candidate_rows
        if row["gold_admission_status"] == "conditional_reference"
    ]
    primary_monomer_candidates = [
        row
        for row in mapping_rows
        if row["synthesis_candidate_status"] == "primary_monomer_candidate"
    ]
    mixture_or_salt_references = [
        row
        for row in mapping_rows
        if row["synthesis_candidate_status"] == "mixture_or_salt_reference"
    ]
    not_synthesis_candidates = [
        row
        for row in mapping_rows
        if row["synthesis_candidate_status"] == "not_synthesis_candidate"
    ]
    standard_inchikey_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    family_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in mapping_rows:
        standard_inchikey_groups[str(row["standard_inchikey"])].append(row)
        family_groups[str(row["tautomer_family_key"])].append(row)
    standard_overlap_groups = [
        group for group in standard_inchikey_groups.values() if len(group) > 1
    ]
    family_overlap_groups = [
        group for group in family_groups.values() if len(group) > 1
    ]
    # 操作性定义：同一标准 InChIKey、不同规范 SMILES、且均为单一组分。
    # 多片段组可能只是盐的质子位置变化，不能一概称为分子互变异构。
    tautomer_representation_overlap_groups = [
        group
        for group in standard_overlap_groups
        if all(int(row["fragment_count"]) == 1 for row in group)
    ]
    strict_cross_file = sum(
        row["strict_candidate"] and row["cross_file_duplicate"]
        for row in mapping_rows
    )
    summary = {
        "audit_version": RULE_VERSION,
        "source": {
            "doi": SOURCE_DOI,
            "record": SOURCE_RECORD,
            "license_spdx": LICENSE_SPDX,
            "files": checks,
        },
        "rules": {
            "nco_smarts": NCO_SMARTS,
            "ncs_smarts": NCS_SMARTS,
            "allowed_atomic_numbers": sorted(ALLOWED_ATOMIC_NUMBERS),
            "strict_candidate": (
                "exact2_NCO + zero_NCS + one_fragment + formal_charge_0 + "
                "allowed_elements"
            ),
            "record_key": (
                "RDKit canonical isomeric SMILES; no salt stripping, "
                "neutralization or tautomer rewriting"
            ),
            "standard_inchikey": (
                "RDKit default Standard InChIKey; equivalent representations "
                "are retained and reported"
            ),
            "family_split_key": (
                "first 14-character connectivity block of Standard InChIKey; "
                "group before data splitting"
            ),
            "synthesis_candidate_status": {
                "primary_monomer_candidate": (
                    "exact2_NCO + one_fragment + formal_charge_0 + "
                    "allowed_elements"
                ),
                "mixture_or_salt_reference": (
                    "exact2_NCO + more_than_one_fragment; retained only as "
                    "Gold-V reference"
                ),
                "not_synthesis_candidate": (
                    "exact2_NCO single-component record with charge/element "
                    "caution; retained only as Gold-V reference"
                ),
            },
        },
        "files": file_summaries,
        "merged": {
            "raw_row_count": len(audit_rows),
            "valid_row_count": sum(
                row["rdkit_parse_state"] == "valid" for row in audit_rows
            ),
            "invalid_row_count": sum(
                row["rdkit_parse_state"] == "invalid" for row in audit_rows
            ),
            "exact_diNCO_unique_count": len(candidate_rows),
            "strict_candidate_unique_count": len(strict_candidates),
            "conditional_diNCO_unique_count": len(conditional_candidates),
            "strict_cross_file_overlap_count": strict_cross_file,
            "gold_v_reference_count": len(candidate_rows),
            "gold_v_candidate_count": len(candidate_rows),
            "single_component_synthesis_primary_count": len(
                primary_monomer_candidates
            ),
            "mixture_or_salt_reference_count": len(mixture_or_salt_references),
            "not_synthesis_candidate_count": len(not_synthesis_candidates),
            "standard_inchikey_unique_count": len(standard_inchikey_groups),
            "standard_inchikey_overlap_group_count": len(
                standard_overlap_groups
            ),
            "standard_inchikey_overlap_record_count": sum(
                len(group) for group in standard_overlap_groups
            ),
            "tautomer_representation_overlap_group_count": len(
                tautomer_representation_overlap_groups
            ),
            "tautomer_family_unique_count": len(family_groups),
            "tautomer_family_overlap_group_count": len(family_overlap_groups),
            "tautomer_family_overlap_record_count": sum(
                len(group) for group in family_overlap_groups
            ),
            "direct_property_supervision_weight_ceiling": 0.0,
        },
        "scientific_status": {
            "gold_layer": "Gold-V",
            "strict_mode": "admitted_reference",
            "conditional_mode": "conditional_reference",
            "quarantine_classes": [
                "diNCS_exact",
                "mixed_NCO_NCS",
                "other",
                "invalid",
            ],
            "note": (
                "12,072 是 Gold-V 结构参考总数，不是可合成单体数；只有 "
                "primary_monomer_candidate 可进入单体主筛选，但该标签仍只是结构"
                "层面的合成候选，不证明实际可合成。所有记录都不自带实验产率、"
                "供应、EHS、反应性或性能标签，不能当作 Gold-E 真值。"
            ),
        },
    }
    return AuditBundle(
        audit_rows=tuple(audit_rows),
        mapping_rows=tuple(mapping_rows),
        candidate_rows=tuple(candidate_rows),
        summary=summary,
    )


def build_candidate_rows(
    exclude_canonical_smiles: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """返回全部 Gold-V 结构参考；不能把返回长度当作可合成单体数。"""

    excluded = set(exclude_canonical_smiles)
    rows = [
        dict(row)
        for row in audit_source().candidate_rows
        if row["canonical_smiles"] not in excluded
    ]
    if len({row["candidate_id"] for row in rows}) != len(rows):
        raise ValueError("PolyUniverse diNCO candidate_id 重复")
    if len({row["canonical_smiles"] for row in rows}) != len(rows):
        raise ValueError("PolyUniverse diNCO 规范SMILES重复")
    return rows


def build_synthesis_candidate_rows(
    exclude_canonical_smiles: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """仅返回严格的单一组分可合成主候选，供主筛选计数使用。"""

    return [
        row
        for row in build_candidate_rows(exclude_canonical_smiles)
        if row["screening_scope"] == "direct_tpu_building_block"
    ]


def _assert_safe_output(path: Path) -> None:
    root = ROOT.resolve(strict=True)
    target = path.resolve(strict=False)
    target.relative_to(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and (
        target.is_symlink() or not stat.S_ISREG(target.lstat().st_mode)
    ):
        raise ValueError(f"拒绝覆盖非普通文件: {target}")


def _atomic_write_text(path: Path, text: str) -> None:
    _assert_safe_output(path)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        delete=False,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _tsv_text(columns: Iterable[str], rows: Iterable[dict[str, Any]]) -> str:
    import io

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(columns),
        delimiter="\t",
        lineterminator="\n",
        extrasaction="ignore",
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def write_outputs(bundle: AuditBundle | None = None) -> AuditBundle:
    bundle = bundle or audit_source()
    _atomic_write_text(AUDIT_PATH, _tsv_text(AUDIT_COLUMNS, bundle.audit_rows))
    _atomic_write_text(
        MAPPING_PATH, _tsv_text(MAPPING_COLUMNS, bundle.mapping_rows)
    )
    _atomic_write_text(
        SUMMARY_PATH,
        json.dumps(bundle.summary, ensure_ascii=False, indent=2) + "\n",
    )
    return bundle


def main() -> None:
    bundle = write_outputs()
    print(json.dumps(bundle.summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
