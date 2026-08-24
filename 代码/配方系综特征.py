"""把构件级构象系综描述符严格连接为配方级 Pareto 输入。

本模块只包含纯函数，不读写项目文件。构件描述符可以使用宽表，或使用
``component_id, feature_name, feature_value`` 长表。连接必须通过三个受控构件
ID 完成；SMILES、名称或行号都不能作为替代连接键。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd


ROLE_ID_COLUMNS: dict[str, str] = {
    "diisocyanate": "diisocyanate_id",
    "macrodiol_proxy": "macrodiol_proxy_id",
    "chain_extender": "chain_extender_id",
}

DEFAULT_FORMULATION_COLUMNS: tuple[str, ...] = (
    "queue_rank",
    "formulation_id",
    "combination_id",
    "diisocyanate_id",
    "macrodiol_proxy_id",
    "chain_extender_id",
    "macrodiol_nominal_mn_g_mol",
    "hard_segment_mass_fraction_target",
    "nco_oh_ratio_target",
)

_LEAKAGE_COLUMNS = {
    "formulation_id",
    "combination_id",
    "split",
    "fold",
    "sample_weight",
    "label",
    "target",
    "y",
    "property_value",
    "measured_value",
    "predicted_value",
}
_LEAKAGE_PREFIXES = ("measured_", "experimental_", "observed_", "predicted_")


def _nonempty_unique_key(frame: pd.DataFrame, column: str, table_name: str) -> None:
    if column not in frame.columns:
        raise ValueError(f"{table_name}缺少字段: {column}")
    values = frame[column]
    empty = values.isna() | values.astype(str).str.strip().eq("")
    if empty.any():
        raise ValueError(f"{table_name} {column} 存在空值")
    if not values.is_unique:
        duplicates = values[values.duplicated(keep=False)].astype(str).unique()[:3]
        raise ValueError(f"{table_name} {column} 不唯一: {list(duplicates)}")


def _reject_leakage(columns: Sequence[str], component_id_column: str) -> None:
    leaked = sorted(
        str(column)
        for column in columns
        if str(column) != component_id_column
        and (
            (normalized := str(column).strip().lower()) in _LEAKAGE_COLUMNS
            or normalized.startswith(_LEAKAGE_PREFIXES)
            or normalized.endswith("_split")
            or normalized.endswith("_fold")
            or normalized.endswith("_label")
        )
    )
    if leaked:
        raise ValueError(f"构件描述符含潜在泄漏字段: {leaked}")


def normalize_component_descriptors(
    descriptors: pd.DataFrame,
    *,
    component_id_column: str = "component_id",
    feature_name_column: str = "feature_name",
    feature_value_column: str = "feature_value",
    status_column: str = "calculation_status",
) -> pd.DataFrame:
    """把构件描述符长/宽表规范成一行一个构件的宽表。

    长表中的同一 ``(component_id, feature_name)`` 只能出现一次。同一构件的
    状态也必须唯一，避免悄悄选择一条计算记录。宽表则要求构件 ID 直接唯一。
    """

    if not isinstance(descriptors, pd.DataFrame) or descriptors.empty:
        raise ValueError("构件描述符不能为空")
    if component_id_column not in descriptors.columns:
        raise ValueError(f"构件描述符缺少字段: {component_id_column}")
    is_long = {feature_name_column, feature_value_column}.issubset(descriptors.columns)
    if not is_long:
        _nonempty_unique_key(descriptors, component_id_column, "构件描述符")
        _reject_leakage(list(descriptors.columns), component_id_column)
        return descriptors.copy().reset_index(drop=True)

    ids = descriptors[component_id_column]
    if (ids.isna() | ids.astype(str).str.strip().eq("")).any():
        raise ValueError(f"构件描述符 {component_id_column} 存在空值")
    names = descriptors[feature_name_column]
    if (names.isna() | names.astype(str).str.strip().eq("")).any():
        raise ValueError("构件描述符 feature_name 存在空值")
    _reject_leakage(names.astype(str).tolist(), component_id_column)
    pairs = descriptors[[component_id_column, feature_name_column]]
    if pairs.duplicated().any():
        duplicate = pairs.loc[pairs.duplicated(keep=False)].iloc[0].to_dict()
        raise ValueError(f"构件长表 ID/特征重复: {duplicate}")

    metadata_columns = {
        component_id_column,
        feature_name_column,
        feature_value_column,
    }
    extra = [column for column in descriptors.columns if column not in metadata_columns]
    if extra != ([status_column] if status_column in extra else []):
        unsupported = [column for column in extra if column != status_column]
        if unsupported:
            raise ValueError(f"构件长表含未声明的元数据字段: {unsupported}")

    numeric_values = pd.to_numeric(descriptors[feature_value_column], errors="coerce")
    invalid_numeric = numeric_values.isna() & descriptors[feature_value_column].notna()
    if invalid_numeric.any():
        bad_feature = str(descriptors.loc[invalid_numeric, feature_name_column].iloc[0])
        raise ValueError(f"构件长表特征 {bad_feature} 不是数值")
    numeric_long = descriptors.copy()
    numeric_long[feature_value_column] = numeric_values
    wide = numeric_long.pivot(
        index=component_id_column,
        columns=feature_name_column,
        values=feature_value_column,
    ).reset_index()
    wide.columns.name = None
    if status_column in descriptors.columns:
        status_counts = descriptors.groupby(component_id_column, dropna=False)[
            status_column
        ].nunique(dropna=False)
        if (status_counts > 1).any():
            bad_id = str(status_counts[status_counts > 1].index[0])
            raise ValueError(f"构件 {bad_id} 存在多个 calculation_status")
        statuses = descriptors.groupby(component_id_column, sort=False)[
            status_column
        ].first()
        wide[status_column] = wide[component_id_column].map(statuses)
    _nonempty_unique_key(wide, component_id_column, "规范化构件描述符")
    return wide


def _status_kind(value: object) -> str:
    if pd.isna(value) or not str(value).strip():
        return "status_missing"
    normalized = str(value).strip().lower()
    if "block" in normalized:
        return "blocked"
    if normalized in {"ready", "success", "succeeded", "complete", "completed"}:
        return "ready"
    return "not_ready"


def aggregate_formulation_features(
    formulations: pd.DataFrame,
    component_descriptors: pd.DataFrame,
    *,
    component_id_column: str = "component_id",
    feature_columns: Sequence[str] | None = None,
    status_column: str = "calculation_status",
    uncertainty_columns: Sequence[str] = ("conformer_uncertainty",),
    formulation_columns: Sequence[str] = DEFAULT_FORMULATION_COLUMNS,
) -> pd.DataFrame:
    """按三个构件 ID 把系综特征连接至配方，并保留可审计门禁状态。

    缺失构件不会被填补或删除，而是保留配方并标记为闭门。构件状态为 blocked
    或非完成态时同样不能进入 Pareto。所有构件特征都带角色前缀。
    """

    required = {"formulation_id", *ROLE_ID_COLUMNS.values()}
    missing = required.difference(formulations.columns)
    if missing:
        raise ValueError(f"配方表缺少字段: {sorted(missing)}")
    _nonempty_unique_key(formulations, "formulation_id", "配方表")
    for id_column in ROLE_ID_COLUMNS.values():
        if (
            formulations[id_column].isna()
            | formulations[id_column].astype(str).str.strip().eq("")
        ).any():
            raise ValueError(f"配方表 {id_column} 存在空值")

    components = normalize_component_descriptors(
        component_descriptors,
        component_id_column=component_id_column,
        status_column=status_column,
    )
    _reject_leakage(list(components.columns), component_id_column)
    excluded = {component_id_column, status_column}
    if feature_columns is None:
        features = [
            column
            for column in components.columns
            if column not in excluded and pd.api.types.is_numeric_dtype(components[column])
        ]
    else:
        features = [str(column) for column in feature_columns]
        missing_features = set(features).difference(components.columns)
        if missing_features:
            raise ValueError(f"构件描述符缺少指定特征: {sorted(missing_features)}")
    if not features:
        raise ValueError("没有可连接的数值构件特征")
    _reject_leakage(features, component_id_column)
    nonnumeric = [
        column for column in features if not pd.api.types.is_numeric_dtype(components[column])
    ]
    if nonnumeric:
        raise ValueError(f"构件特征必须为数值: {nonnumeric}")

    keep = [column for column in formulation_columns if column in formulations.columns]
    if "formulation_id" not in keep:
        raise ValueError("formulation_columns 必须保留 formulation_id")
    output = formulations[keep].copy()
    indexed = components.set_index(component_id_column, drop=False)
    role_status_columns: list[str] = []
    role_missing_columns: list[str] = []
    for role, id_column in ROLE_ID_COLUMNS.items():
        ids = formulations[id_column]
        found = ids.isin(indexed.index)
        missing_column = f"{role}__descriptor_missing"
        output[missing_column] = ~found.to_numpy()
        role_missing_columns.append(missing_column)
        for feature in features:
            output[f"{role}__{feature}"] = ids.map(indexed[feature]).to_numpy()
        raw_status = (
            ids.map(indexed[status_column])
            if status_column in indexed.columns
            else pd.Series("ready", index=formulations.index, dtype=object)
        )
        role_status = raw_status.map(_status_kind)
        role_status.loc[~found] = "missing_component_descriptor"
        status_output = f"{role}__descriptor_status"
        output[status_output] = role_status.to_numpy()
        role_status_columns.append(status_output)

    missing_count = output[role_missing_columns].sum(axis=1).astype(int)
    output["component_descriptor_missing_count"] = missing_count
    role_status = output[role_status_columns]
    output["descriptor_join_status"] = np.select(
        [
            missing_count.gt(0),
            role_status.eq("blocked").any(axis=1),
            role_status.ne("ready").any(axis=1),
        ],
        ["missing_component_descriptor", "blocked", "not_ready"],
        default="ready",
    )

    uncertainty = [column for column in uncertainty_columns if column in features]
    if not uncertainty:
        output["conformer_uncertainty_status"] = "not_provided"
    else:
        uncertainty_output = [
            f"{role}__{column}" for role in ROLE_ID_COLUMNS for column in uncertainty
        ]
        all_present = output[uncertainty_output].notna().all(axis=1)
        output["conformer_uncertainty_status"] = np.where(
            output["descriptor_join_status"].eq("blocked"),
            "blocked",
            np.where(all_present, "complete", "missing"),
        )
    output["pareto_input_status"] = np.where(
        output["descriptor_join_status"].eq("ready"),
        "eligible_pending_objective_check",
        "ineligible_component_gate",
    )
    return output.reset_index(drop=True)


def pareto_nondominated_mask(
    frame: pd.DataFrame,
    objectives: Mapping[str, str],
    *,
    eligibility_mask: Sequence[bool] | pd.Series | None = None,
) -> pd.Series:
    """返回非支配掩码；目标方向必须逐列显式声明为 ``min`` 或 ``max``。"""

    if not objectives:
        raise ValueError("Pareto目标不能为空")
    missing = set(objectives).difference(frame.columns)
    if missing:
        raise ValueError(f"Pareto输入缺少目标: {sorted(missing)}")
    invalid = {name: direction for name, direction in objectives.items() if direction not in {"min", "max"}}
    if invalid:
        raise ValueError(f"Pareto目标方向必须是 min/max: {invalid}")
    values = frame[list(objectives)].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    finite = np.isfinite(values).all(axis=1)
    if eligibility_mask is None:
        eligible = finite
    else:
        supplied = np.asarray(eligibility_mask, dtype=bool)
        if supplied.ndim != 1 or len(supplied) != len(frame):
            raise ValueError("eligibility_mask 与输入行数不一致")
        eligible = finite & supplied
    signs = np.array([1.0 if direction == "max" else -1.0 for direction in objectives.values()])
    utility = values * signs
    result = np.zeros(len(frame), dtype=bool)
    eligible_indexes = np.flatnonzero(eligible)
    for index in eligible_indexes:
        others = utility[eligible_indexes]
        candidate = utility[index]
        dominated = np.any(
            np.all(others >= candidate, axis=1)
            & np.any(others > candidate, axis=1)
        )
        result[index] = not dominated
    return pd.Series(result, index=frame.index, dtype=bool)


def prepare_pareto_input(
    frame: pd.DataFrame,
    objectives: Mapping[str, str],
    *,
    status_column: str = "descriptor_join_status",
    eligible_statuses: Sequence[str] = ("ready",),
) -> pd.DataFrame:
    """标记Pareto资格与第一前沿；缺失目标或门禁失败的行一律闭门。"""

    if status_column not in frame.columns:
        raise ValueError(f"Pareto输入缺少状态字段: {status_column}")
    missing = set(objectives).difference(frame.columns)
    if missing:
        raise ValueError(f"Pareto输入缺少目标: {sorted(missing)}")
    objective_values = frame[list(objectives)].apply(pd.to_numeric, errors="coerce")
    objective_complete = np.isfinite(objective_values.to_numpy(float)).all(axis=1)
    gate_ready = frame[status_column].isin(list(eligible_statuses)).to_numpy()
    eligible = gate_ready & objective_complete
    output = frame.copy()
    output["pareto_eligible"] = eligible
    output["pareto_exclusion_reason"] = np.select(
        [~gate_ready, ~objective_complete],
        ["component_gate_failed", "objective_missing_or_nonfinite"],
        default="eligible",
    )
    output["pareto_is_nondominated"] = pareto_nondominated_mask(
        frame, objectives, eligibility_mask=eligible
    )
    output["pareto_objective_spec"] = ";".join(
        f"{name}:{direction}" for name, direction in objectives.items()
    )
    output["pareto_score"] = pd.NA
    return output
