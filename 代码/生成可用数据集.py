"""从冻结的 Gold-V/C/E 参考层生成任务化、可直接使用的数据发布视图。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "结果"
OUTPUT = RESULTS / "可用数据集"
RELEASE_ID = "tpu-usable-2026-07-21-v1"
SCHEMA_VERSION = "1.0.0"
SPLIT_SEED = "tpu-usable-split-v1"

INPUT_PATHS = {
    "Gold-V": RESULTS / "Gold_V_候选.csv.gz",
    "Gold-C": RESULTS / "Gold_C_计算性能.csv.gz",
    "Gold-E": RESULTS / "Gold_E_实验表格.csv.gz",
    "来源总账": RESULTS / "数据规模总账.csv",
}

OUTPUT_PATHS = {
    "使用说明": OUTPUT / "README.md",
    "候选结构": OUTPUT / "候选结构.csv.gz",
    "计算观测": OUTPUT / "计算观测.csv.gz",
    "实验观测": OUTPUT / "实验观测.csv.gz",
    "曲线索引": OUTPUT / "曲线索引.csv",
    "任务清单": OUTPUT / "任务清单.csv",
    "来源与引用": OUTPUT / "来源与引用.csv",
    "字段字典": OUTPUT / "字段字典.csv",
    "发布清单": OUTPUT / "发布清单.json",
}

CONTEXT_C_ROLES = {
    "simulation_input_descriptor",
    "sensitivity_input_descriptor",
}

CONTEXT_E_KINDS = {
    "formulation_component",
    "specimen_or_geometry_descriptor",
    "specimen_descriptor",
    "process_condition",
    "formulation_or_process_scalar",
    "reagent_scalar",
}

CHARACTERIZATION_E_KINDS = {
    "molecular_characterization",
    "spectral_feature",
    "nmr_1H_peak",
    "nmr_13C_peak",
}

ROLE_FACTORS_C = {
    "polymer_transfer_computational_reference": 0.60,
    "computational_feature_or_mechanistic_reference": 0.50,
    "simulation_input_descriptor": 0.00,
    "sensitivity_input_descriptor": 0.00,
}

TASK_DESCRIPTIONS = {
    "候选_结构排序": {
        "purpose": "构件、虚拟重复单元和相邻化学空间的分级筛选",
        "recommended_model": "规则过滤、聚类、多目标排序或主动学习",
        "caveat": "候选权重是排序先验，不是性能真值，也不代表已证明可合成",
    },
    "计算_结构多任务预训练": {
        "purpose": "用可追溯量化计算和分子描述符学习结构表示",
        "recommended_model": "多任务 GNN、指纹模型或缺失标签多任务回归",
        "caveat": "通用聚合物迁移数据不能直接当作 TPU 宏观实验性能",
    },
    "计算_过程代理模型": {
        "purpose": "在已知模拟体系内学习工况到过程/力学响应",
        "recommended_model": "条件回归、代理模型或物理约束网络",
        "caveat": "同一名义配方的大量工况不能解释为大量新材料",
    },
    "上下文_仅作输入": {
        "purpose": "保留配方、几何、工艺和模拟输入，不进入目标损失",
        "recommended_model": "仅作为特征或条件变量",
        "caveat": "recommended_loss_weight 恒为 0",
    },
    "实验_曲线建模": {
        "purpose": "应力应变、循环、热分析、光谱和过程曲线建模",
        "recommended_model": "序列模型、函数回归或先提取稳健端点",
        "caveat": "同一曲线全部点同折，逐点权重之和受一条曲线权重上限约束",
    },
    "实验_标量校准": {
        "purpose": "用实验标量校准最终性能预测和多保真残差",
        "recommended_model": "组分/配方/工艺条件化回归",
        "caveat": "只有结构—配方—工艺映射闭合的子集可用于纯结构到性能建模",
    },
}

FIELD_DEFINITIONS = {
    "release_id": "可用数据集冻结版本",
    "gold_layer": "来源参考层：Gold-V、Gold-C或Gold-E",
    "task_id": "推荐使用任务；不同任务不可无条件混合",
    "usage_mode": "primary_train、auxiliary_train、context_only或reference_only",
    "model_ready": "是否通过当前任务的数值、单位、角色和权重门",
    "target_role": "该行在任务中的目标/辅助/输入语义",
    "candidate_id": "稳定候选结构主键",
    "source_id": "来源或物化视图标识",
    "source_family_id": "同一论文、数据集及附件共享的来源族",
    "source_record_id": "来源内部记录标识",
    "observation_id": "逐观测稳定主键",
    "canonical_structure": "计算体系使用的规范聚合物结构表示",
    "canonical_smiles": "候选结构的规范SMILES或pSMILES",
    "formulation_id": "配方标识；空值表示尚未映射",
    "sample_id": "试样/材料实例标识",
    "simulation_key": "一次可复算模拟或计算体系标识",
    "curve_id": "整条实验曲线标识",
    "point_index": "曲线内部点序号，不是独立样本序号",
    "property_name": "原始且已审计的性质名称",
    "value": "数值，语义由property_name与unit共同确定",
    "unit": "来源单位或已规范化单位",
    "unit_status": "计算记录单位解析状态",
    "method_family": "DFT、MD、NEMD、FEA、CFD/PBE等方法族",
    "method_detail": "计算方法、软件或协议细节",
    "method_or_test_protocol": "实验方法、测试协议或标准",
    "condition_name": "主要条件或曲线横轴名称",
    "condition_value": "主要条件或曲线横轴数值",
    "condition_unit": "主要条件单位",
    "fidelity_level": "实验/计算/虚拟数据的保真度描述",
    "gold_admission_status": "参考层准入状态",
    "property_admission_status": "计算性质的任务级准入状态",
    "leakage_group": "结构、模拟、曲线、样品和配方传递闭包后的硬分组",
    "development_split": "按hard_group固定生成的train/validation/test",
    "source_holdout_fold": "按来源族固定生成的五折严格外推编号",
    "independent_unit": "权重归一所用的曲线、样品或模拟单位",
    "potential_weight_ceiling": "Gold参考层给出的潜在权重上限",
    "quality_factor": "正式/条件及目标角色对应的任务质量因子",
    "role_factor": "直接目标、迁移或上下文角色因子",
    "independence_weight": "同一独立单元同性质的重复/点数归一因子",
    "recommended_loss_weight": "建议损失权重，不回写原Gold层",
    "source_balanced_sampling_probability": "任务内各有效来源总概率相等的抽样概率",
    "candidate_use": "直接构件、虚拟重复单元、相邻化学或迁移参考",
    "screening_ready": "候选是否通过结构级初筛门",
    "linear_component_class": "线性TPU主路线的二异氰酸酯、二醇扩链剂或宏二醇",
    "linear_tpu_building_block_ready": "是否通过中性、单组分、双官能且无竞争官能团门",
    "candidate_priority_weight": "仅用于候选排序的先验权重，不能进入性能监督",
    "source_locator": "可复核到原始文件、数据记录或行的定位信息",
    "citation_keys": "连接文档/数据来源与参考文献.md的引用键",
    "license": "实验记录携带的来源许可证",
    "license_spdx": "候选来源的SPDX许可证",
    "license_status": "来源总账中的训练/再分发权利状态",
    "exclusion_reason": "该行未成为任务监督目标的直接原因",
}


def _text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hash_int(value: object) -> int:
    payload = f"{SPLIT_SEED}|{value}".encode("utf-8")
    return int(hashlib.sha256(payload).hexdigest()[:16], 16)


def deterministic_split(value: object) -> str:
    bucket = _hash_int(value) % 100
    if bucket < 80:
        return "train"
    if bucket < 90:
        return "validation"
    return "test"


def deterministic_fold(value: object, folds: int = 5) -> int:
    return _hash_int(value) % folds


def _replace_with_retry(source: Path, target: Path) -> None:
    for attempt in range(8):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.15 * (attempt + 1))


def _write_csv(frame: pd.DataFrame, path: Path, *, gzip_output: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    if gzip_output:
        compression: dict[str, object] | None = {
            "method": "gzip",
            "compresslevel": 9,
            "mtime": 0,
        }
        encoding = "utf-8"
    else:
        compression = None
        encoding = "utf-8-sig"
    frame.to_csv(
        temporary,
        index=False,
        encoding=encoding,
        lineterminator="\n",
        compression=compression,
    )
    _replace_with_retry(temporary, path)


def _write_json(payload: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _replace_with_retry(temporary, path)


def _source_maps(ledger: pd.DataFrame) -> dict[str, dict[str, str]]:
    indexed = ledger.set_index("source_id", verify_integrity=True)
    fields = [
        "source_family_id",
        "source_title",
        "canonical_identifier",
        "origin_kind",
        "gold_layer",
        "citation_keys",
        "license_status",
        "quality_status",
    ]
    return {
        field: _text(indexed[field]).to_dict()
        for field in fields
    }


def _attach_source_fields(
    frame: pd.DataFrame, maps: dict[str, dict[str, str]]
) -> pd.DataFrame:
    source = _text(frame["source_id"])
    if "source_family_id" not in frame:
        frame["source_family_id"] = source.map(maps["source_family_id"])
    else:
        existing = _text(frame["source_family_id"])
        frame["source_family_id"] = existing.mask(
            existing.eq(""), source.map(maps["source_family_id"])
        )
    frame["source_family_id"] = _text(frame["source_family_id"]).mask(
        _text(frame["source_family_id"]).eq(""), source
    )
    frame["license_status"] = source.map(maps["license_status"]).fillna("")
    return frame


class _DisjointSet:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        parent = self.parent.setdefault(item, item)
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, first: str, second: str) -> None:
        root_first = self.find(first)
        root_second = self.find(second)
        if root_first != root_second:
            smaller, larger = sorted((root_first, root_second))
            self.parent[larger] = smaller


def _assign_hard_groups(
    computational: pd.DataFrame,
    experimental: pd.DataFrame,
) -> None:
    """Build transitive leakage components before assigning any split.

    The existing split group is preserved as an edge, then connected through
    structure/simulation IDs for Gold-C and curve/sample/formulation IDs for
    Gold-E.  Source-family prefixes prevent accidental joins on generic labels.
    """

    disjoint = _DisjointSet()

    c_columns = [
        "source_family_id",
        "split_group",
        "global_structure_family_key",
        "simulation_key",
    ]
    c_unique = computational[c_columns].fillna("").astype(str).drop_duplicates()
    for row in c_unique.itertuples(index=False):
        family, split_group, structure, simulation = (
            str(value).strip() for value in row
        )
        tokens = []
        if split_group:
            tokens.append(f"split|{split_group}")
        if structure:
            tokens.append(f"structure|{structure}")
        if simulation:
            tokens.append(f"simulation|{family}|{simulation}")
        for token in tokens:
            disjoint.find(token)
        for token in tokens[1:]:
            disjoint.union(tokens[0], token)

    e_columns = [
        "source_family_id",
        "split_group",
        "family_leakage_group",
        "curve_id",
        "sample_id",
        "formulation_id",
    ]
    e_unique = experimental[e_columns].fillna("").astype(str).drop_duplicates()
    for row in e_unique.itertuples(index=False):
        family, split_group, family_group, curve, sample, formulation = (
            str(value).strip() for value in row
        )
        tokens = []
        if split_group:
            tokens.append(f"split|{split_group}")
        if family_group:
            tokens.append(f"family_group|{family}|{family_group}")
        if curve:
            tokens.append(f"curve|{family}|{curve}")
        if sample:
            tokens.append(f"sample|{family}|{sample}")
        if formulation:
            tokens.append(f"formulation|{family}|{formulation}")
        for token in tokens:
            disjoint.find(token)
        for token in tokens[1:]:
            disjoint.union(tokens[0], token)

    members: dict[str, list[str]] = {}
    for token in disjoint.parent:
        members.setdefault(disjoint.find(token), []).append(token)
    component_id: dict[str, str] = {}
    for tokens in members.values():
        canonical = min(tokens)
        identifier = "hard_group_" + hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()[:16]
        for token in tokens:
            component_id[token] = identifier

    def map_split(frame: pd.DataFrame) -> pd.Series:
        tokens = "split|" + _text(frame["split_group"])
        missing = sorted(set(tokens) - set(component_id))
        if missing:
            raise ValueError(f"硬分组缺少split token: {missing[:3]}")
        return tokens.map(component_id)

    computational["leakage_group"] = map_split(computational)
    experimental["leakage_group"] = map_split(experimental)


def _assign_splits(frame: pd.DataFrame) -> pd.DataFrame:
    leakage = _text(frame["leakage_group"])
    fallback = _text(frame["observation_id"])
    leakage = leakage.mask(leakage.eq(""), fallback)
    frame["leakage_group"] = leakage
    frame["development_split"] = leakage.map(deterministic_split)
    frame["source_holdout_fold"] = _text(frame["source_family_id"]).map(
        deterministic_fold
    )
    return frame


def _materialize_weights(frame: pd.DataFrame) -> pd.DataFrame:
    ceiling = pd.to_numeric(frame["potential_weight_ceiling"], errors="coerce").fillna(0.0)
    quality = pd.to_numeric(frame["quality_factor"], errors="coerce").fillna(0.0)
    role = pd.to_numeric(frame["role_factor"], errors="coerce").fillna(0.0)
    preliminary = ceiling * quality * role
    eligible = frame["model_ready"].astype(bool) & preliminary.gt(0)
    zero_effective = ~eligible & frame["model_ready"].astype(bool)
    frame.loc[zero_effective, "exclusion_reason"] = "zero_effective_weight"
    frame.loc[zero_effective, "usage_mode"] = "reference_only"
    frame["model_ready"] = eligible

    group_columns = ["task_id", "property_name", "independent_unit"]
    counts = pd.Series(1.0, index=frame.index)
    counts.loc[eligible] = (
        frame.loc[eligible]
        .groupby(group_columns, dropna=False)["observation_id"]
        .transform("size")
        .astype(float)
    )
    independence = pd.Series(0.0, index=frame.index)
    independence.loc[eligible] = 1.0 / counts.loc[eligible]
    frame["independence_weight"] = independence
    frame["recommended_loss_weight"] = (preliminary * independence).round(12)

    source_total = frame.groupby(
        ["task_id", "source_id"], dropna=False
    )["recommended_loss_weight"].transform("sum")
    active_source_count = (
        frame.loc[frame["recommended_loss_weight"].gt(0)]
        .groupby("task_id")["source_id"]
        .nunique()
        .to_dict()
    )
    task_source_count = frame["task_id"].map(active_source_count).fillna(0).astype(float)
    probability = pd.Series(0.0, index=frame.index)
    positive = frame["recommended_loss_weight"].gt(0) & source_total.gt(0) & task_source_count.gt(0)
    probability.loc[positive] = (
        frame.loc[positive, "recommended_loss_weight"]
        / source_total.loc[positive]
        / task_source_count.loc[positive]
    )
    frame["source_balanced_sampling_probability"] = probability.round(15)
    frame["quality_factor"] = quality.round(6)
    frame["role_factor"] = role.round(6)
    return frame


def _classify_c(frame: pd.DataFrame) -> pd.DataFrame:
    role = _text(frame["record_role"])
    structure = _text(frame["canonical_structure"])

    frame["task_id"] = "计算_过程代理模型"
    frame.loc[structure.ne(""), "task_id"] = "计算_结构多任务预训练"
    frame.loc[role.isin(CONTEXT_C_ROLES), "task_id"] = "上下文_仅作输入"
    frame["target_role"] = role

    unit_status = _text(frame["unit_status"]).str.casefold()
    validation = _text(frame["source_validation_status"]).str.casefold()
    numeric = pd.to_numeric(frame["value"], errors="coerce")
    finite = pd.Series(np.isfinite(numeric), index=frame.index)
    unit_usable = ~unit_status.str.contains("unresolved", regex=False)
    validation_usable = ~validation.str.contains(
        "failed_check|anomaly_retained", regex=True
    )
    target = ~role.isin(CONTEXT_C_ROLES)
    technically_usable = finite & unit_usable & validation_usable & target
    primary = (
        technically_usable
        & _text(frame["gold_admission_status"]).eq("admitted_reference")
        & _text(frame["property_admission_status"]).eq("admitted_reference")
        & frame["task_id"].ne("计算_过程代理模型")
    )
    auxiliary = technically_usable & ~primary

    frame["usage_mode"] = "reference_only"
    frame.loc[primary, "usage_mode"] = "primary_train"
    frame.loc[auxiliary, "usage_mode"] = "auxiliary_train"
    frame.loc[role.isin(CONTEXT_C_ROLES), "usage_mode"] = "context_only"
    frame["model_ready"] = primary | auxiliary

    reason = pd.Series("", index=frame.index, dtype=object)
    reason.loc[~finite] = "non_numeric_value"
    reason.loc[finite & ~unit_usable] = "unit_semantics_unresolved"
    reason.loc[finite & unit_usable & ~validation_usable] = "source_validation_flag"
    reason.loc[role.isin(CONTEXT_C_ROLES)] = "input_descriptor_not_target"
    frame["exclusion_reason"] = reason

    quality = pd.Series(0.0, index=frame.index)
    quality.loc[primary] = 1.0
    quality.loc[auxiliary] = 0.35
    quality.loc[auxiliary & frame["task_id"].eq("计算_过程代理模型")] = 0.25
    frame["quality_factor"] = quality
    frame["role_factor"] = role.map(ROLE_FACTORS_C).fillna(1.0)

    simulation = _text(frame["simulation_key"])
    frame["independent_unit"] = simulation.mask(
        simulation.eq(""), _text(frame["split_group"])
    )
    return frame


def prepare_computational(
    maps: dict[str, dict[str, str]]
) -> pd.DataFrame:
    frame = pd.read_csv(INPUT_PATHS["Gold-C"], low_memory=False)
    frame = _attach_source_fields(frame, maps)
    return _classify_c(frame)


def finalize_computational(frame: pd.DataFrame) -> pd.DataFrame:
    frame = _assign_splits(frame)
    frame = _materialize_weights(frame)
    frame.insert(0, "release_id", RELEASE_ID)
    frame.insert(1, "gold_layer", "Gold-C")

    columns = [
        "release_id",
        "gold_layer",
        "task_id",
        "usage_mode",
        "model_ready",
        "source_id",
        "source_family_id",
        "source_record_id",
        "observation_id",
        "canonical_structure",
        "system_identity",
        "structure_identity_status",
        "global_structure_family_key",
        "simulation_key",
        "record_role",
        "target_role",
        "property_name",
        "value",
        "unit",
        "unit_status",
        "method_family",
        "method_detail",
        "temp",
        "press",
        "fidelity_level",
        "gold_admission_status",
        "property_admission_status",
        "source_validation_status",
        "leakage_group",
        "development_split",
        "source_holdout_fold",
        "independent_unit",
        "potential_weight_ceiling",
        "quality_factor",
        "role_factor",
        "independence_weight",
        "recommended_loss_weight",
        "source_balanced_sampling_probability",
        "license_status",
        "source_locator",
        "citation_keys",
        "exclusion_reason",
    ]
    return frame[columns]


def _classify_e(frame: pd.DataFrame) -> pd.DataFrame:
    kind = _text(frame["record_kind"])
    curve = _text(frame["curve_id"]).ne("")

    frame["task_id"] = "实验_标量校准"
    frame.loc[curve, "task_id"] = "实验_曲线建模"
    frame.loc[kind.isin(CONTEXT_E_KINDS), "task_id"] = "上下文_仅作输入"
    frame["target_role"] = "experimental_scalar"
    frame.loc[curve, "target_role"] = "curve_target"
    frame.loc[kind.isin(CHARACTERIZATION_E_KINDS), "target_role"] = (
        "auxiliary_characterization"
    )
    frame.loc[kind.eq("literature_aggregate_property"), "target_role"] = (
        "literature_aggregate"
    )
    frame.loc[kind.eq("experimental_target_transformed"), "target_role"] = (
        "transformed_target"
    )
    frame.loc[kind.isin(CONTEXT_E_KINDS), "target_role"] = "feature_only"

    unit = _text(frame["unit"]).str.casefold()
    numeric = pd.to_numeric(frame["value"], errors="coerce")
    finite = pd.Series(np.isfinite(numeric), index=frame.index)
    unit_usable = unit.ne("") & ~unit.str.contains("unresolved", regex=False)
    target = ~kind.isin(CONTEXT_E_KINDS)
    technically_usable = finite & unit_usable & target
    admitted = _text(frame["gold_admission_status"]).eq("admitted_reference")
    primary = technically_usable & admitted
    auxiliary = technically_usable & ~admitted

    frame["usage_mode"] = "reference_only"
    frame.loc[primary, "usage_mode"] = "primary_train"
    frame.loc[auxiliary, "usage_mode"] = "auxiliary_train"
    frame.loc[kind.isin(CONTEXT_E_KINDS), "usage_mode"] = "context_only"
    frame["model_ready"] = primary | auxiliary

    reason = pd.Series("", index=frame.index, dtype=object)
    reason.loc[~finite] = "non_numeric_value"
    reason.loc[finite & ~unit_usable] = "unit_semantics_unresolved_or_missing"
    reason.loc[kind.isin(CONTEXT_E_KINDS)] = "context_descriptor_not_target"
    frame["exclusion_reason"] = reason

    quality = pd.Series(0.0, index=frame.index)
    quality.loc[primary] = 1.0
    quality.loc[auxiliary] = 0.25
    transfer_target = frame["target_role"].isin(
        {"literature_aggregate", "transformed_target"}
    )
    characterization_target = frame["target_role"].eq("auxiliary_characterization")
    quality.loc[frame["model_ready"] & transfer_target] *= 0.50
    quality.loc[frame["model_ready"] & characterization_target] *= 0.75
    frame["quality_factor"] = quality
    frame["role_factor"] = 1.0

    curve_id = _text(frame["curve_id"])
    sample_id = _text(frame["sample_id"])
    observation = _text(frame["observation_id"])
    independent = curve_id.mask(curve_id.eq(""), sample_id)
    independent = independent.mask(independent.eq(""), observation)
    frame["independent_unit"] = independent

    return frame


def prepare_experimental(
    maps: dict[str, dict[str, str]]
) -> pd.DataFrame:
    frame = pd.read_csv(INPUT_PATHS["Gold-E"], low_memory=False)
    frame = _attach_source_fields(frame, maps)
    return _classify_e(frame)


def finalize_experimental(frame: pd.DataFrame) -> pd.DataFrame:
    frame = _assign_splits(frame)
    frame = _materialize_weights(frame)
    frame.insert(0, "release_id", RELEASE_ID)
    frame.insert(1, "gold_layer", "Gold-E")

    columns = [
        "release_id",
        "gold_layer",
        "task_id",
        "usage_mode",
        "model_ready",
        "source_id",
        "source_family_id",
        "source_record_id",
        "observation_id",
        "formulation_id",
        "sample_id",
        "curve_id",
        "point_index",
        "record_kind",
        "target_role",
        "property_name",
        "value",
        "unit",
        "uncertainty_value",
        "uncertainty_type",
        "condition_name",
        "condition_value",
        "condition_unit",
        "secondary_condition_name",
        "secondary_condition_value",
        "secondary_condition_unit",
        "auxiliary_value_name",
        "auxiliary_value",
        "auxiliary_unit",
        "method_or_test_protocol",
        "target_origin",
        "data_origin",
        "fidelity_level",
        "gold_admission_status",
        "mapping_status",
        "protocol_status",
        "leakage_group",
        "development_split",
        "source_holdout_fold",
        "independent_unit",
        "potential_weight_ceiling",
        "quality_factor",
        "role_factor",
        "independence_weight",
        "recommended_loss_weight",
        "source_balanced_sampling_probability",
        "license",
        "license_status",
        "source_locator",
        "citation_keys",
        "exclusion_reason",
    ]
    return frame[columns]


def build_candidates(
    maps: dict[str, dict[str, str]]
) -> pd.DataFrame:
    frame = pd.read_csv(INPUT_PATHS["Gold-V"], low_memory=False)
    frame = _attach_source_fields(frame, maps)
    scope = _text(frame["screening_scope"])
    admitted = _text(frame["gold_admission_status"]).eq("admitted_reference")
    functional = _text(frame["functional_group_match"]).str.casefold().isin(
        {"true", "1", "yes"}
    )

    frame["candidate_use"] = "reference_only"
    frame.loc[scope.eq("direct_tpu_building_block"), "candidate_use"] = (
        "direct_building_block"
    )
    frame.loc[
        scope.isin(
            {
                "virtual_tpu_repeat_unit_candidate",
                "virtual_polyurethane_fragment",
            }
        ),
        "candidate_use",
    ] = "virtual_repeat_unit"
    frame.loc[
        scope.isin(
            {
                "tpuu_or_nipu_building_block",
                "polyol_synthesis_precursor",
                "adjacent_reactive_building_block",
            }
        ),
        "candidate_use",
    ] = "adjacent_chemistry"
    frame.loc[scope.eq("general_polymer_md_transfer_reference"), "candidate_use"] = (
        "transfer_reference"
    )

    ready_use = frame["candidate_use"].isin(
        {"direct_building_block", "virtual_repeat_unit", "adjacent_chemistry"}
    )
    frame["screening_ready"] = admitted & functional & ready_use
    role = _text(frame["tpu_role"])
    nco = pd.to_numeric(frame["isocyanate_group_count"], errors="coerce").fillna(0)
    hydroxyl = pd.to_numeric(frame["hydroxyl_group_count"], errors="coerce").fillna(0)
    amine = pd.to_numeric(frame["amine_group_count"], errors="coerce").fillna(0)
    thiol = pd.to_numeric(frame["thiol_group_count"], errors="coerce").fillna(0)
    acid = pd.to_numeric(
        frame["carboxylic_acid_group_count"], errors="coerce"
    ).fillna(0)
    carbonate = pd.to_numeric(
        frame["cyclic_carbonate_group_count"], errors="coerce"
    ).fillna(0)
    epoxide = pd.to_numeric(frame["epoxide_group_count"], errors="coerce").fillna(0)
    clean_base = (
        admitted
        & functional
        & scope.eq("direct_tpu_building_block")
        & pd.to_numeric(frame["formal_charge"], errors="coerce").fillna(999).eq(0)
        & ~_text(frame["canonical_smiles"]).str.contains(".", regex=False)
        & _text(frame["license_spdx"]).ne("")
    )
    no_competing_groups = (
        amine.eq(0)
        & thiol.eq(0)
        & acid.eq(0)
        & carbonate.eq(0)
        & epoxide.eq(0)
    )
    frame["linear_component_class"] = ""
    clean_diisocyanate = (
        clean_base
        & role.eq("diisocyanate_candidate")
        & nco.eq(2)
        & hydroxyl.eq(0)
        & no_competing_groups
    )
    clean_diol = (
        clean_base
        & role.eq("diol_chain_extender_candidate")
        & hydroxyl.eq(2)
        & nco.eq(0)
        & no_competing_groups
    )
    clean_macrodiol = (
        clean_base
        & role.eq("macrodiol_polyol_candidate")
        & hydroxyl.eq(2)
        & nco.eq(0)
        & no_competing_groups
    )
    frame.loc[clean_diisocyanate, "linear_component_class"] = "diisocyanate"
    frame.loc[clean_diol, "linear_component_class"] = "chain_extender_diol"
    frame.loc[clean_macrodiol, "linear_component_class"] = "macrodiol"
    frame["linear_tpu_building_block_ready"] = _text(
        frame["linear_component_class"]
    ).ne("")
    priority = pd.to_numeric(frame["screening_priority"], errors="coerce")
    priority_weight = priority.map(
        {1.0: 1.00, 2.0: 0.75, 3.0: 0.50, 4.0: 0.25, 5.0: 0.10, 9.0: 0.00}
    ).fillna(0.0)
    admission_factor = np.where(admitted, 1.0, 0.5)
    functional_factor = np.where(functional, 1.0, 0.25)
    frame["candidate_priority_weight"] = (
        priority_weight * admission_factor * functional_factor
    ).round(6)
    structure_key = _text(frame["canonical_smiles"])
    frame["development_split"] = structure_key.map(deterministic_split)
    frame["source_holdout_fold"] = _text(frame["source_family_id"]).map(
        deterministic_fold
    )
    frame.insert(0, "release_id", RELEASE_ID)
    frame["gold_layer"] = "Gold-V"

    columns = [
        "release_id",
        "gold_layer",
        "candidate_id",
        "source_id",
        "source_family_id",
        "source_record_id",
        "source_locator",
        "preferred_name",
        "raw_smiles",
        "canonical_smiles",
        "inchikey",
        "molecular_formula_calculated",
        "molecular_weight_calculated_g_mol",
        "exact_mass_g_mol",
        "formal_charge",
        "isocyanate_group_count",
        "hydroxyl_group_count",
        "amine_group_count",
        "thiol_group_count",
        "carboxylic_acid_group_count",
        "cyclic_carbonate_group_count",
        "epoxide_group_count",
        "tpu_role",
        "role_confidence",
        "screening_scope",
        "screening_priority",
        "functional_group_match",
        "candidate_use",
        "screening_ready",
        "linear_component_class",
        "linear_tpu_building_block_ready",
        "candidate_priority_weight",
        "development_split",
        "source_holdout_fold",
        "structure_status",
        "gold_admission_status",
        "data_origin",
        "fidelity_level",
        "prediction_uncertainty",
        "generation_rule_version",
        "license_spdx",
        "license_status",
    ]
    return frame[columns]


def build_curve_index(experimental: pd.DataFrame) -> pd.DataFrame:
    curves = experimental[_text(experimental["curve_id"]).ne("")].copy()
    curves["_point_index_numeric"] = pd.to_numeric(
        curves["point_index"], errors="coerce"
    )
    curves["_value_numeric"] = pd.to_numeric(curves["value"], errors="coerce")
    curves["_condition_numeric"] = pd.to_numeric(
        curves["condition_value"], errors="coerce"
    )
    grouped = curves.groupby("curve_id", sort=True, dropna=False)
    index = grouped.agg(
        source_id=("source_id", "first"),
        source_family_id=("source_family_id", "first"),
        formulation_id=("formulation_id", "first"),
        sample_id=("sample_id", "first"),
        property_name=("property_name", "first"),
        unit=("unit", "first"),
        point_count=("observation_id", "size"),
        point_index_min=("_point_index_numeric", "min"),
        point_index_max=("_point_index_numeric", "max"),
        value_min=("_value_numeric", "min"),
        value_max=("_value_numeric", "max"),
        condition_name=("condition_name", "first"),
        condition_unit=("condition_unit", "first"),
        condition_min=("_condition_numeric", "min"),
        condition_max=("_condition_numeric", "max"),
        gold_admission_status=("gold_admission_status", "first"),
        usage_mode=("usage_mode", "first"),
        model_ready=("model_ready", "all"),
        leakage_group=("leakage_group", "first"),
        development_split=("development_split", "first"),
        source_holdout_fold=("source_holdout_fold", "first"),
        curve_total_loss_weight=("recommended_loss_weight", "sum"),
        source_locator=("source_locator", "first"),
        citation_keys=("citation_keys", "first"),
        license=("license", "first"),
    ).reset_index()
    index.insert(0, "release_id", RELEASE_ID)
    index["curve_total_loss_weight"] = index["curve_total_loss_weight"].round(12)
    return index


def build_task_inventory(
    candidates: pd.DataFrame,
    computational: pd.DataFrame,
    experimental: pd.DataFrame,
) -> pd.DataFrame:
    frames: list[dict[str, object]] = []
    common = [
        "gold_layer",
        "task_id",
        "usage_mode",
        "model_ready",
        "independent_unit",
        "property_name",
        "source_id",
        "development_split",
        "recommended_loss_weight",
        "source_balanced_sampling_probability",
    ]
    records = pd.concat(
        [computational[common], experimental[common]],
        ignore_index=True,
        copy=False,
    )
    for task_id, group in records.groupby("task_id", sort=True):
        ready = group[group["model_ready"].astype(bool)]
        split_counts = ready["development_split"].value_counts()
        description = TASK_DESCRIPTIONS[task_id]
        frames.append(
            {
                "release_id": RELEASE_ID,
                "task_id": task_id,
                "gold_layer": "+".join(sorted(group["gold_layer"].unique())),
                "purpose": description["purpose"],
                "rows_total": len(group),
                "rows_model_ready": len(ready),
                "rows_primary_train": int(group["usage_mode"].eq("primary_train").sum()),
                "rows_auxiliary_train": int(group["usage_mode"].eq("auxiliary_train").sum()),
                "independent_units_model_ready": ready["independent_unit"].nunique(),
                "property_count": ready["property_name"].nunique(),
                "source_count": ready["source_id"].nunique(),
                "train_rows": int(split_counts.get("train", 0)),
                "validation_rows": int(split_counts.get("validation", 0)),
                "test_rows": int(split_counts.get("test", 0)),
                "recommended_loss_weight_sum": round(
                    float(ready["recommended_loss_weight"].sum()), 9
                ),
                "sampling_probability_sum": round(
                    float(ready["source_balanced_sampling_probability"].sum()), 9
                ),
                "recommended_model": description["recommended_model"],
                "split_rule": "hard_group按80/10/10固定哈希；source_holdout_fold作五折严格来源留出",
                "weight_rule": "潜在上限×质量×角色×独立性；再按任务-来源平衡采样",
                "caveat": description["caveat"],
            }
        )

    candidate_description = TASK_DESCRIPTIONS["候选_结构排序"]
    ready_candidates = candidates[candidates["screening_ready"].astype(bool)]
    split_counts = ready_candidates["development_split"].value_counts()
    frames.append(
        {
            "release_id": RELEASE_ID,
            "task_id": "候选_结构排序",
            "gold_layer": "Gold-V",
            "purpose": candidate_description["purpose"],
            "rows_total": len(candidates),
            "rows_model_ready": len(ready_candidates),
            "rows_primary_train": 0,
            "rows_auxiliary_train": 0,
            "independent_units_model_ready": ready_candidates["candidate_id"].nunique(),
            "property_count": 0,
            "source_count": ready_candidates["source_id"].nunique(),
            "train_rows": int(split_counts.get("train", 0)),
            "validation_rows": int(split_counts.get("validation", 0)),
            "test_rows": int(split_counts.get("test", 0)),
            "recommended_loss_weight_sum": 0.0,
            "sampling_probability_sum": 0.0,
            "recommended_model": candidate_description["recommended_model"],
            "split_rule": "canonical_smiles按80/10/10固定哈希；来源族另设五折",
            "weight_rule": "优先级×准入×官能团匹配，仅用于候选排序",
            "caveat": candidate_description["caveat"],
        }
    )
    return pd.DataFrame(frames).sort_values(["gold_layer", "task_id"]).reset_index(drop=True)


def build_source_reference(
    ledger: pd.DataFrame,
    candidates: pd.DataFrame,
    computational: pd.DataFrame,
    experimental: pd.DataFrame,
) -> pd.DataFrame:
    source_ids = set(candidates["source_id"]) | set(computational["source_id"]) | set(
        experimental["source_id"]
    )
    columns = [
        "source_id",
        "source_family_id",
        "source_title",
        "canonical_identifier",
        "origin_kind",
        "gold_layer",
        "citation_keys",
        "license_status",
        "quality_status",
        "notes",
    ]
    result = ledger[_text(ledger["source_id"]).isin(source_ids)][columns].copy()
    missing = sorted(source_ids - set(_text(result["source_id"])))
    if missing:
        observed = pd.concat(
            [
                computational[
                    [
                        "source_id",
                        "source_family_id",
                        "gold_layer",
                        "citation_keys",
                        "license_status",
                    ]
                ],
                experimental[
                    [
                        "source_id",
                        "source_family_id",
                        "gold_layer",
                        "citation_keys",
                        "license_status",
                    ]
                ],
            ],
            ignore_index=True,
        ).drop_duplicates("source_id")
        family_lookup = ledger.drop_duplicates("source_family_id").set_index(
            "source_family_id"
        )
        extra_rows: list[dict[str, str]] = []
        for source_id in missing:
            match = observed[_text(observed["source_id"]).eq(source_id)]
            if match.empty:
                raise ValueError(f"无法恢复来源元数据: {source_id}")
            metadata = match.iloc[0]
            family_id = str(metadata["source_family_id"])
            family = family_lookup.loc[family_id] if family_id in family_lookup.index else None
            extra_rows.append(
                {
                    "source_id": source_id,
                    "source_family_id": family_id,
                    "source_title": "" if family is None else str(family["source_title"]),
                    "canonical_identifier": ""
                    if family is None
                    else str(family["canonical_identifier"]),
                    "origin_kind": "" if family is None else str(family["origin_kind"]),
                    "gold_layer": str(metadata["gold_layer"]),
                    "citation_keys": str(metadata["citation_keys"]),
                    "license_status": str(metadata["license_status"]),
                    "quality_status": ""
                    if family is None
                    else str(family["quality_status"]),
                    "notes": "同一来源家族的处理后视图；引用与许可继承自来源族，不增加独立来源数。",
                }
            )
        result = pd.concat([result, pd.DataFrame(extra_rows)], ignore_index=True)
    result.insert(0, "release_id", RELEASE_ID)
    return result.sort_values(["source_family_id", "source_id"]).reset_index(drop=True)


def build_field_dictionary(
    candidates: pd.DataFrame,
    computational: pd.DataFrame,
    experimental: pd.DataFrame,
    curves: pd.DataFrame,
    tasks: pd.DataFrame,
    references: pd.DataFrame,
) -> pd.DataFrame:
    tables = {
        "候选结构.csv.gz": candidates,
        "计算观测.csv.gz": computational,
        "实验观测.csv.gz": experimental,
        "曲线索引.csv": curves,
        "任务清单.csv": tasks,
        "来源与引用.csv": references,
    }
    rows: list[dict[str, object]] = []
    for file_name, frame in tables.items():
        for column in frame.columns:
            values = frame[column]
            unique = values.dropna().astype(str).unique()
            allowed = ""
            if 0 < len(unique) <= 12:
                allowed = " | ".join(sorted(unique))
            rows.append(
                {
                    "file_name": file_name,
                    "field_name": column,
                    "dtype": str(values.dtype),
                    "nullable": bool(values.isna().any()),
                    "allowed_values_if_small_enum": allowed,
                    "definition": FIELD_DEFINITIONS.get(
                        column,
                        "继承自冻结Gold参考层或本发布汇总；详见Gold数据集定义与使用说明。",
                    ),
                }
            )
    return pd.DataFrame(rows)


def _validate_release(
    candidates: pd.DataFrame,
    computational: pd.DataFrame,
    experimental: pd.DataFrame,
    curves: pd.DataFrame,
    tasks: pd.DataFrame,
) -> None:
    if candidates["candidate_id"].duplicated().any():
        raise ValueError("候选结构存在重复candidate_id")
    for name, frame in [("Gold-C", computational), ("Gold-E", experimental)]:
        if frame["observation_id"].duplicated().any():
            raise ValueError(f"{name}存在重复observation_id")
        if frame["development_split"].isna().any():
            raise ValueError(f"{name}存在空训练划分")
        group_splits = frame.groupby("leakage_group")["development_split"].nunique()
        if group_splits.gt(1).any():
            raise ValueError(f"{name}同一泄漏组跨越多个开发划分")
        ready = frame["model_ready"].astype(bool)
        if frame.loc[ready, "recommended_loss_weight"].le(0).any():
            raise ValueError(f"{name}模型就绪记录存在非正损失权重")
        if frame.loc[~ready, "recommended_loss_weight"].ne(0).any():
            raise ValueError(f"{name}非模型就绪记录存在非零损失权重")
        probabilities = (
            frame.loc[ready]
            .groupby("task_id")["source_balanced_sampling_probability"]
            .sum()
        )
        if not np.allclose(probabilities.to_numpy(), 1.0, rtol=0, atol=1e-8):
            raise ValueError(f"{name}任务采样概率未归一")
    if len(curves) != experimental["curve_id"].replace("", np.nan).nunique():
        raise ValueError("曲线索引数量与实验观测不一致")
    combined_groups = pd.concat(
        [
            computational[["leakage_group", "development_split"]],
            experimental[["leakage_group", "development_split"]],
        ],
        ignore_index=True,
    )
    if combined_groups.groupby("leakage_group")["development_split"].nunique().gt(1).any():
        raise ValueError("跨Gold层的同一硬分组进入不同开发划分")
    if set(tasks["task_id"]) != set(TASK_DESCRIPTIONS):
        raise ValueError("任务清单缺少已定义任务")


def generate() -> dict[str, object]:
    for path in INPUT_PATHS.values():
        if not path.is_file():
            raise FileNotFoundError(path)
    ledger = pd.read_csv(INPUT_PATHS["来源总账"], low_memory=False)
    maps = _source_maps(ledger)

    candidates = build_candidates(maps)
    computational_prepared = prepare_computational(maps)
    experimental_prepared = prepare_experimental(maps)
    _assign_hard_groups(computational_prepared, experimental_prepared)
    computational = finalize_computational(computational_prepared)
    experimental = finalize_experimental(experimental_prepared)
    curves = build_curve_index(experimental)
    tasks = build_task_inventory(candidates, computational, experimental)
    references = build_source_reference(
        ledger,
        candidates,
        computational,
        experimental,
    )
    dictionary = build_field_dictionary(
        candidates,
        computational,
        experimental,
        curves,
        tasks,
        references,
    )

    _validate_release(candidates, computational, experimental, curves, tasks)

    _write_csv(candidates, OUTPUT_PATHS["候选结构"], gzip_output=True)
    _write_csv(computational, OUTPUT_PATHS["计算观测"], gzip_output=True)
    _write_csv(experimental, OUTPUT_PATHS["实验观测"], gzip_output=True)
    _write_csv(curves, OUTPUT_PATHS["曲线索引"], gzip_output=False)
    _write_csv(tasks, OUTPUT_PATHS["任务清单"], gzip_output=False)
    _write_csv(references, OUTPUT_PATHS["来源与引用"], gzip_output=False)
    _write_csv(dictionary, OUTPUT_PATHS["字段字典"], gzip_output=False)
    oversized = [
        path.name
        for name, path in OUTPUT_PATHS.items()
        if name != "发布清单" and path.stat().st_size >= 95 * 1024 * 1024
    ]
    if oversized:
        raise ValueError(f"GitHub发布文件达到95 MiB门禁: {oversized}")

    output_hashes = {
        name: {
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
        }
        for name, path in OUTPUT_PATHS.items()
        if name != "发布清单"
    }
    manifest: dict[str, object] = {
        "release_id": RELEASE_ID,
        "schema_version": SCHEMA_VERSION,
        "split_seed": SPLIT_SEED,
        "source_reference_layers_are_immutable": True,
        "input_files": {
            name: {
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for name, path in INPUT_PATHS.items()
        },
        "output_files": output_hashes,
        "counts": {
            "candidate_rows": len(candidates),
            "screening_ready_candidates": int(candidates["screening_ready"].sum()),
            "linear_tpu_building_blocks": int(
                candidates["linear_tpu_building_block_ready"].sum()
            ),
            "computational_rows": len(computational),
            "computational_model_ready_rows": int(computational["model_ready"].sum()),
            "computational_hard_groups": computational["leakage_group"].nunique(),
            "experimental_rows": len(experimental),
            "experimental_model_ready_rows": int(experimental["model_ready"].sum()),
            "experimental_hard_groups": experimental["leakage_group"].nunique(),
            "curve_count": len(curves),
            "task_count": len(tasks),
            "active_source_count": len(references),
            "active_source_family_count": references["source_family_id"].nunique(),
        },
        "weight_policy": {
            "loss": "potential_weight_ceiling * quality_factor * role_factor * independence_weight",
            "curve_and_repeated_unit_rule": "rows in one task-property-independent_unit share one total budget",
            "sampling": "equal total probability per active source within each task",
            "conditional_reference": "eligible only as explicitly labeled auxiliary_train when units and value semantics are usable",
            "context": "input descriptors remain present with zero loss weight",
        },
    }
    _write_json(manifest, OUTPUT_PATHS["发布清单"])
    return manifest


def verify_existing() -> dict[str, object]:
    manifest_path = OUTPUT_PATHS["发布清单"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mismatches: list[str] = []
    for section in ("input_files", "output_files"):
        for name, entry in manifest[section].items():
            path = ROOT / entry["path"]
            if not path.is_file():
                mismatches.append(f"{name}:missing")
            elif _sha256(path) != entry["sha256"]:
                mismatches.append(f"{name}:sha256")
    if mismatches:
        raise ValueError("发布校验失败: " + ", ".join(mismatches))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--检查",
        action="store_true",
        help="只校验现有发布文件与发布清单，不重新生成",
    )
    args = parser.parse_args()
    manifest = verify_existing() if args.检查 else generate()
    print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
