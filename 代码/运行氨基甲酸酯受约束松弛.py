"""对刚性扫描选出的角度运行冻结O=C-N-R二面角的ωB97M-D3BJ松弛优化。"""

from __future__ import annotations

import argparse
import contextlib
import importlib.metadata
import json
import math
import os
import platform
import signal
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
from rdkit import Chem
from rdkit.Chem import AllChem, rdMolTransforms

from 运行氨基甲酸酯刚性扫描 import (
    HARTREE_TO_KCAL_MOL,
    _atomic_text,
    select_urethane_torsion,
    sha256,
)


OPTIMIZER_PROFILES = {"standard_v1", "difficult_v2", "difficult_hessian_v3"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextlib.contextmanager
def point_wall_clock_limit(seconds: int):
    """在POSIX主进程中为单个Psi4优化设置可审计墙钟上限。"""

    if seconds < 0:
        raise ValueError("单点墙钟上限不能为负")
    if seconds == 0:
        yield
        return
    if os.name != "posix":
        raise RuntimeError("正墙钟上限只在POSIX计算节点受支持")
    old_handler = signal.getsignal(signal.SIGALRM)
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    if old_timer[0] > 0:
        raise RuntimeError("检测到既有ITIMER_REAL，拒绝覆盖")

    def _timeout_handler(signum: int, frame: object) -> None:
        del signum, frame
        raise TimeoutError(f"Psi4单点墙钟超过{seconds}秒")

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, old_handler)


def build_checkpoint(
    base: dict[str, Any],
    rows: Sequence[dict[str, Any]],
    planned_count: int,
    *,
    checkpoint_status: str = "running_with_point_checkpoint",
) -> dict[str, Any]:
    completed = sum(row.get("point_status") == "completed" for row in rows)
    failed = len(rows) - completed
    return {
        **base,
        "status": checkpoint_status,
        "counts": {
            "planned": int(planned_count),
            "attempted": len(rows),
            "completed": completed,
            "failed": failed,
            "remaining": int(planned_count) - len(rows),
        },
        "last_attempted_angle_degrees": (
            int(rows[-1]["requested_angle_degrees"]) if rows else None
        ),
        "updated_utc": _utc_now(),
    }


def write_checkpoint(
    output_root: Path,
    base: dict[str, Any],
    rows: Sequence[dict[str, Any]],
    planned_count: int,
    *,
    checkpoint_status: str = "running_with_point_checkpoint",
) -> None:
    table = pd.DataFrame(rows)
    if not table.empty:
        table = table.sort_values("requested_angle_degrees", kind="stable")
    _atomic_text(
        output_root / "relaxed_scan_checkpoint.csv",
        table.to_csv(index=False, float_format="%.12g"),
    )
    _atomic_text(
        output_root / "受约束松弛检查点.json",
        json.dumps(
            build_checkpoint(
                base,
                rows,
                planned_count,
                checkpoint_status=checkpoint_status,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def build_optking_options(
    frozen_dihedral: str,
    max_iterations: int,
    optimizer_profile: str,
) -> dict[str, Any]:
    """返回可审计的OptKing配置；困难点配置不改变DFT方法和冻结角定义。"""

    if optimizer_profile not in OPTIMIZER_PROFILES:
        raise ValueError(f"未知OptKing优化策略: {optimizer_profile}")
    if max_iterations < 1:
        raise ValueError("最大优化步必须为正")
    options: dict[str, Any] = {
        "optking__frozen_dihedral": frozen_dihedral,
        "optking__geom_maxiter": max_iterations,
        "optking__g_convergence": "QCHEM",
    }
    if optimizer_profile in {"difficult_v2", "difficult_hessian_v3"}:
        options.update(
            {
                "optking__dynamic_level": 1,
                "optking__opt_coordinates": "BOTH",
                "optking__intrafrag_step_limit": 0.1,
            }
        )
    if optimizer_profile == "difficult_hessian_v3":
        options["optking__full_hess_every"] = 0
    return options


def load_initial_sdf_coordinates(
    path: Path,
    reference_molecule: Chem.Mol,
    source_angle_degrees: int,
) -> np.ndarray:
    if not path.is_file():
        raise ValueError(f"受约束松弛热启动SDF不存在: {path}")
    molecules = [
        molecule
        for molecule in Chem.SDMolSupplier(str(path), removeHs=False)
        if molecule is not None
        and molecule.HasProp("requested_angle_degrees")
        and molecule.GetIntProp("requested_angle_degrees") == source_angle_degrees
    ]
    if len(molecules) != 1:
        raise ValueError(
            f"受约束松弛热启动角度匹配不是1个: {source_angle_degrees}"
        )
    selected = molecules[0]
    if selected.GetNumAtoms() != reference_molecule.GetNumAtoms():
        raise ValueError("受约束松弛热启动原子数不一致")
    if [atom.GetSymbol() for atom in selected.GetAtoms()] != [
        atom.GetSymbol() for atom in reference_molecule.GetAtoms()
    ]:
        raise ValueError("受约束松弛热启动原子顺序不一致")
    reference_smiles = Chem.MolToSmiles(Chem.RemoveHs(Chem.Mol(reference_molecule)))
    selected_smiles = Chem.MolToSmiles(Chem.RemoveHs(Chem.Mol(selected)))
    if selected_smiles != reference_smiles:
        raise ValueError("受约束松弛热启动分子连接不一致")
    return np.asarray(selected.GetConformer().GetPositions(), dtype=float).copy()


def select_plan_rows(
    plan: pd.DataFrame,
    fragment_name: str,
    explicit_angles: Sequence[int] | None = None,
) -> pd.DataFrame:
    required = {
        "fragment_name",
        "validation_family",
        "angle_degrees",
        "selection_reason",
    }
    missing = sorted(required.difference(plan.columns))
    if missing:
        raise ValueError(f"受约束松弛计划缺字段: {missing}")
    selected = plan.loc[plan["fragment_name"].astype(str).eq(fragment_name)].copy()
    if explicit_angles:
        selected = selected.loc[
            selected["angle_degrees"].astype(int).isin(set(explicit_angles))
        ]
    selected = selected.sort_values("angle_degrees", kind="stable")
    if selected.empty or not selected["angle_degrees"].is_unique:
        raise ValueError(f"受约束松弛计划为空或角度重复: {fragment_name}")
    return selected.reset_index(drop=True)


def _version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        if name == "dftd3-python":
            try:
                import dftd3

                return str(dftd3.__version__)
            except (ImportError, AttributeError):
                pass
        return "not_found"


def run_relaxed_scan(
    plan_path: Path,
    output_root: Path,
    *,
    fragment_name: str,
    validation_family: str,
    smiles: str,
    explicit_angles: Sequence[int] | None,
    seed: int,
    threads: int,
    memory_gb: int,
    max_iterations: int,
    optimizer_profile: str,
    point_wall_seconds: int,
    initial_sdf_path: Path | None,
    initial_sdf_angle: int | None,
    release_id: str,
) -> dict[str, Any]:
    output_root = output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError(f"输出目录非空，拒绝覆盖: {output_root}")
    if not plan_path.is_file():
        raise ValueError(f"受约束松弛计划不存在: {plan_path}")
    if threads < 1 or memory_gb < 1 or max_iterations < 1:
        raise ValueError("受约束松弛线程、内存和最大步数必须为正")
    if point_wall_seconds < 0:
        raise ValueError("单点墙钟上限不能为负")
    if point_wall_seconds > 0 and os.name != "posix":
        raise ValueError("正墙钟上限只允许在POSIX计算节点使用")
    if (initial_sdf_path is None) != (initial_sdf_angle is None):
        raise ValueError("热启动SDF与热启动角度必须同时提供")
    if initial_sdf_path is not None and not initial_sdf_path.is_file():
        raise ValueError(f"热启动SDF不存在: {initial_sdf_path}")
    if optimizer_profile not in OPTIMIZER_PROFILES:
        raise ValueError(f"未知OptKing优化策略: {optimizer_profile}")
    plan = select_plan_rows(
        pd.read_csv(plan_path), fragment_name, explicit_angles=explicit_angles
    )
    if not plan["validation_family"].astype(str).eq(validation_family).all():
        raise ValueError("受约束松弛计划家族与参数不一致")
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "受约束松弛清单.json"
    started = time.monotonic()
    base: dict[str, Any] = {
        "release_id": release_id,
        "fragment_name": fragment_name,
        "validation_family": validation_family,
        "smiles": smiles,
        "random_seed": seed,
        "planned_angles_degrees": plan["angle_degrees"].astype(int).tolist(),
        "method": "wb97m-d3bj",
        "basis": "6-31G(d,p)",
        "constraint": "frozen_dihedral_current_value",
        "g_convergence": "QCHEM",
        "geom_maxiter": max_iterations,
        "optimizer_profile": optimizer_profile,
        "point_wall_seconds": point_wall_seconds,
        "initial_geometry": (
            {
                "path": str(initial_sdf_path),
                "sha256": sha256(initial_sdf_path),
                "source_angle_degrees": initial_sdf_angle,
            }
            if initial_sdf_path is not None
            else {"source": "deterministic_etkdg_mmff"}
        ),
        "optimizer_profile_rationale": (
            "Psi4/OptKing difficult-optimization guidance: dynamic level 1, "
            "redundant internals plus Cartesian coordinates, initial step limit 0.1 au."
            if optimizer_profile in {"difficult_v2", "difficult_hessian_v3"}
            else "Original v1 OptKing defaults with explicit QCHEM convergence."
        ),
        "production_md_permission": "blocked_parameter_refit_and_mm_relaxed_comparison",
        "performance_claim_status": "no_performance_claim",
    }
    cwd = Path.cwd()
    try:
        os.environ["OMP_NUM_THREADS"] = str(threads)
        os.environ["MKL_NUM_THREADS"] = str(threads)
        os.environ["OPENBLAS_NUM_THREADS"] = str(threads)
        import psi4

        heavy = Chem.MolFromSmiles(smiles)
        if heavy is None:
            raise ValueError("受约束松弛SMILES无法解析")
        torsion = select_urethane_torsion(heavy)
        molecule = Chem.AddHs(heavy)
        params = AllChem.ETKDGv3()
        params.randomSeed = seed
        params.useRandomCoords = True
        params.maxIterations = 1000
        if AllChem.EmbedMolecule(molecule, params) < 0:
            raise RuntimeError("受约束松弛ETKDG嵌入失败")
        mmff_status = int(
            AllChem.MMFFOptimizeMolecule(
                molecule, maxIters=1000, mmffVariant="MMFF94s"
            )
        )
        base_coordinates = (
            load_initial_sdf_coordinates(
                initial_sdf_path, molecule, int(initial_sdf_angle)
            )
            if initial_sdf_path is not None
            else np.array(
                molecule.GetConformer().GetPositions(), dtype=float, copy=True
            )
        )
        os.chdir(output_root)
        psi4.set_num_threads(threads)
        psi4.set_memory(f"{memory_gb} GB")
        psi4.core.set_output_file("psi4_relaxed_scan.log", False)
        rows: list[dict[str, Any]] = []
        final_molecules: list[Chem.Mol] = []
        frozen = " ".join(str(index + 1) for index in torsion)
        for source in plan.to_dict(orient="records"):
            requested_angle = int(source["angle_degrees"])
            conformer = molecule.GetConformer()
            for atom_index, position in enumerate(base_coordinates):
                conformer.SetAtomPosition(atom_index, position.tolist())
            rdMolTransforms.SetDihedralDeg(
                conformer, *torsion, float(requested_angle)
            )
            geometry_lines = ["0 1"]
            for atom_index, atom in enumerate(molecule.GetAtoms()):
                position = conformer.GetAtomPosition(atom_index)
                geometry_lines.append(
                    f"{atom.GetSymbol()} {position.x:.10f} {position.y:.10f} {position.z:.10f}"
                )
            geometry_lines.extend(["symmetry c1", "no_reorient", "no_com"])
            psi4_molecule = psi4.geometry("\n".join(geometry_lines))
            optking_options = build_optking_options(
                frozen, max_iterations, optimizer_profile
            )
            psi4.set_options(optking_options)
            point_started = time.monotonic()
            try:
                with point_wall_clock_limit(point_wall_seconds):
                    energy, wavefunction = psi4.optimize(
                        "wb97m-d3bj/6-31G(d,p)",
                        molecule=psi4_molecule,
                        return_wfn=True,
                    )
                optimized = wavefunction.molecule()
                coordinates = (
                    np.asarray(optimized.geometry().to_array(), dtype=float)
                    * psi4.constants.bohr2angstroms
                )
                final = Chem.Mol(molecule)
                final_conformer = final.GetConformer()
                for atom_index, position in enumerate(coordinates):
                    final_conformer.SetAtomPosition(atom_index, position.tolist())
                final.SetProp("fragment_name", fragment_name)
                final.SetIntProp("requested_angle_degrees", requested_angle)
                final_angle = float(
                    rdMolTransforms.GetDihedralDeg(final_conformer, *torsion)
                )
                final_molecules.append(final)
                rows.append(
                    {
                        "fragment_name": fragment_name,
                        "validation_family": validation_family,
                        "requested_angle_degrees": requested_angle,
                        "selection_reason": source["selection_reason"],
                        "final_angle_degrees": final_angle,
                        "angle_drift_degrees": min(
                            abs(final_angle - requested_angle),
                            abs(final_angle - requested_angle + 360),
                            abs(final_angle - requested_angle - 360),
                        ),
                        "relaxed_dft_energy_hartree": float(energy),
                        "point_status": "completed",
                        "elapsed_seconds": round(
                            time.monotonic() - point_started, 3
                        ),
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        "fragment_name": fragment_name,
                        "validation_family": validation_family,
                        "requested_angle_degrees": requested_angle,
                        "selection_reason": source["selection_reason"],
                        "final_angle_degrees": pd.NA,
                        "angle_drift_degrees": pd.NA,
                        "relaxed_dft_energy_hartree": pd.NA,
                        "point_status": f"failed_{type(exc).__name__}",
                        "error_message": str(exc).encode(
                            "utf-8", errors="backslashreplace"
                        ).decode("utf-8"),
                        "elapsed_seconds": round(
                            time.monotonic() - point_started, 3
                        ),
                    }
                )
            finally:
                psi4.core.clean()
            write_checkpoint(output_root, base, rows, len(plan))
        table = pd.DataFrame(rows).sort_values("requested_angle_degrees")
        successful = table["point_status"].eq("completed")
        if successful.any():
            minimum = table.loc[successful, "relaxed_dft_energy_hartree"].min()
            table.loc[successful, "relaxed_dft_relative_energy_kcal_mol"] = (
                table.loc[successful, "relaxed_dft_energy_hartree"] - minimum
            ) * HARTREE_TO_KCAL_MOL
        table_path = output_root / "relaxed_scan.csv"
        _atomic_text(table_path, table.to_csv(index=False, float_format="%.12g"))
        writer = Chem.SDWriter(str(output_root / "relaxed_geometries.sdf"))
        for final in final_molecules:
            writer.write(final)
        writer.close()
        write_checkpoint(
            output_root,
            base,
            rows,
            len(plan),
            checkpoint_status=(
                "completed_point_checkpoint"
                if successful.all()
                else "completed_point_loop_with_failures"
            ),
        )
        os.chdir(cwd)
        files = {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in sorted(output_root.iterdir(), key=lambda item: item.name)
            if path.is_file() and path.name != manifest_path.name
        }
        completed_count = int(successful.sum())
        manifest = {
            **base,
            "status": (
                "completed_constrained_relaxed_dft_points"
                if completed_count == len(table)
                else "incomplete_constrained_relaxed_dft_points"
            ),
            "counts": {
                "planned": len(table),
                "completed": completed_count,
                "failed": len(table) - completed_count,
            },
            "mmff_status_code": mmff_status,
            "torsion_atom_indices_zero_based": list(torsion),
            "maximum_angle_drift_degrees": (
                float(table.loc[successful, "angle_drift_degrees"].max())
                if successful.any()
                else None
            ),
            "versions": {
                "python": platform.python_version(),
                "psi4": getattr(psi4, "__version__", "unknown"),
                "dftd3_python": _version("dftd3-python"),
                "optking": _version("optking"),
            },
            "optking_options": build_optking_options(
                frozen, max_iterations, optimizer_profile
            ),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "files": files,
            "interpretation_limit": (
                "Frozen-dihedral relaxed DFT subset only; MM constrained relaxation "
                "and torsion refit are still required."
            ),
        }
        _atomic_text(
            manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
        return manifest
    except Exception as exc:
        os.chdir(cwd)
        failure = {
            **base,
            "status": "failed_constrained_relaxed_dft_scan",
            "error_type": type(exc).__name__,
            "error_message": str(exc).encode(
                "utf-8", errors="backslashreplace"
            ).decode("utf-8"),
            "traceback": traceback.format_exc().encode(
                "utf-8", errors="backslashreplace"
            ).decode("utf-8"),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        _atomic_text(
            manifest_path,
            json.dumps(failure, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--计划", type=Path, required=True)
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument("--片段名", required=True)
    parser.add_argument("--家族", required=True)
    parser.add_argument("--SMILES", required=True)
    parser.add_argument("--角度", type=int, action="append")
    parser.add_argument("--随机种子", type=int, default=20260825)
    parser.add_argument("--线程", type=int, default=1)
    parser.add_argument("--内存GB", type=int, default=8)
    parser.add_argument("--最大优化步", type=int, default=50)
    parser.add_argument(
        "--优化策略",
        choices=sorted(OPTIMIZER_PROFILES),
        default="standard_v1",
    )
    parser.add_argument(
        "--单点墙钟秒",
        type=int,
        default=0,
        help="0表示不限制；正值仅在POSIX计算节点用SIGALRM限制每个角度。",
    )
    parser.add_argument("--热启动SDF", type=Path)
    parser.add_argument("--热启动角度", type=int)
    parser.add_argument("--发布ID", required=True)
    args = parser.parse_args(argv)
    manifest = run_relaxed_scan(
        args.计划,
        args.输出目录,
        fragment_name=args.片段名,
        validation_family=args.家族,
        smiles=args.SMILES,
        explicit_angles=args.角度,
        seed=args.随机种子,
        threads=args.线程,
        memory_gb=args.内存GB,
        max_iterations=args.最大优化步,
        optimizer_profile=args.优化策略,
        point_wall_seconds=args.单点墙钟秒,
        initial_sdf_path=args.热启动SDF,
        initial_sdf_angle=args.热启动角度,
        release_id=args.发布ID,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0 if manifest["counts"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
