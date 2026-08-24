"""严格核验RESP敏感性原始任务并发布紧凑构象/点密度统计。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
from rdkit import Chem


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _atomic_gzip_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(gzip.compress(text.encode("utf-8"), mtime=0))
    temporary.replace(path)


def functional_core_indices(smiles: str, validation_family: str) -> set[int]:
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError(f"RESP敏感性SMILES无法解析: {smiles}")
    if validation_family.endswith("urethane"):
        pattern = Chem.MolFromSmarts("OC(=O)N")
    elif validation_family.endswith("terminal_isocyanate"):
        pattern = Chem.MolFromSmarts("N=C=O")
    else:
        raise ValueError(f"未知RESP敏感性家族: {validation_family}")
    matches = molecule.GetSubstructMatches(pattern)
    if not matches:
        raise ValueError(f"RESP敏感性家族核心SMARTS未匹配: {validation_family}")
    return {index for match in matches for index in match}


def _verify_task(
    task: dict[str, Any], raw_root: Path
) -> tuple[dict[str, Any], pd.DataFrame]:
    directory = raw_root / str(task["output_directory"])
    manifest_path = directory / "RESP烟雾清单.json"
    if not manifest_path.is_file():
        raise ValueError(f"RESP敏感性任务缺清单: {task['task_id']}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "completed_native_two_stage_resp_smoke":
        raise ValueError(f"RESP敏感性任务未完成: {task['task_id']}")
    if (
        manifest.get("fragment_name") != task["fragment_name"]
        or int(manifest.get("random_seed", -1)) != int(task["random_seed"])
        or abs(
            float(manifest.get("vdw_point_density", -1))
            - float(task["vdw_point_density"])
        )
        > 1.0e-12
    ):
        raise ValueError(f"RESP敏感性任务身份不闭合: {task['task_id']}")
    for name, record in manifest["files"].items():
        path = directory / name
        if (
            not path.is_file()
            or path.stat().st_size != int(record["bytes"])
            or sha256(path) != record["sha256"]
        ):
            raise ValueError(f"RESP敏感性原始文件哈希不闭合: {path}")
    charges = pd.read_csv(directory / "resp_charges.csv")
    if len(charges) != int(manifest["atom_count"]):
        raise ValueError(f"RESP敏感性原子数不闭合: {task['task_id']}")
    charge_sum = float(math.fsum(charges["stage2_resp_charge_e"].tolist()))
    if abs(charge_sum - float(manifest["target_total_charge_e"])) > 1.0e-8:
        raise ValueError(f"RESP敏感性CSV电荷和不闭合: {task['task_id']}")
    manifest_record = {
        "task_id": task["task_id"],
        "manifest_path": str(manifest_path),
        "manifest_bytes": manifest_path.stat().st_size,
        "manifest_sha256": sha256(manifest_path),
        "raw_stage2_charge_sum_error_e": abs(
            float(manifest["charge_metrics"]["stage2_resp_charge_sum_e"])
            - float(manifest["target_total_charge_e"])
        ),
        "elapsed_seconds": float(manifest["elapsed_seconds"]),
    }
    return manifest_record, charges


def _population_stats(
    charges: pd.DataFrame, group_columns: list[str], prefix: str
) -> pd.DataFrame:
    grouped = charges.groupby(group_columns, sort=True, dropna=False)[
        "stage2_resp_charge_e"
    ]
    result = grouped.agg(["count", "mean", "std", "min", "max"]).reset_index()
    result["std"] = result["std"].fillna(0.0)
    result["range"] = result["max"] - result["min"]
    return result.rename(
        columns={
            "count": f"{prefix}_count",
            "mean": f"{prefix}_mean_e",
            "std": f"{prefix}_sample_std_e",
            "min": f"{prefix}_min_e",
            "max": f"{prefix}_max_e",
            "range": f"{prefix}_range_e",
        }
    )


def summarize_sensitivity(charges: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    identity = [
        "fragment_name",
        "validation_family",
        "smiles",
        "atom_index_zero_based",
        "element",
        "functional_core",
    ]
    conformer = _population_stats(
        charges,
        [*identity, "vdw_point_density"],
        "across_seed",
    )
    density = _population_stats(
        charges,
        [*identity, "random_seed"],
        "across_density",
    )
    overall = _population_stats(charges, identity, "overall")
    family_rows: list[dict[str, Any]] = []
    for (fragment_name, family), subset in overall.groupby(
        ["fragment_name", "validation_family"], sort=True
    ):
        conf_subset = conformer.loc[
            conformer["fragment_name"].eq(fragment_name)
        ]
        density_subset = density.loc[density["fragment_name"].eq(fragment_name)]
        core_overall = subset.loc[subset["functional_core"]]
        core_conf = conf_subset.loc[conf_subset["functional_core"]]
        core_density = density_subset.loc[density_subset["functional_core"]]
        family_rows.append(
            {
                "fragment_name": fragment_name,
                "validation_family": family,
                "atom_count": len(subset),
                "functional_core_atom_count": len(core_overall),
                "maximum_atom_overall_range_e": float(subset["overall_range_e"].max()),
                "maximum_core_overall_range_e": float(core_overall["overall_range_e"].max()),
                "maximum_atom_across_seed_sample_std_e": float(
                    conf_subset["across_seed_sample_std_e"].max()
                ),
                "maximum_core_across_seed_sample_std_e": float(
                    core_conf["across_seed_sample_std_e"].max()
                ),
                "maximum_atom_across_density_sample_std_e": float(
                    density_subset["across_density_sample_std_e"].max()
                ),
                "maximum_core_across_density_sample_std_e": float(
                    core_density["across_density_sample_std_e"].max()
                ),
                "production_md_permission": "blocked_joint_multiconformer_fit_and_transfer",
            }
        )
    return (
        conformer.sort_values([*identity, "vdw_point_density"], kind="stable"),
        density.sort_values([*identity, "random_seed"], kind="stable"),
        pd.DataFrame(family_rows).sort_values("validation_family", kind="stable"),
    )


def write_release(
    raw_root: Path,
    output_root: Path,
    *,
    release_id: str,
    raw_archive: Path | None = None,
) -> dict[str, Any]:
    plan_path = raw_root / "任务计划.csv"
    status_path = raw_root / "任务状态.csv"
    batch_path = raw_root / "批次状态.json"
    for path in (plan_path, status_path, batch_path):
        if not path.is_file():
            raise ValueError(f"RESP敏感性批次文件不存在: {path}")
    plan = pd.read_csv(plan_path)
    status = pd.read_csv(status_path)
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    if batch.get("status") != "completed" or len(plan) != 36:
        raise ValueError("RESP敏感性36任务尚未全部完成")
    merged = plan.merge(
        status[["task_id", "batch_status", "returncode"]],
        on="task_id",
        how="left",
        validate="one_to_one",
    )
    if not merged["batch_status"].isin(["completed_new", "completed_existing"]).all():
        raise ValueError("RESP敏感性状态表存在未完成任务")

    task_records: list[dict[str, Any]] = []
    charge_frames: list[pd.DataFrame] = []
    for task in merged.sort_values("task_id", kind="stable").to_dict(orient="records"):
        record, charges = _verify_task(task, raw_root)
        core_indices = functional_core_indices(
            str(task["smiles"]), str(task["validation_family"])
        )
        charges = charges.copy()
        charges.insert(
            0,
            "functional_core",
            charges["atom_index_zero_based"].astype(int).isin(core_indices),
        )
        for name in [
            "vdw_point_density",
            "random_seed",
            "smiles",
            "validation_family",
            "fragment_name",
            "task_id",
        ]:
            charges.insert(0, name, task[name])
        charge_frames.append(charges)
        task_records.append({**task, **record})
    task_table = pd.DataFrame(task_records).sort_values("task_id", kind="stable")
    charges = pd.concat(charge_frames, ignore_index=True).sort_values(
        [
            "validation_family",
            "random_seed",
            "vdw_point_density",
            "atom_index_zero_based",
        ],
        kind="stable",
    )
    conformer, density, family = summarize_sensitivity(charges)
    output_root.mkdir(parents=True, exist_ok=True)
    task_out = output_root / "RESP敏感性任务汇总.csv"
    charge_out = output_root / "RESP敏感性逐原子电荷.csv.gz"
    conformer_out = output_root / "构象敏感性逐原子.csv"
    density_out = output_root / "点密度敏感性逐原子.csv"
    family_out = output_root / "片段敏感性汇总.csv"
    report_out = output_root / "RESP敏感性说明.md"
    archive_out = output_root / "原始归档定位.json"
    _atomic_text(task_out, task_table.to_csv(index=False, float_format="%.12g"))
    _atomic_gzip_text(
        charge_out, charges.to_csv(index=False, float_format="%.12g")
    )
    _atomic_text(
        conformer_out, conformer.to_csv(index=False, float_format="%.12g")
    )
    _atomic_text(density_out, density.to_csv(index=False, float_format="%.12g"))
    _atomic_text(family_out, family.to_csv(index=False, float_format="%.12g"))
    _atomic_text(
        report_out,
        "\n".join(
            [
                "# RESP构象与点密度敏感性说明",
                "",
                "矩阵覆盖4个局部化学家族、3个确定性ETKDG/MMFF种子和ESP点密度0.5/1.0/2.0，共36个独立两阶段RESP任务。",
                "逐任务原始网格与Psi4日志保留在服务器；本目录只发布任务清单、逐原子电荷和构象/点密度统计，避免仓库堆积原始网格。",
                "",
                "`across_seed`统计在固定点密度下比较三个构象；`across_density`统计在固定构象下比较三个点密度。`functional_core=true`标记氨基甲酸酯OC(=O)N或异氰酸酯N=C=O核心。",
                "这些结果衡量单构象独立拟合的敏感性，并不等于多构象联合RESP。下一步应根据本矩阵选择代表构象，执行共同电荷约束的联合拟合，再验证片段到完整TPU链的转移。",
                "",
            ]
        ),
    )
    if raw_archive is not None:
        if not raw_archive.is_file():
            raise ValueError(f"RESP敏感性原始归档不存在: {raw_archive}")
        archive_record: dict[str, Any] | None = {
            "storage": "server_only_not_committed_to_git",
            "path": str(raw_archive),
            "bytes": raw_archive.stat().st_size,
            "sha256": sha256(raw_archive),
            "archive_protocol": (
                "GNU tar --sort=name --mtime=UTC_2026-08-25 --owner=0 --group=0 "
                "--numeric-owner -czf"
            ),
        }
        _atomic_text(
            archive_out,
            json.dumps(archive_record, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
    else:
        archive_record = None
    files = [
        task_out,
        charge_out,
        conformer_out,
        density_out,
        family_out,
        report_out,
        *([archive_out] if archive_record is not None else []),
    ]
    manifest = {
        "release_id": release_id,
        "status": "sensitivity_matrix_completed_joint_fit_pending",
        "counts": {
            "tasks": len(task_table),
            "fragments": task_table["fragment_name"].nunique(),
            "seeds": task_table["random_seed"].nunique(),
            "point_densities": task_table["vdw_point_density"].nunique(),
            "atomic_charge_rows": len(charges),
        },
        "maximum_raw_stage2_charge_sum_error_e": float(
            task_table["raw_stage2_charge_sum_error_e"].max()
        ),
        "maximum_core_overall_range_e": float(
            family["maximum_core_overall_range_e"].max()
        ),
        "maximum_core_across_seed_sample_std_e": float(
            family["maximum_core_across_seed_sample_std_e"].max()
        ),
        "maximum_core_across_density_sample_std_e": float(
            family["maximum_core_across_density_sample_std_e"].max()
        ),
        "inputs": {
            path.name: {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in (plan_path, status_path, batch_path)
        },
        "aggregate_task_manifest_sha256": hashlib.sha256(
            "".join(task_table["manifest_sha256"]).encode("ascii")
        ).hexdigest(),
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "raw_storage_policy": "server_only_grids_logs_compact_repository_release",
        "raw_archive": archive_record,
        "production_md_permission": "blocked_joint_multiconformer_fit_and_transfer",
        "performance_claim_status": "no_performance_claim",
    }
    _atomic_text(
        output_root / "RESP敏感性发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--原始目录", type=Path, required=True)
    parser.add_argument(
        "--输出目录",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "RESP敏感性",
    )
    parser.add_argument(
        "--发布ID", default="tpu-reality-md-resp-sensitivity-20260825-v1"
    )
    parser.add_argument("--原始归档", type=Path)
    args = parser.parse_args(argv)
    manifest = write_release(
        args.原始目录,
        args.输出目录,
        release_id=args.发布ID,
        raw_archive=args.原始归档,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
