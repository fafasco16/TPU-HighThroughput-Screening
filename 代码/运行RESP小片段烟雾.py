"""运行含氨基甲酸酯键小片段的原生Psi4/RESP两阶段电荷烟雾。"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import time
import traceback
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd


DEFAULT_SMILES = "COC(=O)NC"
DEFAULT_NAME = "methyl_n_methyl_carbamate"
DEFAULT_SEED = 20260825


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


def validate_charge_arrays(
    stage1: np.ndarray,
    stage2: np.ndarray,
    *,
    atom_count: int,
    target_charge: float,
    charge_tolerance: float = 1.0e-8,
) -> dict[str, float]:
    if stage1.shape != (2, atom_count) or stage2.shape != (2, atom_count):
        raise ValueError("ESP/RESP数组维度与原子数不一致")
    if not np.isfinite(stage1).all() or not np.isfinite(stage2).all():
        raise ValueError("ESP/RESP数组含非有限值")
    stage1_resp_sum = float(math.fsum(stage1[1].tolist()))
    stage2_resp_sum = float(math.fsum(stage2[1].tolist()))
    if abs(stage1_resp_sum - target_charge) > charge_tolerance:
        raise ValueError("第一阶段RESP电荷和不闭合")
    if abs(stage2_resp_sum - target_charge) > charge_tolerance:
        raise ValueError("第二阶段RESP电荷和不闭合")
    return {
        "stage1_esp_charge_sum_e": float(math.fsum(stage1[0].tolist())),
        "stage1_resp_charge_sum_e": stage1_resp_sum,
        "stage2_esp_charge_sum_e": float(math.fsum(stage2[0].tolist())),
        "stage2_resp_charge_sum_e": stage2_resp_sum,
        "stage1_to_stage2_resp_rms_e": float(
            np.sqrt(np.mean(np.square(stage2[1] - stage1[1])))
        ),
        "stage2_resp_min_e": float(stage2[1].min()),
        "stage2_resp_max_e": float(stage2[1].max()),
    }


def robust_esp_solve(matrix: np.ndarray, vector: np.ndarray) -> np.ndarray:
    """RESP线性方程奇异或病态时使用最小二乘，口径与RadonPy防护一致。"""
    try:
        charges = np.linalg.solve(matrix, vector)
    except np.linalg.LinAlgError:
        charges = np.linalg.lstsq(matrix, vector, rcond=None)[0]
    if np.linalg.cond(matrix) > 1 / np.finfo(matrix.dtype).eps:
        charges = np.linalg.lstsq(matrix, vector, rcond=None)[0]
    return charges


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not_found"


def run_smoke(
    output_root: Path,
    *,
    smiles: str,
    fragment_name: str,
    point_density: float,
    threads: int,
    memory_gb: int,
    seed: int,
    release_id: str,
) -> dict[str, Any]:
    output_root = output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError(f"输出目录非空，拒绝覆盖: {output_root}")
    if point_density <= 0 or threads < 1 or memory_gb < 1:
        raise ValueError("点密度、线程和内存必须为正")
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "RESP烟雾清单.json"
    started = time.monotonic()
    base: dict[str, Any] = {
        "release_id": release_id,
        "fragment_name": fragment_name,
        "smiles": smiles,
        "random_seed": seed,
        "target_total_charge_e": 0,
        "multiplicity": 1,
        "method": "HF",
        "basis": "6-31G(d)",
        "vdw_scale_factors": [1.4, 1.6, 1.8, 2.0],
        "vdw_point_density": point_density,
        "resp_stage1_a": 0.0005,
        "resp_stage2_a": 0.001,
        "resp_b": 0.1,
        "production_md_permission": "blocked_fragment_and_conformer_validation_pending",
        "performance_claim_status": "no_performance_claim",
        "interpretation_limit": (
            "Native two-stage RESP execution smoke on one MMFF geometry; not yet a "
            "transferable TPU production charge set."
        ),
    }
    cwd = Path.cwd()
    try:
        os.environ["OMP_NUM_THREADS"] = str(threads)
        os.environ["MKL_NUM_THREADS"] = str(threads)
        os.environ["OPENBLAS_NUM_THREADS"] = str(threads)
        from rdkit import Chem
        from rdkit.Chem import AllChem
        import psi4
        import resp

        resp.espfit.esp_solve = robust_esp_solve

        molecule = Chem.AddHs(Chem.MolFromSmiles(smiles))
        if molecule is None:
            raise ValueError("小片段SMILES无法解析")
        params = AllChem.ETKDGv3()
        params.randomSeed = seed
        params.useRandomCoords = True
        params.maxIterations = 1000
        if AllChem.EmbedMolecule(molecule, params) < 0:
            raise RuntimeError("ETKDGv3嵌入失败")
        mmff_status = int(
            AllChem.MMFFOptimizeMolecule(
                molecule, maxIters=1000, mmffVariant="MMFF94s"
            )
        )
        conformer = molecule.GetConformer()
        geometry_lines = ["0 1"]
        for index, atom in enumerate(molecule.GetAtoms()):
            position = conformer.GetAtomPosition(index)
            geometry_lines.append(
                f"{atom.GetSymbol()} {position.x:.10f} {position.y:.10f} {position.z:.10f}"
            )
        geometry_lines.extend(["symmetry c1", "no_reorient", "no_com"])

        os.chdir(output_root)
        psi4.set_num_threads(threads)
        psi4.set_memory(f"{memory_gb} GB")
        psi4.core.set_output_file("psi4_resp.log", False)
        psi4_molecule = psi4.geometry("\n".join(geometry_lines))
        psi4_molecule.set_name(fragment_name)
        options: dict[str, Any] = {
            "VDW_SCALE_FACTORS": base["vdw_scale_factors"],
            "VDW_POINT_DENSITY": point_density,
            "RESP_A": base["resp_stage1_a"],
            "RESP_B": base["resp_b"],
            "METHOD_ESP": base["method"],
            "BASIS_ESP": base["basis"],
        }
        stage1 = np.asarray(resp.resp([psi4_molecule], options), dtype=float)
        stage1_results = output_root / "stage1_results.out"
        (output_root / "results.out").replace(stage1_results)

        grid_name = f"1_{fragment_name}_grid.dat"
        esp_name = f"1_{fragment_name}_grid_esp.dat"
        stage2_options = {
            "GRID": [grid_name],
            "ESP": [esp_name],
            "RESP_A": base["resp_stage2_a"],
            "RESP_B": base["resp_b"],
        }
        resp.stage2_helper.set_stage2_constraint(
            psi4_molecule, stage1[1], stage2_options
        )
        stage2 = np.asarray(
            resp.resp([psi4_molecule], stage2_options), dtype=float
        )
        stage2_results = output_root / "stage2_results.out"
        (output_root / "results.out").replace(stage2_results)
        metrics = validate_charge_arrays(
            stage1,
            stage2,
            atom_count=molecule.GetNumAtoms(),
            target_charge=0.0,
        )

        charge_rows = []
        for index, atom in enumerate(molecule.GetAtoms()):
            charge_rows.append(
                {
                    "atom_index_zero_based": index,
                    "element": atom.GetSymbol(),
                    "stage1_esp_charge_e": stage1[0, index],
                    "stage1_resp_charge_e": stage1[1, index],
                    "stage2_esp_charge_e": stage2[0, index],
                    "stage2_resp_charge_e": stage2[1, index],
                }
            )
            atom.SetDoubleProp("RESP", float(stage2[1, index]))
            atom.SetDoubleProp("AtomicCharge", float(stage2[1, index]))
        charge_path = output_root / "resp_charges.csv"
        _atomic_text(
            charge_path,
            pd.DataFrame(charge_rows).to_csv(index=False, float_format="%.12g"),
        )
        sdf_path = output_root / "fragment_with_resp.sdf"
        writer = Chem.SDWriter(str(sdf_path))
        writer.write(molecule)
        writer.close()
        psi4.core.clean()
        os.chdir(cwd)

        files = {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in sorted(output_root.iterdir(), key=lambda item: item.name)
            if path.is_file() and path.name != manifest_path.name
        }
        manifest = {
            **base,
            "status": "completed_native_two_stage_resp_smoke",
            "atom_count": molecule.GetNumAtoms(),
            "mmff_status_code": mmff_status,
            "charge_metrics": metrics,
            "versions": {
                "python": platform.python_version(),
                "psi4": getattr(psi4, "__version__", "unknown"),
                "resp": getattr(resp, "__version__", "unknown"),
                "rdkit": _distribution_version("rdkit"),
                "numpy": _distribution_version("numpy"),
                "radonpy_pypi": _distribution_version("radonpy-pypi"),
            },
            "threads": threads,
            "memory_gb": memory_gb,
            "esp_linear_solver": "solve_with_conditioned_lstsq_fallback",
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "files": files,
        }
        _atomic_text(
            manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        return manifest
    except Exception as exc:
        os.chdir(cwd)
        failure = {
            **base,
            "status": "failed_native_two_stage_resp_smoke",
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
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument("--SMILES", default=DEFAULT_SMILES)
    parser.add_argument("--片段名", default=DEFAULT_NAME)
    parser.add_argument("--点密度", type=float, default=1.0)
    parser.add_argument("--线程", type=int, default=1)
    parser.add_argument("--内存GB", type=int, default=8)
    parser.add_argument("--随机种子", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--发布ID", default="tpu-reality-md-native-resp-smoke-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = run_smoke(
        args.输出目录,
        smiles=args.SMILES,
        fragment_name=args.片段名,
        point_density=args.点密度,
        threads=args.线程,
        memory_gb=args.内存GB,
        seed=args.随机种子,
        release_id=args.发布ID,
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "atom_count": manifest["atom_count"],
                "charge_metrics": manifest["charge_metrics"],
                "elapsed_seconds": manifest["elapsed_seconds"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
