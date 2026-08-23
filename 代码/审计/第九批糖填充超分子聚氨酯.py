"""审计 Mendeley 糖填充超分子聚氨酯实验/模型多保真数据。

来源 DOI ``10.17632/z4zy523b8c.1`` 含 28 个 Origin OPJ 工程文件；
它们是论文图件工程，不是 28 个独立材料。论文定义的独立材料条件为未填充
SPU 与八个糖填充配方，共 9 个。本脚本校验官方 API、许可、原件 SHA-256、
固定解析器导出，再由 Origin 图层实际引用恢复曲线，而不是把所有隐藏工作表
或每个曲线点误计成独立样本。

可靠实验曲线和经论文验证的连续体模型曲线均可进入 Gold-E 多保真参考层；
两者通过 ``data_origin``、权重上限、重复曲线哈希和家族泄漏组严格区分。
脚本只读原件，并以原子替换方式写入审计产物。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import stat
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第九批实验_糖填充超分子聚氨酯"
)
OFFICIAL_DIR = SOURCE_DIR / "官方文件"
EXPORT_DIR = SOURCE_DIR / "只读导出"
OUTPUT_DIR = SOURCE_DIR

DATASET_ID = "z4zy523b8c"
DOI = "10.17632/z4zy523b8c.1"
ARTICLE_DOI = "10.1016/j.ijimpeng.2022.104239"
LICENSE_SPDX = "CC-BY-NC-3.0"
AUDIT_VERSION = "batch9-sugar-filled-spu-v1"
FAMILY_GROUP = "spu_sugar_composite_z4zy523b8c_v1"
SOURCE_SCOPE_KEY = "scope_mendeley_z4zy523b8c_v1"

DATASET_URL = "https://data.mendeley.com/datasets/z4zy523b8c/1"
METADATA_URL = "https://data.mendeley.com/public-api/datasets/z4zy523b8c?version=1"
FILES_URL = (
    "https://data.mendeley.com/public-api/datasets/z4zy523b8c/files"
    "?folder_id=root&version=1"
)
ARTICLE_URL = f"https://doi.org/{ARTICLE_DOI}"

EXPECTED_FILE_COUNT = 28
EXPECTED_TOTAL_BYTES = 6_600_228
# 固定解析器实际导出了 144 个可枚举工作表对象。解析器同时报告 12 个
# Spreadsheet 与 37 个 Excel 工作簿容器；容器不是额外工作表，不能叠加计数。
EXPECTED_EXPORTED_SHEETS = 144

FROZEN_EVIDENCE: dict[str, tuple[int, str]] = {
    "CC_BY_NC_3.0_许可证据.html": (
        53_276,
        "a4b64c1e6792e305e3a179156e49b332319d67c2f24c9cc603889df22538db37",
    ),
    "Crossref_论文元数据.json": (
        31_810,
        "a900a1ea7c3a6e7c1f22a4538b7b80fbed4095b7980b49bd329b5496a6a91e07",
    ),
    "DataCite_元数据.json": (
        6_005,
        "ead6766a26530eb3e08fc79b6c78fd924f48e94baea1e409a15dc8735e151c52",
    ),
    "Elsevier_论文核心元数据.xml": (
        2_095,
        "2eb27ffd02c4dc7d99e147723c42dbbbd45b94c0381ae81dd953052bf62431cb",
    ),
    "Mendeley_文件清单_v1.json": (
        19_952,
        "d094db6e20ab19275ba172f06e550d1cde6022e8ee07ae8d8ecacc36e9b5bdc4",
    ),
    "Mendeley_元数据_v1.json": (
        3_204,
        "4f6ff73609fbd9bd2edb0eb1b27c8c564dfd1408231fafb9a9f97c69860ed101",
    ),
    "解析工具证据.json": (
        968,
        "ce2b45cc4dff4a444a72adafe1deb770febe5165e766d37d9aca07fc0abe84c7",
    ),
}

FROZEN_EXPORTS: dict[str, tuple[int, str]] = {
    "Fig10a.opj.json": (
        220_953,
        "7881704b296060b406e6fde043f9e3b96d200415b811e68064473af0acb29681",
    ),
    "Fig10b.opj.json": (
        93_182,
        "4fa0fad9c08d94f3aecfcc7a59d0b069b8eb0df3596d5b7fca63a22ed3989ed6",
    ),
    "Fig10c.opj.json": (
        86_929,
        "096818ab5a3ae3f289f2732ca693e2aef67fff6213805f31baf690a351471c46",
    ),
    "Fig11a.opj.json": (
        14_050,
        "26bed3ee18dcd8736fd0a6c77583950c91d1c45394ec4461c4246d56a8dda4c6",
    ),
    "Fig11b.opj.json": (
        13_716,
        "8469a46518417d391ab6623ee1fe6d75c50f8db38fe81d3c34af50bcfe3aaaa9",
    ),
    "Fig11c.opj.json": (
        13_885,
        "4efd258f3c4700be82a1507580a0f4cc67b903d4312caed933619b94dbdcb49b",
    ),
    "Fig12a.opj.json": (
        89_988,
        "16bb3e367ab8b6a98dba7263db2989829f555c212b917a34a722a43ed3f83740",
    ),
    "Fig12b.opj.json": (
        192_802,
        "a0346ca0767c6873a47180b397344ee47e8a0fd995e4e56950d4fa978749a82d",
    ),
    "Fig12c.opj.json": (
        208_675,
        "66f09e0a85f0d2a797a7f0bff24e91fd06a37f6f54c64300a245e8e048e7baec",
    ),
    "Fig13a.opj.json": (
        191_478,
        "91ebd18a8d0d432de4192ab990931904ccecdb4201742f90bd546ea1073a8529",
    ),
    "Fig13b.opj.json": (
        114_069,
        "ef12f7db17d9dc882bb95ab8208aa2b45ebb90631fc0fe503f69f471c985ce28",
    ),
    "Fig13c.opj.json": (
        86_792,
        "33e22891e1e400cb91987b5003bb7fb526a38edbecb14927617508b0d23efaf7",
    ),
    "Fig14A.opj.json": (
        8_547,
        "92b95e5627d595f4793b3654cac780c1dbd4d948f3c3f9031f2395be35423930",
    ),
    "Fig14B.opj.json": (
        7_263,
        "1b04757c92122eb940f969d2bf712a2f08abed44124be69bbf393d78a268a94d",
    ),
    "Fig2a.opj.json": (
        312_012,
        "9ac924ebc8158c8af5608038769953bcf6fb12749595b1865f919bffae7c964d",
    ),
    "Fig2b.opj.json": (
        72_759,
        "c894eff419f564e74568156ac3bf009498f5e617a0b81e94c359c13e38b5fcff",
    ),
    "Fig3.opj.json": (
        99_176,
        "0a63b463da3f7744a2dbc4e0c40b45fee5c4d05d243ff1215518af2416a5cd90",
    ),
    "Fig4.opj.json": (
        171_814,
        "353dd80815724f0cb24aa646a194c237cd11121a30446c73b6846a625ec03b78",
    ),
    "Fig5a.opj.json": (
        168_666,
        "d3bec3cb9203082111d35413675e4b79148e9b33b85767c039e1c9c99b24e1e0",
    ),
    "Fig5b.opj.json": (
        14_024,
        "98eebfe2d762d760f947a96b9519107e2439e07460d8b5cf81eeda0cdbd5775c",
    ),
    "Fig5c.opj.json": (
        143_315,
        "423398330f59dc9c103ad29318219e7b10d60c4fb2606e08fc13c762ffde6936",
    ),
    "Fig6A.opj.json": (
        7_107,
        "b598b523438b1480950a6ce21c3e30bd3bdca68d4c4c80c51f87cb47a05eba25",
    ),
    "Fig6B.opj.json": (
        6_902,
        "f2a362bf85fa3db3a887143684ecc8f26b1a5938e97ca172fb238114898ad096",
    ),
    "Fig7.opj.json": (
        138_359,
        "95ea3da8e011c933cd0fccb4be9e3bf9a137a6bfb1cec938057bc1e970c365af",
    ),
    "Fig8a.opj.json": (
        573_033,
        "2f85455297a7ce54c68d984e64588977a5911a9125b4346ae83c505fbb4ab929",
    ),
    "Fig8b.opj.json": (
        8_335,
        "3fefd58e93c260f9ecbff5871b5591de5d2cee7cf1f1429a99e8f772bb77d1d2",
    ),
    "Fig8c.opj.json": (
        576_953,
        "ad86bcfd3ebefd346490508a13a7adcd23beaa02281d838bb3019c14eeebcb51",
    ),
    "Fig9.opj.json": (
        208_304,
        "6bc4dd9e63343ebfdbbfba7bccc52632e896dca21ecbb7bfec1ceef74f4b3087",
    ),
}


@dataclass(frozen=True)
class FigureContext:
    panel: str
    materials: tuple[str, ...]
    task: str


FIGURE_CONTEXT: dict[str, FigureContext] = {
    "Fig2a.opj": FigureContext(
        "2a", ("SPU", "G25", "G50", "G75"), "quasi_static_compression"
    ),
    "Fig2b.opj": FigureContext(
        "2b", ("SPU", "G25", "G50", "G75"), "reinforcement_models"
    ),
    "Fig3.opj": FigureContext(
        "3", ("G25", "G50", "G75"), "damage_parameter_sensitivity"
    ),
    "Fig4.opj": FigureContext("4", ("G25", "G50", "G75"), "damage_model_response"),
    "Fig5a.opj": FigureContext(
        "5a", ("G50", "C50", "I50"), "particle_size_stress_response"
    ),
    "Fig5b.opj": FigureContext(
        "5b", ("G50", "C50", "I50"), "particle_size_damage_response"
    ),
    "Fig5c.opj": FigureContext(
        "5c", ("G50", "C50", "I50"), "particle_size_model_validation"
    ),
    "Fig6A.opj": FigureContext(
        "6a", ("G25", "G50", "G75"), "volume_fraction_parameter_fit"
    ),
    "Fig6B.opj": FigureContext(
        "6b", ("G50", "C50", "I50"), "particle_size_parameter_fit"
    ),
    "Fig7.opj": FigureContext("7", ("I30", "C65"), "predicted_parameter_validation"),
    "Fig8a.opj": FigureContext("8a", ("G50",), "low_rate_reinforcement"),
    "Fig8b.opj": FigureContext("8b", ("G50",), "low_rate_damage"),
    "Fig8c.opj": FigureContext("8c", ("G50",), "low_rate_model_validation"),
    "Fig9.opj": FigureContext("9", ("G50",), "high_rate_model_validation"),
    "Fig10a.opj": FigureContext("10a", ("G50",), "high_rate_stress_response"),
    "Fig10b.opj": FigureContext("10b", ("G70",), "high_rate_stress_response"),
    "Fig10c.opj": FigureContext("10c", ("G75",), "high_rate_stress_response"),
    "Fig11a.opj": FigureContext("11a", ("G50",), "adiabatic_temperature_rise"),
    "Fig11b.opj": FigureContext("11b", ("G70",), "adiabatic_temperature_rise"),
    "Fig11c.opj": FigureContext("11c", ("G75",), "adiabatic_temperature_rise"),
    "Fig12a.opj": FigureContext("12a", ("G50",), "adiabatic_model_response"),
    "Fig12b.opj": FigureContext("12b", ("G70",), "adiabatic_model_response"),
    "Fig12c.opj": FigureContext("12c", ("G75",), "adiabatic_model_response"),
    "Fig13a.opj": FigureContext("13a", ("G50",), "final_model_comparison"),
    "Fig13b.opj": FigureContext("13b", ("G70",), "final_model_comparison"),
    "Fig13c.opj": FigureContext("13c", ("G75",), "final_model_comparison"),
    "Fig14A.opj": FigureContext("14a", ("SPU", "G25", "G50", "G75"), "strain_recovery"),
    "Fig14B.opj": FigureContext("14b", ("SPU", "G50", "C50", "I50"), "strain_recovery"),
}


def _material(
    code: str,
    filler: str,
    size_min_um: float | None,
    size_max_um: float | None,
    vf: float,
    rguth: float | None,
    epsilon_a: float | None,
    k: float | None,
    parameter_basis: str,
    coverage: str,
) -> dict[str, Any]:
    return {
        "material_id": code,
        "matrix_family": "thermo-reversible urea-morpholine-end-capped SPU",
        "matrix_components": "Krasol HLBH-P2000 hydrophobic diol; MDI; 4-(2-aminoethyl)morpholine",
        "exact_polymer_smiles": "",
        "filler_material": filler,
        "filler_particle_size_min_um": size_min_um if size_min_um is not None else "",
        "filler_particle_size_max_um": size_max_um if size_max_um is not None else "",
        "filler_volume_fraction": vf,
        "filler_density_kg_m3": 1_600 if filler != "none" else "",
        "guth_reinforcement_factor": rguth if rguth is not None else "",
        "damage_activation_strain_epsilon_a": epsilon_a
        if epsilon_a is not None
        else "",
        "damage_residual_strength_k": k if k is not None else "",
        "parameter_basis": parameter_basis,
        "experimental_coverage": coverage,
        "independent_material_condition": "true",
        "gold_layer": "Gold-E",
        "gold_admission_status": "admitted_reference",
        "data_origin": "experimental_material_with_published_continuum_model",
        "exact_structure_property_trainable": "false",
        "composition_conditioned_curve_trainable": "true_after_curve_mapping_and_deduplication",
        "future_weight_ceiling_experimental": "0.75",
        "future_weight_ceiling_published_model": "0.25",
        "split_group": FAMILY_GROUP,
        "source_scope_key": SOURCE_SCOPE_KEY,
        "license_spdx": LICENSE_SPDX,
    }


MATERIAL_ROWS: tuple[dict[str, Any], ...] = (
    _material(
        "SPU",
        "none",
        None,
        None,
        0.0,
        None,
        None,
        None,
        "not_applicable",
        "quasi-static compression; recovery",
    ),
    _material(
        "G25",
        "granulated sugar",
        530,
        670,
        0.25,
        3.2,
        0.055,
        0.40,
        "fit_to_quasi_static_experiment",
        "quasi-static compression; recovery",
    ),
    _material(
        "G50",
        "granulated sugar",
        530,
        670,
        0.50,
        8.5,
        0.048,
        0.68,
        "fit_to_quasi_static_experiment",
        "0.001/0.01/0.1 s-1 and SHPB to about 1810 s-1; recovery",
    ),
    _material(
        "G70",
        "granulated sugar",
        530,
        670,
        0.70,
        14.9,
        0.043,
        0.93,
        "parameter_surface_then_high_rate_validation",
        "SHPB approximately 580-1400 s-1",
    ),
    _material(
        "G75",
        "granulated sugar",
        530,
        670,
        0.75,
        16.8,
        0.042,
        0.99,
        "fit_to_quasi_static_experiment",
        "quasi-static and SHPB approximately 576-1830 s-1; recovery",
    ),
    _material(
        "C50",
        "caster sugar",
        270,
        340,
        0.50,
        8.5,
        0.054,
        0.61,
        "fit_to_quasi_static_experiment",
        "quasi-static compression; recovery",
    ),
    _material(
        "C65",
        "caster sugar",
        270,
        340,
        0.65,
        13.0,
        0.051,
        0.78,
        "parameter_surface_then_quasi_static_validation",
        "quasi-static compression",
    ),
    _material(
        "I30",
        "icing sugar",
        20,
        25,
        0.30,
        4.0,
        0.065,
        0.31,
        "parameter_surface_then_quasi_static_validation",
        "quasi-static compression",
    ),
    _material(
        "I50",
        "icing sugar",
        20,
        25,
        0.50,
        8.5,
        0.060,
        0.54,
        "fit_to_quasi_static_experiment",
        "quasi-static compression; recovery",
    ),
)

OUTPUT_NAMES = (
    "内容审计摘要.json",
    "文件校验清单.tsv",
    "OPJ解析清单.tsv",
    "曲线审计清单.tsv",
    "材料条件清单.tsv",
    "只读审计报告.md",
)


class AuditBlocked(RuntimeError):
    """Raised when frozen provenance or scientific invariants drift."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify(path: Path, size: int, sha256: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise AuditBlocked(f"缺失或非普通文件：{path}")
    actual_size = path.stat().st_size
    if actual_size != size:
        raise AuditBlocked(f"字节漂移：{path.name}: {actual_size} != {size}")
    actual_sha = _sha256(path)
    if actual_sha != sha256:
        raise AuditBlocked(f"SHA-256 漂移：{path.name}: {actual_sha} != {sha256}")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditBlocked(f"不是严格 UTF-8 JSON：{path}") from exc


def _validate_metadata() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    for name, (size, digest) in FROZEN_EVIDENCE.items():
        _verify(SOURCE_DIR / name, size, digest)

    metadata = _load_json(SOURCE_DIR / "Mendeley_元数据_v1.json")
    if metadata.get("id") != DATASET_ID or metadata.get("version") != 1:
        raise AuditBlocked("Mendeley 数据集 ID 或版本漂移")
    if metadata.get("doi", {}).get("id") != DOI:
        raise AuditBlocked("Mendeley DOI 漂移")
    licence = metadata.get("data_licence", {})
    if licence.get("short_name") != "CC BY NC 3.0":
        raise AuditBlocked("Mendeley 许可漂移")
    if licence.get("url") != "https://creativecommons.org/licenses/by-nc/3.0":
        raise AuditBlocked("Mendeley 许可 URL 漂移")
    if (
        metadata.get("available") is not True
        or metadata.get("confidential") is not False
    ):
        raise AuditBlocked("Mendeley 数据集公开状态漂移")

    crossref = _load_json(SOURCE_DIR / "Crossref_论文元数据.json").get("message", {})
    if str(crossref.get("DOI", "")).lower() != ARTICLE_DOI:
        raise AuditBlocked("论文 DOI 漂移")
    datacite = _load_json(SOURCE_DIR / "DataCite_元数据.json")
    if str(datacite.get("data", {}).get("id", "")).lower() != DOI:
        raise AuditBlocked("DataCite DOI 漂移")

    file_rows = _load_json(SOURCE_DIR / "Mendeley_文件清单_v1.json")
    if not isinstance(file_rows, list) or len(file_rows) != EXPECTED_FILE_COUNT:
        raise AuditBlocked("官方文件数不是 28")
    names = [str(row.get("filename", "")) for row in file_rows]
    if len(set(names)) != EXPECTED_FILE_COUNT or any(
        not name.lower().endswith(".opj") for name in names
    ):
        raise AuditBlocked("官方文件名重复或出现非 OPJ 文件")
    total = sum(
        int(row.get("content_details", {}).get("size", -1)) for row in file_rows
    )
    if total != EXPECTED_TOTAL_BYTES:
        raise AuditBlocked("官方总字节漂移")
    return metadata, sorted(file_rows, key=lambda row: str(row["filename"]).casefold())


def _clean_origin_text(value: Any) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"@\$\{.*", "", text, flags=re.DOTALL)
    return " ".join(text.split())


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _curve_digest(points: Iterable[tuple[float, float]]) -> str:
    digest = hashlib.sha256()
    for x_value, y_value in points:
        digest.update(float(x_value).hex().encode("ascii"))
        digest.update(b"\t")
        digest.update(float(y_value).hex().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _sheet_payload_digest(document: dict[str, Any]) -> str:
    payload = json.dumps(
        document.get("sheets", []),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _resolve_sheet(document: dict[str, Any], data_name: str) -> dict[str, Any] | None:
    if data_name.startswith("E_"):
        token = data_name[2:]
        match = re.fullmatch(r"(.+?)(?:@(\d+))?", token)
        if not match:
            return None
        book_name = match.group(1)
        sheet_index = int(match.group(2) or "1") - 1
        for sheet in document.get("sheets", []):
            if (
                sheet.get("container_kind") == "excel"
                and sheet.get("book_name") == book_name
                and int(sheet.get("sheet_index", -1)) == sheet_index
            ):
                return sheet
    elif data_name.startswith("T_"):
        name = data_name[2:]
        for sheet in document.get("sheets", []):
            if sheet.get("container_kind") == "spreadsheet" and (
                sheet.get("book_name") == name or sheet.get("sheet_name") == name
            ):
                return sheet
    return None


def _column(sheet: dict[str, Any] | None, name: str) -> dict[str, Any] | None:
    if sheet is None:
        return None
    for column in sheet.get("columns", []):
        if str(column.get("name", "")) == name:
            return column
    return None


def _axis_semantics(x_label: str, y_label: str) -> tuple[str, str, str, str, str]:
    x_norm = x_label.lower()
    y_norm = y_label.lower()
    if "strain" in x_norm and "stress" in y_norm:
        return "true_strain", "1", "true_stress", "MPa", "publication_axis_explicit"
    if "strain" in x_norm and "damage" in y_norm:
        return "true_strain", "1", "damage_factor", "1", "publication_axis_explicit"
    if "filler volume fraction" in x_norm and "modulus" in y_norm:
        return (
            "filler_volume_fraction",
            "1",
            "apparent_young_modulus",
            "MPa",
            "publication_axis_explicit",
        )
    if "volume fraction" in x_norm and any(
        token in y_norm for token in ("g(e)", "epsilon", "ε")
    ):
        return (
            "filler_volume_fraction",
            "1",
            "damage_activation_strain_epsilon_a",
            "1",
            "publication_axis_inferred_from_paper",
        )
    if "filler diameter" in x_norm:
        return (
            "filler_particle_diameter",
            "um",
            "damage_activation_strain_epsilon_a",
            "1",
            "publication_axis_inferred_from_paper",
        )
    if "dt" in y_norm or "(+" in y_norm:
        return (
            "true_strain",
            "1",
            "predicted_temperature_rise",
            "degC",
            "publication_axis_explicit",
        )
    if "log t" in x_norm and "log strain" in y_norm:
        return (
            "log10_recovery_time",
            "hr",
            "log10_recovered_strain",
            "1",
            "publication_axis_explicit",
        )
    return (
        "publication_figure_x",
        "",
        "publication_figure_y",
        "",
        "axis_semantics_unresolved",
    )


def _infer_materials(text: str) -> tuple[str, ...]:
    lower = text.lower()
    found: list[str] = []
    patterns = (
        ("SPU", r"\bspu\b|\bpure\b"),
        ("G25", r"\bg25\b|25\s*%?\s*(?:large|granulated)"),
        ("G50", r"\bg50\b|50\s*%?\s*(?:large|granulated)"),
        ("G70", r"\bg70\b|70\s*%?\s*(?:large|granulated)"),
        ("G75", r"\bg75\b|75\s*%?\s*(?:large|granulated)"),
        ("C50", r"\bc50\b|50\s*%?\s*(?:medium|caster)"),
        ("C65", r"\bc65\b|65\s*%?\s*(?:medium|caster)|medium[_ ]65"),
        ("I30", r"\bi30\b|30\s*%?\s*(?:small|icing)"),
        ("I50", r"\bi50\b|50\s*%?\s*(?:small|icing)"),
    )
    for code, pattern in patterns:
        if re.search(pattern, lower):
            found.append(code)
    return tuple(found)


def _origin_class(
    source_file: str,
    sheet: dict[str, Any] | None,
    plot_type: str,
    x_label: str,
    y_label: str,
    graph_curve_index: int,
) -> str:
    sheet_text = " ".join(
        _clean_origin_text((sheet or {}).get(key, ""))
        for key in ("book_name", "book_label", "sheet_name", "sheet_label")
    ).lower()
    if any(
        token in sheet_text
        for token in (
            "testdata",
            "empirical data",
            "test_",
            "pure255075",
            "purelargemediumsmall",
        )
    ):
        return "experimental_processed_curve"
    if any(
        token in sheet_text
        for token in (
            "model",
            "rein",
            "adiabatic",
            "before adiabatic",
            "after adiabatic",
        )
    ):
        return "published_continuum_model_curve"
    if source_file in {"Fig2a.opj", "Fig14A.opj", "Fig14B.opj"}:
        return "experimental_processed_curve"
    if source_file == "Fig2b.opj":
        return (
            "experimental_derived_scalar"
            if plot_type == "Scatter"
            else "published_reinforcement_model_curve"
        )
    if source_file in {
        "Fig3.opj",
        "Fig6A.opj",
        "Fig6B.opj",
        "Fig11a.opj",
        "Fig11b.opj",
        "Fig11c.opj",
    }:
        return (
            "experimental_derived_scalar"
            if plot_type in {"Scatter", "LineSymbol"}
            else "published_continuum_model_curve"
        )
    if source_file == "Fig5b.opj":
        return (
            "experimental_derived_scalar"
            if plot_type == "Scatter"
            else "published_continuum_model_curve"
        )
    if source_file == "Fig8b.opj":
        return (
            "experimental_derived_scalar"
            if plot_type == "Scatter"
            else "published_continuum_model_curve"
        )
    if source_file == "Fig4.opj" and graph_curve_index == 0:
        return "experimental_processed_curve"
    if "stress" in y_label.lower() and plot_type == "Scatter":
        return "experimental_processed_curve"
    return "mixed_published_experiment_model"


def _material_mapping(
    context: FigureContext,
    source_file: str,
    sheet: dict[str, Any] | None,
    x_column: dict[str, Any] | None,
    y_column: dict[str, Any] | None,
) -> tuple[str, str, str]:
    text_parts = [source_file]
    if sheet:
        text_parts.extend(
            str(sheet.get(key, ""))
            for key in ("book_name", "sheet_name", "sheet_label")
        )
    for column in (x_column, y_column):
        if column:
            text_parts.extend(
                str(column.get(key, ""))
                for key in ("name", "long_name", "units", "comments")
            )
    inferred = _infer_materials(" ".join(text_parts))
    expected = context.materials
    conflicts = tuple(code for code in inferred if code not in expected)
    if conflicts:
        return (
            ";".join(expected),
            ";".join(inferred),
            "publication_context_internal_label_conflict",
        )
    exact = tuple(code for code in inferred if code in expected)
    if exact:
        return ";".join(exact), ";".join(inferred), "explicit_internal_label"
    if len(expected) == 1:
        return expected[0], "", "publication_single_material_context"
    if source_file == "Fig7.opj" and sheet:
        if str(sheet.get("book_name")) == "Book3":
            return "C65", "", "publication_book_context"
        if str(sheet.get("book_name")) == "Book2":
            return "I30", "", "publication_book_context"
    return ";".join(expected), "", "multi_material_scope_unresolved"


def _admission(origin: str, mapping: str, duplicate_rank: int) -> tuple[str, str]:
    if duplicate_rank > 0:
        return "conditional_reference", "0.00"
    if mapping in {
        "publication_context_internal_label_conflict",
        "multi_material_scope_unresolved",
    }:
        return "conditional_reference", "0.00"
    if origin == "experimental_processed_curve":
        return "admitted_reference", "0.75"
    if origin == "experimental_derived_scalar":
        return "admitted_reference", "0.50"
    if origin.startswith("published_"):
        return "admitted_reference", "0.25"
    return "conditional_reference", "0.10"


def _gold_layer(origin: str) -> str:
    """按每条曲线的真实保真来源分层，避免把论文模型冒充实验。"""
    if origin.startswith("experimental_"):
        return "Gold-E"
    if origin.startswith("published_"):
        return "Gold-C"
    if origin == "mixed_published_experiment_model":
        return "Gold-E+Gold-C"
    raise AuditBlocked(f"未知曲线来源，无法分配Gold层：{origin}")


def _audit_exports(
    official_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    official_by_name = {str(row["filename"]): row for row in official_rows}
    if set(FIGURE_CONTEXT) != set(official_by_name):
        raise AuditBlocked("图件语义登记未覆盖全部官方 OPJ")
    if set(FROZEN_EXPORTS) != {f"{name}.json" for name in official_by_name}:
        raise AuditBlocked("只读导出冻结清单未覆盖全部官方 OPJ")

    parse_rows: list[dict[str, Any]] = []
    curve_rows: list[dict[str, Any]] = []
    documents: dict[str, dict[str, Any]] = {}
    total_graph_curves = 0
    resolved_graph_curves = 0

    for export_name in sorted(FROZEN_EXPORTS, key=str.casefold):
        expected_size, expected_sha = FROZEN_EXPORTS[export_name]
        export_path = EXPORT_DIR / export_name
        _verify(export_path, expected_size, expected_sha)
        document = _load_json(export_path)
        source_file = str(document.get("source_file", ""))
        if export_name != f"{source_file}.json" or source_file not in official_by_name:
            raise AuditBlocked(f"导出与官方文件映射漂移：{export_name}")
        if (
            document.get("parser_name") != "Altaxo/LibOriginCSharp"
            or int(document.get("parse_error", -1)) != 0
        ):
            raise AuditBlocked(f"OPJ 解析失败或解析器漂移：{source_file}")
        documents[source_file] = document

        numeric_values = string_values = null_values = numeric_columns = (
            string_columns
        ) = 0
        for sheet in document.get("sheets", []):
            for column in sheet.get("columns", []):
                seen_numeric = seen_string = False
                for value in column.get("values", []):
                    if _numeric(value) is not None:
                        numeric_values += 1
                        seen_numeric = True
                    elif isinstance(value, str):
                        string_values += 1
                        seen_string = True
                    else:
                        null_values += 1
                numeric_columns += int(seen_numeric)
                string_columns += int(seen_string)

        graph_curve_count = sum(
            int(layer.get("curve_count", 0))
            for graph in document.get("graphs", [])
            for layer in graph.get("layers", [])
        )
        total_graph_curves += graph_curve_count
        parse_rows.append(
            {
                "source_file": source_file,
                "official_bytes": int(
                    official_by_name[source_file]["content_details"]["size"]
                ),
                "official_sha256": str(
                    official_by_name[source_file]["content_details"]["sha256_hash"]
                ),
                "origin_version": document.get("origin_version", ""),
                "parse_error": document.get("parse_error", ""),
                "dataset_count": document.get("dataset_count", ""),
                "spreadsheet_count": document.get("spreadsheet_count", ""),
                "excel_count": document.get("excel_count", ""),
                "graph_count": document.get("graph_count", ""),
                "exported_sheet_count": document.get("exported_sheet_count", ""),
                "plotted_graph_curve_count": graph_curve_count,
                "numeric_column_count": numeric_columns,
                "string_column_count": string_columns,
                "finite_numeric_value_count": numeric_values,
                "string_value_count": string_values,
                "null_value_count": null_values,
                "sheet_payload_sha256": _sheet_payload_digest(document),
                "readonly_export_file": export_name,
                "readonly_export_bytes": expected_size,
                "readonly_export_sha256": expected_sha,
                "parser_commit": "f5457c4e2ae9d3b0783dcb3a408ecee3cf7f1c4e",
            }
        )

        context = FIGURE_CONTEXT[source_file]
        curve_ordinal = 0
        for graph in document.get("graphs", []):
            for layer in graph.get("layers", []):
                x_label = _clean_origin_text(
                    layer.get("x_axis", {}).get("label_bottom", "")
                )
                y_label = _clean_origin_text(
                    layer.get("y_axis", {}).get("label_left", "")
                )
                x_name, x_unit, y_name, y_unit, axis_status = _axis_semantics(
                    x_label, y_label
                )
                for graph_curve in layer.get("curves", []):
                    curve_ordinal += 1
                    data_name = str(
                        graph_curve.get("data_name")
                        or graph_curve.get("x_data_name")
                        or ""
                    )
                    sheet = _resolve_sheet(document, data_name)
                    x_column = _column(sheet, str(graph_curve.get("x_column_name", "")))
                    y_column = _column(sheet, str(graph_curve.get("y_column_name", "")))
                    resolution_status = "resolved"
                    points: list[tuple[float, float]] = []
                    if sheet is None:
                        resolution_status = "unresolved_dataset_reference"
                    elif x_column is None or y_column is None:
                        resolution_status = "unresolved_column_reference"
                    else:
                        for x_raw, y_raw in zip(
                            x_column.get("values", []),
                            y_column.get("values", []),
                            strict=False,
                        ):
                            x_value = _numeric(x_raw)
                            y_value = _numeric(y_raw)
                            if x_value is not None and y_value is not None:
                                points.append((x_value, y_value))
                        if len(points) < 2:
                            resolution_status = "insufficient_finite_pairs"
                    if resolution_status == "resolved":
                        resolved_graph_curves += 1
                    material_ids, internal_material_ids, mapping_status = (
                        _material_mapping(
                            context, source_file, sheet, x_column, y_column
                        )
                    )
                    origin = _origin_class(
                        source_file,
                        sheet,
                        str(graph_curve.get("plot_type", "")),
                        x_label,
                        y_label,
                        int(graph_curve.get("curve_index", 0)),
                    )
                    digest = _curve_digest(points) if len(points) >= 2 else ""
                    curve_rows.append(
                        {
                            "curve_id": f"mendeley_z4_{Path(source_file).stem}_{curve_ordinal:03d}",
                            "source_file": source_file,
                            "publication_figure_panel": context.panel,
                            "scientific_task": context.task,
                            "graph_name": graph.get("graph_name", ""),
                            "graph_layer_index": layer.get("layer_index", ""),
                            "graph_curve_index": graph_curve.get("curve_index", ""),
                            "plot_type": graph_curve.get("plot_type", ""),
                            "data_name": data_name,
                            "sheet_name": (sheet or {}).get("sheet_name", ""),
                            "x_column": graph_curve.get("x_column_name", ""),
                            "y_column": graph_curve.get("y_column_name", ""),
                            "x_axis_label_raw": x_label,
                            "y_axis_label_raw": y_label,
                            "x_name": x_name,
                            "x_unit": x_unit,
                            "y_name": y_name,
                            "y_unit": y_unit,
                            "axis_semantics_status": axis_status,
                            "point_count": len(points),
                            "x_min": min((point[0] for point in points), default=""),
                            "x_max": max((point[0] for point in points), default=""),
                            "y_min": min((point[1] for point in points), default=""),
                            "y_max": max((point[1] for point in points), default=""),
                            "curve_sha256": digest,
                            "resolution_status": resolution_status,
                            "material_ids": material_ids,
                            "internal_label_material_ids": internal_material_ids,
                            "material_mapping_status": mapping_status,
                            "data_origin": origin,
                            "record_granularity": "publication_graph_curve",
                            "curve_points_are_independent_material_samples": "false",
                            "gold_layer": _gold_layer(origin),
                            "gold_admission_status": "",
                            "future_weight_ceiling": "",
                            "duplicate_rank": "",
                            "duplicate_of_curve_id": "",
                            "split_group": FAMILY_GROUP,
                            "source_scope_key": SOURCE_SCOPE_KEY,
                            "license_spdx": LICENSE_SPDX,
                        }
                    )

    duplicate_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in curve_rows:
        if row["curve_sha256"]:
            duplicate_groups[str(row["curve_sha256"])].append(row)
    for rows in duplicate_groups.values():
        rows.sort(key=lambda row: str(row["curve_id"]))
        primary = str(rows[0]["curve_id"])
        for rank, row in enumerate(rows):
            row["duplicate_rank"] = rank
            row["duplicate_of_curve_id"] = "" if rank == 0 else primary
            admission, ceiling = _admission(
                str(row["data_origin"]), str(row["material_mapping_status"]), rank
            )
            if row["resolution_status"] != "resolved":
                admission, ceiling = "conditional_reference", "0.00"
            row["gold_admission_status"] = admission
            row["future_weight_ceiling"] = ceiling
    for row in curve_rows:
        if not row["curve_sha256"]:
            row["duplicate_rank"] = ""
            row["gold_admission_status"] = "conditional_reference"
            row["future_weight_ceiling"] = "0.00"

    sheet_payload_groups: dict[str, list[str]] = defaultdict(list)
    for row in parse_rows:
        sheet_payload_groups[str(row["sheet_payload_sha256"])].append(
            str(row["source_file"])
        )
    exact_sheet_duplicate_groups = [
        sorted(group) for group in sheet_payload_groups.values() if len(group) > 1
    ]

    stats = {
        "total_graph_curve_references": total_graph_curves,
        "resolved_graph_curve_references": resolved_graph_curves,
        "unique_resolved_curve_payloads": len(duplicate_groups),
        "duplicate_curve_references": sum(
            max(0, len(rows) - 1) for rows in duplicate_groups.values()
        ),
        "resolved_curve_points_total": sum(
            int(row["point_count"]) for row in curve_rows
        ),
        "exact_sheet_payload_duplicate_groups": exact_sheet_duplicate_groups,
        "curve_origin_counts": dict(
            sorted(Counter(str(row["data_origin"]) for row in curve_rows).items())
        ),
        "curve_gold_layer_counts": dict(
            sorted(Counter(str(row["gold_layer"]) for row in curve_rows).items())
        ),
        "curve_admission_counts": dict(
            sorted(
                Counter(str(row["gold_admission_status"]) for row in curve_rows).items()
            )
        ),
        "curve_mapping_counts": dict(
            sorted(
                Counter(
                    str(row["material_mapping_status"]) for row in curve_rows
                ).items()
            )
        ),
    }
    return parse_rows, curve_rows, stats


def audit() -> dict[str, Any]:
    metadata, official_rows = _validate_metadata()
    file_rows: list[dict[str, Any]] = []
    for row in official_rows:
        details = row["content_details"]
        path = OFFICIAL_DIR / row["filename"]
        _verify(path, int(details["size"]), str(details["sha256_hash"]))
        file_rows.append(
            {
                "filename": row["filename"],
                "file_id": row["id"],
                "bytes": details["size"],
                "sha256": details["sha256_hash"],
                "content_type": details.get("content_type", ""),
                "download_url": details.get("download_url", ""),
                "local_integrity": "verified",
                "file_role": "official_origin_project",
                "independent_material_count": 0,
            }
        )

    parse_rows, curve_rows, curve_stats = _audit_exports(official_rows)
    exported_sheets = sum(int(row["exported_sheet_count"]) for row in parse_rows)
    if exported_sheets != EXPECTED_EXPORTED_SHEETS:
        raise AuditBlocked(f"导出工作表数漂移：{exported_sheets}")
    if (
        len(MATERIAL_ROWS) != 9
        or len({row["material_id"] for row in MATERIAL_ROWS}) != 9
    ):
        raise AuditBlocked("独立材料条件必须严格为 9")

    summary = {
        "audit_version": AUDIT_VERSION,
        "dataset": {
            "repository": "Mendeley Data",
            "dataset_id": DATASET_ID,
            "doi": DOI,
            "article_doi": ARTICLE_DOI,
            "title": metadata.get("name", ""),
            "dataset_url": DATASET_URL,
            "metadata_url": METADATA_URL,
            "files_url": FILES_URL,
            "license_spdx": LICENSE_SPDX,
            "license_name": metadata.get("data_licence", {}).get("full_name", ""),
            "commercial_use_allowed": False,
        },
        "integrity": {
            "official_opj_file_count": len(file_rows),
            "official_total_bytes": sum(int(row["bytes"]) for row in file_rows),
            "official_sha256_verified_count": sum(
                row["local_integrity"] == "verified" for row in file_rows
            ),
            "readonly_export_count": len(parse_rows),
            "opj_parse_success_count": sum(
                int(row["parse_error"]) == 0 for row in parse_rows
            ),
            "exported_sheet_count": exported_sheets,
            "spreadsheet_container_count": sum(
                int(row["spreadsheet_count"]) for row in parse_rows
            ),
            "excel_container_count": sum(int(row["excel_count"]) for row in parse_rows),
            "origin_version_counts": dict(
                sorted(
                    Counter(str(row["origin_version"]) for row in parse_rows).items()
                )
            ),
        },
        "scientific_counts": {
            "independent_material_conditions": len(MATERIAL_ROWS),
            "unfilled_matrix_conditions": 1,
            "sugar_filled_conditions": 8,
            **curve_stats,
        },
        "gold_recommendation": {
            "layer": "Gold-E+Gold-C",
            "status": "admitted_multifidelity_reference",
            "reason": "公开原始 OPJ、逐文件 SHA-256、论文方法与图层曲线引用均可核验；实验与连续体模型分层保留。",
            "experimental_curve_weight_ceiling": 0.75,
            "published_model_curve_weight_ceiling": 0.25,
            "unresolved_or_duplicate_weight_ceiling": 0.0,
            "structure_only_model_use": "blocked_no_exact_polymer_smiles",
            "recommended_use": "家族/配方/填料/工况条件化的压缩曲线、多保真校准、外部验证；按整个 SPU 糖填充家族分组切分。",
        },
        "caveats": [
            "28 个 OPJ 是论文图件工程，不是 28 个材料；独立材料条件严格为 9。",
            "曲线点不是独立材料样本；同一底层数据在多个图层/图件复用，必须按 curve_sha256 去重。",
            "SPU 精确重复单元 SMILES 未闭合，不得用于仅凭 SMILES 的结构-性能监督。",
            "CC BY-NC 3.0 禁止商业用途；必须与可商用数据分仓或显式许可门禁。",
            "公开模型曲线可作为可靠虚拟/计算参考，但不能冒充独立实验标签。",
        ],
    }
    return {
        "summary": summary,
        "file_rows": file_rows,
        "parse_rows": parse_rows,
        "curve_rows": curve_rows,
        "material_rows": list(MATERIAL_ROWS),
    }


def _ensure_safe_output(path: Path) -> None:
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise AuditBlocked(f"输出目录缺失或为链接：{parent}")
    if (
        os.name == "nt"
        and parent.stat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise AuditBlocked(f"输出目录为 reparse point：{parent}")
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise AuditBlocked(f"拒绝覆盖链接或非普通文件：{path}")


def _atomic_write(path: Path, text: str) -> None:
    _ensure_safe_output(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _tsv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=list(rows[0]), delimiter="\t", lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _report(bundle: dict[str, Any]) -> str:
    summary = bundle["summary"]
    integrity = summary["integrity"]
    counts = summary["scientific_counts"]
    return "\n".join(
        [
            "# 糖填充超分子聚氨酯只读审计报告",
            "",
            f"- 数据集：Mendeley Data [{DOI}]({DATASET_URL})",
            f"- 关联论文：Chen et al., *International Journal of Impact Engineering* 166 (2022) 104239, [{ARTICLE_DOI}]({ARTICLE_URL})",
            f"- 许可：{LICENSE_SPDX}（仅非商业使用）",
            f"- 官方原件：{integrity['official_opj_file_count']} 个 OPJ，{integrity['official_total_bytes']:,} bytes，SHA-256 全部通过",
            f"- 解析覆盖：{integrity['opj_parse_success_count']}/28 成功，恢复 {integrity['exported_sheet_count']} 个工作表",
            f"- 图层曲线：{counts['resolved_graph_curve_references']}/{counts['total_graph_curve_references']} 条引用可解析；{counts['unique_resolved_curve_payloads']} 个唯一数值载荷",
            f"- 独立材料：{counts['independent_material_conditions']}（SPU + 8 个糖填充条件），不是 28",
            "",
            "## Gold-E 准入",
            "",
            "实验曲线进入 Gold-E 参考层，建议最高权重 0.75；论文连续体模型和参数面产生的虚拟曲线也保留，建议最高权重 0.25。重复、材料映射未闭合或图层引用未解析的记录权重为 0，但不从参考库删除。所有记录共享同一家族切分组，防止同一 SPU 系列跨训练/测试泄漏。",
            "",
            "## 重要边界",
            "",
            "28 个 OPJ 是图件工程；曲线点、图层和模型输出均不是独立材料。SPU 的原料家族可识别，但精确重复单元 SMILES 未闭合，因此该源适合配方/填料/工况条件化曲线学习、多保真校准和外部验证，不适合直接训练纯 SMILES 结构模型。许可为 CC BY-NC 3.0，商业使用必须隔离。",
            "",
            "## 引用",
            "",
            f"1. Chen, H.; Hart, L. R.; Hayes, W.; Siviour, C. R. *Experimental characterisation and modelling of sugar-filled supramolecular polyurethane* (Version 1) [Data set]. Mendeley Data, 2022. DOI: [{DOI}](https://doi.org/{DOI}).",
            f"2. Chen, H.; Hart, L. R.; Hayes, W.; Siviour, C. R. Experimental characterisation and modelling of the strain rate dependent mechanical response of a filled thermo-reversible supramolecular polyurethane. *International Journal of Impact Engineering* **166** (2022), 104239. DOI: [{ARTICLE_DOI}](https://doi.org/{ARTICLE_DOI}).",
            "3. Altaxo. *LibOriginCSharp: standalone library for reading OriginLab project files*. Fixed commit f5457c4e2ae9d3b0783dcb3a408ecee3cf7f1c4e, GPL-3.0-or-later. https://github.com/Altaxo/LibOriginCSharp",
            "4. Creative Commons. *Attribution-NonCommercial 3.0 Unported*. https://creativecommons.org/licenses/by-nc/3.0/legalcode.en",
            "",
        ]
    )


def render_outputs(bundle: dict[str, Any]) -> dict[str, str]:
    """Render a deterministic, side-effect-free output bundle."""

    outputs = {
        "内容审计摘要.json": json.dumps(
            bundle["summary"], ensure_ascii=False, indent=2, sort_keys=True
        )
        + "\n",
        "文件校验清单.tsv": _tsv(bundle["file_rows"]),
        "OPJ解析清单.tsv": _tsv(bundle["parse_rows"]),
        "曲线审计清单.tsv": _tsv(bundle["curve_rows"]),
        "材料条件清单.tsv": _tsv(bundle["material_rows"]),
        "只读审计报告.md": _report(bundle),
    }
    if set(outputs) != set(OUTPUT_NAMES):
        raise AuditBlocked("输出集合漂移")
    return outputs


def main() -> None:
    bundle = audit()
    outputs = render_outputs(bundle)
    for name, content in outputs.items():
        _atomic_write(OUTPUT_DIR / name, content)
    print(json.dumps(bundle["summary"], ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
