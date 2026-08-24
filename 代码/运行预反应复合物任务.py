"""运行带NCO–OH距离约束的GFN2-xTB预反应复合物优化任务。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd


EXPECTED_XTB_VERSION = "6.7.1"
HARTREE_TO_KCAL_MOL = 627.5094740631
_NORMAL_TERMINATION = re.compile(r"normal termination of xtb", re.IGNORECASE)


class PrereactionRunError(RuntimeError):
    """预反应任务未通过输入、运行或输出门。"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _resolve_executable(value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        located = shutil.which(value)
        if located is None:
            raise PrereactionRunError(f"找不到xTB可执行文件: {value}")
        resolved = Path(located).resolve()
    if not resolved.is_file():
        raise PrereactionRunError(f"xTB可执行文件不存在: {resolved}")
    return resolved


def _version(executable: Path) -> str:
    completed = subprocess.run(
        [str(executable), "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    text = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode != 0 or "6.7.1" not in text:
        raise PrereactionRunError("xTB版本门失败，要求6.7.1")
    return EXPECTED_XTB_VERSION


def _read_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PrereactionRunError(f"状态文件无效: {path}") from exc
    if not isinstance(value, dict):
        raise PrereactionRunError(f"状态文件不是对象: {path}")
    return value


def _read_xyz_distance(path: Path, first_1based: int, second_1based: int) -> float:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 3:
        raise PrereactionRunError("优化XYZ不完整")
    try:
        atom_count = int(lines[0].strip())
    except ValueError as exc:
        raise PrereactionRunError("优化XYZ原子数无效") from exc
    if atom_count <= 0 or len(lines) != atom_count + 2:
        raise PrereactionRunError("优化XYZ不是严格单帧")
    indexes = (first_1based - 1, second_1based - 1)
    if min(indexes) < 0 or max(indexes) >= atom_count:
        raise PrereactionRunError("反应位点索引越界")
    coordinates = []
    for index in indexes:
        fields = lines[index + 2].split()
        if len(fields) < 4:
            raise PrereactionRunError("优化XYZ原子行无效")
        try:
            coordinates.append(np.asarray([float(v) for v in fields[1:4]]))
        except ValueError as exc:
            raise PrereactionRunError("优化XYZ坐标无效") from exc
    distance = float(np.linalg.norm(coordinates[0] - coordinates[1]))
    if not math.isfinite(distance):
        raise PrereactionRunError("优化反应距离不是有限数")
    return distance


def _json_energy(path: Path) -> float:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        energy = float(value["total energy"])
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise PrereactionRunError("xtbout.json缺少有效总能量") from exc
    if not math.isfinite(energy):
        raise PrereactionRunError("xTB总能量不是有限数")
    return energy


def _task_row(tasks: pd.DataFrame, index: int) -> pd.Series:
    required = {
        "task_index",
        "task_slug",
        "pair_id",
        "pair_type",
        "diisocyanate_id",
        "oh_component_id",
        "geometry_status",
        "execution_permission",
        "nco_carbon_atom_index_1based",
        "oh_oxygen_atom_index_1based",
        "monomer_energy_sum_hartree",
        "charge",
        "uhf",
        "input_xyz_file",
        "input_xyz_sha256",
        "xcontrol_file",
        "xcontrol_sha256",
        "xtb_version",
        "xtb_binary_sha256",
        "method",
        "environment_model",
        "optimization_level",
    }
    missing = sorted(required.difference(tasks.columns))
    if missing:
        raise PrereactionRunError(f"预反应任务表缺少字段: {missing}")
    numeric = pd.to_numeric(tasks["task_index"], errors="raise").astype(int)
    if not numeric.is_unique:
        raise PrereactionRunError("预反应task_index不唯一")
    matches = tasks.loc[numeric.eq(int(index))]
    if len(matches) != 1:
        raise PrereactionRunError(f"预反应task_index不存在: {index}")
    return matches.iloc[0]


def _safe_input(root: Path, relative: str, expected_hash: str, label: str) -> Path:
    candidate = Path(str(relative))
    if candidate.is_absolute():
        raise PrereactionRunError(f"{label}路径必须为相对路径")
    resolved_root = root.resolve()
    resolved = (root / candidate).resolve()
    if resolved_root not in resolved.parents or not resolved.is_file():
        raise PrereactionRunError(f"{label}不存在或越出根目录")
    if sha256(resolved) != str(expected_hash):
        raise PrereactionRunError(f"{label} SHA-256不一致")
    return resolved


def _state_identity(row: pd.Series) -> dict[str, Any]:
    return {
        "task_index": int(row["task_index"]),
        "task_slug": str(row["task_slug"]),
        "pair_id": str(row["pair_id"]),
        "pair_type": str(row["pair_type"]),
        "diisocyanate_id": str(row["diisocyanate_id"]),
        "oh_component_id": str(row["oh_component_id"]),
        "input_sha256": str(row["input_xyz_sha256"]),
        "xcontrol_sha256": str(row["xcontrol_sha256"]),
    }


def _completed_is_valid(state: dict[str, Any], row: pd.Series, root: Path) -> bool:
    if state.get("status") != "completed":
        return False
    if any(state.get(key) != value for key, value in _state_identity(row).items()):
        return False
    output = state.get("output_sha256")
    attempt = state.get("attempt_directory")
    if not isinstance(output, dict) or not isinstance(attempt, str):
        return False
    attempt_root = root / attempt
    return all(
        (attempt_root / name).is_file()
        and sha256(attempt_root / name) == digest
        for name, digest in output.items()
    )


def run_task(
    root: Path,
    index: int,
    xtb_executable: str,
    *,
    tasks: pd.DataFrame | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    if tasks is None:
        tasks = pd.read_csv(root / "预反应复合物任务.csv")
    row = _task_row(tasks, index)
    state_path = root / "状态" / f"{row['task_slug']}.json"
    old_state = _read_state(state_path)
    if old_state is not None and old_state.get("status") == "failed":
        raise PrereactionRunError("已有失败状态，后续尝试必须由人工决定")
    if old_state is not None and _completed_is_valid(old_state, row, root):
        return old_state
    identity = _state_identity(row)
    if str(row["geometry_status"]) != "ready" or str(row["execution_permission"]) != "allowed":
        state = {
            **identity,
            "status": "blocked_input_geometry",
            "failure_reason": str(row["geometry_status"]),
            "finished_utc": _utc_now(),
        }
        _atomic_json(state_path, state)
        return state
    input_path = _safe_input(
        root, str(row["input_xyz_file"]), str(row["input_xyz_sha256"]), "复合物XYZ"
    )
    control_path = _safe_input(
        root, str(row["xcontrol_file"]), str(row["xcontrol_sha256"]), "xcontrol"
    )
    executable = _resolve_executable(xtb_executable)
    actual_binary_hash = sha256(executable)
    if actual_binary_hash != str(row["xtb_binary_sha256"]):
        raise PrereactionRunError("xTB二进制SHA-256不一致")
    version = _version(executable)
    if str(row["xtb_version"]) != version or str(row["method"]) != "GFN2-xTB":
        raise PrereactionRunError("xTB方法/版本任务门失败")
    if str(row["environment_model"]) != "gas_phase":
        raise PrereactionRunError("预反应运行器当前只接受gas_phase")

    work_root = root / "工作" / str(row["task_slug"])
    work_root.mkdir(parents=True, exist_ok=True)
    if list(work_root.glob("尝试_*")):
        raise PrereactionRunError("已有未完成尝试，禁止自动重复提交")
    attempt_root = work_root / "尝试_001"
    attempt_root.mkdir()
    shutil.copy2(input_path, attempt_root / "complex.xyz")
    shutil.copy2(control_path, attempt_root / "constraints.inp")
    command = [
        str(executable),
        "complex.xyz",
        "--opt",
        str(row["optimization_level"]),
        "--gfn",
        "2",
        "--chrg",
        str(int(row["charge"])),
        "--uhf",
        str(int(row["uhf"])),
        "--input",
        "constraints.inp",
        "--json",
        "--pop",
        "--dipole",
        "--wbo",
        "--norestart",
        "-P",
        "1",
    ]
    state = {
        **identity,
        "status": "running",
        "attempt": 1,
        "attempt_directory": attempt_root.relative_to(root).as_posix(),
        "command": command,
        "xtb_version": version,
        "xtb_binary_sha256": actual_binary_hash,
        "threads": 1,
        "hostname": socket.gethostname(),
        "started_utc": _utc_now(),
    }
    _atomic_json(state_path, state)
    started = time.monotonic()
    log_path = attempt_root / "xtb.out"
    with log_path.open("w", encoding="utf-8", errors="replace") as stream:
        completed = subprocess.run(
            command,
            cwd=attempt_root,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
        )
    state.update(
        finished_utc=_utc_now(),
        runtime_seconds=round(time.monotonic() - started, 3),
        exit_code=int(completed.returncode),
    )
    failure = ""
    required_outputs = ["xtbopt.xyz", "xtbout.json", "xtb.out", "wbo"]
    if completed.returncode != 0:
        failure = "nonzero_exit_code"
    elif not all((attempt_root / name).is_file() for name in required_outputs):
        failure = "missing_required_output"
    elif not _NORMAL_TERMINATION.search(log_path.read_text(encoding="utf-8", errors="replace")):
        failure = "missing_normal_termination"
    else:
        try:
            energy = _json_energy(attempt_root / "xtbout.json")
            distance = _read_xyz_distance(
                attempt_root / "xtbopt.xyz",
                int(row["nco_carbon_atom_index_1based"]),
                int(row["oh_oxygen_atom_index_1based"]),
            )
        except PrereactionRunError as exc:
            failure = str(exc)
        else:
            if not 2.3 <= distance <= 3.1:
                failure = "final_reactive_distance_outside_constraint_gate"
            else:
                monomer_sum = float(row["monomer_energy_sum_hartree"])
                state.update(
                    complex_total_energy_hartree=energy,
                    monomer_energy_sum_hartree=monomer_sum,
                    association_energy_proxy_kcal_mol=(
                        energy - monomer_sum
                    )
                    * HARTREE_TO_KCAL_MOL,
                    final_reactive_distance_a=distance,
                )
    if failure:
        state.update(status="failed", failure_reason=failure)
        _atomic_json(state_path, state)
        raise PrereactionRunError(f"预反应任务失败: {index}; {failure}")
    state["output_sha256"] = {
        name: sha256(attempt_root / name) for name in required_outputs
    }
    state["status"] = "completed"
    _atomic_json(state_path, state)
    return state


def assigned_task_indices(
    tasks: pd.DataFrame, worker_index: int, worker_count: int
) -> list[int]:
    if worker_count <= 0:
        raise ValueError("worker_count必须为正")
    if not 0 <= worker_index < worker_count:
        raise ValueError("worker_index必须落在[0, worker_count)内")
    indexes = sorted(
        pd.to_numeric(tasks["task_index"], errors="raise").astype(int).tolist()
    )
    return indexes[worker_index::worker_count]


def run_batch(
    root: Path, worker_index: int, worker_count: int, xtb_executable: str
) -> dict[str, Any]:
    tasks = pd.read_csv(root / "预反应复合物任务.csv")
    indexes = assigned_task_indices(tasks, worker_index, worker_count)
    counts = {"completed_or_blocked": 0, "failed": 0}
    failures: list[dict[str, Any]] = []
    for index in indexes:
        try:
            state = run_task(root, index, xtb_executable, tasks=tasks)
        except PrereactionRunError as exc:
            counts["failed"] += 1
            failures.append({"task_index": index, "error": str(exc)})
            continue
        if state.get("status") in {"completed", "blocked_input_geometry"}:
            counts["completed_or_blocked"] += 1
    summary = {
        "worker_index": worker_index,
        "worker_count": worker_count,
        "assigned": len(indexes),
        **counts,
        "failures": failures,
    }
    _atomic_json(root / "worker汇总" / f"worker_{worker_index:03d}.json", summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--根目录", type=Path, required=True)
    parser.add_argument("--索引", type=int)
    parser.add_argument("--worker索引", type=int)
    parser.add_argument("--worker总数", type=int)
    parser.add_argument("--xtb", default="xtb")
    args = parser.parse_args(argv)
    if args.索引 is not None:
        run_task(args.根目录, args.索引, args.xtb)
        return 0
    if args.worker索引 is None or args.worker总数 is None:
        parser.error("批次模式必须同时提供--worker索引和--worker总数")
    summary = run_batch(args.根目录, args.worker索引, args.worker总数, args.xtb)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
