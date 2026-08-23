"""审计 Mendeley Data 非异氰酸酯 PHCU 多模态实验原始数据。

来源 DOI: 10.17632/bvv43yk29c.1。官方压缩包只包含原始仪器文件，
其中 Origin 拉伸、TGA、XRD 已通过只读导出恢复为数值表。本脚本不把
曲线点、光谱点或热分析点误计为独立材料样本；独立最终配方固定为
PHCU10/20/30/40/50/70 六个同系列配方。

审计采取宽口径多保真准入：可靠实验曲线进入 Gold-E 参考层；表征曲线
可作辅助任务；标签或结构未闭合的数据不删除，而是以显式门禁、权重上限
和泄漏组保留。脚本只读原件并原子写入审计产物，从不改写原始文件。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = (
    PROJECT_ROOT
    / "数据/原始/外部数据/新增开放数据/第八批实验_非异氰酸酯PHCU热塑性聚氨酯"
)
EXTRACTED_DIR = SOURCE_DIR / "解压原件"
EXPORT_DIR = SOURCE_DIR / "只读导出"

DOI = "10.17632/bvv43yk29c.1"
ARTICLE_DOI = "10.1016/j.eurpolymj.2018.08.006"
SUPPORTING_ARTICLE_DOI = "10.1016/j.polymer.2021.123675"
LICENSE = "CC BY 4.0"
AUDIT_VERSION = "batch8-nonisocyanate-phcu-v1"
FORMULATION_CODES = ("PHCU10", "PHCU20", "PHCU30", "PHCU40", "PHCU50", "PHCU70")
HU_MOL_PERCENT = dict(zip(FORMULATION_CODES, (10, 20, 30, 40, 50, 70), strict=True))

DATASET_URL = "https://data.mendeley.com/datasets/bvv43yk29c/1"
METADATA_URL = "https://data.mendeley.com/public-api/datasets/bvv43yk29c?version=1"
FILES_URL = (
    "https://data.mendeley.com/public-api/datasets/bvv43yk29c/files"
    "?folder_id=root&version=1"
)
DIRECT_DOWNLOAD_URL = (
    "https://data.mendeley.com/public-files/datasets/bvv43yk29c/files/"
    "5a169134-f898-4e56-95d9-79041b368e32/file_downloaded"
)
ARTICLE_URL = "https://www.sciencedirect.com/science/article/pii/S0014305718310310"
SUPPORTING_ARTICLE_URL = (
    "https://www.sciencedirect.com/science/article/pii/S0032386121002986"
)

OUTPUT_NAMES = (
    "内容审计摘要.json",
    "曲线审计清单.tsv",
    "配方审计清单.tsv",
    "文件校验清单.tsv",
    "只读审计报告.md",
)

# 官方原件和一次性只读导出的身份均冻结。只读导出不是官方附件，
# 但它与本次审计使用的数值视图保持可复算的一致身份。
FROZEN_FILES: dict[str, tuple[int, str, str]] = {
    "data.rar": (
        2_956_365,
        "2b10ccfa6ea2b0b223e65eee525d671ca63919f7483e0fc76a0a337a350c5d10",
        "official_attachment",
    ),
    "Mendeley_元数据_v1.json": (
        2_743,
        "8339a2399cb1f9331879d80c31f848e26d11afebc4cc3ac5ebf1312aa094a724",
        "official_public_api_snapshot",
    ),
    "Mendeley_文件清单_v1.json": (
        711,
        "3626bb9caa07898fad49f2614736207714dc79cf5ba36f08ab26f825708c28c5",
        "official_public_api_snapshot",
    ),
    "Mendeley_文件夹_v1.json": (
        2,
        "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
        "official_public_api_snapshot",
    ),
    "解压原件/DSC/PHCU10.001": (338_273, "3ebfb6f37669e8aeeec24173e7af66eea828b2e6cc20683333b958871da3444a", "archive_member"),
    "解压原件/DSC/PHCU20.001": (338_731, "c78c49d396c00982e539e9e479701e7ba2cab50200c55899c01f40b6b8a70403", "archive_member"),
    "解压原件/DSC/PHCU30.001": (338_755, "50b1f17073e84b2d672074ef2c2cf2efcbffeeb73d9ac47aba06df2178bb94c7", "archive_member"),
    "解压原件/DSC/PHCU40.001": (422_813, "526ed6aa799811895e1033400ca72048b822810f88c9578708b88b9889c86e5a", "archive_member"),
    "解压原件/DSC/PHCU50.001": (338_799, "5378244d0b18dc8cd9f81076e9730783e8099a840af7c157e4cce110b9ca5640", "archive_member"),
    "解压原件/DSC/PHCU70.001": (338_725, "de3b16cec2519352922be5eac06d34e5659f1b5272a5db6b9c1240736af44781", "archive_member"),
    "解压原件/FTIR/BHC.SPA": (75_144, "4a54e5d7b198d71574152bd0d54bc3d8ed905e5aaed49a0f230ee4287679b07e", "archive_member"),
    "解压原件/FTIR/Crosslinked polymer.SPA": (75_144, "13d9c6c76554c2a7f1c8ce6bb501ef91057ec81b00f251fdaeaa546960f78193", "archive_member"),
    "解压原件/FTIR/PHCU.SPA": (75_144, "63809c0ba86ca8436fdab5911c8b2deadd3ecff0f72cb06fe4e1a5e806f27b16", "archive_member"),
    "解压原件/GPC/PHCU10.arw": (49_770, "3948db6f450598c18e8dc1eb455b7c8cf9ef728c487ae970ae6930b430ece847", "archive_member"),
    "解压原件/GPC/PHCU20.arw": (49_137, "08a4f4b1a662142c01cc51bcd5a98f05f50e931c0e781b7c6abc0339b329fb1d", "archive_member"),
    "解压原件/GPC/PHCU30.arw": (49_512, "b77b006379acf1b79a85b2f25b314bfddb29711534b99b566c7d0e9ba8f68672", "archive_member"),
    "解压原件/GPC/PHCU40.arw": (48_393, "33cf13cca1c2d4db598db609f84d01686bfd1e181dc01ffb1b3fcc82441b6b8f", "archive_member"),
    "解压原件/GPC/PHCU50.arw": (48_464, "5bdd3f684cedf6f39affb14400cce01b0d70eb35cba29ddbffa39fd9e8e97fbb", "archive_member"),
    "解压原件/NMR/BHC-Solid13C.txt": (35_842, "9cf768b45820779d2c68e1270c3bc54ff44f9ccf117d1a8c893b9402ae8377ea", "archive_member"),
    "解压原件/NMR/Crosslinked polymer-solid13C.txt": (36_332, "3dbd9acc1e7ac725ea6c49f035fe76fcfc143fed9438514305e92a01e9334ab9", "archive_member"),
    "解压原件/NMR/PCDL-1H.txt": (1_120_340, "088767547a087dc5baf4c89ee50ce5d9a5421497bbcf7bc8ac53e38ed9c43c7a", "archive_member"),
    "解压原件/NMR/PHCU30-13C.txt": (1_166_833, "9f3c7345ec7724105f5810a02201949118a298696218ae4fa6a7f3b7d12eace6", "archive_member"),
    "解压原件/NMR/PHCU30-1H.txt": (1_119_455, "6b736608e871114722b4475313d3ea0392faa0d6fab8139bf884c4a67e25fccb", "archive_member"),
    "解压原件/NMR/PUDL.txt": (1_119_485, "55ba3556efd9c3af84624154071d50a325263d9798694d85ceae5a1551c008b2", "archive_member"),
    "解压原件/Tensile/tensile.opj": (834_488, "c7f8078f40d687c7dc5008c086255f868fe2e5d4a2a1745230f9b89da3edf9e8", "archive_member"),
    "解压原件/TGA/TGA.opj": (365_106, "db7327eb3b9310622e4257d5ef55ebcde56c2991f895073bacfad3ffc72f0239", "archive_member"),
    "解压原件/XRD/XRD.opj": (382_688, "367c1eeccb8a855505850e42d92073dd1a7b865101e7d30eda665311fd68ed49", "archive_member"),
    "只读导出/TGA.opj.1.dat": (260_983, "fc3555c9932e534e0b8571028672a049c64d4ac6a4bcf1da18702aa593f5779e", "read_only_origin_export"),
    "只读导出/XRD.opj.1.dat": (200_404, "f722eeecd3bb222ebde0ac45056f5b81eefa35b3944ae7af908b7619f7a7c65e", "read_only_origin_export"),
    "只读导出/拉伸.opj.1.dat": (940_365, "a5840660143f15dd0c0a3455b4d3612748aa9e3aca7833b0aae9a5fb0940c273", "read_only_origin_export"),
}

CURVE_COLUMNS = (
    "curve_id", "formulation_id", "material_role", "modality", "source_file",
    "source_pair", "point_count", "x_name", "x_unit", "y_name", "y_unit",
    "x_min", "x_max", "y_min", "y_max", "x_at_y_max",
    "temperature_at_5_percent_loss_c", "temperature_at_10_percent_loss_c",
    "temperature_at_50_percent_loss_c", "residue_percent_near_600_c",
    "mapping_status", "unit_status", "replicate_status", "data_origin",
    "record_granularity", "gold_layer", "gold_admission_status",
    "future_weight_ceiling", "split_group", "family_leakage_group",
    "curve_points_are_independent_samples", "notes",
)

FORMULATION_COLUMNS = (
    "formulation_id", "family_name", "hu_mol_percent", "hu_semantics_source",
    "exact_polymer_smiles", "polymer_structure_resolution",
    "known_monomer_smiles", "synthesis_route", "data_origin", "gold_layer",
    "gold_admission_status", "source_reliability", "composition_series_trainable",
    "exact_structure_property_trainable", "reported_family_mn_max_g_mol",
    "formulation_mn_g_mol", "formulation_mw_g_mol", "mn_mw_status",
    "reported_family_melting_range_c", "reported_family_tensile_range_mpa",
    "reported_family_elongation_range_percent", "tensile_curve_id",
    "tensile_point_count", "derived_peak_stress_mpa",
    "derived_strain_at_peak_stress_percent", "curve_max_strain_percent",
    "tga_curve_id", "tga_point_count", "derived_t5_loss_c",
    "derived_t10_loss_c", "derived_t50_loss_c", "gpc_raw_curve_available",
    "dsc_external_file", "dsc_internal_file", "dsc_mapping_status",
    "replicate_status", "split_group", "family_leakage_group",
    "curve_points_are_independent_samples", "future_weight_ceiling", "notes",
)

FILE_COLUMNS = (
    "file", "role", "bytes", "sha256", "verification",
    "official_sha256", "official_sha256_match", "provenance",
)


class AuditBlocked(RuntimeError):
    """原件身份、元数据或冻结结构发生漂移。"""


def _digest(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_files() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for relative, (expected_bytes, expected_sha256, role) in FROZEN_FILES.items():
        path = SOURCE_DIR / relative
        if not path.is_file():
            raise AuditBlocked(f"缺少冻结文件：{path}")
        actual_bytes = path.stat().st_size
        actual_sha256 = _digest(path)
        if (actual_bytes, actual_sha256) != (expected_bytes, expected_sha256):
            raise AuditBlocked(
                f"文件漂移：{relative} bytes={actual_bytes}, sha256={actual_sha256}"
            )
        official_sha256 = expected_sha256 if relative == "data.rar" else ""
        rows.append(
            {
                "file": relative,
                "role": role,
                "bytes": actual_bytes,
                "sha256": actual_sha256,
                "verification": "matched_frozen_identity",
                "official_sha256": official_sha256,
                "official_sha256_match": actual_sha256 == official_sha256 if official_sha256 else "",
                "provenance": (
                    DIRECT_DOWNLOAD_URL
                    if role == "official_attachment"
                    else (FILES_URL if role == "official_public_api_snapshot" else "data.rar")
                ),
            }
        )

    metadata = json.loads((SOURCE_DIR / "Mendeley_元数据_v1.json").read_text(encoding="utf-8"))
    if metadata.get("id") != "bvv43yk29c" or metadata.get("version") != 1:
        raise AuditBlocked("Mendeley 数据集 ID 或版本漂移")
    if metadata.get("doi", {}).get("id") != DOI:
        raise AuditBlocked("Mendeley DOI 漂移")
    if metadata.get("data_licence", {}).get("short_name") != LICENSE:
        raise AuditBlocked("Mendeley 许可漂移")
    if not metadata.get("available") or metadata.get("confidential"):
        raise AuditBlocked("Mendeley 数据集公开状态漂移")

    files = json.loads((SOURCE_DIR / "Mendeley_文件清单_v1.json").read_text(encoding="utf-8"))
    if len(files) != 1 or files[0].get("filename") != "data.rar":
        raise AuditBlocked("官方附件数量或名称漂移")
    detail = files[0].get("content_details", {})
    expected = FROZEN_FILES["data.rar"]
    if (detail.get("size"), detail.get("sha256_hash")) != expected[:2]:
        raise AuditBlocked("官方 API 中附件大小或 SHA256 漂移")
    return rows


def _finite(value: str) -> float | None:
    try:
        number = float(value.strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read_text_pairs(path: Path) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8-sig", errors="strict").splitlines(), start=1
    ):
        fields = line.replace(",", " ").split()
        if len(fields) < 2:
            raise AuditBlocked(f"二列曲线格式漂移：{path} 第{line_number}行")
        x, y = _finite(fields[0]), _finite(fields[1])
        if x is None or y is None:
            raise AuditBlocked(f"二列曲线出现非有限值：{path} 第{line_number}行")
        points.append((x, y))
    if not points:
        raise AuditBlocked(f"空曲线：{path}")
    return points


def _read_origin_pairs(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter=";")
        try:
            headers = [cell.strip() for cell in next(reader) if cell.strip()]
        except StopIteration as exc:
            raise AuditBlocked(f"空 Origin 导出：{path}") from exc
        if len(headers) % 2:
            raise AuditBlocked(f"Origin 导出列数不是偶数：{path}")
        columns: list[list[float | None]] = [[] for _ in headers]
        for row in reader:
            for index in range(len(headers)):
                columns[index].append(_finite(row[index]) if index < len(row) else None)

    pairs: list[dict[str, Any]] = []
    for index in range(0, len(headers), 2):
        points = [
            (x, y)
            for x, y in zip(columns[index], columns[index + 1], strict=True)
            if x is not None and y is not None
        ]
        if not points:
            raise AuditBlocked(f"Origin 导出存在空列对：{path} {headers[index]}/{headers[index + 1]}")
        pairs.append(
            {
                "source_pair": f"{headers[index]}/{headers[index + 1]}",
                "points": points,
            }
        )
    return pairs


def _stats(points: list[tuple[float, float]]) -> dict[str, float | int]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    maximum_index = max(range(len(points)), key=lambda index: ys[index])
    return {
        "point_count": len(points),
        "x_min": min(xs),
        "x_max": max(xs),
        "y_min": min(ys),
        "y_max": max(ys),
        "x_at_y_max": xs[maximum_index],
    }


def _descending_crossing(points: list[tuple[float, float]], target_y: float) -> float | None:
    ordered = sorted(points)
    for (x0, y0), (x1, y1) in zip(ordered, ordered[1:]):
        if y0 >= target_y >= y1 and y0 != y1:
            return x0 + (target_y - y0) * (x1 - x0) / (y1 - y0)
    return None


def _nearest_y(points: list[tuple[float, float]], target_x: float) -> float:
    return min(points, key=lambda point: abs(point[0] - target_x))[1]


def _base_curve(**values: Any) -> dict[str, Any]:
    row = {column: "" for column in CURVE_COLUMNS}
    row.update(
        {
            "data_origin": "experimental",
            "gold_layer": "Gold-E",
            "curve_points_are_independent_samples": False,
        }
    )
    row.update(values)
    return row


def parse_tensile() -> list[dict[str, Any]]:
    pairs = _read_origin_pairs(EXPORT_DIR / "拉伸.opj.1.dat")
    if len(pairs) != 6:
        raise AuditBlocked(f"拉伸列对数漂移：{len(pairs)}")
    rows: list[dict[str, Any]] = []
    for code, pair in zip(FORMULATION_CODES, pairs, strict=True):
        stats = _stats(pair["points"])
        rows.append(
            _base_curve(
                curve_id=f"mendeley_bvv43yk29c_v1_tensile_{code}",
                formulation_id=code,
                material_role="final_polymer",
                modality="tensile_stress_strain",
                source_file="解压原件/Tensile/tensile.opj",
                source_pair=pair["source_pair"],
                **stats,
                x_name="engineering_strain",
                x_unit="%",
                y_name="engineering_stress",
                y_unit="MPa",
                mapping_status="inferred_by_six_pair_order_and_article_range",
                unit_status="inferred_from_article_and_curve_scale",
                replicate_status="single_curve_no_replicate_identifier",
                record_granularity="within_formulation_curve_point",
                gold_admission_status="admitted_reference",
                future_weight_ceiling=0.60,
                split_group=f"doi:{DOI}|{code}",
                family_leakage_group=f"doi:{DOI}|phcu_composition_series",
                notes=(
                    "Origin列名、单位和注释为空；六列对按PHCU10/20/30/40/50/70顺序推断。"
                    "y最大值可作曲线派生峰值应力，x最大值或峰值处x不得未经断裂判据直接冒充论文断裂伸长率。"
                ),
            )
        )
    return rows


def parse_tga() -> list[dict[str, Any]]:
    pairs = _read_origin_pairs(EXPORT_DIR / "TGA.opj.1.dat")
    if len(pairs) != 6:
        raise AuditBlocked(f"TGA列对数漂移：{len(pairs)}")
    rows: list[dict[str, Any]] = []
    for code, pair in zip(FORMULATION_CODES, pairs, strict=True):
        points = pair["points"]
        rows.append(
            _base_curve(
                curve_id=f"mendeley_bvv43yk29c_v1_tga_{code}",
                formulation_id=code,
                material_role="final_polymer",
                modality="thermogravimetric_mass_curve",
                source_file="解压原件/TGA/TGA.opj",
                source_pair=pair["source_pair"],
                **_stats(points),
                x_name="temperature",
                x_unit="°C",
                y_name="remaining_mass",
                y_unit="%",
                temperature_at_5_percent_loss_c=_descending_crossing(points, 95.0),
                temperature_at_10_percent_loss_c=_descending_crossing(points, 90.0),
                temperature_at_50_percent_loss_c=_descending_crossing(points, 50.0),
                residue_percent_near_600_c=_nearest_y(points, 600.0),
                mapping_status="inferred_by_six_pair_order_and_monotonic_series",
                unit_status="inferred_from_curve_scale_and_method",
                replicate_status="single_curve_no_replicate_identifier",
                record_granularity="within_formulation_curve_point",
                gold_admission_status="admitted_reference",
                future_weight_ceiling=0.55,
                split_group=f"doi:{DOI}|{code}",
                family_leakage_group=f"doi:{DOI}|phcu_composition_series",
                notes="T5/T10/T50及600°C残余质量均由作者曲线复算，不是额外独立实验记录。",
            )
        )
    return rows


def parse_xrd() -> list[dict[str, Any]]:
    pairs = _read_origin_pairs(EXPORT_DIR / "XRD.opj.1.dat")
    if len(pairs) != 7:
        raise AuditBlocked(f"XRD列对数漂移：{len(pairs)}")
    return [
        _base_curve(
            curve_id=f"mendeley_bvv43yk29c_v1_xrd_curve_{index:02d}",
            formulation_id="",
            material_role="final_or_precursor_unresolved",
            modality="wide_angle_xray_diffraction",
            source_file="解压原件/XRD/XRD.opj",
            source_pair=pair["source_pair"],
            **_stats(pair["points"]),
            x_name="two_theta",
            x_unit="degree",
            y_name="intensity",
            y_unit="a.u.",
            mapping_status="unresolved_opj_has_no_curve_labels",
            unit_status="axis_semantics_inferred_no_embedded_unit",
            replicate_status="single_curve_slot_no_replicate_identifier",
            record_granularity="diffraction_curve_point",
            gold_admission_status="conditional_reference",
            future_weight_ceiling=0.20,
            split_group=f"doi:{DOI}|xrd_unresolved_series",
            family_leakage_group=f"doi:{DOI}|phcu_composition_series",
            notes="七条WAXD曲线保留为同源辅助任务；未从无标签列序强行分配到具体PHCU配方。",
        )
        for index, pair in enumerate(pairs, start=1)
    ]


def parse_gpc() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for code in FORMULATION_CODES[:-1]:
        relative = f"解压原件/GPC/{code}.arw"
        points = _read_text_pairs(SOURCE_DIR / relative)
        rows.append(
            _base_curve(
                curve_id=f"mendeley_bvv43yk29c_v1_gpc_{code}",
                formulation_id=code,
                material_role="final_polymer",
                modality="gpc_chromatogram",
                source_file=relative,
                source_pair="column_1/column_2",
                **_stats(points),
                x_name="retention_time",
                x_unit="min",
                y_name="detector_response",
                y_unit="a.u.",
                mapping_status="direct_from_filename",
                unit_status="time_scale_inferred_signal_arbitrary",
                replicate_status="single_chromatogram_no_replicate_identifier",
                record_granularity="chromatogram_point",
                gold_admission_status="admitted_reference",
                future_weight_ceiling=0.25,
                split_group=f"doi:{DOI}|{code}",
                family_leakage_group=f"doi:{DOI}|phcu_composition_series",
                notes=(
                    "文件只有时间-响应色谱轨迹，没有标样校准、积分区间或Mn/Mw结果；"
                    "不能由该文件直接生成配方级Mn、Mw或分散系数标签。PHCU70无GPC文件。"
                ),
            )
        )
    return rows


def parse_nmr() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((EXTRACTED_DIR / "NMR").glob("*.txt")):
        stem = path.stem
        points = _read_text_pairs(path)
        if stem.startswith("PHCU30"):
            formulation, role, split = "PHCU30", "final_polymer", f"doi:{DOI}|PHCU30"
        elif stem.startswith("Crosslinked"):
            formulation, role, split = "", "crosslinked_side_product", f"doi:{DOI}|crosslinked_control"
        elif stem.startswith("BHC"):
            formulation, role, split = "", "BHC_precursor", f"doi:{DOI}|BHC_precursor"
        elif stem.startswith("PCDL"):
            formulation, role, split = "", "PCDL_precursor", f"doi:{DOI}|PCDL_precursor"
        else:
            formulation, role, split = "", "PUDL_precursor", f"doi:{DOI}|PUDL_precursor"
        nucleus = "13C" if "13C" in stem else "1H"
        rows.append(
            _base_curve(
                curve_id=f"mendeley_bvv43yk29c_v1_nmr_{re.sub(r'[^A-Za-z0-9]+', '_', stem)}",
                formulation_id=formulation,
                material_role=role,
                modality=f"nmr_{nucleus}",
                source_file=path.relative_to(SOURCE_DIR).as_posix(),
                source_pair="chemical_shift/intensity",
                **_stats(points),
                x_name="chemical_shift",
                x_unit="ppm",
                y_name="intensity",
                y_unit="a.u.",
                mapping_status="direct_from_filename",
                unit_status="axis_semantics_from_filename_and_spectral_scale",
                replicate_status="single_spectrum_no_replicate_identifier",
                record_granularity="spectrum_point",
                gold_admission_status="admitted_reference",
                future_weight_ceiling=0.20,
                split_group=split,
                family_leakage_group=f"doi:{DOI}|phcu_synthesis_family",
                notes="结构确认/辅助表征曲线；光谱点不是独立配方或独立实验重复。",
            )
        )
    return rows


def _dsc_header(path: Path) -> dict[str, Any]:
    text = path.read_bytes().decode("utf-16le", errors="ignore").split("\x0c", 1)[0]
    lines = text.lstrip("\ufeff").replace("\r", "").split("\n")
    result: dict[str, Any] = {"methods": [], "signals": [], "gas": []}
    for line in lines:
        if line.startswith("File "):
            result["internal_file"] = line[5:]
        elif line.startswith("Sample "):
            result["sample"] = line[7:]
        elif line.startswith("Size "):
            result["size"] = line[5:]
        elif line.startswith("Instrument "):
            result["instrument"] = line[11:]
        elif line.startswith("Date "):
            result["date"] = line[5:]
        elif line.startswith("Time "):
            result["time"] = line[5:]
        elif line.startswith("OrgMethod "):
            result["methods"].append(line[10:])
        elif line.startswith("Sig"):
            result["signals"].append(line)
        elif line.startswith("Xcomment Gas"):
            result["gas"].append(line[9:])
    required = ("internal_file", "size", "instrument", "date", "time")
    if any(key not in result for key in required):
        raise AuditBlocked(f"DSC头字段不完整：{path}")
    return result


def _internal_phcu_code(internal_file: str) -> str:
    match = re.search(r"phcu[-_ ]?(10|20|30|40|50|70|85)(?!\d)", internal_file, re.I)
    return f"PHCU{match.group(1)}" if match else ""


def parse_dsc() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    headers: dict[str, dict[str, Any]] = {}
    for code in FORMULATION_CODES:
        path = EXTRACTED_DIR / "DSC" / f"{code}.001"
        header = _dsc_header(path)
        headers[code] = header
        internal_code = _internal_phcu_code(str(header["internal_file"]))
        if internal_code and internal_code != code:
            mapping = "external_internal_conflict"
        elif internal_code == code:
            mapping = "external_internal_match"
        else:
            mapping = "internal_code_unresolved"
        rows.append(
            _base_curve(
                curve_id=f"mendeley_bvv43yk29c_v1_dsc_{code}",
                formulation_id=code,
                material_role="final_polymer_mapping_unverified",
                modality="differential_scanning_calorimetry_binary_raw",
                source_file=path.relative_to(SOURCE_DIR).as_posix(),
                source_pair="TA_Q2000_binary_signals",
                point_count="",
                x_name="temperature",
                x_unit="°C",
                y_name="heat_flow",
                y_unit="mW",
                mapping_status=mapping,
                unit_status="embedded_in_TA_header",
                replicate_status="single_binary_run_no_replicate_identifier",
                record_granularity="binary_instrument_curve",
                gold_admission_status="evidence_only",
                future_weight_ceiling=0.0,
                split_group=f"doi:{DOI}|{code}",
                family_leakage_group=f"doi:{DOI}|phcu_composition_series",
                notes=(
                    f"external={code}.001; internal={header['internal_file']}; sample={header.get('sample', '')}; "
                    f"size={header['size']}; instrument={header['instrument']}; date={header['date']} {header['time']}. "
                    "原始二进制保留，未伪造曲线点或Tm。"
                ),
            )
        )
    return rows, headers


def parse_ftir() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((EXTRACTED_DIR / "FTIR").glob("*.SPA")):
        stem = path.stem
        role = (
            "BHC_precursor" if stem == "BHC" else
            "crosslinked_side_product" if stem.startswith("Crosslinked") else
            "final_polymer_generic_unmapped"
        )
        rows.append(
            _base_curve(
                curve_id=f"mendeley_bvv43yk29c_v1_ftir_{re.sub(r'[^A-Za-z0-9]+', '_', stem)}",
                formulation_id="",
                material_role=role,
                modality="ftir_binary_raw",
                source_file=path.relative_to(SOURCE_DIR).as_posix(),
                source_pair="Thermo_SPA_binary",
                point_count="",
                x_name="wavenumber",
                x_unit="cm^-1",
                y_name="response",
                y_unit="unresolved",
                mapping_status="direct_material_role_from_filename_specific_formulation_unresolved",
                unit_status="binary_not_materialized",
                replicate_status="single_binary_spectrum_no_replicate_identifier",
                record_granularity="binary_instrument_spectrum",
                gold_admission_status="evidence_only",
                future_weight_ceiling=0.0,
                split_group=f"doi:{DOI}|ftir_{stem}",
                family_leakage_group=f"doi:{DOI}|phcu_synthesis_family",
                notes="Thermo SPA官方原始文件保留；当前不从专有二进制猜测数值点。",
            )
        )
    return rows


def build_formulations(
    curves: list[dict[str, Any]], dsc_headers: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    tensile = {row["formulation_id"]: row for row in curves if row["modality"] == "tensile_stress_strain"}
    tga = {row["formulation_id"]: row for row in curves if row["modality"] == "thermogravimetric_mass_curve"}
    gpc_codes = {row["formulation_id"] for row in curves if row["modality"] == "gpc_chromatogram"}
    rows: list[dict[str, Any]] = []
    for code in FORMULATION_CODES:
        tensile_row, tga_row = tensile[code], tga[code]
        internal_file = str(dsc_headers[code]["internal_file"])
        internal_code = _internal_phcu_code(internal_file)
        if internal_code and internal_code != code:
            dsc_mapping = "external_internal_conflict"
        elif internal_code == code:
            dsc_mapping = "external_internal_match"
        else:
            dsc_mapping = "internal_code_unresolved"
        rows.append(
            {
                "formulation_id": code,
                "family_name": "poly(hexamethylene carbonate)-co-poly(hexamethylene urethane) segmental block copolymer",
                "hu_mol_percent": HU_MOL_PERCENT[code],
                "hu_semantics_source": f"supporting_primary_article:{SUPPORTING_ARTICLE_DOI}",
                "exact_polymer_smiles": "",
                "polymer_structure_resolution": "family_repeat_motifs_and_HU_fraction_resolved; exact_block_lengths_endgroups_feed_recipe_unresolved",
                "known_monomer_smiles": "dimethyl_carbonate=COC(=O)OC|1,6-hexanediol=OCCCCCCO|1,6-hexanediamine=NCCCCCCN",
                "synthesis_route": "BHC_from_HDA_and_DMC; PUDL_from_BHC_and_excess_HDO; PCDL_from_DMC_and_HDO; PUDL_PCDL_polycondensation",
                "data_origin": "experimental",
                "gold_layer": "Gold-E",
                "gold_admission_status": "admitted_reference",
                "source_reliability": "R1_official_repository_plus_primary_article",
                "composition_series_trainable": True,
                "exact_structure_property_trainable": False,
                "reported_family_mn_max_g_mol": 60_000,
                "formulation_mn_g_mol": "",
                "formulation_mw_g_mol": "",
                "mn_mw_status": "unresolved_raw_GPC_has_no_calibration_or_integrated_Mn_Mw",
                "reported_family_melting_range_c": "60-137",
                "reported_family_tensile_range_mpa": "17-29",
                "reported_family_elongation_range_percent": "26-665",
                "tensile_curve_id": tensile_row["curve_id"],
                "tensile_point_count": tensile_row["point_count"],
                "derived_peak_stress_mpa": tensile_row["y_max"],
                "derived_strain_at_peak_stress_percent": tensile_row["x_at_y_max"],
                "curve_max_strain_percent": tensile_row["x_max"],
                "tga_curve_id": tga_row["curve_id"],
                "tga_point_count": tga_row["point_count"],
                "derived_t5_loss_c": tga_row["temperature_at_5_percent_loss_c"],
                "derived_t10_loss_c": tga_row["temperature_at_10_percent_loss_c"],
                "derived_t50_loss_c": tga_row["temperature_at_50_percent_loss_c"],
                "gpc_raw_curve_available": code in gpc_codes,
                "dsc_external_file": f"解压原件/DSC/{code}.001",
                "dsc_internal_file": internal_file,
                "dsc_mapping_status": dsc_mapping,
                "replicate_status": "no_explicit_replicate_identifier_in_curve_files",
                "split_group": f"doi:{DOI}|{code}",
                "family_leakage_group": f"doi:{DOI}|phcu_composition_series",
                "curve_points_are_independent_samples": False,
                "future_weight_ceiling": 0.60,
                "notes": (
                    "六个配方是独立材料单位；拉伸/TGA列序映射为推断。"
                    "配方级Mn/Mw与精确链结构未闭合，不能将family Mn上限或原始色谱轨迹复制为六个确定标签。"
                ),
            }
        )
    return rows


def build_summary(
    curves: list[dict[str, Any]], formulations: list[dict[str, Any]], files: list[dict[str, Any]]
) -> dict[str, Any]:
    numeric_curves = [row for row in curves if isinstance(row["point_count"], int)]
    counts_by_modality: dict[str, dict[str, int]] = {}
    for modality in sorted({row["modality"] for row in curves}):
        subset = [row for row in curves if row["modality"] == modality]
        counts_by_modality[modality] = {
            "curves": len(subset),
            "numeric_points": sum(
                int(row["point_count"]) for row in subset if isinstance(row["point_count"], int)
            ),
        }
    dsc_conflicts = [
        {
            "external": row["dsc_external_file"],
            "internal": row["dsc_internal_file"],
            "status": row["dsc_mapping_status"],
        }
        for row in formulations
        if row["dsc_mapping_status"] != "external_internal_match"
    ]
    return {
        "audit_version": AUDIT_VERSION,
        "source": {
            "title": "A Solvent-free Route to Non-isocyanate Poly(carbonate urethane) with High Molecular Weight and Competitive Mechanical Properties",
            "doi": DOI,
            "version": 1,
            "published": "2018-06-06",
            "contributor": "Ziyun Shen",
            "institution": "Institute of Chemistry Chinese Academy of Sciences",
            "license": LICENSE,
            "dataset_url": DATASET_URL,
            "metadata_url": METADATA_URL,
            "files_url": FILES_URL,
            "direct_download_url": DIRECT_DOWNLOAD_URL,
            "source_reliability": "R1",
        },
        "linked_article": {
            "doi": ARTICLE_DOI,
            "journal": "European Polymer Journal",
            "volume": 107,
            "year": 2018,
            "pages": "258-266",
            "url": ARTICLE_URL,
        },
        "supporting_composition_article": {
            "doi": SUPPORTING_ARTICLE_DOI,
            "journal": "Polymer",
            "volume": 222,
            "year": 2021,
            "article_number": "123675",
            "url": SUPPORTING_ARTICLE_URL,
            "evidence_used": "PHCU-number denotes HU mol%; PUDL Mn 740 and PCDL Mn 1900 g/mol",
            "not_treated_as_direct_measurement_in_2018_archive": True,
        },
        "counts": {
            "official_attachments": 1,
            "archive_members": 23,
            "frozen_local_source_files": len(files),
            "independent_final_formulations": len(formulations),
            "final_polymer_families": 1,
            "curve_records_total": len(curves),
            "numeric_curve_records": len(numeric_curves),
            "binary_unmaterialized_curve_records": len(curves) - len(numeric_curves),
            "numeric_curve_points_total": sum(int(row["point_count"]) for row in numeric_curves),
            "curve_points_counted_as_independent_samples": 0,
            "explicit_replicate_groups": 0,
            "simulation_records": 0,
        },
        "counts_by_modality": counts_by_modality,
        "composition_and_structure": {
            "formulation_codes": list(FORMULATION_CODES),
            "hu_mol_percent": [HU_MOL_PERCENT[code] for code in FORMULATION_CODES],
            "exact_polymer_smiles_available": False,
            "known_monomer_smiles_available": True,
            "exact_feed_recipe_available": False,
            "exact_block_lengths_and_endgroups_available": False,
            "composition_series_trainable": True,
            "exact_structure_property_trainable": False,
        },
        "molecular_weight_semantics": {
            "reported_family_mn_max_g_mol": 60_000,
            "per_formulation_mn_available": False,
            "per_formulation_mw_available": False,
            "gpc_raw_chromatograms": 5,
            "gpc_calibration_or_integrated_results_available": False,
            "supporting_article_precursor_mn_g_mol": {"PUDL": 740, "PCDL": 1900},
        },
        "dsc_label_audit": {
            "external_internal_matches": sum(row["dsc_mapping_status"] == "external_internal_match" for row in formulations),
            "external_internal_conflicts": sum(row["dsc_mapping_status"] == "external_internal_conflict" for row in formulations),
            "internal_code_unresolved": sum(row["dsc_mapping_status"] == "internal_code_unresolved" for row in formulations),
            "details": dsc_conflicts,
            "direct_supervision_blocked_until_reconciled": True,
        },
        "scientific_classification": {
            "gold_layer": "Gold-E",
            "gold_c": False,
            "admission_mode": "multi_fidelity_reference",
            "independent_sample_unit": "formulation",
            "valid_tasks": [
                "composition_series_to_full_stress_strain_curve",
                "composition_series_to_TGA_curve_or_curve_derived_targets",
                "auxiliary_GPC_NMR_XRD_representation_learning",
                "family_level_nonisocyanate_TPU_candidate_prior",
            ],
            "blocked_or_conditional_tasks": [
                "exact_polymer_SMILES_to_property",
                "per_formulation_Mn_or_Mw_supervision",
                "DSC_scalar_supervision_before_label_reconciliation",
                "random_curve_point_train_test_split",
            ],
            "weight_ceilings": {
                "tensile_curve": 0.60,
                "TGA_curve": 0.55,
                "GPC_auxiliary": 0.25,
                "NMR_or_unmapped_XRD_auxiliary": 0.20,
                "unmaterialized_or_label_conflicted_binary": 0.0,
            },
        },
        "leakage_policy": {
            "point_level_split_forbidden": True,
            "minimum_group": f"doi:{DOI}|formulation_id",
            "strict_family_novelty_group": f"doi:{DOI}|phcu_composition_series",
            "precursors_and_final_polymers_share_synthesis_family_group": True,
            "same_curve_derived_scalars_share_curve_group": True,
        },
        "limitations": [
            "Origin拉伸、TGA、XRD的列名、单位和注释为空；拉伸/TGA配方映射是有文献范围支持的顺序推断。",
            "六配方没有可识别的独立重复；一条曲线中的点是同一试验轨迹，不是独立样本。",
            "XRD七条曲线的具体材料标签未在OPJ中保存，不能强行映射。",
            "DSC外部文件名与内部仪器File字段存在三项明确冲突和一项无法解析。",
            "GPC仅有五条原始色谱轨迹，缺少校准和积分，不能直接导出配方级Mn/Mw。",
            "官方压缩包不含每个配方的精确投料、链长、端基或唯一聚合物SMILES。",
        ],
    }


def _tsv(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in columns})
    return stream.getvalue().encode("utf-8")


def _json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _report(summary: dict[str, Any], formulations: list[dict[str, Any]]) -> bytes:
    counts = summary["counts"]
    modality = summary["counts_by_modality"]
    rows = [
        "# 非异氰酸酯 PHCU 数据集只读审计报告",
        "",
        "## 审计结论",
        "",
        f"该来源可作为 **Gold-E 多保真实验参考包** 纳入数据库，而不是 Gold-C。独立材料单位为 {counts['independent_final_formulations']} 个 PHCU 配方；{counts['numeric_curve_points_total']:,} 个已恢复数值点全部是配方内曲线/光谱采样点，不能计作独立材料样本。数据来自 Mendeley Data 官方 v1 附件，许可为 CC BY 4.0[1,4]。",
        "",
        "拉伸和 TGA 各有 6 条曲线，列序及曲线范围支持按 PHCU10、20、30、40、50、70 对应，但 Origin 文件未保存列名与单位，因此清单明确标为顺序推断。GPC、NMR、XRD 保留作辅助任务。DSC 因文件外部标签和仪器内部 File 字段冲突，当前仅进证据层。",
        "",
        "## 数量与粒度",
        "",
        "| 项目 | 数量 | 独立样本含义 |",
        "|---|---:|---|",
        f"| 官方附件 | {counts['official_attachments']} | 一个 data.rar |",
        f"| 压缩包原始文件 | {counts['archive_members']} | 23 个仪器/数据文件，不等于 23 个配方 |",
        f"| 最终聚合物配方 | {counts['independent_final_formulations']} | 唯一可用于材料级统计的单位 |",
        f"| 已恢复数值曲线 | {counts['numeric_curve_records']} | 同一配方可有多种表征 |",
        f"| 已恢复数值点 | {counts['numeric_curve_points_total']:,} | 全部为曲线内相关点 |",
        f"| 可识别实验重复组 | {counts['explicit_replicate_groups']} | 原件未提供重复编号 |",
        "",
        "| 模态 | 曲线数 | 数值点 |",
        "|---|---:|---:|",
    ]
    for name, values in modality.items():
        rows.append(f"| {name} | {values['curves']} | {values['numeric_points']:,} |")
    rows.extend(
        [
            "",
            "## 六个配方可用字段",
            "",
            "PHCU 编号按同体系后续原始论文解释为 hexamethylene urethane（HU）摩尔含量；该论文还给出 PUDL 与 PCDL 的 Mn 分别为 740 与 1900 g/mol[3]。这些值属于辅助文献证据，不冒充 2018 压缩包中的逐配方直接测量。",
            "",
            "| 配方 | HU/mol% | 拉伸点数 | 峰值应力/MPa（曲线派生） | 峰值处应变/% | T5/°C（曲线派生） | DSC标签 |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in formulations:
        rows.append(
            f"| {row['formulation_id']} | {row['hu_mol_percent']} | {row['tensile_point_count']} | "
            f"{float(row['derived_peak_stress_mpa']):.3f} | "
            f"{float(row['derived_strain_at_peak_stress_percent']):.3f} | "
            f"{float(row['derived_t5_loss_c']):.3f} | {row['dsc_mapping_status']} |"
        )
    rows.extend(
        [
            "",
            "结构层面已知 DMC、1,6-己二醇、1,6-己二胺三种原料及 PUDL/PCDL 聚缩合路线[2]，并可保存这些单体 SMILES；但逐配方精确投料、嵌段长度、端基和唯一聚合物 SMILES 未闭合。因此可做 HU 组成系列到曲线的学习，不应直接作为“精确聚合物 SMILES → 性能”的高权重监督样本。",
            "",
            "## 分子量与性能字段边界",
            "",
            "官方元数据和论文只支持该系列 Mn 最高 60,000 g/mol、熔点 60–137 °C、拉伸强度 17–29 MPa、断裂伸长率 26–665%[1,2]。五个 `.arw` 文件是原始 GPC 时间—响应轨迹，没有标样校准、积分区间或作者输出的逐配方 Mn/Mw，因此 `formulation_mn_g_mol` 与 `formulation_mw_g_mol` 保持空值。",
            "",
            "拉伸表中的峰值应力、峰值处应变和曲线最大应变均来自作者曲线复算。最大应变或峰值处应变不自动等同于论文定义的断裂伸长率。TGA 的 T5、T10、T50 和约 600 °C 残余质量同样是曲线派生量，与原始曲线共享同一泄漏组。",
            "",
            "## DSC 标签冲突",
            "",
            "- `PHCU10.001` 内部 File 指向 `...PHCU30...001`。",
            "- `PHCU20.001` 内部 File 指向 `...phcu40.001`。",
            "- `PHCU70.001` 内部 File 指向 `...PHCU85...001`。",
            "- `PHCU40.001` 内部文件名为 `PHCU201710MOL.001`，无法解析成冻结的六配方编号。",
            "- `PHCU30.001` 与 `PHCU50.001` 的内外编号一致。",
            "",
            "因此 DSC 原件完整保留，但在标签核对或正文图表逐一对齐前，数值监督权重为 0；这不是删除数据，而是把它放在可追溯证据层。",
            "",
            "## 建议任务、权重与泄漏边界",
            "",
            "- 拉伸全曲线：Gold-E 参考层，建议权重上限 0.60。",
            "- TGA 全曲线及同曲线派生量：Gold-E 参考层，建议权重上限 0.55。",
            "- GPC 色谱、NMR、未映射 XRD：辅助表征/表示学习，建议权重上限 0.20–0.25。",
            "- 未解码 FTIR 与标签冲突 DSC：保留证据，当前监督权重 0。",
            f"- 最低划分单位为 `doi:{DOI}|配方编号`；同一曲线的点和派生标量必须同折。",
            f"- 若评价对新化学体系的外推，六个配方及前驱体应整体置于 `doi:{DOI}|phcu_composition_series` 家族泄漏组，不能随机拆点。",
            "",
            "## 官方入口与参考文献",
            "",
            f"1. Shen, Z. *A Solvent-free Route to Non-isocyanate Poly(carbonate urethane) with High Molecular Weight and Competitive Mechanical Properties* (Version 1) [Data set]. Mendeley Data, 2018. DOI: [{DOI}](https://doi.org/{DOI}). 官方[元数据]({METADATA_URL})、[文件清单]({FILES_URL})、[附件下载]({DIRECT_DOWNLOAD_URL})。",
            f"2. Shen, Z.; Zhang, J.; Zhu, W.; Zheng, L.; Li, C.; Xiao, Y.; Liu, J.; Wu, S.; Zhang, B. A solvent-free route to non-isocyanate poly(carbonate urethane) with high molecular weight and competitive mechanical properties. *European Polymer Journal* **2018**, *107*, 258–266. DOI: [{ARTICLE_DOI}](https://doi.org/{ARTICLE_DOI}).",
            f"3. Zhang, C.; Pérez-Camargo, R. A.; Zheng, L.; Zhao, Y.; Liu, G.; Wang, L.; Wang, D. Crystallization of poly(hexamethylene carbonate)-co-poly(hexamethylene urethane) segmental block copolymers: From single to double crystalline phases. *Polymer* **2021**, *222*, 123675. DOI: [{SUPPORTING_ARTICLE_DOI}](https://doi.org/{SUPPORTING_ARTICLE_DOI}).",
            "4. Creative Commons. *Attribution 4.0 International (CC BY 4.0)*. https://creativecommons.org/licenses/by/4.0/.",
            "",
            "本报告是只读审计结果；数值清单、配方清单和文件 SHA256 见同目录 TSV/JSON。",
        ]
    )
    return ("\n".join(rows) + "\n").encode("utf-8")


def atomic_write(path: Path, payload: bytes) -> None:
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
        temporary.unlink(missing_ok=True)


def run_audit(*, write_outputs: bool = True) -> dict[str, Any]:
    files = verify_files()
    dsc_curves, dsc_headers = parse_dsc()
    curves = (
        parse_tensile()
        + parse_tga()
        + parse_xrd()
        + parse_gpc()
        + parse_nmr()
        + dsc_curves
        + parse_ftir()
    )
    formulations = build_formulations(curves, dsc_headers)
    summary = build_summary(curves, formulations, files)
    outputs = {
        "内容审计摘要.json": _json(summary),
        "曲线审计清单.tsv": _tsv(curves, CURVE_COLUMNS),
        "配方审计清单.tsv": _tsv(formulations, FORMULATION_COLUMNS),
        "文件校验清单.tsv": _tsv(files, FILE_COLUMNS),
        "只读审计报告.md": _report(summary, formulations),
    }
    if set(outputs) != set(OUTPUT_NAMES):
        raise AuditBlocked("输出集合漂移")
    if write_outputs:
        for name, payload in outputs.items():
            atomic_write(SOURCE_DIR / name, payload)
    return {
        "summary": summary,
        "curves": curves,
        "formulations": formulations,
        "files": files,
        "outputs": outputs,
    }


if __name__ == "__main__":
    result = run_audit(write_outputs=True)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
