"""生成仅供阶段决策使用的配方系综特征、确定性聚类与 Pareto 前沿。

本模块只消费构件级计算描述符，不产生宏观力学性能预测。缺少任一构件、
构件计算未完成或目标值非有限的配方均保留在48行总表中，但不能参与标准化、
聚类或 Pareto 计算。所有发布行都带有 ``stage_only_not_final`` 标记。
"""

from __future__ import annotations

import argparse
import math
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import pandas as pd

from 配方系综特征 import aggregate_formulation_features, prepare_pareto_input


STAGE_SCOPE = "stage_only_not_final"

# 目标是构件级量化代理，不是强度、韧性或最终材料性能。
DEFAULT_OBJECTIVES: Mapping[str, str] = OrderedDict(
    (
        ("objective_nco_site_charge_e", "max"),
        ("objective_oh_site_charge_e_mean", "min"),
        ("objective_reactive_site_accessibility", "max"),
        ("objective_conformer_uncertainty", "min"),
        ("objective_effective_conformer_burden", "min"),
    )
)

_ROLE_PREFIXES = ("diisocyanate", "macrodiol_proxy", "chain_extender")


def _require_columns(frame: pd.DataFrame, columns: Sequence[str], label: str) -> None:
    missing = sorted(set(columns).difference(frame.columns))
    if missing:
        raise ValueError(f"{label}缺少字段: {missing}")


def _validate_component_roles(
    formulations: pd.DataFrame,
    components: pd.DataFrame,
    *,
    component_id_column: str,
) -> None:
    """若构件表提供角色字段，阻止把同一ID接到错误的配方角色。"""

    if "component_role" not in components.columns:
        return
    _require_columns(components, [component_id_column, "component_role"], "构件系综表")
    ids = components[component_id_column]
    if ids.isna().any() or ids.astype(str).str.strip().eq("").any():
        raise ValueError(f"构件系综表 {component_id_column} 存在空值")
    if not ids.is_unique:
        raise ValueError(f"构件系综表 {component_id_column} 不唯一")
    role_by_id = components.set_index(component_id_column)["component_role"]
    expected_columns = {
        "diisocyanate": "diisocyanate_id",
        "macrodiol_proxy": "macrodiol_proxy_id",
        "chain_extender": "chain_extender_id",
    }
    for expected_role, id_column in expected_columns.items():
        _require_columns(formulations, [id_column], "配方队列")
        observed = formulations[id_column].map(role_by_id).dropna().astype(str)
        wrong = observed.ne(expected_role)
        if wrong.any():
            bad_id = str(formulations.loc[observed.index[wrong][0], id_column])
            raise ValueError(
                f"构件角色不匹配: {bad_id} 应为 {expected_role}"
            )


def derive_stage_objectives(joined: pd.DataFrame) -> pd.DataFrame:
    """从三个构件的系综字段构造有明确物理含义的阶段目标。"""

    charge = "site_charge_e_mean_weighted_mean"
    sasa = "site_relative_sasa_mean_weighted_mean"
    uncertainty_fields = (
        "homo_lumo_gap_ev_weighted_sd",
        "site_charge_e_mean_weighted_sd",
        "site_relative_sasa_mean_weighted_sd",
    )
    required = [
        f"diisocyanate__{charge}",
        f"macrodiol_proxy__{charge}",
        f"chain_extender__{charge}",
        *(f"{role}__{sasa}" for role in _ROLE_PREFIXES),
        *(f"{role}__effective_conformer_count" for role in _ROLE_PREFIXES),
        *(
            f"{role}__{field}"
            for role in _ROLE_PREFIXES
            for field in uncertainty_fields
        ),
    ]
    _require_columns(joined, required, "配方系综特征")
    output = joined.copy()
    output["objective_nco_site_charge_e"] = pd.to_numeric(
        output[f"diisocyanate__{charge}"], errors="coerce"
    )
    output["objective_oh_site_charge_e_mean"] = output[
        [f"macrodiol_proxy__{charge}", f"chain_extender__{charge}"]
    ].apply(pd.to_numeric, errors="coerce").mean(axis=1, skipna=False)
    output["objective_reactive_site_accessibility"] = output[
        [f"{role}__{sasa}" for role in _ROLE_PREFIXES]
    ].apply(pd.to_numeric, errors="coerce").min(axis=1, skipna=False)
    uncertainty = output[
        [
            f"{role}__{field}"
            for role in _ROLE_PREFIXES
            for field in uncertainty_fields
        ]
    ].apply(pd.to_numeric, errors="coerce")
    output["objective_conformer_uncertainty"] = np.sqrt(
        uncertainty.pow(2).mean(axis=1, skipna=False)
    )
    output["objective_effective_conformer_burden"] = output[
        [f"{role}__effective_conformer_count" for role in _ROLE_PREFIXES]
    ].apply(pd.to_numeric, errors="coerce").sum(axis=1, skipna=False)
    return output


def deterministic_standardize(
    frame: pd.DataFrame,
    columns: Sequence[str],
    eligibility_mask: Sequence[bool] | pd.Series,
) -> tuple[pd.DataFrame, dict[str, dict[str, float]]]:
    """仅用合格行拟合总体均值/标准差；常数列确定性映射为0。"""

    _require_columns(frame, list(columns), "标准化输入")
    eligible = np.asarray(eligibility_mask, dtype=bool)
    if eligible.ndim != 1 or len(eligible) != len(frame):
        raise ValueError("eligibility_mask 与标准化输入行数不一致")
    if not eligible.any():
        raise ValueError("没有可用于阶段标准化的合格配方")
    numeric = frame[list(columns)].apply(pd.to_numeric, errors="coerce")
    fitted = numeric.loc[eligible]
    if not np.isfinite(fitted.to_numpy(float)).all():
        raise ValueError("合格配方的标准化字段存在缺失或非有限值")
    output = frame.copy()
    parameters: dict[str, dict[str, float]] = {}
    for column in columns:
        values = fitted[column].to_numpy(float)
        mean = float(math.fsum(values) / len(values))
        variance = float(math.fsum((value - mean) ** 2 for value in values) / len(values))
        scale = math.sqrt(max(variance, 0.0))
        if not math.isfinite(scale) or scale <= np.finfo(float).eps:
            scale = 1.0
            constant = True
        else:
            constant = False
        z_column = f"stage_z__{column}"
        output[z_column] = np.nan
        output.loc[eligible, z_column] = (fitted[column] - mean) / scale
        parameters[column] = {"mean": mean, "scale": scale, "constant": constant}
    return output, parameters


def deterministic_farthest_first_clusters(
    frame: pd.DataFrame,
    feature_columns: Sequence[str],
    eligibility_mask: Sequence[bool] | pd.Series,
    *,
    cluster_count: int = 8,
    id_column: str = "formulation_id",
) -> tuple[pd.Series, pd.Series]:
    """以最远点优先选中心并最近中心分配；所有并列均按ID破局。"""

    if cluster_count < 1:
        raise ValueError("cluster_count 必须至少为1")
    _require_columns(frame, [id_column, *feature_columns], "聚类输入")
    if frame[id_column].isna().any() or not frame[id_column].astype(str).is_unique:
        raise ValueError("聚类输入的 formulation_id 必须非空且唯一")
    eligible = np.asarray(eligibility_mask, dtype=bool)
    if eligible.ndim != 1 or len(eligible) != len(frame):
        raise ValueError("eligibility_mask 与聚类输入行数不一致")
    labels = pd.Series(pd.NA, index=frame.index, dtype="string")
    representatives = pd.Series(False, index=frame.index, dtype=bool)
    indexes = np.flatnonzero(eligible)
    if len(indexes) == 0:
        return labels, representatives
    values = frame.iloc[indexes][list(feature_columns)].to_numpy(float)
    if not np.isfinite(values).all():
        raise ValueError("合格聚类输入存在缺失或非有限值")
    identifiers = frame.iloc[indexes][id_column].astype(str).to_numpy()
    centroid = values.mean(axis=0)
    centroid_distance = np.sum((values - centroid) ** 2, axis=1)

    def _winner(scores: np.ndarray) -> int:
        best = float(np.max(scores))
        tied = np.flatnonzero(np.isclose(scores, best, rtol=0.0, atol=1e-15))
        return int(min(tied, key=lambda position: identifiers[position]))

    medoids = [_winner(centroid_distance)]
    target_count = min(cluster_count, len(indexes))
    while len(medoids) < target_count:
        distances = np.stack(
            [np.sum((values - values[medoid]) ** 2, axis=1) for medoid in medoids],
            axis=1,
        )
        nearest = distances.min(axis=1)
        nearest[medoids] = -1.0
        medoids.append(_winner(nearest))
    distance_to_medoids = np.stack(
        [np.sum((values - values[medoid]) ** 2, axis=1) for medoid in medoids],
        axis=1,
    )
    assignment = np.argmin(distance_to_medoids, axis=1)
    for local_position, original_position in enumerate(indexes):
        labels.iloc[original_position] = f"stage_cluster_{assignment[local_position] + 1:02d}"
    for medoid in medoids:
        representatives.iloc[indexes[medoid]] = True
    return labels, representatives


def build_stage_screening(
    formulations: pd.DataFrame,
    components: pd.DataFrame,
    *,
    objectives: Mapping[str, str] = DEFAULT_OBJECTIVES,
    cluster_count: int = 8,
    component_id_column: str = "candidate_id",
    status_column: str = "ensemble_status",
) -> tuple[pd.DataFrame, dict[str, dict[str, float]]]:
    """返回保留全部配方行的阶段筛选表及标准化参数。"""

    if not isinstance(formulations, pd.DataFrame) or formulations.empty:
        raise ValueError("配方队列不能为空")
    if not isinstance(components, pd.DataFrame) or components.empty:
        raise ValueError("构件系综表不能为空")
    _validate_component_roles(
        formulations, components, component_id_column=component_id_column
    )
    joined = aggregate_formulation_features(
        formulations,
        components,
        component_id_column=component_id_column,
        status_column=status_column,
        uncertainty_columns=(
            "homo_lumo_gap_ev_weighted_sd",
            "site_charge_e_mean_weighted_sd",
            "site_relative_sasa_mean_weighted_sd",
        ),
    )
    if len(joined) != len(formulations) or not joined["formulation_id"].is_unique:
        raise ValueError("阶段连接发生行膨胀或 formulation_id 不唯一")
    derived = derive_stage_objectives(joined)
    pareto = prepare_pareto_input(derived, objectives)
    eligible = pareto["pareto_eligible"].to_numpy(bool)
    standardized, parameters = deterministic_standardize(
        pareto, list(objectives), eligible
    )
    z_columns = [f"stage_z__{column}" for column in objectives]
    cluster_labels, representatives = deterministic_farthest_first_clusters(
        standardized,
        z_columns,
        eligible,
        cluster_count=cluster_count,
    )
    output = standardized.copy()
    output["stage_cluster_id"] = cluster_labels
    output["stage_cluster_representative"] = representatives
    output["stage_screen_status"] = np.where(
        output["pareto_eligible"], "ready", "closed"
    )
    output["stage_gate_reason"] = np.where(
        output["pareto_eligible"],
        "ready_for_stage_descriptor_screen",
        np.where(
            output["descriptor_join_status"].ne("ready"),
            output["descriptor_join_status"],
            output["pareto_exclusion_reason"],
        ),
    )
    output["screening_scope"] = STAGE_SCOPE
    output["performance_claim_status"] = "no_performance_claim"
    return output.reset_index(drop=True), parameters


def build_stage_report(
    screening: pd.DataFrame,
    objectives: Mapping[str, str],
    parameters: Mapping[str, Mapping[str, float]],
) -> str:
    """生成带科学边界和可复现参数的阶段报告。"""

    _require_columns(
        screening,
        [
            "screening_scope", "stage_screen_status", "pareto_eligible",
            "pareto_is_nondominated", "stage_cluster_id",
        ],
        "阶段筛选表",
    )
    if not screening["screening_scope"].eq(STAGE_SCOPE).all():
        raise ValueError("阶段筛选表缺少 stage_only_not_final 边界")
    ready = int(screening["stage_screen_status"].eq("ready").sum())
    closed = len(screening) - ready
    frontier = int(screening["pareto_is_nondominated"].sum())
    clusters = int(screening.loc[screening["pareto_eligible"], "stage_cluster_id"].nunique())
    lines = [
        "# 阶段配方筛选报告",
        "",
        f"- 发布边界：`{STAGE_SCOPE}`",
        f"- 配方总数：{len(screening)}",
        f"- 阶段可用：{ready}",
        f"- 闭门保留：{closed}",
        f"- 阶段Pareto第一前沿：{frontier}",
        f"- 确定性多样性簇：{clusters}",
        "",
        "## Pareto目标",
        "",
    ]
    lines.extend(f"- `{name}`：`{direction}`" for name, direction in objectives.items())
    lines.extend(["", "## 标准化参数", ""])
    for name in objectives:
        values = parameters[name]
        lines.append(
            f"- `{name}`：mean={values['mean']:.12g}，scale={values['scale']:.12g}，"
            f"constant={str(bool(values['constant'])).lower()}"
        )
    lines.extend(
        [
            "",
            "## 使用边界",
            "",
            "本结果仅用于当前已闭合构件的阶段性量化描述符比较，不是最终候选排名。",
            "缺失或未完成构件始终保留在总表中并闭门，不参与标准化、聚类或Pareto。",
            "目标均为构件级反应性、可及性、构象不确定度和计算负担代理，不能解释为TPU强度、韧性或可合成性结论。",
            "最后构件补齐后必须用完整批次重新生成最终版；阶段结果不得直接写入Gold真值层。",
            "",
        ]
    )
    return "\n".join(lines)


def _write_csv_atomic(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    compression = "gzip" if path.name.endswith(".gz") else None
    frame.to_csv(temporary, index=False, encoding="utf-8", compression=compression)
    temporary.replace(path)


def write_stage_outputs(
    formulations_path: Path,
    components_path: Path,
    feature_output: Path,
    pareto_output: Path,
    report_output: Path,
    *,
    cluster_count: int = 8,
) -> dict[str, int]:
    formulations = pd.read_csv(formulations_path)
    components = pd.read_csv(components_path)
    screening, parameters = build_stage_screening(
        formulations, components, cluster_count=cluster_count
    )
    frontier = screening.loc[
        screening["pareto_eligible"] & screening["pareto_is_nondominated"]
    ].copy()
    _write_csv_atomic(screening, feature_output)
    _write_csv_atomic(frontier, pareto_output)
    report_output.parent.mkdir(parents=True, exist_ok=True)
    temporary_report = report_output.with_name(f".{report_output.name}.tmp")
    temporary_report.write_text(
        build_stage_report(screening, DEFAULT_OBJECTIVES, parameters), encoding="utf-8"
    )
    temporary_report.replace(report_output)
    return {
        "total": len(screening),
        "ready": int(screening["stage_screen_status"].eq("ready").sum()),
        "closed": int(screening["stage_screen_status"].eq("closed").sum()),
        "pareto_frontier": len(frontier),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--配方队列", type=Path, required=True)
    parser.add_argument("--构件系综", type=Path, required=True)
    parser.add_argument("--特征输出", type=Path, required=True)
    parser.add_argument("--Pareto输出", type=Path, required=True)
    parser.add_argument("--报告输出", type=Path, required=True)
    parser.add_argument("--聚类数", type=int, default=8)
    args = parser.parse_args()
    counts = write_stage_outputs(
        args.配方队列,
        args.构件系综,
        args.特征输出,
        args.Pareto输出,
        args.报告输出,
        cluster_count=args.聚类数,
    )
    print(counts)


if __name__ == "__main__":
    main()
