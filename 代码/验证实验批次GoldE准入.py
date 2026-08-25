"""审计实验批次与测量证据是否达到Gold-E准入，不制造实验值。"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from 汇总RESP敏感性 import sha256


ROOT = Path(__file__).resolve().parents[1]
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MANDATORY_TASKS = {
    "FTIR_NCO_conversion",
    "GPC_Mn_Mw_PDI",
    "DMA_temperature_sweep",
    "tensile_full_curve",
    "density",
}
BATCH_REQUIRED_TEXT = [
    "batch_id",
    "synthesis_date",
    "operator_id",
    "laboratory_site",
    "diisocyanate_lot",
    "macrodiol_lot",
    "chain_extender_lot",
    "diisocyanate_coa_sha256",
    "macrodiol_coa_sha256",
    "chain_extender_coa_sha256",
    "sds_review_record_id",
    "ehs_approval_record_id",
    "actual_component_masses_g_json",
    "catalyst_identity",
    "catalyst_loading_basis",
    "synthesis_route_one_or_two_step",
    "process_atmosphere",
    "conversion_measurement_id",
    "material_sample_id",
]
BATCH_REQUIRED_NUMERIC = [
    "macrodiol_oh_number_mg_koh_g",
    "macrodiol_water_ppm",
    "macrodiol_mn_g_mol",
    "macrodiol_mw_g_mol",
    "macrodiol_pdi",
    "diisocyanate_assay_mass_fraction",
    "chain_extender_assay_mass_fraction",
    "actual_nco_oh_ratio",
    "catalyst_loading_value",
]
MEASUREMENT_REQUIRED_TEXT = [
    "batch_id",
    "material_sample_id",
    "specimen_id",
    "replicate_id",
    "protocol_standard_or_sop",
    "instrument_id",
    "calibration_record_id",
    "raw_file_path",
    "raw_file_sha256",
    "processed_file_path",
    "processed_file_sha256",
]


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _missing(value: object) -> bool:
    return pd.isna(value) or not str(value).strip()


def _valid_sha(value: object) -> bool:
    return not _missing(value) and bool(SHA256_PATTERN.fullmatch(str(value).lower()))


def audit_batch_metadata(row: dict[str, Any]) -> tuple[bool, list[str]]:
    missing = [name for name in BATCH_REQUIRED_TEXT if _missing(row.get(name))]
    for name in BATCH_REQUIRED_NUMERIC:
        value = pd.to_numeric(row.get(name), errors="coerce")
        if pd.isna(value):
            missing.append(name)
            continue
        if name == "macrodiol_water_ppm":
            valid = float(value) >= 0
        elif name == "macrodiol_pdi":
            valid = float(value) >= 1
        elif name.endswith("assay_mass_fraction"):
            valid = 0 < float(value) <= 1
        else:
            valid = float(value) > 0
        if not valid:
            missing.append(name)
    for name in (
        "diisocyanate_coa_sha256",
        "macrodiol_coa_sha256",
        "chain_extender_coa_sha256",
    ):
        if not _missing(row.get(name)) and not _valid_sha(row.get(name)):
            missing.append(f"{name}:invalid_sha256")
    masses_value = row.get("actual_component_masses_g_json")
    if not _missing(masses_value):
        try:
            masses = json.loads(str(masses_value))
            expected = {"diisocyanate_g", "macrodiol_g", "chain_extender_g"}
            if set(masses) != expected or any(float(masses[key]) <= 0 for key in expected):
                missing.append("actual_component_masses_g_json:invalid_components")
        except (json.JSONDecodeError, TypeError, ValueError):
            missing.append("actual_component_masses_g_json:invalid_json")
    if row.get("actual_release_status") != "qc_passed":
        missing.append("actual_release_status:qc_passed_required")
    if row.get("gold_e_ingestion_status") != "ready_for_gold_e":
        missing.append("gold_e_ingestion_status:ready_for_gold_e_required")
    missing = sorted(set(missing))
    return not missing, missing


def measurement_ready(row: dict[str, Any]) -> tuple[bool, list[str]]:
    missing = [name for name in MEASUREMENT_REQUIRED_TEXT if _missing(row.get(name))]
    for name in ("raw_file_sha256", "processed_file_sha256"):
        if not _missing(row.get(name)) and not _valid_sha(row.get(name)):
            missing.append(f"{name}:invalid_sha256")
    if row.get("unit_status") != "verified":
        missing.append("unit_status:verified_required")
    if row.get("qc_status") != "passed":
        missing.append("qc_status:passed_required")
    if row.get("gold_e_record_status") != "ready_for_gold_e":
        missing.append("gold_e_record_status:ready_for_gold_e_required")
    missing = sorted(set(missing))
    return not missing, missing


def audit_gold_e_admission(
    batches: pd.DataFrame, measurements: pd.DataFrame
) -> pd.DataFrame:
    for frame, label, required in [
        (batches, "批次", {"formulation_id", *BATCH_REQUIRED_TEXT, *BATCH_REQUIRED_NUMERIC}),
        (measurements, "测量", {"formulation_id", "measurement_task", *MEASUREMENT_REQUIRED_TEXT}),
    ]:
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise ValueError(f"实验Gold-E{label}表缺字段: {missing}")
    if not batches["formulation_id"].is_unique:
        raise ValueError("实验Gold-E批次模板formulation_id必须唯一")
    rows = []
    for batch in batches.sort_values("experiment_order", kind="stable").to_dict(
        orient="records"
    ):
        formulation_id = str(batch["formulation_id"])
        batch_ready, batch_missing = audit_batch_metadata(batch)
        subset = measurements.loc[
            measurements["formulation_id"].astype(str).eq(formulation_id)
        ].copy()
        planned_tasks = set(subset["measurement_task"].astype(str))
        if not MANDATORY_TASKS.issubset(planned_tasks):
            raise ValueError(f"实验Gold-E必需测量计划不闭合: {formulation_id}")
        ready_tasks = set()
        measurement_missing: dict[str, list[str]] = {}
        for measurement in subset.to_dict(orient="records"):
            ready, missing = measurement_ready(measurement)
            task = str(measurement["measurement_task"])
            if ready:
                ready_tasks.add(task)
            elif task in MANDATORY_TASKS:
                measurement_missing[task] = missing
        mandatory_ready = MANDATORY_TASKS.issubset(ready_tasks)
        admission_ready = batch_ready and mandatory_ready
        rows.append(
            {
                "formulation_id": formulation_id,
                "experiment_order": int(batch["experiment_order"]),
                "experiment_stage": batch["experiment_stage"],
                "batch_metadata_ready": batch_ready,
                "batch_missing_field_count": len(batch_missing),
                "batch_missing_fields": ";".join(batch_missing),
                "measurement_plan_row_count": len(subset),
                "mandatory_measurement_count": len(MANDATORY_TASKS),
                "mandatory_measurements_ready": len(MANDATORY_TASKS.intersection(ready_tasks)),
                "mandatory_measurements_missing_json": json.dumps(
                    measurement_missing, ensure_ascii=False, sort_keys=True
                ),
                "gold_e_admission_status": (
                    "ready_for_gold_e_ingestion"
                    if admission_ready
                    else "blocked_missing_batch_or_measurement_evidence"
                ),
                "performance_claim_permission": (
                    "eligible_after_record_level_ingestion_validation"
                    if admission_ready
                    else "no_claim_before_qc"
                ),
            }
        )
    return pd.DataFrame(rows)


def write_release(
    batch_path: Path,
    measurement_path: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    for path in (batch_path, measurement_path):
        if not path.is_file():
            raise ValueError(f"实验Gold-E准入输入不存在: {path}")
    audit = audit_gold_e_admission(
        pd.read_csv(batch_path), pd.read_csv(measurement_path)
    )
    output_root.mkdir(parents=True, exist_ok=True)
    audit_out = output_root / "实验GoldE准入状态.csv"
    report_out = output_root / "实验GoldE准入说明.md"
    _atomic_text(audit_out, audit.to_csv(index=False))
    ready = int(audit["gold_e_admission_status"].eq("ready_for_gold_e_ingestion").sum())
    _atomic_text(
        report_out,
        "\n".join(
            [
                "# 实验批次Gold-E准入审计",
                "",
                f"- 计划体系：{len(audit)}",
                f"- 当前可准入：{ready}",
                f"- 当前阻断：{len(audit) - ready}",
                "",
                "准入同时要求批次/原料/工艺身份、CoA与SDS/EHS记录、真实计量和FTIR、GPC、DMA、完整拉伸曲线、密度五类测量证据完成QC。空模板得到0可准入是预期结果，不以默认值或虚拟计算补齐实验字段。",
                "",
            ]
        ),
    )
    files = [audit_out, report_out]
    manifest = {
        "release_id": release_id,
        "status": (
            "experimental_gold_e_admission_ready"
            if ready == len(audit)
            else "experimental_gold_e_admission_blocked_missing_real_evidence"
        ),
        "counts": {"systems": len(audit), "ready": ready, "blocked": len(audit) - ready},
        "mandatory_measurements": sorted(MANDATORY_TASKS),
        "inputs": {
            path.name: {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in (batch_path, measurement_path)
        },
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "performance_claim_status": "no_claim_before_qc",
    }
    _atomic_text(
        output_root / "实验GoldE准入发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--批次", type=Path, required=True)
    parser.add_argument("--测量", type=Path, required=True)
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument("--发布ID", required=True)
    args = parser.parse_args(argv)
    manifest = write_release(
        args.批次, args.测量, args.输出目录, release_id=args.发布ID
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
