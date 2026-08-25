"""生成模型预测与真实Gold-E实验配对、残差校准的空白闭环模板。"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from 汇总RESP敏感性 import sha256


ROOT = Path(__file__).resolve().parents[1]
CALIBRATION_TASKS = [
    "FTIR_NCO_conversion",
    "GPC_Mn_Mw_PDI",
    "DMA_temperature_sweep",
    "tensile_full_curve",
    "density",
]


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def build_template(shortlist: pd.DataFrame, gold_e_audit: pd.DataFrame) -> pd.DataFrame:
    required_shortlist = {"formulation_id", "experiment_order", "experiment_stage"}
    required_audit = {"formulation_id", "gold_e_admission_status"}
    missing = {
        "shortlist": sorted(required_shortlist.difference(shortlist.columns)),
        "gold_e_audit": sorted(required_audit.difference(gold_e_audit.columns)),
    }
    if any(missing.values()):
        raise ValueError(f"模型实验闭环模板输入缺字段: {missing}")
    if len(shortlist) != 6 or not shortlist["formulation_id"].is_unique:
        raise ValueError("模型实验闭环模板要求6条唯一实验短名单")
    if not gold_e_audit["formulation_id"].is_unique:
        raise ValueError("模型实验闭环Gold-E审计ID不唯一")
    joined = shortlist[["formulation_id", "experiment_order", "experiment_stage"]].merge(
        gold_e_audit[["formulation_id", "gold_e_admission_status"]],
        on="formulation_id",
        how="left",
        validate="one_to_one",
    )
    if joined["gold_e_admission_status"].isna().any():
        raise ValueError("模型实验闭环短名单未完全连接Gold-E审计")
    rows = []
    for source in joined.sort_values("experiment_order").to_dict(orient="records"):
        for task in CALIBRATION_TASKS:
            rows.append(
                {
                    "formulation_id": source["formulation_id"],
                    "experiment_order": source["experiment_order"],
                    "experiment_stage": source["experiment_stage"],
                    "measurement_task": task,
                    "target_property": "",
                    "condition_key": "",
                    "model_id": "",
                    "model_training_release_id": "",
                    "prediction_value": pd.NA,
                    "prediction_unit": "",
                    "prediction_std": pd.NA,
                    "prediction_lower": pd.NA,
                    "prediction_upper": pd.NA,
                    "prediction_domain_status": "not_evaluated",
                    "batch_id": "",
                    "material_sample_id": "",
                    "gold_e_record_id": "",
                    "experimental_value": pd.NA,
                    "experimental_unit": "",
                    "experimental_std": pd.NA,
                    "experimental_qc_status": "not_run",
                    "training_batch_leakage_status": "not_evaluated",
                    "calibration_role": "holdout_calibration_only",
                    "residual_experiment_minus_prediction": pd.NA,
                    "standardized_residual": pd.NA,
                    "gold_e_admission_status": source["gold_e_admission_status"],
                    "closed_loop_status": "blocked_missing_prediction_or_gold_e",
                    "model_update_permission": "blocked",
                }
            )
    return pd.DataFrame(rows)


def compute_residuals(template: pd.DataFrame) -> pd.DataFrame:
    required = {
        "prediction_value",
        "prediction_unit",
        "prediction_std",
        "prediction_domain_status",
        "experimental_value",
        "experimental_unit",
        "experimental_std",
        "experimental_qc_status",
        "training_batch_leakage_status",
        "calibration_role",
        "gold_e_admission_status",
    }
    missing = sorted(required.difference(template.columns))
    if missing:
        raise ValueError(f"模型实验残差输入缺字段: {missing}")
    output = template.copy()
    output["residual_experiment_minus_prediction"] = pd.NA
    output["standardized_residual"] = pd.NA
    output["closed_loop_status"] = "blocked_missing_prediction_or_gold_e"
    output["model_update_permission"] = "blocked"
    for index, row in output.iterrows():
        prediction = pd.to_numeric(row["prediction_value"], errors="coerce")
        experiment = pd.to_numeric(row["experimental_value"], errors="coerce")
        prediction_std = pd.to_numeric(row["prediction_std"], errors="coerce")
        experiment_std = pd.to_numeric(row["experimental_std"], errors="coerce")
        if pd.isna(prediction) or pd.isna(experiment):
            continue
        conditions = {
            "domain": row["prediction_domain_status"] == "in_domain",
            "gold_e": row["gold_e_admission_status"] == "ready_for_gold_e_ingestion",
            "qc": row["experimental_qc_status"] == "passed",
            "unit": bool(str(row["prediction_unit"]).strip())
            and row["prediction_unit"] == row["experimental_unit"],
            "leakage": row["training_batch_leakage_status"] == "batch_not_in_training",
            "role": row["calibration_role"] == "holdout_calibration_only",
            "uncertainty": not pd.isna(prediction_std)
            and not pd.isna(experiment_std)
            and float(prediction_std) >= 0
            and float(experiment_std) >= 0,
        }
        failed = [name for name, passed in conditions.items() if not passed]
        if failed:
            output.at[index, "closed_loop_status"] = "blocked_" + "_".join(failed)
            continue
        residual = float(experiment) - float(prediction)
        combined_std = math.sqrt(float(prediction_std) ** 2 + float(experiment_std) ** 2)
        output.at[index, "residual_experiment_minus_prediction"] = residual
        output.at[index, "standardized_residual"] = (
            residual / combined_std if combined_std > 0 else pd.NA
        )
        output.at[index, "closed_loop_status"] = "ready_holdout_residual"
        output.at[index, "model_update_permission"] = (
            "eligible_for_calibration_analysis_not_automatic_retraining"
        )
    return output


def write_release(
    shortlist_path: Path,
    gold_e_audit_path: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    for path in (shortlist_path, gold_e_audit_path):
        if not path.is_file():
            raise ValueError(f"模型实验闭环输入不存在: {path}")
    template = build_template(pd.read_csv(shortlist_path), pd.read_csv(gold_e_audit_path))
    template = compute_residuals(template)
    output_root.mkdir(parents=True, exist_ok=True)
    table_out = output_root / "模型实验闭环模板.csv"
    report_out = output_root / "模型实验闭环说明.md"
    _atomic_text(table_out, template.to_csv(index=False))
    ready = int(template["closed_loop_status"].eq("ready_holdout_residual").sum())
    _atomic_text(
        report_out,
        "\n".join(
            [
                "# 模型—实验残差闭环",
                "",
                f"- 配方×必需任务行：{len(template)}",
                f"- 当前可计算残差：{ready}",
                "",
                "只有适用域内预测、Gold-E准入、实验QC通过、单位一致、批次未进入训练集且角色为独立留出时才计算残差。残差只允许进入校准分析，不自动触发重训练或把测试批次泄漏回模型选择。",
                "",
            ]
        ),
    )
    files = [table_out, report_out]
    manifest = {
        "release_id": release_id,
        "status": (
            "model_experiment_residual_pairs_ready"
            if ready
            else "model_experiment_loop_blocked_no_real_gold_e_pairs"
        ),
        "counts": {"rows": len(template), "ready_residuals": ready, "blocked": len(template) - ready},
        "calibration_tasks": CALIBRATION_TASKS,
        "inputs": {
            path.name: {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in (shortlist_path, gold_e_audit_path)
        },
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "automatic_retraining_permission": "blocked_requires_reviewed_calibration_analysis",
    }
    _atomic_text(
        output_root / "模型实验闭环发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--短名单", type=Path, required=True)
    parser.add_argument("--GoldE审计", type=Path, required=True)
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument("--发布ID", required=True)
    args = parser.parse_args(argv)
    manifest = write_release(
        args.短名单, args.GoldE审计, args.输出目录, release_id=args.发布ID
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
