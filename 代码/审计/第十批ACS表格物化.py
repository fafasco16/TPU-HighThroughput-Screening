"""物化四个 ACS Supporting Information 中已人工复核的数值表格。

本模块把官方 Figshare/ACS PDF 中可直接定位的配方、工艺、实验性质与
MD 内聚能整理为多保真长表。它不会数字化图线，不把二手文献比较行当作
本研究样品，也不会创建训练划分或训练权重。所有记录在生成前都重新核对
PDF 字节数、SHA-256、页数和关键文本锚点；表格值来自原表视觉复核。

输出由 ``代码/生成数据总账.py`` 统一写入 ``结果``，本模块本身不修改原始目录。
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "数据/原始/外部数据/新增开放数据"
AUDIT_VERSION = "batch10-acs-table-materialization-v1"


@dataclass(frozen=True)
class SourceSpec:
    directory: str
    pdf_name: str
    size: int
    sha256: str
    pages: int
    article_doi: str
    supplement_doi: str
    citation_key: str
    anchors: tuple[tuple[int, str], ...]


SPECS: dict[str, SourceSpec] = {
    "ACS_Figshare_双相演化聚氨酯": SourceSpec(
        directory="ACS_Figshare_双相演化聚氨酯",
        pdf_name="tz5c00732_si_001.pdf",
        size=1_176_571,
        sha256="5bdedae10fcaff85da215a98a5dadfe7b0608ea6d14ba7dcc1adcbbc468938c9",
        pages=17,
        article_doi="10.1021/acsmaterialslett.5c00732",
        supplement_doi="10.1021/acsmaterialslett.5c00732.s001",
        citation_key="ledger-106-lu-2025-dual-phase-tpu-si",
        anchors=(
            (3, "Polycaprolactone diol (PCL, Mn = 530 g/mol)"),
            (6, "Table S1. The molar ratio of each chemical."),
            (6, "Table S2. Molecular weight and its dispersion index"),
            (7, "Table S3. Summary of the assignment"),
            (14, "Calculation of cohesion energy"),
            (14, "Materials Studio (MS) 2023 with the COMPASS III force field"),
        ),
    ),
    "ACS_Figshare_呋喃高强聚氨酯": SourceSpec(
        directory="ACS_Figshare_呋喃高强聚氨酯",
        pdf_name="ma5c03627_si_001.pdf",
        size=2_261_687,
        sha256="1b85a8294ce375e9b7f7cf314df369eaf7edfa9713a2ff6031aae74330df9108",
        pages=35,
        article_doi="10.1021/acs.macromol.5c03627",
        supplement_doi="10.1021/acs.macromol.5c03627.s001",
        citation_key="ledger-110-yang-2026-furan-tpu-si",
        anchors=(
            (5, "PTMG-2000, Mn = 2000 g mol"),
            (7, "Synthesis of control samples"),
            (9, "Dumbbell-shaped specimens"),
            (13, "Table S1. Formulations for FPUs"),
            (14, "Table S5. Energy dissipation value"),
            (15, "Table S6. Elastic recovery value"),
            (15, "Table S7. Residual strain"),
            (16, "CFPU-350% 62.6 161.9 95.6"),
            (17, "Table S9. Volume resistance"),
        ),
    ),
    "ACS_Figshare_聚酰亚胺回收链扩剂PU": SourceSpec(
        directory="ACS_Figshare_聚酰亚胺回收链扩剂PU",
        pdf_name="ap5c04872_si_001.pdf",
        size=922_002,
        sha256="c18bb54c66f7182cff03508067f7def63ee417fbdbdbfe29067ba568849bedea",
        pages=11,
        article_doi="10.1021/acsapm.5c04872",
        supplement_doi="10.1021/acsapm.5c04872.s001",
        citation_key="ledger-112-guo-2026-polyimide-chain-extender-si",
        anchors=(
            (2, "Table S1. The reaction temperature and reaction time"),
            (2, "Table S2. The components of different samples"),
            (8, "Table S3. Summary of the mechanical properties"),
        ),
    ),
    "ACS_Figshare_氢键纳米结构TPU": SourceSpec(
        directory="ACS_Figshare_氢键纳米结构TPU",
        pdf_name="ma6c00352_si_001.pdf",
        size=2_077_355,
        sha256="29d8b451025a86acb3c075a6cb7c29428b725e7c40083a70d271500444b93765",
        pages=28,
        article_doi="10.1021/acs.macromol.6c00352",
        supplement_doi="10.1021/acs.macromol.6c00352.s001",
        citation_key="ledger-118-wei-2026-hbond-nanostructure-si",
        anchors=(
            (2, "Poly(tetramethylene ether) glycol (PTMG, Mn = 1000 Da)"),
            (3, "Synthesis of HTPUs"),
            (6, "Instron-5966"),
            (14, "Table S1. Feeding compositions"),
            (15, "Table S2. Molecular weights of HTPUs"),
            (15, "Table S3. Glass transition temperatures"),
            (16, "Table S4. Summary of the ratios"),
        ),
    ),
}


MATERIALIZED_EVIDENCE_GROUPS: dict[str, frozenset[str]] = {
    "ACS_Figshare_双相演化聚氨酯": frozenset(
        {"table_s1_formulation", "table_s2_molecular_weight", "table_s3_hydrogen_bond"}
    ),
    "ACS_Figshare_呋喃高强聚氨酯": frozenset(
        {
            "table_s1_s2_formulations",
            "table_s5_dissipation",
            "table_s6_recovery",
            "table_s7_residual_strain",
        }
    ),
    "ACS_Figshare_聚酰亚胺回收链扩剂PU": frozenset(
        {
            "table_s1_chain_extender_process",
            "table_s2_formulation",
            "table_s3_mechanics",
        }
    ),
    "ACS_Figshare_氢键纳米结构TPU": frozenset(
        {
            "table_s1_formulation",
            "table_s2_molecular_weight",
            "table_s3_mechanics",
            "table_s4_hydrogen_bond",
        }
    ),
}


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


class AuditBlocked(RuntimeError):
    """原件身份、页数、文本锚点或静态表格约束发生漂移。"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_sources() -> list[dict[str, Any]]:
    verified: list[dict[str, Any]] = []
    for spec in SPECS.values():
        path = DATA_ROOT / spec.directory / spec.pdf_name
        if not path.is_file():
            raise AuditBlocked(f"缺少 ACS SI 原件：{path}")
        actual_size = path.stat().st_size
        actual_sha256 = _sha256(path)
        if (actual_size, actual_sha256) != (spec.size, spec.sha256):
            raise AuditBlocked(
                f"ACS SI 原件漂移：{spec.pdf_name}; bytes={actual_size}; sha256={actual_sha256}"
            )
        reader = PdfReader(path)
        if len(reader.pages) != spec.pages:
            raise AuditBlocked(
                f"ACS SI 页数漂移：{spec.pdf_name}; pages={len(reader.pages)}"
            )
        page_text: dict[int, str] = {}
        for page_number, anchor in spec.anchors:
            if page_number not in page_text:
                page_text[page_number] = (
                    reader.pages[page_number - 1].extract_text() or ""
                ).replace("\u00a0", " ")
            normalized = " ".join(page_text[page_number].split())
            normalized_anchor = " ".join(anchor.split())
            if normalized_anchor not in normalized:
                raise AuditBlocked(
                    f"ACS SI 文本锚点漂移：{spec.pdf_name}#page={page_number}: {anchor}"
                )
        verified.append(
            {
                "source_directory": spec.directory,
                "pdf_name": spec.pdf_name,
                "bytes": actual_size,
                "sha256": actual_sha256,
                "pages": len(reader.pages),
                "verification": "matched_frozen_identity_and_text_anchors",
            }
        )
    return verified


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise AuditBlocked(f"{label} 不是有效数值")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AuditBlocked(f"{label} 无法解析为数值：{value!r}") from exc
    if not math.isfinite(number):
        raise AuditBlocked(f"{label} 不是有限数值：{value!r}")
    return number


def _record(
    source: str,
    table: str,
    page: int,
    sample_id: str,
    record_kind: str,
    property_name: str,
    value: float,
    unit: str,
    *,
    formulation_id: str | None = None,
    component_name: str = "",
    component_role: str = "",
    uncertainty_value: float | str = "",
    uncertainty_type: str = "",
    condition_name: str = "",
    condition_value: float | str = "",
    condition_unit: str = "",
    target_origin: str = "experimental",
    data_origin: str = "experimental_published_si_table",
    reduction_level: str = "published_table_value",
    method_or_test_protocol: str = "source_supporting_information",
    fidelity_level: str = "experimental_published_aggregate",
    gold_admission_status: str = "admitted_reference",
    mapping_status: str = "direct_source_sample_label",
    protocol_status: str = "table_and_si_protocol",
    potential_weight_ceiling: float = 0.0,
    notes: str = "",
) -> dict[str, Any]:
    if source not in SPECS:
        raise AuditBlocked(f"未知 ACS 来源：{source}")
    if gold_admission_status not in {"admitted_reference", "conditional_reference"}:
        raise AuditBlocked(f"非法准入状态：{gold_admission_status}")
    value = _finite(value, f"{source}/{table}/{sample_id}/{property_name}")
    uncertainty = (
        ""
        if uncertainty_value == ""
        else _finite(uncertainty_value, f"{sample_id}/{property_name}/uncertainty")
    )
    condition = (
        ""
        if condition_value == ""
        else _finite(condition_value, f"{sample_id}/{property_name}/condition")
    )
    spec = SPECS[source]
    formulation = formulation_id if formulation_id is not None else sample_id
    identity = "|".join(
        str(item)
        for item in (
            source,
            table,
            page,
            sample_id,
            record_kind,
            component_name,
            property_name,
            condition_name,
            condition,
        )
    )
    observation_id = "acs_table_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return {
        "source_directory": source,
        "source_record_id": f"{source}|{sample_id}",
        "observation_id": observation_id,
        "formulation_id": formulation,
        "sample_id": sample_id,
        "record_kind": record_kind,
        "component_name": component_name,
        "component_role": component_role,
        "property_name": property_name,
        "value": value,
        "unit": unit,
        "uncertainty_value": uncertainty,
        "uncertainty_type": uncertainty_type,
        "condition_name": condition_name,
        "condition_value": condition,
        "condition_unit": condition_unit,
        "target_origin": target_origin,
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
        "split_group": f"{source}|{formulation or sample_id}",
        "source_locator": f"{spec.pdf_name}#page={page};{table}",
        "file_sha256": spec.sha256,
        "license": "CC BY-NC 4.0",
        "citation_keys": spec.citation_key,
        "notes": notes,
    }


def _append_formulation(
    rows: list[dict[str, Any]],
    source: str,
    table: str,
    page: int,
    sample: str,
    component: str,
    role: str,
    property_name: str,
    value: float,
    unit: str,
    *,
    admission: str = "admitted_reference",
    mapping: str = "direct_source_sample_label",
    notes: str = "",
) -> None:
    rows.append(
        _record(
            source,
            table,
            page,
            sample,
            "formulation_component",
            property_name,
            value,
            unit,
            component_name=component,
            component_role=role,
            data_origin="published_si_formulation",
            reduction_level="published_formulation_value",
            fidelity_level="published_formulation",
            gold_admission_status=admission,
            mapping_status=mapping,
            protocol_status="not_a_property_test",
            potential_weight_ceiling=0.0,
            notes=notes,
        )
    )


def _append_process(
    rows: list[dict[str, Any]],
    source: str,
    page: int,
    family: str,
    step: str,
    property_name: str,
    value: float,
    unit: str,
    *,
    notes: str = "",
) -> None:
    rows.append(
        _record(
            source,
            "SI experimental section",
            page,
            family,
            "process_condition",
            property_name,
            value,
            unit,
            formulation_id="",
            condition_name=step,
            data_origin="published_si_protocol",
            reduction_level="reported_common_protocol",
            fidelity_level="published_process_protocol",
            method_or_test_protocol=step,
            protocol_status="source_protocol",
            potential_weight_ceiling=0.0,
            notes=notes,
        )
    )


def _htpu_rows(rows: list[dict[str, Any]]) -> None:
    source = "ACS_Figshare_氢键纳米结构TPU"
    formulations = (
        ("HTPU-P1", "PTMG", "EDA", 0.60),
        ("HTPU-P2", "PTMG", "MEDA", 0.74),
        ("HTPU-P3", "PTMG", "DMEDA", 0.88),
        ("HTPU-P4", "PTMG", "BDA", 0.88),
        ("HTPU-P5", "PTMG", "HDA", 1.16),
        ("HTPU-P6", "PCLD", "CA", 1.52),
        ("HTPU-P7", "PCLD", "EDA", 0.60),
    )
    for sample, polyol, extender, extender_mass in formulations:
        for component, role, mass in (
            (polyol, "polyol", 10.0),
            ("IPDI", "diisocyanate", 4.62),
            (extender, "chain_extender", extender_mass),
        ):
            _append_formulation(rows, source, "Table S1", 14, sample, component, role, "component_mass", mass, "g")
        for component, role, equivalent in (
            (polyol, "polyol", 1.0),
            ("IPDI", "diisocyanate", 2.0),
            (extender, "chain_extender", 1.0),
        ):
            _append_formulation(
                rows,
                source,
                "Table S1",
                14,
                sample,
                component,
                role,
                "nominal_molar_equivalent",
                equivalent,
                "relative_equivalent",
                notes="Table S1 nominal ratio is 1/2/1; the P1 narrative reports 2.08 IPDI equivalents after actual weighing.",
            )
        _append_formulation(
            rows,
            source,
            "Materials + Table S1",
            14,
            sample,
            polyol,
            "polyol",
            "component_number_average_molar_mass",
            1000,
            "g/mol",
        )

    gpc = (
        ("HTPU-P1", 98, 231, 2.37),
        ("HTPU-P2", 110, 227, 2.07),
        ("HTPU-P3", 134, 355, 2.65),
        ("HTPU-P4", 70, 150, 2.14),
        ("HTPU-P5", 96, 199, 2.08),
        ("HTPU-P6", 67, 163, 2.42),
        ("HTPU-P7", 118, 292, 2.47),
    )
    gpc_protocol = "GPC HLC-8420; DMF + 0.1 M LiBr; 40 °C; 0.6 mL/min"
    for sample, mn, mw, dispersity in gpc:
        for prop, value, unit in (
            ("number_average_molar_mass", mn, "kDa"),
            ("weight_average_molar_mass", mw, "kDa"),
            ("dispersity", dispersity, "dimensionless"),
        ):
            rows.append(
                _record(
                    source,
                    "Table S2",
                    15,
                    sample,
                    "molecular_characterization",
                    prop,
                    value,
                    unit,
                    method_or_test_protocol=gpc_protocol,
                    potential_weight_ceiling=0.55,
                )
            )

    mechanics = (
        ("HTPU-P1", -32.00, 47.00, 79.21, 1475, 365.72),
        ("HTPU-P2", -28.5, 33.00, 43.49, 1547, 173.53),
        ("HTPU-P3", -28.5, 19.00, 37.10, 1973, 161.21),
        ("HTPU-P4", -31.80, 40.25, 70.97, 1519, 323.07),
        ("HTPU-P5", -35.6, 27.00, 37.52, 1093, 160.53),
        ("HTPU-P6", None, 25.47, 47.88, 1332, 192.51),
        ("HTPU-P7", None, 32.45, 83.11, 1282, 334.07),
    )
    tensile_protocol = "Instron 5966; 1 kN; room temperature; 50 mm/min; gauge 15×4×1 mm"
    for sample, tg_soft, tg_hard, strength, elongation, toughness in mechanics:
        if tg_soft is not None:
            rows.append(_record(source, "Table S3", 15, sample, "thermal_property", "glass_transition_temperature_soft_segment", tg_soft, "degC", method_or_test_protocol="source DSC/DMA assignment; Table S3", potential_weight_ceiling=0.50))
        rows.append(_record(source, "Table S3", 15, sample, "thermal_property", "glass_transition_temperature_hard_segment", tg_hard, "degC", method_or_test_protocol="source DSC/DMA assignment; Table S3", potential_weight_ceiling=0.50))
        rows.append(_record(source, "Table S3", 15, sample, "mechanical_property", "tensile_strength", strength, "MPa", method_or_test_protocol=tensile_protocol, potential_weight_ceiling=0.65))
        rows.append(_record(source, "Table S3", 15, sample, "mechanical_property", "elongation_at_break", elongation, "%", method_or_test_protocol=tensile_protocol, potential_weight_ceiling=0.65))
        rows.append(_record(source, "Table S3", 15, sample, "mechanical_property", "toughness", toughness, "MJ/m^3", method_or_test_protocol=tensile_protocol + "; area under engineering stress-strain curve to fracture", potential_weight_ceiling=0.65))

    hbond = {
        "HTPU-P1": (("urethane_free", 1720, 14.23), ("urethane_hbond_ordered", 1701, 38.82), ("urea_free", 1666, 18.30), ("urea_hbond", 1639, 28.65), ("total", None, 67.47)),
        "HTPU-P4": (("urethane_free", 1722, 15.43), ("urethane_hbond_ordered", 1699, 35.51), ("urea_free", 1665, 21.39), ("urea_hbond", 1638, 27.67), ("total", None, 63.18)),
        "HTPU-P5": (("urethane_free", 1721, 18.18), ("urethane_hbond_ordered", 1699, 29.32), ("urea_free", 1664, 27.72), ("urea_hbond", 1639, 24.78), ("total", None, 54.1)),
    }
    for sample, peaks in hbond.items():
        for assignment, wavenumber, area in peaks:
            if wavenumber is not None:
                rows.append(_record(source, "Table S4", 16, sample, "spectral_feature", f"carbonyl_peak_wavenumber_{assignment}", wavenumber, "cm^-1", method_or_test_protocol="FTIR Nicolet 560; transmission; 4 cm^-1 resolution", potential_weight_ceiling=0.40))
                rows.append(_record(source, "Table S4", 16, sample, "spectral_feature", f"carbonyl_peak_area_fraction_{assignment}", area, "%", method_or_test_protocol="FTIR peak deconvolution; source assignment", potential_weight_ceiling=0.40))
            else:
                rows.append(_record(source, "Table S4", 16, sample, "spectral_feature", "total_hydrogen_bond_degree", area, "%", method_or_test_protocol="FTIR peak deconvolution; source definition", potential_weight_ceiling=0.45))

    for step, prop, value, unit in (
        ("polyol vacuum drying", "temperature", 105, "degC"),
        ("polyol vacuum drying", "duration", 60, "min"),
        ("IPDI prepolymer reaction", "temperature", 60, "degC"),
        ("IPDI prepolymer reaction", "duration", 1, "h"),
        ("DBTDL addition", "catalyst_mass", 0.02, "g"),
        ("post-catalyst prepolymer reaction", "temperature", 80, "degC"),
        ("post-catalyst prepolymer reaction", "duration", 3, "h"),
        ("bulk dilution", "anhydrous_DMF_mass", 40, "g"),
        ("chain-extender solution", "anhydrous_DMF_mass", 10, "g"),
        ("PTFE mold cure", "temperature", 80, "degC"),
        ("PTFE mold cure", "duration", 72, "h"),
        ("final vacuum drying", "temperature", 80, "degC"),
        ("final vacuum drying", "duration", 12, "h"),
    ):
        _append_process(rows, source, 3, "HTPU-family", step, prop, value, unit)


def _dual_phase_rows(rows: list[dict[str, Any]]) -> None:
    source = "ACS_Figshare_双相演化聚氨酯"
    formulations = (
        ("D4C0", 4, 0, 4),
        ("D3C1", 3, 1, 4),
        ("D2C2", 2, 2, 4),
        ("D1C3", 1, 3, 4),
        ("D0C4", 0, 4, 4),
    )
    for sample, d400, pcl, hmdi in formulations:
        for component, role, ratio in (
            ("D400", "polyether_amine", d400),
            ("PCL", "polyol", pcl),
            ("HMDI", "diisocyanate", hmdi),
        ):
            _append_formulation(rows, source, "Table S1", 6, sample, component, role, "molar_ratio_part", ratio, "relative_molar_part")
        if d400:
            _append_formulation(rows, source, "Materials + Table S1", 6, sample, "D400", "polyether_amine", "component_number_average_molar_mass", 400, "g/mol")
        if pcl:
            _append_formulation(rows, source, "Materials + Table S1", 6, sample, "PCL", "polyol", "component_number_average_molar_mass", 530, "g/mol")

    for sample, mn, mw, dispersity in (
        ("D4C0", 287, 3452, 12.0),
        ("D3C1", 372, 3900, 10.5),
        ("D2C2", 146, 407, 2.8),
        ("D1C3", 201, 815, 4.1),
        ("D0C4", 251, 825, 3.3),
    ):
        for prop, value, unit in (
            ("number_average_molar_mass", mn, "kDa"),
            ("weight_average_molar_mass", mw, "kDa"),
            ("dispersity", dispersity, "dimensionless"),
        ):
            rows.append(_record(source, "Table S2", 6, sample, "molecular_characterization", prop, value, unit, method_or_test_protocol="source GPC; calibration details not in SI", protocol_status="partial_si_protocol", potential_weight_ceiling=0.35))

    peak_rows = {
        "D4C0": (("urea_free", 1667, 15.5), ("urea_hbond_disordered", 1645, 20.3), ("urea_hbond_ordered", 1625, 64.2)),
        "D3C1": (("urethane_free", 1734, 15.8), ("ester_free", 1718, 8.6), ("urethane_ester_hbond_ordered", 1698, 16.8), ("urea_free", 1668, 3.0), ("urea_hbond_disordered", 1639, 35.4), ("urea_hbond_ordered", 1625, 20.4)),
        "D2C2": (("urethane_free", 1734, 10.8), ("ester_free", 1717, 40.9), ("urethane_ester_hbond_ordered", 1691, 7.9), ("urea_free", 1669, 6.1), ("urea_hbond_disordered", 1640, 23.6), ("urea_hbond_ordered", 1626, 10.7)),
        "D1C3": (("urethane_free", 1731, 37.5), ("ester_free", 1711, 23.9), ("urethane_ester_hbond_ordered", 1693, 19.2), ("urea_free", 1667, 6.9), ("urea_hbond_disordered", 1640, 6.4), ("urea_hbond_ordered", 1627, 6.1)),
        "D0C4": (("urethane_free", 1730, 42.4), ("ester_free", 1712, 26.9), ("urethane_ester_hbond_ordered", 1693, 30.6)),
    }
    totals = {"D4C0": 84.5, "D3C1": 72.6, "D2C2": 42.2, "D1C3": 31.7, "D0C4": 30.6}
    for sample, peaks in peak_rows.items():
        for assignment, wavenumber, area in peaks:
            rows.append(_record(source, "Table S3", 7, sample, "spectral_feature", f"carbonyl_peak_wavenumber_{assignment}", wavenumber, "cm^-1", method_or_test_protocol="FTIR C=O peak deconvolution; source assignment", potential_weight_ceiling=0.35))
            rows.append(_record(source, "Table S3", 7, sample, "spectral_feature", f"carbonyl_peak_area_fraction_{assignment}", area, "%", method_or_test_protocol="FTIR C=O peak deconvolution; source assignment", potential_weight_ceiling=0.35))
        rows.append(_record(source, "Table S3", 7, sample, "spectral_feature", "total_hydrogen_bond_density", totals[sample], "%", method_or_test_protocol="source equation based on deconvoluted C=O band", potential_weight_ceiling=0.40))

    for step, prop, value, unit in (
        ("PCL/D400 degassing", "temperature", 60, "degC"),
        ("PCL/D400 degassing", "duration", 30, "min"),
        ("HMDI reaction under nitrogen", "duration", 24, "h"),
        ("solution-cast sequential drying", "temperature", 60, "degC"),
    ):
        _append_process(rows, source, 3, "DxCy-family", step, prop, value, unit)

    md_protocol = (
        "all-atom MD; Materials Studio 2023 Forcite; COMPASS III; five chains; periodic cubic box; "
        "energy minimization; 25 annealing cycles; NPT 298 K/1 atm 1000 ps with Nosé thermostat and "
        "Berendsen barostat; NVT 500 ps; trajectory interval 5 ps; cohesive energy averaged over 10 frames"
    )
    for sample, value in (
        ("D4C0", 438.74),
        ("D3C1", 385.76),
        ("D2C2", 313.63),
        ("D1C3", 277.61),
        ("D0C4", 211194),
    ):
        anomaly = sample == "D0C4"
        rows.append(
            _record(
                source,
                "Supplementary Note 3.1",
                14,
                sample,
                "computational_property",
                "cohesive_energy_per_chain",
                value,
                "kcal/mol",
                target_origin="md",
                data_origin="published_all_atom_md_output",
                reduction_level="reported_system_average",
                method_or_test_protocol=md_protocol,
                fidelity_level="all_atom_MD_published_aggregate",
                gold_admission_status="conditional_reference" if anomaly else "admitted_reference",
                mapping_status="direct_formulation_to_md_system",
                protocol_status="complete_si_protocol_with_source_value_anomaly" if anomaly else "complete_si_protocol",
                potential_weight_ceiling=0.10 if anomaly else 0.20,
                notes=(
                    "Source PDF visibly prints 211194 kcal/mol without a decimal; raw published value is retained without correction and must not be used until author/article clarification."
                    if anomaly
                    else "Computed descriptor; it is not an experimental cohesive energy or macroscopic mechanical truth."
                ),
            )
        )


def _furan_rows(rows: list[dict[str, Any]]) -> None:
    source = "ACS_Figshare_呋喃高强聚氨酯"
    for sample, ipdi, fdca, hard_segment in (
        ("FPU-3", 2.22, 0.91, 23.8),
        ("FPU-5", 1.67, 0.46, 17.6),
        ("FPU-7", 1.48, 0.31, 15.2),
        ("FPU-11", 1.33, 0.18, 13.1),
    ):
        for component, role, prop, value, unit in (
            ("PTMG-2000", "polyol", "component_mass", 10, "g"),
            ("PTMG-2000", "polyol", "component_number_average_molar_mass", 2000, "g/mol"),
            ("IPDI", "diisocyanate", "component_mass", ipdi, "g"),
            ("FDCA (source label; synthesis section describes FDCH)", "chain_extender", "component_mass", fdca, "g"),
            ("hard_segment", "derived_composition", "hard_segment_mass_fraction", hard_segment, "%"),
        ):
            _append_formulation(rows, source, "Table S1", 13, sample, component, role, prop, value, unit, notes="FDCA/FDCH acronym inconsistency is preserved; no chain-extender SMILES is inferred.")

    for sample, extender, extender_mass in (
        ("FAPU-3", "2,5-FNH", 0.62),
        ("FOPU-3", "2,5-FOH", 0.63),
    ):
        conflict = sample == "FAPU-3"
        for component, role, prop, value, unit in (
            ("PTMG-2000", "polyol", "component_mass", 10, "g"),
            ("PTMG-2000", "polyol", "component_number_average_molar_mass", 2000, "g/mol"),
            ("IPDI", "diisocyanate", "component_mass", 2.22, "g"),
            (extender, "chain_extender", "component_mass", extender_mass, "g"),
            ("hard_segment", "derived_composition", "hard_segment_mass_fraction", 22.1, "%"),
        ):
            _append_formulation(
                rows,
                source,
                "Table S2",
                13,
                sample,
                component,
                role,
                prop,
                value,
                unit,
                admission="conditional_reference" if conflict else "admitted_reference",
                mapping="source_label_conflict_FAPU_vs_FNPU" if conflict else "direct_source_sample_label",
                notes="Table S2 visibly labels FAPU-3, while Sections 1.4 and later tables use FNPU-3; identity link remains conditional." if conflict else "",
            )

    for sample, crosslink, bmi_g, bmi_mmol in (
        ("CFPU-3-20%", 20, 0.116, 0.32),
        ("CFPU-3-50%", 50, 0.29, 0.81),
        ("CFPU-3-100%", 100, 0.58, 1.62),
    ):
        for component, role, prop, value, unit in (
            ("FPU-3", "base_polyurethane", "component_mass", 8.6, "g"),
            ("FPU-3", "base_polyurethane", "component_amount", 0.81, "mmol"),
            ("BMI", "post_crosslinker", "component_mass", bmi_g, "g"),
            ("BMI", "post_crosslinker", "component_amount", bmi_mmol, "mmol"),
            ("post_crosslink", "derived_composition", "nominal_crosslinking_degree", crosslink, "%"),
        ):
            _append_formulation(rows, source, "SI Section 1.5", 7, sample, component, role, prop, value, unit)

    for loading in (20, 40, 60):
        _append_formulation(rows, source, "Table S9 + SI Section 1.6", 17, f"CFPU-3Li-{loading}%", "LiTFSI", "conductive_additive", "additive_loading_relative_to_resin", loading, "wt%")

    edi = {
        "FPU-3": ([20.25, 23.03, 26.23, 29.46, 32.77, 35.61, 38.13, 40.16, 42.35, 44.44], [0.69, 0.76, 0.65, 0.58, 0.88, 0.67, 0.68, 0.59, 0.62, 0.74]),
        "FPU-5": ([12.9, 13.38, 13.55, 17.46, 28.38, 34.76, 37.71, 39.5, 41.54], [0.59, 0.66, 0.55, 0.48, 0.78, 0.57, 0.58, 0.59, 0.65]),
        "FPU-7": ([16.08, 12.67, 11.28, 20.34, 34.06, 40.67, 43.11, 44.64, 45.81, 46.34], [0.69, 0.76, 0.65, 0.58, 0.88, 0.67, 0.68, 0.59, 0.62, 0.74]),
        "FPU-11": ([17.45, 16.26, 16.2, 17.45, 25.28, 34.73, 41.07, 45.17, 46.91, 47.72], [0.69, 0.76, 0.65, 0.58, 0.88, 0.67, 0.68, 0.59, 0.62, 0.74]),
        "FNPU-3": ([38.01, 34.06, 32.69, 34.89, 43.03, 49.56, 52.69, 56.37, 59.03], [0.89, 0.96, 0.85, 0.78, 1.08, 0.87, 0.88, 0.59, 0.95]),
        "CFPU-3-50%": ([28.75, 31.36, 35.17, 39.82, 43.59, 47.52, 52.66, 55.4], [0.64, 0.66, 0.85, 0.88, 1.08, 0.97, 0.98, 0.79]),
    }
    er = {
        "FPU-3": ([92, 94.4, 95.14, 96.03, 96.16, 96.24, 96.46, 96.48, 96.38, 96.41], [1.9, 1.75, 1.66, 1.87, 2, 2.04, 2.01, 2.1, 1.98, 2.1]),
        "FPU-5": ([94, 95.89, 97.23, 97.65, 97.95, 98.08, 97.9, 98.16, 98.12], [1.45, 1.56, 1.5, 1.55, 1.67, 1.76, 1.91, 1.6, 1.78]),
        "FPU-7": ([91, 94.79, 96.83, 97.88, 98.09, 97.88, 97.56, 97.32, 97.48, 97.93], [1.5, 1.6, 1.56, 1.5, 1.7, 1.6, 2.01, 1.68, 1.78, 1.67]),
        "FPU-11": ([90, 94.24, 95.04, 95.7, 96.45, 96.93, 97.21, 96.89, 96.74, 96.85], [1.8, 1.68, 1.66, 1.57, 1.87, 1.96, 2.01, 1.88, 1.98, 1.67]),
        "FNPU-3": ([82, 88.3, 90, 91.5, 91.89, 90.27, 89.27, 88.28, 88.8], [1.35, 1.26, 1.26, 1.35, 1.47, 1.36, 1.91, 1.68, 1.98]),
        "CFPU-3-50%": ([92, 94.43, 94.83, 95.63, 95.45, 95.68, 95.52, 95.66], [1.9, 1.63, 1.56, 1.77, 1.94, 1.9, 2.01, 2.1]),
    }
    cyclic_protocol = "Instron 4302; 1 kN; room temperature; 50 mm/min; gauge 15×4×1 mm; incremental cycles 100–1000%"
    for table, prop, values_by_sample in (("Table S5", "energy_dissipation_index", edi), ("Table S6", "elastic_recovery", er)):
        page = 14 if table == "Table S5" else 15
        for sample, (means, errors) in values_by_sample.items():
            for index, (mean, error) in enumerate(zip(means, errors, strict=True), start=1):
                rows.append(
                    _record(
                        source,
                        table,
                        page,
                        sample,
                        "cyclic_mechanical_property",
                        prop,
                        mean,
                        "%",
                        formulation_id="" if sample == "FNPU-3" else None,
                        uncertainty_value=error,
                        uncertainty_type="reported_plus_minus_type_unresolved",
                        condition_name="maximum_strain",
                        condition_value=index * 100,
                        condition_unit="%",
                        method_or_test_protocol=cyclic_protocol,
                        potential_weight_ceiling=0.55 if sample != "FNPU-3" else 0.20,
                        mapping_status="sample_label_only_formulation_link_conflicted" if sample == "FNPU-3" else "direct_source_sample_label",
                        notes="Plus/minus values are preserved, but SI does not identify SD versus SEM. FNPU-3 property is valid at sample-label level; Table S2 calls the apparent formulation FAPU-3." if sample == "FNPU-3" else "Plus/minus values are preserved, but SI does not identify SD versus SEM.",
                    )
                )

    residual = {
        "FNPU-3": ((18, 32), (44, 82), (103, 154)),
        "FPU-3": ((9, 15), (33, 45), (57, 73)),
        "FPU-5": ((4, 7), (14, 19), (67, 82)),
        "FPU-7": ((6, 10), (21, 30), (60, 75)),
        "FPU-11": ((8, 18), (32, 60), (53, 96)),
    }
    for sample, strain_rows in residual.items():
        for strain, (first, last) in zip((100, 300, 600), strain_rows, strict=True):
            for loop, value in (("first", first), ("last", last)):
                rows.append(
                    _record(
                        source,
                        "Table S7",
                        15,
                        sample,
                        "cyclic_mechanical_property",
                        "residual_strain",
                        value,
                        "%",
                        formulation_id="" if sample == "FNPU-3" else None,
                        condition_name=f"maximum_strain_{strain}_percent_{loop}_loop",
                        condition_value=strain,
                        condition_unit="%",
                        method_or_test_protocol="constant-strain cycling; 10 cycles; " + cyclic_protocol,
                        potential_weight_ceiling=0.45 if sample != "FNPU-3" else 0.20,
                        mapping_status="sample_label_only_formulation_link_conflicted" if sample == "FNPU-3" else "direct_source_sample_label",
                    )
                )

    for prop, value, unit in (
        ("tensile_strength", 62.6, "MPa"),
        ("toughness", 161.9, "MJ/m^3"),
        ("tensile_strength_recycling_efficiency", 95.6, "%"),
    ):
        rows.append(_record(source, "Table S8, This Work row only", 16, "CFPU-3-50%", "mechanical_property", prop, value, unit, method_or_test_protocol="source tensile/recycling protocol; published comparison table own-work row only", potential_weight_ceiling=0.45, notes="Other literature-comparison rows in Table S8 are intentionally excluded."))
    for sample, value in (("CFPU-3Li-20%", 6.41e5), ("CFPU-3Li-40%", 2.75e5), ("CFPU-3Li-60%", 6.75e4)):
        rows.append(_record(source, "Table S9", 17, sample, "electrical_property", "volume_resistance", value, "ohm", method_or_test_protocol="source Table S9; geometry-specific resistivity protocol not reported in SI table", protocol_status="partial_si_protocol", potential_weight_ceiling=0.30))

    for page, family, step, prop, value, unit in (
        (6, "FPU-family", "PTMG vacuum drying", "temperature", 105, "degC"),
        (6, "FPU-family", "PTMG vacuum drying", "duration", 1.5, "h"),
        (6, "FPU-family", "initial IPDI reaction", "temperature", 60, "degC"),
        (6, "FPU-family", "initial IPDI reaction", "duration", 0.5, "h"),
        (6, "FPU-family", "DBTDL loading", "catalyst_loading", 3, "permille_by_weight"),
        (6, "FPU-family", "post-catalyst reaction at 60 C", "duration", 1, "h"),
        (6, "FPU-family", "prepolymer reaction at 80 C", "duration", 3, "h"),
        (6, "FPU-family", "chain extension at room temperature", "duration", 2, "h"),
        (6, "FPU-family", "chain extension at 60 C", "duration", 3, "h"),
        (6, "FPU-family", "initial cast drying", "temperature", 80, "degC"),
        (6, "FPU-family", "initial cast drying", "duration", 24, "h"),
        (6, "FPU-family", "final vacuum drying", "temperature", 100, "degC"),
        (7, "CFPU-family", "BMI post-crosslinking", "temperature", 70, "degC"),
        (7, "CFPU-family", "BMI post-crosslinking", "duration", 8, "h"),
        (8, "CFPU-recycling", "mold preheating", "temperature", 140, "degC"),
        (8, "CFPU-recycling", "mold preheating", "duration", 5, "min"),
        (8, "CFPU-recycling", "hot pressing", "pressure", 9, "MPa"),
        (8, "CFPU-recycling", "hot pressing", "duration", 30, "min"),
        (8, "CFPU-recycling", "network reformation", "pressure", 5, "MPa"),
        (8, "CFPU-recycling", "network reformation", "temperature", 60, "degC"),
        (8, "CFPU-recycling", "network reformation", "duration", 1, "h"),
    ):
        _append_process(rows, source, page, family, step, prop, value, unit)


def _chain_extender_rows(rows: list[dict[str, Any]]) -> None:
    source = "ACS_Figshare_聚酰亚胺回收链扩剂PU"
    for sample, temperature, duration in (
        ("PI-100", 100, 10.15),
        ("PI-120", 120, 5.25),
        ("PI-140", 140, 1.67),
        ("PI-160", 160, 0.83),
        ("PI-180", 180, 0.42),
    ):
        rows.append(_record(source, "Table S1", 2, sample, "process_condition", "reaction_temperature", temperature, "degC", formulation_id="", data_origin="published_si_protocol_table", fidelity_level="published_process_protocol", protocol_status="partial_si_protocol", potential_weight_ceiling=0.0))
        rows.append(_record(source, "Table S1", 2, sample, "process_condition", "reaction_time", duration, "h", formulation_id="", data_origin="published_si_protocol_table", fidelity_level="published_process_protocol", protocol_status="partial_si_protocol", potential_weight_ceiling=0.0))

    for index, four_hpa_mmol, four_hpa_percent in (
        (1, 0.3, 1.69),
        (2, 0.6, 3.38),
        (3, 0.9, 5.07),
        (4, 1.2, 6.75),
        (5, 1.5, 8.44),
    ):
        sample = f"PU-4HPA-{index}"
        for component, role, prop, value, unit in (
            ("PTMEG", "polyol", "component_amount", 3, "mmol"),
            ("IPDI", "diisocyanate", "component_amount", 6, "mmol"),
            ("DBTDL", "catalyst", "component_mass", 0.24, "g"),
            ("4HPA", "upcycled_chain_extender", "component_amount", four_hpa_mmol, "mmol"),
            ("4HPA", "upcycled_chain_extender", "reported_component_fraction", four_hpa_percent, "%"),
        ):
            _append_formulation(rows, source, "Table S2", 2, sample, component, role, prop, value, unit)

    for sample, strength, elongation in (
        ("PU-4HPA-1", 30.92, 997),
        ("PU-4HPA-2", 45.03, 1255),
        ("PU-4HPA-3", 57.90, 1294),
        ("PU-4HPA-4", 78.97, 1267),
        ("PU-4HPA-5", 65.15, 1082),
    ):
        for prop, value, unit in (
            ("tensile_strength", strength, "MPa"),
            ("elongation_at_break", elongation, "%"),
        ):
            rows.append(
                _record(
                    source,
                    "Table S3",
                    8,
                    sample,
                    "mechanical_property",
                    prop,
                    value,
                    unit,
                    method_or_test_protocol="mechanical test protocol not present in downloaded SI; main article lookup required",
                    gold_admission_status="conditional_reference",
                    protocol_status="missing_main_article_test_protocol",
                    potential_weight_ceiling=0.35,
                    notes="Table S4 literature comparison is excluded; only Table S3 own-study rows are retained.",
                )
            )


def build_records() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    verified = verify_sources()
    rows: list[dict[str, Any]] = []
    _htpu_rows(rows)
    _dual_phase_rows(rows)
    _furan_rows(rows)
    _chain_extender_rows(rows)

    if len({row["observation_id"] for row in rows}) != len(rows):
        duplicates = [key for key, count in Counter(row["observation_id"] for row in rows).items() if count > 1]
        raise AuditBlocked(f"ACS 物化记录 observation_id 重复：{duplicates[:5]}")
    if any(set(row) != set(RECORD_COLUMNS) for row in rows):
        raise AuditBlocked("ACS 物化记录字段集合漂移")
    if any(row["current_weight_materialized"] != "false" or row["training_weight"] != "" for row in rows):
        raise AuditBlocked("ACS 物化阶段禁止创建训练权重")

    source_counts = Counter(row["source_directory"] for row in rows)
    target_counts = Counter(row["target_origin"] for row in rows)
    admission_counts = Counter(row["gold_admission_status"] for row in rows)
    uncertainty_count = sum(row["uncertainty_value"] != "" for row in rows)
    summary = {
        "audit_version": AUDIT_VERSION,
        "source_count": len(SPECS),
        "record_count": len(rows),
        "numeric_value_count_including_uncertainty": len(rows) + uncertainty_count,
        "uncertainty_value_count": uncertainty_count,
        "source_record_counts": dict(source_counts),
        "target_origin_counts": dict(target_counts),
        "admission_counts": dict(admission_counts),
        "verified_files": verified,
        "training_split_materialized": False,
        "training_weight_materialized": False,
    }
    return rows, summary


if __name__ == "__main__":
    import json

    _, result = build_records()
    print(json.dumps(result, ensure_ascii=False, indent=2))
