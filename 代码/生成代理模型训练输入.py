"""生成代理模型训练入口清单，不训练模型也不启动新量化计算。"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DIRECTED = ROOT / "结果" / "定向筛选"
LABELS = DIRECTED / "三目标实验标签.csv.gz"
COMPUTED = DIRECTED / "三目标计算证据.csv.gz"
TASKS = DIRECTED / "训练前任务清单.csv"
LEDGER = DIRECTED / "扩充数据总账.csv"
SUMMARY = DIRECTED / "代理训练任务统计.csv"
SOURCE_MAP = DIRECTED / "代理训练数据源清单.csv"
README = DIRECTED / "代理模型训练说明.md"
MANIFEST = DIRECTED / "代理模型训练输入发布清单.json"

OBJECTIVES = ("toughness", "cyclic_recovery", "thermal_stability")
PRIMARY_ROLES = {
    "primary_direct_scalar",
    "primary_conditioned_scalar",
    "primary_curve_for_endpoint",
    "primary_cyclic_curve",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require(frame: pd.DataFrame, columns: set[str], name: str) -> None:
    missing = columns - set(frame.columns)
    if missing:
        raise ValueError(f"{name}缺少字段: {sorted(missing)}")


def _load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    labels = pd.read_csv(LABELS, low_memory=False)
    computed = pd.read_csv(COMPUTED, low_memory=False)
    tasks = pd.read_csv(TASKS, low_memory=False)
    ledger = pd.read_csv(LEDGER, low_memory=False)
    _require(
        labels,
        {
            "target_family",
            "model_ready",
            "recommended_loss_weight",
            "development_split",
            "source_family_id",
            "formulation_id",
            "independent_unit",
            "leakage_group",
            "target_metric_role",
        },
        "三目标实验标签",
    )
    _require(
        computed,
        {
            "target_family",
            "model_ready",
            "recommended_loss_weight",
            "development_split",
            "source_family_id",
            "independent_unit",
            "leakage_group",
        },
        "三目标计算证据",
    )
    _require(
        tasks,
        {
            "objective_id",
            "minimum_independent_formulations",
            "current_chemistry_closed_groups",
            "model_family",
        },
        "训练前任务清单",
    )
    _require(
        ledger,
        {
            "package_id",
            "target_family",
            "row_count",
            "model_admission_layer",
            "mapping_tier",
        },
        "扩充数据总账",
    )
    labels["model_ready"] = labels["model_ready"].fillna(False).astype(bool)
    computed["model_ready"] = computed["model_ready"].fillna(False).astype(bool)
    labels["recommended_loss_weight"] = pd.to_numeric(
        labels["recommended_loss_weight"], errors="coerce"
    ).fillna(0.0)
    computed["recommended_loss_weight"] = pd.to_numeric(
        computed["recommended_loss_weight"], errors="coerce"
    ).fillna(0.0)
    return labels, computed, tasks, ledger


def _nunique(frame: pd.DataFrame, column: str) -> int:
    return int(frame[column].replace("", pd.NA).dropna().nunique())


def _summary(
    labels: pd.DataFrame, computed: pd.DataFrame, tasks: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for objective in OBJECTIVES:
        task = tasks.loc[tasks["objective_id"].eq(objective)]
        if len(task) != 1:
            raise ValueError(f"任务清单中目标数量异常: {objective}")
        task_row = task.iloc[0]
        group = labels.loc[labels["target_family"].eq(objective)].copy()
        ready = group.loc[
            group["model_ready"] & group["recommended_loss_weight"].gt(0)
        ]
        primary = ready.loc[ready["target_metric_role"].isin(PRIMARY_ROLES)]
        auxiliary = ready.loc[~ready["target_metric_role"].isin(PRIMARY_ROLES)]
        rows.append(
            {
                "objective_id": objective,
                "objective_name": task_row.get("objective_name", objective),
                "preferred_target": task_row.get("preferred_target", ""),
                "recommended_model_family": task_row["model_family"],
                "all_label_row_count": len(group),
                "model_ready_positive_weight_row_count": len(ready),
                "primary_row_count": len(primary),
                "auxiliary_row_count": len(auxiliary),
                "independent_unit_count": _nunique(ready, "independent_unit"),
                "leakage_group_count": _nunique(ready, "leakage_group"),
                "source_family_count": _nunique(ready, "source_family_id"),
                "formulation_count": _nunique(ready, "formulation_id"),
                "train_row_count": int(ready["development_split"].eq("train").sum()),
                "validation_row_count": int(
                    ready["development_split"].eq("validation").sum()
                ),
                "test_row_count": int(ready["development_split"].eq("test").sum()),
                "recommended_loss_weight_sum": float(
                    ready["recommended_loss_weight"].sum()
                ),
                "minimum_independent_formulations": int(
                    task_row["minimum_independent_formulations"]
                ),
                "current_chemistry_closed_groups": int(
                    task_row["current_chemistry_closed_groups"]
                ),
                "proxy_training_ready": bool(len(ready) > 0),
                "strict_core_structure_model_ready": bool(
                    task_row["current_chemistry_closed_groups"]
                    >= task_row["minimum_independent_formulations"]
                ),
                "recommended_first_use": (
                    "grouped_proxy_or_transfer_baseline_with_loss_weights"
                ),
                "strict_core_block_reason": (
                    "chemistry_closed_formulation_count_below_minimum"
                ),
            }
        )
    computed_ready = computed.loc[
        computed["model_ready"] & computed["recommended_loss_weight"].gt(0)
    ]
    rows.append(
        {
            "objective_id": "computed_multitask_auxiliary",
            "objective_name": "计算多任务辅助",
            "preferred_target": "multi_property_computational_evidence",
            "recommended_model_family": "multi_task_representation_or_residual_model",
            "all_label_row_count": len(computed),
            "model_ready_positive_weight_row_count": len(computed_ready),
            "primary_row_count": 0,
            "auxiliary_row_count": len(computed_ready),
            "independent_unit_count": _nunique(computed_ready, "independent_unit"),
            "leakage_group_count": _nunique(computed_ready, "leakage_group"),
            "source_family_count": _nunique(computed_ready, "source_family_id"),
            "formulation_count": 0,
            "train_row_count": int(
                computed_ready["development_split"].eq("train").sum()
            ),
            "validation_row_count": int(
                computed_ready["development_split"].eq("validation").sum()
            ),
            "test_row_count": int(
                computed_ready["development_split"].eq("test").sum()
            ),
            "recommended_loss_weight_sum": float(
                computed_ready["recommended_loss_weight"].sum()
            ),
            "minimum_independent_formulations": 0,
            "current_chemistry_closed_groups": 0,
            "proxy_training_ready": bool(len(computed_ready) > 0),
            "strict_core_structure_model_ready": False,
            "recommended_first_use": "pretrain_or_multifidelity_residual_features",
            "strict_core_block_reason": "computed_values_are_not_macro_experiment_truth",
        }
    )
    return pd.DataFrame(rows)


def _source_map(ledger: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "package_id",
        "target_family",
        "row_count",
        "material_count",
        "mapping_tier",
        "model_admission_layer",
        "source_independence_status",
        "mapping_completeness_score",
        "license",
        "next_mapping_action",
    ]
    return ledger[columns].sort_values(
        ["target_family", "package_id"], kind="mergesort"
    ).reset_index(drop=True)


def _write_readme(summary: pd.DataFrame) -> str:
    objective_lines = []
    for row in summary.loc[summary["objective_id"].isin(OBJECTIVES)].itertuples():
        objective_lines.append(
            f"| `{row.objective_id}` | {row.model_ready_positive_weight_row_count:,} | "
            f"{row.independent_unit_count:,} | {row.source_family_count:,} | "
            f"{row.train_row_count:,}/{row.validation_row_count:,}/{row.test_row_count:,} | "
            f"{'可开始代理训练' if row.proxy_training_ready else '无正权重记录'} | "
            f"{'严格核心模型未闭合' if not row.strict_core_structure_model_ready else '核心结构模型门通过'} |"
        )
    objective_table = "\n".join(objective_lines)
    return f"""# 代理模型训练入口

本文件是训练前的任务化入口，不包含模型权重，也不启动 DFT/MD 或其他新计算。

## 当前就绪度

| 目标 | 正权重可用行 | 独立单元 | 来源族 | train/validation/test | 代理训练 | 严格核心结构模型 |
|---|---:|---:|---:|---:|---|---|
{objective_table}

三目标长表：`三目标实验标签.csv.gz`；计算辅助：`三目标计算证据.csv.gz`；扩充包映射：`代理训练数据源清单.csv`。所有行必须按已发布`development_split`和`leakage_group`使用，不能逐点随机切分。

## 最短启动片段

```python
import pandas as pd

labels = pd.read_csv("结果/定向筛选/三目标实验标签.csv.gz", low_memory=False)
objective = "toughness"  # 或 cyclic_recovery / thermal_stability
train = labels[
    labels["target_family"].eq(objective)
    & labels["model_ready"]
    & labels["recommended_loss_weight"].gt(0)
    & labels["development_split"].eq("train")
].copy()
X = train  # 先连接配方/结构/工艺特征，再交给模型
y = train["value"]
weight = train["recommended_loss_weight"]
groups = train["leakage_group"]
```

计算证据应作为多任务表示预训练、低保真目标或残差特征，并保留`method_family`、`temp`、`press`和`fidelity_level`；不能和实验真值无条件合并。曲线任务应按`curve_id`/`sample_id`/`leakage_group`整组拆分。

当前代理训练可以开始，但“SMILES/配方 → 三目标宏观性能”的严格核心模型仍受化学闭合数限制：韧性、循环恢复、热稳定分别只有有限的闭合配方组。迁移层（再生/老化泡沫、SHPU 等）只能低权重或外部验证使用，不能抬高核心 TPU 标签数量。

## 文件

- `代理训练任务统计.csv`：每个目标的行数、独立单元、来源、划分、权重和严格模型门。
- `代理训练数据源清单.csv`：67 个扩充包的目标、层级、映射等级和许可；异构包不在这里强行拼接。
- `代理模型训练输入发布清单.json`：输入/输出 SHA-256 和计数。
"""


def _manifest(
    summary: pd.DataFrame, source_map: pd.DataFrame, output_hashes: dict[str, str]
) -> dict[str, object]:
    return {
        "release_id": "tpu-proxy-training-entry-2026-08-31-v1",
        "policy": {
            "model_training_started": False,
            "new_calculation_started": False,
            "group_split_required": True,
            "experimental_and_computational_targets_unconditionally_merged": False,
            "transfer_layers_can_raise_core_tpu_count": False,
        },
        "inputs": {
            "labels": {
                "path": LABELS.relative_to(ROOT).as_posix(),
                "sha256": _sha256(LABELS),
            },
            "computed": {
                "path": COMPUTED.relative_to(ROOT).as_posix(),
                "sha256": _sha256(COMPUTED),
            },
            "tasks": {
                "path": TASKS.relative_to(ROOT).as_posix(),
                "sha256": _sha256(TASKS),
            },
            "ledger": {
                "path": LEDGER.relative_to(ROOT).as_posix(),
                "sha256": _sha256(LEDGER),
            },
        },
        "counts": {
            "objective_count": len(OBJECTIVES),
            "summary_row_count": len(summary),
            "source_package_row_count": len(source_map),
            "proxy_ready_objective_count": int(
                summary.loc[
                    summary["objective_id"].isin(OBJECTIVES), "proxy_training_ready"
                ].sum()
            ),
            "strict_core_structure_model_ready_objective_count": int(
                summary.loc[
                    summary["objective_id"].isin(OBJECTIVES),
                    "strict_core_structure_model_ready",
                ].sum()
            ),
        },
        "outputs": {
            key: {
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": output_hashes[key],
            }
            for key, path in {
                "summary": SUMMARY,
                "source_map": SOURCE_MAP,
                "readme": README,
            }.items()
        },
    }


def write_release() -> None:
    labels, computed, tasks, ledger = _load_inputs()
    summary = _summary(labels, computed, tasks)
    source_map = _source_map(ledger)
    readme = _write_readme(summary)
    DIRECTED.mkdir(parents=True, exist_ok=True)
    summary.to_csv(SUMMARY, index=False, encoding="utf-8-sig", lineterminator="\n")
    source_map.to_csv(
        SOURCE_MAP, index=False, encoding="utf-8-sig", lineterminator="\n"
    )
    README.write_text(readme, encoding="utf-8")
    output_hashes = {
        "summary": _sha256(SUMMARY),
        "source_map": _sha256(SOURCE_MAP),
        "readme": _sha256(README),
    }
    MANIFEST.write_text(
        json.dumps(_manifest(summary, source_map, output_hashes), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"summary_rows": len(summary), "source_packages": len(source_map)}, ensure_ascii=False))


def check_release() -> None:
    labels, computed, tasks, ledger = _load_inputs()
    summary = _summary(labels, computed, tasks)
    source_map = _source_map(ledger)
    expected_readme = _write_readme(summary)
    if not SUMMARY.exists() or not SOURCE_MAP.exists() or not README.exists() or not MANIFEST.exists():
        raise SystemExit("代理模型训练入口发布物不完整")
    with tempfile.TemporaryDirectory() as directory:
        candidate_summary = Path(directory) / SUMMARY.name
        candidate_source_map = Path(directory) / SOURCE_MAP.name
        summary.to_csv(
            candidate_summary,
            index=False,
            encoding="utf-8-sig",
            lineterminator="\n",
        )
        source_map.to_csv(
            candidate_source_map,
            index=False,
            encoding="utf-8-sig",
            lineterminator="\n",
        )
        if _sha256(candidate_summary) != _sha256(SUMMARY):
            raise SystemExit("代理训练任务统计无法确定性重建")
        if _sha256(candidate_source_map) != _sha256(SOURCE_MAP):
            raise SystemExit("代理训练数据源清单无法确定性重建")
    if README.read_text(encoding="utf-8") != expected_readme:
        raise SystemExit("代理模型训练说明无法确定性重建")
    hashes = {
        "summary": _sha256(SUMMARY),
        "source_map": _sha256(SOURCE_MAP),
        "readme": _sha256(README),
    }
    if json.loads(MANIFEST.read_text(encoding="utf-8")) != _manifest(
        summary, source_map, hashes
    ):
        raise SystemExit("代理模型训练入口发布清单不一致")
    print("代理模型训练入口检查通过")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    if args.检查:
        check_release()
    else:
        write_release()


if __name__ == "__main__":
    main()
