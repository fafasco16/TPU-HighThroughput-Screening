"""从已完成xTB发布中冻结每个现实构件的预反应单体结构。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd


SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$", re.ASCII)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required(frame: pd.DataFrame, columns: set[str], label: str) -> None:
    missing = sorted(columns.difference(frame.columns))
    if missing:
        raise ValueError(f"{label}缺少字段: {missing}")


def build_monomer_manifest(
    discrete_results: pd.DataFrame,
    discrete_tasks: pd.DataFrame,
    ptmg_tasks: pd.DataFrame,
) -> pd.DataFrame:
    _required(
        discrete_results,
        {
            "candidate_id",
            "component_role",
            "xtb_task_slug",
            "run_status",
            "total_energy_hartree",
        },
        "离散构件逐构象结果",
    )
    _required(
        discrete_tasks,
        {"xtb_task_slug", "conformer_xyz_file", "conformer_xyz_sha256"},
        "离散构件xTB任务",
    )
    if not discrete_tasks["xtb_task_slug"].is_unique:
        raise ValueError("离散构件xTB任务xtb_task_slug不唯一")
    successful = discrete_results.loc[
        discrete_results["run_status"].astype(str).eq("success")
    ].copy()
    successful["total_energy_hartree"] = pd.to_numeric(
        successful["total_energy_hartree"], errors="coerce"
    )
    successful = successful.loc[successful["total_energy_hartree"].notna()]
    if successful.empty:
        raise ValueError("没有成功的离散构件逐构象结果")
    lowest = (
        successful.sort_values(
            ["candidate_id", "total_energy_hartree", "xtb_task_slug"],
            kind="stable",
        )
        .groupby("candidate_id", as_index=False, sort=True)
        .first()
    )
    discrete = lowest.merge(
        discrete_tasks[
            ["xtb_task_slug", "conformer_xyz_file", "conformer_xyz_sha256"]
        ],
        on="xtb_task_slug",
        how="left",
        validate="one_to_one",
    )
    if discrete["conformer_xyz_file"].isna().any():
        raise ValueError("最低能离散构象缺少xTB任务回连")
    discrete_output = pd.DataFrame(
        {
            "candidate_id": discrete["candidate_id"].astype(str),
            "component_role": discrete["component_role"].astype(str),
            "descriptor_fidelity": "crest_ensemble_lowest_xtb_energy",
            "xtb_task_slug": discrete["xtb_task_slug"].astype(str),
            "total_energy_hartree": discrete["total_energy_hartree"],
            "source_root_kind": "discrete",
            "source_relative_path": discrete["conformer_xyz_file"].astype(str),
            "source_xyz_sha256": discrete["conformer_xyz_sha256"].astype(str),
            "representation_scope": "exact_discrete_commercial_substance",
        }
    )

    outputs = [discrete_output]
    if not ptmg_tasks.empty:
        _required(
            ptmg_tasks,
            {
                "candidate_id",
                "component_role",
                "xtb_task_slug",
                "conformer_xyz_file",
                "conformer_xyz_sha256",
                "representation_scope",
            },
            "PTMG代理xTB任务",
        )
        if not ptmg_tasks["candidate_id"].is_unique or not ptmg_tasks[
            "xtb_task_slug"
        ].is_unique:
            raise ValueError("PTMG代理任务身份不唯一")
        if not ptmg_tasks["component_role"].astype(str).eq("macrodiol_proxy").all():
            raise ValueError("PTMG代理任务角色不一致")
        ptmg_output = pd.DataFrame(
            {
                "candidate_id": ptmg_tasks["candidate_id"].astype(str),
                "component_role": ptmg_tasks["component_role"].astype(str),
                "descriptor_fidelity": "single_conformer_proxy",
                "xtb_task_slug": ptmg_tasks["xtb_task_slug"].astype(str),
                "total_energy_hartree": pd.Series(
                    np.full(len(ptmg_tasks), np.nan), dtype=float
                ),
                "source_root_kind": "ptmg",
                "source_relative_path": ptmg_tasks["conformer_xyz_file"].astype(str),
                "source_xyz_sha256": ptmg_tasks["conformer_xyz_sha256"].astype(str),
                "representation_scope": ptmg_tasks["representation_scope"].astype(str),
            }
        )
        outputs.append(ptmg_output)
    result = pd.concat(outputs, ignore_index=True)
    if not result["candidate_id"].is_unique:
        raise ValueError("预反应单体candidate_id不唯一")
    if not result["candidate_id"].map(lambda value: bool(SAFE_ID.fullmatch(value))).all():
        raise ValueError("预反应单体candidate_id不能安全用于文件名")
    result["published_xyz_file"] = result["candidate_id"].map(
        lambda value: f"单体结构/{value}.xyz"
    )
    return result.sort_values(["component_role", "candidate_id"]).reset_index(drop=True)


def _safe_source(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute():
        raise ValueError("预反应单体来源路径必须为相对路径")
    resolved_root = root.resolve()
    resolved = (root / path).resolve()
    if resolved_root not in resolved.parents:
        raise ValueError("预反应单体来源路径越出发布根目录")
    return resolved


def materialize_monomers(
    manifest: pd.DataFrame,
    discrete_root: Path,
    ptmg_root: Path,
    output_root: Path,
) -> pd.DataFrame:
    roots = {"discrete": discrete_root, "ptmg": ptmg_root}
    output = manifest.copy()
    hashes: list[str] = []
    sizes: list[int] = []
    expected_files = set(output["published_xyz_file"].astype(str))
    structure_root = output_root / "单体结构"
    if structure_root.exists():
        stale = sorted(
            path.relative_to(output_root).as_posix()
            for path in structure_root.glob("*.xyz")
            if path.relative_to(output_root).as_posix() not in expected_files
        )
        if stale:
            raise ValueError(f"单体结构目录含非本发布文件: {stale[:3]}")
    structure_root.mkdir(parents=True, exist_ok=True)
    for row in output.itertuples(index=False):
        root = roots.get(str(row.source_root_kind))
        if root is None:
            raise ValueError(f"未知单体来源根类型: {row.source_root_kind}")
        source = _safe_source(root, str(row.source_relative_path))
        if not source.is_file():
            raise ValueError(f"预反应单体来源不存在: {source}")
        actual = sha256(source)
        if actual != str(row.source_xyz_sha256):
            raise ValueError(f"{row.candidate_id}来源XYZ SHA-256不一致")
        target = output_root / str(row.published_xyz_file)
        temporary = target.with_name(target.name + ".tmp")
        shutil.copyfile(source, temporary)
        temporary.replace(target)
        target_hash = sha256(target)
        if target_hash != actual:
            raise ValueError(f"{row.candidate_id}发布XYZ复制后哈希变化")
        hashes.append(target_hash)
        sizes.append(target.stat().st_size)
    output["published_xyz_sha256"] = hashes
    output["published_xyz_bytes"] = sizes
    return output


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def write_release(
    discrete_results_path: Path,
    discrete_tasks_path: Path,
    discrete_root: Path,
    ptmg_tasks_path: Path,
    ptmg_root: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    manifest = build_monomer_manifest(
        pd.read_csv(discrete_results_path),
        pd.read_csv(discrete_tasks_path),
        pd.read_csv(ptmg_tasks_path),
    )
    published = materialize_monomers(
        manifest, discrete_root, ptmg_root, output_root
    )
    table_path = output_root / "单体结构清单.csv"
    _atomic_text(table_path, published.to_csv(index=False, float_format="%.12g"))
    release = {
        "release_id": release_id,
        "status": "completed",
        "counts": {
            "monomers": len(published),
            "discrete": int(published["source_root_kind"].eq("discrete").sum()),
            "ptmg_proxies": int(published["source_root_kind"].eq("ptmg").sum()),
        },
        "table": {
            "path": table_path.name,
            "bytes": table_path.stat().st_size,
            "sha256": sha256(table_path),
        },
        "aggregate_xyz_sha256": hashlib.sha256(
            "".join(published["published_xyz_sha256"]).encode("ascii")
        ).hexdigest(),
    }
    _atomic_text(
        output_root / "单体结构发布清单.json",
        json.dumps(release, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return release


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--离散逐构象", type=Path, required=True)
    parser.add_argument("--离散任务", type=Path, required=True)
    parser.add_argument("--离散根目录", type=Path, required=True)
    parser.add_argument("--PTMG任务", type=Path, required=True)
    parser.add_argument("--PTMG根目录", type=Path, required=True)
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument(
        "--发布ID", default="tpu-reality-prereaction-monomers-20260825-v1"
    )
    args = parser.parse_args(argv)
    release = write_release(
        args.离散逐构象,
        args.离散任务,
        args.离散根目录,
        args.PTMG任务,
        args.PTMG根目录,
        args.输出目录,
        release_id=args.发布ID,
    )
    print(json.dumps(release["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
