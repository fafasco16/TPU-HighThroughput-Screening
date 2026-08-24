"""在冻结发布划分上训练和评估轻量多任务 GNN。

训练只读取 ``development_split=train``；validation 用于早停和选择唯一
checkpoint；test 在选定 checkpoint 后只预测一次。``primary_plus_aux`` 只向
训练折加入辅助观测，两个模式的 validation/test 均保持 ``primary_train``。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import platform
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

try:
    from GNN数据 import (
        DEFAULT_TARGETS,
        EDGE_FEATURE_NAMES,
        GraphSample,
        NODE_FEATURE_NAMES,
        build_graph_dataset,
        collate_graphs,
        load_computational_observations,
    )
    from GNN模型 import (
        MultiTaskGNN,
        TargetStandardizer,
        fit_target_standardizer,
        masked_weighted_mse,
        require_torch,
        set_random_seed,
    )
except ImportError:  # pragma: no cover - package-style execution
    from .GNN数据 import (
        DEFAULT_TARGETS,
        EDGE_FEATURE_NAMES,
        GraphSample,
        NODE_FEATURE_NAMES,
        build_graph_dataset,
        collate_graphs,
        load_computational_observations,
    )
    from .GNN模型 import (
        MultiTaskGNN,
        TargetStandardizer,
        fit_target_standardizer,
        masked_weighted_mse,
        require_torch,
        set_random_seed,
    )


TRAINING_MODES = ("primary_only", "primary_plus_aux")
SPLITS = ("train", "validation", "test")


@dataclass(frozen=True)
class TrainingResult:
    model: Any
    standardizer: TargetStandardizer
    best_epoch: int
    best_validation_loss: float
    epochs_ran: int
    checkpoint_path: Path


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError("GNN 配置必须是 YAML 对象")
    required = {"release", "task", "model", "training", "source_family_holdout", "output"}
    missing = sorted(required.difference(config))
    if missing:
        raise ValueError(f"GNN 配置缺少分区: {missing}")
    target_items = config["task"].get("targets", [])
    targets = tuple(str(item.get("name", "")).strip() for item in target_items)
    if targets != tuple(DEFAULT_TARGETS):
        raise ValueError(f"GNN 正式目标必须依次为 {list(DEFAULT_TARGETS)}，不得包含 Tg")
    modes = tuple(config["task"].get("training_modes", []))
    if modes != TRAINING_MODES:
        raise ValueError(f"training_modes 必须依次为 {list(TRAINING_MODES)}")
    integer_positive = ("batch_size", "maximum_epochs", "early_stopping_patience")
    for key in integer_positive:
        value = config["training"].get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"training.{key} 必须是正整数")
    seed = config["training"].get("random_seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("training.random_seed 必须是非负整数")
    for key in ("learning_rate", "minimum_improvement"):
        value = float(config["training"].get(key, float("nan")))
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"training.{key} 必须是有限正数")
    weight_decay = float(config["training"].get("weight_decay", float("nan")))
    if not math.isfinite(weight_decay) or weight_decay < 0:
        raise ValueError("training.weight_decay 必须是有限非负数")
    model = config["model"]
    for key in ("hidden_dim", "message_passing_steps"):
        value = model.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"model.{key} 必须是正整数")
    dropout = float(model.get("dropout", float("nan")))
    if not math.isfinite(dropout) or not 0 <= dropout < 1:
        raise ValueError("model.dropout 必须位于 [0, 1)")
    return config


def resolve_device(requested: str) -> str:
    torch = require_torch()
    normalized = str(requested).strip().lower()
    if normalized == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if normalized not in {"cpu", "cuda"}:
        raise ValueError("设备只能是 auto、cpu 或 cuda")
    if normalized == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("请求 CUDA，但 PyTorch 未检测到可用 GPU")
    return normalized


def select_mode_samples(
    samples: Sequence[GraphSample], mode: str
) -> dict[str, list[GraphSample]]:
    if mode not in TRAINING_MODES:
        raise ValueError(f"未知训练模式: {mode}")
    train_usage = {"primary_train"}
    if mode == "primary_plus_aux":
        train_usage.add("auxiliary_train")
    selected = {
        "train": [
            sample
            for sample in samples
            if sample.development_split == "train" and sample.usage_mode in train_usage
        ],
        "validation": [
            sample
            for sample in samples
            if sample.development_split == "validation"
            and sample.usage_mode == "primary_train"
        ],
        "test": [
            sample
            for sample in samples
            if sample.development_split == "test" and sample.usage_mode == "primary_train"
        ],
    }
    empty = [split for split, rows in selected.items() if not rows]
    if empty:
        raise ValueError(f"{mode} 缺少数据折: {empty}")
    return selected


def iter_minibatches(
    samples: Sequence[GraphSample],
    batch_size: int,
    *,
    shuffle: bool,
    seed: int,
) -> Iterable[list[GraphSample]]:
    if batch_size <= 0:
        raise ValueError("batch_size 必须为正")
    order = np.arange(len(samples))
    if shuffle:
        np.random.default_rng(seed).shuffle(order)
    for start in range(0, len(order), batch_size):
        yield [samples[int(index)] for index in order[start : start + batch_size]]


def fit_standardizer(samples: Sequence[GraphSample]) -> TargetStandardizer:
    if not samples:
        raise ValueError("训练样本不能为空")
    return fit_target_standardizer(
        np.stack([sample.targets for sample in samples]),
        np.stack([sample.target_mask for sample in samples]),
        np.stack([sample.target_weights for sample in samples]),
        samples[0].target_names,
    )


def _torch_batch(samples: Sequence[GraphSample], device: str) -> dict[str, Any]:
    batch = collate_graphs(samples, as_torch=True)
    tensor_keys = (
        "node_features",
        "edge_index",
        "edge_features",
        "graph_index",
        "targets",
        "target_mask",
        "target_weights",
    )
    for key in tensor_keys:
        batch[key] = batch[key].to(device)
    return batch


def _batch_prediction(model: Any, batch: Mapping[str, Any]) -> Any:
    return model(
        batch["node_features"],
        batch["edge_index"],
        batch["graph_index"],
        batch["edge_features"],
        graph_count=len(batch["observation_id"]),
    )


def validation_loss(
    model: Any,
    samples: Sequence[GraphSample],
    standardizer: TargetStandardizer,
    batch_size: int,
    device: str,
) -> float:
    torch = require_torch()
    model.eval()
    weighted_sum = 0.0
    total_weight = 0.0
    with torch.no_grad():
        for rows in iter_minibatches(samples, batch_size, shuffle=False, seed=0):
            batch = _torch_batch(rows, device)
            target = standardizer.transform_tensor(batch["targets"], batch["target_mask"])
            prediction = _batch_prediction(model, batch)
            loss = masked_weighted_mse(
                prediction, target, batch["target_mask"], batch["target_weights"]
            )
            weight = float((batch["target_weights"] * batch["target_mask"]).sum().item())
            weighted_sum += float(loss.item()) * weight
            total_weight += weight
    if total_weight <= 0:
        raise ValueError("验证集没有正权重目标")
    return weighted_sum / total_weight


def train_model(
    train_samples: Sequence[GraphSample],
    validation_samples: Sequence[GraphSample],
    config: Mapping[str, Any],
    *,
    mode: str,
    device: str,
    checkpoint_path: str | Path,
) -> TrainingResult:
    torch = require_torch()
    training = config["training"]
    model_config = config["model"]
    seed = int(training["random_seed"])
    set_random_seed(seed)
    standardizer = fit_standardizer(train_samples)
    edge_dim = len(EDGE_FEATURE_NAMES) if bool(model_config["use_edge_features"]) else 0
    model = MultiTaskGNN(
        input_dim=len(NODE_FEATURE_NAMES),
        hidden_dim=int(model_config["hidden_dim"]),
        tasks=train_samples[0].target_names,
        message_passing_steps=int(model_config["message_passing_steps"]),
        dropout=float(model_config["dropout"]),
        edge_dim=edge_dim,
    ).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=float(training["learning_rate"]),
        weight_decay=float(training["weight_decay"]),
    )
    batch_size = int(training["batch_size"])
    patience = int(training["early_stopping_patience"])
    minimum_improvement = float(training["minimum_improvement"])
    maximum_epochs = int(training["maximum_epochs"])
    best_loss = float("inf")
    best_epoch = 0
    best_state: dict[str, Any] | None = None
    stale_epochs = 0
    epochs_ran = 0
    for epoch in range(1, maximum_epochs + 1):
        model.train()
        for rows in iter_minibatches(
            train_samples, batch_size, shuffle=True, seed=seed + epoch
        ):
            batch = _torch_batch(rows, device)
            target = standardizer.transform_tensor(batch["targets"], batch["target_mask"])
            optimizer.zero_grad(set_to_none=True)
            prediction = _batch_prediction(model, batch)
            loss = masked_weighted_mse(
                prediction, target, batch["target_mask"], batch["target_weights"]
            )
            loss.backward()
            optimizer.step()
        current = validation_loss(
            model, validation_samples, standardizer, batch_size, device
        )
        epochs_ran = epoch
        if current < best_loss - minimum_improvement:
            best_loss = current
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= patience:
                break
    if best_state is None:
        raise RuntimeError("验证集没有产生有限 checkpoint")
    model.load_state_dict(best_state)
    checkpoint = Path(checkpoint_path)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "state_dict": best_state,
            "target_names": list(train_samples[0].target_names),
            "standardizer": {
                "mean": standardizer.mean.tolist(),
                "scale": standardizer.scale.tolist(),
            },
            "mode": mode,
            "seed": seed,
            "best_epoch": best_epoch,
            "best_validation_loss": best_loss,
            "model_config": dict(model_config),
        },
        checkpoint,
    )
    return TrainingResult(
        model, standardizer, best_epoch, best_loss, epochs_ran, checkpoint
    )


def predict_samples(
    result: TrainingResult,
    samples: Sequence[GraphSample],
    *,
    batch_size: int,
    device: str,
) -> np.ndarray:
    torch = require_torch()
    result.model.eval()
    parts: list[np.ndarray] = []
    with torch.no_grad():
        for rows in iter_minibatches(samples, batch_size, shuffle=False, seed=0):
            batch = _torch_batch(rows, device)
            standardized = _batch_prediction(result.model, batch)
            parts.append(result.standardizer.inverse_tensor(standardized).cpu().numpy())
    if not parts:
        raise ValueError("评估样本不能为空")
    return np.concatenate(parts, axis=0)


def regression_metrics(
    truth: Sequence[float], prediction: Sequence[float], weights: Sequence[float]
) -> dict[str, float]:
    y = np.asarray(truth, dtype=np.float64)
    p = np.asarray(prediction, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    if y.ndim != 1 or p.shape != y.shape or w.shape != y.shape:
        raise ValueError("truth、prediction、weights 必须是同形一维数组")
    if not (np.isfinite(y).all() and np.isfinite(p).all() and np.isfinite(w).all()):
        raise ValueError("指标输入必须全部有限")
    if (w < 0).any() or w.sum() <= 0:
        raise ValueError("指标权重必须非负且总和为正")
    residual = p - y
    mean = float(np.dot(y, w) / w.sum())
    mse = float(np.dot(residual**2, w) / w.sum())
    mae = float(np.dot(np.abs(residual), w) / w.sum())
    total = float(np.dot((y - mean) ** 2, w))
    r2 = float("nan") if total <= np.finfo(float).eps else 1.0 - float(np.dot(residual**2, w)) / total
    y_rank = pd.Series(y[w > 0]).rank(method="average").to_numpy()
    p_rank = pd.Series(p[w > 0]).rank(method="average").to_numpy()
    spearman = (
        float("nan")
        if len(y_rank) < 2 or np.std(y_rank) <= np.finfo(float).eps or np.std(p_rank) <= np.finfo(float).eps
        else float(np.corrcoef(y_rank, p_rank)[0, 1])
    )
    return {"mae": mae, "rmse": math.sqrt(mse), "r2": r2, "spearman": spearman}


def prediction_frame(
    samples: Sequence[GraphSample],
    predictions: np.ndarray,
    *,
    evaluation_id: str,
    evaluation_scheme: str,
    training_mode: str,
    held_out_source_family: str = "",
) -> pd.DataFrame:
    if predictions.shape != (len(samples), len(DEFAULT_TARGETS)):
        raise ValueError("预测矩阵形状与样本/任务数不一致")
    rows = []
    target_lookup = {name: index for index, name in enumerate(DEFAULT_TARGETS)}
    for sample, values in zip(samples, predictions, strict=True):
        index = target_lookup[sample.target_name]
        rows.append(
            {
                "evaluation_id": evaluation_id,
                "evaluation_scheme": evaluation_scheme,
                "held_out_source_family": held_out_source_family,
                "training_mode": training_mode,
                "model_name": "multitask_gnn",
                "observation_id": sample.observation_id,
                "source_id": sample.source_id,
                "source_family_id": sample.source_family_id,
                "leakage_group": sample.leakage_group,
                "development_split": sample.development_split,
                "usage_mode": sample.usage_mode,
                "target_name": sample.target_name,
                "unit": sample.target_unit,
                "truth": float(sample.targets[index]),
                "prediction": float(values[index]),
                "recommended_loss_weight": sample.recommended_loss_weight,
            }
        )
    return pd.DataFrame(rows)


def metric_rows(predictions: pd.DataFrame) -> pd.DataFrame:
    required = {
        "evaluation_id", "evaluation_scheme", "held_out_source_family",
        "training_mode", "model_name", "target_name", "unit",
        "leakage_group", "truth", "prediction", "recommended_loss_weight",
    }
    missing = required.difference(predictions.columns)
    if missing:
        raise ValueError(f"逐样本预测缺少字段: {sorted(missing)}")
    rows: list[dict[str, Any]] = []
    identity = [
        "evaluation_id", "evaluation_scheme", "held_out_source_family",
        "training_mode", "model_name", "target_name", "unit",
    ]
    for keys, group in predictions.groupby(identity, dropna=False, sort=True):
        common = dict(zip(identity, keys, strict=True))
        row_metrics = regression_metrics(group["truth"], group["prediction"], group["recommended_loss_weight"])
        rows.append({**common, "aggregation_level": "row_weighted", "n_rows": len(group), "n_groups": group["leakage_group"].nunique(), **row_metrics})
        macro = []
        for leakage_group, members in group.groupby("leakage_group", sort=True):
            weights = members["recommended_loss_weight"].to_numpy(float)
            macro.append(
                {
                    "leakage_group": leakage_group,
                    "truth": np.average(members["truth"], weights=weights),
                    "prediction": np.average(members["prediction"], weights=weights),
                }
            )
        macro_frame = pd.DataFrame(macro)
        macro_metrics = regression_metrics(
            macro_frame["truth"], macro_frame["prediction"], np.ones(len(macro_frame))
        )
        rows.append({**common, "aggregation_level": "hard_group_macro", "n_rows": len(group), "n_groups": len(macro_frame), **macro_metrics})
    return pd.DataFrame(rows)


def eligible_source_holdouts(
    samples: Sequence[GraphSample],
    mode: str,
    minimum_groups: Mapping[str, int],
) -> list[tuple[str, dict[str, list[GraphSample]]]]:
    primary = [sample for sample in samples if sample.usage_mode == "primary_train"]
    output = []
    for family in sorted({sample.source_family_id for sample in primary}):
        test = [sample for sample in primary if sample.source_family_id == family]
        held_groups = {sample.leakage_group for sample in test}
        mode_splits = select_mode_samples(samples, mode)
        train = [sample for sample in mode_splits["train"] if sample.source_family_id != family and sample.leakage_group not in held_groups]
        validation = [sample for sample in mode_splits["validation"] if sample.source_family_id != family and sample.leakage_group not in held_groups]
        subsets = {"train": train, "validation": validation, "test": test}
        counts = {key: len({sample.leakage_group for sample in rows}) for key, rows in subsets.items()}
        if all(counts[key] >= int(minimum_groups[key]) for key in SPLITS):
            output.append((family, subsets))
    return output


def _environment_manifest(device: str) -> dict[str, Any]:
    torch = require_torch()
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "cuda_available": bool(torch.cuda.is_available()),
        "device": device,
        "gpu": torch.cuda.get_device_name(0) if device == "cuda" else "",
    }


def run_training(
    data_path: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
    *,
    requested_device: str = "auto",
) -> dict[str, Any]:
    config = load_config(config_path)
    device = resolve_device(requested_device)
    seed = int(config["training"]["random_seed"])
    set_random_seed(seed)
    observations, targets = load_computational_observations(
        data_path, targets=DEFAULT_TARGETS
    )
    samples = build_graph_dataset(observations, targets=targets)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    all_predictions: list[pd.DataFrame] = []
    checkpoints: dict[str, dict[str, Any]] = {}
    holdout_summary: dict[str, list[str]] = {}
    batch_size = int(config["training"]["batch_size"])
    for mode in TRAINING_MODES:
        split = select_mode_samples(samples, mode)
        checkpoint = output / "checkpoint" / f"{mode}.pt"
        result = train_model(split["train"], split["validation"], config, mode=mode, device=device, checkpoint_path=checkpoint)
        # 常规 test 只在 checkpoint 选定后调用一次。
        test_prediction = predict_samples(result, split["test"], batch_size=batch_size, device=device)
        all_predictions.append(prediction_frame(split["test"], test_prediction, evaluation_id=f"development_test::{mode}", evaluation_scheme="development_test", training_mode=mode))
        checkpoints[mode] = {
            "path": str(checkpoint.relative_to(output)),
            "sha256": sha256_file(checkpoint),
            "best_epoch": result.best_epoch,
            "epochs_ran": result.epochs_ran,
            "best_validation_loss": result.best_validation_loss,
        }
        eligible = []
        if bool(config["source_family_holdout"]["enabled"]):
            eligible = eligible_source_holdouts(samples, mode, config["source_family_holdout"]["minimum_groups"])
        holdout_summary[mode] = [family for family, _ in eligible]
        for family, family_split in eligible:
            family_checkpoint = output / "checkpoint" / "来源族留出" / mode / f"{family}.pt"
            family_result = train_model(family_split["train"], family_split["validation"], config, mode=mode, device=device, checkpoint_path=family_checkpoint)
            family_prediction = predict_samples(family_result, family_split["test"], batch_size=batch_size, device=device)
            all_predictions.append(prediction_frame(family_split["test"], family_prediction, evaluation_id=f"leave_one_source_family_out::{mode}::{family}", evaluation_scheme="leave_one_source_family_out", training_mode=mode, held_out_source_family=family))
            checkpoints[f"{mode}::{family}"] = {
                "path": str(family_checkpoint.relative_to(output)),
                "sha256": sha256_file(family_checkpoint),
                "best_epoch": family_result.best_epoch,
                "epochs_ran": family_result.epochs_ran,
                "best_validation_loss": family_result.best_validation_loss,
            }
    predictions = pd.concat(all_predictions, ignore_index=True)
    metrics = metric_rows(predictions)
    float_format = str(config["output"]["csv_float_format"])
    prediction_path = output / "逐样本预测.csv.gz"
    metric_path = output / "指标.csv"
    predictions.to_csv(prediction_path, index=False, compression="gzip", float_format=float_format)
    metrics.to_csv(metric_path, index=False, float_format=float_format)
    code_dir = Path(__file__).resolve().parent
    manifest = {
        "status": "completed",
        "seed": seed,
        "environment": _environment_manifest(device),
        "rows": {"samples": len(samples), "predictions": len(predictions), "metrics": len(metrics)},
        "source_family_holdouts": holdout_summary,
        "inputs": {
            "data": {"path": str(Path(data_path)), "sha256": sha256_file(data_path)},
            "config": {"path": str(Path(config_path)), "sha256": sha256_file(config_path)},
            "GNN数据.py": {"path": str(code_dir / "GNN数据.py"), "sha256": sha256_file(code_dir / "GNN数据.py")},
            "GNN模型.py": {"path": str(code_dir / "GNN模型.py"), "sha256": sha256_file(code_dir / "GNN模型.py")},
            "训练GNN基线.py": {"path": str(Path(__file__).resolve()), "sha256": sha256_file(Path(__file__).resolve())},
        },
        "checkpoints": checkpoints,
        "outputs": {
            "metrics": {"path": metric_path.name, "sha256": sha256_file(metric_path)},
            "predictions": {"path": prediction_path.name, "sha256": sha256_file(prediction_path)},
        },
    }
    manifest_path = output / "运行清单.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--数据", type=Path, default=Path("结果/可用数据集/计算观测.csv.gz"))
    parser.add_argument("--配置", type=Path, default=Path("模型/GNN配置.yaml"))
    parser.add_argument("--输出目录", type=Path, default=Path("模型/GNN结果"))
    parser.add_argument("--设备", choices=("auto", "cpu", "cuda"), default="auto")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    manifest = run_training(
        arguments.数据,
        arguments.配置,
        arguments.输出目录,
        requested_device=arguments.设备,
    )
    print(json.dumps(manifest["rows"], ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
