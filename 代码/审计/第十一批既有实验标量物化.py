"""把已经完成来源审计的五组实验标量物化为统一 Gold-E 长表。

这些值此前已经出现在逐记录样本清单中，但样本清单只保存治理元数据，
不保存数值本身。本模块读取被冻结的审计 TSV，把数值、单位、试样/配方键、
工况、准入状态和原始定位统一成 ``第十批ACS表格物化.RECORD_COLUMNS``。

本模块不创建训练/验证划分，不物化训练权重，也不修改原始目录。
"""

from __future__ import annotations

import csv
import hashlib
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from 审计.第十批ACS表格物化 import RECORD_COLUMNS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "数据/原始/外部数据/新增开放数据"
AUDIT_VERSION = "batch11-existing-experimental-scalars-v1"


@dataclass(frozen=True)
class SourceSpec:
    directory: str
    filename: str
    rows: int
    size: int
    sha256: str
    license: str
    doi: str
    required_columns: frozenset[str]

    @property
    def path(self) -> Path:
        return DATA_ROOT / self.directory / self.filename


SPECS: dict[str, SourceSpec] = {
    "fdm": SourceSpec(
        directory="Mendeley_FDM_TPU晶格与基材力学",
        filename="标量审计清单.tsv",
        rows=1_206,
        size=623_948,
        sha256="b65142feaed3ab276f4f6870369e479730f3320de98fb1bfc1a51b4bb419cbcc",
        license="CC BY 4.0",
        doi="10.17632/dbzdkz95f8.1",
        required_columns=frozenset(
            {
                "来源",
                "工作簿",
                "试样组",
                "试样ID",
                "observable",
                "value",
                "unit",
                "definition_id",
                "scalar_lineage_class",
                "source_summary_state",
                "source_summary_evidence",
                "formula_target_specimen_id",
                "quality_gate",
                "备注",
            }
        ),
    ),
    "spore": SourceSpec(
        directory="第七批补充材料_孢子填充TPU",
        filename="标量审计清单.tsv",
        rows=144,
        size=12_206,
        sha256="48dc8191c7630310a941bdc1e74eecfe2862f00d0a45a467c861f9115004517c",
        license="CC BY 4.0",
        doi="10.1038/s41467-024-47132-8",
        required_columns=frozenset(
            {
                "scalar_id",
                "source_sheet",
                "source_figure",
                "spore_type",
                "spore_wt_pct",
                "formulation_id",
                "replicate_source_order",
                "metric",
                "value",
                "unit",
                "source_cell",
            }
        ),
    ),
    "sheffield": SourceSpec(
        directory="第九批实验_Sheffield_PU理性设计",
        filename="标量审计清单.tsv",
        rows=764,
        size=312_960,
        sha256="bd9afe4642acd14769419b92862a9820458ed2a1b0ccc21f1154e5ae4c806f2c",
        license="CC BY 4.0",
        doi="10.15131/shef.data.21510876.v1",
        required_columns=frozenset(
            {
                "scalar_id",
                "sample_id",
                "experiment",
                "formulation_id",
                "split_group",
                "observable",
                "value",
                "unit",
                "source_location",
                "target_origin",
                "data_origin",
                "derivation",
                "gold_admission_status",
                "future_weight_ceiling",
                "is_external_control",
                "chemistry_resolution",
                "notes",
            }
        ),
    ),
    "sls": SourceSpec(
        directory="第八批实验_SLS_TPU晶格工艺",
        filename="标量审计清单.tsv",
        rows=375,
        size=285_447,
        sha256="2cbe217252e422fd69dbac1af97d2039dd510ff341501a8efa89e876bad52a0a",
        license="CC BY 4.0",
        doi="10.6084/m9.figshare.31550614.v1",
        required_columns=frozenset(
            {
                "scalar_id",
                "specimen_id",
                "source_location",
                "condition_id",
                "replicate_index",
                "split_group",
                "observable",
                "value",
                "unit",
                "unit_status",
                "target_origin",
                "data_origin",
                "quality_gate",
                "gold_admission_status",
                "future_weight_ceiling",
                "laser_power_w",
                "scan_speed_mm_s",
                "hatch_distance_mm",
                "layer_thickness_mm",
                "energy_density_areal_j_mm2",
                "energy_density_volumetric_j_mm3",
                "chemistry_resolution",
                "geometry_resolution",
                "notes",
            }
        ),
    ),
    "literature": SourceSpec(
        directory="第八批实验_TPU文献力学汇总",
        filename="标量审计清单.tsv",
        rows=186,
        size=120_749,
        sha256="a2e8f91b250a59b2d44eca9e93c1ed290501c92bd8473d63d195147fcc8e1b14",
        license="CC BY 4.0",
        doi="10.17632/ftntxg4zdz.1",
        required_columns=frozenset(
            {
                "scalar_id",
                "record_id",
                "source_location",
                "split_group",
                "production_technique",
                "observable",
                "value",
                "unit",
                "target_origin",
                "data_origin",
                "quality_gate",
                "future_weight_ceiling",
                "reference_group_id",
                "reference_doi",
                "reference_text",
                "notes",
            }
        ),
    ),
}


class AuditBlocked(RuntimeError):
    """冻结输入、字段、数量或数值发生漂移。"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_tsv(spec: SourceSpec) -> list[dict[str, str]]:
    if not spec.path.is_file():
        raise AuditBlocked(f"缺少审计表：{spec.path}")
    if spec.path.stat().st_size != spec.size or _sha256(spec.path) != spec.sha256:
        raise AuditBlocked(f"审计表字节身份漂移：{spec.path}")
    with spec.path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fields = set(reader.fieldnames or ())
        if not spec.required_columns.issubset(fields):
            raise AuditBlocked(
                f"审计表字段缺失：{spec.directory}; "
                f"missing={sorted(spec.required_columns - fields)}"
            )
        rows = list(reader)
    if len(rows) != spec.rows:
        raise AuditBlocked(
            f"审计表行数漂移：{spec.directory}; expected={spec.rows}, actual={len(rows)}"
        )
    return rows


def verify_inputs() -> list[dict[str, Any]]:
    verified: list[dict[str, Any]] = []
    for spec in SPECS.values():
        rows = _read_tsv(spec)
        verified.append(
            {
                "source_directory": spec.directory,
                "filename": spec.filename,
                "rows": len(rows),
                "bytes": spec.size,
                "sha256": spec.sha256,
                "license": spec.license,
                "doi": spec.doi,
            }
        )
    return verified


def _finite(value: Any, label: str) -> float:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise AuditBlocked(f"{label} 无法解析为数值：{value!r}") from exc
    if not math.isfinite(number):
        raise AuditBlocked(f"{label} 不是有限数值：{value!r}")
    return number


def _stable_id(prefix: str, *parts: Any) -> str:
    text = "|".join(str(part) for part in parts)
    return prefix + hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")
    if slug:
        return slug
    return "unicode_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _record(
    spec: SourceSpec,
    *,
    source_record_id: str,
    observation_id: str,
    formulation_id: str,
    sample_id: str,
    record_kind: str,
    property_name: str,
    value: Any,
    unit: str,
    data_origin: str,
    reduction_level: str,
    method_or_test_protocol: str,
    fidelity_level: str,
    gold_admission_status: str,
    mapping_status: str,
    protocol_status: str,
    potential_weight_ceiling: Any,
    split_group: str,
    source_locator: str,
    notes: str,
    component_name: str = "",
    component_role: str = "",
    condition_name: str = "",
    condition_value: Any = "",
    condition_unit: str = "",
) -> dict[str, Any]:
    if gold_admission_status not in {
        "admitted_reference",
        "conditional_reference",
    }:
        raise AuditBlocked(f"非法 Gold-E 准入状态：{gold_admission_status}")
    row = {
        "source_directory": spec.directory,
        "source_record_id": source_record_id,
        "observation_id": observation_id,
        "formulation_id": formulation_id,
        "sample_id": sample_id,
        "record_kind": record_kind,
        "component_name": component_name,
        "component_role": component_role,
        "property_name": property_name,
        "value": _finite(value, f"{spec.directory}/{observation_id}"),
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
        "potential_weight_ceiling": _finite(
            potential_weight_ceiling, f"{observation_id}/potential_weight_ceiling"
        ),
        "current_weight_materialized": "false",
        "training_weight": "",
        "split_group": split_group,
        "source_locator": source_locator,
        "file_sha256": spec.sha256,
        "license": spec.license,
        "citation_keys": "",
        "notes": notes,
    }
    if set(row) != set(RECORD_COLUMNS):
        raise AuditBlocked(f"Gold-E 字段漂移：{spec.directory}")
    return row


def _fdm_rows(spec: SourceSpec, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    modality_map = {
        "基材拉伸": "base_tensile",
        "基材弯曲": "base_bending",
        "晶格弯曲": "lattice_bending",
        "晶格压缩": "lattice_compression",
    }
    descriptor_units = {"mm", "mm²", "mm/mm", "Nmm²", ""}
    for index, source in enumerate(rows, start=1):
        # 36 行是已知的工作簿 ID/公式冲突证据，审计表明确不发布数值；
        # 它们继续留在样本清单的 evidence 记录中，不伪造成 Gold-E 数值。
        if not source["value"].strip():
            continue
        group_prefix = source["试样组"].split("|", 1)[0]
        modality = modality_map.get(group_prefix, "source_mode")
        observable = source["observable"]
        sample_id = (
            source.get("formula_target_specimen_id")
            or source.get("source_display_id")
            or source["试样ID"]
        )
        quality_gate = source["quality_gate"]
        if quality_gate == "pass_source_summary_selected":
            admission = "admitted_reference"
            ceiling = 0.10 if source["unit"] in descriptor_units else 0.35
        elif quality_gate == "hold_source_summary_not_selected":
            admission = "conditional_reference"
            ceiling = 0.10
        elif quality_gate == "hold_source_summary_conflict":
            admission = "conditional_reference"
            ceiling = 0.0
        else:
            raise AuditBlocked(f"未知 FDM quality_gate：{quality_gate}")
        output.append(
            _record(
                spec,
                source_record_id=f"fdm|{source['试样组']}|{sample_id}",
                observation_id=_stable_id(
                    "fdm_scalar_",
                    source["工作簿"],
                    source["试样组"],
                    sample_id,
                    source["definition_id"],
                    index,
                ),
                formulation_id="commercial_TPU_FDM_shared_chemistry",
                sample_id=sample_id,
                record_kind=(
                    "specimen_or_geometry_descriptor"
                    if source["unit"] in descriptor_units
                    else "mechanical_property"
                ),
                property_name=f"fdm_{modality}__{_slug(observable)}",
                value=source["value"],
                unit=source["unit"],
                data_origin="experimental_primary_workbook_or_source_formula",
                reduction_level=source["scalar_lineage_class"],
                method_or_test_protocol=(
                    f"source workbook={source['工作簿']}; "
                    f"definition_id={source['definition_id']}"
                ),
                fidelity_level="experimental_specimen_scalar",
                gold_admission_status=admission,
                mapping_status=f"specimen_resolved;{quality_gate}",
                protocol_status="source_workbook_protocol_partial",
                potential_weight_ceiling=ceiling,
                split_group=f"{spec.directory}|{source['试样组']}",
                source_locator=(
                    f"{spec.filename}#row={index + 1};"
                    f"{source['source_summary_evidence']}"
                ),
                notes=f"original_observable={observable}; {source['备注']}",
            )
        )
    return output


def _spore_rows(spec: SourceSpec, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    metric_map = {
        "tensile_stress": "tensile_strength",
        "elongation_at_break": "elongation_at_break",
        "young_modulus": "young_modulus",
        "toughness": "toughness",
    }
    output: list[dict[str, Any]] = []
    for index, source in enumerate(rows, start=1):
        replicate = source["replicate_source_order"]
        sample_id = f"{source['formulation_id']}|replicate_{replicate}"
        output.append(
            _record(
                spec,
                source_record_id=f"spore_tpu|{sample_id}",
                observation_id=source["scalar_id"],
                formulation_id=source["formulation_id"],
                sample_id=sample_id,
                record_kind="mechanical_property",
                component_name=source["spore_type"],
                component_role="living_filler",
                property_name=metric_map[source["metric"]],
                value=source["value"],
                unit=source["unit"],
                condition_name="spore_loading",
                condition_value=_finite(source["spore_wt_pct"], source["scalar_id"]),
                condition_unit="wt%",
                data_origin="experimental_primary_workbook",
                reduction_level="direct_replicate_measurement",
                method_or_test_protocol=(
                    "Instron 5982; 100 N load cell; 20 mm/min; "
                    "dogbone tensile test to fracture"
                ),
                fidelity_level="experimental_replicate_scalar",
                gold_admission_status="admitted_reference",
                mapping_status="formulation_filler_loading_and_replicate_resolved",
                protocol_status="complete_source_protocol",
                potential_weight_ceiling=0.8,
                split_group=f"{spec.directory}|{source['formulation_id']}",
                source_locator=(
                    f"{spec.filename}#{source['scalar_id']};"
                    f"{source['source_sheet']}!{source['source_cell']}"
                ),
                notes=(
                    f"source_figure={source['source_figure']}; "
                    "commercial TPU chemistry is product-level, not monomer-resolved"
                ),
            )
        )
    return output


def _sheffield_rows(
    spec: SourceSpec, rows: list[dict[str, str]]
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source in rows:
        if (
            source["gold_admission_status"] == "evidence_only"
            or source["is_external_control"].strip().lower() == "true"
        ):
            continue
        admission = source["gold_admission_status"]
        output.append(
            _record(
                spec,
                source_record_id=f"sheffield|{source['sample_id']}",
                observation_id=source["scalar_id"],
                formulation_id=source["formulation_id"],
                sample_id=source["sample_id"],
                record_kind="foam_reaction_morphology_or_transport_property",
                property_name=source["observable"],
                value=source["value"],
                unit=source["unit"],
                condition_name="experiment_number",
                condition_value=_finite(source["experiment"], source["scalar_id"]),
                condition_unit="1",
                data_origin=source["data_origin"],
                reduction_level=source["derivation"],
                method_or_test_protocol="official raw data plus published article methods",
                fidelity_level="experimental_batch_or_derived_scalar",
                gold_admission_status=admission,
                mapping_status=source["chemistry_resolution"],
                protocol_status="source_data_and_article_protocol_available",
                potential_weight_ceiling=source["future_weight_ceiling"],
                split_group=source["split_group"],
                source_locator=f"{spec.filename}#{source['scalar_id']};{source['source_location']}",
                notes=source["notes"],
            )
        )
    return output


def _sls_rows(spec: SourceSpec, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source in rows:
        process = (
            f"SLS LP={source['laser_power_w']} W; "
            f"SS={source['scan_speed_mm_s']} mm/s; "
            f"HD={source['hatch_distance_mm']} mm; "
            f"LT={source['layer_thickness_mm']} mm; "
            f"areal={source['energy_density_areal_j_mm2']} J/mm2; "
            f"volumetric={source['energy_density_volumetric_j_mm3']} J/mm3"
        )
        output.append(
            _record(
                spec,
                source_record_id=f"sls_tpu|{source['specimen_id']}",
                observation_id=source["scalar_id"],
                formulation_id="commercial_TPU_01GR",
                sample_id=source["specimen_id"],
                record_kind=(
                    "specimen_descriptor"
                    if source["observable"] == "specimen_weight"
                    else "mechanical_application_property"
                ),
                property_name=source["observable"],
                value=source["value"],
                unit=source["unit"],
                condition_name="replicate_index",
                condition_value=_finite(source["replicate_index"], source["scalar_id"]),
                condition_unit="1",
                data_origin=source["data_origin"],
                reduction_level="specimen_endpoint",
                method_or_test_protocol=process,
                fidelity_level="experimental_process_specimen_scalar",
                gold_admission_status=source["gold_admission_status"],
                mapping_status=(
                    f"{source['chemistry_resolution']};{source['geometry_resolution']}"
                ),
                protocol_status=(
                    "unit_closed" if source["unit_status"] == "closed" else "unit_unresolved"
                ),
                potential_weight_ceiling=source["future_weight_ceiling"],
                split_group=source["split_group"],
                source_locator=f"{spec.filename}#{source['scalar_id']};{source['source_location']}",
                notes=source["notes"],
            )
        )
    return output


def _literature_rows(
    spec: SourceSpec, rows: list[dict[str, str]]
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source in rows:
        output.append(
            _record(
                spec,
                source_record_id=f"literature_tpu|{source['record_id']}",
                observation_id=source["scalar_id"],
                formulation_id=source["record_id"],
                sample_id=source["record_id"],
                record_kind="literature_aggregate_property",
                property_name=source["observable"],
                value=source["value"],
                unit=source["unit"],
                data_origin=source["data_origin"],
                reduction_level="literature_aggregate",
                method_or_test_protocol=(
                    f"production_technique={source['production_technique']}; "
                    f"underlying_reference={source['reference_doi']}"
                ),
                fidelity_level="experimental_literature_aggregate",
                gold_admission_status="conditional_reference",
                mapping_status="reference_group_resolved_chemistry_unresolved",
                protocol_status="partial_underlying_article_protocol",
                potential_weight_ceiling=source["future_weight_ceiling"],
                split_group=source["split_group"],
                source_locator=f"{spec.filename}#{source['scalar_id']};{source['source_location']}",
                notes=f"{source['notes']} reference={source['reference_text']}",
            )
        )
    return output


BUILDERS: dict[
    str, Callable[[SourceSpec, list[dict[str, str]]], list[dict[str, Any]]]
] = {
    "fdm": _fdm_rows,
    "spore": _spore_rows,
    "sheffield": _sheffield_rows,
    "sls": _sls_rows,
    "literature": _literature_rows,
}


def build_records() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    verified = verify_inputs()
    records: list[dict[str, Any]] = []
    for key, spec in SPECS.items():
        records.extend(BUILDERS[key](spec, _read_tsv(spec)))

    if len(records) != 2_630:
        raise AuditBlocked(f"第十一批 Gold-E 行数漂移：{len(records)}")
    observation_ids = [str(row["observation_id"]) for row in records]
    if len(set(observation_ids)) != len(observation_ids):
        raise AuditBlocked("第十一批 Gold-E observation_id 不唯一")
    if any(set(row) != set(RECORD_COLUMNS) for row in records):
        raise AuditBlocked("第十一批 Gold-E 字段集合漂移")
    if any(row["training_weight"] != "" for row in records):
        raise AuditBlocked("第十一批不得提前物化训练权重")

    source_counts = Counter(row["source_directory"] for row in records)
    admission_counts = Counter(row["gold_admission_status"] for row in records)
    if source_counts != {
        SPECS["fdm"].directory: 1_170,
        SPECS["spore"].directory: 144,
        SPECS["sheffield"].directory: 755,
        SPECS["sls"].directory: 375,
        SPECS["literature"].directory: 186,
    }:
        raise AuditBlocked(f"第十一批来源计数漂移：{source_counts}")
    if admission_counts != {
        "admitted_reference": 2_102,
        "conditional_reference": 528,
    }:
        raise AuditBlocked(f"第十一批准入计数漂移：{admission_counts}")

    return records, {
        "audit_version": AUDIT_VERSION,
        "status": "pass",
        "verified_inputs": verified,
        "record_count": len(records),
        "source_record_counts": dict(source_counts),
        "admission_counts": dict(admission_counts),
        "training_split_materialized": False,
        "training_weight_materialized": False,
    }


if __name__ == "__main__":
    rows, audit = build_records()
    print(f"records={len(rows)}")
    print(audit)
