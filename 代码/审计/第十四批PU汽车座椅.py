"""审计并物化 PU 多层汽车座椅的 IFD、松弛曲线与汇总标量。

来源由两个 Mendeley Data 固定版本组成：原始曲线 DOI
``10.17632/wtfkgk8k8r.2`` 与汇总标量 DOI
``10.17632/jr5fddz5yk.2``。二者服务于同一篇论文，必须共享论文家族和
材料体系分组；汇总标量不能被当作独立实验再次计数。

本模块保留原始测量顺序，不做训练/验证划分，不物化训练权重。IFD 原件
缺少显式列名，横轴 mm、纵轴 kgf 由加载/卸载轨迹及汇总表中的 kgf→N
关系交叉确认，因此曲线保持条件参考。应力松弛原件纵轴为负载传感器电压
信号，缺少力值校准，不能冒充绝对应力；时间—电压曲线仍可用于松弛形状
学习。汇总表中交联密度未声明单位，单独保持条件参考。
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = (
    PROJECT_ROOT
    / "数据/原始"
    / "外部数据"
    / "新增开放数据"
    / "第十四批实验_PU汽车座椅"
)

RAW_METADATA_PATH = SOURCE_DIR / "Mendeley_原始数据集_v2_官方元数据.json"
PROCESSED_METADATA_PATH = SOURCE_DIR / "Mendeley_汇总数据集_v2_官方元数据.json"
RAW_DATACITE_PATH = SOURCE_DIR / "DataCite_原始数据集_v2_元数据.json"
PROCESSED_DATACITE_PATH = SOURCE_DIR / "DataCite_汇总数据集_v2_元数据.json"
CROSSREF_PATH = SOURCE_DIR / "Crossref_论文元数据.json"
PAPER_PREVIEW_PATH = SOURCE_DIR / "论文官方预览.pdf"
SUMMARY_WORKBOOK_PATH = SOURCE_DIR / "Hardness, IHF, MIF, SF, H.L., and S.R..xlsx"

OUTPUT_TSV = SOURCE_DIR / "Gold_E_实验观测长表.tsv"
OUTPUT_AUDIT = SOURCE_DIR / "内容审计摘要.json"
OUTPUT_CHECKSUMS = SOURCE_DIR / "文件校验清单.tsv"
OUTPUT_README = SOURCE_DIR / "来源说明.md"

AUDIT_VERSION = "batch14-pu-multilayer-seat-v1"
SOURCE_DIRECTORY = SOURCE_DIR.name
RAW_SOURCE_ID = "source_mendeley_pu_seat_raw_v2"
PROCESSED_SOURCE_ID = "source_mendeley_pu_seat_processed_v2"
SOURCE_FAMILY_KEY = "family_moon_2020_pu_multilayer_seat"
RAW_DOI = "10.17632/wtfkgk8k8r.2"
PROCESSED_DOI = "10.17632/jr5fddz5yk.2"
PAPER_DOI = "10.1007/s12239-020-0102-z"
LICENSE = "CC BY 4.0"
RAW_CITATIONS = "oh-2019-pu-seat-raw-v2;moon-2020-pu-multilayer-seat"
SUMMARY_CITATIONS = (
    "oh-2019-pu-seat-raw-v2;oh-2019-pu-seat-processed-v2;"
    "moon-2020-pu-multilayer-seat"
)

EXPECTED_IFD_POINTS = 6_320
EXPECTED_RELAXATION_POINTS = 67_276
EXPECTED_CURVE_POINTS = 73_596
EXPECTED_SUMMARY_SCALARS = 59
EXPECTED_TOTAL_ROWS = 73_655
EXPECTED_ADMITTED_ROWS = 54
EXPECTED_CONDITIONAL_ROWS = 73_601

RECORD_COLUMNS = (
    "source_directory",
    "source_record_id",
    "observation_id",
    "formulation_id",
    "sample_id",
    "record_kind",
    "component_name",
    "component_role",
    "property_name",
    "value",
    "unit",
    "uncertainty_value",
    "uncertainty_type",
    "condition_name",
    "condition_value",
    "condition_unit",
    "target_origin",
    "data_origin",
    "reduction_level",
    "method_or_test_protocol",
    "fidelity_level",
    "gold_admission_status",
    "mapping_status",
    "protocol_status",
    "potential_weight_ceiling",
    "current_weight_materialized",
    "training_weight",
    "split_group",
    "source_locator",
    "file_sha256",
    "license",
    "citation_keys",
    "notes",
)

_CELL_RE = re.compile(r"^(?P<column>[A-Z]+)(?P<row>[1-9]\d*)$")
_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


class AuditBlocked(RuntimeError):
    """冻结输入、结构、数量、单位证据或去重约束发生漂移。"""


@dataclass(frozen=True)
class FrozenFile:
    filename: str
    size: int
    sha256: str

    @property
    def path(self) -> Path:
        return SOURCE_DIR / self.filename


@dataclass(frozen=True)
class SystemSpec:
    key: str
    source_label: str
    system_identity: str
    identity_status: str

    @property
    def formulation_id(self) -> str:
        return f"pu_seat_{self.key}"

    @property
    def split_group(self) -> str:
        return f"{SOURCE_FAMILY_KEY}|system={self.key}"


@dataclass(frozen=True)
class CurveSpec:
    filename: str
    system_key: str
    curve_kind: str
    points: int
    size: int
    sha256: str

    @property
    def path(self) -> Path:
        return SOURCE_DIR / self.filename


SYSTEMS: dict[str, SystemSpec] = {
    "soft_puf": SystemSpec(
        "soft_puf",
        "Soft",
        "single-layer soft polyurethane foam",
        "source_material_class_resolved_grade_and_recipe_undisclosed",
    ),
    "mid_puf": SystemSpec(
        "mid_puf",
        "Mid",
        "single-layer medium-hardness polyurethane foam",
        "source_material_class_resolved_grade_and_recipe_undisclosed",
    ),
    "hard_puf": SystemSpec(
        "hard_puf",
        "Hard",
        "single-layer hard polyurethane foam",
        "source_material_class_resolved_grade_and_recipe_undisclosed",
    ),
    "memory_foam": SystemSpec(
        "memory_foam",
        "Memory foam",
        "single-layer commercial PU-based memory foam",
        "source_material_class_resolved_grade_and_recipe_undisclosed",
    ),
    "technogel": SystemSpec(
        "technogel",
        "Technogel",
        "single-layer commercial PU-based technogel",
        "source_material_class_resolved_grade_and_recipe_undisclosed",
    ),
    "multilayer_type_a": SystemSpec(
        "multilayer_type_a",
        "Type A",
        "multilayer PU seat cushion, source label Type A",
        "multilayer_source_label_only_layer_mapping_not_in_deposit",
    ),
    "multilayer_type_b": SystemSpec(
        "multilayer_type_b",
        "Type B",
        "multilayer PU seat cushion, source label Type B",
        "multilayer_source_label_only_layer_mapping_not_in_deposit",
    ),
    "multilayer_type_c": SystemSpec(
        "multilayer_type_c",
        "Type C",
        "multilayer PU seat cushion, source label Type C",
        "multilayer_source_label_only_layer_mapping_not_in_deposit",
    ),
    "multilayer_type_d": SystemSpec(
        "multilayer_type_d",
        "Type D",
        "multilayer PU seat cushion, source label Type D",
        "multilayer_source_label_only_layer_mapping_not_in_deposit",
    ),
}

LABEL_TO_SYSTEM = {spec.source_label: key for key, spec in SYSTEMS.items()}

CURVE_SPECS = (
    CurveSpec("soft only20180909003.dat", "soft_puf", "ifd", 652, 23_243, "4b9ae57153d408097aa7aa2f0a5fb7db900fd35743d526b79c4ea4852cdcc764"),
    CurveSpec("mid only20180909006.dat", "mid_puf", "ifd", 649, 23_234, "a621681c4309ce0fc40c94854ef94b439d0920e3063c149d2197d74739c844b5"),
    CurveSpec("hard only20180909003.dat", "hard_puf", "ifd", 653, 23_300, "dafceebf6660a4cb6589b24a3d9bfbbb1c22a3713539268716afb5a99fd50876"),
    CurveSpec("memory foam20180913003.dat", "memory_foam", "ifd", 654, 23_388, "488c43c4fbe00047c558aed69fed51f8437c5a3142f0801871fb793fa09fd6cb"),
    CurveSpec("technogel20180918001.txt", "technogel", "ifd", 558, 20_043, "739cc336346adf97da4c04f87349f2865075a672f33c36a9824ba6dc1b4c0c85"),
    CurveSpec("Type A20180909009.dat", "multilayer_type_a", "ifd", 810, 28_952, "41e9dbacbe7cd0d93542044181f3e13602a8376d0e99586f0e614676c5bf5409"),
    CurveSpec("Type B20180909003.dat", "multilayer_type_b", "ifd", 794, 28_290, "f2868d9e3e0eeed1569179ee8bbe9d5c0cea3e1aeab10240f541556dc16ac5b0"),
    CurveSpec("Type C20180909006.dat", "multilayer_type_c", "ifd", 766, 27_360, "477242e13d32c02c71f2e24454fccb35b050a816bb01724e6b4ae968856f29c4"),
    CurveSpec("Type D20180909006.dat", "multilayer_type_d", "ifd", 784, 28_000, "cb13e07206d4d8f99ddc100536e1d356b0a94d0be00856a0a87f133fda3d2bf4"),
    CurveSpec("Soft55kgf20181112001.dat", "soft_puf", "stress_relaxation", 7_522, 193_495, "e24ada261c62a1cb1c4d503a05817f59c6609d1d1322359dd0ca0ac2d60bd47d"),
    CurveSpec("Mid55kgf20181113001.dat", "mid_puf", "stress_relaxation", 7_492, 192_719, "9e0d5ab104fae72ad97ec917d4eb2480d37a8c38f5b48e2a427bafacba36cc22"),
    CurveSpec("Hard55kgf20181112002.dat", "hard_puf", "stress_relaxation", 7_468, 192_101, "10d28df5e554a8769b5e84762a7a0219686bfedef39655df951d61c3f713320a"),
    CurveSpec("Memory55kgf20181112001.dat", "memory_foam", "stress_relaxation", 7_469, 184_550, "46cd23d482f22e0d81ca2c067fc867e29ec2b1d2a9be1020caaaf1a610be7693"),
    CurveSpec("Technogel55kgf20181115001.dat", "technogel", "stress_relaxation", 7_379, 174_461, "7a1c5197fec58fe69337ddb3f5018a0dcf43999ce0f2e8a0395f92d6bc5e1f6d"),
    CurveSpec("TypeA55kgf20181113002.dat", "multilayer_type_a", "stress_relaxation", 7_484, 192_531, "0873b083d89fa27f6d5c8d1c2f9a133405b5b9fc1c2421c7c662f731b4204da2"),
    CurveSpec("TypeB55kgf20181114001.dat", "multilayer_type_b", "stress_relaxation", 7_505, 192_407, "41c9c78c9733ca654820f3aaeee78763690e13c243c15db6a1ce0b22fddd6648"),
    CurveSpec("TypeC55kgf20181114001.dat", "multilayer_type_c", "stress_relaxation", 7_466, 191_946, "2c03eefd6530fd5cbd0afde75f5dd75f71f292bf1c4cce918f8d1a60a57cbb62"),
    CurveSpec("TypeD55kgf20181115001.dat", "multilayer_type_d", "stress_relaxation", 7_491, 198_101, "0b7ec5c97f5defb239843c661f72d2c2cb81bcf4ddc11cc878b534758f939c8b"),
)

SUPPORT_FILES = (
    FrozenFile("Hardness, IHF, MIF, SF, H.L., and S.R..xlsx", 9_950, "e9d0bdbddefff3eda582655c2b53f704c3b59c6d788bd13b80bd16c1ec260fd7"),
    FrozenFile("Mendeley_原始数据集_v2_官方元数据.json", 14_959, "edd28f95abc6ed347adb91662644e3735b2b5e2213fd2766d467b9a639ff6969"),
    FrozenFile("Mendeley_汇总数据集_v2_官方元数据.json", 2_704, "48695c3509e090d13957e37050b5b00ce4c8643a849b3de99309af10a80e6042"),
    FrozenFile("DataCite_原始数据集_v2_元数据.json", 4_057, "892cde54f3206021953f197ab89fcfbf52e1435b37e5ec08a74ebae2c17c6242"),
    FrozenFile("DataCite_汇总数据集_v2_元数据.json", 4_098, "f808ae29289ada01766134191cf4d22b17287da297103eb9b042a55c8ed31ee1"),
    FrozenFile("Crossref_论文元数据.json", 15_211, "8d3588f04033846b072a96d564a96e030f2c4f3b35d7ea67616967d01280ce30"),
    FrozenFile("论文官方预览.pdf", 78_295, "56c711ef392b29d79945820f8519dd438d0408e469e8d7a3e86392b842db0d21"),
)


SUMMARY_METRICS = {
    "IHF": {
        "property_name": "initial_hardness_factor",
        "unit": "dimensionless",
        "record_kind": "comfort_metric",
        "data_origin": "experimental_processed_summary_from_ifd",
        "mapping_status": "source_metric_name;dimensionless_by_definition",
        "ceiling": "0.50",
    },
    "MIF": {
        "property_name": "modulus_irregularity_factor",
        "unit": "%",
        "record_kind": "comfort_metric",
        "data_origin": "experimental_processed_summary_from_ifd",
        "mapping_status": "source_metric_name;percent_unit_inferred_from_metric_definition",
        "ceiling": "0.45",
    },
    "SF": {
        "property_name": "sag_factor",
        "unit": "dimensionless",
        "record_kind": "comfort_metric",
        "data_origin": "experimental_processed_summary_from_ifd",
        "mapping_status": "source_metric_name;dimensionless_by_definition",
        "ceiling": "0.50",
    },
    "Hysters loss": {
        "property_name": "hysteresis_loss",
        "unit": "%",
        "record_kind": "comfort_metric",
        "data_origin": "experimental_processed_summary_from_ifd",
        "mapping_status": "source_metric_name;percent_unit_inferred_from_metric_definition",
        "ceiling": "0.50",
    },
    "Hardness": {
        "property_name": "indentation_hardness",
        "unit": "N",
        "record_kind": "mechanical_property",
        "data_origin": "experimental_processed_summary_from_ifd",
        "mapping_status": "newton_unit_inferred_and_crosschecked_against_source_kgf_header_times_9.8",
        "ceiling": "0.55",
    },
    "Stress relaxation": {
        "property_name": "stress_relaxation_fractional_loss",
        "unit": "%",
        "record_kind": "viscoelastic_property",
        "data_origin": "experimental_processed_summary_from_stress_relaxation",
        "mapping_status": "source_metric_name;percent_unit_crosschecked_against_peak_to_terminal_signal_drop",
        "ceiling": "0.50",
    },
    "Crosslink density": {
        "property_name": "crosslink_density",
        "unit": "source_native_unit_unresolved",
        "record_kind": "network_metric",
        "data_origin": "experimental_processed_crosslink_density",
        "mapping_status": "source_metric_name_resolved;unit_not_declared_in_workbook_or_public_preview",
        "ceiling": "0.20",
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditBlocked(f"JSON 无法读取：{path}") from exc
    if not isinstance(payload, dict):
        raise AuditBlocked(f"JSON 顶层不是对象：{path}")
    return payload


def _finite_text(value: str, context: str) -> float:
    try:
        number = float(value.strip())
    except (AttributeError, ValueError) as exc:
        raise AuditBlocked(f"{context} 不是数值：{value!r}") from exc
    if not math.isfinite(number):
        raise AuditBlocked(f"{context} 不是有限数值：{value!r}")
    return number


def _normal_number_text(value: str) -> str:
    number = _finite_text(value, "workbook scalar")
    return format(number, ".15g")


def _stable_observation_id(source_record_id: str) -> str:
    token = hashlib.sha256(source_record_id.encode("utf-8")).hexdigest()[:24]
    return f"obs_pu_seat_{token}"


def _verify_frozen_files() -> list[dict[str, Any]]:
    identities: list[dict[str, Any]] = []
    for spec in (*CURVE_SPECS, *SUPPORT_FILES):
        path = SOURCE_DIR / spec.filename
        if not path.is_file():
            raise AuditBlocked(f"缺少冻结输入：{path}")
        actual_size = path.stat().st_size
        actual_hash = _sha256(path)
        if actual_size != spec.size or actual_hash != spec.sha256:
            raise AuditBlocked(
                f"冻结输入身份漂移：{path.name}; "
                f"expected=({spec.size},{spec.sha256}), "
                f"actual=({actual_size},{actual_hash})"
            )
        identities.append(
            {
                "filename": spec.filename,
                "bytes": spec.size,
                "sha256": spec.sha256,
                "role": "raw_curve" if isinstance(spec, CurveSpec) else "supporting_evidence",
            }
        )
    return identities


def _official_file_map(payload: dict[str, Any]) -> dict[str, tuple[int, str]]:
    result: dict[str, tuple[int, str]] = {}
    for entry in payload.get("files", []):
        if not isinstance(entry, dict):
            raise AuditBlocked("Mendeley files 条目不是对象")
        content = entry.get("content_details")
        if not isinstance(content, dict):
            raise AuditBlocked("Mendeley content_details 缺失")
        filename = str(entry.get("filename", ""))
        result[filename] = (int(content.get("size", -1)), str(content.get("sha256_hash", "")))
    return result


def _verify_metadata() -> dict[str, Any]:
    raw = _read_json(RAW_METADATA_PATH)
    processed = _read_json(PROCESSED_METADATA_PATH)
    raw_datacite = _read_json(RAW_DATACITE_PATH)
    processed_datacite = _read_json(PROCESSED_DATACITE_PATH)
    crossref = _read_json(CROSSREF_PATH)

    def verify_mendeley(payload: dict[str, Any], doi: str, version: int) -> None:
        doi_obj = payload.get("doi")
        if not isinstance(doi_obj, dict) or doi_obj.get("id") != doi:
            raise AuditBlocked(f"Mendeley DOI 漂移：{doi}")
        if payload.get("version") != version or not payload.get("available"):
            raise AuditBlocked(f"Mendeley 固定版本不可用：{doi}")
        licence = payload.get("data_licence")
        if not isinstance(licence, dict) or licence.get("short_name") != LICENSE:
            raise AuditBlocked(f"Mendeley 许可证不是 {LICENSE}：{doi}")

    verify_mendeley(raw, RAW_DOI, 2)
    verify_mendeley(processed, PROCESSED_DOI, 2)

    expected_raw_files = {
        spec.filename: (spec.size, spec.sha256) for spec in CURVE_SPECS
    }
    if _official_file_map(raw) != expected_raw_files:
        raise AuditBlocked("原始数据集文件清单与官方 v2 元数据不一致")
    expected_processed_files = {
        SUMMARY_WORKBOOK_PATH.name: (
            SUMMARY_WORKBOOK_PATH.stat().st_size,
            _sha256(SUMMARY_WORKBOOK_PATH),
        )
    }
    if _official_file_map(processed) != expected_processed_files:
        raise AuditBlocked("汇总数据集文件清单与官方 v2 元数据不一致")

    for payload, doi in ((raw_datacite, RAW_DOI), (processed_datacite, PROCESSED_DOI)):
        data = payload.get("data")
        if not isinstance(data, dict) or data.get("id") != doi:
            raise AuditBlocked(f"DataCite DOI 漂移：{doi}")
        attributes = data.get("attributes")
        rights = attributes.get("rightsList", []) if isinstance(attributes, dict) else []
        if not any(
            isinstance(item, dict) and item.get("rightsIdentifier") == "cc-by-4.0"
            for item in rights
        ):
            raise AuditBlocked(f"DataCite 未确认 CC BY 4.0：{doi}")

    message = crossref.get("message")
    if crossref.get("status") != "ok" or not isinstance(message, dict):
        raise AuditBlocked("Crossref 元数据无效")
    if str(message.get("DOI", "")).lower() != PAPER_DOI:
        raise AuditBlocked("论文 DOI 漂移")
    titles = message.get("title", [])
    if titles != ["Study on Seating Comfort of Polyurethane Multilayer Seat Cushions"]:
        raise AuditBlocked("论文标题漂移")
    families = [
        str(author.get("family", ""))
        for author in message.get("author", [])
        if isinstance(author, dict)
    ]
    if families != ["Moon", "Sinha", "Kwak", "Ha", "Oh"]:
        raise AuditBlocked("论文作者顺序漂移")
    if PAPER_PREVIEW_PATH.read_bytes()[:5] != b"%PDF-":
        raise AuditBlocked("论文官方预览不是 PDF")

    return {
        "raw_dataset": {
            "source_id": RAW_SOURCE_ID,
            "doi": RAW_DOI,
            "version": 2,
            "published": raw.get("publish_date"),
            "file_count": len(expected_raw_files),
            "bytes": sum(size for size, _ in expected_raw_files.values()),
            "license": LICENSE,
        },
        "processed_dataset": {
            "source_id": PROCESSED_SOURCE_ID,
            "doi": PROCESSED_DOI,
            "version": 2,
            "published": processed.get("publish_date"),
            "file_count": len(expected_processed_files),
            "bytes": sum(size for size, _ in expected_processed_files.values()),
            "license": LICENSE,
        },
        "publication": {
            "doi": PAPER_DOI,
            "title": titles[0],
            "authors": families,
            "journal": message.get("container-title", [""])[0],
            "volume": message.get("volume"),
            "issue": message.get("issue"),
            "pages": message.get("page"),
            "official_preview_pages": 1,
        },
    }


def _read_curve(spec: CurveSpec) -> dict[str, Any]:
    try:
        lines = spec.path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, UnicodeError) as exc:
        raise AuditBlocked(f"曲线文件无法读取：{spec.path}") from exc
    if len(lines) < 20:
        raise AuditBlocked(f"曲线文件过短：{spec.path.name}")
    try:
        points = int(lines[18].strip())
    except ValueError as exc:
        raise AuditBlocked(f"第 19 行点数无效：{spec.path.name}") from exc
    if points != spec.points or len(lines) != 19 + 2 * points:
        raise AuditBlocked(
            f"曲线数组长度漂移：{spec.path.name}; "
            f"expected_points={spec.points}, actual_points={points}, lines={len(lines)}"
        )
    x_text = [item.strip() for item in lines[19 : 19 + points]]
    y_text = [item.strip() for item in lines[19 + points : 19 + 2 * points]]
    x = [_finite_text(item, f"{spec.filename}/x") for item in x_text]
    y = [_finite_text(item, f"{spec.filename}/y") for item in y_text]
    if spec.curve_kind == "ifd":
        peak_index = max(range(points), key=x.__getitem__)
        if peak_index in {0, points - 1}:
            raise AuditBlocked(f"IFD 曲线缺少加载/卸载反转：{spec.filename}")
        if not all(x[i + 1] >= x[i] for i in range(peak_index)):
            raise AuditBlocked(f"IFD 加载段位移不是单调非降：{spec.filename}")
        if not all(x[i + 1] <= x[i] for i in range(peak_index, points - 1)):
            raise AuditBlocked(f"IFD 卸载段位移不是单调非升：{spec.filename}")
        condition_name, condition_unit = "indentation_displacement", "mm"
        property_name, unit = "indentation_force", "kgf"
    elif spec.curve_kind == "stress_relaxation":
        if not all(x[i + 1] > x[i] for i in range(points - 1)):
            raise AuditBlocked(f"松弛时间轴不是严格递增：{spec.filename}")
        peak_index = max(range(points), key=y.__getitem__)
        condition_name, condition_unit = "elapsed_time", "s"
        property_name, unit = "load_cell_signal_voltage", "V"
    else:
        raise AuditBlocked(f"未知曲线类型：{spec.curve_kind}")
    if not lines[14].strip() or not lines[15].strip():
        raise AuditBlocked(f"曲线采集时间或试验序号缺失：{spec.filename}")
    return {
        "spec": spec,
        "header": lines[:19],
        "acquired_at": lines[14].strip(),
        "source_run_index": lines[15].strip(),
        "x_text": x_text,
        "y_text": y_text,
        "x": x,
        "y": y,
        "peak_index": peak_index,
        "condition_name": condition_name,
        "condition_unit": condition_unit,
        "property_name": property_name,
        "unit": unit,
    }


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except (KeyError, ET.ParseError) as exc:
        raise AuditBlocked("汇总工作簿 sharedStrings.xml 无法读取") from exc
    strings: list[str] = []
    for item in root.findall(f"{_XLSX_NS}si"):
        strings.append("".join(node.text or "" for node in item.iter(f"{_XLSX_NS}t")))
    return strings


def _read_summary_workbook() -> tuple[list[str], list[dict[str, str]]]:
    try:
        archive = zipfile.ZipFile(SUMMARY_WORKBOOK_PATH)
    except (FileNotFoundError, zipfile.BadZipFile) as exc:
        raise AuditBlocked("汇总工作簿不是有效 XLSX") from exc
    with archive:
        members = archive.namelist()
        if any(
            name.startswith(("/", "\\")) or ".." in Path(name).parts
            for name in members
        ):
            raise AuditBlocked("汇总工作簿包含不安全成员路径")
        shared = _xlsx_shared_strings(archive)
        try:
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        except (KeyError, ET.ParseError) as exc:
            raise AuditBlocked("汇总工作簿 Sheet1 无法读取") from exc

    cells: dict[str, str] = {}
    formulas: list[str] = []
    for cell in sheet.iter(f"{_XLSX_NS}c"):
        coordinate = str(cell.get("r", ""))
        if not _CELL_RE.match(coordinate):
            raise AuditBlocked(f"非法 XLSX 单元格坐标：{coordinate}")
        formula = cell.find(f"{_XLSX_NS}f")
        if formula is not None:
            formulas.append(coordinate)
        value_node = cell.find(f"{_XLSX_NS}v")
        if value_node is None or value_node.text is None:
            cells[coordinate] = ""
            continue
        raw = value_node.text
        if cell.get("t") == "s":
            try:
                cells[coordinate] = shared[int(raw)]
            except (ValueError, IndexError) as exc:
                raise AuditBlocked(f"共享字符串索引无效：{coordinate}") from exc
        else:
            cells[coordinate] = raw
    if formulas:
        raise AuditBlocked(f"汇总工作簿不应包含公式：{formulas}")

    headers = [cells.get(f"{column}4", "") for column in "BCDEFGH"]
    if headers != list(SUMMARY_METRICS):
        raise AuditBlocked(f"汇总表字段漂移：{headers}")
    rows: list[dict[str, str]] = []
    for row_number in range(5, 14):
        label = cells.get(f"A{row_number}", "")
        if label not in LABEL_TO_SYSTEM:
            raise AuditBlocked(f"汇总表体系标签漂移：A{row_number}={label!r}")
        row = {"source_label": label, "source_row": str(row_number)}
        for column, header in zip("BCDEFGH", headers, strict=True):
            value = cells.get(f"{column}{row_number}", "")
            if value:
                _finite_text(value, f"Sheet1!{column}{row_number}")
            row[header] = value
            row[f"{header}__cell"] = f"{column}{row_number}"
        rows.append(row)
    if len(rows) != 9:
        raise AuditBlocked("汇总表体系数不是 9")
    return headers, rows


def _base_record(
    *,
    source_record_id: str,
    system: SystemSpec,
    record_kind: str,
    property_name: str,
    value: str,
    unit: str,
    condition_name: str,
    condition_value: str,
    condition_unit: str,
    data_origin: str,
    reduction_level: str,
    method_or_test_protocol: str,
    fidelity_level: str,
    gold_admission_status: str,
    mapping_status: str,
    protocol_status: str,
    potential_weight_ceiling: str,
    source_locator: str,
    file_sha256: str,
    citation_keys: str,
    notes: str,
) -> dict[str, str]:
    if gold_admission_status not in {"admitted_reference", "conditional_reference"}:
        raise AuditBlocked(f"非法准入状态：{gold_admission_status}")
    row = {
        "source_directory": SOURCE_DIRECTORY,
        "source_record_id": source_record_id,
        "observation_id": _stable_observation_id(source_record_id),
        "formulation_id": system.formulation_id,
        "sample_id": system.source_label,
        "record_kind": record_kind,
        "component_name": "",
        "component_role": "",
        "property_name": property_name,
        "value": value,
        "unit": unit,
        "uncertainty_value": "",
        "uncertainty_type": "",
        "condition_name": condition_name,
        "condition_value": condition_value,
        "condition_unit": condition_unit,
        "target_origin": "experimental",
        "data_origin": data_origin,
        "reduction_level": reduction_level,
        "method_or_test_protocol": method_or_test_protocol,
        "fidelity_level": fidelity_level,
        "gold_admission_status": gold_admission_status,
        "mapping_status": mapping_status,
        "protocol_status": protocol_status,
        "potential_weight_ceiling": potential_weight_ceiling,
        "current_weight_materialized": "false",
        "training_weight": "",
        "split_group": system.split_group,
        "source_locator": source_locator,
        "file_sha256": file_sha256,
        "license": LICENSE,
        "citation_keys": citation_keys,
        "notes": notes,
    }
    if tuple(row) != RECORD_COLUMNS:
        raise AuditBlocked("Gold-E 字段顺序与统一契约不一致")
    return row


def _curve_rows(curve: dict[str, Any]) -> list[dict[str, str]]:
    spec: CurveSpec = curve["spec"]
    system = SYSTEMS[spec.system_key]
    is_ifd = spec.curve_kind == "ifd"
    if is_ifd:
        method = "source-native indentation force deflection loading/unloading trace"
        mapping = "displacement_mm_and_force_kgf_inferred_with_processed_workbook_crosscheck"
        fidelity = "experimental_source_native_ifd_curve_unit_inferred"
        ceiling = "0.35"
        notes = (
            f"{system.system_identity}; {system.identity_status}; raw sequence retained; "
            "1 kgf to 9.8 N relation is visible in the processed hardness table; "
            "full test protocol is absent from the open deposit."
        )
    else:
        method = "source-native approximately two-hour stress-relaxation acquisition"
        mapping = "elapsed_time_s_and_load_cell_voltage_signal_inferred_from_trace_and_header"
        fidelity = "experimental_source_native_relaxation_signal_uncalibrated"
        ceiling = "0.25"
        notes = (
            f"{system.system_identity}; {system.identity_status}; raw sequence retained; "
            "voltage is a load-cell signal and no force calibration is deposited, so it "
            "must not be treated as absolute force or stress."
        )
    rows: list[dict[str, str]] = []
    n = spec.points
    for index, (condition, value) in enumerate(
        zip(curve["x_text"], curve["y_text"], strict=True)
    ):
        source_record_id = (
            f"pu_seat|system={system.key}|curve={spec.curve_kind}|point={index:06d}"
        )
        x_line = 20 + index
        y_line = 20 + n + index
        rows.append(
            _base_record(
                source_record_id=source_record_id,
                system=system,
                record_kind="mechanical_curve_point",
                property_name=curve["property_name"],
                value=value,
                unit=curve["unit"],
                condition_name=curve["condition_name"],
                condition_value=condition,
                condition_unit=curve["condition_unit"],
                data_origin="experimental_raw_curve",
                reduction_level="source_native_curve_point",
                method_or_test_protocol=method,
                fidelity_level=fidelity,
                gold_admission_status="conditional_reference",
                mapping_status=mapping,
                protocol_status="partial_protocol_public_preview_only",
                potential_weight_ceiling=ceiling,
                source_locator=(
                    f"{SOURCE_DIRECTORY}/{spec.filename}|x_line={x_line}|"
                    f"y_line={y_line}|point_index={index}"
                ),
                file_sha256=spec.sha256,
                citation_keys=RAW_CITATIONS,
                notes=notes,
            )
        )
    return rows


def _summary_rows(summary_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    workbook_hash = _sha256(SUMMARY_WORKBOOK_PATH)
    records: list[dict[str, str]] = []
    for source in summary_rows:
        system = SYSTEMS[LABEL_TO_SYSTEM[source["source_label"]]]
        for source_header, policy in SUMMARY_METRICS.items():
            raw_value = source[source_header]
            if not raw_value:
                continue
            is_crosslink = source_header == "Crosslink density"
            source_record_id = (
                f"pu_seat|system={system.key}|summary={policy['property_name']}"
            )
            records.append(
                _base_record(
                    source_record_id=source_record_id,
                    system=system,
                    record_kind=str(policy["record_kind"]),
                    property_name=str(policy["property_name"]),
                    value=_normal_number_text(raw_value),
                    unit=str(policy["unit"]),
                    condition_name="",
                    condition_value="",
                    condition_unit="",
                    data_origin=str(policy["data_origin"]),
                    reduction_level="source_processed_scalar",
                    method_or_test_protocol=(
                        "source processed workbook; paper abstract identifies the IFD and "
                        "stress-relaxation metrics; full methods are not in the open preview"
                    ),
                    fidelity_level="published_processed_experimental_summary",
                    gold_admission_status=(
                        "conditional_reference" if is_crosslink else "admitted_reference"
                    ),
                    mapping_status=str(policy["mapping_status"]),
                    protocol_status="partial_protocol_public_preview_only",
                    potential_weight_ceiling=str(policy["ceiling"]),
                    source_locator=(
                        f"{SOURCE_DIRECTORY}/{SUMMARY_WORKBOOK_PATH.name}|"
                        f"Sheet1!{source[source_header + '__cell']}"
                    ),
                    file_sha256=workbook_hash,
                    citation_keys=SUMMARY_CITATIONS,
                    notes=(
                        f"{system.system_identity}; {system.identity_status}; processed scalar "
                        "shares the same system split group as both raw curves and is not an "
                        "independent experiment."
                    ),
                )
            )
    return records


def build_gold_e_rows() -> list[dict[str, str]]:
    _verify_frozen_files()
    _verify_metadata()
    curves = [_read_curve(spec) for spec in CURVE_SPECS]
    _, workbook_rows = _read_summary_workbook()
    rows = [row for curve in curves for row in _curve_rows(curve)]
    rows.extend(_summary_rows(workbook_rows))
    if len(rows) != EXPECTED_TOTAL_ROWS:
        raise AuditBlocked(
            f"Gold-E 行数漂移：expected={EXPECTED_TOTAL_ROWS}, actual={len(rows)}"
        )
    source_ids = [row["source_record_id"] for row in rows]
    observation_ids = [row["observation_id"] for row in rows]
    if len(source_ids) != len(set(source_ids)):
        raise AuditBlocked("source_record_id 不唯一")
    if len(observation_ids) != len(set(observation_ids)):
        raise AuditBlocked("observation_id 不唯一")
    if {row["split_group"] for row in rows} != {
        system.split_group for system in SYSTEMS.values()
    }:
        raise AuditBlocked("体系级 split_group 漂移")
    return rows


def _raw_processed_crosschecks(
    curves: list[dict[str, Any]], summary_rows: list[dict[str, str]]
) -> dict[str, Any]:
    by_system_kind = {
        (curve["spec"].system_key, curve["spec"].curve_kind): curve
        for curve in curves
    }
    exact_hardness_matches = 0
    hardness_within_one_newton = 0
    stress_differences: dict[str, float] = {}
    for source in summary_rows:
        system_key = LABEL_TO_SYSTEM[source["source_label"]]
        ifd = by_system_kind[(system_key, "ifd")]
        header_numbers = [
            _finite_text(item, f"{system_key}/header")
            for item in ifd["header"][4:13]
            if item.strip()
        ]
        hardness_n = _finite_text(source["Hardness"], f"{system_key}/hardness")
        best_delta = min(abs(value * 9.8 - hardness_n) for value in header_numbers)
        if best_delta < 1e-9:
            exact_hardness_matches += 1
        if best_delta <= 1.0:
            hardness_within_one_newton += 1

        relaxation = by_system_kind[(system_key, "stress_relaxation")]
        peak = max(relaxation["y"])
        terminal = relaxation["y"][-1]
        derived_loss = (peak - terminal) / peak * 100.0
        published_loss = _finite_text(
            source["Stress relaxation"], f"{system_key}/stress relaxation"
        )
        stress_differences[system_key] = abs(derived_loss - published_loss)

    if exact_hardness_matches < 6 or hardness_within_one_newton < 7:
        raise AuditBlocked("IFD kgf→N 单位交叉验证不足")
    if max(stress_differences.values()) > 5.0:
        raise AuditBlocked("松弛曲线与汇总百分比的家族交叉验证失败")
    return {
        "hardness_exact_kgf_times_9_8_header_matches": exact_hardness_matches,
        "hardness_within_1_newton_header_matches": hardness_within_one_newton,
        "stress_relaxation_peak_to_terminal_max_abs_difference_percentage_points": max(
            stress_differences.values()
        ),
        "stress_relaxation_abs_differences_percentage_points": dict(
            sorted(stress_differences.items())
        ),
    }


def audit() -> dict[str, Any]:
    identities = _verify_frozen_files()
    metadata = _verify_metadata()
    curves = [_read_curve(spec) for spec in CURVE_SPECS]
    headers, workbook_rows = _read_summary_workbook()
    rows = build_gold_e_rows()
    crosschecks = _raw_processed_crosschecks(curves, workbook_rows)

    curve_summaries = []
    for curve in curves:
        spec: CurveSpec = curve["spec"]
        curve_summaries.append(
            {
                "filename": spec.filename,
                "system_key": spec.system_key,
                "curve_kind": spec.curve_kind,
                "points": spec.points,
                "acquired_at": curve["acquired_at"],
                "condition_name": curve["condition_name"],
                "condition_unit": curve["condition_unit"],
                "property_name": curve["property_name"],
                "unit": curve["unit"],
                "x_min": min(curve["x"]),
                "x_max": max(curve["x"]),
                "y_min": min(curve["y"]),
                "y_max": max(curve["y"]),
                "peak_index": curve["peak_index"],
            }
        )

    counts_by_property = Counter(row["property_name"] for row in rows)
    counts_by_status = Counter(row["gold_admission_status"] for row in rows)
    counts_by_origin = Counter(row["data_origin"] for row in rows)
    if counts_by_status != {
        "admitted_reference": EXPECTED_ADMITTED_ROWS,
        "conditional_reference": EXPECTED_CONDITIONAL_ROWS,
    }:
        raise AuditBlocked(f"准入计数漂移：{dict(counts_by_status)}")
    if sum(bool(row["training_weight"]) for row in rows):
        raise AuditBlocked("不应物化训练权重")

    systems_payload = [
        {
            "system_key": spec.key,
            "source_label": spec.source_label,
            "system_identity": spec.system_identity,
            "identity_status": spec.identity_status,
            "split_group": spec.split_group,
            "ifd_curve_count": 1,
            "stress_relaxation_curve_count": 1,
            "is_independent_experiment_family": False,
        }
        for spec in SYSTEMS.values()
    ]

    return {
        "audit_version": AUDIT_VERSION,
        "source_family": {
            "source_family_key": SOURCE_FAMILY_KEY,
            "dataset_doi_count": 2,
            "datasets_are_independent": False,
            "same_publication_family": True,
            "publication_doi": PAPER_DOI,
            "independent_experiment_campaign_count": 1,
            "material_system_count": 9,
            "split_group_count": 9,
            "raw_curve_points_are_independent_samples": False,
            "processed_scalars_are_independent_replicates": False,
        },
        "metadata": metadata,
        "input_identity": identities,
        "material_systems": systems_payload,
        "curves": {
            "curve_file_count": len(curves),
            "curve_count": len(curves),
            "ifd_curve_count": sum(
                curve["spec"].curve_kind == "ifd" for curve in curves
            ),
            "stress_relaxation_curve_count": sum(
                curve["spec"].curve_kind == "stress_relaxation" for curve in curves
            ),
            "ifd_point_count": sum(
                curve["spec"].points
                for curve in curves
                if curve["spec"].curve_kind == "ifd"
            ),
            "stress_relaxation_point_count": sum(
                curve["spec"].points
                for curve in curves
                if curve["spec"].curve_kind == "stress_relaxation"
            ),
            "total_curve_point_count": sum(curve["spec"].points for curve in curves),
            "source_numeric_axis_and_value_count": 2
            * sum(curve["spec"].points for curve in curves),
            "curve_summaries": curve_summaries,
        },
        "processed_summary": {
            "sheet": "Sheet1",
            "headers": headers,
            "material_system_count": len(workbook_rows),
            "scalar_count": sum(
                bool(row[header]) for row in workbook_rows for header in headers
            ),
            "crosslink_density_count": sum(
                bool(row["Crosslink density"]) for row in workbook_rows
            ),
            "type_a_to_d_crosslink_density_missing_by_source": True,
        },
        "lineage_crosschecks": crosschecks,
        "materialization": {
            "gold_e_row_count": len(rows),
            "curve_point_row_count": EXPECTED_CURVE_POINTS,
            "processed_scalar_row_count": EXPECTED_SUMMARY_SCALARS,
            "property_counts": dict(sorted(counts_by_property.items())),
            "data_origin_counts": dict(sorted(counts_by_origin.items())),
            "gold_admission_status_counts": dict(sorted(counts_by_status.items())),
            "observation_id_count": len({row["observation_id"] for row in rows}),
            "split_group_count": len({row["split_group"] for row in rows}),
            "current_weight_materialized": False,
            "training_weight_nonempty_count": 0,
        },
        "unit_and_protocol_boundaries": {
            "ifd": (
                "displacement mm and force kgf are inferred from the reversible indentation "
                "trajectory plus the processed kgf-to-N crosscheck; retain as conditional"
            ),
            "stress_relaxation": (
                "elapsed time is seconds; ordinate is uncalibrated load-cell voltage, not "
                "absolute force or stress; retain as conditional"
            ),
            "processed_metrics": (
                "IHF and SF are dimensionless; MIF, hysteresis loss and stress relaxation "
                "are percentages; hardness is N; these units are internally crosschecked"
            ),
            "crosslink_density": (
                "unit is absent from the workbook and public preview; preserve source value "
                "with unresolved unit and conditional status"
            ),
            "full_protocol": "not present in the open one-page official preview or deposits",
        },
        "limitations": [
            "No monomer, formulation, NCO/OH, density, geometry or specimen dimensions are deposited.",
            "Type A-D layer identities and layer order are not present in the two data deposits.",
            "Only one IFD and one stress-relaxation trace per source-labelled system are present; no replicate uncertainty can be estimated.",
            "Stress-relaxation voltage lacks a force calibration and cannot be used as absolute stress.",
            "The processed workbook omits units; crosslink-density unit remains unresolved.",
        ],
    }


def _readme_text(payload: dict[str, Any]) -> str:
    materialization = payload["materialization"]
    return f"""# 第十四批实验：PU 多层汽车座椅

## 固定来源

1. Oh, Jeong Seok. *Raw data for manuscript of \"Study on Seating Comfort of Polyurethane Multilayer Seat Cushions\"*. Mendeley Data, version 2, 2019. https://doi.org/{RAW_DOI}. CC BY 4.0.
2. Oh, Jeong Seok. *The processed data for manuscript of \"Study on Seating Comfort of Polyurethane Multilayer Seat Cushions\"*. Mendeley Data, version 2, 2019. https://doi.org/{PROCESSED_DOI}. CC BY 4.0.
3. Moon, J.; Sinha, T. K.; Kwak, S. B.; Ha, J. U.; Oh, J. S. Study on Seating Comfort of Polyurethane Multilayer Seat Cushions. *International Journal of Automotive Technology* **2020**, *21* (5), 1089–1095. https://doi.org/{PAPER_DOI}.

原始数据页：https://data.mendeley.com/datasets/wtfkgk8k8r/2
汇总数据页：https://data.mendeley.com/datasets/jr5fddz5yk/2
论文官方页：https://link.springer.com/article/{PAPER_DOI}

## 数据家族与去重

两个数据 DOI 属于同一篇论文、同一次实验活动。原始集给出 9 个来源标签体系的 IFD 与应力松弛曲线；汇总集给出同一批体系的派生指标。数据库只计 1 个实验活动、9 个材料体系，绝不把两个 DOI、18 条曲线或 73,596 个曲线点当作独立材料体系。所有同体系记录共用一个 `split_group`。

9 个来源标签为 Soft、Mid、Hard、Memory foam、Technogel、Type A、Type B、Type C、Type D。前五者只能解析到材料类别，具体商品牌号和配方未知；Type A-D 是多层坐垫来源标签，开放存档没有给出各层材料与层序。

## 可物化内容

- IFD：9 条曲线、6,320 个点；横轴为压入位移 mm，纵轴为载荷 kgf。单位由加载—卸载轨迹和汇总硬度的 kgf×9.8→N 关系交叉验证，但原文件没有列名，故为条件参考。
- 应力松弛：9 条曲线、67,276 个点；横轴为时间 s，纵轴为负载传感器电压 V。缺少力值标定，不能解释为绝对力或应力，故为条件参考。
- 汇总指标：59 个数值，其中 IHF、MIF、SF、hysteresis loss、hardness、stress relaxation 共 54 个为正式 Gold-E 参考；5 个交联密度值因单位缺失为条件参考。
- Gold-E 长表总计 {materialization['gold_e_row_count']:,} 行：正式参考 {EXPECTED_ADMITTED_ROWS:,}，条件参考 {EXPECTED_CONDITIONAL_ROWS:,}。训练权重为空，未创建训练/验证划分。

## 关键边界

原始曲线点是同一试样轨迹上的相关观测，不是独立样本。汇总标量是原始曲线的同族压缩表达，不是额外实验。当前数据适合学习坐垫级 IFD 形状、松弛形状和舒适性指标之间的关系；由于无单体、配方、密度、几何尺寸及 Type A-D 层序，不能直接用于从 SMILES 预测材料性能，也不能将电压轨迹当作绝对应力。

## 生成文件

- `Gold_E_实验观测长表.tsv`：统一 Gold-E 字段契约。
- `内容审计摘要.json`：体系、曲线、计数、单位证据、同族去重和限制。
- `文件校验清单.tsv`：25 个冻结输入的字节数与 SHA-256。

运行：`python 代码/审计/第十四批PU汽车座椅.py`
"""


def write_outputs() -> dict[str, Any]:
    payload = audit()
    rows = build_gold_e_rows()
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    with OUTPUT_TSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RECORD_COLUMNS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    OUTPUT_AUDIT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with OUTPUT_CHECKSUMS.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("filename", "bytes", "sha256", "role"),
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(payload["input_identity"])
    OUTPUT_README.write_text(_readme_text(payload), encoding="utf-8")
    return payload


if __name__ == "__main__":
    result = write_outputs()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
