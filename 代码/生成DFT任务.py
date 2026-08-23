"""从 48 条 DFT 配方队列发布唯一构件任务和初始三维 XYZ。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

import DFT任务 as dft


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUEUE = ROOT / "候选" / "DFT_MD复核队列.csv"
DEFAULT_OUTPUT = ROOT / "计算"
MANIFEST_NAME = "DFT任务发布清单.json"


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def _aggregate_geometry_hash(entries: list[dict[str, object]]) -> str:
    payload = "\n".join(
        f"{entry['path']}|{entry['sha256']}|{entry['bytes']}" for entry in entries
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build(queue_path: Path, output_root: Path, seed: int) -> dict[str, object]:
    output_root.mkdir(parents=True, exist_ok=True)
    queue = pd.read_csv(queue_path)
    tasks = dft.build_component_tasks(queue, seed=seed)
    tasks = dft.materialize_initial_structures(tasks, output_root)
    task_path = output_root / "DFT任务清单.csv"
    tasks.to_csv(task_path, index=False, float_format="%.12g")
    geometry_entries = []
    ready_tasks = tasks.loc[tasks["geometry_status"].eq("ready")]
    for relative in ready_tasks["initial_xyz_file"]:
        path = output_root / relative
        geometry_entries.append(
            {
                "path": _relative(path),
                "sha256": dft.sha256(path),
                "bytes": path.stat().st_size,
            }
        )
    manifest = {
        "dft_task_release_id": "tpu-crest-gfn2-2026-08-23-v1",
        "source_queue": {
            "path": _relative(queue_path),
            "sha256": dft.sha256(queue_path),
            "bytes": queue_path.stat().st_size,
        },
        "configuration": {
            "geometry_seed": int(seed),
            "initial_conformers": 10,
            "embedding": "RDKit_ETKDGv3",
            "force_field_preference": ["MMFF94s", "UFF"],
            "crest_method": "GFN2-xTB",
            "charge": 0,
            "uhf": 0,
        },
        "counts": {
            "source_formulation_rows": len(queue),
            "unique_component_tasks": len(tasks),
            "geometry_ready_tasks": int(tasks["geometry_status"].eq("ready").sum()),
            "geometry_blocked_tasks": int(tasks["geometry_status"].ne("ready").sum()),
            "diisocyanate_tasks": int(tasks["component_role"].eq("diisocyanate").sum()),
            "macrodiol_proxy_tasks": int(tasks["component_role"].eq("macrodiol_proxy").sum()),
            "chain_extender_tasks": int(tasks["component_role"].eq("chain_extender").sum()),
            "initial_xyz_files": len(geometry_entries),
        },
        "task_table": {
            "path": _relative(task_path),
            "sha256": dft.sha256(task_path),
            "bytes": task_path.stat().st_size,
        },
        "initial_geometries": {
            "aggregate_sha256": _aggregate_geometry_hash(geometry_entries),
            "files": geometry_entries,
        },
        "interpretation_limits": [
            "初始XYZ是RDKit构象生成与力场预优化结果，不是DFT或实验结构。",
            "CREST/GFN2-xTB结果只作构象与反应性代理，不是TPU宏观性能标签。",
            "macrodiol_proxy不是Mn=2000真实低聚物，块体MD保持关闭。",
            "ORCA/r2SCAN-3c在当前服务器环境不可用，Tier-1b未启动。",
        ],
    }
    (output_root / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def verify(output_root: Path) -> None:
    manifest_path = output_root / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    queue_entry = manifest["source_queue"]
    queue_path = ROOT / queue_entry["path"]
    if not queue_path.is_file() or dft.sha256(queue_path) != queue_entry["sha256"]:
        raise ValueError("DFT 来源队列哈希不匹配")
    task_entry = manifest["task_table"]
    task_path = ROOT / task_entry["path"]
    if not task_path.is_file() or dft.sha256(task_path) != task_entry["sha256"]:
        raise ValueError("DFT 任务清单哈希不匹配")
    tasks = pd.read_csv(task_path)
    counts = manifest["counts"]
    if len(tasks) != counts["unique_component_tasks"] or not tasks["candidate_id"].is_unique:
        raise ValueError("DFT 唯一构件任务数量或 ID 异常")
    if int(tasks["geometry_status"].eq("ready").sum()) != counts["geometry_ready_tasks"]:
        raise ValueError("DFT 初始结构就绪数量异常")
    entries = manifest["initial_geometries"]["files"]
    for entry in entries:
        path = ROOT / entry["path"]
        if not path.is_file() or path.stat().st_size != entry["bytes"] or dft.sha256(path) != entry["sha256"]:
            raise ValueError(f"DFT 初始结构哈希不匹配: {path}")
    if _aggregate_geometry_hash(entries) != manifest["initial_geometries"]["aggregate_sha256"]:
        raise ValueError("DFT 初始结构聚合哈希不匹配")
    blocked = tasks.loc[tasks["geometry_status"].ne("ready")]
    if not blocked["initial_xyz_sha256"].fillna("").eq("").all():
        raise ValueError("受阻任务不应伪造初始结构哈希")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--队列", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--输出目录", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--种子", type=int, default=20260823)
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    output = args.输出目录.resolve()
    if args.检查:
        verify(output)
        print(f"DFT 任务发布核验通过: {output}")
        return
    manifest = build(args.队列.resolve(), output, args.种子)
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
