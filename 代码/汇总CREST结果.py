"""汇总服务器 CREST 任务状态，不把未完成或失败任务填成数值。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


def _read_state(path: Path) -> tuple[str, dict[str, Any]]:
    if not path.is_file():
        return "pending", {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "invalid_state", {}
    if not isinstance(value, dict):
        return "invalid_state", {}
    return str(value.get("status", "invalid_state")), value


def count_xyz_conformers(path: Path) -> int:
    if not path.is_file():
        return 0
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    position = 0
    count = 0
    while position < len(lines):
        try:
            atoms = int(lines[position].strip())
        except ValueError:
            return 0
        next_position = position + atoms + 2
        if atoms <= 0 or next_position > len(lines):
            return 0
        count += 1
        position = next_position
    return count if position == len(lines) else 0


def collect_status(tasks: pd.DataFrame, result_root: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for task in tasks.itertuples(index=False):
        task_root = result_root / task.task_slug
        status, state = _read_state(task_root / "运行状态.json")
        output_relative = state.get("conformer_output", "")
        output_path = task_root / output_relative if output_relative else None
        rows.append(
            {
                "task_index": int(task.task_index),
                "task_slug": task.task_slug,
                "candidate_id": task.candidate_id,
                "component_role": task.component_role,
                "status": status,
                "attempt": state.get("attempt"),
                "exit_code": state.get("exit_code"),
                "runtime_seconds": state.get("runtime_seconds"),
                "conformer_count": count_xyz_conformers(output_path) if output_path else 0,
                "input_sha256": state.get("input_sha256", task.initial_xyz_sha256),
                "output_sha256": state.get("output_sha256"),
                "crest_version": state.get("crest_version"),
                "hostname": state.get("hostname"),
                "slurm_job_id": state.get("slurm_job_id"),
                "failure_reason": state.get("failure_reason"),
                "result_directory": str(task_root),
            }
        )
    return pd.DataFrame(rows).sort_values("task_index", kind="stable")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--根目录", type=Path, required=True)
    parser.add_argument("--输出", type=Path)
    args = parser.parse_args()
    root = args.根目录.resolve()
    output = args.输出 or (root / "CREST运行汇总.csv")
    tasks = pd.read_csv(root / "DFT任务清单.csv")
    summary = collect_status(tasks, root / "结果")
    summary.to_csv(output, index=False, float_format="%.12g")
    print(summary["status"].value_counts(dropna=False).to_dict())


if __name__ == "__main__":
    main()
