"""把NCO–OH预反应配对代理回连到12条高层DFT候选。"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from 配方系综特征 import prepare_pareto_input


ROOT = Path(__file__).resolve().parents[1]
OBJECTIVES: Mapping[str, str] = OrderedDict(
    (
        ("macrodiol_pair__best_association_energy_proxy_kcal_mol", "min"),
        ("chain_extender_pair__best_association_energy_proxy_kcal_mol", "min"),
        ("association_proxy_max_start_span_kcal_mol", "min"),
    )
)
PAIR_SPECS = {
    "macrodiol_pair": ("diisocyanate_macrodiol", "macrodiol_id"),
    "chain_extender_pair": (
        "diisocyanate_chain_extender",
        "chain_extender_id",
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required(frame: pd.DataFrame, columns: set[str], label: str) -> None:
    missing = sorted(columns.difference(frame.columns))
    if missing:
        raise ValueError(f"{label}缺少字段: {missing}")


def _truth(value: object) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes"}


def build_updated_priorities(
    formulations: pd.DataFrame, pair_results: pd.DataFrame
) -> pd.DataFrame:
    _required(
        formulations,
        {
            "high_level_dft_rank",
            "formulation_id",
            "planning_tier",
            "diisocyanate_id",
            "macrodiol_id",
            "chain_extender_id",
            "performance_claim_status",
        },
        "高层DFT候选",
    )
    if formulations.empty or not formulations["formulation_id"].is_unique:
        raise ValueError("高层DFT候选formulation_id必须非空唯一")
    if not formulations["performance_claim_status"].astype(str).eq(
        "no_performance_claim"
    ).all():
        raise ValueError("高层DFT候选不得包含性能宣称")
    _required(
        pair_results,
        {
            "pair_id",
            "pair_type",
            "diisocyanate_id",
            "oh_component_id",
            "pair_status",
            "pair_release_eligible",
            "completed_starts",
            "blocked_starts",
            "best_association_energy_proxy_kcal_mol",
            "median_association_energy_proxy_kcal_mol",
            "association_energy_start_span_kcal_mol",
            "best_task_slug",
        },
        "预反应逐配对结果",
    )
    identity = pair_results[
        ["pair_type", "diisocyanate_id", "oh_component_id"]
    ]
    if identity.duplicated().any() or not pair_results["pair_id"].is_unique:
        raise ValueError("预反应配对身份不唯一")
    indexed = pair_results.set_index(
        ["pair_type", "diisocyanate_id", "oh_component_id"], drop=False
    )
    output = formulations.copy()
    pair_status_columns: list[str] = []
    span_columns: list[str] = []
    payload: dict[str, object] = {}
    fields = (
        "pair_id",
        "pair_status",
        "pair_release_eligible",
        "completed_starts",
        "blocked_starts",
        "best_association_energy_proxy_kcal_mol",
        "median_association_energy_proxy_kcal_mol",
        "association_energy_start_span_kcal_mol",
        "best_task_slug",
    )
    for prefix, (pair_type, oh_column) in PAIR_SPECS.items():
        keys = list(
            zip(
                [pair_type] * len(output),
                output["diisocyanate_id"].astype(str),
                output[oh_column].astype(str),
            )
        )
        found = pd.Series([key in indexed.index for key in keys], index=output.index)
        for field in fields:
            payload[f"{prefix}__{field}"] = [
                indexed.loc[key, field] if key in indexed.index else pd.NA
                for key in keys
            ]
        status_column = f"{prefix}__join_status"
        eligibility = pd.Series(payload[f"{prefix}__pair_release_eligible"], index=output.index).map(
            _truth
        )
        payload[status_column] = np.select(
            [~found, found & ~eligibility],
            ["missing_pair_result", "incomplete_pair_result"],
            default="ready",
        )
        pair_status_columns.append(status_column)
        span_columns.append(
            f"{prefix}__association_energy_start_span_kcal_mol"
        )
    output = pd.concat(
        [output, pd.DataFrame(payload, index=output.index)], axis=1, copy=False
    )
    status_frame = output[pair_status_columns]
    output["prereaction_join_status"] = np.select(
        [
            status_frame.eq("missing_pair_result").any(axis=1),
            status_frame.ne("ready").any(axis=1),
        ],
        ["missing_pair_result", "incomplete_pair_result"],
        default="ready",
    )
    macro_energy = pd.to_numeric(
        output[
            "macrodiol_pair__best_association_energy_proxy_kcal_mol"
        ],
        errors="coerce",
    )
    extender_energy = pd.to_numeric(
        output[
            "chain_extender_pair__best_association_energy_proxy_kcal_mol"
        ],
        errors="coerce",
    )
    output["association_proxy_mean_kcal_mol"] = pd.concat(
        [macro_energy, extender_energy], axis=1
    ).mean(axis=1, skipna=False)
    output["association_proxy_balance_abs_difference_kcal_mol"] = (
        macro_energy - extender_energy
    ).abs()
    output["association_proxy_max_start_span_kcal_mol"] = output[
        span_columns
    ].apply(pd.to_numeric, errors="coerce").max(axis=1, skipna=False)
    gate = pd.DataFrame(
        {
            "prereaction_gate_status": np.where(
                output["prereaction_join_status"].eq("ready"),
                "ready",
                "closed",
            ),
            **{name: output[name] for name in OBJECTIVES},
        }
    )
    pareto = prepare_pareto_input(
        gate,
        OBJECTIVES,
        status_column="prereaction_gate_status",
        eligible_statuses=("ready",),
    )
    output["prereaction_pareto_eligible"] = pareto["pareto_eligible"]
    output["prereaction_pareto_exclusion_reason"] = pareto[
        "pareto_exclusion_reason"
    ]
    output["prereaction_pareto_is_nondominated"] = pareto[
        "pareto_is_nondominated"
    ]
    output["prereaction_pareto_objective_spec"] = pareto[
        "pareto_objective_spec"
    ]
    output["prereaction_pareto_score"] = pd.NA
    output["updated_dft_priority_class"] = np.select(
        [
            output["planning_tier"].astype(str).eq(
                "tier1_small_control_matrix"
            ),
            output["prereaction_pareto_is_nondominated"],
            output["prereaction_pareto_eligible"],
        ],
        [
            "commercial_control",
            "prereaction_pareto",
            "prereaction_complete_nonpareto",
        ],
        default="incomplete_prereaction_evidence",
    )
    class_order = {
        "commercial_control": 0,
        "prereaction_pareto": 1,
        "prereaction_complete_nonpareto": 2,
        "incomplete_prereaction_evidence": 3,
    }
    ordered = output.assign(
        _class=output["updated_dft_priority_class"].map(class_order)
    ).sort_values(
        ["_class", "high_level_dft_rank", "formulation_id"], kind="stable"
    )
    rank_map = {
        formulation_id: rank
        for rank, formulation_id in enumerate(
            ordered["formulation_id"].astype(str), start=1
        )
    }
    output["updated_dft_priority_rank"] = output["formulation_id"].map(rank_map)
    output["dft_engine_status"] = "blocked_no_authorized_r2scan3c_engine"
    output["performance_claim_status"] = "no_performance_claim"
    return output.sort_values("updated_dft_priority_rank").reset_index(drop=True)


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def build_report(updated: pd.DataFrame) -> str:
    ready = int(updated["prereaction_join_status"].eq("ready").sum())
    pareto = int(updated["prereaction_pareto_is_nondominated"].sum())
    return "\n".join(
        [
            "# 预反应配对优先级报告",
            "",
            f"- 高层DFT候选：{len(updated)}",
            f"- 两类配对均闭合：{ready}",
            f"- 预反应代理Pareto：{pareto}",
            "",
            "本轮使用受约束GFN2-xTB复合物缔合能及多起点离散程度，只用于高层DFT输入优先级。",
            "负的缔合能代理不能解释为反应能垒、速率常数、转化率或TPU宏观性能。",
            "商业对照始终保留；未闭合配对不删除，只关闭其Pareto资格。",
            "正式r2SCAN-3c计算仍等待授权引擎，并须执行几何与频率门。",
            "",
        ]
    )


def write_release(
    formulations_path: Path,
    pair_results_path: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    if not formulations_path.is_file() or not pair_results_path.is_file():
        raise ValueError("预反应优先级输入不存在")
    updated = build_updated_priorities(
        pd.read_csv(formulations_path), pd.read_csv(pair_results_path)
    )
    frontier = updated.loc[
        updated["prereaction_pareto_eligible"]
        & updated["prereaction_pareto_is_nondominated"]
    ].copy()
    table_path = output_root / "高层DFT候选_预反应更新.csv"
    pareto_path = output_root / "预反应Pareto.csv"
    report_path = output_root / "预反应优先级报告.md"
    _atomic_text(table_path, updated.to_csv(index=False, float_format="%.12g"))
    _atomic_text(pareto_path, frontier.to_csv(index=False, float_format="%.12g"))
    _atomic_text(report_path, build_report(updated))
    manifest = {
        "release_id": release_id,
        "status": (
            "completed"
            if updated["prereaction_join_status"].eq("ready").all()
            else "incomplete"
        ),
        "counts": {
            "formulations": len(updated),
            "ready_formulations": int(
                updated["prereaction_join_status"].eq("ready").sum()
            ),
            "pareto_formulations": len(frontier),
        },
        "objectives": dict(OBJECTIVES),
        "inputs": {
            "formulations": {
                "path": str(formulations_path),
                "sha256": sha256(formulations_path),
            },
            "pair_results": {
                "path": str(pair_results_path),
                "sha256": sha256(pair_results_path),
            },
        },
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in (table_path, pareto_path, report_path)
        },
    }
    _atomic_text(
        output_root / "预反应优先级发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--高层候选", type=Path, required=True)
    parser.add_argument("--配对结果", type=Path, required=True)
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument(
        "--发布ID", default="tpu-reality-prereaction-priority-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.高层候选,
        args.配对结果,
        args.输出目录,
        release_id=args.发布ID,
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
