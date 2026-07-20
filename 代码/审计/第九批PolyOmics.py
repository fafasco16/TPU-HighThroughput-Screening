"""审计固定版本 PolyOmics 计算数据库并提取 PU 多保真参考行。

该脚本只读取 Hugging Face 数据集 ``yhayashi1986/PolyOmics`` 的固定提交，
不联网、不运行模拟，也不生成训练集。它把 ``class_PURT``（聚氨酯）与
``class_PURA``（聚脲邻域）从通用聚合物中分开，并分别记录平衡计算和热导
计算的核验状态。一般聚合物只做来源级迁移预训练统计，不能冒充 TPU 新材料。

运行：

    python 代码/审计/第九批PolyOmics.py
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from rdkit import Chem, rdBase


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第九批计算_PolyOmics"
)
CSV_NAME = "general_polymers_with_sp_abbe_dynamic-dielectric.csv"
CSV_PATH = SOURCE_DIR / CSV_NAME

DATASET_ID = "yhayashi1986/PolyOmics"
DATASET_DOI = "10.57967/hf/7475"
PINNED_REVISION = "43c8c74cac5bef00e7c3a6cca95a9fab9ba1979c"
LICENSE_SPDX = "CC-BY-4.0"
SOURCE_ID = "source_polyomics_data"
AUDIT_VERSION = "batch9-polyomics-v1"

FROZEN_FILES: dict[str, dict[str, Any]] = {
    CSV_NAME: {
        "bytes": 190_382_682,
        "sha256": "e230bd86499559b68b3fd20e7d7fdb538558ccf62463386f981c544953d0c853",
        "url": (
            "https://huggingface.co/datasets/yhayashi1986/PolyOmics/resolve/"
            f"{PINNED_REVISION}/{CSV_NAME}"
        ),
        "role": "scientific_payload",
    },
    "官方数据卡.md": {
        "bytes": 1_267,
        "sha256": "81d6962e1462edcdff16af0580b32c9dd3a43bfec84b286c99b88405f2c92dae",
        "url": (
            "https://huggingface.co/datasets/yhayashi1986/PolyOmics/resolve/"
            f"{PINNED_REVISION}/README.md"
        ),
        "role": "official_dataset_card",
    },
    "官方版本元数据.json": {
        "bytes": 3_407,
        "sha256": "be8d815a1b934ca061a92e0a74781db1855a185b189eb6ef349537481a67604b",
        "url": (
            "https://huggingface.co/api/datasets/yhayashi1986/PolyOmics/revision/"
            f"{PINNED_REVISION}"
        ),
        "role": "official_revision_api_snapshot",
    },
    "DOI元数据.json": {
        "bytes": 384,
        "sha256": "3d5b987a10291cddf8de19f22489b9ad3bbe4b134efc05ac7d0f5e318d6fa2f0",
        "url": "https://doi.org/10.57967/hf/7475",
        "role": "doi_csl_snapshot",
    },
}

OUTPUT_NAMES = (
    "来源元数据.json",
    "下载清单.tsv",
    "数据审计摘要.json",
    "字段覆盖.tsv",
    "PU计算参考.tsv",
    "PU结构重复组.tsv",
)
OUTPUT_PATHS = frozenset(SOURCE_DIR / name for name in OUTPUT_NAMES)

REQUIRED_FIELDS = frozenset(
    {
        "UUID",
        "monomer_ID",
        "smiles_list",
        "smiles_1",
        "smiles_2",
        "smiles_3",
        "smiles_4",
        "copoly_ratio_list",
        "copoly_type",
        "qm_method",
        "temp",
        "press",
        "input_tacticity",
        "tacticity",
        "check_eq",
        "check_tc",
        "do_TC",
        "forcefield",
        "polymer_class",
        "class_PURT",
        "class_PURA",
        "density",
        "thermal_conductivity",
        "tg",
        "sp_total",
        "abbe_number_sos",
        "efdp_permittivity_real",
    }
)

PROPERTY_FIELDS = (
    "density",
    "Rg",
    "Scaled Rg",
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
    "refractive_index",
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
    "tg",
    "sp_ced",
    "sp_total",
    "sp_vdw",
    "sp_ele",
    "sp_ele_short",
    "sp_ele_long",
    "refractive_index_sos_486",
    "refractive_index_sos_589",
    "refractive_index_sos_656",
    "refractive_index_sos",
    "abbe_number_sos",
    "efdp_permittivity_real",
    "efdp_permittivity_imaginary",
    "efdp_dielectric_loss_tan",
)

METHOD_FIELDS = (
    "qm_method",
    "charge",
    "input_natom",
    "input_nchain",
    "ini_density",
    "temp",
    "press",
    "input_tacticity",
    "tacticity",
    "ter_ID_1",
    "ter_ID_2",
    "forcefield",
    "Python_ver",
    "RadonPy_ver",
    "RDKit_ver",
    "Psi4_ver",
    "LAMMPS_ver",
    "preset_eq_ver",
    "preset_tc_ver",
    "preset_tg_ver",
    "preset_sp_ver",
)

COHORTS = ("all", "PURT", "PURA", "PU_union", "general")

PU_REFERENCE_COLUMNS = (
    "record_id",
    "source_id",
    "source_record_id",
    "source_locator",
    "dataset_doi",
    "pinned_revision",
    "license_spdx",
    "source_data_row",
    "uuid",
    "monomer_id",
    "raw_smiles_list",
    "canonical_component_smiles",
    "structure_parse_state",
    "structure_key",
    "simulation_key",
    "leakage_group",
    "exact_smiles_source_count",
    "structure_source_count",
    "simulation_source_count",
    "independent_material_increment_within_source",
    "class_scope",
    "class_PURT",
    "class_PURA",
    "polymer_class",
    "direct_pu_computational_reference",
    "direct_tpu_target_candidate",
    "transfer_only",
    "data_origin",
    "fidelity_level",
    "gold_layer",
    "gold_admission_status",
    "equilibrium_status",
    "thermal_status",
    "thermal_target_admission",
    "potential_weight_ceiling",
    "training_weight",
    "check_eq",
    "check_tc",
    "do_TC",
    *METHOD_FIELDS,
    *PROPERTY_FIELDS,
)

FIELD_COVERAGE_COLUMNS = (
    "field_index",
    "field_name",
    "all_present",
    "all_missing",
    "PURT_present",
    "PURA_present",
    "PU_union_present",
    "general_present",
    "PURT_coverage",
    "PURA_coverage",
    "PU_union_coverage",
    "general_coverage",
    "is_property_field",
)

DUPLICATE_COLUMNS = (
    "structure_key",
    "canonical_component_smiles",
    "PU_union_row_count",
    "distinct_simulation_key_count",
    "PURT_row_count",
    "PURA_row_count",
    "source_record_ids",
)


@dataclass(frozen=True)
class AuditBundle:
    headers: tuple[str, ...]
    pu_rows: tuple[dict[str, Any], ...]
    field_rows: tuple[dict[str, Any], ...]
    duplicate_rows: tuple[dict[str, Any], ...]
    summary: dict[str, Any]
    file_checks: tuple[dict[str, Any], ...]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_frozen_files() -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    for name, spec in FROZEN_FILES.items():
        path = SOURCE_DIR / name
        if not path.is_file():
            raise FileNotFoundError(f"缺少 PolyOmics 冻结原件: {path}")
        size = path.stat().st_size
        if size != spec["bytes"]:
            raise ValueError(f"冻结原件字节数不符: {name}; {size} != {spec['bytes']}")
        if name == CSV_NAME:
            digest = _sha256_file(path)
            payloads[name] = b""
        else:
            payload = path.read_bytes()
            digest = _sha256(payload)
            payloads[name] = payload
        if digest != spec["sha256"]:
            raise ValueError(f"冻结原件 SHA256 不符: {name}; {digest}")
    return payloads


def validate_official_metadata(payloads: Mapping[str, bytes]) -> None:
    revision = json.loads(payloads["官方版本元数据.json"].decode("utf-8"))
    if revision.get("id") != DATASET_ID or revision.get("sha") != PINNED_REVISION:
        raise ValueError("官方 Hugging Face 版本元数据与固定 revision 不一致")
    card = payloads["官方数据卡.md"].decode("utf-8").lower()
    if "cc-by-4.0" not in card or "general_polymers" not in card:
        raise ValueError("官方数据卡缺少许可证或主表说明")
    doi = json.loads(payloads["DOI元数据.json"].decode("utf-8"))
    if str(doi.get("DOI", "")).lower() != DATASET_DOI:
        raise ValueError("DOI 元数据与数据集 DOI 不一致")


def _truth(value: str) -> bool:
    return value.strip().casefold() == "true"


@lru_cache(maxsize=100_000)
def _canonical_component(smiles: str) -> tuple[str, bool]:
    raw = smiles.strip()
    if not raw:
        return "", True
    with rdBase.BlockLogs():
        mol = Chem.MolFromSmiles(raw)
    if mol is None:
        return f"RAW:{raw}", False
    return Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True), True


def _structure_identity(row: Mapping[str, str]) -> tuple[str, str, bool]:
    components: list[str] = []
    parsed = True
    for index in range(1, 5):
        raw = row[f"smiles_{index}"].strip()
        if not raw:
            continue
        canonical, valid = _canonical_component(raw)
        components.append(canonical)
        parsed = parsed and valid
    if not components:
        canonical, valid = _canonical_component(row["smiles_list"])
        components.append(canonical)
        parsed = parsed and valid
    identity = {
        "components": components,
        "copoly_ratio_list": row["copoly_ratio_list"].strip(),
        "copoly_type": row["copoly_type"].strip(),
    }
    serialized = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    key = "polyomics_structure_" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]
    return key, "||".join(components), parsed


def _simulation_key(row: Mapping[str, str], structure_key: str) -> str:
    basis = {
        "structure_key": structure_key,
        **{name: row[name].strip() for name in METHOD_FIELDS},
    }
    serialized = json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "polyomics_simulation_" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]


def _thermal_status(row: Mapping[str, str]) -> str:
    checked = row["check_tc"].strip()
    present = bool(row["thermal_conductivity"].strip())
    if checked == "True" and present:
        return "verified"
    if checked == "True":
        return "check_passed_target_missing"
    if checked == "False" and present:
        return "failed_check_value_retained"
    if checked == "False":
        return "failed_check_target_missing"
    if present:
        return "unchecked_value_retained"
    return "not_available"


def _cohorts(is_purt: bool, is_pura: bool) -> tuple[str, ...]:
    values = ["all"]
    if is_purt:
        values.append("PURT")
    if is_pura:
        values.append("PURA")
    if is_purt or is_pura:
        values.append("PU_union")
    else:
        values.append("general")
    return tuple(values)


def _class_scope(is_purt: bool, is_pura: bool) -> str:
    if is_purt and is_pura:
        return "polyurethane_polyurea_overlap"
    if is_purt:
        return "polyurethane"
    return "polyurea_adjacent"


def _ratio(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator:.8f}" if denominator else ""


@lru_cache(maxsize=1)
def audit() -> AuditBundle:
    payloads = read_frozen_files()
    validate_official_metadata(payloads)

    cohort_rows: Counter[str] = Counter()
    field_present: dict[str, Counter[str]] = {name: Counter() for name in COHORTS}
    check_status: dict[str, Counter[tuple[str, str, str]]] = {
        name: Counter() for name in COHORTS
    }
    thermal_status: dict[str, Counter[str]] = {name: Counter() for name in COHORTS}
    exact_smiles: dict[str, Counter[str]] = {name: Counter() for name in COHORTS}
    structures: dict[str, Counter[str]] = {name: Counter() for name in COHORTS}
    simulations: dict[str, Counter[str]] = {name: Counter() for name in COHORTS}
    structure_parse: dict[str, Counter[str]] = {name: Counter() for name in COHORTS}
    class_combinations: Counter[tuple[bool, bool]] = Counter()
    uuid_counter: Counter[str] = Counter()
    pu_staging: list[dict[str, Any]] = []
    duplicate_evidence: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "canonical_component_smiles": "",
            "simulation_keys": set(),
            "purt": 0,
            "pura": 0,
            "uuids": [],
        }
    )

    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("PolyOmics 主表缺少表头")
        headers = tuple(reader.fieldnames)
        if len(headers) != 255 or len(set(headers)) != len(headers):
            raise ValueError(f"PolyOmics 主表表头异常: {len(headers)} 列")
        missing_required = REQUIRED_FIELDS.difference(headers)
        if missing_required:
            raise ValueError(f"PolyOmics 主表缺少必需字段: {sorted(missing_required)}")

        for source_data_row, row in enumerate(reader, start=1):
            if None in row:
                raise ValueError(f"第 {source_data_row} 行存在越界单元格")
            is_purt = _truth(row["class_PURT"])
            is_pura = _truth(row["class_PURA"])
            cohorts = _cohorts(is_purt, is_pura)
            class_combinations[(is_purt, is_pura)] += 1
            uuid_counter[row["UUID"]] += 1
            structure_key, canonical_components, parsed = _structure_identity(row)
            simulation_key = _simulation_key(row, structure_key)
            tc_status = _thermal_status(row)
            for cohort in cohorts:
                cohort_rows[cohort] += 1
                for field in headers:
                    if row[field].strip():
                        field_present[cohort][field] += 1
                check_status[cohort][
                    (row["check_eq"], row["check_tc"], row["do_TC"])
                ] += 1
                thermal_status[cohort][tc_status] += 1
                exact_smiles[cohort][row["smiles_list"]] += 1
                structures[cohort][structure_key] += 1
                simulations[cohort][simulation_key] += 1
                structure_parse[cohort]["parsed" if parsed else "fallback_raw"] += 1

            if is_purt or is_pura:
                evidence = duplicate_evidence[structure_key]
                evidence["canonical_component_smiles"] = canonical_components
                evidence["simulation_keys"].add(simulation_key)
                evidence["purt"] += int(is_purt)
                evidence["pura"] += int(is_pura)
                evidence["uuids"].append(row["UUID"])
                pu_staging.append(
                    {
                        "source_data_row": source_data_row,
                        "row": dict(row),
                        "is_purt": is_purt,
                        "is_pura": is_pura,
                        "canonical_components": canonical_components,
                        "parsed": parsed,
                        "structure_key": structure_key,
                        "simulation_key": simulation_key,
                        "thermal_status": tc_status,
                    }
                )

    if cohort_rows["all"] != 95_335:
        raise ValueError(f"PolyOmics 主表行数异常: {cohort_rows['all']}")

    field_rows: list[dict[str, Any]] = []
    for index, field in enumerate(headers, start=1):
        field_rows.append(
            {
                "field_index": index,
                "field_name": field,
                "all_present": field_present["all"][field],
                "all_missing": cohort_rows["all"] - field_present["all"][field],
                "PURT_present": field_present["PURT"][field],
                "PURA_present": field_present["PURA"][field],
                "PU_union_present": field_present["PU_union"][field],
                "general_present": field_present["general"][field],
                "PURT_coverage": _ratio(field_present["PURT"][field], cohort_rows["PURT"]),
                "PURA_coverage": _ratio(field_present["PURA"][field], cohort_rows["PURA"]),
                "PU_union_coverage": _ratio(
                    field_present["PU_union"][field], cohort_rows["PU_union"]
                ),
                "general_coverage": _ratio(
                    field_present["general"][field], cohort_rows["general"]
                ),
                "is_property_field": str(field in PROPERTY_FIELDS).lower(),
            }
        )

    pu_rows: list[dict[str, Any]] = []
    first_structure: set[str] = set()
    admission_counter: Counter[str] = Counter()
    for staged in pu_staging:
        row = staged["row"]
        is_purt = staged["is_purt"]
        eq_verified = _truth(row["check_eq"])
        admitted = bool(is_purt and eq_verified)
        admission = "admitted_reference" if admitted else "conditional_reference"
        thermal_verified = staged["thermal_status"] == "verified"
        thermal_admission = (
            "admitted_reference"
            if is_purt and thermal_verified
            else (
                "conditional_reference"
                if row["thermal_conductivity"].strip()
                else "not_available"
            )
        )
        structure_key = staged["structure_key"]
        independent = structure_key not in first_structure
        first_structure.add(structure_key)
        admission_counter[admission] += 1
        record = {
            "record_id": f"polyomics:{row['UUID']}",
            "source_id": SOURCE_ID,
            "source_record_id": row["UUID"],
            "source_locator": f"{CSV_NAME}:data_row={staged['source_data_row']}",
            "dataset_doi": DATASET_DOI,
            "pinned_revision": PINNED_REVISION,
            "license_spdx": LICENSE_SPDX,
            "source_data_row": staged["source_data_row"],
            "uuid": row["UUID"],
            "monomer_id": row["monomer_ID"],
            "raw_smiles_list": row["smiles_list"],
            "canonical_component_smiles": staged["canonical_components"],
            "structure_parse_state": "rdkit_canonical" if staged["parsed"] else "raw_fallback",
            "structure_key": structure_key,
            "simulation_key": staged["simulation_key"],
            "leakage_group": structure_key,
            "exact_smiles_source_count": exact_smiles["PU_union"][row["smiles_list"]],
            "structure_source_count": structures["PU_union"][structure_key],
            "simulation_source_count": simulations["PU_union"][staged["simulation_key"]],
            "independent_material_increment_within_source": int(independent),
            "class_scope": _class_scope(is_purt, staged["is_pura"]),
            "class_PURT": str(is_purt).lower(),
            "class_PURA": str(staged["is_pura"]).lower(),
            "polymer_class": row["polymer_class"],
            # class_PURT 表示聚氨酯类计算记录，但并不能证明它是具有软/硬段、
            # 分子量与工艺闭合的热塑性聚氨酯配方。它可以直接支持 PU 计算任务，
            # 不能被提升为“直接 TPU 性能标签”。
            "direct_pu_computational_reference": str(is_purt).lower(),
            "direct_tpu_target_candidate": "false",
            "transfer_only": str(not is_purt).lower(),
            "data_origin": "computational",
            "fidelity_level": "DFT+MD",
            "gold_layer": "Gold-C",
            "gold_admission_status": admission,
            "equilibrium_status": "verified" if eq_verified else "unverified_or_failed",
            "thermal_status": staged["thermal_status"],
            "thermal_target_admission": thermal_admission,
            "potential_weight_ceiling": "0.20" if admitted else "0.10",
            "training_weight": "",
            "check_eq": row["check_eq"],
            "check_tc": row["check_tc"],
            "do_TC": row["do_TC"],
        }
        record.update({name: row[name] for name in METHOD_FIELDS})
        record.update({name: row[name] for name in PROPERTY_FIELDS})
        if set(record) != set(PU_REFERENCE_COLUMNS):
            raise AssertionError("PU 计算参考行字段与冻结列定义不一致")
        pu_rows.append(record)

    duplicate_rows: list[dict[str, Any]] = []
    for structure_key, count in sorted(structures["PU_union"].items()):
        if count <= 1:
            continue
        evidence = duplicate_evidence[structure_key]
        duplicate_rows.append(
            {
                "structure_key": structure_key,
                "canonical_component_smiles": evidence["canonical_component_smiles"],
                "PU_union_row_count": count,
                "distinct_simulation_key_count": len(evidence["simulation_keys"]),
                "PURT_row_count": evidence["purt"],
                "PURA_row_count": evidence["pura"],
                "source_record_ids": ";".join(sorted(evidence["uuids"])),
            }
        )

    file_checks = tuple(
        {
            "name": name,
            "bytes": spec["bytes"],
            "sha256": spec["sha256"],
            "url": spec["url"],
            "role": spec["role"],
            "verified": True,
        }
        for name, spec in FROZEN_FILES.items()
    )

    def counter_json(counter: Mapping[Any, int]) -> dict[str, int]:
        return {str(key): value for key, value in sorted(counter.items(), key=lambda item: str(item[0]))}

    uniqueness = {}
    for cohort in COHORTS:
        uniqueness[cohort] = {
            "exact_smiles_unique_count": len(exact_smiles[cohort]),
            "exact_smiles_duplicate_row_count": sum(
                value - 1 for value in exact_smiles[cohort].values() if value > 1
            ),
            "structure_key_unique_count": len(structures[cohort]),
            "structure_key_duplicate_row_count": sum(
                value - 1 for value in structures[cohort].values() if value > 1
            ),
            "structure_key_duplicate_group_count": sum(
                value > 1 for value in structures[cohort].values()
            ),
            "simulation_key_unique_count": len(simulations[cohort]),
            "simulation_key_duplicate_row_count": sum(
                value - 1 for value in simulations[cohort].values() if value > 1
            ),
            "simulation_key_duplicate_group_count": sum(
                value > 1 for value in simulations[cohort].values()
            ),
        }

    property_coverage = {
        cohort: {field: field_present[cohort][field] for field in PROPERTY_FIELDS}
        for cohort in COHORTS
    }
    summary = {
        "audit_version": AUDIT_VERSION,
        "source": {
            "source_id": SOURCE_ID,
            "dataset_id": DATASET_ID,
            "doi": DATASET_DOI,
            "pinned_revision": PINNED_REVISION,
            "license_spdx": LICENSE_SPDX,
            "files": list(file_checks),
        },
        "dimensions": {
            "row_count": cohort_rows["all"],
            "column_count": len(headers),
            "uuid_unique_count": len(uuid_counter),
            "uuid_duplicate_row_count": sum(
                value - 1 for value in uuid_counter.values() if value > 1
            ),
        },
        "classes": {
            "class_PURT_row_count": cohort_rows["PURT"],
            "class_PURA_row_count": cohort_rows["PURA"],
            "PURT_PURA_union_row_count": cohort_rows["PU_union"],
            "PURT_PURA_overlap_row_count": class_combinations[(True, True)],
            "PURA_only_row_count": class_combinations[(False, True)],
            "non_PURT_row_count": cohort_rows["all"] - cohort_rows["PURT"],
            "neither_PURT_nor_PURA_row_count": cohort_rows["general"],
            "class_combinations": counter_json(class_combinations),
        },
        "calculation_checks": {
            cohort: {
                "check_eq_check_tc_do_TC": counter_json(check_status[cohort]),
                "thermal_status": counter_json(thermal_status[cohort]),
            }
            for cohort in COHORTS
        },
        "structure_parsing": {
            cohort: counter_json(structure_parse[cohort]) for cohort in COHORTS
        },
        "uniqueness": uniqueness,
        "property_coverage": property_coverage,
        "gold_c_reference": {
            "row_count": len(pu_rows),
            "admitted_reference_count": admission_counter["admitted_reference"],
            "conditional_reference_count": admission_counter["conditional_reference"],
            "training_weight_materialized": False,
            "general_polymer_rows_materialized_as_tpu": 0,
            "cross_source_structure_increment_requires_global_dedup": True,
        },
        "interpretation": {
            "PURT": "class_PURT 且 check_eq=True 的行作为正式 Gold-C 计算参考；热导另受 check_tc 约束。",
            "PURA": "PURA-only 作为与 TPU 相邻的条件参考，不冒充直接 TPU 标签。",
            "general": "既非 PURT 也非 PURA 的行只报告聚合级迁移预训练覆盖，不写入 PU 行表。",
            "duplicates": "structure_key 用规范组分结构和组成定义；跨 SMiPoly/PolyUniverse 的增量由全局总账再次去重。",
        },
    }
    return AuditBundle(
        headers=headers,
        pu_rows=tuple(pu_rows),
        field_rows=tuple(field_rows),
        duplicate_rows=tuple(duplicate_rows),
        summary=summary,
        file_checks=file_checks,
    )


def build_computation_rows() -> tuple[dict[str, Any], ...]:
    """返回总账可接入的 PURT/PURA Gold-C 参考行，不赋训练权重。"""

    return audit().pu_rows


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _tsv_bytes(rows: Iterable[Mapping[str, Any]], columns: Sequence[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=list(columns),
        delimiter="\t",
        lineterminator="\n",
        extrasaction="raise",
    )
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def render_outputs(bundle: AuditBundle) -> dict[str, bytes]:
    source_metadata = {
        **bundle.summary["source"],
        "audit_version": AUDIT_VERSION,
        "scientific_scope": "PolyOmics fixed-revision DFT+MD reference; PURT direct and PURA adjacent",
        "training_dataset_created": False,
    }
    download_rows = [
        {
            "name": row["name"],
            "bytes": row["bytes"],
            "sha256": row["sha256"],
            "role": row["role"],
            "url": row["url"],
            "verified": str(row["verified"]).lower(),
        }
        for row in bundle.file_checks
    ]
    return {
        "来源元数据.json": _json_bytes(source_metadata),
        "下载清单.tsv": _tsv_bytes(
            download_rows, ("name", "bytes", "sha256", "role", "url", "verified")
        ),
        "数据审计摘要.json": _json_bytes(bundle.summary),
        "字段覆盖.tsv": _tsv_bytes(bundle.field_rows, FIELD_COVERAGE_COLUMNS),
        "PU计算参考.tsv": _tsv_bytes(bundle.pu_rows, PU_REFERENCE_COLUMNS),
        "PU结构重复组.tsv": _tsv_bytes(bundle.duplicate_rows, DUPLICATE_COLUMNS),
    }


def atomic_write(path: Path, payload: bytes) -> None:
    resolved = path.resolve()
    if resolved not in {item.resolve() for item in OUTPUT_PATHS}:
        raise ValueError(f"拒绝写入未登记输出: {resolved}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".audit.tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> None:
    bundle = audit()
    for name, payload in render_outputs(bundle).items():
        atomic_write(SOURCE_DIR / name, payload)
    print(
        json.dumps(
            {
                "rows": bundle.summary["dimensions"]["row_count"],
                "PURT": bundle.summary["classes"]["class_PURT_row_count"],
                "PURA": bundle.summary["classes"]["class_PURA_row_count"],
                "PU_union": bundle.summary["classes"]["PURT_PURA_union_row_count"],
                "admitted": bundle.summary["gold_c_reference"]["admitted_reference_count"],
                "conditional": bundle.summary["gold_c_reference"]["conditional_reference_count"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
