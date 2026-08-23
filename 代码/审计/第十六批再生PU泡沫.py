"""审计并物化再生 PU 泡沫的配方、压缩、黏度与导热实验数据。

数据包来自 Zenodo 固定记录 10.5281/zenodo.5713819，论文最新版为
10.12688/openreseurope.13288.2。原始压缩 CSV 延伸到约 96% 形变，但论文按
UNE-EN ISO 604 明确指出当前试样几何只在 0--27% 形变内有意义，因此本模块
只把该区间的应力--应变点物化为 Gold-E 条件参考。RPUF0 的第 7 个文件是
同一仪器的西班牙语 13 列导出；其中真实压缩应变位于第 10 列，而不是恒为零
的“循环计数”列，因此按列名解析并保留其有效区间数据。

原始包还存在一项关键冲突：标为 RPUF3.0 的导热工作簿与 RPUF0 的单元格
内容完全相同。该工作簿只计冲突证据，不生成第二个性能标签。可靠但不完整
的曲线、工作簿和派生端点仍进入 Gold 条件参考层；训练权重保持为空。
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from pypdf import PdfReader

from 审计.第十批ACS表格物化 import RECORD_COLUMNS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = (
    PROJECT_ROOT
    / "数据/原始/外部数据/新增开放数据/第十六批实验_再生PU泡沫"
)
ARCHIVE_PATH = SOURCE_DIR / "row data for _Sustainable insulating foams_3.rar"
PDF_PATH = SOURCE_DIR / "论文_10.12688_openreseurope.13288.2.pdf"
EXTRACT_ROOT = SOURCE_DIR / "原始文件"
DATA_ROOT = EXTRACT_ROOT / "row data for _Sustainable insulating foams_3"

OUTPUT_GOLD_E = SOURCE_DIR / "Gold_E_实验记录.tsv"
OUTPUT_COMPRESSION = SOURCE_DIR / "压缩曲线点.tsv"
OUTPUT_VISCOSITY = SOURCE_DIR / "黏度曲线点.tsv"
OUTPUT_AUDIT = SOURCE_DIR / "内容审计摘要.json"
OUTPUT_CHECKSUMS = SOURCE_DIR / "文件校验清单.tsv"
OUTPUT_README = SOURCE_DIR / "来源说明.md"

SOURCE_ID = "source_zenodo_5713819_recycled_pu_foam"
DATASET_DOI = "10.5281/zenodo.5713819"
ARTICLE_DOI = "10.12688/openreseurope.13288.2"
CITATION_KEYS = (
    "elorza-2021-recycled-pu-foam-data;"
    "elorza-2021-recycled-pu-foam-paper"
)
SOURCE_FAMILY_KEY = "family_elorza_recycled_pu_foam"
LICENSE = "CC BY 4.0"

ARCHIVE_BYTES = 3_857_900
ARCHIVE_MD5 = "1b1f47244417b72e3553ec76cf9d751e"
ARCHIVE_SHA256 = "23d0c39729b2aabff631a64239ae0bda8377f2bfd47cce25b52dc96197204a78"
PDF_BYTES = 1_465_953
PDF_SHA256 = "95e32ae80b63afdbf144b002099d4e546e5bb2dfb49aae406ca645df7c02c0e4"
EXTRACTED_FILE_COUNT = 81
EXTRACTED_BYTES = 7_161_867
EXTRACTED_AGGREGATE_SHA256 = (
    "e6c1eb48c0f588edc53a0767eb2526ae15ea9aac60641bcd5a107b5322fd2655"
)
EXPECTED_COMPRESSION_FILE_COUNT = 59
EXPECTED_COMPRESSION_OBSERVED_POINTS = 45_922
EXPECTED_COMPRESSION_VALID_CURVES = 59
EXPECTED_COMPRESSION_CANDIDATE_POINTS = 13_159
EXPECTED_DERIVED_ENDPOINTS = 118
EXPECTED_VISCOSITY_CURVES = 10
EXPECTED_VISCOSITY_POINTS = 581
EXPECTED_THERMAL_WORKBOOKS = 8
EXPECTED_THERMAL_ENDPOINTS = 7
EXPECTED_ARTICLE_COMPONENT_ROWS = 39
EXPECTED_ARTICLE_DENSITY_ROWS = 9
EXPECTED_ARTICLE_COMPRESSION_ROWS = 18
EXPECTED_GOLD_E_ROWS = 13_931

FORMULATION_DATA: dict[str, dict[str, Any]] = {
    "RPUF_0": {"foam": "rigid", "filler": 0.0, "components": {"component_A": 46.52, "component_B": 53.48, "water": 0.0, "CDW_PU_foam_powder": 0.0}, "density": 51.6},
    "RPUF_1.5": {"foam": "rigid", "filler": 1.5, "components": {"component_A": 39.90, "component_B": 57.80, "water": 0.80, "CDW_PU_foam_powder": 1.50}, "density": 42.2},
    "RPUF_3.0": {"foam": "rigid", "filler": 3.0, "components": {"component_A": 39.30, "component_B": 56.92, "water": 0.78, "CDW_PU_foam_powder": 3.00}, "density": 44.4},
    "RPUF_4.5": {"foam": "rigid", "filler": 4.5, "components": {"component_A": 38.70, "component_B": 56.03, "water": 0.77, "CDW_PU_foam_powder": 4.50}, "density": 43.6},
    "RPUF_6.0": {"foam": "rigid", "filler": 6.0, "components": {"component_A": 38.09, "component_B": 55.15, "water": 0.76, "CDW_PU_foam_powder": 6.00}, "density": 43.5},
    "RPUF_10": {"foam": "rigid", "filler": 10.0, "components": {"component_A": 36.48, "component_B": 52.79, "water": 0.73, "CDW_PU_foam_powder": 10.00}, "density": None},
    "SPUF_0": {"foam": "soft", "filler": 0.0, "components": {"component_A": 47.62, "component_B": 52.38, "CDW_PU_foam_powder": 0.0}, "density": 23.1},
    "SPUF_1.5": {"foam": "soft", "filler": 1.5, "components": {"component_A": 46.91, "component_B": 51.59, "CDW_PU_foam_powder": 1.50}, "density": 24.7},
    "SPUF_3.0": {"foam": "soft", "filler": 3.0, "components": {"component_A": 46.21, "component_B": 50.79, "CDW_PU_foam_powder": 3.00}, "density": 26.9},
    "SPUF_4.5": {"foam": "soft", "filler": 4.5, "components": {"component_A": 45.48, "component_B": 50.02, "CDW_PU_foam_powder": 4.50}, "density": 26.6},
    "SPUF_6.0": {"foam": "soft", "filler": 6.0, "components": {"component_A": 44.77, "component_B": 49.23, "CDW_PU_foam_powder": 6.00}, "density": None},
}

CODE_TO_FORMULATION = {
    "RPUF00": "RPUF_0",
    "RPUF15": "RPUF_1.5",
    "RPUF30": "RPUF_3.0",
    "RPUF45": "RPUF_4.5",
    "RPUF60": "RPUF_6.0",
    "SPUF00": "SPUF_0",
    "SPUF15": "SPUF_1.5",
    "SPUF30": "SPUF_3.0",
    "SPUF45": "SPUF_4.5",
    "SPUF60": "SPUF_6.0",
}

ARTICLE_COMPRESSION_KPA = {
    "RPUF_0": (123.7, 213.5),
    "RPUF_1.5": (88.4, 176.0),
    "RPUF_3.0": (79.3, 173.0),
    "RPUF_4.5": (67.6, 130.3),
    "RPUF_6.0": (62.5, 149.7),
    "SPUF_0": (12.2, 17.4),
    "SPUF_1.5": (14.8, 24.5),
    "SPUF_3.0": (11.3, 20.1),
    "SPUF_4.5": (13.2, 23.0),
}


class AuditBlocked(RuntimeError):
    """冻结原件、内容身份、字段或确定性数量发生漂移。"""


def _hash(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _finite(value: Any, context: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AuditBlocked(f"非数值：{context}={value!r}") from exc
    if not math.isfinite(number):
        raise AuditBlocked(f"非有限数值：{context}={value!r}")
    return number


def _text_number(value: float) -> str:
    return format(value, ".12g")


def _relative(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def _normalized_pdf_text(reader: PdfReader, page_number: int) -> str:
    text = reader.pages[page_number - 1].extract_text() or ""
    return " ".join(text.replace("\u00a0", " ").split())


def verify_source() -> dict[str, Any]:
    frozen = (
        (ARCHIVE_PATH, ARCHIVE_BYTES, ARCHIVE_SHA256),
        (PDF_PATH, PDF_BYTES, PDF_SHA256),
    )
    for path, expected_size, expected_sha256 in frozen:
        if not path.is_file():
            raise AuditBlocked(f"缺少冻结输入：{path}")
        actual = (path.stat().st_size, _hash(path))
        if actual != (expected_size, expected_sha256):
            raise AuditBlocked(
                f"冻结输入漂移：{path.name}; bytes={actual[0]}; sha256={actual[1]}"
            )
    if _hash(ARCHIVE_PATH, "md5") != ARCHIVE_MD5:
        raise AuditBlocked("Zenodo 归档 MD5 与官方记录不一致")

    files = sorted(path for path in EXTRACT_ROOT.rglob("*") if path.is_file())
    lines = []
    for path in files:
        relative = path.relative_to(EXTRACT_ROOT).as_posix()
        lines.append(f"{relative}\t{path.stat().st_size}\t{_hash(path)}")
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    if (
        len(files),
        sum(path.stat().st_size for path in files),
        digest,
    ) != (
        EXTRACTED_FILE_COUNT,
        EXTRACTED_BYTES,
        EXTRACTED_AGGREGATE_SHA256,
    ):
        raise AuditBlocked(
            "归档解包内容漂移："
            f"files={len(files)}; bytes={sum(path.stat().st_size for path in files)}; "
            f"aggregate_sha256={digest}"
        )

    reader = PdfReader(PDF_PATH)
    if len(reader.pages) != 21:
        raise AuditBlocked(f"论文页数漂移：{len(reader.pages)}")
    anchors = {
        5: (
            "UNE-EN ISO 604 standard",
            "meaningful only within the 0–27% range of deformation",
            "square prism samples of 25 mm x 25 mm x 30 mm",
        ),
        6: (
            "Composition (%) of rigid polyurethane foam formulations",
            "Composition (%) of soft polyurethane foam formulations",
        ),
        8: (
            "Compression strength (Em) of rigid PU foams",
            "The pristine foam has a thermal conductivity of 0.02462 W/mK",
        ),
        9: (
            "Compression characterization of soft PU foams",
            "The pristine foam has a thermal conductivity of 0.03767 W/mK",
        ),
    }
    for page_number, expected in anchors.items():
        text = _normalized_pdf_text(reader, page_number)
        missing = [anchor for anchor in expected if anchor not in text]
        if missing:
            raise AuditBlocked(f"论文第{page_number}页文本锚点缺失：{missing}")
    return {
        "dataset_doi": DATASET_DOI,
        "article_doi": ARTICLE_DOI,
        "license": LICENSE,
        "archive_md5": ARCHIVE_MD5,
        "archive_sha256": ARCHIVE_SHA256,
        "pdf_sha256": PDF_SHA256,
        "pdf_pages": len(reader.pages),
        "extracted_file_count": len(files),
        "extracted_bytes": EXTRACTED_BYTES,
        "extracted_aggregate_sha256": digest,
        "peer_review_status": "two_approved_with_reservations_two_not_approved",
    }


def _xlsx_cells(path: Path) -> dict[tuple[int, int], str]:
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{ns}si"):
                shared.append("".join(node.text or "" for node in item.iter(f"{ns}t")))
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    cells: dict[tuple[int, int], str] = {}
    for cell in sheet.iter(f"{ns}c"):
        reference = cell.attrib.get("r", "")
        match = re.fullmatch(r"([A-Z]+)(\d+)", reference)
        if not match:
            continue
        letters, row_text = match.groups()
        column = 0
        for letter in letters:
            column = column * 26 + ord(letter) - ord("A") + 1
        cell_type = cell.attrib.get("t", "")
        if cell_type == "inlineStr":
            value = "".join(node.text or "" for node in cell.iter(f"{ns}t"))
        else:
            value_node = cell.find(f"{ns}v")
            value = "" if value_node is None else value_node.text or ""
            if cell_type == "s" and value:
                value = shared[int(value)]
        cells[(int(row_text), column)] = value
    return cells


def _xlsx_content_digest(path: Path) -> str:
    cells = _xlsx_cells(path)
    payload = json.dumps(
        [[row, col, value] for (row, col), value in sorted(cells.items())],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _base_row(**updates: Any) -> dict[str, Any]:
    row: dict[str, Any] = {column: "" for column in RECORD_COLUMNS}
    row.update(
        {
            "source_directory": SOURCE_DIR.name,
            "target_origin": "experimental",
            "split_group": SOURCE_FAMILY_KEY,
            "current_weight_materialized": "false",
            "training_weight": "",
            "license": LICENSE,
            "citation_keys": CITATION_KEYS,
        }
    )
    row.update(updates)
    missing = set(RECORD_COLUMNS) - row.keys()
    if missing:
        raise AuditBlocked(f"Gold-E 字段缺失：{sorted(missing)}")
    return row


def _code_from_path(path: Path) -> str:
    match = re.search(r"([RS]PUF\d{2})", path.name)
    if not match:
        match = re.search(r"([RS]PUF\d{2})", path.parent.name)
    if not match or match.group(1) not in CODE_TO_FORMULATION:
        raise AuditBlocked(f"无法识别配方代码：{path}")
    return match.group(1)


def _compression_source_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    files = sorted(DATA_ROOT.rglob("*.csv"))
    if len(files) != EXPECTED_COMPRESSION_FILE_COUNT:
        raise AuditBlocked(f"压缩 CSV 数量漂移：{len(files)}")
    gold_rows: list[dict[str, Any]] = []
    point_rows: list[dict[str, Any]] = []
    observed_points = 0
    valid_curves = 0
    alternate_schema_files: list[str] = []
    excluded_insufficient_strain_files: list[str] = []
    group_endpoints: defaultdict[str, list[tuple[float, float]]] = defaultdict(list)

    for path in files:
        code = _code_from_path(path)
        formulation = CODE_TO_FORMULATION[code]
        specimen = path.stem
        with path.open("r", encoding="cp1252", newline="") as handle:
            reader = csv.reader(handle, delimiter=";")
            header = next(reader)
            units = next(reader)
            raw_rows = list(reader)
        english_header = (
            "Extension",
            "Load",
            "Deformation 1",
            "Compression strength",
            "Time",
            "Compression extension",
            "Compression deformation",
            "Compression load",
            "Corrected position",
            "Displacement (Deformation 1)",
        )
        spanish_header = (
            "Extensión",
            "Carga",
            "Deformación 1",
            "Esfuerzo de compresión",
            "Tiempo",
            "Recuento de ciclos",
            "Datos marcados",
            "Recuento de marca de graduación",
            "Extensión por compresión",
            "Deformación por compresión",
            "Carga de compresión",
            "Posición corregida",
            "Desplazamiento (Deformación 1)",
        )
        if tuple(header) == english_header:
            stress_index, strain_index = 3, 6
        elif tuple(header) == spanish_header:
            stress_index, strain_index = 3, 9
            alternate_schema_files.append(path.name)
        else:
            raise AuditBlocked(f"压缩 CSV 字段或单位漂移：{path.name}")
        if units[stress_index] != "(MPa)" or units[strain_index] != "(mm/mm)":
            raise AuditBlocked(f"压缩 CSV 字段或单位漂移：{path.name}")
        observed_points += len(raw_rows)
        values: list[tuple[int, float, float]] = []
        for index, raw in enumerate(raw_rows, start=1):
            if len(raw) != len(header):
                raise AuditBlocked(f"压缩 CSV 列数漂移：{path.name}:row={index + 2}")
            strain = _finite(raw[strain_index], f"{path.name}:strain:{index}")
            stress = _finite(raw[stress_index], f"{path.name}:stress:{index}")
            values.append((index, strain, stress))
        if any(
            next_strain <= strain
            for (_, strain, _), (_, next_strain, _) in zip(values, values[1:])
        ):
            raise AuditBlocked(f"压缩应变通道不是严格递增：{path.name}")
        if max(strain for _, strain, _ in values) < 0.25:
            excluded_insufficient_strain_files.append(path.name)
            continue
        valid_curves += 1
        source_sha256 = _hash(path)
        selected = [
            item
            for item in values
            if 0.0 <= item[1] <= 0.27
        ]
        if not selected:
            raise AuditBlocked(f"有效压缩区间为空：{path.name}")
        for point_index, strain, stress in selected:
            observation = f"recycled-pu:{formulation}:{specimen}:compression:{point_index:04d}"
            locator = f"{_relative(path)}#row={point_index + 2}"
            point_rows.append(
                {
                    "formulation_id": formulation,
                    "specimen_id": specimen,
                    "point_index": point_index,
                    "compressive_strain": _text_number(strain),
                    "compressive_stress_mpa": _text_number(stress),
                    "source_locator": locator,
                    "gold_admission_status": "conditional_reference",
                }
            )
            gold_rows.append(
                _base_row(
                    source_record_id=f"compression:{formulation}:{specimen}",
                    observation_id=observation,
                    formulation_id=formulation,
                    sample_id=specimen,
                    record_kind="curve_point",
                    property_name="compressive_stress",
                    value=_text_number(stress),
                    unit="MPa",
                    condition_name="compressive_strain",
                    condition_value=_text_number(strain),
                    condition_unit="dimensionless",
                    data_origin="source_raw_curve",
                    reduction_level="raw_point",
                    method_or_test_protocol=(
                        "UNE-EN ISO 604; 25x25x30 mm square prism; 2 mm/min; "
                        "source-declared valid deformation range 0--27%"
                    ),
                    fidelity_level="direct_experimental_transfer_domain_foam",
                    gold_admission_status="conditional_reference",
                    mapping_status="formulation_resolved_commercial_components_unresolved",
                    protocol_status="protocol_complete_raw_curve_selection_mismatch_with_article",
                    potential_weight_ceiling=0.25,
                    source_locator=locator,
                    file_sha256=source_sha256,
                    notes=(
                        "原始试样曲线；同一配方全部同折。论文称平均使用5个试样，"
                        "归档多数配方有6条曲线，故保留为条件参考。"
                    ),
                )
            )

        ordered = sorted((strain, stress) for _, strain, stress in values)
        endpoints = []
        for target in (0.10, 0.25):
            endpoint = _interpolate(ordered, target)
            endpoints.append(endpoint)
            observation = f"recycled-pu:{formulation}:{specimen}:stress-at-{int(target * 100)}pct"
            gold_rows.append(
                _base_row(
                    source_record_id=f"compression:{formulation}:{specimen}",
                    observation_id=observation,
                    formulation_id=formulation,
                    sample_id=specimen,
                    record_kind="derived_scalar",
                    property_name=f"compressive_stress_at_{int(target * 100)}_percent_strain",
                    value=_text_number(endpoint),
                    unit="MPa",
                    condition_name="compressive_strain",
                    condition_value=_text_number(target),
                    condition_unit="dimensionless",
                    data_origin="deterministically_interpolated_from_source_curve",
                    reduction_level="derived",
                    method_or_test_protocol="linear interpolation between adjacent source points",
                    fidelity_level="deterministic_curve_derived_experimental",
                    gold_admission_status="conditional_reference",
                    mapping_status="formulation_resolved_commercial_components_unresolved",
                    protocol_status="derived_from_valid_source_interval",
                    potential_weight_ceiling=0.20,
                    source_locator=f"{_relative(path)}#interpolated_strain={target}",
                    file_sha256=source_sha256,
                    notes="与母曲线共享试样和损失预算，不增加独立试样数。",
                )
            )
        group_endpoints[formulation].append((endpoints[0], endpoints[1]))

    if observed_points != EXPECTED_COMPRESSION_OBSERVED_POINTS:
        raise AuditBlocked(f"压缩观测点数漂移：{observed_points}")
    if valid_curves != EXPECTED_COMPRESSION_VALID_CURVES:
        raise AuditBlocked(f"有效压缩曲线数漂移：{valid_curves}")
    if len(point_rows) != EXPECTED_COMPRESSION_CANDIDATE_POINTS:
        raise AuditBlocked(f"压缩候选点数漂移：{len(point_rows)}")
    derived_count = sum(row["record_kind"] == "derived_scalar" for row in gold_rows)
    if derived_count != EXPECTED_DERIVED_ENDPOINTS:
        raise AuditBlocked(f"压缩派生端点数漂移：{derived_count}")
    if alternate_schema_files != ["RPUF1_compression_Specimen_7.csv"]:
        raise AuditBlocked(f"压缩备用语言格式文件漂移：{alternate_schema_files}")
    if excluded_insufficient_strain_files:
        raise AuditBlocked(
            "存在无法插值到 25% 应变的压缩曲线："
            f"{excluded_insufficient_strain_files}"
        )

    comparisons: dict[str, Any] = {}
    for formulation, endpoints in sorted(group_endpoints.items()):
        mean10 = sum(item[0] for item in endpoints) / len(endpoints) * 1000
        mean25 = sum(item[1] for item in endpoints) / len(endpoints) * 1000
        article10, article25 = ARTICLE_COMPRESSION_KPA[formulation]
        comparisons[formulation] = {
            "valid_raw_curve_count": len(endpoints),
            "raw_curve_mean_10pct_kpa": mean10,
            "article_table_10pct_kpa": article10,
            "absolute_difference_10pct_kpa": abs(mean10 - article10),
            "raw_curve_mean_25pct_kpa": mean25,
            "article_table_25pct_kpa": article25,
            "absolute_difference_25pct_kpa": abs(mean25 - article25),
        }
    return gold_rows, point_rows, {
        "file_count": len(files),
        "observed_point_count": observed_points,
        "valid_curve_count": valid_curves,
        "candidate_point_count": len(point_rows),
        "derived_endpoint_count": derived_count,
        "alternate_language_schema_files": alternate_schema_files,
        "excluded_insufficient_strain_files": excluded_insufficient_strain_files,
        "aggregate_reproduction": comparisons,
    }


def _interpolate(values: list[tuple[float, float]], target: float) -> float:
    for (x0, y0), (x1, y1) in zip(values, values[1:]):
        if x0 <= target <= x1 and x1 > x0:
            return y0 + (y1 - y0) * (target - x0) / (x1 - x0)
    raise AuditBlocked(f"曲线无法插值到应变 {target}")


def _viscosity_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    paths = sorted(DATA_ROOT.rglob("*viscosty.xlsx"))
    if len(paths) != EXPECTED_VISCOSITY_CURVES:
        raise AuditBlocked(f"黏度工作簿数量漂移：{len(paths)}")
    gold_rows: list[dict[str, Any]] = []
    point_rows: list[dict[str, Any]] = []
    per_curve: dict[str, int] = {}
    for path in paths:
        code = _code_from_path(path)
        formulation = CODE_TO_FORMULATION[code]
        cells = _xlsx_cells(path)
        if cells.get((1, 1)) != "viscosity" or cells.get((1, 2)) != "time":
            raise AuditBlocked(f"黏度表头漂移：{path.name}")
        if cells.get((2, 1)) != "Pa.s" or cells.get((2, 2)) != "min":
            raise AuditBlocked(f"黏度单位漂移：{path.name}")
        source_sha256 = _hash(path)
        count = 0
        for row_number in range(3, max(row for row, _ in cells) + 1):
            if (row_number, 1) not in cells and (row_number, 2) not in cells:
                continue
            viscosity = _finite(cells.get((row_number, 1)), f"{path.name}:A{row_number}")
            elapsed = _finite(cells.get((row_number, 2)), f"{path.name}:B{row_number}")
            count += 1
            observation = f"recycled-pu:{formulation}:viscosity:{count:03d}"
            locator = f"{_relative(path)}#Hoja1!A{row_number}:B{row_number}"
            point_rows.append(
                {
                    "formulation_id": formulation,
                    "point_index": count,
                    "elapsed_time_min": _text_number(elapsed),
                    "viscosity_pa_s": _text_number(viscosity),
                    "source_locator": locator,
                    "gold_admission_status": "conditional_reference",
                }
            )
            gold_rows.append(
                _base_row(
                    source_record_id=f"viscosity:{formulation}",
                    observation_id=observation,
                    formulation_id=formulation,
                    sample_id=f"component_A_slurry_{formulation}",
                    record_kind="curve_point",
                    component_name="component_A_plus_CDW_PU_foam_powder",
                    component_role="polyol_side_mixture_before_isocyanate_addition",
                    property_name="dynamic_viscosity",
                    value=_text_number(viscosity),
                    unit="Pa*s",
                    condition_name="elapsed_time",
                    condition_value=_text_number(elapsed),
                    condition_unit="min",
                    data_origin="source_raw_workbook",
                    reduction_level="raw_point",
                    method_or_test_protocol="TA Instruments AR200ex rheometer; source time series",
                    fidelity_level="direct_experimental_process_property",
                    gold_admission_status="conditional_reference",
                    mapping_status="formulation_resolved_commercial_component_A_unresolved",
                    protocol_status="rheometer_identified_shear_rate_and_temperature_unreported",
                    potential_weight_ceiling=0.25,
                    source_locator=locator,
                    file_sha256=source_sha256,
                    notes="时间序列点共享一个配方运行预算，不按点扩增独立样本。",
                )
            )
        per_curve[formulation] = count
    if len(point_rows) != EXPECTED_VISCOSITY_POINTS:
        raise AuditBlocked(f"黏度点数漂移：{len(point_rows)}")
    return gold_rows, point_rows, {
        "curve_count": len(paths),
        "point_count": len(point_rows),
        "points_per_curve": per_curve,
    }


def _thermal_endpoint(path: Path) -> tuple[float, float, str]:
    cells = _xlsx_cells(path)
    rows: defaultdict[int, dict[int, str]] = defaultdict(dict)
    for (row, column), value in cells.items():
        rows[row][column] = value
    block_rows: list[tuple[int, float, float, float]] = []
    for row_number, values in sorted(rows.items()):
        status = values.get(2, "").strip().lower()
        if status in {"ne", "te", "se", "pe"}:
            try:
                upper = _finite(values.get(3), f"{path.name}:C{row_number}")
                lower = _finite(values.get(4), f"{path.name}:D{row_number}")
                conductivity = _finite(values.get(7), f"{path.name}:G{row_number}")
            except AuditBlocked:
                continue
            block_rows.append((row_number, upper, lower, conductivity))
    if not block_rows:
        raise AuditBlocked(f"导热工作簿没有有效 block：{path.name}")
    for row_number, values in sorted(rows.items()):
        if values.get(3, "").strip() == "Mean Temp" and "Average Cond" in values.get(6, ""):
            result = rows.get(row_number + 1, {})
            mean_temp = _finite(result.get(3), f"{path.name}:C{row_number + 1}")
            conductivity = _finite(result.get(6), f"{path.name}:F{row_number + 1}")
            return conductivity, mean_temp, f"results_table_row_{row_number + 1}"
    row_number, upper, lower, conductivity = block_rows[-1]
    return conductivity, (upper + lower) / 2, f"last_stable_block_row_{row_number}"


def _thermal_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    paths = sorted(DATA_ROOT.glob("*thermal conductivity.xlsx"))
    if len(paths) != EXPECTED_THERMAL_WORKBOOKS:
        raise AuditBlocked(f"导热工作簿数量漂移：{len(paths)}")
    by_code = {_code_from_path(path): path for path in paths}
    duplicate_pair = (by_code["RPUF00"], by_code["RPUF30"])
    digests = tuple(_xlsx_content_digest(path) for path in duplicate_pair)
    if digests[0] != digests[1]:
        raise AuditBlocked("RPUF0/RPUF3.0 导热单元格内容不再重复，需重新审计")
    gold_rows: list[dict[str, Any]] = []
    endpoints: dict[str, Any] = {}
    for path in paths:
        code = _code_from_path(path)
        if code == "RPUF30":
            continue
        formulation = CODE_TO_FORMULATION[code]
        conductivity, mean_temp, extraction = _thermal_endpoint(path)
        locator = f"{_relative(path)}#{extraction}"
        endpoints[formulation] = {
            "thermal_conductivity_w_mk": conductivity,
            "mean_temperature_c": mean_temp,
            "extraction": extraction,
        }
        gold_rows.append(
            _base_row(
                source_record_id=f"thermal-conductivity:{formulation}",
                observation_id=f"recycled-pu:{formulation}:thermal-conductivity",
                formulation_id=formulation,
                sample_id=f"thermal_plate_{formulation}",
                record_kind="scalar_measurement",
                property_name="thermal_conductivity",
                value=_text_number(conductivity),
                unit="W/(m*K)",
                condition_name="mean_plate_temperature",
                condition_value=_text_number(mean_temp),
                condition_unit="degC",
                data_origin="source_raw_workbook",
                reduction_level="measurement",
                method_or_test_protocol=(
                    "Fox 200 steady-state heat flux; 204x204x51 mm plate; "
                    "10 degC gradient; 80 degC/12 h conditioning"
                ),
                fidelity_level="direct_experimental_transfer_domain_foam",
                gold_admission_status="conditional_reference",
                mapping_status="formulation_resolved_thermal_plate_specimen_not_cross_mapped",
                protocol_status="source_workbook_endpoint_raw_replicate_mapping_incomplete",
                potential_weight_ceiling=0.25,
                source_locator=locator,
                file_sha256=_hash(path),
                notes=(
                    "RPUF3.0 工作簿与 RPUF0 单元格内容完全相同，前者被隔离；"
                    "其余端点保留原始工作簿定位。"
                ),
            )
        )
    if len(gold_rows) != EXPECTED_THERMAL_ENDPOINTS:
        raise AuditBlocked(f"导热端点数漂移：{len(gold_rows)}")
    return gold_rows, {
        "workbook_count": len(paths),
        "candidate_endpoint_count": len(gold_rows),
        "duplicate_conflict": {
            "excluded": "RPUF30_thermal conductivity.xlsx",
            "duplicates": "RPUF00_thermal conductivity.xlsx",
            "normalized_cell_content_sha256": digests[0],
        },
        "endpoints": endpoints,
    }


def _article_rows() -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    pdf_sha256 = _hash(PDF_PATH)
    component_count = density_count = compression_count = 0
    for formulation, payload in FORMULATION_DATA.items():
        failed = payload["density"] is None
        for component, value in payload["components"].items():
            component_count += 1
            rows.append(
                _base_row(
                    source_record_id=f"article-formulation:{formulation}",
                    observation_id=f"recycled-pu:{formulation}:component:{component}",
                    formulation_id=formulation,
                    sample_id=formulation,
                    record_kind="formulation_component",
                    component_name=component,
                    component_role=(
                        "recycled_filler" if component == "CDW_PU_foam_powder" else "source_declared_component"
                    ),
                    property_name="formulation_mass_fraction",
                    value=_text_number(value),
                    unit="wt%",
                    data_origin="published_article_table",
                    reduction_level="measurement",
                    method_or_test_protocol="Open Research Europe version 2, Table 1 or Table 2",
                    fidelity_level="published_formulation_context",
                    gold_admission_status="admitted_reference",
                    mapping_status="formulation_resolved_commercial_components_unresolved",
                    protocol_status="published_table_verified",
                    potential_weight_ceiling=0.30 if not failed else 0.15,
                    source_locator=f"{_relative(PDF_PATH)}#page=6;formulation={formulation};component={component}",
                    file_sha256=pdf_sha256,
                    notes=(
                        "该配方未形成完整可测试泡沫，只作失败配方参考。"
                        if failed
                        else "来源表格配方上下文；组分商品身份未完全公开。"
                    ),
                )
            )
        if payload["density"] is not None:
            density_count += 1
            rows.append(
                _base_row(
                    source_record_id=f"article-density:{formulation}",
                    observation_id=f"recycled-pu:{formulation}:apparent-density",
                    formulation_id=formulation,
                    sample_id=formulation,
                    record_kind="aggregate_scalar",
                    property_name="apparent_density",
                    value=_text_number(payload["density"]),
                    unit="kg/m^3",
                    data_origin="published_article_table",
                    reduction_level="aggregate",
                    method_or_test_protocol="mass divided by calibrated-calliper mean specimen volume",
                    fidelity_level="published_experimental_aggregate",
                    gold_admission_status="admitted_reference",
                    mapping_status="formulation_resolved_commercial_components_unresolved",
                    protocol_status="published_table_verified",
                    potential_weight_ceiling=0.40,
                    source_locator=f"{_relative(PDF_PATH)}#page=6;formulation={formulation};apparent_density",
                    file_sha256=pdf_sha256,
                    notes="表观密度为配方级汇总，不扩增为试样重复。",
                )
            )
    for formulation, values in ARTICLE_COMPRESSION_KPA.items():
        for strain_percent, value in zip((10, 25), values):
            compression_count += 1
            rows.append(
                _base_row(
                    source_record_id=f"article-compression:{formulation}",
                    observation_id=f"recycled-pu:{formulation}:article-stress-at-{strain_percent}pct",
                    formulation_id=formulation,
                    sample_id=formulation,
                    record_kind="aggregate_scalar",
                    property_name=f"compressive_strength_at_{strain_percent}_percent_deformation",
                    value=_text_number(value),
                    unit="kPa",
                    condition_name="compressive_strain",
                    condition_value=_text_number(strain_percent / 100),
                    condition_unit="dimensionless",
                    data_origin="published_article_table",
                    reduction_level="aggregate",
                    method_or_test_protocol="UNE-EN ISO 604; published Table 5 or Table 6",
                    fidelity_level="published_experimental_aggregate",
                    gold_admission_status="admitted_reference",
                    mapping_status="formulation_resolved_commercial_components_unresolved",
                    protocol_status="published_aggregate_reproduced_by_raw_curves",
                    potential_weight_ceiling=0.40,
                    source_locator=f"{_relative(PDF_PATH)}#page={8 if formulation.startswith('R') else 9};formulation={formulation};strain={strain_percent}%",
                    file_sha256=pdf_sha256,
                    notes="与逐试样原始曲线共享来源家族预算，不重复计独立样本。",
                )
            )
    counts = {
        "formulation_component_rows": component_count,
        "apparent_density_rows": density_count,
        "compression_aggregate_rows": compression_count,
    }
    expected = (
        EXPECTED_ARTICLE_COMPONENT_ROWS,
        EXPECTED_ARTICLE_DENSITY_ROWS,
        EXPECTED_ARTICLE_COMPRESSION_ROWS,
    )
    if tuple(counts.values()) != expected:
        raise AuditBlocked(f"论文表格物化数量漂移：{counts}")
    return rows, counts


def build_gold_e_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    verification = verify_source()
    compression_rows, compression_points, compression = _compression_source_rows()
    viscosity_rows, viscosity_points, viscosity = _viscosity_rows()
    thermal_rows, thermal = _thermal_rows()
    article_rows, article = _article_rows()
    rows = [*compression_rows, *viscosity_rows, *thermal_rows, *article_rows]
    if len(rows) != EXPECTED_GOLD_E_ROWS:
        raise AuditBlocked(f"Gold-E 行数漂移：{len(rows)}")
    if len({str(row["observation_id"]) for row in rows}) != len(rows):
        raise AuditBlocked("Gold-E observation_id 不唯一")
    admission_counts = Counter(str(row["gold_admission_status"]) for row in rows)
    if admission_counts != {
        "conditional_reference": 13_865,
        "admitted_reference": 66,
    }:
        raise AuditBlocked(f"Gold-E 准入计数漂移：{dict(admission_counts)}")
    summary = {
        "audit_version": "batch16-recycled-pu-foam-v2",
        "source": verification,
        "counts": {
            "formulation_count": len(FORMULATION_DATA),
            "foam_type_count": 2,
            "physical_specimen_file_count": EXPECTED_COMPRESSION_FILE_COUNT,
            "experimental_run_container_count": (
                EXPECTED_COMPRESSION_FILE_COUNT
                + EXPECTED_VISCOSITY_CURVES
                + EXPECTED_THERMAL_WORKBOOKS
            ),
            "curve_count_observed": EXPECTED_COMPRESSION_FILE_COUNT + EXPECTED_VISCOSITY_CURVES,
            "curve_count_candidate": EXPECTED_COMPRESSION_VALID_CURVES + EXPECTED_VISCOSITY_CURVES,
            "point_count_observed": EXPECTED_COMPRESSION_OBSERVED_POINTS + EXPECTED_VISCOSITY_POINTS,
            "point_count_candidate": EXPECTED_COMPRESSION_CANDIDATE_POINTS + EXPECTED_VISCOSITY_POINTS,
            "direct_scalar_count_observed": (
                EXPECTED_ARTICLE_COMPONENT_ROWS
                + EXPECTED_ARTICLE_DENSITY_ROWS
                + EXPECTED_ARTICLE_COMPRESSION_ROWS
                + EXPECTED_THERMAL_WORKBOOKS
            ),
            "direct_scalar_count_candidate": (
                EXPECTED_ARTICLE_COMPONENT_ROWS
                + EXPECTED_ARTICLE_DENSITY_ROWS
                + EXPECTED_ARTICLE_COMPRESSION_ROWS
                + EXPECTED_THERMAL_ENDPOINTS
            ),
            "valid_derived_scalar_count": EXPECTED_DERIVED_ENDPOINTS,
            "gold_e_numeric_row_count": len(rows),
            "source_identity_count_contribution": 1,
        },
        "admission_counts": dict(admission_counts),
        "compression": compression,
        "viscosity": viscosity,
        "thermal_conductivity": thermal,
        "article_tables": article,
        "training_state": {
            "current_weight_materialized": False,
            "model_ready_record_count": 0,
            "split_group_count": len({str(row["split_group"]) for row in rows}),
        },
        "_compression_points": compression_points,
        "_viscosity_points": viscosity_points,
    }
    return rows, summary


def _write_tsv(path: Path, rows: Iterable[dict[str, Any]], columns: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _write_outputs(rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    compression_points = summary.pop("_compression_points")
    viscosity_points = summary.pop("_viscosity_points")
    _write_tsv(OUTPUT_GOLD_E, rows, RECORD_COLUMNS)
    _write_tsv(
        OUTPUT_COMPRESSION,
        compression_points,
        (
            "formulation_id",
            "specimen_id",
            "point_index",
            "compressive_strain",
            "compressive_stress_mpa",
            "source_locator",
            "gold_admission_status",
        ),
    )
    _write_tsv(
        OUTPUT_VISCOSITY,
        viscosity_points,
        (
            "formulation_id",
            "point_index",
            "elapsed_time_min",
            "viscosity_pa_s",
            "source_locator",
            "gold_admission_status",
        ),
    )
    OUTPUT_AUDIT.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    inputs = [ARCHIVE_PATH, PDF_PATH, *(path for path in sorted(EXTRACT_ROOT.rglob("*")) if path.is_file())]
    _write_tsv(
        OUTPUT_CHECKSUMS,
        (
            {"path": _relative(path), "bytes": path.stat().st_size, "sha256": _hash(path)}
            for path in inputs
        ),
        ("path", "bytes", "sha256"),
    )
    counts = summary["counts"]
    OUTPUT_README.write_text(
        "# 第十六批：再生 PU 泡沫实验数据\n\n"
        f"- 数据集 DOI：`{DATASET_DOI}`（Zenodo，CC BY 4.0）\n"
        f"- 论文 DOI：`{ARTICLE_DOI}`\n"
        f"- 配方：{counts['formulation_count']} 个；原始压缩试样文件：{counts['physical_specimen_file_count']} 个。\n"
        f"- Gold-E 数值行：{counts['gold_e_numeric_row_count']:,}；其中压缩有效区间点 "
        f"{EXPECTED_COMPRESSION_CANDIDATE_POINTS:,}、黏度点 {EXPECTED_VISCOSITY_POINTS:,}。\n"
        "- RPUF0 第 7 条压缩曲线是西班牙语 13 列导出，已按真实压缩应变列解析；"
        "RPUF3.0 导热工作簿与 RPUF0 内容重复，未重复造数。\n"
        "- 所有点、端点和同研究配方共享一个 split_group；当前训练权重为空。\n",
        encoding="utf-8",
    )


def audit() -> dict[str, Any]:
    rows, summary = build_gold_e_rows()
    return {key: value for key, value in summary.items() if not key.startswith("_")}


def main() -> None:
    rows, summary = build_gold_e_rows()
    _write_outputs(rows, summary)


if __name__ == "__main__":
    main()
