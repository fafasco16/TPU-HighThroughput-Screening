"""审计并物化多组分聚氨酯 DFT-AA-MD-反应性 CG 补充数据。

数据来自 ACS Figshare 上固定 DOI 的 Supporting Information。源 PDF 的三张
数值表经人工视觉复核，并由 PDF 字节哈希、页数、表题和逐行文本锚点共同
锁定。本模块只物化表格中的源生数值，不数字化图线，也不把粗粒化粒子、
初始盒尺寸或敏感性运行误计为新的材料体系。

Table S1 给出 10 个 CG 体系的组成；Table S3 给出同一批体系的 Rg、周期盒
边长和密度；Table S2 给出 R1-1-H2 的 4 个初始盒敏感性运行。Table S2 中
200 Å 初始盒对应 Table S3 的 R1-1-H2，因此三个重复结果只保留一次。

原表把明显属于质量密度的单位排为 ``g·mol-1``。本模块依据量纲、数值随
盒边长三次方缩放以及正文语义规范化为 ``g/cm^3``，但全部保留为条件参考，
不把这一推断伪装成来源明示单位。所有训练权重与数据划分均保持为空。
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from pypdf import PdfReader


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = (
    PROJECT_ROOT
    / "数据/原始"
    / "外部数据"
    / "新增开放数据"
    / "第十五批计算_多组分PU多尺度模拟"
)
PDF_PATH = SOURCE_DIR / "多组分PU多尺度模拟补充数据.pdf"
FIGSHARE_METADATA_PATH = SOURCE_DIR / "Figshare官方元数据.json"
CROSSREF_METADATA_PATH = SOURCE_DIR / "论文Crossref元数据.json"

OUTPUT_TSV = SOURCE_DIR / "Gold_C_计算观测长表.tsv"
OUTPUT_AUDIT = SOURCE_DIR / "内容审计摘要.json"
OUTPUT_CHECKSUMS = SOURCE_DIR / "文件校验清单.tsv"
OUTPUT_README = SOURCE_DIR / "来源说明.md"

SOURCE_ID = "source_figshare_ma5c03283_si"
SUPPLEMENT_DOI = "10.1021/acs.macromol.5c03283.s001"
ARTICLE_DOI = "10.1021/acs.macromol.5c03283"
CITATION_KEYS = (
    "meng-2026-multiscale-pu-si;"
    "meng-2026-multiscale-pu-paper"
)

PDF_BYTES = 1_861_266
PDF_MD5 = "f203b4ac78b49c95c4264114598118cf"
PDF_SHA256 = "16b5847bcf222e9c54a5b1e566b82c65fa259e9764e90d85be01dda26167833d"
FIGSHARE_METADATA_BYTES = 9_187
FIGSHARE_METADATA_SHA256 = (
    "400e3467ebb67e1130770330804d0f94b10e02e045e3430f03ac2399a939b685"
)
CROSSREF_METADATA_BYTES = 8_459
CROSSREF_METADATA_SHA256 = (
    "31a5e57c095670b965f13889d0db42d2471ef6e5fb7da571d1bab90138141751"
)
EXPECTED_PAGE_COUNT = 17
EXPECTED_MODEL_COUNT = 10
EXPECTED_SIMULATION_RUN_COUNT = 13
EXPECTED_ROW_COUNT = 115

METHOD_FAMILY = "DFT-AA-MD-reactive-CG"
METHOD_DETAIL = (
    "bottom-up multiscale workflow: DFT-informed RESP charges, all-atom MD, "
    "iterative Boltzmann inversion with pressure correction, and reactive "
    "coarse-grained bond formation"
)
FIDELITY_LEVEL = "peer_reviewed_multiscale_reactive_cg_table"
SOURCE_VALIDATION_STATUS = (
    "peer_reviewed_supporting_table_no_raw_trajectory_or_restart_files"
)
LICENSE = "CC BY-NC 4.0"
STRUCTURE_STATUS = (
    "coarse_grained_component_family_only_exact_atomistic_graph_unresolved"
)

GOLD_C_VALUE_COLUMNS = (
    "source_id",
    "source_record_id",
    "observation_id",
    "canonical_structure",
    "system_identity",
    "structure_identity_status",
    "global_structure_family_key",
    "simulation_key",
    "split_group",
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
)


MODEL_CONFIGURATIONS = (
    ("R1-1-H1", 3000, 3000, 1500, 1500, 0.5, 1.0, "0.5 / 1", "1 / 1"),
    ("R1-1-H2", 3000, 3000, 3000, 3000, 1.0, 1.0, "1 / 1", "1 / 1"),
    ("R1-1-H3", 3000, 3000, 4500, 4500, 1.5, 1.0, "1.5 / 1", "1 / 1"),
    ("R1-1-H4", 3000, 3000, 6000, 6000, 2.0, 1.0, "2 / 1", "1 / 1"),
    ("R1-5-H2", 1000, 5000, 1000, 5000, 1.0, 0.2, "1 / 1", "1 / 5"),
    ("R1-3-H2", 1500, 4500, 1500, 4500, 1.0, 1 / 3, "1 / 1", "1 / 3"),
    ("R1-2-H2", 2000, 4000, 2000, 4000, 1.0, 0.5, "1 / 1", "1 / 2"),
    ("R2-1-H2", 4000, 2000, 4000, 2000, 1.0, 2.0, "1 / 1", "2 / 1"),
    ("R3-1-H2", 4500, 1500, 4500, 1500, 1.0, 3.0, "1 / 1", "3 / 1"),
    ("R5-1-H2", 5000, 1000, 5000, 1000, 1.0, 5.0, "1 / 1", "5 / 1"),
)

MODEL_RESULTS = (
    ("R1-1-H1", 123.78, 226.96, 1.15),
    ("R1-1-H2", 126.24, 242.62, 1.09),
    ("R1-1-H3", 132.01, 247.27, 1.17),
    ("R1-1-H4", 135.78, 251.27, 1.25),
    ("R1-5-H2", 141.22, 263.55, 0.86),
    ("R1-3-H2", 130.59, 246.14, 1.05),
    ("R1-2-H2", 127.04, 241.04, 1.11),
    ("R2-1-H2", 127.35, 247.01, 1.03),
    ("R3-1-H2", 128.13, 249.82, 0.99),
    ("R5-1-H2", 129.14, 253.89, 0.94),
)

SENSITIVITY_RESULTS = (
    # initial_box_A, initial_density, final_box_A, final_density, Rg_A,
    # residual_reactive_sites_percent, tensile_strength_MPa
    (100, 15.00, 244.15, 1.11, 111.28, 5.61, 9.97),
    (200, 1.91, 242.62, 1.09, 126.24, 4.73, 9.51),
    (300, 0.57, 241.89, 1.08, 142.76, 6.03, 9.20),
    (450, 0.17, 245.92, 1.04, 115.47, 2.89, 10.08),
)


class AuditBlocked(RuntimeError):
    """冻结输入、表格锚点或确定性数量发生漂移。"""


def _hash(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditBlocked(f"元数据无法读取：{path}") from exc
    if not isinstance(payload, dict):
        raise AuditBlocked(f"元数据顶层不是对象：{path}")
    return payload


def _normalized_page_text(reader: PdfReader, page_number: int) -> str:
    text = reader.pages[page_number - 1].extract_text() or ""
    return " ".join(text.replace("\u00a0", " ").split())


def _source_row_anchors() -> dict[int, tuple[str, ...]]:
    s1_rows = tuple(
        f"{name} {pdms} {pcdl} {urea} {urethane} {hard_text} {pdms_text}"
        for (
            name,
            pdms,
            pcdl,
            urea,
            urethane,
            _hard_ratio,
            _pdms_ratio,
            hard_text,
            pdms_text,
        ) in MODEL_CONFIGURATIONS
    )
    s2_rows = tuple(
        f"{initial_box}3 {initial_density:.2f} {final_box:.2f}3 "
        f"{final_density:.2f} {rg:.2f} {residual:.2f} {strength:.2f}"
        for (
            initial_box,
            initial_density,
            final_box,
            final_density,
            rg,
            residual,
            strength,
        ) in SENSITIVITY_RESULTS
    )
    s3_rows = tuple(
        f"{model} {rg:.2f} {box_side:.2f}3 {density:.2f}"
        for model, rg, box_side, density in MODEL_RESULTS
    )
    return {
        6: (
            "S2.2 Iterative Boltzmann Inversion (IBI) Method",
            "The total potential energy is expressed as",
        ),
        7: (
            "pressure correction term was incorporated into the pairwise potentials",
            "cutoff distance for nonbonded interactions, which was set to 20 Å",
        ),
        9: (
            "Table S1. Details of the configurations of CG systems.",
            *s1_rows,
        ),
        13: (
            "Table S2. Simulation results for different initial configurations of R1-1-H2.",
            *s2_rows,
        ),
        16: (
            "Table S3. Details of the results of CG systems.",
            *s3_rows,
            "The side length of simulation box is over 1.5 times of the Rg",
        ),
    }


def verify_source() -> dict[str, Any]:
    frozen = (
        (PDF_PATH, PDF_BYTES, PDF_SHA256),
        (
            FIGSHARE_METADATA_PATH,
            FIGSHARE_METADATA_BYTES,
            FIGSHARE_METADATA_SHA256,
        ),
        (
            CROSSREF_METADATA_PATH,
            CROSSREF_METADATA_BYTES,
            CROSSREF_METADATA_SHA256,
        ),
    )
    verified_files: list[dict[str, Any]] = []
    for path, expected_size, expected_sha256 in frozen:
        if not path.is_file():
            raise AuditBlocked(f"缺少冻结输入：{path}")
        size = path.stat().st_size
        sha256 = _hash(path, "sha256")
        if (size, sha256) != (expected_size, expected_sha256):
            raise AuditBlocked(
                f"冻结输入漂移：{path.name}; bytes={size}; sha256={sha256}"
            )
        verified_files.append(
            {"file": path.name, "bytes": size, "sha256": sha256}
        )

    if _hash(PDF_PATH, "md5") != PDF_MD5:
        raise AuditBlocked("PDF MD5 与 Figshare 文件清单不一致")

    reader = PdfReader(PDF_PATH)
    if len(reader.pages) != EXPECTED_PAGE_COUNT:
        raise AuditBlocked(
            f"PDF 页数漂移：expected={EXPECTED_PAGE_COUNT}, actual={len(reader.pages)}"
        )
    for page_number, anchors in _source_row_anchors().items():
        page_text = _normalized_page_text(reader, page_number)
        for anchor in anchors:
            normalized_anchor = " ".join(anchor.split())
            if normalized_anchor not in page_text:
                raise AuditBlocked(
                    f"PDF 表格锚点漂移：page={page_number}; anchor={anchor!r}"
                )

    figshare = _read_json(FIGSHARE_METADATA_PATH)
    if figshare.get("id") != 31981709 or figshare.get("doi") != SUPPLEMENT_DOI:
        raise AuditBlocked("Figshare ID 或补充材料 DOI 漂移")
    license_payload = figshare.get("license")
    if not isinstance(license_payload, dict) or license_payload.get("name") != LICENSE:
        raise AuditBlocked("Figshare 许可不是 CC BY-NC 4.0")
    files = figshare.get("files")
    if not isinstance(files, list) or len(files) != 1:
        raise AuditBlocked("Figshare 文件清单不是唯一 PDF")
    file_payload = files[0]
    if (
        file_payload.get("name") != "ma5c03283_si_001.pdf"
        or file_payload.get("size") != PDF_BYTES
        or file_payload.get("computed_md5") != PDF_MD5
    ):
        raise AuditBlocked("Figshare 文件名、字节数或 MD5 漂移")

    crossref = _read_json(CROSSREF_METADATA_PATH)
    message = crossref.get("message")
    if not isinstance(message, dict) or message.get("DOI") != ARTICLE_DOI:
        raise AuditBlocked("Crossref 论文 DOI 漂移")
    if message.get("page") != "5057-5070" or message.get("volume") != "59":
        raise AuditBlocked("Crossref 卷页信息漂移")

    return {
        "verified_files": verified_files,
        "pdf_md5": PDF_MD5,
        "pdf_pages": len(reader.pages),
        "figshare_id": figshare["id"],
        "supplement_doi": figshare["doi"],
        "article_doi": message["DOI"],
        "license": license_payload["name"],
        "verification": "matched_frozen_identity_metadata_and_all_table_row_anchors",
    }


def _finite_text(value: float | int) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise AuditBlocked(f"观测值不是有限数：{value!r}")
    return format(number, ".12g")


def _safe_key(value: str) -> str:
    return value.lower().replace("-", "_")


def _base_simulation_key(model: str) -> str:
    if model == "R1-1-H2":
        return "simulation_ma5c03283_r1_1_h2_initial_box_200a"
    return f"simulation_ma5c03283_{_safe_key(model)}_reported"


def _system_identity(model: str) -> str:
    config = next(item for item in MODEL_CONFIGURATIONS if item[0] == model)
    return (
        f"multicomponent polyurethane CG model {model}; "
        f"PDMS={config[1]}, PCDL={config[2]}, urea={config[3]}, "
        f"urethane={config[4]} coarse-grained sites; "
        f"hard/soft={config[7]}; PDMS/PCDL={config[8]}"
    )


def _row(
    *,
    table: str,
    page: int,
    record: str,
    model: str,
    simulation_key: str,
    property_name: str,
    value: float | int,
    unit: str,
    unit_status: str,
    record_role: str,
    row_label: str,
    conditional: bool = False,
) -> dict[str, str]:
    safe_property = property_name.replace("/", "_")
    observation_id = (
        f"ma5c03283:{table.lower()}:{_safe_key(record)}:{safe_property}"
    )
    admission = "conditional_reference" if conditional else "admitted_reference"
    family_key = f"family_multicomponent_pu_{_safe_key(model)}"
    if record_role.endswith("input_descriptor"):
        potential_weight_ceiling = "0.00"
    elif conditional:
        potential_weight_ceiling = "0.15"
    else:
        potential_weight_ceiling = "0.30"
    return {
        "source_id": SOURCE_ID,
        "source_record_id": f"ma5c03283:{table}:{record}",
        "observation_id": observation_id,
        "canonical_structure": "",
        "system_identity": _system_identity(model),
        "structure_identity_status": STRUCTURE_STATUS,
        "global_structure_family_key": family_key,
        "simulation_key": simulation_key,
        "split_group": family_key,
        "property_name": property_name,
        "value": _finite_text(value),
        "unit": unit,
        "unit_status": unit_status,
        "method_family": METHOD_FAMILY,
        "method_detail": METHOD_DETAIL,
        "fidelity_level": FIDELITY_LEVEL,
        "temp": "",
        "press": "",
        "gold_admission_status": admission,
        "property_admission_status": admission,
        "source_validation_status": SOURCE_VALIDATION_STATUS,
        "record_role": record_role,
        "potential_weight_ceiling": potential_weight_ceiling,
        "current_weight_materialized": "false",
        "training_weight": "",
        "source_locator": (
            f"多组分PU多尺度模拟补充数据.pdf#page={page}&table={table}"
            f"&row={row_label}"
        ),
        "citation_keys": CITATION_KEYS,
    }


def _table_s1_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    properties = (
        ("coarse_grained_PDMS_site_count", 1, "count"),
        ("coarse_grained_PCDL_site_count", 2, "count"),
        ("coarse_grained_urea_site_count", 3, "count"),
        ("coarse_grained_urethane_site_count", 4, "count"),
        ("hard_to_soft_segment_ratio", 5, "dimensionless"),
        ("PDMS_to_PCDL_ratio", 6, "dimensionless"),
    )
    for config in MODEL_CONFIGURATIONS:
        model = config[0]
        for property_name, index, unit in properties:
            rows.append(
                _row(
                    table="S1",
                    page=9,
                    record=model,
                    model=model,
                    simulation_key=_base_simulation_key(model),
                    property_name=property_name,
                    value=config[index],
                    unit=unit,
                    unit_status="resolved_source_native",
                    record_role="simulation_input_descriptor",
                    row_label=model,
                )
            )
    return rows


def _table_s3_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for model, rg, box_side, density in MODEL_RESULTS:
        common = {
            "table": "S3",
            "page": 16,
            "record": model,
            "model": model,
            "simulation_key": _base_simulation_key(model),
            "record_role": "simulation_output",
            "row_label": model,
        }
        rows.append(
            _row(
                **common,
                property_name="radius_of_gyration",
                value=rg,
                unit="angstrom",
                unit_status="resolved_source_native",
            )
        )
        rows.append(
            _row(
                **common,
                property_name="cubic_periodic_box_side_length",
                value=box_side,
                unit="angstrom",
                unit_status="resolved_from_cubic_box_notation_and_source_note",
            )
        )
        rows.append(
            _row(
                **common,
                property_name="mass_density",
                value=density,
                unit="g/cm^3",
                unit_status=(
                    "source_unit_label_typographical_error_inferred_g_per_cm3"
                ),
                conditional=True,
            )
        )
    return rows


def _table_s2_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    model = "R1-1-H2"
    for (
        initial_box,
        initial_density,
        final_box,
        final_density,
        rg,
        residual,
        strength,
    ) in SENSITIVITY_RESULTS:
        record = f"{model}-initial-{initial_box}A"
        simulation_key = f"simulation_ma5c03283_r1_1_h2_initial_box_{initial_box}a"
        common = {
            "table": "S2",
            "page": 13,
            "record": record,
            "model": model,
            "simulation_key": simulation_key,
            "row_label": f"initial_box_{initial_box}A",
        }
        rows.append(
            _row(
                **common,
                property_name="initial_cubic_box_side_length",
                value=initial_box,
                unit="angstrom",
                unit_status="resolved_from_cubic_box_notation_and_source_note",
                record_role="sensitivity_input_descriptor",
            )
        )
        rows.append(
            _row(
                **common,
                property_name="initial_mass_density",
                value=initial_density,
                unit="g/cm^3",
                unit_status=(
                    "source_unit_label_typographical_error_inferred_g_per_cm3"
                ),
                record_role="sensitivity_input_descriptor",
                conditional=True,
            )
        )

        # The 200 Å run is the selected R1-1-H2 configuration already reported
        # in Table S3. Avoid duplicating its final box, density and Rg values.
        if initial_box != 200:
            rows.append(
                _row(
                    **common,
                    property_name="final_cubic_box_side_length",
                    value=final_box,
                    unit="angstrom",
                    unit_status=(
                        "resolved_from_cubic_box_notation_and_source_note"
                    ),
                    record_role="sensitivity_output",
                )
            )
            rows.append(
                _row(
                    **common,
                    property_name="final_mass_density",
                    value=final_density,
                    unit="g/cm^3",
                    unit_status=(
                        "source_unit_label_typographical_error_inferred_g_per_cm3"
                    ),
                    record_role="sensitivity_output",
                    conditional=True,
                )
            )
            rows.append(
                _row(
                    **common,
                    property_name="radius_of_gyration",
                    value=rg,
                    unit="angstrom",
                    unit_status="resolved_source_native",
                    record_role="sensitivity_output",
                )
            )

        rows.append(
            _row(
                **common,
                property_name="residual_reactive_sites",
                value=residual,
                unit="percent",
                unit_status="resolved_source_native",
                record_role="sensitivity_output",
            )
        )
        rows.append(
            _row(
                **common,
                property_name="tensile_strength",
                value=strength,
                unit="MPa",
                unit_status="resolved_source_native",
                record_role="sensitivity_output",
            )
        )
    return rows


def build_rows() -> tuple[list[dict[str, str]], dict[str, Any]]:
    verification = verify_source()
    rows = [*_table_s1_rows(), *_table_s3_rows(), *_table_s2_rows()]

    if len(rows) != EXPECTED_ROW_COUNT:
        raise AuditBlocked(
            f"物化行数漂移：expected={EXPECTED_ROW_COUNT}, actual={len(rows)}"
        )
    observation_ids = [row["observation_id"] for row in rows]
    if len(set(observation_ids)) != len(observation_ids):
        raise AuditBlocked("observation_id 不唯一")
    if any(row["training_weight"] for row in rows):
        raise AuditBlocked("本批次禁止提前物化训练权重")

    model_keys = {row["global_structure_family_key"] for row in rows}
    simulation_keys = {row["simulation_key"] for row in rows}
    input_rows = [
        row for row in rows if row["record_role"].endswith("input_descriptor")
    ]
    output_rows = [row for row in rows if row["record_role"].endswith("output")]
    if len(model_keys) != EXPECTED_MODEL_COUNT:
        raise AuditBlocked("材料体系键数量漂移")
    if len(simulation_keys) != EXPECTED_SIMULATION_RUN_COUNT:
        raise AuditBlocked("模拟运行键数量漂移")

    summary: dict[str, Any] = {
        "audit_version": "batch15-multicomponent-pu-multiscale-v1",
        "source_id": SOURCE_ID,
        "supplement_doi": SUPPLEMENT_DOI,
        "article_doi": ARTICLE_DOI,
        "license": LICENSE,
        "record_count": len(rows),
        "numeric_value_count": len(rows),
        "numeric_context_count": len(rows),
        "input_descriptor_count": len(input_rows),
        "performance_output_count": len(output_rows),
        "performance_output_admission_counts": dict(
            sorted(
                Counter(
                    row["gold_admission_status"] for row in output_rows
                ).items()
            )
        ),
        "unique_material_system_count": len(model_keys),
        "unique_simulation_run_count": len(simulation_keys),
        "split_group_count": len({row["split_group"] for row in rows}),
        "record_role_counts": dict(
            sorted(Counter(row["record_role"] for row in rows).items())
        ),
        "admission_counts": dict(
            sorted(Counter(row["gold_admission_status"] for row in rows).items())
        ),
        "unit_status_counts": dict(
            sorted(Counter(row["unit_status"] for row in rows).items())
        ),
        "property_counts": dict(
            sorted(Counter(row["property_name"] for row in rows).items())
        ),
        "verification": verification,
        "deduplication": {
            "table_s2_200a_corresponds_to_table_s3_r1_1_h2": True,
            "duplicated_final_box_density_rg_rows_omitted": 3,
        },
        "known_limitations": [
            "only peer-reviewed SI tables are deposited; raw trajectories, restart files and force-field parameter files are absent",
            "exact atomistic graph/SMILES is not recoverable from the tabulated CG component labels alone",
            "the source prints density as g·mol-1; g/cm^3 normalization is inference-backed and therefore conditional",
            "CC BY-NC 4.0 permits research reuse but imposes a non-commercial restriction",
            "time frames, particles and sensitivity runs are not independent material systems",
        ],
        "training_weight_materialized": False,
    }
    return rows, summary


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=GOLD_C_VALUE_COLUMNS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _write_readme(summary: dict[str, Any]) -> None:
    content = f"""# 第十五批计算数据：多组分 PU 多尺度模拟

## 数据身份

- 数据集 DOI：<https://doi.org/{SUPPLEMENT_DOI}>
- 论文 DOI：<https://doi.org/{ARTICLE_DOI}>
- 固定文件：`多组分PU多尺度模拟补充数据.pdf`
- 文件大小：{PDF_BYTES:,} bytes
- PDF SHA-256：`{PDF_SHA256}`
- PDF MD5（与 Figshare 清单一致）：`{PDF_MD5}`
- 许可：{LICENSE}（仅按非商业科研范围复用）

## 物化范围

物化 Table S1、S2、S3 的 115 个数值：10 个 CG 组成体系、13 个不重复模拟
运行。Table S2 的 200 Å 初始盒与 Table S3 的 R1-1-H2 是同一运行，重复的
最终盒边长、密度和 Rg 已去重。粒子、盒、时间和敏感性运行不计作新材料。

- Gold-C 正式参考：{summary['admission_counts']['admitted_reference']} 条；
- Gold-C 条件参考：{summary['admission_counts']['conditional_reference']} 条；
- 训练权重与训练/验证划分：未生成。

原表密度单位排作 `g·mol-1`，与物理量、数值随盒体积的缩放关系和上下文不符。
本批次将其规范化为 `g/cm^3`，但 17 条密度记录全部降为条件参考，并保留
`source_unit_label_typographical_error_inferred_g_per_cm3` 标志。

## 参考文献

[1] Meng, Y.; Wu, X.; Zhang, A.; Lin, Y. *Supporting Information for Multiscale
Simulation of Multicomponent Polyurethane Elastomers: Unraveling
Composition-Dependent Microphase Separation Dynamics and Mechanical Properties*.
ACS Figshare, 2026. <https://doi.org/{SUPPLEMENT_DOI}>.

[2] Meng, Y.; Wu, X.; Zhang, A.; Lin, Y. Multiscale Simulation of Multicomponent
Polyurethane Elastomers: Unraveling Composition-Dependent Microphase Separation
Dynamics and Mechanical Properties. *Macromolecules* **2026**, *59* (8),
5057–5070. <https://doi.org/{ARTICLE_DOI}>.

## 使用边界

该来源适合做多保真组成—介观结构—力学趋势参考，不应当被表述为原始轨迹库，
也不能单独建立精确 SMILES 到性质的监督关系。任何后续训练必须按
`simulation_key` 分组，且受 CC BY-NC 4.0 的非商业条款约束。
"""
    OUTPUT_README.write_text(content, encoding="utf-8")


def _write_checksums(paths: list[Path]) -> None:
    with OUTPUT_CHECKSUMS.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["file", "bytes", "sha256"])
        for path in paths:
            writer.writerow([path.name, path.stat().st_size, _hash(path, "sha256")])


def main() -> None:
    rows, summary = build_rows()
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    _write_tsv(OUTPUT_TSV, rows)
    OUTPUT_AUDIT.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_readme(summary)
    _write_checksums(
        [
            PDF_PATH,
            FIGSHARE_METADATA_PATH,
            CROSSREF_METADATA_PATH,
            OUTPUT_TSV,
            OUTPUT_AUDIT,
            OUTPUT_README,
        ]
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
