"""按预注册统计门验证商业对照MD重复，不依赖目视判定。"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from 汇总RESP敏感性 import sha256


ROOT = Path(__file__).resolve().parents[1]
RUN_KEYS = ["formulation_id", "replica_index"]
TIMESERIES_COLUMNS = [
    *RUN_KEYS,
    "time_ps",
    "density_g_cm3",
    "potential_energy_kcal_mol",
    "volume_a3",
    "radius_of_gyration_a",
    "end_to_end_distance_a",
    "temperature_k",
    "pressure_atm",
]


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _split_difference(values: np.ndarray, *, relative_to_mean: bool) -> float:
    left, right = np.array_split(np.asarray(values, dtype=float), 2)
    difference = abs(float(left.mean()) - float(right.mean()))
    if relative_to_mean:
        denominator = abs(float(np.mean(values)))
    else:
        denominator = float(np.std(values, ddof=1))
    if denominator <= np.finfo(float).eps:
        return 0.0 if difference <= np.finfo(float).eps else math.inf
    return difference / denominator


def _block_sem_fraction(values: np.ndarray, block_count: int = 5) -> float:
    data = np.asarray(values, dtype=float)
    if len(data) < block_count * 2:
        return math.inf
    block_means = np.asarray(
        [block.mean() for block in np.array_split(data, block_count)], dtype=float
    )
    denominator = abs(float(data.mean()))
    if denominator <= np.finfo(float).eps:
        return math.inf
    return float(block_means.std(ddof=1) / math.sqrt(block_count) / denominator)


def analyze_replica(
    frame: pd.DataFrame,
    *,
    minimum_duration_ps: float = 10_000.0,
    minimum_points: int = 1001,
    density_slope_limit_fraction_per_ns: float = 0.001,
    density_split_mean_limit_fraction: float = 0.01,
    density_block_sem_limit_fraction: float = 0.01,
    rg_split_mean_limit_fraction: float = 0.05,
    energy_split_mean_limit_sd: float = 0.5,
) -> dict[str, Any]:
    missing = sorted(set(TIMESERIES_COLUMNS).difference(frame.columns))
    if missing:
        raise ValueError(f"商业对照MD轨迹缺字段: {missing}")
    identities = frame[RUN_KEYS].drop_duplicates()
    if len(identities) != 1:
        raise ValueError("商业对照MD单重复输入包含多个身份")
    data = frame.sort_values("time_ps", kind="stable").copy()
    if data["time_ps"].duplicated().any():
        raise ValueError("商业对照MD单重复时间点重复")
    numeric_columns = [column for column in TIMESERIES_COLUMNS if column not in RUN_KEYS]
    numeric = data[numeric_columns].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(numeric.to_numpy(float)).all():
        raise ValueError("商业对照MD轨迹存在非有限数值")
    if not numeric["density_g_cm3"].gt(0).all() or not numeric["volume_a3"].gt(0).all():
        raise ValueError("商业对照MD密度或体积非正")
    time = numeric["time_ps"].to_numpy(float)
    duration = float(time[-1] - time[0]) if len(time) else 0.0
    half_start = time[0] + 0.5 * duration if len(time) else 0.0
    production = numeric.loc[numeric["time_ps"].ge(half_start)].copy()
    density = production["density_g_cm3"].to_numpy(float)
    time_ns = production["time_ps"].to_numpy(float) / 1000.0
    density_mean = float(density.mean()) if len(density) else math.nan
    density_slope = (
        float(np.polyfit(time_ns, density, 1)[0]) if len(density) >= 2 else math.inf
    )
    density_slope_fraction = abs(density_slope) / abs(density_mean)
    density_split = _split_difference(density, relative_to_mean=True)
    density_sem = _block_sem_fraction(density)
    rg_split = _split_difference(
        production["radius_of_gyration_a"].to_numpy(float), relative_to_mean=True
    )
    energy_split = _split_difference(
        production["potential_energy_kcal_mol"].to_numpy(float),
        relative_to_mean=False,
    )
    gates = {
        "duration_gate": duration >= minimum_duration_ps,
        "point_count_gate": len(data) >= minimum_points,
        "density_slope_gate": density_slope_fraction
        <= density_slope_limit_fraction_per_ns,
        "density_split_mean_gate": density_split
        <= density_split_mean_limit_fraction,
        "density_block_sem_gate": density_sem <= density_block_sem_limit_fraction,
        "rg_split_mean_gate": rg_split <= rg_split_mean_limit_fraction,
        "energy_split_mean_gate": energy_split <= energy_split_mean_limit_sd,
    }
    identity = identities.iloc[0]
    return {
        "formulation_id": identity["formulation_id"],
        "replica_index": int(identity["replica_index"]),
        "point_count": len(data),
        "duration_ps": duration,
        "production_half_point_count": len(production),
        "density_mean_g_cm3": density_mean,
        "density_slope_fraction_per_ns": density_slope_fraction,
        "density_split_mean_difference_fraction": density_split,
        "density_block_sem_fraction": density_sem,
        "rg_split_mean_difference_fraction": rg_split,
        "energy_split_mean_difference_sd": energy_split,
        **gates,
        "replica_convergence_pass": all(gates.values()),
    }


def summarize_systems(
    replica_metrics: pd.DataFrame,
    *,
    required_replicates: int = 3,
    density_between_replica_cv_limit: float = 0.03,
) -> pd.DataFrame:
    required = {
        *RUN_KEYS,
        "density_mean_g_cm3",
        "replica_convergence_pass",
    }
    missing = sorted(required.difference(replica_metrics.columns))
    if missing:
        raise ValueError(f"商业对照MD重复指标缺字段: {missing}")
    rows = []
    for formulation_id, subset in replica_metrics.groupby("formulation_id", sort=True):
        if len(subset) != required_replicates or not subset["replica_index"].is_unique:
            raise ValueError(f"商业对照MD重复数不闭合: {formulation_id}")
        density = subset["density_mean_g_cm3"].to_numpy(float)
        density_cv = float(density.std(ddof=1) / density.mean())
        all_replica_pass = bool(subset["replica_convergence_pass"].all())
        rows.append(
            {
                "formulation_id": formulation_id,
                "replicate_count": len(subset),
                "replicates_passed": int(subset["replica_convergence_pass"].sum()),
                "all_replicas_pass": all_replica_pass,
                "density_mean_across_replicas_g_cm3": float(density.mean()),
                "density_between_replica_cv": density_cv,
                "density_between_replica_cv_gate": density_cv
                <= density_between_replica_cv_limit,
                "system_convergence_pass": all_replica_pass
                and density_cv <= density_between_replica_cv_limit,
            }
        )
    return pd.DataFrame(rows)


def write_release(
    plan_path: Path,
    timeseries_path: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    for path in (plan_path, timeseries_path):
        if not path.is_file():
            raise ValueError(f"商业对照MD收敛输入不存在: {path}")
    plan = pd.read_csv(plan_path)
    timeseries = pd.read_csv(timeseries_path)
    expected_runs = plan[RUN_KEYS].drop_duplicates()
    observed_runs = timeseries[RUN_KEYS].drop_duplicates()
    merged = expected_runs.merge(observed_runs, on=RUN_KEYS, how="outer", indicator=True)
    if not merged["_merge"].eq("both").all():
        raise ValueError("商业对照MD计划与轨迹运行身份不闭合")
    metrics = pd.DataFrame(
        [
            analyze_replica(subset)
            for _, subset in timeseries.groupby(RUN_KEYS, sort=True)
        ]
    )
    systems = summarize_systems(metrics)
    output_root.mkdir(parents=True, exist_ok=True)
    metrics_out = output_root / "商业对照MD逐重复收敛.csv"
    systems_out = output_root / "商业对照MD逐体系收敛.csv"
    report_out = output_root / "商业对照MD收敛说明.md"
    _atomic_text(metrics_out, metrics.to_csv(index=False, float_format="%.12g"))
    _atomic_text(systems_out, systems.to_csv(index=False, float_format="%.12g"))
    all_pass = bool(systems["system_convergence_pass"].all())
    _atomic_text(
        report_out,
        "\n".join(
            [
                "# 商业对照MD收敛验证",
                "",
                "只分析预注册的两个体系×三个重复。单重复要求至少10 ns/1001点，并检查后半段密度斜率、分段均值、块标准误、Rg和势能漂移；体系层要求三个重复全部通过且密度重复间CV≤3%。",
                "",
                (
                    "全部体系通过统计收敛门；这仍不等于实验准确性，必须继续与真实合成对照校准。"
                    if all_pass
                    else "至少一个体系或重复未通过；不得只删除失败重复或只汇报通过结果。"
                ),
                "",
            ]
        ),
    )
    files = [metrics_out, systems_out, report_out]
    manifest = {
        "release_id": release_id,
        "status": (
            "commercial_control_md_statistical_convergence_passed_experimental_calibration_pending"
            if all_pass
            else "commercial_control_md_statistical_convergence_failed"
        ),
        "counts": {
            "runs": len(metrics),
            "systems": len(systems),
            "runs_passed": int(metrics["replica_convergence_pass"].sum()),
            "systems_passed": int(systems["system_convergence_pass"].sum()),
        },
        "thresholds": {
            "minimum_duration_ps": 10_000,
            "minimum_points": 1001,
            "density_slope_fraction_per_ns": 0.001,
            "density_split_mean_difference_fraction": 0.01,
            "density_block_sem_fraction": 0.01,
            "rg_split_mean_difference_fraction": 0.05,
            "energy_split_mean_difference_sd": 0.5,
            "density_between_replica_cv": 0.03,
        },
        "inputs": {
            path.name: {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in (plan_path, timeseries_path)
        },
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "performance_claim_status": "no_experimental_accuracy_claim_before_real_control_calibration",
    }
    _atomic_text(
        output_root / "商业对照MD收敛发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--计划", type=Path, required=True)
    parser.add_argument("--轨迹", type=Path, required=True)
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument("--发布ID", required=True)
    args = parser.parse_args(argv)
    manifest = write_release(
        args.计划, args.轨迹, args.输出目录, release_id=args.发布ID
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0 if manifest["status"].endswith("experimental_calibration_pending") else 1


if __name__ == "__main__":
    raise SystemExit(main())
