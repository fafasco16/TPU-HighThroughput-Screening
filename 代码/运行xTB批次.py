"""在独立实例上用常驻worker分片运行三万级xTB构象任务。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from 运行xTB构象任务 import XtbRunError, run_task


def assigned_task_indices(
    tasks: pd.DataFrame, worker_index: int, worker_count: int
) -> list[int]:
    if worker_count <= 0:
        raise ValueError("worker_count必须为正")
    if not 0 <= worker_index < worker_count:
        raise ValueError("worker_index必须落在[0, worker_count)内")
    if "xtb_task_index" not in tasks.columns:
        raise ValueError("任务表缺少xtb_task_index")
    indexes = pd.to_numeric(tasks["xtb_task_index"], errors="raise").astype(int)
    if not indexes.is_unique:
        raise ValueError("xtb_task_index不唯一")
    ordered = sorted(indexes.tolist())
    return ordered[worker_index::worker_count]


def run_batch(
    root: Path,
    worker_index: int,
    worker_count: int,
    xtb_executable: str,
) -> dict[str, Any]:
    tasks = pd.read_csv(root / "xTB构象任务清单.csv")
    indexes = assigned_task_indices(tasks, worker_index, worker_count)
    counts = {"completed_or_skipped": 0, "failed": 0}
    failures: list[dict[str, Any]] = []
    for index in indexes:
        try:
            state = run_task(
                root,
                index,
                xtb_executable,
                tasks=tasks,
            )
        except XtbRunError as exc:
            counts["failed"] += 1
            failures.append({"xtb_task_index": index, "error": str(exc)})
            print(f"failed xTB task {index}: {exc}", flush=True)
            continue
        if state.get("status") == "completed":
            counts["completed_or_skipped"] += 1
    summary = {
        "worker_index": worker_index,
        "worker_count": worker_count,
        "assigned": len(indexes),
        **counts,
        "failures": failures,
    }
    summary_root = root / "worker汇总"
    summary_root.mkdir(parents=True, exist_ok=True)
    output = summary_root / f"worker_{worker_index:03d}.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--根目录", type=Path, required=True)
    parser.add_argument("--worker索引", type=int, required=True)
    parser.add_argument("--worker总数", type=int, required=True)
    parser.add_argument("--xtb", default="xtb")
    args = parser.parse_args()
    run_batch(
        args.根目录.resolve(),
        args.worker索引,
        args.worker总数,
        args.xtb,
    )


if __name__ == "__main__":
    main()
