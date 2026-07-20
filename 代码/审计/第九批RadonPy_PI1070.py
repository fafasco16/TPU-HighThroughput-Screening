"""离线复算第九批 RadonPy PI1070 高通量 DFT/MD 数据审计。

本脚本只读取固定 Git 提交的原件，不联网、不运行模拟、不创建训练集，
也不写入项目总账。它完整审计 1,077 行、157 列，严格把
``polymer_class == 11`` 的 11 条聚氨酯重复单元与其余 1,066 条通用聚合物
分开：前者形成 Gold-C 计算参考，后者仅保留为迁移学习条件参考。

运行：

    python 代码/审计/第九批RadonPy_PI1070.py
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = (
    PROJECT_ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第九批计算_RadonPy_PI1070"
)

PINNED_COMMIT = "840dd4a2b5f261fc9370bb6786eff0b71a463d2f"
PINNED_TREE = "b6d2a7074c7048a9a91554c9c4d3393de67571a7"
REPOSITORY = "RadonPy/RadonPy"
ARTICLE_DOI = "10.1038/s41524-022-00906-4"
AUDIT_DATE = "2026-07-21"
AUDIT_VERSION = "batch9-radonpy-pi1070-v1"


@dataclass(frozen=True)
class FrozenFile:
    size: int
    sha256: str
    git_blob: str | None
    url: str
    role: str


FROZEN_FILES: dict[str, FrozenFile] = {
    "PI1070.csv": FrozenFile(
        1_598_888,
        "4ee41d526db3d03eb5b83010672b4e63a4d51a114871a2187ca7fd57d30556a4",
        "109a773f3da95043cc7861aad3e4e67140865ace",
        f"https://raw.githubusercontent.com/{REPOSITORY}/{PINNED_COMMIT}/data/PI1070.csv",
        "scientific_payload",
    ),
    "README.md": FrozenFile(
        10_621,
        "639450057cb6420917f1e8e70caa44f72bfa23d07e8897c84fcc92544d7d472f",
        "13652ee7a4e006d12f97f470eb6887b063273452",
        f"https://raw.githubusercontent.com/{REPOSITORY}/{PINNED_COMMIT}/README.md",
        "repository_documentation",
    ),
    "LICENSE": FrozenFile(
        1_526,
        "0f660cb23fa6c593637c177cda5b08942a3e433c6a0a0cbb2860fcedf07c72f8",
        "3d7de701e30705db7e4d08d2a82b02c86d6f14da",
        f"https://raw.githubusercontent.com/{REPOSITORY}/{PINNED_COMMIT}/LICENSE",
        "repository_license",
    ),
    "官方提交元数据.json": FrozenFile(
        5_699,
        "856429431cf60edba905f4a080573d5591c28ebecf6a1020d1f6f2d1be5f6025",
        None,
        f"https://api.github.com/repos/{REPOSITORY}/commits/{PINNED_COMMIT}",
        "official_commit_api_snapshot",
    ),
    "官方树元数据.json": FrozenFile(
        42_814,
        "e29a24be10151fe65e3eb9ddd52386330b7634573a1b53604cf15a4da2cf0ee5",
        None,
        f"https://api.github.com/repos/{REPOSITORY}/git/trees/{PINNED_TREE}?recursive=1",
        "official_tree_api_snapshot",
    ),
}

OUTPUT_NAMES = (
    "来源元数据.json",
    "下载清单.tsv",
    "内容审计摘要.json",
    "字段审计清单.tsv",
    "全量行审计.tsv",
    "重复泄漏组.tsv",
    "PU重复单元清单.tsv",
    "PU计算观测清单.tsv",
)
OUTPUT_PATHS = frozenset(SOURCE_DIR / name for name in OUTPUT_NAMES)


IDENTITY_AND_DESCRIPTOR_FIELDS = (
    "monomer_ID",
    "smiles",
    "mol_weight_monomer",
    "atomic_weight_mean",
    "vdw_volume_monomer",
    "qm_total_energy_monomer",
    "qm_homo_monomer",
    "qm_lumo_monomer",
    "qm_dipole_monomer",
    "qm_dipole_x_monomer",
    "qm_dipole_y_monomer",
    "qm_dipole_z_monomer",
    "qm_polarizability_monomer",
    "qm_polarizability_xx_monomer",
    "qm_polarizability_yy_monomer",
    "qm_polarizability_zz_monomer",
    "qm_polarizability_xy_monomer",
    "qm_polarizability_xz_monomer",
    "qm_polarizability_yz_monomer",
    "temp",
    "press",
    "tacticity",
    "DP",
    "n_mol",
    "n_atom_mean",
    "Mn",
)

PROPERTY_GROUPS = (
    "density",
    "Rg",
    "r2",
    "self-diffusion",
    "Cp",
    "Cv",
    "compressibility",
    "bulk_modulus",
    "isentropic_compressibility",
    "isentropic_bulk_modulus",
    "volume_expansion",
    "linear_expansion",
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
)


def _property_headers(base: str) -> tuple[str, ...]:
    return (base, f"{base}_min", f"{base}_max", f"{base}_std", f"{base}_count")


EXPECTED_HEADERS = (
    *IDENTITY_AND_DESCRIPTOR_FIELDS,
    *(header for base in PROPERTY_GROUPS for header in _property_headers(base)),
    "polymer_class",
)

DFT_PROPERTIES = (
    "qm_total_energy_monomer",
    "qm_homo_monomer",
    "qm_lumo_monomer",
    "qm_dipole_monomer",
    "qm_dipole_x_monomer",
    "qm_dipole_y_monomer",
    "qm_dipole_z_monomer",
    "qm_polarizability_monomer",
    "qm_polarizability_xx_monomer",
    "qm_polarizability_yy_monomer",
    "qm_polarizability_zz_monomer",
    "qm_polarizability_xy_monomer",
    "qm_polarizability_xz_monomer",
    "qm_polarizability_yz_monomer",
)

PRIMARY_MD_TARGETS = PROPERTY_GROUPS[:18]
MECHANISTIC_MD_FEATURES = PROPERTY_GROUPS[18:]

BASE_UNITS: dict[str, str] = {
    "monomer_ID": "identifier",
    "smiles": "source_SMILES",
    "mol_weight_monomer": "g/mol",
    "atomic_weight_mean": "g/mol",
    "vdw_volume_monomer": "angstrom^3",
    "qm_total_energy_monomer": "kJ/mol",
    "qm_homo_monomer": "eV",
    "qm_lumo_monomer": "eV",
    "qm_dipole_monomer": "debye",
    "qm_dipole_x_monomer": "debye",
    "qm_dipole_y_monomer": "debye",
    "qm_dipole_z_monomer": "debye",
    "qm_polarizability_monomer": "angstrom^3",
    "qm_polarizability_xx_monomer": "angstrom^3",
    "qm_polarizability_yy_monomer": "angstrom^3",
    "qm_polarizability_zz_monomer": "angstrom^3",
    "qm_polarizability_xy_monomer": "angstrom^3",
    "qm_polarizability_xz_monomer": "angstrom^3",
    "qm_polarizability_yz_monomer": "angstrom^3",
    "temp": "K",
    "press": "atm",
    "tacticity": "category",
    "DP": "count",
    "n_mol": "count",
    "n_atom_mean": "count",
    "Mn": "g/mol",
    "density": "g/cm^3",
    "Rg": "angstrom",
    "r2": "angstrom",
    "self-diffusion": "m^2/s",
    "Cp": "J/(kg*K)",
    "Cv": "J/(kg*K)",
    "compressibility": "1/Pa",
    "bulk_modulus": "Pa",
    "isentropic_compressibility": "1/Pa",
    "isentropic_bulk_modulus": "Pa",
    "volume_expansion": "1/K",
    "linear_expansion": "1/K",
    "static_dielectric_const": "dimensionless",
    "dielectric_const_dc": "dimensionless",
    "nematic_order_parameter": "dimensionless",
    "refractive_index": "dimensionless",
    "thermal_conductivity": "W/(m*K)",
    "thermal_diffusivity": "m^2/s",
    "TC_ke": "W/(m*K)",
    "TC_pe": "W/(m*K)",
    "TC_pair": "W/(m*K)",
    "TC_bond": "W/(m*K)",
    "TC_angle": "W/(m*K)",
    "TC_dihed": "W/(m*K)",
    "TC_improper": "W/(m*K)",
    "TC_kspace": "W/(m*K)",
    "polymer_class": "PoLyInfo_class_code",
}

INTEGER_FIELDS = {
    "DP",
    "n_mol",
    "n_atom_mean",
    "polymer_class",
    *(f"{base}_count" for base in PROPERTY_GROUPS),
}
TEXT_FIELDS = {"monomer_ID", "smiles", "tacticity"}

PU_IDS = {
    "PI656",
    "PI657",
    "PI658",
    "PI659",
    "PI660",
    "PI661",
    "PI662",
    "PI663",
    "PI664",
    "PI665",
    "PI947",
}


class AuditBlocked(RuntimeError):
    """固定输入、结构或科学语义不满足审计协议。"""


@dataclass(frozen=True)
class AuditBundle:
    rows: tuple[dict[str, str], ...]
    pu_rows: tuple[dict[str, str], ...]
    field_rows: tuple[dict[str, object], ...]
    row_audit: tuple[dict[str, object], ...]
    duplicate_groups: tuple[dict[str, object], ...]
    observations: tuple[dict[str, object], ...]
    metadata: dict[str, object]
    summary: dict[str, object]


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def git_blob_sha(payload: bytes) -> str:
    prefix = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(prefix + payload, usedforsecurity=False).hexdigest()


def _require_plain_file(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise AuditBlocked(f"原件缺失或不是普通文件：{path}")
    if path.resolve(strict=True) != path.absolute():
        raise AuditBlocked(f"拒绝经链接解析的原件：{path}")


def read_frozen_files() -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    for name, specification in FROZEN_FILES.items():
        path = SOURCE_DIR / name
        _require_plain_file(path)
        payload = path.read_bytes()
        if len(payload) != specification.size:
            raise AuditBlocked(f"固定字节数漂移：{name}")
        if sha256_bytes(payload) != specification.sha256:
            raise AuditBlocked(f"固定 SHA256 漂移：{name}")
        if specification.git_blob and git_blob_sha(payload) != specification.git_blob:
            raise AuditBlocked(f"固定 Git blob 漂移：{name}")
        payloads[name] = payload
    return payloads


def validate_official_metadata(payloads: Mapping[str, bytes]) -> None:
    try:
        commit = json.loads(payloads["官方提交元数据.json"].decode("utf-8"))
        tree = json.loads(payloads["官方树元数据.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditBlocked("官方 GitHub 元数据快照不可解析") from exc

    if commit.get("sha") != PINNED_COMMIT:
        raise AuditBlocked("官方提交快照不是固定 commit")
    observed_tree = ((commit.get("commit") or {}).get("tree") or {}).get("sha")
    if observed_tree != PINNED_TREE:
        raise AuditBlocked("固定 commit 的 tree 漂移")
    if tree.get("sha") != PINNED_TREE or tree.get("truncated") is not False:
        raise AuditBlocked("官方树快照缺失、漂移或被截断")

    entries = {
        item.get("path"): (item.get("type"), item.get("sha"), item.get("size"))
        for item in tree.get("tree", [])
        if isinstance(item, dict)
    }
    expected = {
        "data/PI1070.csv": (
            "blob",
            FROZEN_FILES["PI1070.csv"].git_blob,
            FROZEN_FILES["PI1070.csv"].size,
        ),
        "README.md": (
            "blob",
            FROZEN_FILES["README.md"].git_blob,
            FROZEN_FILES["README.md"].size,
        ),
        "LICENSE": (
            "blob",
            FROZEN_FILES["LICENSE"].git_blob,
            FROZEN_FILES["LICENSE"].size,
        ),
    }
    for path, identity in expected.items():
        if entries.get(path) != identity:
            raise AuditBlocked(f"官方树中文件对象不闭合：{path}")

    license_text = payloads["LICENSE"].decode("utf-8")
    readme_text = payloads["README.md"].decode("utf-8")
    if "BSD 3-Clause License" not in license_text:
        raise AuditBlocked("固定 LICENSE 不是 BSD 3-Clause")
    if "1070 amorphous polymers" not in readme_text or "PI1070.csv" not in readme_text:
        raise AuditBlocked("README 未闭合 PI1070 数据声明")


def _parse_finite_decimal(value: str, field: str, row_number: int) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise AuditBlocked(f"数值不可解析：行{row_number}/{field}={value!r}") from exc
    if not parsed.is_finite():
        raise AuditBlocked(f"拒绝非有限数值：行{row_number}/{field}")
    return parsed


def parse_csv(payload: bytes) -> tuple[tuple[dict[str, str], ...], tuple[str, ...]]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AuditBlocked("PI1070.csv 不是 UTF-8") from exc
    if "\x00" in text:
        raise AuditBlocked("PI1070.csv 含 NUL 字节")

    reader = csv.DictReader(io.StringIO(text, newline=""))
    headers = tuple(reader.fieldnames or ())
    if headers != EXPECTED_HEADERS or len(headers) != 157:
        raise AuditBlocked(f"CSV 字段漂移：观察到 {len(headers)} 列")

    rows: list[dict[str, str]] = []
    ids: set[str] = set()
    for row_number, raw in enumerate(reader, start=2):
        if None in raw:
            raise AuditBlocked(f"CSV 行含多余字段：{row_number}")
        row = {name: (raw.get(name) or "").strip() for name in headers}
        monomer_id = row["monomer_ID"]
        smiles = row["smiles"]
        if not re.fullmatch(r"PI\d+", monomer_id) or monomer_id in ids:
            raise AuditBlocked(f"monomer_ID 缺失、非法或重复：{monomer_id!r}")
        if not smiles or smiles.count("*") != 2:
            raise AuditBlocked(f"重复单元 SMILES 连接点不为两个：{monomer_id}")
        ids.add(monomer_id)

        for field, value in row.items():
            if value == "":
                continue
            if field in TEXT_FIELDS:
                continue
            parsed = _parse_finite_decimal(value, field, row_number)
            if field in INTEGER_FIELDS and parsed != parsed.to_integral_value():
                raise AuditBlocked(f"整数字段出现非整数：行{row_number}/{field}")
        polymer_class = int(row["polymer_class"])
        if not 1 <= polymer_class <= 21:
            raise AuditBlocked(f"PoLyInfo 分类越界：{monomer_id}/{polymer_class}")

        for base in PROPERTY_GROUPS:
            group = [row[field] for field in _property_headers(base)]
            present = [value != "" for value in group]
            if any(present) and not all(present):
                raise AuditBlocked(f"属性统计组不完整：{monomer_id}/{base}")
            if not all(present):
                continue
            value, minimum, maximum, standard_deviation, count = (
                _parse_finite_decimal(item, base, row_number) for item in group
            )
            if minimum > maximum or value < minimum or value > maximum:
                raise AuditBlocked(f"均值/极值关系异常：{monomer_id}/{base}")
            if standard_deviation < 0 or count <= 0 or count != count.to_integral_value():
                raise AuditBlocked(f"标准差或重复次数非法：{monomer_id}/{base}")
        rows.append(row)

    if len(rows) != 1_077 or len(ids) != 1_077:
        raise AuditBlocked(f"CSV 行数或唯一 ID 数漂移：{len(rows)}/{len(ids)}")
    return tuple(rows), headers


def base_field(field: str) -> str:
    for suffix in ("_min", "_max", "_std", "_count"):
        if field.endswith(suffix) and field[: -len(suffix)] in PROPERTY_GROUPS:
            return field[: -len(suffix)]
    return field


def field_method(field: str) -> tuple[str, str]:
    base = base_field(field)
    if base in DFT_PROPERTIES:
        if "polarizability" in base:
            return (
                "DFT",
                "Psi4; omegaB97M-D3BJ finite-field polarizability with basis sets documented in the article",
            )
        return (
            "DFT",
            "Psi4; omegaB97M-D3BJ single-point electronic properties with article-documented basis sets",
        )
    if base in {
        "thermal_conductivity",
        "thermal_diffusivity",
        *MECHANISTIC_MD_FEATURES,
    }:
        return (
            "NEMD",
            "LAMMPS all-atom reverse NEMD (Muller-Plathe), GAFF2; correlated replicate summary",
        )
    if base == "refractive_index":
        return (
            "DFT+MD derived",
            "Lorentz-Lorenz relation using DFT polarizability and MD density",
        )
    if base in PROPERTY_GROUPS:
        return (
            "MD",
            "LAMMPS all-atom classical MD, GAFF2, equilibrated amorphous homopolymer",
        )
    if field in {"temp", "press", "DP", "n_mol", "n_atom_mean", "Mn", "tacticity"}:
        return ("simulation_condition", "RadonPy model and simulation-cell condition")
    if field == "polymer_class":
        return ("classification", "PoLyInfo 21-class code as materialized by source")
    if field in {"monomer_ID", "smiles"}:
        return ("identity", "source-provided repeating-unit identity")
    return ("molecular_descriptor", "source-provided repeating-unit descriptor")


def field_unit(field: str) -> str:
    base = base_field(field)
    if field.endswith("_count") and base in PROPERTY_GROUPS:
        return "count"
    return BASE_UNITS[base]


def leakage_group(smiles: str) -> str:
    token = hashlib.sha256(smiles.encode("utf-8")).hexdigest()[:16]
    return f"radonpy_pi1070_exact_smiles_{token}"


def _decimal_summary(values: Iterable[str]) -> tuple[str, str]:
    parsed = [Decimal(value) for value in values if value != ""]
    if not parsed:
        return "", ""
    return format(min(parsed), "f"), format(max(parsed), "f")


def build_field_audit(
    rows: Sequence[dict[str, str]], pu_rows: Sequence[dict[str, str]], headers: Sequence[str]
) -> tuple[dict[str, object], ...]:
    output: list[dict[str, object]] = []
    for position, field in enumerate(headers, start=1):
        all_values = [row[field] for row in rows]
        pu_values = [row[field] for row in pu_rows]
        present = [value for value in all_values if value != ""]
        minimum, maximum = ("", "")
        if field not in TEXT_FIELDS:
            minimum, maximum = _decimal_summary(present)
        method_family, method_detail = field_method(field)
        output.append(
            {
                "column_index": position,
                "field_name": field,
                "base_property": base_field(field),
                "data_type": "text" if field in TEXT_FIELDS else (
                    "integer" if field in INTEGER_FIELDS else "numeric"
                ),
                "unit": field_unit(field),
                "method_family": method_family,
                "method_detail": method_detail,
                "all_present_count": len(present),
                "all_missing_count": len(rows) - len(present),
                "pu_present_count": sum(value != "" for value in pu_values),
                "pu_missing_count": sum(value == "" for value in pu_values),
                "unique_nonblank_count": len(set(present)),
                "numeric_min": minimum,
                "numeric_max": maximum,
            }
        )
    return tuple(output)


def build_row_and_duplicate_audit(
    rows: Sequence[dict[str, str]], headers: Sequence[str]
) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
    by_smiles: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_smiles[row["smiles"]].append(row)

    row_audit: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=2):
        is_pu = row["polymer_class"] == "11"
        group = leakage_group(row["smiles"])
        duplicate_count = len(by_smiles[row["smiles"]])
        missing = sum(row[field] == "" for field in headers)
        row_audit.append(
            {
                "source_row_number": index,
                "monomer_ID": row["monomer_ID"],
                "smiles": row["smiles"],
                "polymer_class": row["polymer_class"],
                "is_polyurethane_class_11": str(is_pu).lower(),
                "material_scope": (
                    "polyurethane_repeat_unit" if is_pu else "non_PU_general_polymer"
                ),
                "gold_layer": "Gold-C" if is_pu else "Gold-C-transfer",
                "admission_state": "admitted" if is_pu else "conditional_reference",
                "decision": (
                    "admitted_gold_c_pu_computational_reference"
                    if is_pu
                    else "conditional_transfer_reference_not_pu"
                ),
                "field_count": len(headers),
                "present_field_count": len(headers) - missing,
                "missing_field_count": missing,
                "exact_smiles_duplicate_count": duplicate_count,
                "leakage_group": group,
                "split_group": group,
                "independent_material_increment": "1" if duplicate_count == 1 else "0",
                "direct_tpu_target_candidate": "false",
                "training_weight": "",
            }
        )

    duplicate_rows: list[dict[str, object]] = []
    for smiles, members in sorted(by_smiles.items()):
        if len(members) <= 1:
            continue
        group = leakage_group(smiles)
        duplicate_rows.append(
            {
                "duplicate_group_id": group,
                "matching_basis": "exact_source_smiles",
                "smiles": smiles,
                "member_count": len(members),
                "monomer_IDs": ";".join(sorted(row["monomer_ID"] for row in members)),
                "polymer_classes": ";".join(
                    sorted({row["polymer_class"] for row in members}, key=int)
                ),
                "contains_polyurethane_class_11": str(
                    any(row["polymer_class"] == "11" for row in members)
                ).lower(),
                "split_group": group,
                "required_action": "keep_all_members_in_same_split_and_do_not_count_as_independent",
            }
        )
    return tuple(row_audit), tuple(duplicate_rows)


def observation_method(property_name: str) -> tuple[str, str]:
    return field_method(property_name)


def build_observations(pu_rows: Sequence[dict[str, str]]) -> tuple[dict[str, object], ...]:
    output: list[dict[str, object]] = []
    for row in pu_rows:
        split_group = leakage_group(row["smiles"])
        for property_name in (*DFT_PROPERTIES, *PROPERTY_GROUPS):
            value = row[property_name]
            if value == "":
                raise AuditBlocked(f"PU 观测字段缺失：{row['monomer_ID']}/{property_name}")
            method_family, method_detail = observation_method(property_name)
            is_md_group = property_name in PROPERTY_GROUPS
            is_primary_target = property_name in PRIMARY_MD_TARGETS
            output.append(
                {
                    "observation_id": f"radonpy_{row['monomer_ID']}_{property_name}",
                    "monomer_ID": row["monomer_ID"],
                    "smiles": row["smiles"],
                    "polymer_class": row["polymer_class"],
                    "property_name": property_name,
                    "value": value,
                    "unit": field_unit(property_name),
                    "minimum": row[f"{property_name}_min"] if is_md_group else "",
                    "maximum": row[f"{property_name}_max"] if is_md_group else "",
                    "standard_deviation": row[f"{property_name}_std"] if is_md_group else "",
                    "replicate_count": row[f"{property_name}_count"] if is_md_group else "",
                    "method_family": method_family,
                    "method_detail": method_detail,
                    "temperature_K": row["temp"],
                    "pressure_atm": row["press"],
                    "degree_of_polymerization": row["DP"],
                    "chain_count": row["n_mol"],
                    "gold_layer": "Gold-C",
                    "admission_state": "admitted",
                    "decision": "admitted_gold_c_pu_computational_reference",
                    "record_role": (
                        "computational_target"
                        if is_primary_target
                        else "computational_feature_or_mechanistic_reference"
                    ),
                    "target_candidate": str(is_primary_target).lower(),
                    "leakage_group": split_group,
                    "split_group": split_group,
                    "independent_material_increment": "0",
                    "training_weight": "",
                    "source_commit": PINNED_COMMIT,
                }
            )
    return tuple(output)


def audit() -> AuditBundle:
    payloads = read_frozen_files()
    validate_official_metadata(payloads)
    rows, headers = parse_csv(payloads["PI1070.csv"])
    pu_rows = tuple(row for row in rows if row["polymer_class"] == "11")
    if len(pu_rows) != 11 or {row["monomer_ID"] for row in pu_rows} != PU_IDS:
        raise AuditBlocked("polymer_class=11 的 PU 子集身份漂移")
    if any(any(row[field] == "" for field in headers) for row in pu_rows):
        raise AuditBlocked("11 条 PU 重复单元必须保持 157 字段完整")

    field_rows = build_field_audit(rows, pu_rows, headers)
    row_audit, duplicate_groups = build_row_and_duplicate_audit(rows, headers)
    observations = build_observations(pu_rows)
    class_counts = Counter(row["polymer_class"] for row in rows)
    missing_cell_count = sum(row[field] == "" for row in rows for field in headers)
    rows_with_missing = sum(any(row[field] == "" for field in headers) for row in rows)
    unique_smiles = len({row["smiles"] for row in rows})

    metadata: dict[str, object] = {
        "source_name": "RadonPy PI1070",
        "repository": REPOSITORY,
        "fixed_commit": PINNED_COMMIT,
        "fixed_tree": PINNED_TREE,
        "dataset_path": "data/PI1070.csv",
        "repository_license": "BSD-3-Clause",
        "article_doi": ARTICLE_DOI,
        "article_url": f"https://doi.org/{ARTICLE_DOI}",
        "article_citation": (
            "Hayashi, Y.; Shiomi, J.; Morikawa, J.; Yoshida, R. RadonPy: automated "
            "physical property calculation using all-atom classical molecular dynamics "
            "simulations for polymer informatics. npj Comput. Mater. 8, 222 (2022)."
        ),
        "calculation_engines": {"DFT": "Psi4", "MD_and_NEMD": "LAMMPS"},
        "force_field": "GAFF2",
        "simulation_scope": "linear amorphous homopolymers; approximately 10 chains; 300 K; 1 atm",
        "classification_rule": (
            "Only source rows with polymer_class=11 are treated as polyurethane repeat units. "
            "All other rows are explicitly non-PU transfer references."
        ),
        "raw_files": {
            name: {
                "bytes": specification.size,
                "sha256": specification.sha256,
                "git_blob": specification.git_blob,
                "url": specification.url,
                "role": specification.role,
            }
            for name, specification in FROZEN_FILES.items()
        },
    }

    summary: dict[str, object] = {
        "audit_version": AUDIT_VERSION,
        "generated_at": AUDIT_DATE,
        "source": {
            "name": "RadonPy PI1070",
            "repository": REPOSITORY,
            "fixed_commit": PINNED_COMMIT,
            "fixed_tree": PINNED_TREE,
            "license": "BSD-3-Clause",
            "article_doi": ARTICLE_DOI,
        },
        "dimensions": {
            "materialized_csv_rows": len(rows),
            "materialized_csv_columns": len(headers),
            "historical_dataset_label": 1070,
            "dimension_note": (
                "README retains the historical '1070 amorphous polymers' label, while the "
                "fixed PI1070.csv at this commit materially contains 1,077 unique monomer IDs."
            ),
            "unique_monomer_IDs": len({row["monomer_ID"] for row in rows}),
            "unique_exact_smiles": unique_smiles,
            "rows_with_any_missing_value": rows_with_missing,
            "missing_cell_count": missing_cell_count,
            "polymer_class_counts": dict(sorted(class_counts.items(), key=lambda item: int(item[0]))),
        },
        "polyurethane_subset": {
            "classification_code": 11,
            "row_count": len(pu_rows),
            "monomer_IDs": sorted(PU_IDS, key=lambda item: int(item[2:])),
            "complete_157_field_rows": sum(
                all(row[field] != "" for field in headers) for row in pu_rows
            ),
            "unique_exact_smiles": len({row["smiles"] for row in pu_rows}),
            "computational_observation_rows": len(observations),
            "primary_md_target_rows": sum(
                row["target_candidate"] == "true" for row in observations
            ),
            "dft_feature_rows": len(pu_rows) * len(DFT_PROPERTIES),
            "md_or_nemd_property_rows": len(pu_rows) * len(PROPERTY_GROUPS),
            "independent_material_designs": len(pu_rows),
            "admission": "Gold-C admitted computational reference",
        },
        "non_pu_transfer_subset": {
            "row_count": len(rows) - len(pu_rows),
            "admission": "Gold-C-transfer conditional_reference",
            "direct_pu_or_tpu_target_candidates": 0,
            "reason": "polymer_class is not 11; retain only for general-polymer representation transfer",
        },
        "duplicates_and_leakage": {
            "exact_smiles_duplicate_groups": len(duplicate_groups),
            "rows_in_exact_smiles_duplicate_groups": sum(
                int(row["member_count"]) for row in duplicate_groups
            ),
            "split_policy": "group by exact source SMILES; all properties of one row stay together",
            "independent_exact_structure_upper_bound": unique_smiles,
        },
        "scientific_scope": {
            "gold_layer": "Gold-C",
            "method_families": ["DFT", "all-atom classical MD", "reverse NEMD"],
            "experimental_equivalence": False,
            "tpu_formulation_equivalence": False,
            "limitations": [
                "polymer_class=11 denotes polyurethane homopolymer repeat units, not segmented TPU formulations",
                "all values are computational summaries and must retain simulation method and condition labels",
                "replicate mean/min/max/std/count columns are correlated summaries, not independent materials",
                "non-PU rows may support representation transfer but cannot be relabeled as PU or TPU",
                "mechanical TPU targets such as tensile strength, elongation, toughness, hysteresis and cyclic recovery are absent",
            ],
        },
        "training_weight_materialized": False,
        "model_ready": False,
        "direct_training_materialization": False,
    }
    return AuditBundle(
        rows=rows,
        pu_rows=pu_rows,
        field_rows=field_rows,
        row_audit=row_audit,
        duplicate_groups=duplicate_groups,
        observations=observations,
        metadata=metadata,
        summary=summary,
    )


def render_tsv(rows: Sequence[Mapping[str, object]], fieldnames: Sequence[str]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=fieldnames,
        delimiter="\t",
        lineterminator="\n",
        extrasaction="raise",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in fieldnames})
    return buffer.getvalue().encode("utf-8")


def render_outputs(bundle: AuditBundle) -> dict[str, bytes]:
    download_rows = [
        {
            "filename": name,
            "role": specification.role,
            "url": specification.url,
            "commit": PINNED_COMMIT,
            "bytes": specification.size,
            "sha256": specification.sha256,
            "git_blob": specification.git_blob or "",
            "license": "BSD-3-Clause",
            "local_state": "verified_present",
        }
        for name, specification in FROZEN_FILES.items()
    ]

    pu_output_rows: list[dict[str, object]] = []
    for row in bundle.pu_rows:
        prefix = {
            "gold_layer": "Gold-C",
            "admission_state": "admitted",
            "decision": "admitted_gold_c_pu_computational_reference",
            "leakage_group": leakage_group(row["smiles"]),
            "split_group": leakage_group(row["smiles"]),
            "training_weight": "",
            "source_commit": PINNED_COMMIT,
        }
        pu_output_rows.append({**prefix, **row})

    return {
        "来源元数据.json": (
            json.dumps(bundle.metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8"),
        "下载清单.tsv": render_tsv(
            download_rows,
            (
                "filename",
                "role",
                "url",
                "commit",
                "bytes",
                "sha256",
                "git_blob",
                "license",
                "local_state",
            ),
        ),
        "内容审计摘要.json": (
            json.dumps(bundle.summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8"),
        "字段审计清单.tsv": render_tsv(
            bundle.field_rows,
            (
                "column_index",
                "field_name",
                "base_property",
                "data_type",
                "unit",
                "method_family",
                "method_detail",
                "all_present_count",
                "all_missing_count",
                "pu_present_count",
                "pu_missing_count",
                "unique_nonblank_count",
                "numeric_min",
                "numeric_max",
            ),
        ),
        "全量行审计.tsv": render_tsv(
            bundle.row_audit,
            tuple(bundle.row_audit[0].keys()),
        ),
        "重复泄漏组.tsv": render_tsv(
            bundle.duplicate_groups,
            (
                "duplicate_group_id",
                "matching_basis",
                "smiles",
                "member_count",
                "monomer_IDs",
                "polymer_classes",
                "contains_polyurethane_class_11",
                "split_group",
                "required_action",
            ),
        ),
        "PU重复单元清单.tsv": render_tsv(
            pu_output_rows,
            (
                "gold_layer",
                "admission_state",
                "decision",
                "leakage_group",
                "split_group",
                "training_weight",
                "source_commit",
                *EXPECTED_HEADERS,
            ),
        ),
        "PU计算观测清单.tsv": render_tsv(
            bundle.observations,
            tuple(bundle.observations[0].keys()),
        ),
    }


def input_snapshot() -> dict[str, tuple[int, str]]:
    return {
        name: ((SOURCE_DIR / name).stat().st_size, sha256_bytes((SOURCE_DIR / name).read_bytes()))
        for name in FROZEN_FILES
    }


def atomic_write(path: Path, payload: bytes) -> None:
    if path not in OUTPUT_PATHS:
        raise AuditBlocked(f"拒绝写入白名单外路径：{path}")
    if not SOURCE_DIR.is_dir() or SOURCE_DIR.is_symlink():
        raise AuditBlocked("审计来源目录缺失或是链接")
    if path.exists() and (not path.is_file() or path.is_symlink()):
        raise AuditBlocked(f"拒绝覆盖非普通文件：{path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".audit.tmp", dir=SOURCE_DIR
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if not temporary.is_file() or temporary.is_symlink():
            raise AuditBlocked(f"临时输出不是普通文件：{temporary}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    before = input_snapshot()
    bundle = audit()
    outputs = render_outputs(bundle)
    if set(outputs) != set(OUTPUT_NAMES):
        raise AuditBlocked("输出文件集合漂移")
    if before != input_snapshot():
        raise AuditBlocked("审计期间固定输入发生变化")
    for name in OUTPUT_NAMES:
        atomic_write(SOURCE_DIR / name, outputs[name])
    if before != input_snapshot():
        raise AuditBlocked("写出后固定输入发生变化")
    print(json.dumps(bundle.summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
