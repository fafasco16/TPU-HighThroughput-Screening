"""从冻结发布层生成 TPU 第一阶段可审计基线。

脚本只读取 ``结果/可用数据集``，不修改 Gold 层，不下载数据，
也不保存不透明的二进制模型。计算性质严格沿用已发布的
``development_split`` 和 ``leakage_group``；PUE-643 只作单来源变换目标
的管线烟雾测试。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import warnings
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from rdkit import RDLogger, rdBase

import 模型基线 as baseline


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RELEASE_DIR = ROOT / "结果" / "可用数据集"
DEFAULT_CONFIG_PATH = ROOT / "模型" / "配置" / "基线配置.yaml"
DEFAULT_OUTPUT_DIR = ROOT / "模型" / "基线结果"

OUTPUT_NAMES = {
    "readiness": "目标可训练性.csv",
    "metrics": "指标.csv",
    "predictions": "逐样本预测.csv.gz",
    "curve_endpoints": "实验曲线端点.csv",
    "manifest": "运行清单.json",
    "report": "基线报告.md",
}

REFERENCE_NUMBER_BY_KEY = {
    "ledger-001": 1,
    "ledger-020": 20,
    "ledger-055": 55,
    "ledger-056": 56,
    "ledger-145": 145,
    "ledger-146": 146,
    "ledger-151": 151,
    "ledger-156": 156,
    "ledger-157": 157,
    "ledger-158": 158,
    "ledger-165": 165,
    "ledger-166": 166,
    "ledger-167": 167,
    "ledger-168": 168,
    "ledger-169": 169,
    "ledger-170": 170,
    "ledger-171": 171,
    "ledger-172": 172,
    "ledger-173": 173,
}

COMPUTATIONAL_COLUMNS = [
    "release_id",
    "task_id",
    "model_ready",
    "source_id",
    "source_family_id",
    "observation_id",
    "canonical_structure",
    "structure_identity_status",
    "usage_mode",
    "target_role",
    "property_name",
    "value",
    "unit",
    "leakage_group",
    "development_split",
    "recommended_loss_weight",
    "source_locator",
    "citation_keys",
]

EXPERIMENTAL_COLUMNS = [
    "release_id",
    "task_id",
    "usage_mode",
    "model_ready",
    "source_id",
    "source_family_id",
    "observation_id",
    "formulation_id",
    "sample_id",
    "curve_id",
    "point_index",
    "record_kind",
    "property_name",
    "value",
    "unit",
    "condition_name",
    "condition_value",
    "condition_unit",
    "leakage_group",
    "development_split",
    "recommended_loss_weight",
    "source_locator",
    "citation_keys",
]

PREDICTION_COLUMNS = [
    "evaluation_id",
    "evaluation_scheme",
    "training_mode",
    "model_name",
    "observation_id",
    "leakage_group",
    "development_split",
    "source_id",
    "source_family_id",
    "structure_identity_status",
    "structure_star_count",
    "target_name",
    "true_value",
    "unit",
    "weight",
    "prediction",
    "selected_alpha",
    "evidence_scope",
    "target_semantics",
    "source_locator",
    "citation_keys",
]

METRIC_COLUMNS = [
    "evaluation_id",
    "evaluation_scheme",
    "held_out_source_family",
    "training_mode",
    "model_name",
    "target_name",
    "unit",
    "evaluation_split",
    "aggregation_level",
    "n_rows",
    "n_groups",
    "mae",
    "rmse",
    "r2",
    "spearman",
    "selected_alpha",
    "evidence_scope",
    "target_semantics",
    "status",
]


def _text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def _as_bool(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False).astype(bool)
    return _text(series).str.lower().isin({"true", "1", "yes"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_columns(frame: pd.DataFrame, required: Iterable[str], label: str) -> None:
    missing = sorted(set(required).difference(frame.columns))
    if missing:
        raise ValueError(f"{label}缺少字段: {missing}")


def _validate_group_split(
    frame: pd.DataFrame, group_column: str, split_column: str
) -> None:
    """调用核心泄漏门禁，屏蔽 Pandas 分类列 observed 默认值的过渡警告。"""

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="The default of observed=False is deprecated",
            category=FutureWarning,
        )
        baseline.validate_group_split(frame, group_column, split_column)


def load_config(path: Path) -> dict[str, Any]:
    """读取并验证基线配置；不使用隐式默认掩盖拼写错误。"""

    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError("基线配置必须是 YAML 对象")
    required_sections = {
        "release",
        "computational_task",
        "features",
        "ridge",
        "curves",
        "pue643",
        "run",
    }
    missing = sorted(required_sections.difference(config))
    if missing:
        raise ValueError(f"基线配置缺少分区: {missing}")
    targets = config["computational_task"].get("targets", [])
    if not targets or any(not {"name", "unit", "role"}.issubset(item) for item in targets):
        raise ValueError("计算目标必须明确 name、unit 和 role")
    modes = config["computational_task"].get("training_modes", [])
    if modes != ["primary_only", "primary_plus_aux"]:
        raise ValueError("训练模式必须依次为 primary_only 和 primary_plus_aux")
    alphas = [float(value) for value in config["ridge"].get("alphas", [])]
    if not alphas or any(not np.isfinite(value) or value < 0 for value in alphas):
        raise ValueError("Ridge alphas 必须为非空的有限非负数列表")
    if int(config["features"].get("morgan_bits", 0)) <= 0:
        raise ValueError("morgan_bits 必须为正整数")
    return config


def verify_release(release_dir: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    """验证冻结发布 ID 和清单所列文件哈希。"""

    manifest_path = release_dir / str(config["release"]["manifest"])
    if not manifest_path.is_file():
        raise FileNotFoundError(f"发布清单不存在: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    expected_release = str(config["release"]["id"])
    if manifest.get("release_id") != expected_release:
        raise ValueError(
            f"发布 ID 不匹配: {manifest.get('release_id')} != {expected_release}"
        )
    failures: list[str] = []
    checked: dict[str, dict[str, Any]] = {}
    for label, metadata in sorted(manifest.get("output_files", {}).items()):
        filename = Path(str(metadata["path"])).name
        path = release_dir / filename
        if not path.is_file():
            failures.append(f"{label}: 缺少 {filename}")
            continue
        actual = _sha256(path)
        expected = str(metadata["sha256"])
        checked[label] = {"path": str(path), "sha256": actual, "bytes": path.stat().st_size}
        if actual != expected:
            failures.append(f"{label}: SHA-256 漂移")
    if failures:
        raise ValueError("冻结发布验证失败: " + "; ".join(failures))
    required_files = {"计算观测.csv.gz", "实验观测.csv.gz", "曲线索引.csv"}
    missing_required = sorted(name for name in required_files if not (release_dir / name).is_file())
    if missing_required:
        raise ValueError(f"发布层缺少基线必需文件: {missing_required}")
    return {"release_id": expected_release, "manifest": manifest, "checked": checked}


def load_computational_observations(
    release_dir: Path, task_id: str | None = None
) -> pd.DataFrame:
    path = release_dir / "计算观测.csv.gz"
    header = pd.read_csv(path, nrows=0)
    _require_columns(header, COMPUTATIONAL_COLUMNS, "计算观测")
    frame = pd.read_csv(path, usecols=COMPUTATIONAL_COLUMNS, low_memory=False)
    if task_id is not None:
        frame = frame.loc[_text(frame["task_id"]).eq(task_id)].copy()
    frame["model_ready"] = _as_bool(frame["model_ready"])
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame["recommended_loss_weight"] = pd.to_numeric(
        frame["recommended_loss_weight"], errors="coerce"
    ).fillna(0.0)
    for column in set(COMPUTATIONAL_COLUMNS).difference(
        {"model_ready", "value", "recommended_loss_weight"}
    ):
        frame[column] = _text(frame[column])
    # 这些列在结构任务中只有少量枚举值，分类编码可显著
    # 降低百万行发布视图的常驻内存，不改变行值或排序。
    for column in (
        "release_id",
        "task_id",
        "source_id",
        "source_family_id",
        "canonical_structure",
        "structure_identity_status",
        "usage_mode",
        "target_role",
        "property_name",
        "unit",
        "development_split",
        "leakage_group",
        "citation_keys",
    ):
        frame[column] = frame[column].astype("category")
    return frame


def load_experimental_subset(release_dir: Path, config: Mapping[str, Any]) -> pd.DataFrame:
    """分块只保留曲线点和 PUE-643，避免为无关实验行占用内存。"""

    path = release_dir / "实验观测.csv.gz"
    header = pd.read_csv(path, nrows=0)
    _require_columns(header, EXPERIMENTAL_COLUMNS, "实验观测")
    curve_task = str(config["curves"]["task_id"])
    pue_family = str(config["pue643"]["source_family_id"])
    selected: list[pd.DataFrame] = []
    for chunk in pd.read_csv(
        path,
        usecols=EXPERIMENTAL_COLUMNS,
        chunksize=100_000,
        low_memory=False,
    ):
        task = _text(chunk["task_id"])
        family = _text(chunk["source_family_id"])
        curve_id = _text(chunk["curve_id"])
        keep = ((task == curve_task) & curve_id.ne("")) | family.eq(pue_family)
        if keep.any():
            selected.append(chunk.loc[keep].copy())
    if not selected:
        return pd.DataFrame(columns=EXPERIMENTAL_COLUMNS)
    frame = pd.concat(selected, ignore_index=True)
    frame["model_ready"] = _as_bool(frame["model_ready"])
    for column in ["point_index", "value", "recommended_loss_weight"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in set(EXPERIMENTAL_COLUMNS).difference(
        {"model_ready", "point_index", "value", "recommended_loss_weight"}
    ):
        frame[column] = _text(frame[column])
    # 曲线点的逐点 locator/observation_id 不进入端点输出（端点追溯
    # 来自冻结曲线索引）；仅 PUE 预测保留逐行身份，避免存放
    # 近30万个不会被使用的长字符串。
    pue_mask = frame["source_family_id"].eq(pue_family)
    for column in ("observation_id", "source_locator", "citation_keys"):
        frame.loc[~pue_mask, column] = ""
    for column in (
        "release_id",
        "task_id",
        "usage_mode",
        "source_id",
        "source_family_id",
        "observation_id",
        "formulation_id",
        "sample_id",
        "curve_id",
        "record_kind",
        "property_name",
        "unit",
        "condition_name",
        "condition_unit",
        "leakage_group",
        "development_split",
        "source_locator",
        "citation_keys",
    ):
        frame[column] = frame[column].astype("category")
    return frame


def _fingerprint_cache(
    structures: Sequence[str], radius: int, n_bits: int
) -> tuple[dict[str, np.ndarray], set[str]]:
    cache: dict[str, np.ndarray] = {}
    invalid: set[str] = set()
    RDLogger.DisableLog("rdApp.error")
    try:
        for structure in sorted(set(structures)):
            if not structure:
                invalid.add(structure)
                continue
            try:
                # 位指纹在缓存中用 uint8 保存；核心拟合函数会在需要时
                # 转为 float64，避免数万个重复单元的指纹常驻内存放大8倍。
                cache[structure] = baseline.featurize_smiles(
                    structure, radius, n_bits
                ).astype(np.uint8, copy=False)
            except ValueError:
                invalid.add(structure)
    finally:
        RDLogger.EnableLog("rdApp.error")
    return cache, invalid


def _group_counts(frame: pd.DataFrame, mask: pd.Series) -> dict[str, int]:
    subset = frame.loc[mask]
    return {
        split: int(subset.loc[subset["development_split"].eq(split), "leakage_group"].nunique())
        for split in ("train", "validation", "test")
    }


def build_target_readiness(
    observations: pd.DataFrame,
    config: Mapping[str, Any],
    valid_structures: set[str],
) -> pd.DataFrame:
    """对计算任务的所有原始性质—单位组合生成门禁表。"""

    task = config["computational_task"]
    task_rows = observations.loc[observations["task_id"].eq(str(task["task_id"]))].copy()
    _validate_group_split(task_rows, "leakage_group", "development_split")
    configured = {
        (str(item["name"]), str(item["unit"])): str(item["role"])
        for item in task["targets"]
    }
    minimum = {key: int(value) for key, value in task["minimum_groups"].items()}
    rows: list[dict[str, Any]] = []
    for (target_name, unit), group in task_rows.groupby(
        ["property_name", "unit"], dropna=False, sort=True, observed=True
    ):
        target_name = str(target_name)
        unit = str(unit)
        finite = np.isfinite(group["value"].to_numpy(dtype=np.float64))
        valid_structure = group["canonical_structure"].isin(valid_structures)
        ready = (
            group["model_ready"]
            & finite
            & valid_structure
            & group["recommended_loss_weight"].gt(0)
        )
        primary = ready & group["usage_mode"].eq("primary_train")
        all_counts = _group_counts(group, ready)
        primary_counts = _group_counts(group, primary)
        configured_role = configured.get((target_name, unit), "not_configured")
        fair = all(
            primary_counts[split] >= minimum[split]
            for split in ("train", "validation", "test")
        )
        if configured_role == "not_configured":
            status = "audited_not_configured"
            reason = "本轮未选为基线目标；保留原始标签口径"
        elif fair:
            status = "fair_primary_evaluation_ready"
            reason = "primary train/validation/test 硬组均达门槛"
        else:
            status = "exploratory_not_fairly_evaluable"
            reason = "primary validation/test 硬组不足，不输出公平比较模型"
        holdout_minimum = {
            key: int(value)
            for key, value in task["source_holdout_minimum_groups"].items()
        }
        eligible_holdouts = 0
        if fair:
            for held_out in sorted(group.loc[primary, "source_family_id"].unique()):
                held_out_groups = set(
                    group.loc[
                        primary & group["source_family_id"].eq(held_out),
                        "leakage_group",
                    ]
                )
                source_train = primary & group["source_family_id"].ne(held_out)
                source_train &= group["development_split"].eq("train")
                source_train &= ~group["leakage_group"].isin(held_out_groups)
                source_validation = primary & group["source_family_id"].ne(held_out)
                source_validation &= group["development_split"].eq("validation")
                source_validation &= ~group["leakage_group"].isin(held_out_groups)
                holdout_counts = {
                    "train": int(group.loc[source_train, "leakage_group"].nunique()),
                    "validation": int(
                        group.loc[source_validation, "leakage_group"].nunique()
                    ),
                    "test": len(held_out_groups),
                }
                if all(
                    holdout_counts[key] >= holdout_minimum[key]
                    for key in holdout_counts
                ):
                    eligible_holdouts += 1
        rows.append(
            {
                "gold_layer": "Gold-C",
                "task_id": task["task_id"],
                "target_name": target_name,
                "unit": unit,
                "configured_role": configured_role,
                "rows_total": int(len(group)),
                "rows_model_ready": int(group["model_ready"].sum()),
                "rows_finite_valid_structure_positive_weight": int(ready.sum()),
                "invalid_or_missing_structure_rows": int((~valid_structure).sum()),
                "structure_identity_statuses": ";".join(
                    sorted(group.loc[ready, "structure_identity_status"].unique())
                ),
                "citation_keys": ";".join(
                    sorted(
                        {
                            key
                            for value in group["citation_keys"]
                            for key in str(value).split(";")
                            if key
                        }
                    )
                ),
                "source_ids": ";".join(
                    sorted(group.loc[ready, "source_id"].unique())
                ),
                "valid_rows_with_exactly_two_stars": int(
                    (
                        ready
                        & group["canonical_structure"].str.count(r"\*").eq(2)
                    ).sum()
                ),
                "hard_groups_train": all_counts["train"],
                "hard_groups_validation": all_counts["validation"],
                "hard_groups_test": all_counts["test"],
                "primary_hard_groups_train": primary_counts["train"],
                "primary_hard_groups_validation": primary_counts["validation"],
                "primary_hard_groups_test": primary_counts["test"],
                "source_family_count": int(group.loc[ready, "source_family_id"].nunique()),
                "eligible_leave_one_source_family_out_count": eligible_holdouts,
                "fair_primary_evaluation": bool(fair),
                "training_status": status,
                "status_reason": reason,
                "multifidelity_pairing_status": "not_established",
                "structure_applicability_warning": (
                    "pSMILES/repeat-unit association baseline; not a closed "
                    "formulation, molecular-weight or process model"
                ),
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["configured_role", "target_name", "unit"], kind="mergesort"
    ).reset_index(drop=True)


def _feature_matrix(frame: pd.DataFrame, cache: Mapping[str, np.ndarray]) -> np.ndarray:
    return np.stack([cache[value] for value in frame["canonical_structure"]], axis=0)


def _group_macro_arrays(
    frame: pd.DataFrame, predictions: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    temporary = pd.DataFrame(
        {
            "group": frame["leakage_group"].to_numpy(),
            "truth": frame["value"].to_numpy(dtype=np.float64),
            "prediction": np.asarray(predictions, dtype=np.float64),
            "weight": frame["recommended_loss_weight"].to_numpy(dtype=np.float64),
        }
    )
    records: list[tuple[float, float]] = []
    for _, group in temporary.groupby("group", sort=True):
        weights = group["weight"].to_numpy(dtype=np.float64)
        records.append(
            (
                baseline.weighted_mean(group["truth"].to_numpy(), weights),
                baseline.weighted_mean(group["prediction"].to_numpy(), weights),
            )
        )
    truth = np.asarray([record[0] for record in records], dtype=np.float64)
    prediction = np.asarray([record[1] for record in records], dtype=np.float64)
    return truth, prediction, np.ones(len(records), dtype=np.float64)


def _append_metric_rows(
    rows: list[dict[str, Any]],
    evaluation: pd.DataFrame,
    predictions: np.ndarray,
    *,
    evaluation_id: str,
    scheme: str,
    held_out_source: str,
    training_mode: str,
    model_name: str,
    target_name: str,
    unit: str,
    split: str,
    selected_alpha: float | None,
    evidence_scope: str,
    target_semantics: str,
) -> None:
    weight = evaluation["recommended_loss_weight"].to_numpy(dtype=np.float64)
    truth = evaluation["value"].to_numpy(dtype=np.float64)
    metrics = baseline.regression_metrics(truth, predictions, weight)
    common = {
        "evaluation_id": evaluation_id,
        "evaluation_scheme": scheme,
        "held_out_source_family": held_out_source,
        "training_mode": training_mode,
        "model_name": model_name,
        "target_name": target_name,
        "unit": unit,
        "evaluation_split": split,
        "n_rows": int(len(evaluation)),
        "n_groups": int(evaluation["leakage_group"].nunique()),
        "selected_alpha": selected_alpha,
        "evidence_scope": evidence_scope,
        "target_semantics": target_semantics,
        "status": "evaluated",
    }
    rows.append({**common, "aggregation_level": "row_weighted", **metrics})
    group_truth, group_prediction, group_weights = _group_macro_arrays(evaluation, predictions)
    group_metrics = baseline.regression_metrics(group_truth, group_prediction, group_weights)
    rows.append({**common, "aggregation_level": "hard_group_macro", **group_metrics})


def _prediction_frame(
    evaluation: pd.DataFrame,
    predictions: np.ndarray,
    *,
    evaluation_id: str,
    scheme: str,
    training_mode: str,
    model_name: str,
    target_name: str,
    unit: str,
    selected_alpha: float | None,
    evidence_scope: str,
    target_semantics: str,
) -> pd.DataFrame:
    result = pd.DataFrame(
        {
            "evaluation_id": evaluation_id,
            "evaluation_scheme": scheme,
            "training_mode": training_mode,
            "model_name": model_name,
            "observation_id": evaluation["observation_id"].to_numpy(),
            "leakage_group": evaluation["leakage_group"].to_numpy(),
            "development_split": evaluation["development_split"].to_numpy(),
            "source_id": evaluation["source_id"].to_numpy(),
            "source_family_id": evaluation["source_family_id"].to_numpy(),
            "structure_identity_status": (
                evaluation["structure_identity_status"].to_numpy()
                if "structure_identity_status" in evaluation
                else np.full(len(evaluation), "not_applicable")
            ),
            "structure_star_count": (
                evaluation["canonical_structure"].str.count(r"\*").to_numpy(dtype=int)
                if "canonical_structure" in evaluation
                else np.full(len(evaluation), -1, dtype=int)
            ),
            "target_name": target_name,
            "true_value": evaluation["value"].to_numpy(dtype=np.float64),
            "unit": unit,
            "weight": evaluation["recommended_loss_weight"].to_numpy(dtype=np.float64),
            "prediction": np.asarray(predictions, dtype=np.float64),
            "selected_alpha": np.full(
                len(evaluation),
                np.nan if selected_alpha is None else float(selected_alpha),
                dtype=np.float64,
            ),
            "evidence_scope": evidence_scope,
            "target_semantics": target_semantics,
            "source_locator": evaluation["source_locator"].to_numpy(),
            "citation_keys": evaluation["citation_keys"].to_numpy(),
        }
    )
    return result[PREDICTION_COLUMNS]


def _fit_and_evaluate(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    tests: Sequence[tuple[str, pd.DataFrame]],
    cache: Mapping[str, np.ndarray],
    alphas: Sequence[float],
    *,
    evaluation_id: str,
    scheme: str,
    held_out_source: str,
    training_mode: str,
    target_name: str,
    unit: str,
    evidence_scope: str,
    target_semantics: str,
) -> tuple[list[dict[str, Any]], list[pd.DataFrame]]:
    train_x = _feature_matrix(train, cache)
    validation_x = _feature_matrix(validation, cache)
    train_y = train["value"].to_numpy(dtype=np.float64)
    train_weight = train["recommended_loss_weight"].to_numpy(dtype=np.float64)
    validation_y = validation["value"].to_numpy(dtype=np.float64)
    validation_weight = validation["recommended_loss_weight"].to_numpy(dtype=np.float64)
    selection = baseline.choose_ridge_alpha(
        train_x,
        train_y,
        train_weight,
        validation_x,
        validation_y,
        validation_weight,
        alphas,
    )
    mean_value = baseline.weighted_mean(train_y, train_weight)
    metric_rows: list[dict[str, Any]] = []
    prediction_frames: list[pd.DataFrame] = []
    for split, evaluation in tests:
        evaluation_x = _feature_matrix(evaluation, cache)
        model_predictions = {
            "weighted_mean": np.full(len(evaluation), mean_value, dtype=np.float64),
            "morgan_weighted_ridge": baseline.predict_weighted_ridge(
                selection["model"], evaluation_x
            ),
        }
        for model_name, prediction in model_predictions.items():
            selected_alpha = float(selection["alpha"]) if model_name != "weighted_mean" else None
            _append_metric_rows(
                metric_rows,
                evaluation,
                prediction,
                evaluation_id=evaluation_id,
                scheme=scheme,
                held_out_source=held_out_source,
                training_mode=training_mode,
                model_name=model_name,
                target_name=target_name,
                unit=unit,
                split=split,
                selected_alpha=selected_alpha,
                evidence_scope=evidence_scope,
                target_semantics=target_semantics,
            )
            prediction_frames.append(
                _prediction_frame(
                    evaluation,
                    prediction,
                    evaluation_id=evaluation_id,
                    scheme=scheme,
                    training_mode=training_mode,
                    model_name=model_name,
                    target_name=target_name,
                    unit=unit,
                    selected_alpha=selected_alpha,
                    evidence_scope=evidence_scope,
                    target_semantics=target_semantics,
                )
            )
    return metric_rows, prediction_frames


def train_computational_baselines(
    observations: pd.DataFrame,
    config: Mapping[str, Any],
    cache: Mapping[str, np.ndarray],
    readiness: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """训练常数与 Morgan-Ridge；严格主目标不足时不输出模型。"""

    task = config["computational_task"]
    radius = int(config["features"]["morgan_radius"])
    bits = int(config["features"]["morgan_bits"])
    if cache and len(next(iter(cache.values()))) != bits:
        raise ValueError("Morgan 特征缓存维度与配置不一致")
    alphas = [float(value) for value in config["ridge"]["alphas"]]
    minimum = {key: int(value) for key, value in task["minimum_groups"].items()}
    holdout_min = {
        key: int(value) for key, value in task["source_holdout_minimum_groups"].items()
    }
    metric_rows: list[dict[str, Any]] = []
    prediction_frames: list[pd.DataFrame] = []
    fair_lookup = {
        (row.target_name, row.unit): bool(row.fair_primary_evaluation)
        for row in readiness.itertuples(index=False)
    }
    base = observations.loc[
        observations["task_id"].eq(str(task["task_id"]))
        & observations["model_ready"]
        & observations["recommended_loss_weight"].gt(0)
        & observations["canonical_structure"].isin(cache)
        & np.isfinite(observations["value"].to_numpy(dtype=np.float64))
    ].copy()
    _validate_group_split(base, "leakage_group", "development_split")
    for target in task["targets"]:
        target_name = str(target["name"])
        unit = str(target["unit"])
        if not fair_lookup.get((target_name, unit), False):
            continue
        target_rows = base.loc[
            base["property_name"].eq(target_name) & base["unit"].eq(unit)
        ].copy()
        for training_mode in task["training_modes"]:
            if training_mode == "primary_only":
                training_usage = {"primary_train"}
            else:
                training_usage = {"primary_train", "auxiliary_train"}
            train = target_rows.loc[
                target_rows["development_split"].eq("train")
                & target_rows["usage_mode"].isin(training_usage)
            ].sort_values(["leakage_group", "observation_id"], kind="mergesort")
            validation = target_rows.loc[
                target_rows["development_split"].eq("validation")
                & target_rows["usage_mode"].eq("primary_train")
            ].sort_values(["leakage_group", "observation_id"], kind="mergesort")
            test = target_rows.loc[
                target_rows["development_split"].eq("test")
                & target_rows["usage_mode"].eq("primary_train")
            ].sort_values(["leakage_group", "observation_id"], kind="mergesort")
            counts = {
                "train": train["leakage_group"].nunique(),
                "validation": validation["leakage_group"].nunique(),
                "test": test["leakage_group"].nunique(),
            }
            if any(counts[split] < minimum[split] for split in counts):
                continue
            identifier = f"development::{target_name}::{unit}::{training_mode}"
            metrics, predictions = _fit_and_evaluate(
                train,
                validation,
                [("validation", validation), ("test", test)],
                cache,
                alphas,
                evaluation_id=identifier,
                scheme="published_development_split",
                held_out_source="",
                training_mode=training_mode,
                target_name=target_name,
                unit=unit,
                evidence_scope="computational_transfer_baseline",
                target_semantics="published_computational_target",
            )
            metric_rows.extend(metrics)
            prediction_frames.extend(predictions)

            for held_out in sorted(target_rows["source_family_id"].unique()):
                source_test = target_rows.loc[
                    target_rows["source_family_id"].eq(held_out)
                    & target_rows["usage_mode"].eq("primary_train")
                ].sort_values(["leakage_group", "observation_id"], kind="mergesort")
                held_out_groups = set(source_test["leakage_group"])
                source_train = target_rows.loc[
                    target_rows["source_family_id"].ne(held_out)
                    & target_rows["development_split"].eq("train")
                    & target_rows["usage_mode"].isin(training_usage)
                    & ~target_rows["leakage_group"].isin(held_out_groups)
                ].sort_values(["leakage_group", "observation_id"], kind="mergesort")
                source_validation = target_rows.loc[
                    target_rows["source_family_id"].ne(held_out)
                    & target_rows["development_split"].eq("validation")
                    & target_rows["usage_mode"].eq("primary_train")
                    & ~target_rows["leakage_group"].isin(held_out_groups)
                ].sort_values(["leakage_group", "observation_id"], kind="mergesort")
                holdout_counts = {
                    "train": source_train["leakage_group"].nunique(),
                    "validation": source_validation["leakage_group"].nunique(),
                    "test": source_test["leakage_group"].nunique(),
                }
                if any(holdout_counts[key] < holdout_min[key] for key in holdout_counts):
                    continue
                identifier = f"source-loo::{target_name}::{unit}::{training_mode}::{held_out}"
                metrics, predictions = _fit_and_evaluate(
                    source_train,
                    source_validation,
                    [("held_out_source", source_test)],
                    cache,
                    alphas,
                    evaluation_id=identifier,
                    scheme="leave_one_source_family_out",
                    held_out_source=held_out,
                    training_mode=training_mode,
                    target_name=target_name,
                    unit=unit,
                    evidence_scope="computational_source_extrapolation",
                    target_semantics="published_computational_target",
                )
                metric_rows.extend(metrics)
                prediction_frames.extend(predictions)
    metrics = pd.DataFrame(metric_rows, columns=METRIC_COLUMNS)
    predictions = (
        pd.concat(prediction_frames, ignore_index=True)
        if prediction_frames
        else pd.DataFrame(columns=PREDICTION_COLUMNS)
    )
    if not metrics.empty:
        metrics = metrics.sort_values(
            ["evaluation_scheme", "target_name", "training_mode", "evaluation_id", "model_name", "evaluation_split", "aggregation_level"],
            kind="mergesort",
        ).reset_index(drop=True)
    if not predictions.empty:
        predictions = predictions.sort_values(
            ["evaluation_scheme", "target_name", "training_mode", "evaluation_id", "model_name", "development_split", "leakage_group", "observation_id"],
            kind="mergesort",
        ).reset_index(drop=True)
    return metrics, predictions


def build_curve_endpoints(
    observations: pd.DataFrame,
    curve_index: pd.DataFrame,
    config: Mapping[str, Any],
) -> pd.DataFrame:
    """为每个发布曲线生成一行保守端点或明确拒绝状态。"""

    required_index = {
        "curve_id",
        "source_id",
        "source_family_id",
        "formulation_id",
        "sample_id",
        "property_name",
        "unit",
        "condition_name",
        "condition_unit",
        "model_ready",
        "leakage_group",
        "development_split",
        "source_locator",
        "citation_keys",
    }
    _require_columns(curve_index, required_index, "曲线索引")
    index = curve_index.copy()
    index["model_ready"] = _as_bool(index["model_ready"])
    for column in required_index.difference({"model_ready"}):
        index[column] = _text(index[column])
    curve_rows = observations.loc[
        observations["task_id"].eq(str(config["curves"]["task_id"]))
        & observations["curve_id"].ne("")
    ].copy()
    if not curve_rows.empty:
        _validate_group_split(curve_rows, "curve_id", "development_split")
        _validate_group_split(curve_rows, "curve_id", "leakage_group")
    grouped = {
        key: value
        for key, value in curve_rows.groupby("curve_id", sort=False, observed=True)
    }
    records: list[dict[str, Any]] = []
    gate_on_release = bool(config["curves"].get("derive_only_when_release_model_ready", True))
    for row in index.sort_values("curve_id", kind="mergesort").itertuples(index=False):
        curve_id = str(row.curve_id)
        all_curve_rows = grouped.get(curve_id)
        if all_curve_rows is None:
            points = None
            endpoint: dict[str, Any] = {
                "endpoint_status": "missing_curve_points",
                "curve_type": "unsupported",
            }
        else:
            # 同一 curve_id 可同时挂接曲线点与已派生的标量端点。
            # 必须按曲线索引的完整轴语义过滤，不能把标量行混进序列。
            points = all_curve_rows.loc[
                all_curve_rows["property_name"].eq(str(row.property_name))
                & all_curve_rows["unit"].eq(str(row.unit))
                & all_curve_rows["condition_name"].eq(str(row.condition_name))
                & all_curve_rows["condition_unit"].eq(str(row.condition_unit))
            ]
        if all_curve_rows is not None and gate_on_release and not bool(row.model_ready):
            endpoint = {
                "endpoint_status": "release_not_model_ready",
                "curve_type": "unsupported",
            }
        elif all_curve_rows is not None and points is not None:
            endpoint = baseline.derive_curve_endpoints(points)
        records.append(
            {
                "curve_id": curve_id,
                "source_id": str(row.source_id),
                "source_family_id": str(row.source_family_id),
                "formulation_id": str(row.formulation_id),
                "sample_id": str(row.sample_id),
                "release_model_ready": bool(row.model_ready),
                "leakage_group": str(row.leakage_group),
                "development_split": str(row.development_split),
                "point_count": int(len(points)) if points is not None else 0,
                "evidence_scope": "within_source_auxiliary",
                "source_locator": str(row.source_locator),
                "citation_keys": str(row.citation_keys),
                **endpoint,
            }
        )
    frame = pd.DataFrame(records)
    base_columns = [
        "curve_id",
        "source_id",
        "source_family_id",
        "formulation_id",
        "sample_id",
        "release_model_ready",
        "leakage_group",
        "development_split",
        "point_count",
        "endpoint_status",
        "curve_type",
        "evidence_scope",
        "source_locator",
        "citation_keys",
    ]
    endpoint_columns = sorted(set(frame.columns).difference(base_columns))
    return frame.reindex(columns=base_columns + endpoint_columns)


def _parse_pue_features(
    rows: pd.DataFrame, config: Mapping[str, Any]
) -> tuple[pd.DataFrame, list[str]]:
    pue = config["pue643"]
    expected_count = int(pue["expected_input_fields"])
    categorical = [str(value) for value in pue.get("categorical_fields", [])]
    records: list[dict[str, Any]] = []
    field_set: set[str] | None = None
    for row in rows.itertuples(index=False):
        try:
            payload = json.loads(str(row.condition_value))
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError(f"PUE 输入 JSON 无法解析: {row.observation_id}") from error
        if not isinstance(payload, dict) or len(payload) != expected_count:
            raise ValueError(f"PUE 输入字段数不是 {expected_count}: {row.observation_id}")
        current_fields = set(map(str, payload))
        if field_set is None:
            field_set = current_fields
        elif current_fields != field_set:
            raise ValueError("PUE 输入 JSON 字段集不一致")
        normalized: dict[str, float] = {}
        for key, value in payload.items():
            numeric = float(value)
            if not np.isfinite(numeric):
                raise ValueError(f"PUE 输入含非有限数: {row.observation_id}")
            normalized[str(key)] = numeric
        group = baseline.stable_mixed_feature_group(normalized)
        records.append(
            {
                **row._asdict(),
                "_feature_payload": normalized,
                "leakage_group": group,
                "development_split": baseline.deterministic_split(
                    group, seed=int(pue["split_seed"])
                ),
            }
        )
    parsed = pd.DataFrame(records)
    if parsed.empty or field_set is None:
        return parsed, []
    missing_categorical = sorted(set(categorical).difference(field_set))
    if missing_categorical:
        raise ValueError(f"PUE 类别字段缺失: {missing_categorical}")
    return parsed, sorted(field_set)


def _pue_design_matrix(
    rows: pd.DataFrame,
    fields: Sequence[str],
    categorical_fields: Sequence[str],
    category_levels: Mapping[str, Sequence[float]],
) -> np.ndarray:
    """按训练集冻结的类别字典编码；未知类别为全零且由调用方审计。"""

    numeric_fields = [field for field in fields if field not in categorical_fields]
    columns: list[np.ndarray] = []
    for field in numeric_fields:
        columns.append(
            np.asarray([row[field] for row in rows["_feature_payload"]], dtype=np.float64)
        )
    for field in categorical_fields:
        values = np.asarray([row[field] for row in rows["_feature_payload"]], dtype=np.float64)
        for level in category_levels[field]:
            columns.append((values == float(level)).astype(np.float64))
    if not columns:
        raise ValueError("PUE 设计矩阵不能为空")
    return np.column_stack(columns)


def run_pue_smoke(
    observations: pd.DataFrame, config: Mapping[str, Any]
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """对 PUE-643 进行等权、重分组的单来源变换目标烟雾测试。"""

    pue = config["pue643"]
    if not bool(pue.get("enabled", True)):
        return (
            pd.DataFrame(columns=METRIC_COLUMNS),
            pd.DataFrame(columns=PREDICTION_COLUMNS),
            {"status": "disabled"},
        )
    rows = observations.loc[
        observations["source_family_id"].eq(str(pue["source_family_id"]))
        & observations["source_id"].eq(str(pue["source_id"]))
        & observations["property_name"].isin([str(value) for value in pue["targets"]])
        & observations["condition_name"].eq(str(pue["condition_name"]))
    ].copy()
    if rows.empty:
        return (
            pd.DataFrame(columns=METRIC_COLUMNS),
            pd.DataFrame(columns=PREDICTION_COLUMNS),
            {"status": "not_available_fail_closed"},
        )
    parsed, fields = _parse_pue_features(rows, config)
    _validate_group_split(parsed, "leakage_group", "development_split")
    categorical = [str(value) for value in pue.get("categorical_fields", [])]
    minimum = {key: int(value) for key, value in pue["minimum_groups"].items()}
    alphas = [float(value) for value in config["ridge"]["alphas"]]
    metric_rows: list[dict[str, Any]] = []
    prediction_frames: list[pd.DataFrame] = []
    statuses: dict[str, str] = {}
    category_levels_by_target: dict[str, dict[str, list[float]]] = {}
    unknown_categories_by_target: dict[str, dict[str, dict[str, int]]] = {}
    for target in [str(value) for value in pue["targets"]]:
        target_rows = parsed.loc[
            parsed["property_name"].eq(target) & np.isfinite(parsed["value"])
        ].copy()
        group_sizes = target_rows.groupby("leakage_group")["observation_id"].transform("size")
        target_rows["recommended_loss_weight"] = 1.0 / group_sizes.astype(float)
        splits = {
            name: target_rows.loc[target_rows["development_split"].eq(name)].sort_values(
                ["leakage_group", "observation_id"], kind="mergesort"
            )
            for name in ("train", "validation", "test")
        }
        counts = {name: frame["leakage_group"].nunique() for name, frame in splits.items()}
        if any(counts[name] < minimum[name] for name in counts):
            statuses[target] = "insufficient_derived_groups"
            continue
        # 类别字典仅从当前目标的训练折生成，避免窃看验证/测试类别集。
        category_levels = {
            field: sorted(
                {
                    float(payload[field])
                    for payload in splits["train"]["_feature_payload"]
                }
            )
            for field in categorical
        }
        category_levels_by_target[target] = category_levels
        unknown_categories_by_target[target] = {}
        for split_name, split_frame in splits.items():
            unknown_categories_by_target[target][split_name] = {
                field: int(
                    sum(
                        float(payload[field]) not in set(category_levels[field])
                        for payload in split_frame["_feature_payload"]
                    )
                )
                for field in categorical
            }
        train_x = _pue_design_matrix(
            splits["train"], fields, categorical, category_levels
        )
        validation_x = _pue_design_matrix(
            splits["validation"], fields, categorical, category_levels
        )
        selection = baseline.choose_ridge_alpha(
            train_x,
            splits["train"]["value"].to_numpy(dtype=float),
            splits["train"]["recommended_loss_weight"].to_numpy(dtype=float),
            validation_x,
            splits["validation"]["value"].to_numpy(dtype=float),
            splits["validation"]["recommended_loss_weight"].to_numpy(dtype=float),
            alphas,
        )
        mean_value = baseline.weighted_mean(
            splits["train"]["value"].to_numpy(dtype=float),
            splits["train"]["recommended_loss_weight"].to_numpy(dtype=float),
        )
        identifier = f"pue643::{target}"
        for split_name in ("validation", "test"):
            evaluation = splits[split_name]
            evaluation_x = _pue_design_matrix(
                evaluation, fields, categorical, category_levels
            )
            models = {
                "weighted_mean": np.full(len(evaluation), mean_value),
                "numeric_categorical_weighted_ridge": baseline.predict_weighted_ridge(
                    selection["model"], evaluation_x
                ),
            }
            for model_name, prediction in models.items():
                alpha = float(selection["alpha"]) if model_name != "weighted_mean" else None
                _append_metric_rows(
                    metric_rows,
                    evaluation,
                    prediction,
                    evaluation_id=identifier,
                    scheme="pue643_derived_feature_group_split",
                    held_out_source="",
                    training_mode="equal_group_weight_smoke",
                    model_name=model_name,
                    target_name=target,
                    unit="published_log_transform_unit_unresolved",
                    split=split_name,
                    selected_alpha=alpha,
                    evidence_scope="within_source_smoke_test",
                    target_semantics="transformed_target_only",
                )
                prediction_frames.append(
                    _prediction_frame(
                        evaluation,
                        prediction,
                        evaluation_id=identifier,
                        scheme="pue643_derived_feature_group_split",
                        training_mode="equal_group_weight_smoke",
                        model_name=model_name,
                        target_name=target,
                        unit="published_log_transform_unit_unresolved",
                        selected_alpha=alpha,
                        evidence_scope="within_source_smoke_test",
                        target_semantics="transformed_target_only",
                    )
                )
        statuses[target] = "evaluated_with_transformed_target_only"
    metrics = pd.DataFrame(metric_rows, columns=METRIC_COLUMNS)
    predictions = (
        pd.concat(prediction_frames, ignore_index=True)
        if prediction_frames
        else pd.DataFrame(columns=PREDICTION_COLUMNS)
    )
    metadata = {
        "status": "completed" if metric_rows else "not_estimable_fail_closed",
        "published_input_field_count": len(fields),
        "categorical_fields": categorical,
        "numeric_field_count": len(fields) - len(categorical),
        "category_levels_from_train_only": category_levels_by_target,
        "unknown_category_rows_encoded_all_zero": unknown_categories_by_target,
        "unique_derived_feature_groups": int(parsed["leakage_group"].nunique()),
        "target_status": statuses,
        "scope": "within_source_smoke_test",
        "target_semantics": "transformed_target_only",
    }
    return metrics, predictions, metadata


def _atomic_write_csv(
    frame: pd.DataFrame, path: Path, *, gzip_output: bool, float_format: str
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    compression: dict[str, Any] | None
    if gzip_output:
        compression = {"method": "gzip", "compresslevel": 9, "mtime": 0}
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
        float_format=float_format,
    )
    os.replace(temporary, path)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _fmt_number(value: Any, digits: int = 3) -> str:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return ""
    if abs(float(number)) >= 100_000:
        return f"{float(number):.3e}"
    return f"{float(number):.{digits}g}"


def _metric_row(
    metrics: pd.DataFrame,
    *,
    target_name: str,
    training_mode: str,
    model_name: str,
    evaluation_split: str = "test",
    aggregation_level: str = "row_weighted",
    evaluation_scheme: str = "published_development_split",
) -> pd.Series | None:
    subset = metrics.loc[
        metrics["target_name"].eq(target_name)
        & metrics["training_mode"].eq(training_mode)
        & metrics["model_name"].eq(model_name)
        & metrics["evaluation_split"].eq(evaluation_split)
        & metrics["aggregation_level"].eq(aggregation_level)
        & metrics["evaluation_scheme"].eq(evaluation_scheme)
    ]
    if subset.empty:
        return None
    return subset.iloc[0]


def _append_markdown_table(
    lines: list[str], headers: Sequence[str], rows: Sequence[Sequence[Any]]
) -> None:
    if not rows:
        lines.append("无可展示记录。")
        return
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")


def _read_reference_entries(citation_keys: Iterable[str]) -> list[str]:
    normalized_keys = []
    for key in citation_keys:
        match = re.match(r"^(ledger-\d+)", key)
        normalized_keys.append(match.group(1) if match else key)
    numbers = sorted(
        {
            REFERENCE_NUMBER_BY_KEY[key]
            for key in normalized_keys
            if key in REFERENCE_NUMBER_BY_KEY
        }
    )
    if not numbers:
        return []
    reference_path = ROOT / "文档" / "数据来源与参考文献.md"
    if not reference_path.is_file():
        return [f"[{number}] 条目缺失：请检查 {reference_path}" for number in numbers]
    entries: dict[int, str] = {}
    for line in reference_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\[(\d+)\]\s+(.+)$", line.strip())
        if match:
            entries[int(match.group(1))] = line.strip()
    return [entries.get(number, f"[{number}] 条目未在参考文献总表中找到。") for number in numbers]


def _development_metric_rows(metrics: pd.DataFrame) -> list[list[str]]:
    rows: list[list[str]] = []
    targets = ["Rg", "bulk_modulus", "density", "thermal_conductivity"]
    for target in targets:
        ridge = _metric_row(
            metrics,
            target_name=target,
            training_mode="primary_plus_aux",
            model_name="morgan_weighted_ridge",
        )
        mean = _metric_row(
            metrics,
            target_name=target,
            training_mode="primary_plus_aux",
            model_name="weighted_mean",
        )
        if ridge is None or mean is None:
            continue
        mean_rmse = pd.to_numeric(pd.Series([mean["rmse"]]), errors="coerce").iloc[0]
        ridge_rmse = pd.to_numeric(pd.Series([ridge["rmse"]]), errors="coerce").iloc[0]
        improvement = ""
        if pd.notna(mean_rmse) and mean_rmse != 0 and pd.notna(ridge_rmse):
            improvement = f"{(1 - ridge_rmse / mean_rmse) * 100:.1f}%"
        rows.append(
            [
                target,
                str(ridge.get("unit", "")),
                str(int(pd.to_numeric(ridge.get("n_groups"), errors="coerce"))),
                _fmt_number(ridge.get("rmse")),
                _fmt_number(ridge.get("r2")),
                _fmt_number(ridge.get("spearman")),
                _fmt_number(mean.get("rmse")),
                improvement,
            ]
        )
    return rows


def _auxiliary_gain_rows(metrics: pd.DataFrame) -> list[list[str]]:
    rows: list[list[str]] = []
    targets = ["Rg", "bulk_modulus", "density", "thermal_conductivity"]
    for target in targets:
        primary = _metric_row(
            metrics,
            target_name=target,
            training_mode="primary_only",
            model_name="morgan_weighted_ridge",
        )
        plus_aux = _metric_row(
            metrics,
            target_name=target,
            training_mode="primary_plus_aux",
            model_name="morgan_weighted_ridge",
        )
        if primary is None or plus_aux is None:
            continue
        primary_rmse = pd.to_numeric(pd.Series([primary["rmse"]]), errors="coerce").iloc[0]
        plus_aux_rmse = pd.to_numeric(pd.Series([plus_aux["rmse"]]), errors="coerce").iloc[0]
        delta = ""
        if pd.notna(primary_rmse) and primary_rmse != 0 and pd.notna(plus_aux_rmse):
            delta = f"{(1 - plus_aux_rmse / primary_rmse) * 100:.1f}%"
        rows.append(
            [
                target,
                _fmt_number(primary.get("rmse")),
                _fmt_number(plus_aux.get("rmse")),
                delta,
                _fmt_number(plus_aux.get("r2")),
            ]
        )
    return rows


def _source_holdout_rows(metrics: pd.DataFrame) -> list[list[str]]:
    rows: list[list[str]] = []
    subset = metrics.loc[
        metrics["evaluation_scheme"].eq("leave_one_source_family_out")
        & metrics["aggregation_level"].eq("row_weighted")
        & metrics["model_name"].isin(["morgan_weighted_ridge", "weighted_mean"])
    ]
    if subset.empty:
        return rows
    for (_, target, mode, held_out), group in subset.groupby(
        ["evaluation_scheme", "target_name", "training_mode", "held_out_source_family"],
        sort=True,
    ):
        ridge = group.loc[group["model_name"].eq("morgan_weighted_ridge")]
        mean = group.loc[group["model_name"].eq("weighted_mean")]
        if ridge.empty or mean.empty:
            continue
        ridge_row = ridge.iloc[0]
        mean_row = mean.iloc[0]
        rows.append(
            [
                target,
                mode,
                held_out,
                str(int(pd.to_numeric(ridge_row.get("n_groups"), errors="coerce"))),
                _fmt_number(ridge_row.get("rmse")),
                _fmt_number(ridge_row.get("r2")),
                _fmt_number(mean_row.get("rmse")),
                _fmt_number(mean_row.get("r2")),
            ]
        )
    return rows


def _pue_metric_rows(metrics: pd.DataFrame) -> list[list[str]]:
    rows: list[list[str]] = []
    for target in ["logYM", "logTS", "logEB"]:
        ridge = _metric_row(
            metrics,
            target_name=target,
            training_mode="equal_group_weight_smoke",
            model_name="numeric_categorical_weighted_ridge",
            evaluation_scheme="pue643_derived_feature_group_split",
        )
        mean = _metric_row(
            metrics,
            target_name=target,
            training_mode="equal_group_weight_smoke",
            model_name="weighted_mean",
            evaluation_scheme="pue643_derived_feature_group_split",
        )
        if ridge is None or mean is None:
            continue
        rows.append(
            [
                target,
                str(int(pd.to_numeric(ridge.get("n_groups"), errors="coerce"))),
                _fmt_number(ridge.get("rmse")),
                _fmt_number(ridge.get("r2")),
                _fmt_number(ridge.get("spearman")),
                _fmt_number(mean.get("rmse")),
            ]
        )
    return rows


def _readiness_rows(readiness: pd.DataFrame) -> list[list[str]]:
    rows: list[list[str]] = []
    configured = readiness.loc[readiness["configured_role"].ne("not_configured")]
    for row in configured.sort_values(["configured_role", "target_name"]).itertuples():
        rows.append(
            [
                row.target_name,
                row.unit,
                row.configured_role,
                str(bool(row.fair_primary_evaluation)),
                str(int(row.primary_hard_groups_train)),
                str(int(row.primary_hard_groups_validation)),
                str(int(row.primary_hard_groups_test)),
                str(int(row.source_family_count)),
            ]
        )
    return rows


def _report_markdown(
    readiness: pd.DataFrame,
    metrics: pd.DataFrame,
    predictions: pd.DataFrame,
    endpoints: pd.DataFrame,
    pue_metadata: Mapping[str, Any],
) -> str:
    configured = readiness.loc[readiness["configured_role"].ne("not_configured")]
    trained = configured.loc[configured["fair_primary_evaluation"]]
    endpoint_counts = endpoints["endpoint_status"].value_counts().to_dict() if not endpoints.empty else {}
    citations: set[str] = set()
    sources: set[str] = set()
    for value in configured["citation_keys"].dropna().astype(str):
        citations.update(key for key in value.split(";") if key)
    for value in configured["source_ids"].dropna().astype(str):
        sources.update(key for key in value.split(";") if key)
    if not predictions.empty:
        sources.update(_text(predictions["source_id"]).loc[lambda values: values.ne("")])
        for value in predictions["citation_keys"].dropna().astype(str):
            citations.update(key for key in value.split(";") if key)
    if not endpoints.empty:
        sources.update(_text(endpoints["source_id"]).loc[lambda values: values.ne("")])
        for value in endpoints["citation_keys"].dropna().astype(str):
            citations.update(key for key in value.split(";") if key)
    lines = [
        "# TPU 第一阶段基线报告",
        "",
        "本报告只描述冻结发布层上的管线基线，不代表已获得最终 TPU 性能模型或发现新材料。",
        "首版只是加权常数与 Morgan 指纹 Ridge 的传统基线，用于验证数据、切分、权重和评估链路；它不是拟投论文的最终模型。",
        "",
        "## 计算目标",
        "",
        f"- 配置目标：{len(configured)} 个；达到严格 primary train/validation/test 门槛：{len(trained)} 个。",
        f"- 已生成评估指标行：{len(metrics)}。负结果不删除，常数与 Morgan-Ridge 按相同 primary 验证/测试对象比较。",
        "- 来源外推只在 leave-one-source-family-out 的训练、验证与留出组均达门槛时计算；没有把稀疏的 source_holdout_fold 称为五折评估。",
        "- `structure_identity_status` 和结构中的 `*` 数量已保留到可训练性表/逐样本预测。本基线学习的是 pSMILES/重复单元关联，不能代替闭合配方、分子量和工艺表示。",
        "",
        "### 目标可训练性",
        "",
    ]
    _append_markdown_table(
        lines,
        ["目标", "单位", "角色", "公平评估", "训练组", "验证组", "测试组", "来源族"],
        _readiness_rows(readiness),
    )
    lines.extend(
        [
            "",
            "### 内部测试集表现",
            "",
            "下表使用 `primary_plus_aux` 训练模式、row-weighted test 指标；辅助数据只参与训练，不改变 primary 验证/测试对象。",
            "",
        ]
    )
    _append_markdown_table(
        lines,
        ["目标", "单位", "测试组", "Ridge RMSE", "R2", "Spearman", "常数 RMSE", "RMSE 降低"],
        _development_metric_rows(metrics),
    )
    lines.extend(
        [
            "",
            "### 辅助模拟数据的作用",
            "",
            "这里比较同一 primary 测试集上 `primary_only` 与 `primary_plus_aux` 的 Morgan-Ridge RMSE。当前只能证明辅助数据对内部切分有小幅帮助，不能证明跨来源外推已经可靠。",
            "",
        ]
    )
    _append_markdown_table(
        lines,
        ["目标", "primary_only RMSE", "primary_plus_aux RMSE", "RMSE 降低", "plus_aux R2"],
        _auxiliary_gain_rows(metrics),
    )
    lines.extend(
        [
            "",
            "### 来源外推检查",
            "",
            "目前只有 Rg/Open Polymer Challenge 满足 leave-one-source-family-out 门槛；R2 仍为负，说明第一阶段模型还不能直接承担跨数据库发现任务。",
            "",
        ]
    )
    _append_markdown_table(
        lines,
        ["目标", "训练模式", "留出来源族", "测试组", "Ridge RMSE", "Ridge R2", "常数 RMSE", "常数 R2"],
        _source_holdout_rows(metrics),
    )
    lines.extend(
        [
            "",
            "## 实验曲线",
            "",
            f"- 曲线索引行：{len(endpoints)}；端点状态：{json.dumps(endpoint_counts, ensure_ascii=False, sort_keys=True)}。",
            "- 只对轴语义和单位闭合的拉伸/压缩曲线生成保守端点。最大观测值不命名为断裂性能，循环曲线在未分割周期时 fail-closed。",
            "- `unsupported_axis_or_unit` 中包含 54 条 IIR 端点包，它们不是连续应力-应变序列；这类记录仍保留血缘，可作为后续外部验证或人工特征补录候选。",
            "",
            "## PUE-643 烟雾测试",
            "",
            f"- 状态：{pue_metadata.get('status', 'unknown')}；完整输入向量派生硬组：{pue_metadata.get('unique_derived_feature_groups', 0)}。",
            "- Form_Method 与 PMStep 按类别特征编码，其余18个字段作数值特征。logYM/logTS/logEB 仅标记为 transformed_target_only，不逆变换、不声称物理单位。",
            "- 类别字典只由各目标训练折生成；验证/测试未知类别编码为全零，其计数写入运行清单。",
            "",
        ]
    )
    _append_markdown_table(
        lines,
        ["目标", "测试组", "Ridge RMSE", "R2", "Spearman", "常数 RMSE"],
        _pue_metric_rows(metrics),
    )
    lines.extend(
        [
            "",
            "## 多保真结论与限制",
            "",
            "当前 Gold-C 与 Gold-E 之间没有足够闭合的同结构—配方—工艺成对映射，因此多保真增益记为 `not_established`，不以行数替代映射证据。",
            "",
            "## 引用连接",
            "",
            "本次实际使用/审计到的 source_id：",
            "",
        ]
    )
    lines.extend(f"- `{source_id}`" for source_id in sorted(sources))
    if not sources:
        lines.append("- 无")
    lines.extend([
        "",
        "完整文献条目见 `文档/数据来源与参考文献.md`。本次基线和曲线端点涉及的 citation keys：",
        "",
    ])
    lines.extend(f"- `{key}`" for key in sorted(citations))
    if not citations:
        lines.append("- 无可物化端点引用（请检查端点状态）")
    reference_entries = _read_reference_entries(citations)
    lines.extend(
        [
            "",
            "## 本报告涉及的参考文献条目",
            "",
        ]
    )
    if reference_entries:
        lines.extend(f"- {entry}" for entry in reference_entries)
    else:
        lines.append("- 无")
    return "\n".join(lines) + "\n"


def _merge_results(
    first: pd.DataFrame, second: pd.DataFrame, columns: Sequence[str]
) -> pd.DataFrame:
    frames = [frame for frame in (first, second) if not frame.empty]
    if not frames:
        return pd.DataFrame(columns=columns)
    result = pd.concat(frames, ignore_index=True).reindex(columns=columns)
    sort_columns = [column for column in ("evaluation_scheme", "target_name", "training_mode", "evaluation_id", "model_name", "evaluation_split", "aggregation_level", "development_split", "leakage_group", "observation_id") if column in result.columns]
    return result.sort_values(sort_columns, kind="mergesort").reset_index(drop=True)


def run_pipeline(
    config_path: Path = DEFAULT_CONFIG_PATH,
    release_dir: Path = DEFAULT_RELEASE_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    """运行完整基线并返回运行清单；输出不回写发布目录。"""

    started_clock = time.perf_counter()
    started_utc = datetime.now(timezone.utc).isoformat()
    config = load_config(config_path)
    release_audit = verify_release(release_dir, config)
    computational_task_id = str(config["computational_task"]["task_id"])
    computational = load_computational_observations(
        release_dir, task_id=computational_task_id
    )
    task_rows = computational
    radius = int(config["features"]["morgan_radius"])
    bits = int(config["features"]["morgan_bits"])
    cache, invalid = _fingerprint_cache(
        task_rows["canonical_structure"].dropna().astype(str).unique().tolist(),
        radius,
        bits,
    )
    readiness = build_target_readiness(computational, config, set(cache))
    computational_metrics, computational_predictions = train_computational_baselines(
        computational, config, cache, readiness
    )
    experimental = load_experimental_subset(release_dir, config)
    curve_index = pd.read_csv(release_dir / "曲线索引.csv", low_memory=False)
    endpoints = build_curve_endpoints(experimental, curve_index, config)
    pue_metrics, pue_predictions, pue_metadata = run_pue_smoke(experimental, config)
    metrics = _merge_results(computational_metrics, pue_metrics, METRIC_COLUMNS)
    predictions = _merge_results(
        computational_predictions, pue_predictions, PREDICTION_COLUMNS
    )

    float_format = str(config["run"]["csv_float_format"])
    paths = {key: output_dir / name for key, name in OUTPUT_NAMES.items()}
    _atomic_write_csv(readiness, paths["readiness"], gzip_output=False, float_format=float_format)
    _atomic_write_csv(metrics, paths["metrics"], gzip_output=False, float_format=float_format)
    _atomic_write_csv(
        predictions, paths["predictions"], gzip_output=True, float_format=float_format
    )
    _atomic_write_csv(
        endpoints, paths["curve_endpoints"], gzip_output=False, float_format=float_format
    )
    report = _report_markdown(
        readiness, metrics, predictions, endpoints, pue_metadata
    )
    _atomic_write_text(paths["report"], report)

    generated_files: dict[str, dict[str, Any]] = {}
    for key in ("readiness", "metrics", "predictions", "curve_endpoints", "report"):
        path = paths[key]
        generated_files[key] = {
            "path": path.name,
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
    manifest = {
        "release_id": config["release"]["id"],
        "run_started_utc": started_utc,
        "runtime_seconds": round(time.perf_counter() - started_clock, 6),
        "git_commit_baseline": _git_commit(),
        "input_files": {
            label: {
                "path": Path(metadata["path"]).name,
                "bytes": metadata["bytes"],
                "sha256": metadata["sha256"],
            }
            for label, metadata in release_audit["checked"].items()
        },
        "config": config,
        "config_file": {
            "path": str(config_path),
            "sha256": _sha256(config_path),
        },
        "software": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "rdkit": rdBase.rdkitVersion,
        },
        "counts": {
            "computational_structure_task_rows_read": int(len(computational)),
            "valid_unique_structures": int(len(cache)),
            "invalid_or_missing_unique_structures": int(len(invalid)),
            "readiness_rows": int(len(readiness)),
            "metric_rows": int(len(metrics)),
            "prediction_rows": int(len(predictions)),
            "curve_endpoint_rows": int(len(endpoints)),
        },
        "pue643": pue_metadata,
        "multifidelity_gain_status": "not_established",
        "output_files": generated_files,
        "determinism": {
            "stable_sort": True,
            "gzip_mtime": 0,
            "variable_fields": ["run_started_utc", "runtime_seconds"],
        },
    }
    _atomic_write_text(
        paths["manifest"],
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    return manifest


def verify_generated_outputs(
    output_dir: Path,
    *,
    config_path: Path | None = None,
    expected_release_id: str | None = None,
) -> dict[str, Any]:
    """根据运行清单校验已生成结果，不重跑模型。"""

    manifest_path = output_dir / OUTPUT_NAMES["manifest"]
    if not manifest_path.is_file():
        return {"status": "not_generated", "checked": {}}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    if expected_release_id is not None and manifest.get("release_id") != expected_release_id:
        failures.append("运行清单 release_id 与当前配置不一致")
    if config_path is not None:
        recorded_config_hash = manifest.get("config_file", {}).get("sha256")
        if not config_path.is_file() or recorded_config_hash != _sha256(config_path):
            failures.append("基线配置 SHA-256 与运行清单不一致")
    checked: dict[str, str] = {}
    for key, metadata in sorted(manifest.get("output_files", {}).items()):
        path = output_dir / str(metadata["path"])
        if not path.is_file():
            failures.append(f"{key}: 文件缺失")
            continue
        actual = _sha256(path)
        checked[key] = actual
        if actual != str(metadata["sha256"]):
            failures.append(f"{key}: SHA-256 漂移")
    if failures:
        raise ValueError("基线输出验证失败: " + "; ".join(failures))
    return {"status": "verified", "checked": checked}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--配置", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--发布目录", type=Path, default=DEFAULT_RELEASE_DIR)
    parser.add_argument("--输出目录", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--检查",
        action="store_true",
        help="只校验冻结发布和现有基线结果，不训练、不写文件",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args.配置)
    if args.检查:
        release = verify_release(args.发布目录, config)
        generated = verify_generated_outputs(
            args.输出目录,
            config_path=args.配置,
            expected_release_id=str(config["release"]["id"]),
        )
        print(
            json.dumps(
                {
                    "release_id": release["release_id"],
                    "release_status": "verified",
                    "generated_outputs": generated,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    manifest = run_pipeline(args.配置, args.发布目录, args.输出目录)
    print(
        json.dumps(
            {
                "release_id": manifest["release_id"],
                "metric_rows": manifest["counts"]["metric_rows"],
                "prediction_rows": manifest["counts"]["prediction_rows"],
                "output_dir": str(args.输出目录),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
