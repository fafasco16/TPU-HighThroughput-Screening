"""并行运行四类TPU局部片段的构象种子×ESP点密度RESP敏感性矩阵。"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
FRAGMENTS = [
    {
        "fragment_name": "methyl_n_methyl_carbamate",
        "validation_family": "aliphatic_urethane",
        "smiles": "COC(=O)NC",
    },
    {
        "fragment_name": "methyl_n_phenyl_carbamate",
        "validation_family": "aromatic_urethane",
        "smiles": "COC(=O)Nc1ccccc1",
    },
    {
        "fragment_name": "ethyl_isocyanate",
        "validation_family": "aliphatic_terminal_isocyanate",
        "smiles": "CCN=C=O",
    },
    {
        "fragment_name": "phenyl_isocyanate",
        "validation_family": "aromatic_terminal_isocyanate",
        "smiles": "O=C=Nc1ccccc1",
    },
]
DEFAULT_SEEDS = [20260825, 20260826, 20260827]
DEFAULT_DENSITIES = [0.5, 1.0, 2.0]


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _task_id(fragment_name: str, seed: int, density: float) -> str:
    payload = f"{fragment_name}\0{seed}\0{density:.6f}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def build_plan(
    seeds: Sequence[int], densities: Sequence[float]
) -> pd.DataFrame:
    if not seeds or not densities:
        raise ValueError("RESP敏感性种子和点密度不能为空")
    if len(set(seeds)) != len(seeds) or len(set(densities)) != len(densities):
        raise ValueError("RESP敏感性种子和点密度不得重复")
    if any(seed < 1 for seed in seeds) or any(density <= 0 for density in densities):
        raise ValueError("RESP敏感性种子和点密度必须为正")
    rows: list[dict[str, Any]] = []
    for fragment in FRAGMENTS:
        for seed in sorted(seeds):
            for density in sorted(densities):
                task_id = _task_id(fragment["fragment_name"], seed, density)
                rows.append(
                    {
                        "task_id": task_id,
                        **fragment,
                        "random_seed": seed,
                        "vdw_point_density": density,
                        "output_directory": f"任务_{task_id}",
                        "protocol": "HF_6-31Gd_two_stage_RESP",
                        "performance_claim_status": "no_performance_claim",
                    }
                )
    frame = pd.DataFrame(rows).sort_values("task_id", kind="stable").reset_index(
        drop=True
    )
    if not frame["task_id"].is_unique:
        raise ValueError("RESP敏感性task_id发生碰撞")
    return frame


def _existing_status(task: dict[str, Any], raw_root: Path) -> str | None:
    manifest_path = (
        raw_root / str(task["output_directory"]) / "RESP烟雾清单.json"
    )
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "existing_invalid_manifest"
    identity_ok = (
        manifest.get("fragment_name") == task["fragment_name"]
        and int(manifest.get("random_seed", -1)) == int(task["random_seed"])
        and abs(
            float(manifest.get("vdw_point_density", -1))
            - float(task["vdw_point_density"])
        )
        < 1.0e-12
    )
    if not identity_ok:
        return "existing_identity_mismatch"
    if manifest.get("status") == "completed_native_two_stage_resp_smoke":
        return "completed_existing"
    return "existing_noncompleted_no_automatic_retry"


def _run_one(
    task: dict[str, Any],
    *,
    raw_root: Path,
    log_root: Path,
    worker_script: Path,
    threads: int,
    memory_gb: int,
) -> dict[str, Any]:
    existing = _existing_status(task, raw_root)
    if existing is not None:
        return {**task, "batch_status": existing, "returncode": 0 if existing == "completed_existing" else -1}
    output_directory = raw_root / str(task["output_directory"])
    command = [
        sys.executable,
        str(worker_script),
        "--输出目录",
        str(output_directory),
        "--SMILES",
        str(task["smiles"]),
        "--片段名",
        str(task["fragment_name"]),
        "--点密度",
        str(task["vdw_point_density"]),
        "--线程",
        str(threads),
        "--内存GB",
        str(memory_gb),
        "--随机种子",
        str(task["random_seed"]),
        "--发布ID",
        f"tpu-resp-sensitivity-{task['task_id']}-v1",
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
        check=False,
    )
    log_root.mkdir(parents=True, exist_ok=True)
    _atomic_text(
        log_root / f"{task['task_id']}.log",
        "\n".join(
            [
                f"returncode={completed.returncode}",
                "[stdout]",
                completed.stdout,
                "[stderr]",
                completed.stderr,
            ]
        ),
    )
    manifest_status = _existing_status(task, raw_root)
    batch_status = (
        "completed_new"
        if completed.returncode == 0 and manifest_status == "completed_existing"
        else manifest_status or "failed_without_manifest"
    )
    return {
        **task,
        "batch_status": batch_status,
        "returncode": int(completed.returncode),
    }


def run_batch(
    raw_root: Path,
    *,
    seeds: Sequence[int],
    densities: Sequence[float],
    workers: int,
    threads_per_task: int,
    memory_gb_per_task: int,
    worker_script: Path,
) -> dict[str, Any]:
    if workers < 1 or threads_per_task < 1 or memory_gb_per_task < 1:
        raise ValueError("并发、线程和内存必须为正")
    if not worker_script.is_file():
        raise ValueError(f"RESP单任务脚本不存在: {worker_script}")
    raw_root.mkdir(parents=True, exist_ok=True)
    plan = build_plan(seeds, densities)
    plan_path = raw_root / "任务计划.csv"
    _atomic_text(plan_path, plan.to_csv(index=False, float_format="%.12g"))
    records = plan.to_dict(orient="records")
    log_root = raw_root / "日志"
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                _run_one,
                task,
                raw_root=raw_root,
                log_root=log_root,
                worker_script=worker_script,
                threads=threads_per_task,
                memory_gb=memory_gb_per_task,
            )
            for task in records
        ]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    status = pd.DataFrame(results).sort_values("task_id", kind="stable")
    status_path = raw_root / "任务状态.csv"
    _atomic_text(status_path, status.to_csv(index=False, float_format="%.12g"))
    completed_count = int(
        status["batch_status"].isin(["completed_new", "completed_existing"]).sum()
    )
    manifest = {
        "status": "completed" if completed_count == len(status) else "incomplete",
        "counts": {
            "planned": len(status),
            "completed": completed_count,
            "failed_or_blocked": len(status) - completed_count,
        },
        "protocol": {
            "fragment_count": len(FRAGMENTS),
            "seeds": sorted(seeds),
            "vdw_point_densities": sorted(densities),
            "workers": workers,
            "threads_per_task": threads_per_task,
            "memory_gb_per_task": memory_gb_per_task,
        },
    }
    _atomic_text(
        raw_root / "批次状态.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--原始目录", type=Path, required=True)
    parser.add_argument("--种子", type=int, action="append")
    parser.add_argument("--点密度", type=float, action="append")
    parser.add_argument("--并发", type=int, default=4)
    parser.add_argument("--每任务线程", type=int, default=1)
    parser.add_argument("--每任务内存GB", type=int, default=8)
    parser.add_argument(
        "--单任务脚本",
        type=Path,
        default=Path(__file__).with_name("运行RESP小片段烟雾.py"),
    )
    args = parser.parse_args(argv)
    manifest = run_batch(
        args.原始目录,
        seeds=args.种子 or DEFAULT_SEEDS,
        densities=args.点密度 or DEFAULT_DENSITIES,
        workers=args.并发,
        threads_per_task=args.每任务线程,
        memory_gb_per_task=args.每任务内存GB,
        worker_script=args.单任务脚本,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
