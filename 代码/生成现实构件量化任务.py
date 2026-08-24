"""为现实库商业构件生成确定性三维结构和CREST/xTB任务清单。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

import DFT任务 as dft


ROOT = Path(__file__).resolve().parents[1]


def build_tasks(
    components: pd.DataFrame,
    ptmg_models: pd.DataFrame,
    *,
    seed: int = 20260824,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    discrete = components.loc[
        components["identity_kind"].isin(["discrete_substance", "commercial_isomer_mixture"])
    ]
    for row in discrete.itertuples(index=False):
        rows.append(
            {
                "candidate_id": row.component_id,
                "component_role": row.role,
                "canonical_smiles": row.canonical_smiles,
                "commercial_identity": row.preferred_name,
                "model_scope": (
                    "exact_discrete_commercial_substance"
                    if row.identity_kind == "discrete_substance"
                    else "commercial_isomer_mixture_connectivity_no_stereoisomer_ratio_claim"
                ),
                "geometry_seed": seed,
                "initial_conformer_count": 10,
                "charge": 0,
                "uhf": 0,
            }
        )
    for row in ptmg_models.itertuples(index=False):
        conformers = 3 if int(row.repeat_count) <= 14 else 1
        rows.append(
            {
                "candidate_id": row.component_id,
                "component_role": "macrodiol_representative",
                "canonical_smiles": row.representative_smiles,
                "commercial_identity": f"{row.component_id} single-chain representative",
                "model_scope": row.approximation_status,
                "geometry_seed": seed,
                "initial_conformer_count": conformers,
                "charge": 0,
                "uhf": 0,
            }
        )
    tasks = pd.DataFrame(rows).sort_values(["component_role", "candidate_id"]).reset_index(drop=True)
    tasks.insert(0, "task_index", range(len(tasks)))
    tasks["task_slug"] = tasks["candidate_id"].map(lambda value: f"reality_{value}")
    tasks["initial_xyz_file"] = tasks["task_slug"].map(lambda slug: f"初始结构/{slug}.xyz")
    expected_count = int(
        components["identity_kind"].isin(["discrete_substance", "commercial_isomer_mixture"]).sum()
    ) + len(ptmg_models)
    if len(tasks) != expected_count or not tasks["candidate_id"].is_unique:
        raise ValueError("现实构件量化任务数量或构件ID异常")
    return tasks


def materialize(tasks: pd.DataFrame, output_root: Path) -> pd.DataFrame:
    structure_dir = output_root / "初始结构"
    structure_dir.mkdir(parents=True, exist_ok=True)
    expected = set(tasks["initial_xyz_file"].map(lambda value: Path(value).name))
    unexpected = sorted(path.name for path in structure_dir.glob("*.xyz") if path.name not in expected)
    if unexpected:
        raise ValueError(f"现实构件初始结构目录含非本发布文件: {unexpected[:3]}")
    metadata = []
    for row in tasks.itertuples(index=False):
        path = output_root / row.initial_xyz_file
        try:
            geometry = dft.generate_initial_xyz(
                row.canonical_smiles,
                seed=int(row.geometry_seed),
                conformer_count=int(row.initial_conformer_count),
            )
        except ValueError as error:
            geometry = {
                "geometry_status": "blocked_rdkit_3d_embedding",
                "geometry_error": str(error),
                "atom_count": 0,
                "embedded_conformer_count": 0,
                "initial_force_field": "",
                "initial_force_field_energy": float("nan"),
                "initial_force_field_converged": False,
                "initial_xyz_sha256": "",
                "initial_xyz_bytes": 0,
            }
        else:
            path.write_text(str(geometry.pop("xyz")), encoding="ascii")
            geometry["geometry_status"] = (
                "ready"
                if bool(geometry["initial_force_field_converged"])
                else "ready_requires_xtb_preoptimization"
            )
            geometry["geometry_error"] = ""
            geometry["initial_xyz_sha256"] = dft.sha256(path)
            geometry["initial_xyz_bytes"] = path.stat().st_size
        metadata.append(geometry)
    return pd.concat([tasks.reset_index(drop=True), pd.DataFrame(metadata)], axis=1)


def _aggregate_hash(tasks: pd.DataFrame) -> str:
    payload = "\n".join(
        f"{row.initial_xyz_file}|{row.initial_xyz_sha256}|{int(row.initial_xyz_bytes)}"
        for row in tasks.loc[tasks["geometry_status"].str.startswith("ready")].itertuples(index=False)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def apply_preoptimization(
    tasks: pd.DataFrame,
    manifest_path: Path | None,
    output_root: Path,
) -> pd.DataFrame:
    output = tasks.copy()
    output["preoptimization_status"] = "not_required"
    output["preoptimization_method"] = ""
    output["preoptimized_xyz_file"] = ""
    output["preoptimized_xyz_sha256"] = ""
    output["preoptimization_log_file"] = ""
    required = output["geometry_status"].eq("ready_requires_xtb_preoptimization")
    output.loc[required, "preoptimization_status"] = "required_not_completed"
    if manifest_path is None or not manifest_path.is_file():
        return output
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for record in manifest.get("rows", []):
        grade = str(record["grade"])
        component_id = f"commercial_{grade.lower().replace('-', '_')}"
        mask = output["candidate_id"].eq(component_id)
        if not mask.any() or record.get("status") != "completed":
            continue
        initial_sha = str(output.loc[mask, "initial_xyz_sha256"].iloc[0])
        if initial_sha != str(record.get("input_sha256", "")):
            raise ValueError(f"预优化输入哈希与现实构件XYZ不一致: {component_id}")
        relative_opt = Path("预优化") / grade / "xtbopt.xyz"
        relative_log = Path("预优化") / grade / "preopt.log"
        opt_path = output_root / relative_opt
        log_path = output_root / relative_log
        if not opt_path.is_file():
            raise FileNotFoundError(opt_path)
        if dft.sha256(opt_path) != record["optimized_sha256"]:
            raise ValueError(f"预优化输出哈希不一致: {component_id}")
        if log_path.is_file() and dft.sha256(log_path) != record["log_sha256"]:
            raise ValueError(f"预优化日志哈希不一致: {component_id}")
        output.loc[mask, "geometry_status"] = "ready_after_gfnff_preoptimization"
        output.loc[mask, "preoptimization_status"] = "completed"
        output.loc[mask, "preoptimization_method"] = str(record.get("method", "GFN-FF"))
        output.loc[mask, "preoptimized_xyz_file"] = relative_opt.as_posix()
        output.loc[mask, "preoptimized_xyz_sha256"] = str(record["optimized_sha256"])
        output.loc[mask, "preoptimization_log_file"] = relative_log.as_posix() if log_path.is_file() else ""
    return output


def build(
    components_path: Path,
    ptmg_path: Path,
    output_root: Path,
    *,
    seed: int,
    preoptimization_manifest: Path | None = None,
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    components = pd.read_csv(components_path)
    ptmg_models = pd.read_csv(ptmg_path)
    tasks = materialize(build_tasks(components, ptmg_models, seed=seed), output_root)
    tasks = apply_preoptimization(tasks, preoptimization_manifest, output_root)
    task_path = output_root / "量化任务.csv"
    tasks.to_csv(task_path, index=False, encoding="utf-8", float_format="%.12g")
    manifest = {
        "release_id": "tpu-reality-component-quantum-2026-08-24-v1",
        "status": (
            "completed"
            if tasks["geometry_status"].str.startswith("ready").all()
            and not tasks["preoptimization_status"].eq("required_not_completed").any()
            else "completed_with_preoptimization_required"
            if tasks["geometry_status"].str.startswith("ready").all()
            else "completed_with_blocks"
        ),
        "configuration": {
            "seed": seed,
            "embedding": "RDKit_ETKDGv3",
            "force_field_preference": ["MMFF94s", "UFF"],
            "crest_method": "GFN2-xTB",
            "charge": 0,
            "uhf": 0,
        },
        "counts": {
            "tasks": len(tasks),
            "geometry_ready": int(tasks["geometry_status"].str.startswith("ready").sum()),
            "geometry_blocked": int(tasks["geometry_status"].str.startswith("blocked").sum()),
            "force_field_converged": int(tasks["initial_force_field_converged"].eq(True).sum()),
            "xtb_preoptimization_required": int(tasks["preoptimization_status"].eq("required_not_completed").sum()),
            "gfnff_preoptimization_completed": int(tasks["preoptimization_status"].eq("completed").sum()),
            "discrete_commercial_substances": int(tasks["model_scope"].eq("exact_discrete_commercial_substance").sum()),
            "ptmg_single_chain_representatives": int(tasks["component_role"].eq("macrodiol_representative").sum()),
        },
        "inputs": {
            "components": {"path": str(components_path), "sha256": dft.sha256(components_path)},
            "ptmg_models": {"path": str(ptmg_path), "sha256": dft.sha256(ptmg_path)},
        },
        "task_table": {"path": str(task_path), "sha256": dft.sha256(task_path)},
        "initial_geometries_aggregate_sha256": _aggregate_hash(tasks),
        "interpretation_limits": [
            "PTMG结构是单一羟基封端低聚链代表，不代表商业产品Mn/Mw/PDI分布。",
            "初始XYZ是RDKit/力场预优化结果，不是DFT或实验结构。",
            "构件CREST/xTB描述符不能直接解释为完整TPU强度或韧性。",
        ],
    }
    manifest_path = output_root / "发布清单.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--构件", type=Path, default=ROOT / "数据" / "现实库" / "构件.csv")
    parser.add_argument("--PTMG", type=Path, default=ROOT / "数据" / "现实库" / "PTMG代表模型.csv")
    parser.add_argument("--输出目录", type=Path, default=ROOT / "计算" / "现实构件")
    parser.add_argument("--预优化清单", type=Path, default=ROOT / "计算" / "现实构件" / "预优化" / "预优化清单.json")
    parser.add_argument("--种子", type=int, default=20260824)
    args = parser.parse_args(argv)
    manifest = build(
        args.构件,
        args.PTMG,
        args.输出目录,
        seed=args.种子,
        preoptimization_manifest=args.预优化清单,
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
