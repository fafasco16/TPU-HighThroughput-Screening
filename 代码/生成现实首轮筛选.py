"""基于现实配方量化代理生成首轮 Pareto、多样性簇和 DFT/MD 复核队列。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from 生成阶段配方筛选 import (
    deterministic_farthest_first_clusters,
    deterministic_standardize,
)
from 配方系综特征 import prepare_pareto_input


ROOT = Path(__file__).resolve().parents[1]
SCREENING_SCOPE = "quantum_proxy_stage_not_final"
DEFAULT_OBJECTIVES: Mapping[str, str] = OrderedDict(
    (
        ("objective_nco_oh_charge_complementarity", "max"),
        ("objective_reactive_site_accessibility_floor", "max"),
        ("objective_homo_lumo_gap_floor", "max"),
        ("objective_discrete_conformer_uncertainty", "min"),
        ("objective_discrete_conformer_burden", "min"),
    )
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required(frame: pd.DataFrame, columns: Sequence[str], label: str) -> None:
    missing = sorted(set(columns).difference(frame.columns))
    if missing:
        raise ValueError(f"{label}缺少字段: {missing}")


def _numeric(frame: pd.DataFrame, columns: Sequence[str], label: str) -> pd.DataFrame:
    values = frame[list(columns)].apply(pd.to_numeric, errors="coerce")
    if values.isna().all(axis=None):
        raise ValueError(f"{label}全部无法解析为数值")
    return values


def derive_quantum_objectives(frame: pd.DataFrame) -> pd.DataFrame:
    """由三个构件的量化描述符构造不含宏观性能宣称的筛选目标。"""

    charge = "site_charge_e_mean_weighted_mean"
    sasa = "site_relative_sasa_mean_weighted_mean"
    gap = "homo_lumo_gap_ev_weighted_mean"
    required = [
        *(f"{role}__{charge}" for role in ("diisocyanate", "macrodiol", "chain_extender")),
        *(f"{role}__{sasa}" for role in ("diisocyanate", "macrodiol", "chain_extender")),
        *(f"{role}__{gap}" for role in ("diisocyanate", "macrodiol", "chain_extender")),
        "diisocyanate__conformer_uncertainty",
        "chain_extender__conformer_uncertainty",
        "diisocyanate__effective_conformer_count",
        "chain_extender__effective_conformer_count",
    ]
    _required(frame, required, "现实配方量化表")
    numeric = _numeric(frame, required, "现实配方量化目标")
    output = frame.copy()
    oh_charge = numeric[
        [f"macrodiol__{charge}", f"chain_extender__{charge}"]
    ].mean(axis=1, skipna=False)
    output["objective_nco_oh_charge_complementarity"] = (
        numeric[f"diisocyanate__{charge}"] - oh_charge
    )
    output["objective_reactive_site_accessibility_floor"] = numeric[
        [f"{role}__{sasa}" for role in ("diisocyanate", "macrodiol", "chain_extender")]
    ].min(axis=1, skipna=False)
    output["objective_homo_lumo_gap_floor"] = numeric[
        [f"{role}__{gap}" for role in ("diisocyanate", "macrodiol", "chain_extender")]
    ].min(axis=1, skipna=False)
    output["objective_discrete_conformer_uncertainty"] = np.sqrt(
        numeric[
            [
                "diisocyanate__conformer_uncertainty",
                "chain_extender__conformer_uncertainty",
            ]
        ].pow(2).mean(axis=1, skipna=False)
    )
    output["objective_discrete_conformer_burden"] = numeric[
        [
            "diisocyanate__effective_conformer_count",
            "chain_extender__effective_conformer_count",
        ]
    ].sum(axis=1, skipna=False)
    return output


def _select_review_queue(
    frame: pd.DataFrame, queue_size: int
) -> tuple[pd.DataFrame, dict[str, str]]:
    if queue_size < 1:
        raise ValueError("queue_size必须为正")
    eligible = frame.loc[frame["quantum_screen_eligible"]].copy()
    if eligible.empty:
        raise ValueError("没有可进入DFT/MD复核队列的量化配方")
    component_columns = {
        "diisocyanate": "diisocyanate_id",
        "macrodiol": "macrodiol_id",
        "chain_extender": "chain_extender_id",
    }
    _required(eligible, list(component_columns.values()), "DFT/MD复核候选")
    component_caps = {
        column: max(
            2,
            math.ceil(
                queue_size / eligible[column].astype(str).nunique() * 1.25
            ),
        )
        for column in component_columns.values()
    }
    controls = eligible.loc[
        eligible["planning_tier"].astype(str).eq("tier1_small_control_matrix")
    ].sort_values(["baseline_priority", "formulation_id"], kind="stable")
    if len(controls) > queue_size:
        raise ValueError("queue_size小于必须保留的tier1商业对照数量")
    selected: list[str] = []
    reasons: dict[str, str] = {}
    seen: set[str] = set()
    component_counts = {
        column: {value: 0 for value in eligible[column].astype(str).unique()}
        for column in component_columns.values()
    }
    indexed_eligible = eligible.set_index("formulation_id", drop=False)
    target_size = min(queue_size, len(eligible))

    def add(formulation_id: str, reason: str, *, enforce_caps: bool = True) -> bool:
        if formulation_id in seen:
            return False
        row = indexed_eligible.loc[formulation_id]
        if isinstance(row, pd.DataFrame):
            raise ValueError("复核队列formulation_id不唯一")
        if enforce_caps and any(
            component_counts[column][str(row[column])] >= component_caps[column]
            for column in component_columns.values()
        ):
            return False
        seen.add(formulation_id)
        selected.append(formulation_id)
        reasons[formulation_id] = reason
        for column in component_columns.values():
            component_counts[column][str(row[column])] += 1
        return True

    for formulation_id in controls["formulation_id"].astype(str):
        add(formulation_id, "commercial_small_control_matrix", enforce_caps=False)

    tier_order = {
        "tier1_small_control_matrix": 0,
        "tier2_control_grid": 1,
        "tier3_commercial_comparison": 2,
    }
    coverage_ranked = eligible.assign(
        _pareto_cluster=(
            eligible["pareto_is_nondominated"]
            & eligible["screening_cluster_representative"]
        ).astype(int),
        _pareto=eligible["pareto_is_nondominated"].astype(int),
        _cluster=eligible["screening_cluster_representative"].astype(int),
        _tier=eligible["planning_tier"].map(tier_order).fillna(9).astype(int),
    )
    for role, column in component_columns.items():
        for component_id in sorted(eligible[column].astype(str).unique()):
            if component_counts[column][component_id] > 0:
                continue
            candidates = coverage_ranked.loc[
                coverage_ranked[column].astype(str).eq(component_id)
            ].sort_values(
                ["_pareto_cluster", "_pareto", "_cluster", "_tier", "formulation_id"],
                ascending=[False, False, False, True, True],
                kind="stable",
            )
            for formulation_id in candidates["formulation_id"].astype(str):
                if add(formulation_id, f"component_coverage_{role}"):
                    break

    groups = [
        (
            eligible.loc[
                eligible["pareto_is_nondominated"]
                & eligible["screening_cluster_representative"]
            ].sort_values(["screening_cluster_id", "formulation_id"], kind="stable"),
            "pareto_cluster_representative",
        ),
        (
            eligible.loc[eligible["pareto_is_nondominated"]].sort_values(
                "formulation_id", kind="stable"
            ),
            "pareto_frontier",
        ),
        (
            eligible.loc[eligible["screening_cluster_representative"]].sort_values(
                ["screening_cluster_id", "formulation_id"], kind="stable"
            ),
            "diversity_cluster_representative",
        ),
        (
            eligible.sort_values("formulation_id", kind="stable"),
            "deterministic_diversity_fill",
        ),
    ]
    for group, reason in groups:
        for formulation_id in group["formulation_id"].astype(str):
            add(formulation_id, reason)
            if len(selected) == target_size:
                break
        if len(selected) == target_size:
            break
    if len(selected) < target_size:
        for formulation_id in eligible.sort_values("formulation_id")[
            "formulation_id"
        ].astype(str):
            add(
                formulation_id,
                "deterministic_fill_after_component_cap",
                enforce_caps=False,
            )
            if len(selected) == target_size:
                break
    indexed = frame.set_index("formulation_id", drop=False)
    queue = indexed.loc[selected].copy().reset_index(drop=True)
    queue.insert(0, "review_queue_rank", np.arange(1, len(queue) + 1))
    queue["review_selection_reason"] = queue["formulation_id"].map(reasons)
    queue["review_decision_status"] = "pending_DFT_MD_protocol_assignment"
    return queue, reasons


def build_first_round(
    formulations: pd.DataFrame,
    *,
    objectives: Mapping[str, str] = DEFAULT_OBJECTIVES,
    cluster_count: int = 32,
    queue_size: int = 40,
) -> tuple[pd.DataFrame, dict[str, dict[str, float]], pd.DataFrame]:
    if cluster_count < 1:
        raise ValueError("cluster_count必须为正")
    if queue_size < 1:
        raise ValueError("queue_size必须为正")
    _required(
        formulations,
        [
            "formulation_id",
            "planning_tier",
            "baseline_priority",
            "hard_segment_mass_fraction_target",
            "macrodiol_nominal_mn_g_mol",
            "formulation_domain_floor",
            "formulation_applicability_status",
            "screening_input_status",
            "performance_claim_status",
        ],
        "现实配方量化表",
    )
    if formulations.empty or not formulations["formulation_id"].is_unique:
        raise ValueError("现实配方量化表formulation_id必须非空唯一")
    if not formulations["performance_claim_status"].astype(str).eq(
        "no_performance_claim"
    ).all():
        raise ValueError("现实首轮筛选输入不得包含性能宣称")
    derived = derive_quantum_objectives(formulations)
    derived["quantum_gate_status"] = np.where(
        derived["screening_input_status"].astype(str).str.startswith(
            "ready_for_quantum_proxy_screen"
        ),
        "ready",
        "closed",
    )
    pareto = prepare_pareto_input(
        derived,
        objectives,
        status_column="quantum_gate_status",
        eligible_statuses=("ready",),
    )
    pareto["quantum_screen_eligible"] = pareto["pareto_eligible"]
    diversity_columns = [
        *objectives,
        "hard_segment_mass_fraction_target",
        "macrodiol_nominal_mn_g_mol",
        "formulation_domain_floor",
    ]
    standardized, parameters = deterministic_standardize(
        pareto,
        diversity_columns,
        pareto["quantum_screen_eligible"].to_numpy(bool),
    )
    z_columns = [f"stage_z__{column}" for column in diversity_columns]
    clusters, representatives = deterministic_farthest_first_clusters(
        standardized,
        z_columns,
        standardized["quantum_screen_eligible"].to_numpy(bool),
        cluster_count=cluster_count,
    )
    output = standardized.copy()
    output["screening_cluster_id"] = clusters
    output["screening_cluster_representative"] = representatives
    output["gnn_prediction_permission"] = np.where(
        output["formulation_applicability_status"].eq(
            "component_structures_within_or_near_domain"
        ),
        "diagnostic_only_requires_multicomponent_model",
        "blocked_outside_training_structure_domain",
    )
    output["screening_scope"] = SCREENING_SCOPE
    output["performance_claim_status"] = "no_performance_claim"
    queue, reasons = _select_review_queue(output, queue_size)
    output["selected_for_dft_md_review"] = output["formulation_id"].isin(
        queue["formulation_id"]
    )
    output["review_selection_reason"] = output["formulation_id"].map(reasons)
    return output.reset_index(drop=True), parameters, queue


def build_report(
    screening: pd.DataFrame,
    queue: pd.DataFrame,
    objectives: Mapping[str, str],
) -> str:
    eligible = int(screening["quantum_screen_eligible"].sum())
    frontier = int(screening["pareto_is_nondominated"].sum())
    out_of_domain = int(
        screening["gnn_prediction_permission"]
        .eq("blocked_outside_training_structure_domain")
        .sum()
    )
    frontier_base_systems = int(
        screening.loc[screening["pareto_is_nondominated"], "base_system_id"].nunique()
    )
    queue_base_systems = int(queue["base_system_id"].nunique())
    queue_role_coverage = {
        "二异氰酸酯": int(queue["diisocyanate_id"].nunique()),
        "PTMG": int(queue["macrodiol_id"].nunique()),
        "扩链剂": int(queue["chain_extender_id"].nunique()),
    }
    lines = [
        "# 现实配方首轮量化筛选报告",
        "",
        f"- 发布边界：`{SCREENING_SCOPE}`",
        f"- 现实配方总数：{len(screening)}",
        f"- 可进入量化代理筛选：{eligible}",
        f"- Pareto 第一前沿：{frontier}",
        f"- Pareto基础三构件体系：{frontier_base_systems}",
        f"- DFT/MD 复核队列：{len(queue)}",
        f"- 复核队列基础体系：{queue_base_systems}",
        f"- 复核队列构件覆盖：{queue_role_coverage['二异氰酸酯']}个二异氰酸酯 / {queue_role_coverage['PTMG']}个PTMG / {queue_role_coverage['扩链剂']}个扩链剂",
        f"- GNN结构域外：{out_of_domain}",
        "",
        "## 目标与方向",
        "",
    ]
    lines.extend(f"- `{name}`：`{direction}`" for name, direction in objectives.items())
    lines.extend(
        [
            "",
            "## 科学边界",
            "",
            "本轮只比较反应位点电荷互补、位点可达性、HOMO-LUMO能隙下限、离散构件构象不确定度和构象负担。",
            "二异氰酸酯与扩链剂使用CREST系综；PTMG使用一个按名义Mn选取的确定性低聚链，不能代表商品Mn/Mw/PDI分布。",
            "GNN结构域外只关闭GNN性能外推，不关闭独立的xTB/DFT/MD量化代理筛选；这些体系进入高验证优先级。",
            "Pareto与多样性队列不能解释为拉伸强度、韧性或最终性能排名，也不能替代采购、SDS、工艺和真实合成门。",
            "",
            "## 下一步",
            "",
            "对复核队列分配小规模DFT反应位点/二聚体任务和代表性分段TPU MD任务；结合成本、EHS和文献新颖性后，再收敛到8–12个实验样品。",
            "",
        ]
    )
    return "\n".join(lines)


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8", float_format="%.12g")
    temporary.replace(path)


def _atomic_text(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def write_release(
    source_path: Path,
    output_root: Path,
    *,
    release_id: str,
    cluster_count: int = 32,
    queue_size: int = 40,
) -> dict[str, Any]:
    if not source_path.is_file():
        raise ValueError(f"现实配方量化输入不存在: {source_path}")
    screening, parameters, queue = build_first_round(
        pd.read_csv(source_path),
        cluster_count=cluster_count,
        queue_size=queue_size,
    )
    frontier = screening.loc[
        screening["quantum_screen_eligible"]
        & screening["pareto_is_nondominated"]
    ].copy()
    paths = {
        "首轮候选.csv": output_root / "首轮候选.csv",
        "Pareto前沿.csv": output_root / "Pareto前沿.csv",
        "DFT_MD复核队列.csv": output_root / "DFT_MD复核队列.csv",
    }
    _atomic_csv(screening, paths["首轮候选.csv"])
    _atomic_csv(frontier, paths["Pareto前沿.csv"])
    _atomic_csv(queue, paths["DFT_MD复核队列.csv"])
    report_path = output_root / "筛选报告.md"
    _atomic_text(build_report(screening, queue, DEFAULT_OBJECTIVES), report_path)
    manifest = {
        "release_id": release_id,
        "status": "completed",
        "scope": SCREENING_SCOPE,
        "counts": {
            "total_formulations": len(screening),
            "quantum_screen_eligible": int(screening["quantum_screen_eligible"].sum()),
            "pareto_frontier": len(frontier),
            "pareto_base_systems": int(frontier["base_system_id"].nunique()),
            "diversity_clusters": int(
                screening.loc[
                    screening["quantum_screen_eligible"], "screening_cluster_id"
                ].nunique()
            ),
            "review_queue": len(queue),
            "review_queue_base_systems": int(queue["base_system_id"].nunique()),
            "review_queue_diisocyanates": int(queue["diisocyanate_id"].nunique()),
            "review_queue_macrodiols": int(queue["macrodiol_id"].nunique()),
            "review_queue_chain_extenders": int(
                queue["chain_extender_id"].nunique()
            ),
        },
        "objectives": dict(DEFAULT_OBJECTIVES),
        "standardization_parameters": parameters,
        "source": {
            "path": str(source_path),
            "bytes": source_path.stat().st_size,
            "sha256": sha256(source_path),
        },
        "files": {
            name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for name, path in paths.items()
        }
        | {
            report_path.name: {
                "bytes": report_path.stat().st_size,
                "sha256": sha256(report_path),
            }
        },
        "interpretation_limit": (
            "quantum proxy stage only; not a tensile-strength, toughness, synthesis, "
            "or final performance ranking"
        ),
    }
    _atomic_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        output_root / "筛选发布清单.json",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--配方量化表",
        type=Path,
        default=ROOT / "数据" / "现实库" / "配方量化描述符.csv",
    )
    parser.add_argument(
        "--输出目录", type=Path, default=ROOT / "结果" / "现实筛选"
    )
    parser.add_argument(
        "--发布ID", default="tpu-reality-first-round-quantum-screen-20260825-v1"
    )
    parser.add_argument("--聚类数", type=int, default=32)
    parser.add_argument("--复核队列数", type=int, default=40)
    args = parser.parse_args(argv)
    manifest = write_release(
        args.配方量化表,
        args.输出目录,
        release_id=args.发布ID,
        cluster_count=args.聚类数,
        queue_size=args.复核队列数,
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
