"""在独立目录中运行一个可断点续算的 xTB 6.7.1 构象单点任务。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import socket
import subprocess
import tarfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from xTB系综任务 import EXPECTED_XTB_VERSION, METHOD, atom_order_sha256, sha256, split_crest_xyz


class XtbRunError(RuntimeError):
    """单构象 xTB 任务不能安全运行或发布。"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_state(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def _resolve_executable(executable: str) -> Path:
    found = shutil.which(executable)
    candidate = Path(found) if found else Path(executable)
    if not candidate.is_file():
        raise XtbRunError(f"xTB executable not found: {executable}")
    return candidate.resolve()


def _version(executable: Path) -> str:
    completed = subprocess.run(
        [str(executable), "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    text = "\n".join((completed.stdout or "", completed.stderr or ""))
    if completed.returncode != 0 or EXPECTED_XTB_VERSION not in text:
        raise XtbRunError(
            f"xTB version gate failed; expected {EXPECTED_XTB_VERSION}"
        )
    return EXPECTED_XTB_VERSION


def _output_hashes(attempt_dir: Path) -> dict[str, str] | None:
    required = ("xtbout.json", "xtb.out", "wbo")
    if not all((attempt_dir / name).is_file() for name in required):
        return None
    optional = tuple(
        name for name in ("charges", "xtbtopo.mol") if (attempt_dir / name).is_file()
    )
    return {name: sha256(attempt_dir / name) for name in (*required, *optional)}


def _normal_termination(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        lines = [line.strip().lower() for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    except OSError:
        return False
    return bool(lines) and lines[-1] == "normal termination of xtb"


def _task_shard(conformer_id: str) -> str:
    digest = conformer_id.removeprefix("cf_")
    if len(digest) < 2 or any(character not in "0123456789abcdef" for character in digest):
        raise XtbRunError("invalid conformer_id for sharded storage")
    return digest[:2]


def task_layout(root: Path, row: pd.Series) -> dict[str, Path]:
    """把数万任务分散到固定分片，避免为每个成功任务永久保留目录。"""

    slug = str(row["xtb_task_slug"])
    if not re.fullmatch(r"\d{6}_cf_[0-9a-f]{20}", slug):
        raise XtbRunError("invalid xtb_task_slug for sharded storage")
    shard = _task_shard(str(row["conformer_id"]))
    return {
        "state": root / "状态" / shard / f"{slug}.json",
        "lock": root / "锁" / shard / f"{slug}.lock",
        "work": root / "工作" / shard / slug,
        "archive": root / "结果包" / shard / f"{slug}.tar.gz",
    }


def _remove_verified_work_root(root: Path, work_root: Path) -> None:
    """仅允许删除 ``root/工作/<shard>/<task>`` 这一受控层级。"""

    allowed_root = (root / "工作").resolve()
    target = work_root.resolve()
    if target == allowed_root or allowed_root not in target.parents:
        raise XtbRunError("refusing to clean work directory outside controlled root")
    relative = target.relative_to(allowed_root)
    if len(relative.parts) != 2 or not re.fullmatch(r"[0-9a-f]{2}", relative.parts[0]):
        raise XtbRunError("refusing to clean unexpected work directory layout")
    shutil.rmtree(target)


def _safe_archive_path(root: Path, relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative:
        return None
    candidate = Path(relative)
    if candidate.is_absolute():
        return None
    resolved_root = root.resolve()
    resolved = (root / candidate).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        return None
    return resolved


def _verify_archive(path: Path, expected_hashes: dict[str, str]) -> bool:
    if not path.is_file():
        return False
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = {member.name: member for member in archive.getmembers() if member.isfile()}
            if set(members) != set(expected_hashes):
                return False
            for name, expected in expected_hashes.items():
                stream = archive.extractfile(members[name])
                if stream is None:
                    return False
                actual = hashlib.sha256(stream.read()).hexdigest()
                if actual != expected:
                    return False
    except (OSError, tarfile.TarError):
        return False
    return True


def _package_success(
    attempt_dir: Path, archive_path: Path, output_hashes: dict[str, str]
) -> tuple[str, dict[str, str]]:
    """打包并复核成功输出；只有复核后调用方才可清理临时工作目录。"""

    member_hashes = {
        "conformer.xyz": sha256(attempt_dir / "conformer.xyz"),
        **output_hashes,
    }
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive_path.with_name(archive_path.name + ".tmp")
    with tarfile.open(temporary, "w:gz") as archive:
        for name in sorted(member_hashes):
            archive.add(attempt_dir / name, arcname=name, recursive=False)
    if not _verify_archive(temporary, member_hashes):
        raise XtbRunError("result archive verification failed")
    temporary.replace(archive_path)
    return sha256(archive_path), member_hashes


def should_skip(state: dict[str, Any] | None, row: pd.Series, root: Path) -> bool:
    if not state or state.get("status") != "completed":
        return False
    identity = {
        "conformer_id": str(row["conformer_id"]),
        "input_sha256": str(row["conformer_xyz_sha256"]),
        "xtb_version": str(row["xtb_version"]),
        "xtb_binary_sha256": str(row["xtb_binary_sha256"]),
        "method": str(row["method"]),
    }
    if any(state.get(key) != value for key, value in identity.items()):
        return False
    archive_path = _safe_archive_path(root, state.get("archive_file"))
    if archive_path is None or sha256(archive_path) != state.get("archive_sha256"):
        return False
    member_hashes = state.get("archive_member_sha256")
    if not isinstance(member_hashes, dict):
        return False
    return _verify_archive(archive_path, member_hashes)


@contextmanager
def task_lock(lock_path: Path):
    """用 O_EXCL 锁阻止同一构象被两个进程同时写入。"""

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise XtbRunError(f"task is already locked: {lock_path.stem}") from exc
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.close(descriptor)
        descriptor = -1
        yield
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        lock_path.unlink(missing_ok=True)


def _validate_task_input(root: Path, row: pd.Series) -> Path:
    input_path = (root / str(row["conformer_xyz_file"])).resolve()
    if not input_path.is_file() or sha256(input_path) != str(row["conformer_xyz_sha256"]):
        raise XtbRunError("conformer input SHA-256 mismatch or file missing")
    frames = split_crest_xyz(input_path)
    if len(frames) != 1:
        raise XtbRunError("xTB task input must contain exactly one XYZ frame")
    frame = frames[0]
    if frame.atom_count != int(row["atom_count"]):
        raise XtbRunError("conformer atom count mismatch")
    if atom_order_sha256(frame.elements) != str(row["atom_order_sha256"]):
        raise XtbRunError("conformer atom order mismatch")
    return input_path


def _validate_json(path: Path) -> None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        energy = float(value["total energy"])
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise XtbRunError("invalid xtbout.json or missing total energy") from exc
    if not isinstance(value, dict) or not math.isfinite(energy):
        raise XtbRunError("invalid xtbout.json total energy")


def run_task(root: Path, index: int, xtb_executable: str = "xtb") -> dict[str, Any]:
    task_path = root / "xTB构象任务清单.csv"
    tasks = pd.read_csv(task_path)
    required = {
        "xtb_task_index",
        "xtb_task_slug",
        "candidate_id",
        "component_role",
        "conformer_id",
        "conformer_xyz_file",
        "conformer_xyz_sha256",
        "atom_count",
        "atom_order_sha256",
        "charge",
        "uhf",
        "xtb_version",
        "xtb_binary_sha256",
        "method",
        "environment_model",
        "electronic_temperature_k",
    }
    missing = required.difference(tasks.columns)
    if missing:
        raise XtbRunError(f"xTB task table missing fields: {sorted(missing)}")
    selected = tasks.loc[tasks["xtb_task_index"].eq(index)]
    if len(selected) != 1:
        raise XtbRunError(f"xTB task index {index} is absent or non-unique")
    row = selected.iloc[0]
    if str(row["xtb_version"]) != EXPECTED_XTB_VERSION or str(row["method"]) != METHOD:
        raise XtbRunError("xTB method/version task gate failed")
    if str(row["environment_model"]) != "gas_phase":
        raise XtbRunError("this runner only accepts gas_phase tasks")
    if float(row["electronic_temperature_k"]) != 300.0:
        raise XtbRunError("electronic temperature must be 300 K")

    executable = _resolve_executable(xtb_executable)
    actual_binary_hash = sha256(executable)
    if actual_binary_hash != str(row["xtb_binary_sha256"]):
        raise XtbRunError("xTB binary SHA-256 mismatch")
    input_path = _validate_task_input(root, row)
    layout = task_layout(root, row)

    with task_lock(layout["lock"]):
        old_state = _read_state(layout["state"])
        if should_skip(old_state, row, root):
            print(f"skip completed xTB task {index}: {row['conformer_id']}")
            return old_state
        verified_version = _version(executable)
        work_root = layout["work"]
        work_root.mkdir(parents=True, exist_ok=True)
        attempts = sorted(work_root.glob("尝试_*"))
        attempt_number = len(attempts) + 1
        attempt_dir = work_root / f"尝试_{attempt_number:03d}"
        attempt_dir.mkdir()
        shutil.copy2(input_path, attempt_dir / "conformer.xyz")
        relative_attempt = attempt_dir.relative_to(root).as_posix()
        command = [
            str(executable),
            "conformer.xyz",
            "--sp",
            "--gfn",
            "2",
            "--chrg",
            str(int(row["charge"])),
            "--uhf",
            str(int(row["uhf"])),
            "--acc",
            "0.5",
            "--iterations",
            "500",
            "--etemp",
            "300",
            "--pop",
            "--dipole",
            "--wbo",
            "--json",
            "--norestart",
            "-P",
            "1",
        ]
        state: dict[str, Any] = {
            "status": "running",
            "xtb_task_index": int(index),
            "xtb_task_slug": str(row["xtb_task_slug"]),
            "candidate_id": str(row["candidate_id"]),
            "component_role": str(row["component_role"]),
            "conformer_id": str(row["conformer_id"]),
            "input_sha256": str(row["conformer_xyz_sha256"]),
            "atom_order_sha256": str(row["atom_order_sha256"]),
            "charge": int(row["charge"]),
            "uhf": int(row["uhf"]),
            "method": METHOD,
            "environment_model": "gas_phase",
            "xtb_version": verified_version,
            "xtb_binary_sha256": actual_binary_hash,
            "command": command,
            "threads": 1,
            "attempt": attempt_number,
            "attempt_directory": relative_attempt,
            "hostname": socket.gethostname(),
            "slurm_job_id": os.environ.get("SLURM_ARRAY_JOB_ID", os.environ.get("SLURM_JOB_ID", "")),
            "slurm_array_task_id": os.environ.get("SLURM_ARRAY_TASK_ID", ""),
            "started_utc": _utc_now(),
        }
        state_path = layout["state"]
        _write_state(state_path, state)
        started = time.monotonic()
        log_path = attempt_dir / "xtb.out"
        with log_path.open("w", encoding="utf-8", errors="replace") as stream:
            completed = subprocess.run(
                command,
                cwd=attempt_dir,
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=False,
            )
        state.update(
            finished_utc=_utc_now(),
            runtime_seconds=round(time.monotonic() - started, 3),
            exit_code=int(completed.returncode),
        )
        failure_reason = ""
        if completed.returncode != 0:
            failure_reason = "nonzero_exit_code"
        elif (attempt_dir / ".sccnotconverged").exists():
            failure_reason = "scc_not_converged"
        elif not _normal_termination(log_path):
            failure_reason = "missing_normal_termination"
        elif _output_hashes(attempt_dir) is None:
            failure_reason = "missing_required_output"
        else:
            try:
                _validate_json(attempt_dir / "xtbout.json")
            except XtbRunError as exc:
                failure_reason = str(exc)
        if failure_reason:
            state.update(status="failed", failure_reason=failure_reason)
            _write_state(state_path, state)
            raise XtbRunError(f"xTB task failed: {index}; {failure_reason}")
        output_hashes = _output_hashes(attempt_dir)
        assert output_hashes is not None
        archive_hash, member_hashes = _package_success(
            attempt_dir, layout["archive"], output_hashes
        )
        state.update(
            status="completed",
            output_sha256=output_hashes,
            archive_file=layout["archive"].relative_to(root).as_posix(),
            archive_sha256=archive_hash,
            archive_member_sha256=member_hashes,
            work_directory_cleaned=False,
        )
        _write_state(state_path, state)
        # 成功证据已写入并复核压缩包后，才移除临时工作目录。失败路径不执行清理。
        try:
            _remove_verified_work_root(root, work_root)
        except OSError as exc:
            state["cleanup_warning"] = (
                f"verified_archive_retained_but_work_cleanup_failed:{exc}"
            )
        else:
            state["work_directory_cleaned"] = True
        _write_state(state_path, state)
        print(f"completed xTB task {index}: {row['conformer_id']}")
        return state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--根目录", type=Path, required=True)
    parser.add_argument("--索引", type=int, required=True)
    parser.add_argument("--xtb", default="xtb")
    args = parser.parse_args()
    run_task(args.根目录.resolve(), args.索引, args.xtb)


if __name__ == "__main__":
    main()
