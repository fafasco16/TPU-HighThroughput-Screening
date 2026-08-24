"""对同一TPU局部片段的多个确定性构象执行共同电荷两阶段RESP拟合。"""

from __future__ import annotations

import argparse
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

from 运行RESP小片段烟雾 import (
    _atomic_text,
    _distribution_version,
    robust_esp_solve,
    sha256,
    validate_charge_arrays,
)


DEFAULT_SEEDS = [20260825, 20260826, 20260827]


def validate_seeds(seeds: Sequence[int]) -> list[int]:
    normalized = sorted(int(seed) for seed in seeds)
    if len(normalized) < 2 or len(set(normalized)) != len(normalized):
        raise ValueError("联合RESP至少需要2个不重复构象种子")
    if any(seed < 1 for seed in normalized):
        raise ValueError("联合RESP构象种子必须为正")
    return normalized


def run_joint_resp(
    output_root: Path,
    *,
    smiles: str,
    fragment_name: str,
    seeds: Sequence[int],
    point_density: float,
    threads: int,
    memory_gb: int,
    release_id: str,
) -> dict[str, Any]:
    output_root = output_root.resolve()
    seeds = validate_seeds(seeds)
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError(f"输出目录非空，拒绝覆盖: {output_root}")
    if point_density <= 0 or threads < 1 or memory_gb < 1:
        raise ValueError("点密度、线程和内存必须为正")
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "RESP联合清单.json"
    started = time.monotonic()
    base: dict[str, Any] = {
        "release_id": release_id,
        "fragment_name": fragment_name,
        "smiles": smiles,
        "random_seeds": seeds,
        "conformer_count": len(seeds),
        "target_total_charge_e": 0,
        "multiplicity": 1,
        "method": "HF",
        "basis": "6-31G(d)",
        "vdw_scale_factors": [1.4, 1.6, 1.8, 2.0],
        "vdw_point_density": point_density,
        "resp_stage1_a": 0.0005,
        "resp_stage2_a": 0.001,
        "resp_b": 0.1,
        "fit_scope": "shared_atomic_charges_across_conformers_equal_weight",
        "production_md_permission": "blocked_fragment_to_polymer_transfer_and_forcefield_validation",
        "performance_claim_status": "no_performance_claim",
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
        molecules = []
        psi4_molecules = []
        conformer_records: list[dict[str, Any]] = []
        atom_count: int | None = None
        for conformer_index, seed in enumerate(seeds):
            molecule = Chem.AddHs(Chem.MolFromSmiles(smiles))
            if molecule is None:
                raise ValueError("联合RESP小片段SMILES无法解析")
            params = AllChem.ETKDGv3()
            params.randomSeed = seed
            params.useRandomCoords = True
            params.maxIterations = 1000
            if AllChem.EmbedMolecule(molecule, params) < 0:
                raise RuntimeError(f"ETKDGv3嵌入失败: seed={seed}")
            mmff_status = int(
                AllChem.MMFFOptimizeMolecule(
                    molecule, maxIters=1000, mmffVariant="MMFF94s"
                )
            )
            if atom_count is None:
                atom_count = molecule.GetNumAtoms()
            elif atom_count != molecule.GetNumAtoms():
                raise RuntimeError("联合RESP构象原子数不一致")
            conformer = molecule.GetConformer()
            geometry_lines = ["0 1"]
            for index, atom in enumerate(molecule.GetAtoms()):
                position = conformer.GetAtomPosition(index)
                geometry_lines.append(
                    f"{atom.GetSymbol()} {position.x:.10f} {position.y:.10f} {position.z:.10f}"
                )
            geometry_lines.extend(["symmetry c1", "no_reorient", "no_com"])
            psi4_molecule = psi4.geometry("\n".join(geometry_lines))
            name = f"{fragment_name}_conf_{conformer_index:02d}"
            psi4_molecule.set_name(name)
            molecules.append(molecule)
            psi4_molecules.append(psi4_molecule)
            conformer_records.append(
                {
                    "conformer_index": conformer_index,
                    "random_seed": seed,
                    "mmff_status_code": mmff_status,
                    "psi4_name": name,
                }
            )
        assert atom_count is not None

        os.chdir(output_root)
        psi4.set_num_threads(threads)
        psi4.set_memory(f"{memory_gb} GB")
        psi4.core.set_output_file("psi4_joint_resp.log", False)
        options: dict[str, Any] = {
            "VDW_SCALE_FACTORS": base["vdw_scale_factors"],
            "VDW_POINT_DENSITY": point_density,
            "RESP_A": base["resp_stage1_a"],
            "RESP_B": base["resp_b"],
            "METHOD_ESP": base["method"],
            "BASIS_ESP": base["basis"],
            "WEIGHT": [1.0] * len(psi4_molecules),
        }
        stage1 = np.asarray(resp.resp(psi4_molecules, options), dtype=float)
        (output_root / "results.out").replace(output_root / "stage1_results.out")
        grid_names = [
            f"{index + 1}_{record['psi4_name']}_grid.dat"
            for index, record in enumerate(conformer_records)
        ]
        esp_names = [
            f"{index + 1}_{record['psi4_name']}_grid_esp.dat"
            for index, record in enumerate(conformer_records)
        ]
        stage2_options: dict[str, Any] = {
            "GRID": grid_names,
            "ESP": esp_names,
            "RESP_A": base["resp_stage2_a"],
            "RESP_B": base["resp_b"],
            "WEIGHT": [1.0] * len(psi4_molecules),
        }
        resp.stage2_helper.set_stage2_constraint(
            psi4_molecules[0], stage1[1], stage2_options
        )
        stage2 = np.asarray(
            resp.resp(psi4_molecules, stage2_options), dtype=float
        )
        (output_root / "results.out").replace(output_root / "stage2_results.out")
        metrics = validate_charge_arrays(
            stage1,
            stage2,
            atom_count=atom_count,
            target_charge=0.0,
        )
        charge_rows = []
        for index, atom in enumerate(molecules[0].GetAtoms()):
            charge_rows.append(
                {
                    "atom_index_zero_based": index,
                    "element": atom.GetSymbol(),
                    "joint_stage1_esp_charge_e": stage1[0, index],
                    "joint_stage1_resp_charge_e": stage1[1, index],
                    "joint_stage2_esp_charge_e": stage2[0, index],
                    "joint_stage2_resp_charge_e": stage2[1, index],
                }
            )
            for molecule in molecules:
                molecule.GetAtomWithIdx(index).SetDoubleProp(
                    "RESP", float(stage2[1, index])
                )
                molecule.GetAtomWithIdx(index).SetDoubleProp(
                    "AtomicCharge", float(stage2[1, index])
                )
        _atomic_text(
            output_root / "joint_resp_charges.csv",
            pd.DataFrame(charge_rows).to_csv(index=False, float_format="%.12g"),
        )
        _atomic_text(
            output_root / "conformer_inventory.csv",
            pd.DataFrame(conformer_records).to_csv(index=False),
        )
        writer = Chem.SDWriter(str(output_root / "joint_conformers_with_resp.sdf"))
        for molecule in molecules:
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
            "status": "completed_joint_multiconformer_resp_fragment",
            "atom_count": atom_count,
            "charge_metrics": metrics,
            "conformers": conformer_records,
            "versions": {
                "python": platform.python_version(),
                "psi4": getattr(psi4, "__version__", "unknown"),
                "resp": getattr(resp, "__version__", "unknown"),
                "rdkit": _distribution_version("rdkit"),
                "numpy": _distribution_version("numpy"),
                "radonpy_pypi": _distribution_version("radonpy-pypi"),
            },
            "esp_linear_solver": "solve_with_conditioned_lstsq_fallback",
            "threads": threads,
            "memory_gb": memory_gb,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "files": files,
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
            "status": "failed_joint_multiconformer_resp_fragment",
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
    parser.add_argument("--SMILES", required=True)
    parser.add_argument("--片段名", required=True)
    parser.add_argument("--种子", type=int, action="append")
    parser.add_argument("--点密度", type=float, default=1.0)
    parser.add_argument("--线程", type=int, default=1)
    parser.add_argument("--内存GB", type=int, default=8)
    parser.add_argument("--发布ID", required=True)
    args = parser.parse_args(argv)
    manifest = run_joint_resp(
        args.输出目录,
        smiles=args.SMILES,
        fragment_name=args.片段名,
        seeds=args.种子 or DEFAULT_SEEDS,
        point_density=args.点密度,
        threads=args.线程,
        memory_gb=args.内存GB,
        release_id=args.发布ID,
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "fragment_name": manifest["fragment_name"],
                "conformer_count": manifest["conformer_count"],
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
