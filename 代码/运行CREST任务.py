"""在 Slurm 数组中运行一个可断点续算的 CREST/GFN2-xTB 构件任务。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from DFT任务 import sha256


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def completed_state(input_sha256: str, output_path: str = "crest_conformers.xyz") -> dict[str, Any]:
    return {
        "status": "completed",
        "input_sha256": input_sha256,
        "conformer_output": output_path,
    }


def should_skip(
    state: dict[str, Any] | None,
    input_sha256: str,
    result_root: Path | None = None,
) -> bool:
    if not state or state.get("status") != "completed":
        return False
    if state.get("input_sha256") != input_sha256:
        return False
    if result_root is None:
        return True
    relative_output = state.get("conformer_output")
    return isinstance(relative_output, str) and (result_root / relative_output).is_file()


def _read_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_state(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _software_version(executable: str) -> str:
    completed = subprocess.run(
        [executable, "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    text = "\n".join((completed.stdout, completed.stderr))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return next((line for line in lines if "version" in line.lower()), "unknown")


@contextmanager
def slurm_concurrency_slot(root: Path, threads: int):
    """按Slurm实际分配核数限制并发；非Slurm环境直接放行。

    提交脚本可能在排队期间被管理员或 ``scontrol`` 下调 CPU 数。包装器用
    文件锁把活跃 CREST 数限制为 ``SLURM_CPUS_PER_TASK // threads``，避免已
    入队脚本中的较大 ``xargs -P`` 造成CPU超配。
    """

    allocated = os.environ.get("SLURM_CPUS_PER_TASK")
    if not allocated or not os.environ.get("SLURM_JOB_ID"):
        yield
        return
    slot_count = max(1, int(allocated) // max(1, int(threads)))
    slot_root = root / ".并发槽"
    slot_root.mkdir(parents=True, exist_ok=True)
    import fcntl  # Linux/Slurm only; intentionally imported lazily for Windows tests.

    handle = None
    while handle is None:
        for index in range(slot_count):
            candidate = (slot_root / f"slot_{index:03d}.lock").open("a+", encoding="ascii")
            try:
                fcntl.flock(candidate.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                candidate.close()
                continue
            handle = candidate
            break
        if handle is None:
            time.sleep(1.0)
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def run_task(root: Path, index: int, threads: int, crest_executable: str) -> dict[str, Any]:
    tasks = pd.read_csv(root / "DFT任务清单.csv")
    selected = tasks.loc[tasks["task_index"].eq(index)]
    if len(selected) != 1:
        raise ValueError(f"任务索引 {index} 不唯一或不存在")
    task = selected.iloc[0]
    result_root = root / "结果" / str(task["task_slug"])
    result_root.mkdir(parents=True, exist_ok=True)
    state_path = result_root / "运行状态.json"
    if str(task["geometry_status"]) != "ready":
        state = {
            "status": "blocked_input_geometry",
            "task_index": int(index),
            "task_slug": str(task["task_slug"]),
            "candidate_id": str(task["candidate_id"]),
            "component_role": str(task["component_role"]),
            "failure_reason": str(task["geometry_error"]),
            "finished_utc": _utc_now(),
        }
        _write_state(state_path, state)
        print(f"blocked input geometry {index}: {task['candidate_id']}")
        return state
    input_path = root / str(task["initial_xyz_file"])
    input_hash = sha256(input_path)
    if input_hash != str(task["initial_xyz_sha256"]):
        raise ValueError(f"任务 {index} 初始 XYZ 哈希不匹配")

    old_state = _read_state(state_path)
    if should_skip(old_state, input_hash, result_root):
        print(f"skip completed task {index}: {task['candidate_id']}")
        return old_state

    existing_attempts = sorted(result_root.glob("尝试_*"))
    attempt_number = len(existing_attempts) + 1
    attempt_dir = result_root / f"尝试_{attempt_number:03d}"
    attempt_dir.mkdir()
    working_input = attempt_dir / "input.xyz"
    shutil.copy2(input_path, working_input)
    relative_attempt = attempt_dir.relative_to(result_root)
    relative_output = relative_attempt / "crest_conformers.xyz"
    command = [
        crest_executable,
        "input.xyz",
        "--gfn2",
        "--chrg",
        str(int(task["charge"])),
        "--uhf",
        str(int(task["uhf"])),
        "--T",
        str(int(threads)),
    ]
    state: dict[str, Any] = {
        "status": "running",
        "task_index": int(index),
        "task_slug": str(task["task_slug"]),
        "candidate_id": str(task["candidate_id"]),
        "component_role": str(task["component_role"]),
        "input_sha256": input_hash,
        "attempt": attempt_number,
        "attempt_directory": str(relative_attempt).replace("\\", "/"),
        "command": command,
        "threads": int(threads),
        "hostname": socket.gethostname(),
        "slurm_job_id": os.environ.get("SLURM_ARRAY_JOB_ID", os.environ.get("SLURM_JOB_ID", "")),
        "slurm_array_task_id": os.environ.get("SLURM_ARRAY_TASK_ID", ""),
        "started_utc": _utc_now(),
        "crest_version": _software_version(crest_executable),
    }
    _write_state(state_path, state)
    started = time.monotonic()
    log_path = attempt_dir / "crest.out"
    with log_path.open("w", encoding="utf-8", errors="replace") as stream:
        completed = subprocess.run(
            command,
            cwd=attempt_dir,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
        )
    output_path = result_root / relative_output
    state.update(
        {
            "finished_utc": _utc_now(),
            "runtime_seconds": round(time.monotonic() - started, 3),
            "exit_code": int(completed.returncode),
            "crest_log": str((relative_attempt / "crest.out")).replace("\\", "/"),
            "conformer_output": str(relative_output).replace("\\", "/"),
        }
    )
    if completed.returncode == 0 and output_path.is_file():
        state["status"] = "completed"
        state["output_sha256"] = sha256(output_path)
        state["output_bytes"] = output_path.stat().st_size
    else:
        state["status"] = "failed"
        state["failure_reason"] = (
            "nonzero_exit_code" if completed.returncode else "missing_crest_conformers_xyz"
        )
    _write_state(state_path, state)
    if state["status"] != "completed":
        raise RuntimeError(f"CREST 任务失败: {index}; {state['failure_reason']}")
    print(f"completed task {index}: {task['candidate_id']}")
    return state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--根目录", type=Path, required=True)
    parser.add_argument("--索引", type=int, required=True)
    parser.add_argument("--线程", type=int, default=4)
    parser.add_argument("--crest", default="crest")
    args = parser.parse_args()
    root = args.根目录.resolve()
    with slurm_concurrency_slot(root, args.线程):
        run_task(root, args.索引, args.线程, args.crest)


if __name__ == "__main__":
    main()
