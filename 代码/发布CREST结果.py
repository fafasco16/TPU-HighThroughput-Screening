"""审计、精简打包并发布服务器 CREST/GFN2-xTB 结果。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import tarfile
from pathlib import Path
from typing import Any

import pandas as pd

from 汇总CREST结果 import count_xyz_conformers


TERMINAL_STATES = {"completed", "blocked_input_geometry"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def audit_results(tasks: pd.DataFrame, result_root: Path) -> pd.DataFrame:
    """逐任务核验状态、输入身份、构象输出和状态中记录的哈希。"""

    required = {
        "task_index",
        "task_slug",
        "candidate_id",
        "component_role",
        "geometry_status",
        "initial_xyz_sha256",
    }
    missing = required.difference(tasks.columns)
    if missing:
        raise ValueError(f"DFT任务清单缺少字段: {sorted(missing)}")
    if not tasks["task_slug"].is_unique:
        raise ValueError("DFT任务 task_slug 不唯一")

    rows: list[dict[str, Any]] = []
    for task in tasks.itertuples(index=False):
        task_root = result_root / task.task_slug
        state_path = task_root / "运行状态.json"
        status, state = _read_state(state_path)
        output_relative = state.get("conformer_output", "")
        output_path = task_root / output_relative if output_relative else None
        actual_output_hash = (
            sha256(output_path) if output_path is not None and output_path.is_file() else ""
        )
        conformer_count = (
            count_xyz_conformers(output_path)
            if output_path is not None and output_path.is_file()
            else 0
        )

        issues: list[str] = []
        if status == "completed":
            if str(task.geometry_status) != "ready":
                issues.append("completed_task_was_not_geometry_ready")
            if state.get("input_sha256") != str(task.initial_xyz_sha256):
                issues.append("input_sha256_mismatch")
            if not actual_output_hash:
                issues.append("missing_conformer_output")
            elif state.get("output_sha256") != actual_output_hash:
                issues.append("output_sha256_mismatch")
            if conformer_count <= 0:
                issues.append("invalid_or_empty_conformer_output")
        elif status == "blocked_input_geometry":
            if str(task.geometry_status) == "ready":
                issues.append("ready_geometry_was_blocked")
            if actual_output_hash:
                issues.append("blocked_task_has_conformer_output")
        elif status in {"running", "pending", "failed", "invalid_state"}:
            issues.append(f"nonterminal_or_failed_status:{status}")
        else:
            issues.append(f"unsupported_status:{status}")

        rows.append(
            {
                "task_index": int(task.task_index),
                "task_slug": task.task_slug,
                "candidate_id": task.candidate_id,
                "component_role": task.component_role,
                "geometry_status": task.geometry_status,
                "run_status": status,
                "audit_status": "verified" if not issues else "not_verified",
                "audit_issues": ";".join(issues),
                "attempt": state.get("attempt"),
                "runtime_seconds": state.get("runtime_seconds"),
                "conformer_count": conformer_count,
                "input_sha256": state.get("input_sha256", ""),
                "output_sha256": actual_output_hash,
                "state_file": str(state_path),
                "conformer_file": str(output_path) if output_path else "",
            }
        )
    return pd.DataFrame(rows).sort_values("task_index", kind="stable").reset_index(
        drop=True
    )


def _tar_add_file(archive: tarfile.TarFile, path: Path, arcname: str) -> None:
    info = archive.gettarinfo(str(path), arcname=arcname)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    with path.open("rb") as stream:
        archive.addfile(info, stream)


def create_deterministic_package(
    package_path: Path,
    tasks_path: Path,
    summary_path: Path,
    summary: pd.DataFrame,
) -> list[str]:
    """只打包任务表、审计表、状态JSON及已核验构象系综。"""

    members: list[tuple[Path, str]] = [
        (tasks_path, "DFT任务清单.csv"),
        (summary_path, "CREST最终汇总.csv"),
    ]
    for row in summary.itertuples(index=False):
        state_path = Path(row.state_file)
        if state_path.is_file():
            members.append(
                (state_path, f"状态/{row.task_slug}/运行状态.json")
            )
        if row.audit_status == "verified" and row.run_status == "completed":
            conformer_path = Path(row.conformer_file)
            members.append(
                (conformer_path, f"构象/{row.task_slug}/crest_conformers.xyz")
            )
    members.sort(key=lambda item: item[1])
    with package_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for path, arcname in members:
                    _tar_add_file(archive, path, arcname)
    return [arcname for _, arcname in members]


def build(tasks_path: Path, result_root: Path, output_root: Path) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    tasks = pd.read_csv(tasks_path)
    summary = audit_results(tasks, result_root)
    nonterminal = summary.loc[~summary["run_status"].isin(TERMINAL_STATES)]
    unverified = summary.loc[summary["audit_status"].ne("verified")]
    if not nonterminal.empty or not unverified.empty:
        counts = summary["run_status"].value_counts().to_dict()
        raise ValueError(f"CREST结果尚未达到最终发布门: {counts}")

    summary_path = output_root / "CREST最终汇总.csv"
    summary.to_csv(summary_path, index=False, float_format="%.12g")
    package_path = output_root / "CREST精简结果.tar.gz"
    members = create_deterministic_package(
        package_path, tasks_path, summary_path, summary
    )
    manifest = {
        "release_id": "tpu-crest-gfn2-results-2026-08-v1",
        "counts": {
            "task_rows": len(summary),
            "completed": int(summary["run_status"].eq("completed").sum()),
            "blocked_input_geometry": int(
                summary["run_status"].eq("blocked_input_geometry").sum()
            ),
            "failed": int(summary["run_status"].eq("failed").sum()),
            "verified": int(summary["audit_status"].eq("verified").sum()),
            "package_members": len(members),
        },
        "inputs": {
            "tasks": {
                "path": str(tasks_path),
                "sha256": sha256(tasks_path),
                "bytes": tasks_path.stat().st_size,
            }
        },
        "outputs": {
            "summary": {
                "path": str(summary_path),
                "sha256": sha256(summary_path),
                "bytes": summary_path.stat().st_size,
            },
            "package": {
                "path": str(package_path),
                "sha256": sha256(package_path),
                "bytes": package_path.stat().st_size,
            },
        },
        "package_members": members,
        "interpretation_limits": [
            "构象能量来自CREST/GFN2-xTB，不是实验性能标签。",
            "精简包不含全部CREST临时轨迹；服务器原目录仍是完整审计资产。",
            "blocked_input_geometry保留为失败门证据，不补写构象。",
        ],
    }
    manifest_path = output_root / "CREST结果发布清单.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def verify(output_root: Path) -> None:
    manifest_path = output_root / "CREST结果发布清单.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["outputs"].values():
        path = Path(entry["path"])
        if not path.is_file() or path.stat().st_size != entry["bytes"]:
            raise ValueError(f"CREST发布文件不存在或字节数异常: {path}")
        if sha256(path) != entry["sha256"]:
            raise ValueError(f"CREST发布文件哈希异常: {path}")
    with tarfile.open(manifest["outputs"]["package"]["path"], mode="r:gz") as archive:
        members = [member.name for member in archive.getmembers() if member.isfile()]
    if members != manifest["package_members"]:
        raise ValueError("CREST精简包成员与发布清单不一致")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--任务清单", type=Path, required=True)
    parser.add_argument("--结果目录", type=Path, required=True)
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    if args.检查:
        verify(args.输出目录.resolve())
        print(f"CREST结果发布核验通过: {args.输出目录.resolve()}")
        return
    manifest = build(
        args.任务清单.resolve(), args.结果目录.resolve(), args.输出目录.resolve()
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
