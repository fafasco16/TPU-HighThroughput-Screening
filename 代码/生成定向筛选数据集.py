"""生成面向韧性、循环恢复、热稳定、成本与环保的定向筛选视图。"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

import duckdb
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "结果" / "定向筛选"
RELEASE_ID = "tpu-directed-five-objective-2026-08-30-v1"
SCHEMA_VERSION = "1.0.0"

INPUT_PATHS = {
    "可用实验观测": ROOT / "结果" / "可用数据集" / "实验观测.csv.gz",
    "可用计算观测": ROOT / "结果" / "可用数据集" / "计算观测.csv.gz",
    "商用构件证据": ROOT / "候选" / "商用构件证据.csv",
    "实验合理组合": ROOT / "候选" / "实验合理组合.csv",
}

OUTPUT_FILENAMES = {
    "使用说明": "README.md",
    "三目标实验标签": "三目标实验标签.csv.gz",
    "目标标签审计": "目标标签审计.csv",
    "三目标计算证据": "三目标计算证据.csv.gz",
    "计算证据审计": "计算证据审计.csv",
    "现实构件约束": "现实构件约束.csv",
    "现实配方候选": "现实配方候选.csv",
    "筛选任务清单": "筛选任务清单.csv",
}

PROPERTY_RULES: dict[str, tuple[str, str]] = {
    # 韧性：直接积分值优先；强度、伸长与不完整曲线面积只作辅助。
    "toughness": ("toughness", "primary_direct_scalar"),
    "tensile_toughness": ("toughness", "primary_direct_scalar"),
    "observed_stress_strain_area_to_last_point": (
        "toughness",
        "auxiliary_curve_area",
    ),
    "tensile_strength": ("toughness", "auxiliary_mechanical_scalar"),
    "ultimate_tensile_strength": (
        "toughness",
        "auxiliary_mechanical_scalar",
    ),
    "elongation_at_break": ("toughness", "auxiliary_mechanical_scalar"),
    # 循环恢复：必须保留最大应变、循环数和温度条件。
    "elastic_recovery": ("cyclic_recovery", "primary_conditioned_scalar"),
    "residual_strain": ("cyclic_recovery", "primary_conditioned_scalar"),
    "energy_dissipation_index": (
        "cyclic_recovery",
        "primary_conditioned_scalar",
    ),
    "cyclic_tensile_stress": (
        "cyclic_recovery",
        "primary_cyclic_curve",
    ),
    "hysteresis_loss": ("cyclic_recovery", "auxiliary_application_scalar"),
    "hysteresis_loss_ratio": (
        "cyclic_recovery",
        "auxiliary_application_scalar",
    ),
    # 热稳定：TGA/DTG曲线需先提取统一端点；Tg/DSC只作使用温度辅助。
    "tga_mass_signal": (
        "thermal_stability",
        "primary_curve_for_endpoint",
    ),
    "dtg_mass_rate": ("thermal_stability", "primary_curve_for_endpoint"),
    "dta_signal": ("thermal_stability", "auxiliary_thermal_curve"),
    "dsc_heat_flow": ("thermal_stability", "auxiliary_thermal_curve"),
    "glass_transition_temperature_hard_segment": (
        "thermal_stability",
        "auxiliary_service_temperature",
    ),
    "glass_transition_temperature_soft_segment": (
        "thermal_stability",
        "auxiliary_service_temperature",
    ),
}

COMPUTATIONAL_RULES: dict[str, list[tuple[str, str]]] = {
    "compression_energy_density_to_max_observed_log_strain": [
        ("toughness", "process_response_proxy")
    ],
    "maximum_observed_mises_stress": [
        ("toughness", "process_response_proxy")
    ],
    "mises_stress_at_compressive_log_strain_0_1": [
        ("toughness", "process_response_proxy")
    ],
    "mises_stress_at_compressive_log_strain_0_5": [
        ("toughness", "process_response_proxy")
    ],
    "mises_stress_at_compressive_log_strain_1_0": [
        ("toughness", "process_response_proxy")
    ],
    "tensile_strength": [("toughness", "low_fidelity_target")],
    "bulk_modulus": [("toughness", "mechanistic_proxy")],
    "isentropic_bulk_modulus": [("toughness", "mechanistic_proxy")],
    "cohesive_energy_per_chain": [
        ("toughness", "mechanistic_proxy"),
        ("cyclic_recovery", "mechanistic_proxy"),
        ("thermal_stability", "mechanistic_proxy"),
    ],
    "density": [
        ("toughness", "mechanistic_proxy"),
        ("cyclic_recovery", "mechanistic_proxy"),
    ],
    "Density": [
        ("toughness", "mechanistic_proxy"),
        ("cyclic_recovery", "mechanistic_proxy"),
    ],
    "mass_density": [
        ("toughness", "mechanistic_proxy"),
        ("cyclic_recovery", "mechanistic_proxy"),
    ],
    "residual_reactive_sites": [
        ("cyclic_recovery", "mechanistic_proxy")
    ],
    "Tg": [("thermal_stability", "low_fidelity_target")],
}

PRIMARY_ROLES = {
    "primary_direct_scalar",
    "primary_conditioned_scalar",
    "primary_curve_for_endpoint",
    "primary_cyclic_curve",
}


def classify_property(name: str) -> tuple[str, str] | None:
    """返回性质所属目标及监督角色；无关性质返回None。"""

    return PROPERTY_RULES.get(name)


def classify_computational_property(name: str) -> list[tuple[str, str]]:
    """返回计算性质可支持的目标和证据角色。"""

    return COMPUTATIONAL_RULES.get(name, [])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_entry(path: Path) -> dict[str, object]:
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        frame.to_csv(
            path,
            index=False,
            encoding="utf-8",
            lineterminator="\n",
            compression={"method": "gzip", "compresslevel": 9, "mtime": 0},
        )
    else:
        frame.to_csv(path, index=False, encoding="utf-8-sig", lineterminator="\n")


def _pair_key(source: pd.Series, formulation: pd.Series) -> pd.Series:
    return source.fillna("").astype(str) + "|" + formulation.fillna("").astype(str)


def _standardization_requirement(target_family: str, role: str) -> str:
    if target_family == "toughness" and role == "primary_direct_scalar":
        return "统一MJ/m3、拉伸协议、试样和应变率"
    if target_family == "cyclic_recovery" and role == "primary_conditioned_scalar":
        return "固定最大应变、循环数、温度和恢复定义"
    if role == "primary_cyclic_curve":
        return "按整条曲线分组并提取逐循环残余应变、恢复率和滞后能"
    if role == "primary_curve_for_endpoint":
        return "曲线质控后统一提取T5/T10/Td_onset"
    if target_family == "thermal_stability":
        return "仅作Tg/DSC或曲线辅助，不代替热分解端点"
    return "辅助性质不得替代目标真值"


def _chemistry_mapping_status(
    experiments: pd.DataFrame,
    closed_pairs: set[str],
) -> pd.Series:
    keys = _pair_key(experiments["source_id"], experiments["formulation_id"])
    closed = keys.isin(closed_pairs)
    has_formulation = experiments["formulation_id"].fillna("").astype(str).str.strip().ne("")
    result = pd.Series("unmapped", index=experiments.index, dtype="object")
    result.loc[has_formulation] = "formulation_id_only"
    result.loc[closed] = "component_table_closed"
    return result


def _screening_use(row: pd.Series) -> str:
    role = row["target_metric_role"]
    mapping = row["chemistry_mapping_status"]
    ready = bool(row["model_ready"])
    if role == "primary_curve_for_endpoint":
        return "endpoint_extraction_required"
    if role == "primary_cyclic_curve":
        return "cyclic_endpoint_extraction_required"
    if role in {"primary_direct_scalar", "primary_conditioned_scalar"}:
        if ready and mapping == "component_table_closed":
            return "eligible_after_feature_join"
        if ready and mapping == "formulation_id_only":
            return "family_calibration_only"
        return "reference_until_mapping_closed"
    return "auxiliary_only"


def _build_labels(
    experiments: pd.DataFrame,
) -> pd.DataFrame:
    component_rows = experiments[
        experiments["record_kind"].eq("formulation_component")
    ]
    closed_pairs = set(
        _pair_key(component_rows["source_id"], component_rows["formulation_id"])
    )

    labels = experiments[experiments["property_name"].isin(PROPERTY_RULES)].copy()
    mapped = labels["property_name"].map(PROPERTY_RULES)
    labels["target_family"] = mapped.map(lambda value: value[0])
    labels["target_metric_role"] = mapped.map(lambda value: value[1])
    labels["chemistry_mapping_status"] = _chemistry_mapping_status(
        labels, closed_pairs
    )
    labels["standardization_requirement"] = [
        _standardization_requirement(family, role)
        for family, role in zip(
            labels["target_family"],
            labels["target_metric_role"],
            strict=True,
        )
    ]
    labels["new_chemistry_screening_use"] = labels.apply(_screening_use, axis=1)
    labels["formulation_group_key"] = _pair_key(
        labels["source_id"], labels["formulation_id"]
    )
    labels.loc[
        labels["formulation_id"].fillna("").astype(str).str.strip().eq(""),
        "formulation_group_key",
    ] = pd.NA

    priority = [
        "release_id",
        "target_family",
        "target_metric_role",
        "property_name",
        "value",
        "unit",
        "source_id",
        "source_family_id",
        "formulation_id",
        "sample_id",
        "curve_id",
        "point_index",
        "independent_unit",
        "chemistry_mapping_status",
        "new_chemistry_screening_use",
        "standardization_requirement",
        "condition_name",
        "condition_value",
        "condition_unit",
        "secondary_condition_name",
        "secondary_condition_value",
        "secondary_condition_unit",
        "method_or_test_protocol",
        "usage_mode",
        "model_ready",
        "recommended_loss_weight",
        "development_split",
        "source_holdout_fold",
        "source_locator",
        "citation_keys",
        "formulation_group_key",
    ]
    remaining = [column for column in labels.columns if column not in priority]
    return labels[priority + remaining].reset_index(drop=True)


def _nunique_nonblank(series: pd.Series) -> int:
    text = series.dropna().astype(str).str.strip()
    return int(text[text.ne("")].nunique())


def _build_audit(labels: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for (family, property_name, role), group in labels.groupby(
        ["target_family", "property_name", "target_metric_role"], sort=True
    ):
        closed = group[group["chemistry_mapping_status"].eq("component_table_closed")]
        primary = role in PRIMARY_ROLES
        closed_count = _nunique_nonblank(closed["formulation_group_key"])
        if role == "primary_curve_for_endpoint":
            status = "endpoint_extraction_required"
            action = "先从TGA/DTG曲线提取T5、T10和Td_onset并核验样品身份"
        elif role == "primary_cyclic_curve":
            status = "cyclic_endpoint_extraction_required"
            action = "按整条加载—卸载曲线提取逐循环恢复率、残余应变和滞后能"
        elif primary and closed_count < 50:
            status = "insufficient_for_new_chemistry_model"
            action = "定向补充结构—配方—工艺闭合的独立文献体系"
        else:
            status = "auxiliary_or_protocol_specific"
            action = "只作辅助任务或来源内校准，不替代目标真值"
        rows.append(
            {
                "target_family": family,
                "property_name": property_name,
                "target_metric_role": role,
                "row_count": len(group),
                "source_count": group["source_id"].nunique(),
                "source_family_count": group["source_family_id"].nunique(),
                "formulation_group_count": _nunique_nonblank(
                    group["formulation_group_key"]
                ),
                "chemistry_closed_formulation_group_count": closed_count,
                "sample_count": _nunique_nonblank(group["sample_id"]),
                "curve_count": _nunique_nonblank(group["curve_id"]),
                "independent_unit_count": _nunique_nonblank(
                    group["independent_unit"]
                ),
                "model_ready_row_count": int(group["model_ready"].fillna(False).sum()),
                "current_status": status,
                "next_action": action,
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["target_family", "target_metric_role", "property_name"]
    ).reset_index(drop=True)


def _computational_mapping_scope(status: pd.Series) -> pd.Series:
    text = status.fillna("").astype(str).str.lower()
    result = pd.Series("family_or_unresolved", index=status.index, dtype="object")
    result.loc[text.str.contains("exact_polymer_smiles|rdkit_validated")] = (
        "exact_structure"
    )
    result.loc[text.str.contains("formulation_label")] = "formulation_level"
    result.loc[text.str.contains("single_nominal_formulation")] = (
        "single_formulation_process_only"
    )
    result.loc[text.str.contains("coarse_grained_component_family")] = (
        "coarse_grained_family"
    )
    return result


def _computational_system_relevance(frame: pd.DataFrame) -> pd.Series:
    role = frame["target_role"].fillna("").astype(str)
    source = frame["source_id"].fillna("").astype(str)
    result = pd.Series("polymer_transfer", index=frame.index, dtype="object")
    result.loc[role.str.contains("tpu_core", case=False)] = "tpu_core"
    result.loc[source.eq("source_figshare_ma5c03283_si")] = (
        "pu_family_multiscale"
    )
    result.loc[source.eq("ledger_source_106")] = "tpu_formulation_mechanistic"
    result.loc[source.eq("source_mendeley_n9h66xjk7y_v1")] = (
        "single_pu_formulation_process_space"
    )
    return result


def _build_computational_evidence(path: Path) -> pd.DataFrame:
    properties = sorted(COMPUTATIONAL_RULES)
    query = """
        SELECT *
        FROM read_csv_auto(?, all_varchar=false)
        WHERE model_ready
          AND property_name IN (SELECT unnest(?))
    """
    computed = duckdb.connect().execute(query, [str(path), properties]).df()
    if computed.empty:
        raise ValueError("没有找到三目标可用计算证据")

    expanded: list[pd.DataFrame] = []
    for property_name, rules in COMPUTATIONAL_RULES.items():
        source = computed[computed["property_name"].eq(property_name)]
        for target_family, metric_role in rules:
            part = source.copy()
            part["target_family"] = target_family
            part["computational_metric_role"] = metric_role
            expanded.append(part)
    evidence = pd.concat(expanded, ignore_index=True)
    evidence["mapping_scope"] = _computational_mapping_scope(
        evidence["structure_identity_status"]
    )
    evidence["system_relevance"] = _computational_system_relevance(evidence)
    evidence["evidence_role"] = evidence["computational_metric_role"]
    low_fidelity = evidence["computational_metric_role"].eq("low_fidelity_target")
    direct_domain = evidence["system_relevance"].isin(
        ["tpu_core", "pu_family_multiscale", "tpu_formulation_mechanistic"]
    )
    evidence.loc[low_fidelity & direct_domain, "evidence_role"] = (
        "direct_low_fidelity_target"
    )
    evidence.loc[low_fidelity & ~direct_domain, "evidence_role"] = (
        "transfer_low_fidelity_target"
    )
    evidence["allowed_use"] = evidence["evidence_role"].map(
        {
            "direct_low_fidelity_target": "multifidelity_auxiliary_target",
            "transfer_low_fidelity_target": "representation_pretraining_only",
            "process_response_proxy": "within_system_process_proxy_only",
            "mechanistic_proxy": "feature_or_residual_model_only",
        }
    )
    evidence["calibration_requirement"] = evidence["evidence_role"].map(
        {
            "direct_low_fidelity_target": (
                "与同定义实验端点配对后做残差或多保真校准"
            ),
            "transfer_low_fidelity_target": (
                "仅预训练或迁移；不得直接给TPU候选定量排名"
            ),
            "process_response_proxy": (
                "只在同一名义配方和工况域内训练过程代理"
            ),
            "mechanistic_proxy": (
                "作为结构/配方特征；不得替代宏观实验真值"
            ),
        }
    )
    priority = [
        "release_id",
        "target_family",
        "evidence_role",
        "computational_metric_role",
        "property_name",
        "value",
        "unit",
        "method_family",
        "method_detail",
        "source_id",
        "source_family_id",
        "observation_id",
        "canonical_structure",
        "system_identity",
        "mapping_scope",
        "system_relevance",
        "allowed_use",
        "calibration_requirement",
        "independent_unit",
        "leakage_group",
        "usage_mode",
        "recommended_loss_weight",
        "development_split",
        "source_holdout_fold",
        "source_locator",
        "citation_keys",
    ]
    remaining = [column for column in evidence.columns if column not in priority]
    return evidence[priority + remaining].sort_values(
        [
            "target_family",
            "property_name",
            "source_id",
            "observation_id",
            "evidence_role",
        ],
        kind="mergesort",
    ).reset_index(drop=True)


def _build_computational_audit(evidence: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    keys = [
        "target_family",
        "property_name",
        "evidence_role",
        "method_family",
        "system_relevance",
        "allowed_use",
    ]
    for values, group in evidence.groupby(keys, dropna=False, sort=True):
        row = dict(zip(keys, values, strict=True))
        row.update(
            {
                "row_count": len(group),
                "source_count": group["source_id"].nunique(),
                "independent_unit_count": _nunique_nonblank(
                    group["independent_unit"]
                ),
                "hard_group_count": _nunique_nonblank(group["leakage_group"]),
                "structure_count": _nunique_nonblank(
                    group["canonical_structure"]
                ),
                "system_count": _nunique_nonblank(group["system_identity"]),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def _build_components(source: pd.DataFrame) -> pd.DataFrame:
    components = source.copy()
    components["price_currency"] = pd.NA
    components["price_per_kg"] = pd.NA
    components["price_region"] = pd.NA
    components["price_date"] = pd.NA
    components["cost_data_status"] = "missing_quote"
    components["renewable_carbon_fraction"] = pd.NA
    components["ghs_hazard_score"] = pd.NA
    components["solvent_burden_score"] = pd.NA
    components["recyclability_evidence"] = pd.NA
    components["environment_data_status"] = (
        "missing_structured_ehs_and_renewable_fraction"
    )
    components["tpu_route_ready"] = True
    components["direct_tpuu_role_status"] = components["role"].map(
        {
            "chain_extender": "not_amine_chain_extender",
            "diisocyanate": "requires_amine_route_pairing",
            "macrodiol": "requires_amine_route_pairing",
        }
    )
    return components


def _build_formulations(source: pd.DataFrame) -> pd.DataFrame:
    formulations = source.copy()
    formulations["polymer_family"] = "TPU"
    formulations["tpuu_route_ready"] = False
    formulations["estimated_raw_material_cost_per_kg"] = pd.NA
    formulations["renewable_carbon_fraction_estimated"] = pd.NA
    formulations["cost_gate_status"] = "blocked_missing_component_prices"
    formulations["environment_gate_status"] = (
        "blocked_missing_structured_component_scores"
    )
    formulations["three_target_prediction_status"] = (
        "blocked_insufficient_calibrated_models"
    )
    formulations["descriptor_prefilter_status"] = (
        "eligible_for_rule_and_existing_descriptor_prefilter"
    )
    formulations["expensive_calculation_status"] = (
        "deferred_until_multitarget_prefilter"
    )
    return formulations


def _build_tasks(
    labels: pd.DataFrame,
    computational: pd.DataFrame,
) -> pd.DataFrame:
    definitions = [
        {
            "objective_id": "toughness",
            "objective_name": "韧性",
            "objective_type": "single_task_model",
            "preferred_target": "stress_strain_area_to_break_MJ_m3",
            "optimization_direction": "maximize",
            "minimum_independent_formulations": 80,
            "preferred_independent_formulations": 150,
            "model_family": "CatBoost/ExtraTrees/GaussianProcess加不确定度集成",
            "gate": "统一拉伸协议并闭合组分、配方与工艺",
        },
        {
            "objective_id": "cyclic_recovery",
            "objective_name": "循环恢复",
            "objective_type": "single_task_model",
            "preferred_target": "residual_strain_or_elastic_recovery_at_fixed_protocol",
            "optimization_direction": "minimize_residual_or_maximize_recovery",
            "minimum_independent_formulations": 60,
            "preferred_independent_formulations": 100,
            "model_family": "条件回归/高斯过程/小样本集成",
            "gate": "固定应变、循环数、温度和恢复定义",
        },
        {
            "objective_id": "thermal_stability",
            "objective_name": "热稳定",
            "objective_type": "single_task_model",
            "preferred_target": "T5_or_T10_or_Td_onset_degC",
            "optimization_direction": "maximize",
            "minimum_independent_formulations": 80,
            "preferred_independent_formulations": 150,
            "model_family": "端点回归；TGA曲线先做统一端点提取",
            "gate": "气氛、升温速率和端点定义一致；Tg单列",
        },
        {
            "objective_id": "cost",
            "objective_name": "原料成本",
            "objective_type": "deterministic_constraint",
            "preferred_target": "bom_cost_per_kg_polymer",
            "optimization_direction": "minimize",
            "minimum_independent_formulations": 0,
            "preferred_independent_formulations": 0,
            "model_family": "组分质量分数乘以同地区同日期报价",
            "gate": "报价、币种、地区、日期和包装规格闭合",
        },
        {
            "objective_id": "environment",
            "objective_name": "环保约束",
            "objective_type": "deterministic_constraint",
            "preferred_target": "renewable_fraction_plus_hazard_process_recyclability_vector",
            "optimization_direction": "pareto_constraints",
            "minimum_independent_formulations": 0,
            "preferred_independent_formulations": 0,
            "model_family": "透明多指标规则；不提前压成黑箱绿色分数",
            "gate": "可再生碳、GHS/SDS、溶剂催化剂、能耗和回收证据闭合",
        },
    ]
    tasks = pd.DataFrame(definitions)
    summaries = []
    for family in ["toughness", "cyclic_recovery", "thermal_stability"]:
        group = labels[labels["target_family"].eq(family)]
        primary = group[group["target_metric_role"].isin(PRIMARY_ROLES)]
        closed = primary[
            primary["chemistry_mapping_status"].eq("component_table_closed")
        ]
        summaries.append(
            {
                "objective_id": family,
                "current_rows": len(group),
                "current_sources": group["source_id"].nunique(),
                "current_formulation_groups": _nunique_nonblank(
                    group["formulation_group_key"]
                ),
                "current_primary_formulation_groups": _nunique_nonblank(
                    primary["formulation_group_key"]
                ),
                "current_chemistry_closed_groups": _nunique_nonblank(
                    closed["formulation_group_key"]
                ),
            }
        )
    summary = pd.DataFrame(summaries)
    tasks = tasks.merge(summary, on="objective_id", how="left")
    computational_summaries = []
    for family in ["toughness", "cyclic_recovery", "thermal_stability"]:
        group = computational[computational["target_family"].eq(family)]
        direct = group[
            group["evidence_role"].eq("direct_low_fidelity_target")
        ]
        mechanistic = group[group["evidence_role"].eq("mechanistic_proxy")]
        process = group[group["evidence_role"].eq("process_response_proxy")]
        computational_summaries.append(
            {
                "objective_id": family,
                "computational_evidence_rows": len(group),
                "computational_hard_groups": _nunique_nonblank(
                    group["leakage_group"]
                ),
                "direct_low_fidelity_hard_groups": _nunique_nonblank(
                    direct["leakage_group"]
                ),
                "mechanistic_proxy_hard_groups": _nunique_nonblank(
                    mechanistic["leakage_group"]
                ),
                "process_proxy_hard_groups": _nunique_nonblank(
                    process["leakage_group"]
                ),
            }
        )
    tasks = tasks.merge(
        pd.DataFrame(computational_summaries),
        on="objective_id",
        how="left",
    )
    tasks[[
        "current_rows",
        "current_sources",
        "current_formulation_groups",
        "current_primary_formulation_groups",
        "current_chemistry_closed_groups",
        "computational_evidence_rows",
        "computational_hard_groups",
        "direct_low_fidelity_hard_groups",
        "mechanistic_proxy_hard_groups",
        "process_proxy_hard_groups",
    ]] = tasks[[
        "current_rows",
        "current_sources",
        "current_formulation_groups",
        "current_primary_formulation_groups",
        "current_chemistry_closed_groups",
        "computational_evidence_rows",
        "computational_hard_groups",
        "direct_low_fidelity_hard_groups",
        "mechanistic_proxy_hard_groups",
        "process_proxy_hard_groups",
    ]].fillna(0).astype(int)
    tasks["readiness_status"] = "rule_data_missing"
    model_task = tasks["objective_type"].eq("single_task_model")
    enough = tasks["current_chemistry_closed_groups"].ge(
        tasks["minimum_independent_formulations"]
    )
    tasks.loc[model_task & ~enough, "readiness_status"] = (
        "insufficient_chemistry_closed_labels"
    )
    tasks.loc[model_task & enough, "readiness_status"] = "baseline_ready"
    return tasks


def build_release() -> dict[str, pd.DataFrame]:
    """从冻结输入构建全部内存视图，不写文件。"""

    experiments = pd.read_csv(INPUT_PATHS["可用实验观测"], low_memory=False)
    commercial = pd.read_csv(INPUT_PATHS["商用构件证据"], low_memory=False)
    formulations = pd.read_csv(INPUT_PATHS["实验合理组合"], low_memory=False)

    labels = _build_labels(experiments)
    computational = _build_computational_evidence(INPUT_PATHS["可用计算观测"])
    return {
        "labels": labels,
        "audit": _build_audit(labels),
        "computational_evidence": computational,
        "computational_audit": _build_computational_audit(computational),
        "components": _build_components(commercial),
        "formulations": _build_formulations(formulations),
        "tasks": _build_tasks(labels, computational),
    }


def _readme(release: dict[str, pd.DataFrame]) -> str:
    labels = release["labels"]
    tasks = release["tasks"]
    task_rows = "\n".join(
        f"| {row.objective_name} | {row.objective_type} | "
        f"{row.current_chemistry_closed_groups} | "
        f"{row.direct_low_fidelity_hard_groups} | "
        f"{row.mechanistic_proxy_hard_groups} | {row.readiness_status} |"
        for row in tasks.itertuples(index=False)
    )
    return f"""# TPU/TPUU 定向五目标筛选数据集

- 版本：`{RELEASE_ID}`
- 三目标相关实验记录：{len(labels):,}行
- 受治理扩充包：29个、1,709行端点/记录
- 多目标材料索引：68个材料键，其中36个覆盖三类目标证据；TPUU-C/D/R/S由同来源拉伸、循环和TGA直接闭合，Cheetah与Filaflex 60A循环维度是应力松弛代理而非直接循环；新增EOS TPU 1301的17条拉伸与3条无身份冲突松弛曲线只计1个材料键；12个为交联PU迁移层，6个为商业TPU应用实验层，3个为Elastollan/PCL直接形状记忆文献配方，4个为Tecoflex药物复合实验配方，2个为IIR-OH相邻PU耐久配方，1个为TPU95A历史镜像重新物化，1个为PCF20硬质PU泡沫断裂迁移材料
- 商用构件：{len(release['components']):,}种
- 现实配方：{len(release['formulations']):,}个

## 结论

旧数据没有作废，而是重新分工：

| 旧资产 | 继续用途 | 禁止解释 |
|---|---|---|
| Gold-V结构与虚拟候选 | 化学空间、反应规则、适用域和候选排序 | 不是实验性能真值 |
| Gold-C计算与模拟 | 表示预训练、机理描述符、低保真代理和后段复核 | 不能无标记冒充宏观实验性能 |
| Gold-E实验曲线与标量 | 性能端点、来源内校准和最终实验残差 | 曲线点数不是材料数 |
| 商用构件与980个现实配方 | 成本/EHS门、现实Pareto候选和实验设计 | 目录证据不等于实时库存或低成本 |

新主线只训练三个清晰目标：韧性、循环恢复和热稳定。成本与环保保持透明规则约束，不制造虚假标签。昂贵DFT/MD在完成便宜的结构、配方、采购/EHS和模型预筛前保持后置。

`TPUU_Reaction_Conditions_ExcessH2O_Solvent_SEC_DSC.xlsx`仅比较无溶剂、过量水、DMF和DMF/H2O四种反应介质，没有稳定映射到21个最终TPUU配方；它保留为工艺机理证据，不复制为配方性能监督记录。

## 当前任务门

| 目标 | 类型 | 实验主目标闭合组 | 直接低保真计算组 | 机理代理组 | 状态 |
|---|---|---:|---:|---:|---|
{task_rows}

`已闭合化学配方组`只统计主目标中同一来源内存在组分表的配方；辅助强度、伸长、Tg等不会抬高主目标就绪数，试样重复、曲线点和工况变化也不会扩大为新化学体系。

## 文件

- `三目标实验标签.csv.gz`：三目标直接值、曲线和辅助性质，保留原条件、来源、划分和权重。
- `目标标签审计.csv`：逐性质的来源、配方、曲线、独立单元和映射缺口。
- `三目标计算证据.csv.gz`：与三目标相关的直接低保真、迁移、工况代理和机理代理计算记录。
- `计算证据审计.csv`：按硬分组审计计算证据，避免把同一配方的工况数当作材料数。
- `TGA热稳定端点.csv`：由独立脚本从TGA曲线提取T5/T10/T50；身份冲突单独阻断，Td,onset不在缺少统一切线协议时强行派生。
- `TPUU循环端点.csv`：由独立脚本从4条加载—卸载曲线提取80个逐循环恢复、残余应变、滞后能和保持率端点。
- `DRUM机械回收拉伸端点.csv`：从CC0来源物化107条核心TPUU独立拉伸曲线的强度、断裂伸长和韧性端点，覆盖21个配方代码。
- `Zenodo多孔TPU拉伸端点.csv`：从CC BY 4.0工作簿物化25条独立拉伸曲线，覆盖5个来源材料代码；商业TPU基体身份未闭合，因此只作辅助域。
- `Figshare强韧自愈端点.csv`与`Figshare强韧自愈曲线.csv.gz`：物化7条应力—应变曲线、5组原始/愈合汇总端点及性能保持率。
- `标准化热塑性弹性体TGA端点.csv`：从Zenodo原始TGA压缩包提取Cheetah和Filaflex 60A的T5/T10/T50，作为商业牌号辅助域。
- `标准化热塑性弹性体拉伸端点.csv`：物化相同两种商业热塑性弹性体的13条拉伸曲线，与TGA共享材料键。
- `标准热塑性弹性体松弛端点.csv`：从相同Zenodo来源的1,220,406个原始点提取Cheetah与Filaflex 60A在25%稳定保持段的1–10,000 s归一化应力保持率及90%/80%特征时间；仅作低权重恢复代理，不冒充直接循环或形状恢复。
- `DRUM机械回收TGA端点.csv`：提取4个软段家族、19个TPUU配方代码的T5/T10/T50，并保留Mn与硬段比例映射。
- `DRUM机械回收循环端点.csv`：由22条带试样几何的滞回曲线提取240个逐循环物理端点。
- `低天花板TPUU热稳定端点.csv`：从CC0原件提取TPUU-C/D/R/S四条TGA曲线的T5/T10/T50；与主发布表中的20条拉伸和4条循环曲线按同一材料代码闭合。
- `QUB生物基自修复TPU拉伸端点.csv`：从CC BY 4.0原始CSV物化41条跨文件去重后的独立本体拉伸试样，覆盖P35、P40、P45和P40-HDO四个配方键。
- `QUB生物基自修复TPU循环端点.csv`：同一P40试样的6个依赖循环，只作滞后与能量耗散代理；应变控制回零不解释为100%自然恢复。
- `QUB生物基自修复TPUTGA端点.csv`：从P35、P40和P45三条氮气TGA曲线提取T5/T10/T50与DTG峰值降解温度。
- `QUB生物基自修复TPU曲线.csv.gz`：保存41条拉伸、6个依赖循环和3条TGA曲线的原始/归一化点；曲线点不增加材料数。
- `DataInBrief形状记忆PU拉伸端点.csv`：从37次失效测试运行提取强度、断裂伸长和积分韧性；两条已知复制污染尾段共1702点被排除。
- `DataInBrief形状记忆PU循环端点.csv`：24次循环测试运行的240个测量循环端点，只作峰值应力保持、衰减和滞后耗散迁移代理，不冒充直接形状恢复率。
- `DataInBrief形状记忆PU热稳定端点.csv`：12个HDI/HPED/TEA交联PU配方的T5/T10/T50与DMA Tg；该来源不是热塑性TPU核心域。
- `商业TPU温度疲劳端点.csv`：5个Elastollan/Texin商业TPU牌号、190个独立物理试样的196条−20/20/55 ℃压缩疲劳与恢复历史；75条来源能量汇总与196条独立复算值分列保存。
- `商业TPU恢复配对端点.csv`：6个同试样100次冲击后46或49天环境恢复复测配对，监督50%压缩能量恢复；不是形状恢复率。
- `ElastollanPCL形状记忆端点.csv`：Elastollan 1154D与CAPA 6500的30/45/60 wt% TPU共混配方，保存论文表格中的直接形状固定率与恢复率；原始重复数和不确定性未报告，保持文献汇总低权重。
- `Tecoflex药物复合TPU多性能端点.csv`：Tecoflex EG-60D及2/5/10 wt%尼可刹米四配方的T5/T10/T50、部分拉伸曲线面积下界、模量和100%应变应力；纯药物TGA排除。
- `IIR-OH聚氨酯循环端点.csv`：HDI-4/HMDI-4各3次原始运行的600个逐循环峰值应力、加载/卸载能与滞后保持端点；`C0-50`表示0–50%应变范围，每条原始曲线实际含100圈。
- `IIR-OH聚氨酯水解保持端点.csv`：HDI-4/HMDI-4各3组水解前后拉伸保持配对；来源水解时间和介质尚未闭合，保持相邻PU低权重。
- `TPU95A载荷伸长端点.csv`：eSUN eTPU-95A三次拉伸运行的最大工程应变、最大载荷和载荷—伸长功；截面积缺失，不能生成MPa强度或韧性。
- `TPU95A应力松弛端点.csv`：0.1/0.2名义应变各3次运行的1/10/50/100 s保持率和50%特征时间；与历史资产SHA相同，重新物化但新增科学来源贡献为0。
- `PCF20泡沫拉伸断裂端点.csv`：Sawbones PCF20硬质PU泡沫12个ASTM D638拉伸和6个ASTM E399 SENB试样；发布DIC同步应力—应变面积及名义峰值载荷K，不宣称完整有效性判定后的K_IC。
- `TPU1301拉伸端点.csv`：EOS TPU 1301的17次室温SLS块体拉伸运行，保存强度、断裂/最大观测应变、0–5%模量和积分曲线面积；打印方向与速率不增加材料数。
- `TPU1301应力松弛端点.csv`：3次身份闭合松弛运行的1/10/100/300 s保持率；文件名7H但内嵌6V的第4次运行隔离为零权重，松弛仍只作恢复代理。
- `本地来源审计.csv`与`本地扩库队列.csv`：只读扫描本地原件元数据并按三目标排出增量接入顺序。
- `外部来源候选.csv`：通过官方API新增的开放来源及本地原件清单、许可证和引用状态。
- `三目标配方特征.csv.gz`：由独立脚本生成980个现实TPU配方的身份、结构上下文、计量和计算前门。
- `训练前任务清单.csv`与`训练前发布清单.json`：明确记录尚未启动模型训练、预测或新量化计算。
- `现实构件约束.csv`：24种商用构件，价格和结构化EHS未知时明确留空。
- `现实配方候选.csv`：980个TPU现实配方；当前没有胺类扩链剂，因此不得宣称覆盖TPUU空间。
- `筛选任务清单.csv`：三个单任务模型和两个规则目标的最低数据门。
- `发布清单.json`：输入输出哈希与数量。

## 下一步顺序

1. 已完成QUB、DataInBrief、商业TPU疲劳、Elastollan/PCL、Tecoflex、IIR-OH、TPU95A镜像及PCF20断裂接入；下一批继续优先处理能让现有材料形成三目标闭合的数据。
2. 定向补齐韧性、循环恢复和热分解端点的组分—配方—工艺映射；循环滞后代理与直接恢复率始终分层。
3. 为24种商用构件录入同地区同日期报价和结构化SDS/GHS字段。
4. 增加商业二胺/胺类扩链剂后，单独生成TPUU现实候选。
5. 用户解除“计算前停止”门后，才进入模型训练、Pareto预测和新量化计算。

## 参考入口

- `结果/可用数据集/README.md`
- `文档/Gold数据集定义.md`
- `文档/数据来源与参考文献.md`
"""


def _write_outputs(release: dict[str, pd.DataFrame], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(release["labels"], output / OUTPUT_FILENAMES["三目标实验标签"])
    _write_csv(release["audit"], output / OUTPUT_FILENAMES["目标标签审计"])
    _write_csv(
        release["computational_evidence"],
        output / OUTPUT_FILENAMES["三目标计算证据"],
    )
    _write_csv(
        release["computational_audit"],
        output / OUTPUT_FILENAMES["计算证据审计"],
    )
    _write_csv(release["components"], output / OUTPUT_FILENAMES["现实构件约束"])
    _write_csv(release["formulations"], output / OUTPUT_FILENAMES["现实配方候选"])
    _write_csv(release["tasks"], output / OUTPUT_FILENAMES["筛选任务清单"])
    (output / OUTPUT_FILENAMES["使用说明"]).write_text(
        _readme(release), encoding="utf-8"
    )


def _counts(release: dict[str, pd.DataFrame]) -> dict[str, int]:
    labels = release["labels"]
    computational = release["computational_evidence"]
    return {
        "target_label_rows": len(labels),
        "target_property_count": labels["property_name"].nunique(),
        "target_family_count": labels["target_family"].nunique(),
        "target_source_count": labels["source_id"].nunique(),
        "computational_evidence_rows": len(computational),
        "computational_evidence_unique_observations": computational[
            "observation_id"
        ].nunique(),
        "computational_evidence_property_count": computational[
            "property_name"
        ].nunique(),
        "computational_evidence_hard_groups": computational[
            "leakage_group"
        ].nunique(),
        "commercial_component_rows": len(release["components"]),
        "realistic_formulation_rows": len(release["formulations"]),
        "objective_count": len(release["tasks"]),
    }


def write_release(release: dict[str, pd.DataFrame]) -> None:
    _write_outputs(release, OUTPUT)
    output_entries = {
        key: _file_entry(OUTPUT / filename)
        for key, filename in OUTPUT_FILENAMES.items()
    }
    manifest = {
        "release_id": RELEASE_ID,
        "schema_version": SCHEMA_VERSION,
        "counts": _counts(release),
        "input_files": {key: _file_entry(path) for key, path in INPUT_PATHS.items()},
        "output_files": output_entries,
        "policy": {
            "modeled_targets": [
                "toughness",
                "cyclic_recovery",
                "thermal_stability",
            ],
            "deterministic_constraints": ["cost", "environment"],
            "computational_evidence_roles": [
                "direct_low_fidelity_target",
                "transfer_low_fidelity_target",
                "process_response_proxy",
                "mechanistic_proxy",
            ],
            "expensive_calculation_gate": "deferred_until_multitarget_prefilter",
            "tpuu_status": "blocked_until_commercial_amine_extenders_added",
        },
    }
    (OUTPUT / "发布清单.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def check_release(release: dict[str, pd.DataFrame]) -> None:
    manifest_path = OUTPUT / "发布清单.json"
    if not manifest_path.is_file():
        raise SystemExit("缺少发布清单；请先运行生成模式")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["release_id"] != RELEASE_ID or manifest["counts"] != _counts(release):
        raise SystemExit("发布版本或数量与当前输入不一致")
    for key, path in INPUT_PATHS.items():
        if manifest["input_files"][key] != _file_entry(path):
            raise SystemExit(f"输入文件已变化：{key}")

    with tempfile.TemporaryDirectory(prefix="tpu-directed-check-") as directory:
        temporary = Path(directory)
        _write_outputs(release, temporary)
        for key, filename in OUTPUT_FILENAMES.items():
            actual = OUTPUT / filename
            expected = temporary / filename
            if not actual.is_file() or _sha256(actual) != _sha256(expected):
                raise SystemExit(f"发布文件与当前生成逻辑不一致：{key}")
            if manifest["output_files"][key] != _file_entry(actual):
                raise SystemExit(f"发布清单哈希不一致：{key}")
    print("定向五目标数据集检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    release = build_release()
    if args.检查:
        check_release(release)
    else:
        write_release(release)
        print(json.dumps(_counts(release), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
