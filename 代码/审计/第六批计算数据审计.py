"""审计第六批四个开放聚氨酯计算来源。

本脚本不联网、不运行任何模拟、不创建训练拆分，也不物化训练权重。它只读取
固定字节数和 SHA-256 的官方正文/附件，安全解析 JATS XML 与 OOXML，并输出
来源文件、计算体系、计算观测和计算输入参数的确定性审计清单。

运行：

    python 代码/审计/第六批计算数据审计.py
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import re
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from xml.etree import ElementTree


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "数据/原始" / "外部数据" / "新增开放数据"
AUDIT_DATE = "2026-07-21"
AUDIT_VERSION = "1.0"

HDI_NAME = "MDPI_HDI_PEG双力场TPU"
MDI_NAME = "MDPI_MDI聚醚双组分PU分子动力学"
REAXFF_NAME = "Frontiers_PU_ReaxFF热解"
FEA_NAME = "Figshare_商用PUR形状记忆本构FEA"
SOURCE_NAMES = (HDI_NAME, MDI_NAME, REAXFF_NAME, FEA_NAME)

OUTPUT_NAMES = (
    "内容审计摘要.json",
    "文件校验清单.tsv",
    "计算体系清单.tsv",
    "计算观测清单.tsv",
    "计算输入参数清单.tsv",
)
OUTPUT_WHITELIST = frozenset(
    DATA_ROOT / source / filename
    for source in SOURCE_NAMES
    for filename in OUTPUT_NAMES
)

FROZEN_FILES: dict[str, dict[str, tuple[int, str, str]]] = {
    HDI_NAME: {
        "官方Crossref元数据.json": (
            26_804,
            "ce41088f48b2f0ea320861ba17062481f176ee444a5c44ed39ba7b39544b8609",
            "official_metadata",
        ),
        "PMC全文.xml": (
            136_125,
            "8794d868c9ea7387fd692832666d67424cc18716f6c48dba9bdc571106663ad0",
            "publication_fulltext",
        ),
        "molecules-31-01259-s001.zip": (
            2_359_590,
            "b6387d209876deb6aeb496d2322fd6ed198d5c5a7fb7950fd4518f5ce767ef6b",
            "supplementary_information",
        ),
    },
    MDI_NAME: {
        "官方Crossref元数据.json": (
            10_693,
            "daa8c6e2ade082795354d91e62da0313cab1c6e640084a9f25105f380be95a43",
            "official_metadata",
        ),
        "PMC全文.xml": (
            104_150,
            "fcde14e4f5ad3fc05a28325e38cd1d2ab8f56e19d77e3d3d64304a39a9a9e45a",
            "publication_fulltext",
        ),
    },
    REAXFF_NAME: {
        "官方Crossref元数据.json": (
            15_420,
            "8c3ffbe7f847fcc79af5940b700f0797102d933407e3e683933eac48f71ff65b",
            "official_metadata",
        ),
        "Frontiers全文.html": (
            738_047,
            "38a527fd0883e5fb627f8609ea7212b51134da47e248bb6278e4c84450c047bf",
            "publication_fulltext",
        ),
        "Data Sheet 1.docx": (
            66_320,
            "07c1474ed1bbaa773d077ae55d7617f0d79328e6629556cd597037079d9ebabe",
            "supplementary_information",
        ),
    },
    FEA_NAME: {
        "官方API元数据.json": (
            5_287,
            "efbf1e63c677a28e762a44a7909395465ead76257393257c30eef9d54925c73c",
            "official_metadata",
        ),
        "Simulation Data_PECCII 2026.docx": (
            1_168_942,
            "cdf1d7010b637e54f26b52c0dc2c5ec97b6517a8075a62291450d000c98e6fde",
            "simulation_input_and_report",
        ),
    },
}

FILE_COLUMNS = (
    "source_directory",
    "path",
    "role",
    "bytes",
    "sha256",
    "integrity",
    "license",
    "parser_state",
    "training_split_materialized",
    "training_weight_materialized",
)
SYSTEM_COLUMNS = (
    "source_directory",
    "system_id",
    "origin_kind",
    "chemistry_or_material",
    "composition_or_condition",
    "mapping_type",
    "method_or_solver",
    "protocol_branch_count",
    "reported_seed_replicate_count",
    "split_group",
    "decision",
    "future_weight_ceiling",
    "notes",
)
OBSERVATION_COLUMNS = (
    "source_directory",
    "record_id",
    "origin_kind",
    "target_origin",
    "gold_layer",
    "system_id",
    "condition",
    "property_name",
    "value",
    "unit",
    "uncertainty",
    "uncertainty_unit",
    "quality_evidence",
    "source_location",
    "reduction_level",
    "target_candidate",
    "decision",
    "future_weight_ceiling",
    "split_group",
    "independent_sample_increment",
    "training_split",
    "training_weight",
    "notes",
)
INPUT_COLUMNS = (
    "source_directory",
    "material_or_system_id",
    "target_origin",
    "gold_layer",
    "parameter_group",
    "parameter_name",
    "term_index",
    "value_raw",
    "value_numeric",
    "unit",
    "completeness",
    "source_location",
    "target_candidate",
    "decision",
    "future_weight_ceiling",
    "training_split",
    "training_weight",
    "notes",
)


class AuditBlocked(RuntimeError):
    """原件、解析结构或输出安全门禁失败。"""


@dataclass(frozen=True)
class AuditBundle:
    source_directory: str
    summary: dict[str, Any]
    files: list[dict[str, Any]]
    systems: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    inputs: list[dict[str, Any]]


def _is_reparse_point(path: Path) -> bool:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(details, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    is_junction = getattr(path, "is_junction", lambda: False)
    return path.is_symlink() or bool(flag and attributes & flag) or bool(is_junction())


def _require_plain_directory(path: Path) -> None:
    if not path.is_dir() or _is_reparse_point(path):
        raise AuditBlocked(f"来源目录不是普通目录：{path}")
    if path.resolve(strict=True) != path.absolute():
        raise AuditBlocked(f"来源目录解析发生漂移：{path}")


def _require_plain_file(path: Path) -> None:
    if not path.is_file() or _is_reparse_point(path):
        raise AuditBlocked(f"输入不是普通文件：{path}")
    _require_plain_directory(path.parent)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_frozen(path: Path, expected_size: int, expected_hash: str) -> None:
    _require_plain_file(path)
    actual_size = path.stat().st_size
    if actual_size != expected_size:
        raise AuditBlocked(
            f"输入字节数漂移：{path.name}，期望{expected_size}，实际{actual_size}"
        )
    actual_hash = _sha256(path)
    if actual_hash != expected_hash:
        raise AuditBlocked(
            f"输入SHA-256漂移：{path.name}，期望{expected_hash}，实际{actual_hash}"
        )


def _archive_summary(path: Path) -> dict[str, Any]:
    """安全检查 ZIP/OOXML，不执行宏、OLE、外链或嵌入对象。"""

    _require_plain_file(path)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not infos or archive.testzip() is not None:
                raise AuditBlocked(f"ZIP完整性失败：{path.name}")
            seen: set[str] = set()
            total = 0
            encrypted = 0
            symlink_count = 0
            macro_count = 0
            for info in infos:
                normalized = info.filename.replace("\\", "/")
                pure = PurePosixPath(normalized)
                if (
                    not normalized
                    or normalized.startswith("/")
                    or pure.is_absolute()
                    or ".." in pure.parts
                    or ":" in normalized
                    or normalized in seen
                ):
                    raise AuditBlocked(f"ZIP成员路径不安全：{path.name}:{normalized}")
                seen.add(normalized)
                if info.flag_bits & 0x1:
                    encrypted += 1
                mode = info.external_attr >> 16
                if mode and stat.S_ISLNK(mode):
                    symlink_count += 1
                if normalized.lower().endswith(("vbaproject.bin", ".exe", ".dll")):
                    macro_count += 1
                total += info.file_size
            if encrypted or symlink_count or macro_count:
                raise AuditBlocked(
                    f"ZIP含不允许载荷：encrypted={encrypted}, symlink={symlink_count}, "
                    f"active={macro_count}"
                )
            if len(infos) > 2_000 or total > 200 * 1024 * 1024:
                raise AuditBlocked(f"ZIP规模超过审计边界：{path.name}")
            return {
                "member_count": len(infos),
                "uncompressed_bytes": total,
                "member_names": [item.filename for item in infos],
            }
    except zipfile.BadZipFile as exc:
        raise AuditBlocked(f"ZIP损坏：{path.name}") from exc


def _read_json(path: Path) -> dict[str, Any]:
    _require_plain_file(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AuditBlocked(f"JSON解析失败：{path.name}") from exc
    if not isinstance(data, dict):
        raise AuditBlocked(f"JSON顶层不是对象：{path.name}")
    return data


def _normalize_text(value: str) -> str:
    return " ".join(value.replace("\u00a0", " ").split())


def _element_text(element: ElementTree.Element | None) -> str:
    if element is None:
        return ""
    return _normalize_text("".join(element.itertext()))


def _jats_root(path: Path, expected_doi: str) -> ElementTree.Element:
    _require_plain_file(path)
    try:
        root = ElementTree.parse(path).getroot()
    except ElementTree.ParseError as exc:
        raise AuditBlocked(f"JATS XML解析失败：{path.name}") from exc
    dois = {
        _element_text(node).lower()
        for node in root.findall(".//article-id[@pub-id-type='doi']")
    }
    if expected_doi.lower() not in dois:
        raise AuditBlocked(f"JATS DOI不匹配：{expected_doi}")
    return root


def _jats_tables(root: ElementTree.Element) -> dict[str, list[list[str]]]:
    output: dict[str, list[list[str]]] = {}
    for wrap in root.findall(".//table-wrap"):
        label = _element_text(wrap.find("label"))
        if not label:
            continue
        table = wrap.find(".//table")
        if table is None:
            continue
        rows: list[list[str]] = []
        for tr in table.findall(".//tr"):
            cells = [
                _element_text(cell)
                for cell in list(tr)
                if cell.tag in {"th", "td"}
            ]
            rows.append(cells)
        output[label] = rows
    return output


def _docx_document(path: Path) -> tuple[ElementTree.Element, dict[str, Any]]:
    summary = _archive_summary(path)
    with zipfile.ZipFile(path) as archive:
        try:
            payload = archive.read("word/document.xml")
        except KeyError as exc:
            raise AuditBlocked(f"DOCX缺少word/document.xml：{path.name}") from exc
    try:
        return ElementTree.fromstring(payload), summary
    except ElementTree.ParseError as exc:
        raise AuditBlocked(f"DOCX document.xml解析失败：{path.name}") from exc


def _docx_paragraphs(root: ElementTree.Element) -> list[str]:
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    output: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        text = "".join(
            node.text or "" for node in paragraph.findall(".//w:t", namespace)
        ).strip()
        if text:
            output.append(_normalize_text(text))
    return output


def _docx_tables(root: ElementTree.Element) -> list[list[list[str]]]:
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    output: list[list[list[str]]] = []
    for table in root.findall(".//w:tbl", namespace):
        rows: list[list[str]] = []
        for tr in table.findall("./w:tr", namespace):
            cells: list[str] = []
            for tc in tr.findall("./w:tc", namespace):
                text = "".join(
                    node.text or "" for node in tc.findall(".//w:t", namespace)
                )
                cells.append(_normalize_text(text))
            rows.append(cells)
        output.append(rows)
    return output


def _number(value: str) -> float:
    normalized = (
        value.strip()
        .replace("−", "-")
        .replace("–", "-")
        .replace(",", "")
        .replace(" ", "")
    )
    if "×10" in normalized:
        match = re.fullmatch(r"([+-]?[0-9.]+)×10([+-]?[0-9]+)", normalized)
        if match is None:
            raise AuditBlocked(f"科学计数法无法解析：{value}")
        return float(match.group(1)) * (10 ** int(match.group(2)))
    try:
        return float(normalized)
    except ValueError as exc:
        raise AuditBlocked(f"数值无法解析：{value}") from exc


def _mean_sd(value: str) -> tuple[float, float] | None:
    if value.strip() == "-":
        return None
    match = re.fullmatch(r"\s*([+-]?[0-9.]+)\s*±\s*([0-9.]+)\s*", value)
    if match is None:
        raise AuditBlocked(f"均值±标准差无法解析：{value}")
    return float(match.group(1)), float(match.group(2))


def _format_number(value: float | int | None) -> str:
    if value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    return format(value, ".15g")


def _file_rows(source: str, license_name: str) -> list[dict[str, Any]]:
    base = DATA_ROOT / source
    rows: list[dict[str, Any]] = []
    for name, (size, digest, role) in FROZEN_FILES[source].items():
        path = base / name
        _verify_frozen(path, size, digest)
        parser_state = "verified"
        if path.suffix.lower() in {".zip", ".docx"}:
            _archive_summary(path)
            parser_state = "verified_safe_archive"
        rows.append(
            {
                "source_directory": source,
                "path": name,
                "role": role,
                "bytes": size,
                "sha256": digest,
                "integrity": "pass",
                "license": license_name,
                "parser_state": parser_state,
                "training_split_materialized": "false",
                "training_weight_materialized": "false",
            }
        )
    return rows


def _verify_crossref(path: Path, expected_doi: str) -> dict[str, Any]:
    payload = _read_json(path)
    message = payload.get("message")
    if not isinstance(message, dict):
        raise AuditBlocked(f"Crossref message缺失：{path.name}")
    if str(message.get("DOI", "")).lower() != expected_doi.lower():
        raise AuditBlocked(f"Crossref DOI不匹配：{expected_doi}")
    return message


def _observation(
    *,
    source: str,
    record_id: str,
    origin: str,
    system_id: str,
    condition: str,
    property_name: str,
    value: float,
    unit: str,
    source_location: str,
    candidate: bool,
    decision: str,
    ceiling: float,
    split_group: str,
    uncertainty: float | None = None,
    uncertainty_unit: str = "",
    quality_evidence: str = "",
    reduction_level: str = "aggregate",
    notes: str = "",
) -> dict[str, Any]:
    gold_layer = "Gold-E" if origin == "experimental" else "Gold-C"
    return {
        "source_directory": source,
        "record_id": record_id,
        "origin_kind": origin,
        "target_origin": origin,
        "gold_layer": gold_layer,
        "system_id": system_id,
        "condition": condition,
        "property_name": property_name,
        "value": _format_number(value),
        "unit": unit,
        "uncertainty": _format_number(uncertainty),
        "uncertainty_unit": uncertainty_unit,
        "quality_evidence": quality_evidence,
        "source_location": source_location,
        "reduction_level": reduction_level,
        "target_candidate": str(candidate).lower(),
        "decision": decision,
        "future_weight_ceiling": f"{ceiling:.2f}",
        "split_group": split_group,
        "independent_sample_increment": "0",
        "training_split": "false",
        "training_weight": "",
        "notes": notes,
    }


HDI_SYSTEM_SPECS = {
    "PEG-H400": {"peg_mw": 400, "chains": 130, "box_nm": 4.83, "peg_g": 5.0, "hdi_g": 4.24},
    "PEG-H800": {"peg_mw": 800, "chains": 78, "box_nm": 4.83, "peg_g": None, "hdi_g": None},
    "PEG-H1000": {"peg_mw": 1000, "chains": 64, "box_nm": 4.83, "peg_g": 5.0, "hdi_g": 1.68},
    "PEG-H1500": {"peg_mw": 1500, "chains": 40, "box_nm": 4.84, "peg_g": 5.0, "hdi_g": 1.11},
    "PEG-H2000": {"peg_mw": 2000, "chains": 30, "box_nm": 4.81, "peg_g": None, "hdi_g": None},
}


def audit_hdi_peg() -> AuditBundle:
    source = HDI_NAME
    base = DATA_ROOT / source
    _require_plain_directory(base)
    files = _file_rows(source, "CC-BY-4.0")
    _verify_crossref(base / "官方Crossref元数据.json", "10.3390/molecules31081259")
    root = _jats_root(base / "PMC全文.xml", "10.3390/molecules31081259")
    tables = _jats_tables(root)
    if set(tables) != {"Table 1", "Table 2"}:
        raise AuditBlocked(f"HDI–PEG目标表结构漂移：{sorted(tables)}")
    if len(tables["Table 1"]) != 6 or len(tables["Table 2"]) != 6:
        raise AuditBlocked("HDI–PEG表格行数漂移")
    full_text = _element_text(root)
    required_fragments = (
        "GROMACS simulation package (v. 2025.3)",
        "repeated in 10 independent replicates",
        "strain rate of 106 s−1",
        "Open Force Field (OpenFF) framework (v. 2024.09.0)",
    )
    for fragment in required_fragments:
        if fragment not in full_text:
            raise AuditBlocked(f"HDI–PEG协议证据缺失：{fragment}")

    si_path = base / "molecules-31-01259-s001.zip"
    si_archive = _archive_summary(si_path)
    if si_archive["member_names"] != ["molecules-4226108-supplementary.pdf"]:
        raise AuditBlocked("HDI–PEG SI成员结构漂移")
    with zipfile.ZipFile(si_path) as archive:
        member_bytes = archive.read("molecules-4226108-supplementary.pdf")
    member_hash = hashlib.sha256(member_bytes).hexdigest()
    if len(member_bytes) != 2_854_059 or member_hash != (
        "1b4354451a429f3e18b065cfccb6d2d9461fbd70a0d33c971ab11fd6a00a076e"
    ):
        raise AuditBlocked("HDI–PEG SI PDF成员漂移")

    systems: list[dict[str, Any]] = []
    for system_id, spec in HDI_SYSTEM_SPECS.items():
        mapped = spec["peg_g"] is not None
        formulation = (
            f"PEG={spec['peg_g']} g; HDI={spec['hdi_g']} g"
            if mapped
            else "仅模拟扩展；无对应实验膜配方"
        )
        systems.append(
            {
                "source_directory": source,
                "system_id": system_id,
                "origin_kind": "mixed" if mapped else "md",
                "chemistry_or_material": "HDI–PEG thermoplastic polyurethane",
                "composition_or_condition": (
                    f"PEG_Mw={spec['peg_mw']} g/mol; chains={spec['chains']}; "
                    f"box={spec['box_nm']} nm; {formulation}"
                ),
                "mapping_type": "exact_experimental_formulation" if mapped else "series_extension",
                "method_or_solver": "GROMACS 2025.3; OPLS-AA and OpenFF/Sage 2.2.1",
                "protocol_branch_count": 2,
                "reported_seed_replicate_count": 20,
                "split_group": f"doi:10.3390/molecules31081259|{system_id}",
                "decision": "gold_e_and_gold_c_reference" if mapped else "gold_c_reference",
                "future_weight_ceiling": "0.65" if mapped else "0.20",
                "notes": "两力场各10个独立初速度种子；种子增加精度，不增加化学体系数。",
            }
        )

    observations: list[dict[str, Any]] = []
    table_specs = {
        "Table 1": ("glass_transition_temperature", "K"),
        "Table 2": ("elastic_modulus", "MPa"),
    }
    for table_label, (property_name, unit) in table_specs.items():
        rows = tables[table_label]
        expected_header = (
            ["System Name", "TgOpenFF (K)", "TgOPLS-AA (K)", "Tgexperimental (K)"]
            if table_label == "Table 1"
            else ["System Name", "EOpenFF (MPa)", "EOPLS-AA (MPa)", "Eexperimental (MPa)"]
        )
        if rows[0] != expected_header:
            raise AuditBlocked(f"{table_label}表头漂移：{rows[0]}")
        for row in rows[1:]:
            if len(row) != 4 or row[0] not in HDI_SYSTEM_SPECS:
                raise AuditBlocked(f"{table_label}数据行漂移：{row}")
            system_id = row[0]
            mapped = HDI_SYSTEM_SPECS[system_id]["peg_g"] is not None
            split_group = f"doi:10.3390/molecules31081259|{system_id}"
            for method, cell in (("OpenFF", row[1]), ("OPLS-AA", row[2])):
                parsed = _mean_sd(cell)
                if parsed is None:
                    raise AuditBlocked(f"计算值意外缺失：{table_label}:{system_id}:{method}")
                mean, sd = parsed
                if property_name == "glass_transition_temperature":
                    ceiling = 0.40 if mapped else 0.20
                else:
                    ceiling = 0.20 if mapped else 0.15
                observations.append(
                    _observation(
                        source=source,
                        record_id=f"hdi_{system_id}_{method}_{property_name}",
                        origin="md",
                        system_id=system_id,
                        condition=f"force_field={method}",
                        property_name=property_name,
                        value=mean,
                        unit=unit,
                        uncertainty=sd,
                        uncertainty_unit=unit,
                        source_location=f"PMC全文.xml#{table_label}",
                        candidate=True,
                        decision="gold_c_candidate",
                        ceiling=ceiling,
                        split_group=split_group,
                        notes=(
                            "10个独立初速度种子的汇总；绝对模量为高应变率响应。"
                            if property_name == "elastic_modulus"
                            else "10个独立初速度种子的汇总。"
                        ),
                    )
                )
            experimental = _mean_sd(row[3])
            if experimental is not None:
                mean, sd = experimental
                observations.append(
                    _observation(
                        source=source,
                        record_id=f"hdi_{system_id}_experimental_{property_name}",
                        origin="experimental",
                        system_id=system_id,
                        condition="published_aggregate",
                        property_name=property_name,
                        value=mean,
                        unit=unit,
                        uncertainty=sd,
                        uncertainty_unit=unit,
                        source_location=f"PMC全文.xml#{table_label}",
                        candidate=True,
                        decision="gold_e_published_aggregate",
                        ceiling=0.65,
                        split_group=split_group,
                        notes="论文只给均值和离散度；不得物化为原始试样值。",
                    )
                )

    computed = [row for row in observations if row["origin_kind"] == "md"]
    experimental = [row for row in observations if row["origin_kind"] == "experimental"]
    if len(computed) != 20 or len(experimental) != 6 or len(observations) != 26:
        raise AuditBlocked("HDI–PEG观测计数漂移")
    summary = {
        "audit_date": AUDIT_DATE,
        "audit_version": AUDIT_VERSION,
        "source_directory": source,
        "canonical_identifier": "doi:10.3390/molecules31081259",
        "license": "CC-BY-4.0",
        "gold_layers": ["Gold-E", "Gold-C"],
        "file_count": len(files),
        "formulation_or_series_identity_count": 5,
        "experimental_formulation_count": 3,
        "physical_specimen_count": None,
        "reported_experimental_replicates_per_formulation": 5,
        "computational_system_count": 5,
        "system_force_field_branch_count": 10,
        "reported_independent_seed_run_count": 100,
        "observation_record_count": 26,
        "computed_observation_record_count": 20,
        "experimental_aggregate_observation_record_count": 6,
        "candidate_observation_record_count": 26,
        "numeric_value_count": 52,
        "supplementary_pdf_bytes": len(member_bytes),
        "supplementary_pdf_sha256": member_hash,
        "training_split_materialized": False,
        "training_weight_materialized": False,
        "model_ready_record_count": 0,
        "counting_note": "均值是QoI记录，标准差是不确定度数值；100个种子运行不增加化学体系数。",
        "limitations": [
            "公开SI只有PDF，没有原始轨迹、拓扑或逐种子输出。",
            "MD弹性模量来自10^6 s^-1高应变率，不等于准静态实验模量。",
            "PEG-H800和PEG-H2000没有同配方实验锚点。",
        ],
    }
    return AuditBundle(source, summary, files, systems, observations, [])


def audit_mdi_polyether() -> AuditBundle:
    source = MDI_NAME
    base = DATA_ROOT / source
    _require_plain_directory(base)
    files = _file_rows(source, "CC-BY-4.0")
    _verify_crossref(base / "官方Crossref元数据.json", "10.3390/ma16031006")
    root = _jats_root(base / "PMC全文.xml", "10.3390/ma16031006")
    tables = _jats_tables(root)
    for label in ("Table 2", "Table 3", "Table 4", "Table 5", "Table 6"):
        if label not in tables:
            raise AuditBlocked(f"MDI/聚醚缺少{label}")
    full_text = _element_text(root)
    for fragment in ("COMPASS", "100,000-step", "100 ps dynamic operation"):
        if fragment not in full_text:
            raise AuditBlocked(f"MDI/聚醚协议证据缺失：{fragment}")

    ratios = {"PB1": "0.5", "PB2": "0.55", "PB3": "0.6"}
    if tables["Table 2"] != [
        ["Code", "PB1", "PB2", "PB3"],
        ["A:B Ratio", "1:0.5", "1:0.55", "1:0.6"],
    ]:
        raise AuditBlocked("MDI/聚醚配比表漂移")
    systems = [
        {
            "source_directory": source,
            "system_id": code,
            "origin_kind": "md",
            "chemistry_or_material": "polyether polyol–MDI two-component polyurethane",
            "composition_or_condition": f"polyol:MDI mass ratio=1:{ratio}",
            "mapping_type": "formulation_condition_aggregate",
            "method_or_solver": "Materials Studio Forcite; COMPASS",
            "protocol_branch_count": 4,
            "reported_seed_replicate_count": "",
            "split_group": f"doi:10.3390/ma16031006|ratio_1:{ratio}",
            "decision": "gold_c_reference",
            "future_weight_ceiling": "0.20",
            "notes": "四个温度条件；论文未提供随机种子、原始输入或轨迹。",
        }
        for code, ratio in ratios.items()
    ]
    systems.append(
        {
            "source_directory": source,
            "system_id": "polyol_reference",
            "origin_kind": "md",
            "chemistry_or_material": "neat polyether polyol reference component",
            "composition_or_condition": "component A; no MDI; four temperature conditions",
            "mapping_type": "shared_component_reference",
            "method_or_solver": "Materials Studio Forcite; COMPASS",
            "protocol_branch_count": 4,
            "reported_seed_replicate_count": "",
            "split_group": "doi:10.3390/ma16031006|polyol_reference",
            "decision": "gold_c_component_reference",
            "future_weight_ceiling": "0.20",
            "notes": "Table 3/4/5的A(1)/Epo记录；是三种配比共享的组分参考，不计入PU配方数。",
        }
    )

    ratio_to_system = {ratio: code for code, ratio in ratios.items()}
    observations: list[dict[str, Any]] = []

    # Table 3: 16个直接溶解度参数。
    table3 = tables["Table 3"]
    if table3[0] != ["Temperature (K)", "A (1)", "B (0.6)", "B (0.55)", "B (0.5)"]:
        raise AuditBlocked("MDI/聚醚Table 3表头漂移")
    for row in table3[1:]:
        if len(row) != 5:
            raise AuditBlocked(f"MDI/聚醚Table 3行漂移：{row}")
        temperature = int(_number(row[0]))
        for header, raw in zip(table3[0][1:], row[1:], strict=True):
            ratio_match = re.search(r"\(([^)]+)\)", header)
            if ratio_match is None:
                raise AuditBlocked(f"MDI/聚醚溶解度列无法识别：{header}")
            token = ratio_match.group(1)
            if header.startswith("A"):
                system_id = "polyol_reference"
                split_group = "doi:10.3390/ma16031006|polyol_reference"
                condition = f"temperature={temperature} K; component=polyether_polyol"
            else:
                system_id = ratio_to_system[token]
                split_group = f"doi:10.3390/ma16031006|ratio_1:{token}"
                condition = f"temperature={temperature} K; component=MDI"
            observations.append(
                _observation(
                    source=source,
                    record_id=f"mdi_t3_{temperature}_{header.replace(' ', '_')}",
                    origin="md",
                    system_id=system_id,
                    condition=condition,
                    property_name="solubility_parameter",
                    value=_number(raw),
                    unit="(J/cm^3)^0.5",
                    source_location="PMC全文.xml#Table 3",
                    candidate=True,
                    decision="gold_c_candidate",
                    ceiling=0.20,
                    split_group=split_group,
                    notes="组分级相容性描述符，不是宏观实验真值。",
                )
            )

    # Table 4: 28个能量分量保留为证据，不直接作为目标。
    table4 = tables["Table 4"]
    if len(table4) != 6 or table4[1] != [
        "ET (0.5)", "ET (0.55)", "ET (0.6)", "Epo", "EMDI (0.5)", "EMDI (0.55)", "EMDI (0.6)"
    ]:
        raise AuditBlocked("MDI/聚醚Table 4结构漂移")
    energy_headers = table4[1]
    for row in table4[2:]:
        if len(row) != 8:
            raise AuditBlocked(f"MDI/聚醚Table 4行漂移：{row}")
        temperature = int(_number(row[0]))
        for header, raw in zip(energy_headers, row[1:], strict=True):
            ratio_match = re.search(r"\(([^)]+)\)", header)
            if header == "Epo":
                system_id = "polyol_reference"
                split_group = "doi:10.3390/ma16031006|polyol_reference"
            elif ratio_match is not None:
                ratio = ratio_match.group(1)
                system_id = ratio_to_system[ratio]
                split_group = f"doi:10.3390/ma16031006|ratio_1:{ratio}"
            else:
                raise AuditBlocked(f"MDI/聚醚能量列无法识别：{header}")
            observations.append(
                _observation(
                    source=source,
                    record_id=f"mdi_t4_{temperature}_{header}",
                    origin="md",
                    system_id=system_id,
                    condition=f"temperature={temperature} K",
                    property_name=f"energy_component_{header}",
                    value=_number(raw),
                    unit="kcal/mol",
                    source_location="PMC全文.xml#Table 4",
                    candidate=False,
                    decision="reference_only_intermediate_energy",
                    ceiling=0.0,
                    split_group=split_group,
                    notes="能量分量用于复核结合能，不作为独立材料性能标签。",
                )
            )

    # Table 5: 16个扩散系数；R²作为质量字段，低质量一项硬零。
    table5 = tables["Table 5"]
    if len(table5) != 6 or table5[1] != ["D", "R2"] * 4:
        raise AuditBlocked("MDI/聚醚Table 5结构漂移")
    group_headers = table5[0][1:]
    for row in table5[2:]:
        if len(row) != 9:
            raise AuditBlocked(f"MDI/聚醚Table 5行漂移：{row}")
        temperature = int(_number(row[0]))
        for index, header in enumerate(group_headers):
            diffusion = _number(row[1 + index * 2])
            r_squared = _number(row[2 + index * 2])
            ratio_match = re.search(r"\(([^)]+)\)", header)
            if ratio_match is None:
                raise AuditBlocked(f"MDI/聚醚扩散列无法识别：{header}")
            token = ratio_match.group(1)
            if header.startswith("A"):
                system_id = "polyol_reference"
                split_group = "doi:10.3390/ma16031006|polyol_reference"
            else:
                system_id = ratio_to_system[token]
                split_group = f"doi:10.3390/ma16031006|ratio_1:{token}"
            candidate = r_squared >= 0.90
            observations.append(
                _observation(
                    source=source,
                    record_id=f"mdi_t5_{temperature}_{header.replace(' ', '_')}",
                    origin="md",
                    system_id=system_id,
                    condition=f"temperature={temperature} K",
                    property_name="diffusion_coefficient",
                    value=diffusion,
                    unit="m^2/s",
                    quality_evidence=f"R2={r_squared:.4f}",
                    source_location="PMC全文.xml#Table 5",
                    candidate=candidate,
                    decision="gold_c_candidate" if candidate else "hard_zero_low_fit_quality",
                    ceiling=0.20 if candidate else 0.0,
                    split_group=split_group,
                    notes="R²是拟合质量，不另建目标记录。",
                )
            )

    # Table 6: 12条件×5弹性常数；μ与G定义重复，G行硬零。
    table6 = tables["Table 6"]
    if len(table6) != 14 or table6[1] != ["A:B", "λ", "μ", "E (GPa)", "K (GPa)", "G (GPa)"]:
        raise AuditBlocked("MDI/聚醚Table 6结构漂移")
    property_names = ("lame_lambda", "lame_mu", "young_modulus", "bulk_modulus", "shear_modulus")
    current_temperature: int | None = None
    for row in table6[2:]:
        if len(row) == 7:
            current_temperature = int(_number(row[0]))
            ratio_text = row[1]
            values = row[2:]
        elif len(row) == 6 and current_temperature is not None:
            ratio_text = row[0]
            values = row[1:]
        else:
            raise AuditBlocked(f"MDI/聚醚Table 6行漂移：{row}")
        if not ratio_text.startswith("1:") or len(values) != 5:
            raise AuditBlocked(f"MDI/聚醚Table 6配比漂移：{row}")
        ratio = ratio_text.split(":", 1)[1]
        system_id = ratio_to_system[ratio]
        split_group = f"doi:10.3390/ma16031006|ratio_1:{ratio}"
        for property_name, raw in zip(property_names, values, strict=True):
            duplicate = property_name == "shear_modulus"
            observations.append(
                _observation(
                    source=source,
                    record_id=f"mdi_t6_{current_temperature}_{ratio}_{property_name}",
                    origin="md",
                    system_id=system_id,
                    condition=f"temperature={current_temperature} K",
                    property_name=property_name,
                    value=_number(raw),
                    unit="GPa",
                    source_location="PMC全文.xml#Table 6",
                    candidate=not duplicate,
                    decision="derived_duplicate_of_lame_mu" if duplicate else "gold_c_candidate",
                    ceiling=0.0 if duplicate else 0.15,
                    split_group=split_group,
                    notes="G与μ逐行相同，不能形成双重监督。" if duplicate else "纳米尺度计算弹性常数。",
                )
            )

    if len(observations) != 120:
        raise AuditBlocked(f"MDI/聚醚观测计数漂移：{len(observations)}")
    candidate_count = sum(row["target_candidate"] == "true" for row in observations)
    if candidate_count != 79:
        raise AuditBlocked(f"MDI/聚醚候选计数漂移：{candidate_count}")
    summary = {
        "audit_date": AUDIT_DATE,
        "audit_version": AUDIT_VERSION,
        "source_directory": source,
        "canonical_identifier": "doi:10.3390/ma16031006",
        "license": "CC-BY-4.0",
        "gold_layers": ["Gold-C"],
        "file_count": len(files),
        "formulation_count": 3,
        "computational_system_count": 4,
        "mixture_formulation_system_count": 3,
        "reference_component_system_count": 1,
        "ratio_temperature_condition_count": 12,
        "reference_component_temperature_condition_count": 4,
        "reported_independent_seed_run_count": None,
        "observation_record_count": 120,
        "candidate_observation_record_count": 79,
        "reference_only_energy_component_count": 28,
        "low_fit_quality_diffusion_count": 1,
        "definition_duplicate_mu_g_count": 12,
        "numeric_value_count": 136,
        "fit_quality_numeric_count": 16,
        "training_split_materialized": False,
        "training_weight_materialized": False,
        "model_ready_record_count": 0,
        "counting_note": "136=120个表内计算量+16个R²；R²只作质量证据，μ/G重复不进入候选。",
        "limitations": [
            "论文声明原始数据需向作者索取；没有模拟输入、轨迹或逐种子输出。",
            "聚醚多元醇结构未唯一解析，不编造SMILES。",
            "100 ps协议、随机种子和收敛复核不足，计算模量只作低权重参考。",
        ],
    }
    return AuditBundle(source, summary, files, systems, observations, [])


def audit_reaxff() -> AuditBundle:
    source = REAXFF_NAME
    base = DATA_ROOT / source
    _require_plain_directory(base)
    files = _file_rows(source, "CC-BY-4.0")
    _verify_crossref(base / "官方Crossref元数据.json", "10.3389/fchem.2025.1691308")
    html_text = (base / "Frontiers全文.html").read_text(encoding="utf-8")
    visible_text = html.unescape(re.sub(r"<[^>]+>|<!--.*?-->", "", html_text, flags=re.DOTALL))
    visible_text = re.sub(r"\s+", " ", visible_text)
    for fragment in (
        "Investigation of polyurethane pyrolysis characteristics",
        "1,500",
        "3,000",
        "C154H166O32N16",
        "This chain contains 368 atoms",
        "136.35",
    ):
        if fragment not in visible_text:
            raise AuditBlocked(f"ReaxFF正文证据缺失：{fragment}")
    doc, archive = _docx_document(base / "Data Sheet 1.docx")
    paragraphs = _docx_paragraphs(doc)
    text = "\n".join(paragraphs)
    if _docx_tables(doc):
        raise AuditBlocked("ReaxFF补充材料意外出现机器表格，需重新审计")
    if "Ea of PU was 136.35 kJ/mol" not in text or "coefficient was 0.99" not in text:
        raise AuditBlocked("ReaxFF补充材料定量结果漂移")
    systems = [
        {
            "source_directory": source,
            "system_id": "PU_C154H166O32N16_n8",
            "origin_kind": "md",
            "chemistry_or_material": "MDI/polyol-derived PU model; C154H166O32N16 per chain",
            "composition_or_condition": "10 chains; 3680 atoms; density=1.0 g/cm^3; box=35.70 Å",
            "mapping_type": "generic_polyurethane_model",
            "method_or_solver": "Materials Studio 8.0; COMPASS equilibration; ReaxFF MD",
            "protocol_branch_count": 6,
            "reported_seed_replicate_count": "",
            "split_group": "doi:10.3389/fchem.2025.1691308|PU_C154H166O32N16_n8",
            "decision": "gold_c_ehs_reference",
            "future_weight_ceiling": "0.20",
            "notes": "1500/1800/2100/2400/2700/3000 K为加速热解条件，不是六种材料。",
        }
    ]
    split_group = "doi:10.3389/fchem.2025.1691308|PU_C154H166O32N16_n8"
    observations = [
        _observation(
            source=source,
            record_id="reaxff_activation_energy",
            origin="md",
            system_id="PU_C154H166O32N16_n8",
            condition="Arrhenius fit across 6 accelerated-temperature runs",
            property_name="pyrolysis_activation_energy",
            value=136.35,
            unit="kJ/mol",
            source_location="Data Sheet 1.docx#Figure S1 narrative",
            candidate=True,
            decision="gold_c_ehs_candidate",
            ceiling=0.20,
            split_group=split_group,
            notes="六温度共同拟合的派生QoI；独立样本增量为0。",
        ),
        _observation(
            source=source,
            record_id="reaxff_activation_fit_correlation",
            origin="md",
            system_id="PU_C154H166O32N16_n8",
            condition="Arrhenius fit across 6 accelerated-temperature runs",
            property_name="linear_correlation_coefficient",
            value=0.99,
            unit="dimensionless",
            source_location="Data Sheet 1.docx#Figure S1 narrative",
            candidate=False,
            decision="fit_quality_evidence_only",
            ceiling=0.0,
            split_group=split_group,
            notes="拟合质量不是材料性能目标。",
        ),
    ]
    summary = {
        "audit_date": AUDIT_DATE,
        "audit_version": AUDIT_VERSION,
        "source_directory": source,
        "canonical_identifier": "doi:10.3389/fchem.2025.1691308",
        "license": "CC-BY-4.0",
        "gold_layers": ["Gold-C"],
        "file_count": len(files),
        "computational_system_count": 1,
        "temperature_condition_count": 6,
        "reported_independent_seed_run_count": None,
        "observation_record_count": 2,
        "candidate_observation_record_count": 1,
        "numeric_value_count": 2,
        "docx_table_count": 0,
        "docx_archive_member_count": archive["member_count"],
        "training_split_materialized": False,
        "training_weight_materialized": False,
        "model_ready_record_count": 0,
        "counting_note": "活化能是六温度共同拟合的一个派生目标；相关系数只作质量证据。",
        "limitations": [
            "补充材料没有机器可读反应速率表，只有公式、图和两个明确汇总数值。",
            "没有公开原始轨迹、随机种子或逐温度产物表。",
            "1500–3000 K加速热解不能解释为真实服役温度行为。",
        ],
    }
    return AuditBundle(source, summary, files, systems, observations, [])


FEA_MATERIALS = ("MP-4510", "MP-2510", "TASK-3", "TASK-11")


def _input_parameter(
    *,
    material: str,
    group: str,
    name: str,
    term: int | None,
    raw: str,
    numeric: float | None,
    unit: str,
    completeness: str = "complete",
    notes: str = "",
) -> dict[str, Any]:
    return {
        "source_directory": FEA_NAME,
        "material_or_system_id": material,
        "target_origin": "simulation_input",
        "gold_layer": "Gold-C",
        "parameter_group": group,
        "parameter_name": name,
        "term_index": "" if term is None else term,
        "value_raw": raw,
        "value_numeric": _format_number(numeric),
        "unit": unit,
        "completeness": completeness,
        "source_location": f"Simulation Data_PECCII 2026.docx#material_table:{material}",
        "target_candidate": "false",
        "decision": "simulation_input_zero_target_weight",
        "future_weight_ceiling": "0.00",
        "training_split": "false",
        "training_weight": "",
        "notes": notes or "本构/求解输入不产生性能标签。",
    }


def audit_fea() -> AuditBundle:
    source = FEA_NAME
    base = DATA_ROOT / source
    _require_plain_directory(base)
    files = _file_rows(source, "CC-BY-4.0")
    metadata = _read_json(base / "官方API元数据.json")
    if metadata.get("id") != 31_111_210 or metadata.get("version") != 3:
        raise AuditBlocked("Figshare条目ID或版本漂移")
    if str(metadata.get("doi", "")).lower() != "10.6084/m9.figshare.31111210.v3":
        raise AuditBlocked("Figshare DOI漂移")
    license_info = metadata.get("license")
    if not isinstance(license_info, dict) or license_info.get("name") != "CC BY 4.0":
        raise AuditBlocked("Figshare许可证漂移")
    upstream_files = metadata.get("files")
    if not isinstance(upstream_files, list) or len(upstream_files) != 1:
        raise AuditBlocked("Figshare官方文件清单漂移")
    upstream = upstream_files[0]
    if (
        upstream.get("id") != 62_014_738
        or upstream.get("name") != "Simulation Data_PECCII 2026.docx"
        or upstream.get("size") != 1_168_942
        or upstream.get("supplied_md5") != "75d38dfb8705140954ca09bf4296cf6c"
    ):
        raise AuditBlocked("Figshare官方文件元数据漂移")

    doc, archive = _docx_document(base / "Simulation Data_PECCII 2026.docx")
    paragraphs = _docx_paragraphs(doc)
    paragraph_text = "\n".join(paragraphs)
    for material in FEA_MATERIALS:
        if f"Properties for Polyurethane SMP ({material}," not in paragraph_text:
            raise AuditBlocked(f"Figshare材料身份缺失：{material}")
    tables = _docx_tables(doc)
    if len(tables) != 4:
        raise AuditBlocked(f"Figshare材料表数量漂移：{len(tables)}")

    systems: list[dict[str, Any]] = []
    inputs: list[dict[str, Any]] = []
    for material, table in zip(FEA_MATERIALS, tables, strict=True):
        if len(table) != 18 or table[0] != ["Characteristics", "Value"]:
            raise AuditBlocked(f"Figshare {material}表结构漂移")
        systems.append(
            {
                "source_directory": source,
                "system_id": material,
                "origin_kind": "finite_element",
                "chemistry_or_material": f"commercial polyurethane shape-memory polymer {material}",
                "composition_or_condition": "commercial chemistry/formulation unresolved",
                "mapping_type": "commercial_grade_only",
                "method_or_solver": "ANSYS; linear elastic glassy state; Prony viscoelastic rubbery state",
                "protocol_branch_count": 0,
                "reported_seed_replicate_count": "",
                "split_group": f"doi:10.6084/m9.figshare.31111210.v3|{material}",
                "decision": "gold_c_input_reference_only",
                "future_weight_ceiling": "0.00",
                "notes": "DOCX没有机器可读FEA输出表；本批只登记材料卡和本构输入。",
            }
        )
        properties = {row[0]: row[1:] for row in table[1:7]}
        expected_keys = {
            "Density (kg/m3)",
            "Glass transition temperature (ºC)",
            "Elastic modulus (Pa)",
            "CTE ()",
            "Poisson’s ratio",
            "WLF parameters",
        }
        if set(properties) != expected_keys:
            raise AuditBlocked(f"Figshare {material}材料参数键漂移：{sorted(properties)}")

        density_raw = properties["Density (kg/m3)"][0]
        tg_raw = properties["Glass transition temperature (ºC)"][0]
        poisson_raw = properties["Poisson’s ratio"][0]
        inputs.extend(
            [
                _input_parameter(material=material, group="material_card", name="density", term=None, raw=density_raw, numeric=_number(density_raw), unit="kg/m^3"),
                _input_parameter(material=material, group="material_card", name="glass_transition_temperature", term=None, raw=tg_raw, numeric=_number(tg_raw), unit="degC"),
                _input_parameter(material=material, group="material_card", name="poisson_ratio", term=None, raw=poisson_raw, numeric=_number(poisson_raw), unit="dimensionless"),
            ]
        )

        modulus_raw = properties["Elastic modulus (Pa)"][0]
        modulus_matches = re.findall(r"([0-9.]+)×10([0-9]+)", modulus_raw)
        if len(modulus_matches) != 2:
            raise AuditBlocked(f"Figshare {material}弹性模量无法解析：{modulus_raw}")
        for state_name, match in zip(("below_tg", "above_tg"), modulus_matches, strict=True):
            numeric = float(match[0]) * (10 ** int(match[1]))
            inputs.append(
                _input_parameter(
                    material=material,
                    group="material_card",
                    name=f"elastic_modulus_{state_name}",
                    term=None,
                    raw=f"{match[0]}×10{match[1]}",
                    numeric=numeric,
                    unit="Pa",
                )
            )

        cte_raw = properties["CTE ()"][0]
        cte_matches = re.findall(r"([0-9.]+)×", cte_raw)
        if len(cte_matches) != 2:
            raise AuditBlocked(f"Figshare {material} CTE无法解析：{cte_raw}")
        for state_name, token in zip(("below_tg", "above_tg"), cte_matches, strict=True):
            inputs.append(
                _input_parameter(
                    material=material,
                    group="material_card",
                    name=f"coefficient_thermal_expansion_{state_name}",
                    term=None,
                    raw=f"{token}×10^(missing)",
                    numeric=None,
                    unit="unresolved",
                    completeness="incomplete_exponent_or_unit",
                    notes="OOXML文本未保留指数/单位，禁止规范化该值。",
                )
            )

        wlf_raw = properties["WLF parameters"][0]
        wlf_numbers = [float(item) for item in re.findall(r"(?<![A-Za-z0-9])([0-9]+(?:\.[0-9]+)?)", wlf_raw)]
        if len(wlf_numbers) != 3:
            raise AuditBlocked(f"Figshare {material} WLF无法解析：{wlf_raw}")
        for name, value, unit in zip(
            ("wlf_reference_temperature", "wlf_C1", "wlf_C2"),
            wlf_numbers,
            ("degC", "dimensionless", "degC"),
            strict=True,
        ):
            inputs.append(
                _input_parameter(
                    material=material,
                    group="wlf",
                    name=name,
                    term=None,
                    raw=_format_number(value),
                    numeric=value,
                    unit=unit,
                )
            )

        if table[7] != ["Prony Shear Coefficients (n = 10)", "Relative moduli", "Relaxation time (s)"]:
            raise AuditBlocked(f"Figshare {material} Prony表头漂移")
        prony_rows = table[8:]
        if len(prony_rows) != 10:
            raise AuditBlocked(f"Figshare {material} Prony项数漂移")
        for term_index, row in enumerate(prony_rows, 1):
            if len(row) != 3 or row[0] != "":
                raise AuditBlocked(f"Figshare {material} Prony行漂移：{row}")
            inputs.extend(
                [
                    _input_parameter(
                        material=material,
                        group="prony_shear",
                        name="relative_modulus",
                        term=term_index,
                        raw=row[1],
                        numeric=_number(row[1]),
                        unit="dimensionless",
                    ),
                    _input_parameter(
                        material=material,
                        group="prony_shear",
                        name="relaxation_time",
                        term=term_index,
                        raw=row[2],
                        numeric=_number(row[2]),
                        unit="s",
                    ),
                ]
            )

    if len(inputs) != 120:
        raise AuditBlocked(f"Figshare输入参数计数漂移：{len(inputs)}")
    complete_count = sum(row["completeness"] == "complete" for row in inputs)
    if complete_count != 112:
        raise AuditBlocked(f"Figshare完整输入参数计数漂移：{complete_count}")
    summary = {
        "audit_date": AUDIT_DATE,
        "audit_version": AUDIT_VERSION,
        "source_directory": source,
        "canonical_identifier": "doi:10.6084/m9.figshare.31111210.v3",
        "license": "CC-BY-4.0",
        "gold_layers": ["Gold-C"],
        "file_count": len(files),
        "material_identity_count": 4,
        "formulation_count": None,
        "computational_system_count": 4,
        "finite_element_run_count": 0,
        "prony_term_record_count": 40,
        "input_parameter_numeric_token_count": 120,
        "complete_input_parameter_count": 112,
        "incomplete_cte_parameter_count": 8,
        "observation_record_count": 0,
        "candidate_observation_record_count": 0,
        "numeric_target_value_count": 0,
        "docx_table_count": len(tables),
        "docx_archive_member_count": archive["member_count"],
        "upstream_file_md5": "75d38dfb8705140954ca09bf4296cf6c",
        "training_split_materialized": False,
        "training_weight_materialized": False,
        "model_ready_record_count": 0,
        "counting_note": "120个数值均为材料卡/本构输入；40个Prony项、图片和参数不产生性能目标。",
        "limitations": [
            "四个商业材料的具体聚氨酯配方与结构未公开。",
            "DOCX没有机器可读力–位移、力–应变或其他FEA输出表。",
            "8个CTE值在OOXML文本中缺失指数/单位，保留原始片段但不规范化。",
        ],
    }
    return AuditBundle(source, summary, files, systems, [], inputs)


def _tsv_bytes(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=columns,
        delimiter="\t",
        lineterminator="\n",
        extrasaction="raise",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in columns})
    return stream.getvalue().encode("utf-8")


def render_outputs(bundle: AuditBundle) -> dict[str, bytes]:
    return {
        "内容审计摘要.json": (
            json.dumps(bundle.summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8"),
        "文件校验清单.tsv": _tsv_bytes(bundle.files, FILE_COLUMNS),
        "计算体系清单.tsv": _tsv_bytes(bundle.systems, SYSTEM_COLUMNS),
        "计算观测清单.tsv": _tsv_bytes(bundle.observations, OBSERVATION_COLUMNS),
        "计算输入参数清单.tsv": _tsv_bytes(bundle.inputs, INPUT_COLUMNS),
    }


def _write_atomic(target: Path, payload: bytes) -> None:
    if target not in OUTPUT_WHITELIST:
        raise AuditBlocked(f"输出不在白名单：{target}")
    _require_plain_directory(target.parent)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


AUDITORS: tuple[Callable[[], AuditBundle], ...] = (
    audit_hdi_peg,
    audit_mdi_polyether,
    audit_reaxff,
    audit_fea,
)


def main() -> None:
    for auditor in AUDITORS:
        bundle = auditor()
        for name, payload in render_outputs(bundle).items():
            _write_atomic(DATA_ROOT / bundle.source_directory / name, payload)
        print(
            f"{bundle.source_directory}: systems={len(bundle.systems)} "
            f"observations={len(bundle.observations)} inputs={len(bundle.inputs)}"
        )


if __name__ == "__main__":
    main()
