"""把现实构件原始/预优化XYZ冻结为可由现有CREST任务运行器消费的批次。"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from DFT任务 import sha256


ROOT = Path(__file__).resolve().parents[1]


def build(task_path: Path, quantum_root: Path, output_root: Path) -> dict[str, Any]:
    source_tasks = pd.read_csv(task_path)
    required = {
        "task_index",
        "candidate_id",
        "component_role",
        "canonical_smiles",
        "charge",
        "uhf",
        "task_slug",
        "initial_xyz_file",
        "initial_xyz_sha256",
        "geometry_status",
        "preoptimization_status",
        "preoptimized_xyz_file",
        "preoptimized_xyz_sha256",
    }
    missing = sorted(required.difference(source_tasks.columns))
    if missing:
        raise ValueError(f"现实量化任务缺少字段: {missing}")
    output_root.mkdir(parents=True, exist_ok=True)
    structure_dir = output_root / "初始结构"
    structure_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    entries = []
    for row in source_tasks.itertuples(index=False):
        use_preoptimized = row.preoptimization_status == "completed"
        relative_source = row.preoptimized_xyz_file if use_preoptimized else row.initial_xyz_file
        expected_hash = row.preoptimized_xyz_sha256 if use_preoptimized else row.initial_xyz_sha256
        source = quantum_root / str(relative_source)
        if not source.is_file() or sha256(source) != str(expected_hash):
            raise ValueError(f"现实CREST源结构哈希不匹配: {row.candidate_id}")
        destination = structure_dir / f"{row.task_slug}.xyz"
        shutil.copy2(source, destination)
        digest = sha256(destination)
        rows.append(
            {
                "task_index": int(row.task_index),
                "candidate_id": row.candidate_id,
                "component_role": row.component_role,
                "canonical_smiles": row.canonical_smiles,
                "charge": int(row.charge),
                "uhf": int(row.uhf),
                "task_slug": row.task_slug,
                "initial_xyz_file": destination.relative_to(output_root).as_posix(),
                "initial_xyz_sha256": digest,
                "initial_xyz_bytes": destination.stat().st_size,
                "geometry_status": "ready",
                "geometry_error": "",
                "input_model_scope": row.model_scope,
                "input_geometry_source": "gfnff_preoptimized" if use_preoptimized else "rdkit_force_field_converged",
            }
        )
        entries.append(f"{destination.name}|{digest}|{destination.stat().st_size}")
    tasks = pd.DataFrame(rows).sort_values("task_index").reset_index(drop=True)
    if len(tasks) != 19 or not tasks["candidate_id"].is_unique or not tasks["task_index"].is_unique:
        raise ValueError("现实CREST批次必须包含19个唯一任务")
    task_output = output_root / "DFT任务清单.csv"
    tasks.to_csv(task_output, index=False, encoding="utf-8")
    aggregate_hash = __import__("hashlib").sha256("\n".join(entries).encode("utf-8")).hexdigest()
    manifest = {
        "release_id": "tpu-reality-crest-input-20260824-v1",
        "status": "ready",
        "counts": {
            "tasks": len(tasks),
            "discrete_tasks": int(tasks["component_role"].ne("macrodiol_representative").sum()),
            "ptmg_representative_tasks": int(tasks["component_role"].eq("macrodiol_representative").sum()),
            "gfnff_preoptimized_inputs": int(tasks["input_geometry_source"].eq("gfnff_preoptimized").sum()),
        },
        "source_task_table": {"path": str(task_path), "sha256": sha256(task_path)},
        "task_table": {"path": str(task_output), "sha256": sha256(task_output)},
        "initial_geometries_aggregate_sha256": aggregate_hash,
        "execution_policy": {
            "smoke_test_candidate_ids": ["commercial_bdo_14", "commercial_ipdi"],
            "first_wave": "14 discrete commercial components",
            "ptmg_wave": "hold until discrete smoke tests and runtime review",
            "threads_per_task": 4,
            "max_parallel_tasks_on_16_cpu_instance": 4,
        },
    }
    (output_root / "发布清单.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--任务", type=Path, default=ROOT / "计算" / "现实构件" / "量化任务.csv")
    parser.add_argument("--量化目录", type=Path, default=ROOT / "计算" / "现实构件")
    parser.add_argument("--输出目录", type=Path, default=ROOT / "计算" / "现实CREST")
    args = parser.parse_args(argv)
    manifest = build(args.任务, args.量化目录, args.输出目录)
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
