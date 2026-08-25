"""为6条现实实验短名单生成批次、试样和测量任务的空白可追溯回填模板。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
MEASUREMENT_TASKS = [
    ("reaction_identity", "FTIR_NCO_conversion", "raw_spectrum_and_derived_conversion"),
    ("molecular_weight", "GPC_Mn_Mw_PDI", "raw_chromatogram_and_calibration"),
    ("thermal", "DSC_transition", "raw_heat_flow_curve"),
    ("viscoelastic", "DMA_temperature_sweep", "Eprime_Edoubleprime_tandelta_curve"),
    ("mechanical", "tensile_full_curve", "stress_strain_curve_per_specimen"),
    ("cyclic", "cyclic_hysteresis_recovery", "cycle_resolved_curve_per_specimen"),
    ("physical", "density", "specimen_level_value"),
    ("aging", "selected_aging_retention", "paired_pre_post_measurements_if_authorized"),
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def build_batch_template(shortlist: pd.DataFrame) -> pd.DataFrame:
    required = {
        "experiment_order",
        "experiment_stage",
        "formulation_id",
        "diisocyanate_id",
        "macrodiol_id",
        "chain_extender_id",
        "hard_segment_mass_fraction_target",
        "nco_oh_ratio_target",
        "experiment_release_status_current",
    }
    missing = sorted(required.difference(shortlist.columns))
    if missing:
        raise ValueError(f"实验回填模板短名单缺字段: {missing}")
    if len(shortlist) != 6 or not shortlist["formulation_id"].is_unique:
        raise ValueError("实验回填模板要求6条唯一短名单")
    rows = []
    for source in shortlist.sort_values("experiment_order").to_dict(orient="records"):
        rows.append(
            {
                "formulation_id": source["formulation_id"],
                "experiment_order": source["experiment_order"],
                "experiment_stage": source["experiment_stage"],
                "planned_diisocyanate_id": source["diisocyanate_id"],
                "planned_macrodiol_id": source["macrodiol_id"],
                "planned_chain_extender_id": source["chain_extender_id"],
                "planned_hard_segment_mass_fraction": source[
                    "hard_segment_mass_fraction_target"
                ],
                "planned_nco_oh_ratio": source["nco_oh_ratio_target"],
                "batch_id": "",
                "synthesis_date": "",
                "operator_id": "",
                "laboratory_site": "",
                "diisocyanate_lot": "",
                "macrodiol_lot": "",
                "chain_extender_lot": "",
                "diisocyanate_coa_sha256": "",
                "macrodiol_coa_sha256": "",
                "chain_extender_coa_sha256": "",
                "sds_review_record_id": "",
                "ehs_approval_record_id": "",
                "macrodiol_oh_number_mg_koh_g": pd.NA,
                "macrodiol_water_ppm": pd.NA,
                "macrodiol_mn_g_mol": pd.NA,
                "macrodiol_mw_g_mol": pd.NA,
                "macrodiol_pdi": pd.NA,
                "diisocyanate_assay_mass_fraction": pd.NA,
                "chain_extender_assay_mass_fraction": pd.NA,
                "actual_component_masses_g_json": "",
                "actual_nco_oh_ratio": pd.NA,
                "catalyst_identity": "",
                "catalyst_loading_basis": "",
                "catalyst_loading_value": pd.NA,
                "synthesis_route_one_or_two_step": "",
                "prepolymer_temperature_c": pd.NA,
                "prepolymer_time_min": pd.NA,
                "chain_extension_temperature_c": pd.NA,
                "chain_extension_time_min": pd.NA,
                "molding_temperature_c": pd.NA,
                "cure_temperature_c": pd.NA,
                "cure_time_h": pd.NA,
                "anneal_temperature_c": pd.NA,
                "anneal_time_h": pd.NA,
                "process_atmosphere": "",
                "conversion_measurement_id": "",
                "material_sample_id": "",
                "process_notes": "",
                "planned_release_status": source[
                    "experiment_release_status_current"
                ],
                "actual_release_status": "not_started",
                "gold_e_ingestion_status": "not_ready_missing_real_batch",
            }
        )
    return pd.DataFrame(rows)


def build_measurement_template(shortlist: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for source in shortlist.sort_values("experiment_order").to_dict(orient="records"):
        for task_order, (family, task, raw_requirement) in enumerate(
            MEASUREMENT_TASKS, start=1
        ):
            rows.append(
                {
                    "formulation_id": source["formulation_id"],
                    "experiment_stage": source["experiment_stage"],
                    "measurement_task_order": task_order,
                    "measurement_family": family,
                    "measurement_task": task,
                    "raw_data_requirement": raw_requirement,
                    "batch_id": "",
                    "material_sample_id": "",
                    "specimen_id": "",
                    "replicate_id": "",
                    "protocol_standard_or_sop": "",
                    "instrument_id": "",
                    "calibration_record_id": "",
                    "test_temperature_c": pd.NA,
                    "test_rate_or_frequency": pd.NA,
                    "test_rate_or_frequency_unit": "",
                    "raw_file_path": "",
                    "raw_file_sha256": "",
                    "processed_file_path": "",
                    "processed_file_sha256": "",
                    "unit_status": "not_recorded",
                    "qc_status": "not_run",
                    "gold_e_record_status": "not_ready",
                    "performance_claim_status": "no_claim_before_qc",
                }
            )
    return pd.DataFrame(rows)


def write_release(
    shortlist_path: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    if not shortlist_path.is_file():
        raise ValueError(f"实验回填短名单不存在: {shortlist_path}")
    shortlist = pd.read_csv(shortlist_path)
    batches = build_batch_template(shortlist)
    measurements = build_measurement_template(shortlist)
    output_root.mkdir(parents=True, exist_ok=True)
    batch_path = output_root / "实验批次回填模板.csv"
    measurement_path = output_root / "实验测量计划模板.csv"
    report_path = output_root / "实验回填字段说明.md"
    _atomic_text(batch_path, batches.to_csv(index=False, float_format="%.12g"))
    _atomic_text(
        measurement_path, measurements.to_csv(index=False, float_format="%.12g")
    )
    _atomic_text(
        report_path,
        "\n".join(
            [
                "# 实验回填字段说明",
                "",
                "本目录是空白数据采集模板，不是实验授权、SOP或已完成数据。批次表一行对应一次真实合成批次；同一配方的重复批次必须新增行并使用不同`batch_id`，不得覆盖。",
                "",
                "原料必须记录真实批号、CoA文件SHA-256、SDS/EHS审批、PTMG OH值/含水量/Mn/Mw/PDI以及实际称量。计划NCO/OH和硬段分数不能代替批次实测/实配值。",
                "",
                "测量计划一行只定义任务类型。每个真实试样和重复必须拆成独立行，保存原始文件及处理文件哈希。测试标准、重复数、仪器、速率、温度和单位由实验负责人及本单位SOP确定，模板不擅自填充。",
                "",
                "只有批次身份、原始数据、单位、协议、QC和泄漏组均闭合的记录才可进入Gold-E；未完成字段保持空值，不填0、不用计算预测代替实验。",
                "",
            ]
        ),
    )
    files = [batch_path, measurement_path, report_path]
    manifest = {
        "release_id": release_id,
        "status": "empty_experiment_capture_templates_ready_no_experiment_authorization",
        "counts": {
            "planned_formulations": len(batches),
            "measurement_task_rows": len(measurements),
            "measurement_families": measurements["measurement_family"].nunique(),
        },
        "input": {
            "path": str(shortlist_path),
            "bytes": shortlist_path.stat().st_size,
            "sha256": sha256(shortlist_path),
        },
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "experiment_authorization": "not_granted_by_template",
        "gold_e_ingestion_status": "blocked_until_real_batches_and_qc",
        "performance_claim_status": "no_performance_claim",
    }
    _atomic_text(
        output_root / "实验回填模板发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--短名单",
        type=Path,
        default=ROOT / "结果" / "现实筛选" / "实验短名单" / "实验短名单6.csv",
    )
    parser.add_argument(
        "--输出目录",
        type=Path,
        default=ROOT / "结果" / "现实筛选" / "实验短名单",
    )
    parser.add_argument(
        "--发布ID", default="tpu-reality-experiment-capture-template-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.短名单, args.输出目录, release_id=args.发布ID
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
