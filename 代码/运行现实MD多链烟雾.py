"""为最小现实TPU低聚链运行可复现的多链周期盒LAMMPS烟雾测试。

该脚本只验证 RadonPy -> GAFF2 -> 无定形装箱 -> LAMMPS 最小化/NVT
执行链；Gasteiger 电荷和聚氨酯替代参数未验证，因此输出不得解释为生产MD。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import time
import traceback
from pathlib import Path
from typing import Any, Sequence

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = 20260825


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def _safe_text(value: object) -> str:
    """把可能含文件系统代理字符的异常文本转成可发布UTF-8。"""
    return str(value).encode("utf-8", errors="backslashreplace").decode("utf-8")


def select_graph(graphs: pd.DataFrame, formulation_id: str | None) -> dict[str, Any]:
    required = {
        "formulation_id",
        "canonical_smiles",
        "atom_count",
        "chemical_graph_status",
        "performance_claim_status",
    }
    missing = sorted(required.difference(graphs.columns))
    if missing:
        raise ValueError(f"低聚链化学图缺少字段: {missing}")
    if graphs.empty or not graphs["formulation_id"].is_unique:
        raise ValueError("低聚链化学图formulation_id必须非空唯一")
    admitted = graphs.loc[
        graphs["chemical_graph_status"].astype(str).eq("completed")
        & graphs["performance_claim_status"].astype(str).eq("no_performance_claim")
    ].copy()
    if formulation_id:
        admitted = admitted.loc[
            admitted["formulation_id"].astype(str).eq(formulation_id)
        ]
        if len(admitted) != 1:
            raise ValueError(f"未找到唯一可用配方: {formulation_id}")
    if admitted.empty:
        raise ValueError("没有可用于烟雾测试的completed低聚链")
    selected = admitted.sort_values(
        ["atom_count", "formulation_id"], kind="stable"
    ).iloc[0]
    return selected.to_dict()


def summarize_alternates(text: str) -> dict[str, Any]:
    lines = sorted(
        line.strip() for line in text.splitlines() if "Using alternate" in line
    )
    unique = sorted(set(lines))
    return {
        "alternate_parameter_line_count": len(lines),
        "alternate_parameter_unique_count": len(unique),
        "alternate_parameter_unique_sha256": hashlib.sha256(
            "\n".join(unique).encode("utf-8")
        ).hexdigest(),
    }


def parse_lammps_log(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    loop_times = [float(value) for value in re.findall(r"Loop time of ([0-9.eE+-]+)", text)]
    criterion_match = re.search(r"Stopping criterion =\s*([^\n\r]+)", text)
    energy_match = re.search(
        r"Energy initial, next-to-last, final\s*=\s*"
        r"([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)",
        text,
    )
    force_match = re.search(
        r"Force two-norm initial, final\s*=\s*"
        r"([0-9.eE+-]+)\s+([0-9.eE+-]+)",
        text,
    )
    criterion = criterion_match.group(1).strip() if criterion_match else "not_found"
    minimization_converged = criterion.lower() in {
        "energy tolerance",
        "force tolerance",
    }
    return {
        "error_detected": "ERROR:" in text,
        "normal_loop_count": len(loop_times),
        "loop_wall_seconds": loop_times,
        "contains_total_wall_time": "Total wall time:" in text,
        "minimization_stopping_criterion": criterion,
        "minimization_converged": minimization_converged,
        "minimization_energy_initial_next_final_kcal_mol": (
            [float(value) for value in energy_match.groups()] if energy_match else []
        ),
        "minimization_force_two_norm_initial_final": (
            [float(value) for value in force_match.groups()] if force_match else []
        ),
    }


def _file_records(output_root: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for path in sorted(output_root.iterdir(), key=lambda item: item.name):
        if path.is_file() and path.name != "多链烟雾发布清单.json":
            records[path.name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    return records


def run_smoke(
    graph_path: Path,
    output_root: Path,
    *,
    formulation_id: str | None,
    chain_count: int,
    initial_density_g_cm3: float,
    nvt_steps: int,
    minimization_max_iterations: int,
    minimization_max_evaluations: int,
    time_step_fs: float,
    temperature_k: float,
    random_seed: int,
    lammps_path: str,
    release_id: str,
) -> dict[str, Any]:
    if not graph_path.is_file():
        raise ValueError(f"低聚链化学图不存在: {graph_path}")
    if chain_count < 2:
        raise ValueError("多链烟雾至少需要2条链")
    if not 0.05 <= initial_density_g_cm3 <= 0.5:
        raise ValueError("烟雾测试初始密度必须在0.05–0.5 g cm^-3")
    if (
        nvt_steps < 1
        or minimization_max_iterations < 1
        or minimization_max_evaluations < minimization_max_iterations
        or time_step_fs <= 0
        or temperature_k <= 0
    ):
        raise ValueError("NVT步数、时间步长和温度必须为正")
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError(f"输出目录非空，拒绝覆盖: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)

    selected = select_graph(pd.read_csv(graph_path), formulation_id)
    manifest_path = output_root / "多链烟雾发布清单.json"
    started = time.monotonic()
    base: dict[str, Any] = {
        "release_id": release_id,
        "formulation_id": str(selected["formulation_id"]),
        "single_chain_atom_count": int(selected["atom_count"]),
        "chain_count": chain_count,
        "expected_total_atom_count": int(selected["atom_count"]) * chain_count,
        "initial_density_g_cm3": initial_density_g_cm3,
        "random_seed": random_seed,
        "forcefield": "GAFF2_with_alternate_parameters",
        "charge_method": "gasteiger_smoke_only",
        "production_md_permission": (
            "blocked_urethane_alternate_parameter_and_charge_validation"
        ),
        "performance_claim_status": "no_performance_claim",
        "interpretation_limit": (
            "Execution-chain smoke only; the short low-density NVT trajectory is "
            "not equilibrated production TPU MD and yields no property claim."
        ),
    }
    try:
        import numpy as np
        from rdkit import Chem
        from rdkit.Chem import AllChem
        from radonpy.core import poly
        from radonpy.ff.gaff2 import GAFF2
        from radonpy.sim import lammps, md

        os.environ["OMP_NUM_THREADS"] = "1"
        np.random.seed(random_seed)
        molecule = Chem.AddHs(Chem.MolFromSmiles(str(selected["canonical_smiles"])))
        if molecule.GetNumAtoms() != int(selected["atom_count"]):
            raise RuntimeError("加氢后原子数与低聚链化学图不一致")
        params = AllChem.ETKDGv3()
        params.randomSeed = int(random_seed)
        params.useRandomCoords = True
        params.maxIterations = 1000
        if AllChem.EmbedMolecule(molecule, params) < 0:
            raise RuntimeError("ETKDGv3三维嵌入失败")
        if AllChem.MMFFHasAllMoleculeParams(molecule):
            AllChem.MMFFOptimizeMolecule(
                molecule, maxIters=1000, mmffVariant="MMFF94s"
            )

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            assigned = bool(GAFF2().ff_assign(molecule, charge="gasteiger"))
        alternate = summarize_alternates(captured.getvalue())
        if not assigned:
            raise RuntimeError("RadonPy GAFF2参数分配失败")

        cell = poly.amorphous_cell(
            molecule,
            chain_count,
            density=initial_density_g_cm3,
            retry=5,
            retry_step=2000,
            threshold=2.0,
            dec_rate=0.8,
        )
        total_atoms = cell.GetNumAtoms()
        if total_atoms != base["expected_total_atom_count"]:
            raise RuntimeError("多链周期盒原子数不闭合")

        workflow = md.MD(
            # LAMMPS输入内容和内部文件名限定ASCII；中文只用于上级目录和清单。
            input_file="multichain_smoke.in",
            log_file="multichain_smoke_lammps.log",
            dat_file="multichain_smoke.data",
            dump_file="multichain_smoke.dump",
            rst1_file="multichain_smoke_1.rst",
            rst2_file="multichain_smoke_2.rst",
            outstr="multichain_smoke_last.dump",
            write_data="multichain_smoke_last.data",
            dump_freq=max(1, min(500, nvt_steps)),
            thermo_freq=max(1, min(100, nvt_steps)),
            rst_freq=max(1, nvt_steps),
        )
        workflow.add_min(
            min_style="cg",
            etol=1.0e-6,
            ftol=1.0e-8,
            maxiter=minimization_max_iterations,
            maxeval=minimization_max_evaluations,
        )
        workflow.add_md(
            "nvt",
            step=nvt_steps,
            time_step=time_step_fs,
            t_start=temperature_k,
            t_stop=temperature_k,
            t_dump=50.0,
            set_init_velocity=temperature_k,
        )

        runner = lammps.LAMMPS(
            work_dir=str(output_root), solver_path=lammps_path
        )
        if not runner.make_dat(
            cell,
            file_name=workflow.dat_file,
            dir_name=str(output_root),
            velocity=False,
        ):
            raise RuntimeError("RadonPy LAMMPS data文件生成失败")
        if not runner.make_input(
            workflow, file_name=workflow.input_file, dir_name=str(output_root)
        ):
            raise RuntimeError("RadonPy LAMMPS输入文件生成失败")
        completed = runner.exec(
            input_file=workflow.input_file,
            output_file="multichain_smoke_screen.log",
            omp=0,
            mpi=0,
        )
        log_path = output_root / workflow.log_file
        log_summary = parse_lammps_log(log_path) if log_path.is_file() else {
            "error_detected": True,
            "normal_loop_count": 0,
            "loop_wall_seconds": [],
            "contains_total_wall_time": False,
        }
        success = (
            completed.returncode == 0
            and not log_summary["error_detected"]
            and log_summary["normal_loop_count"] >= 2
            and (output_root / workflow.write_data).is_file()
        )
        fully_converged = success and bool(log_summary["minimization_converged"])
        manifest = {
            **base,
            **alternate,
            "status": (
                "completed_multichain_smoke_production_md_blocked"
                if fully_converged
                else "completed_execution_chain_minimization_not_converged_production_md_blocked"
                if success
                else "failed_multichain_smoke"
            ),
            "actual_total_atom_count": total_atoms,
            "protocol": {
                "packing": "RadonPy_poly.amorphous_cell",
                "minimization": {
                    "style": "cg",
                    "energy_tolerance": 1.0e-6,
                    "force_tolerance": 1.0e-8,
                    "max_iterations": minimization_max_iterations,
                    "max_evaluations": minimization_max_evaluations,
                },
                "ensemble": "NVT_Nose-Hoover",
                "temperature_k": temperature_k,
                "time_step_fs": time_step_fs,
                "nvt_steps": nvt_steps,
                "trajectory_time_ps": nvt_steps * time_step_fs / 1000.0,
            },
            "lammps_returncode": int(completed.returncode),
            "lammps_log_summary": log_summary,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        manifest["files"] = _file_records(output_root)
        _atomic_json(manifest_path, manifest)
        if not success:
            raise RuntimeError("LAMMPS多链烟雾未通过成功门")
        return manifest
    except Exception as exc:
        if not manifest_path.is_file():
            failure = {
                **base,
                "status": "failed_multichain_smoke",
                "error_type": type(exc).__name__,
                "error_message": _safe_text(exc),
                "traceback": _safe_text(traceback.format_exc()),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "files": _file_records(output_root),
            }
            _atomic_json(manifest_path, failure)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--化学图",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "低聚链化学图.csv.gz",
    )
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument("--配方ID")
    parser.add_argument("--链数", type=int, default=2)
    parser.add_argument("--初始密度", type=float, default=0.20)
    parser.add_argument("--NVT步数", type=int, default=2000)
    parser.add_argument("--最小化最大迭代", type=int, default=5000)
    parser.add_argument("--最小化最大评估", type=int, default=50000)
    parser.add_argument("--时间步长fs", type=float, default=0.5)
    parser.add_argument("--温度K", type=float, default=300.0)
    parser.add_argument("--随机种子", type=int, default=DEFAULT_SEED)
    parser.add_argument("--LAMMPS", default="/opt/tpu-md-venv/bin/lmp")
    parser.add_argument(
        "--发布ID", default="tpu-reality-md-multichain-smoke-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = run_smoke(
        args.化学图,
        args.输出目录,
        formulation_id=args.配方ID,
        chain_count=args.链数,
        initial_density_g_cm3=args.初始密度,
        nvt_steps=args.NVT步数,
        minimization_max_iterations=args.最小化最大迭代,
        minimization_max_evaluations=args.最小化最大评估,
        time_step_fs=args.时间步长fs,
        temperature_k=args.温度K,
        random_seed=args.随机种子,
        lammps_path=args.LAMMPS,
        release_id=args.发布ID,
    )
    print(json.dumps({
        "status": manifest["status"],
        "formulation_id": manifest["formulation_id"],
        "actual_total_atom_count": manifest["actual_total_atom_count"],
        "elapsed_seconds": manifest["elapsed_seconds"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
