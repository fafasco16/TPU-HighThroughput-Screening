"""在相同刚性构象上比较氨基甲酸酯O=C-N-R扭转的ωB97M-D3BJ与GAFF2势能。"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import io
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
from rdkit import Chem
from rdkit.Chem import AllChem, rdMolTransforms


ROOT = Path(__file__).resolve().parents[1]
HARTREE_TO_KCAL_MOL = 627.509474
URETHANE_PATTERN = Chem.MolFromSmarts("OC(=O)N")


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


def default_angle_grid(step_degrees: int = 15) -> list[int]:
    if step_degrees < 1 or 360 % step_degrees != 0:
        raise ValueError("刚性扫描角度步长必须为360的正整数因子")
    return list(range(-180, 180, step_degrees))


def select_urethane_torsion(molecule: Chem.Mol) -> tuple[int, int, int, int]:
    matches = molecule.GetSubstructMatches(URETHANE_PATTERN)
    if len(matches) != 1:
        raise ValueError(f"刚性扫描模型氨基甲酸酯核心不是1个: {len(matches)}")
    alkoxy_o, carbonyl_c, carbonyl_o, nitrogen = matches[0]
    external = [
        neighbor.GetIdx()
        for neighbor in molecule.GetAtomWithIdx(nitrogen).GetNeighbors()
        if neighbor.GetIdx() != carbonyl_c and neighbor.GetAtomicNum() > 1
    ]
    if len(external) != 1:
        raise ValueError("刚性扫描氨基甲酸酯N外部重原子邻居不是1个")
    return carbonyl_o, carbonyl_c, nitrogen, external[0]


def _version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not_found"


def run_scan(
    joint_charge_path: Path,
    output_root: Path,
    *,
    fragment_name: str,
    validation_family: str,
    smiles: str,
    angles: Sequence[int],
    seed: int,
    threads: int,
    memory_gb: int,
    release_id: str,
) -> dict[str, Any]:
    output_root = output_root.resolve()
    angles = sorted(set(int(angle) for angle in angles))
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError(f"输出目录非空，拒绝覆盖: {output_root}")
    if not joint_charge_path.is_file():
        raise ValueError(f"联合RESP电荷不存在: {joint_charge_path}")
    if len(angles) < 8 or any(angle < -180 or angle >= 180 for angle in angles):
        raise ValueError("刚性扫描角度必须在[-180,180)且至少8点")
    if threads < 1 or memory_gb < 1:
        raise ValueError("刚性扫描线程和内存必须为正")
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "刚性扫描清单.json"
    started = time.monotonic()
    base: dict[str, Any] = {
        "release_id": release_id,
        "fragment_name": fragment_name,
        "validation_family": validation_family,
        "smiles": smiles,
        "random_seed": seed,
        "angles_degrees": angles,
        "dft_method": "wb97m-d3bj",
        "dft_basis": "6-31G(d,p)",
        "scan_type": "rigid_single_point_same_geometry",
        "production_md_permission": "blocked_rigid_scan_screening_only",
        "performance_claim_status": "no_performance_claim",
    }
    cwd = Path.cwd()
    try:
        os.environ["OMP_NUM_THREADS"] = str(threads)
        os.environ["MKL_NUM_THREADS"] = str(threads)
        os.environ["OPENBLAS_NUM_THREADS"] = str(threads)
        import psi4
        from radonpy.core import calc
        from radonpy.ff.gaff2 import GAFF2

        heavy = Chem.MolFromSmiles(smiles)
        if heavy is None:
            raise ValueError("刚性扫描SMILES无法解析")
        torsion = select_urethane_torsion(heavy)
        molecule = Chem.AddHs(heavy)
        params = AllChem.ETKDGv3()
        params.randomSeed = seed
        params.useRandomCoords = True
        params.maxIterations = 1000
        if AllChem.EmbedMolecule(molecule, params) < 0:
            raise RuntimeError("刚性扫描ETKDG嵌入失败")
        mmff_status = int(
            AllChem.MMFFOptimizeMolecule(
                molecule, maxIters=1000, mmffVariant="MMFF94s"
            )
        )
        charge_table = pd.read_csv(joint_charge_path)
        charge_table = charge_table.loc[
            charge_table["fragment_name"].astype(str).eq(fragment_name)
        ].sort_values("atom_index_zero_based", kind="stable")
        if len(charge_table) != molecule.GetNumAtoms():
            raise ValueError("联合RESP电荷原子数与扫描模型不一致")
        captured = io.StringIO()
        forcefield = GAFF2()
        with contextlib.redirect_stdout(captured):
            assigned = bool(forcefield.ff_assign(molecule, charge=None))
        if not assigned:
            raise RuntimeError("刚性扫描GAFF2参数分配失败")
        for source in charge_table.to_dict(orient="records"):
            index = int(source["atom_index_zero_based"])
            molecule.GetAtomWithIdx(index).SetDoubleProp(
                "AtomicCharge", float(source["joint_stage2_resp_charge_e"])
            )
        calc.set_charge_lj_matrix(molecule, forcefield)
        target_dihedral = None
        for dihedral in molecule.dihedrals.values():
            if (dihedral.a, dihedral.b, dihedral.c, dihedral.d) == torsion:
                target_dihedral = dihedral
                break
        if target_dihedral is None:
            raise RuntimeError(f"GAFF2未生成目标扭转参数: {torsion}")
        base_coordinates = np.array(
            molecule.GetConformer().GetPositions(), dtype=float, copy=True
        )
        os.chdir(output_root)
        psi4.set_num_threads(threads)
        psi4.set_memory(f"{memory_gb} GB")
        psi4.core.set_output_file("psi4_rigid_scan.log", False)
        rows: list[dict[str, Any]] = []
        for requested_angle in angles:
            conformer = molecule.GetConformer()
            for atom_index, position in enumerate(base_coordinates):
                conformer.SetAtomPosition(atom_index, position.tolist())
            rdMolTransforms.SetDihedralDeg(conformer, *torsion, float(requested_angle))
            actual_angle = float(rdMolTransforms.GetDihedralDeg(conformer, *torsion))
            energies = calc.energy_mm(molecule)
            geometry_lines = ["0 1"]
            for atom_index, atom in enumerate(molecule.GetAtoms()):
                position = conformer.GetAtomPosition(atom_index)
                geometry_lines.append(
                    f"{atom.GetSymbol()} {position.x:.10f} {position.y:.10f} {position.z:.10f}"
                )
            geometry_lines.extend(["symmetry c1", "no_reorient", "no_com"])
            psi4_molecule = psi4.geometry("\n".join(geometry_lines))
            dft_energy = float(
                psi4.energy("wb97m-d3bj/6-31G(d,p)", molecule=psi4_molecule)
            )
            psi4.core.clean()
            rows.append(
                {
                    "fragment_name": fragment_name,
                    "validation_family": validation_family,
                    "requested_angle_degrees": requested_angle,
                    "actual_angle_degrees": actual_angle,
                    "dft_energy_hartree": dft_energy,
                    "gaff2_total_energy_kcal_mol": energies[0],
                    "gaff2_bond_energy_kcal_mol": energies[1],
                    "gaff2_angle_energy_kcal_mol": energies[2],
                    "gaff2_dihedral_energy_kcal_mol": energies[3],
                    "gaff2_improper_energy_kcal_mol": energies[4],
                    "gaff2_coulomb_energy_kcal_mol": energies[5],
                    "gaff2_lj_energy_kcal_mol": energies[6],
                    "point_status": "completed",
                }
            )
        table = pd.DataFrame(rows).sort_values("requested_angle_degrees")
        table["dft_relative_energy_kcal_mol"] = (
            table["dft_energy_hartree"] - table["dft_energy_hartree"].min()
        ) * HARTREE_TO_KCAL_MOL
        table["gaff2_relative_energy_kcal_mol"] = (
            table["gaff2_total_energy_kcal_mol"]
            - table["gaff2_total_energy_kcal_mol"].min()
        )
        scan_path = output_root / "rigid_scan.csv"
        _atomic_text(scan_path, table.to_csv(index=False, float_format="%.12g"))
        writer = Chem.SDWriter(str(output_root / "base_with_joint_resp.sdf"))
        writer.write(molecule)
        writer.close()
        os.chdir(cwd)
        alternate_lines = sorted(
            set(
                line.strip()
                for line in captured.getvalue().splitlines()
                if "Using alternate" in line
            )
        )
        dft_curve = table["dft_relative_energy_kcal_mol"]
        mm_curve = table["gaff2_relative_energy_kcal_mol"]
        metrics = {
            "point_count": len(table),
            "curve_rmse_kcal_mol": float(
                np.sqrt(np.mean(np.square(dft_curve - mm_curve)))
            ),
            "curve_pearson_r": float(dft_curve.corr(mm_curve)),
            "dft_rigid_barrier_kcal_mol": float(dft_curve.max()),
            "gaff2_rigid_barrier_kcal_mol": float(mm_curve.max()),
            "barrier_difference_gaff2_minus_dft_kcal_mol": float(
                mm_curve.max() - dft_curve.max()
            ),
            "dft_minimum_angle_degrees": int(
                table.loc[dft_curve.idxmin(), "requested_angle_degrees"]
            ),
            "gaff2_minimum_angle_degrees": int(
                table.loc[mm_curve.idxmin(), "requested_angle_degrees"]
            ),
        }
        files = {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in sorted(output_root.iterdir(), key=lambda item: item.name)
            if path.is_file() and path.name != manifest_path.name
        }
        manifest = {
            **base,
            "status": "completed_rigid_dft_gaff2_scan_screening",
            "atom_count": molecule.GetNumAtoms(),
            "mmff_status_code": mmff_status,
            "torsion_atom_indices_zero_based": list(torsion),
            "target_gaff2_dihedral_type": target_dihedral.ff.type,
            "target_gaff2_dihedral_k": target_dihedral.ff.k.tolist(),
            "target_gaff2_dihedral_periodicity": target_dihedral.ff.n.tolist(),
            "alternate_parameter_unique_count": len(alternate_lines),
            "alternate_parameter_unique_sha256": hashlib.sha256(
                "\n".join(alternate_lines).encode("utf-8")
            ).hexdigest(),
            "metrics": metrics,
            "versions": {
                "python": platform.python_version(),
                "psi4": getattr(psi4, "__version__", "unknown"),
                "dftd3_python": _version("dftd3-python"),
                "radonpy_pypi": _version("radonpy-pypi"),
                "rdkit": _version("rdkit"),
            },
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "files": files,
            "interpretation_limit": (
                "Rigid same-geometry single-point screening; not a relaxed torsion "
                "parameterization or production force-field validation."
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
            "status": "failed_rigid_dft_gaff2_scan",
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
    parser.add_argument("--联合RESP电荷", type=Path, required=True)
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument("--片段名", required=True)
    parser.add_argument("--家族", required=True)
    parser.add_argument("--SMILES", required=True)
    parser.add_argument("--角度步长", type=int, default=15)
    parser.add_argument("--随机种子", type=int, default=20260825)
    parser.add_argument("--线程", type=int, default=1)
    parser.add_argument("--内存GB", type=int, default=8)
    parser.add_argument("--发布ID", required=True)
    args = parser.parse_args(argv)
    manifest = run_scan(
        args.联合RESP电荷,
        args.输出目录,
        fragment_name=args.片段名,
        validation_family=args.家族,
        smiles=args.SMILES,
        angles=default_angle_grid(args.角度步长),
        seed=args.随机种子,
        threads=args.线程,
        memory_gb=args.内存GB,
        release_id=args.发布ID,
    )
    print(json.dumps(manifest["metrics"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
