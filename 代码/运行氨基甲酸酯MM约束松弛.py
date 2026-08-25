"""用LAMMPS二面角约束最小化氨基甲酸酯模型并报告解除约束后的GAFF2能量。"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import os
import re
import subprocess
import time
import traceback
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
from rdkit import Chem
from rdkit.Chem import AllChem, rdMolTransforms

from 运行氨基甲酸酯刚性扫描 import (
    _atomic_text,
    select_urethane_torsion,
    sha256,
)
from 运行氨基甲酸酯受约束松弛 import select_plan_rows


def build_lammps_input(
    torsion: Sequence[int],
    angle_degrees: float,
    *,
    restraint_k: float,
) -> str:
    if len(torsion) != 4 or restraint_k <= 0:
        raise ValueError("LAMMPS约束扭转输入无效")
    atom_ids = [int(index) + 1 for index in torsion]
    # LAMMPS与RDKit对同一四原子二面角相差180°；统一到[-180, 180)。
    lammps_target_angle = ((float(angle_degrees) + 180.0 + 180.0) % 360.0) - 180.0
    return "\n".join(
        [
            "units real",
            "atom_style full",
            "boundary f f f",
            "pair_style lj/charmm/coul/charmm 8.0 12.0",
            "dielectric 1.0",
            "bond_style harmonic",
            "angle_style harmonic",
            "dihedral_style fourier",
            "improper_style cvff",
            "special_bonds amber",
            "pair_modify mix arithmetic",
            "neighbor 2.0 bin",
            "neigh_modify delay 0 every 1 check yes",
            "read_data point.data",
            (
                "fix REST all restrain dihedral "
                + " ".join(str(value) for value in atom_ids)
                + f" {restraint_k:.8f} {restraint_k:.8f} {lammps_target_angle:.8f}"
            ),
            "fix_modify REST energy yes",
            "thermo_style custom step pe ebond eangle edihed eimp evdwl ecoul f_REST",
            "thermo_modify flush yes",
            "thermo 100",
            "min_style cg",
            "minimize 1.0e-8 1.0e-6 20000 200000",
            "write_dump all custom restrained_final.dump id type x y z modify sort id",
            "unfix REST",
            "thermo_style custom step pe ebond eangle edihed eimp evdwl ecoul",
            "run 0",
            'variable upe equal pe',
            'print "UNRESTRAINED_PE $(v_upe:%.15g)" file unrestrained_energy.txt screen yes',
            "quit",
            "",
        ]
    )


def parse_unrestrained_energy(path: Path) -> float:
    match = re.search(
        r"UNRESTRAINED_PE\s+([-+0-9.eE]+)",
        path.read_text(encoding="utf-8", errors="replace"),
    )
    if not match:
        raise ValueError(f"LAMMPS未写出解除约束能量: {path}")
    return float(match.group(1))


def read_lammps_dump_coordinates(path: Path, atom_count: int) -> np.ndarray:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    try:
        header_index = lines.index("ITEM: ATOMS id type x y z")
    except ValueError as exc:
        raise ValueError(f"LAMMPS终态dump缺原子表: {path}") from exc
    rows = []
    for line in lines[header_index + 1 : header_index + 1 + atom_count]:
        parts = line.split()
        rows.append((int(parts[0]), [float(value) for value in parts[2:5]]))
    if len(rows) != atom_count:
        raise ValueError("LAMMPS终态dump原子数不闭合")
    rows.sort(key=lambda item: item[0])
    return np.asarray([coordinates for _, coordinates in rows], dtype=float)


def run_mm_relaxed_scan(
    plan_path: Path,
    joint_charge_path: Path,
    output_root: Path,
    *,
    fragment_name: str,
    validation_family: str,
    smiles: str,
    explicit_angles: Sequence[int] | None,
    seed: int,
    restraint_k: float,
    lammps_path: str,
    release_id: str,
) -> dict[str, Any]:
    output_root = output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError(f"输出目录非空，拒绝覆盖: {output_root}")
    for path in (plan_path, joint_charge_path):
        if not path.is_file():
            raise ValueError(f"MM约束松弛输入不存在: {path}")
    plan = select_plan_rows(
        pd.read_csv(plan_path), fragment_name, explicit_angles=explicit_angles
    )
    if not plan["validation_family"].astype(str).eq(validation_family).all():
        raise ValueError("MM约束松弛计划家族不一致")
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "MM约束松弛清单.json"
    started = time.monotonic()
    base: dict[str, Any] = {
        "release_id": release_id,
        "fragment_name": fragment_name,
        "validation_family": validation_family,
        "smiles": smiles,
        "planned_angles_degrees": plan["angle_degrees"].astype(int).tolist(),
        "random_seed": seed,
        "forcefield": "RadonPy_GAFF2_with_alternate_parameters",
        "charge_model": "three_conformer_joint_RESP_fragment",
        "restraint_k_kcal_mol_rad2": restraint_k,
        "dihedral_convention": "lammps_phi0_equals_wrapped_rdkit_angle_plus_180_degrees",
        "production_md_permission": "blocked_parameter_refit_and_dft_relaxed_comparison",
        "performance_claim_status": "no_performance_claim",
    }
    try:
        from radonpy.ff.gaff2 import GAFF2
        from radonpy.sim import lammps

        heavy = Chem.MolFromSmiles(smiles)
        if heavy is None:
            raise ValueError("MM约束松弛SMILES无法解析")
        torsion = select_urethane_torsion(heavy)
        molecule = Chem.AddHs(heavy)
        params = AllChem.ETKDGv3()
        params.randomSeed = seed
        params.useRandomCoords = True
        params.maxIterations = 1000
        if AllChem.EmbedMolecule(molecule, params) < 0:
            raise RuntimeError("MM约束松弛ETKDG嵌入失败")
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
            raise ValueError("MM约束松弛联合RESP原子数不一致")
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            assigned = bool(GAFF2().ff_assign(molecule, charge=None))
        if not assigned:
            raise RuntimeError("MM约束松弛GAFF2参数分配失败")
        for source in charge_table.to_dict(orient="records"):
            molecule.GetAtomWithIdx(int(source["atom_index_zero_based"])).SetDoubleProp(
                "AtomicCharge", float(source["joint_stage2_resp_charge_e"])
            )
        base_coordinates = np.asarray(
            molecule.GetConformer().GetPositions(), dtype=float
        ).copy()
        rows = []
        final_molecules = []
        runner = lammps.LAMMPS(
            work_dir=str(output_root), solver_path=lammps_path
        )
        for source in plan.to_dict(orient="records"):
            angle = int(source["angle_degrees"])
            point_root = output_root / f"point_{angle:+04d}"
            point_root.mkdir(parents=True, exist_ok=False)
            conformer = molecule.GetConformer()
            for atom_index, position in enumerate(base_coordinates):
                conformer.SetAtomPosition(atom_index, position.tolist())
            rdMolTransforms.SetDihedralDeg(conformer, *torsion, float(angle))
            if not runner.make_dat(
                molecule,
                file_name="point.data",
                dir_name=str(point_root),
                velocity=False,
            ):
                raise RuntimeError(f"MM约束松弛data生成失败: {angle}")
            input_path = point_root / "point.in"
            _atomic_text(
                input_path,
                build_lammps_input(torsion, angle, restraint_k=restraint_k),
            )
            point_started = time.monotonic()
            completed = subprocess.run(
                [lammps_path, "-in", "point.in", "-log", "point.log", "-screen", "screen.log"],
                cwd=point_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="backslashreplace",
                check=False,
                env={**os.environ, "OMP_NUM_THREADS": "1"},
            )
            screen_path = point_root / "subprocess_capture.log"
            _atomic_text(
                screen_path,
                "\n".join(
                    [
                        f"returncode={completed.returncode}",
                        "[stdout]",
                        completed.stdout,
                        "[stderr]",
                        completed.stderr,
                    ]
                ),
            )
            if completed.returncode != 0:
                rows.append(
                    {
                        "fragment_name": fragment_name,
                        "validation_family": validation_family,
                        "requested_angle_degrees": angle,
                        "selection_reason": source["selection_reason"],
                        "point_status": "failed_lammps_returncode",
                        "returncode": completed.returncode,
                        "elapsed_seconds": round(time.monotonic() - point_started, 3),
                    }
                )
                continue
            energy = parse_unrestrained_energy(point_root / "unrestrained_energy.txt")
            coordinates = read_lammps_dump_coordinates(
                point_root / "restrained_final.dump", molecule.GetNumAtoms()
            )
            final = Chem.Mol(molecule)
            final_conformer = final.GetConformer()
            for atom_index, position in enumerate(coordinates):
                final_conformer.SetAtomPosition(atom_index, position.tolist())
            final_angle = float(
                rdMolTransforms.GetDihedralDeg(final_conformer, *torsion)
            )
            final.SetProp("fragment_name", fragment_name)
            final.SetIntProp("requested_angle_degrees", angle)
            final_molecules.append(final)
            drift = min(
                abs(final_angle - angle),
                abs(final_angle - angle + 360),
                abs(final_angle - angle - 360),
            )
            rows.append(
                {
                    "fragment_name": fragment_name,
                    "validation_family": validation_family,
                    "requested_angle_degrees": angle,
                    "selection_reason": source["selection_reason"],
                    "final_angle_degrees": final_angle,
                    "angle_drift_degrees": drift,
                    "unrestrained_gaff2_energy_kcal_mol": energy,
                    "point_status": "completed",
                    "returncode": completed.returncode,
                    "elapsed_seconds": round(time.monotonic() - point_started, 3),
                }
            )
        table = pd.DataFrame(rows).sort_values("requested_angle_degrees")
        successful = table["point_status"].eq("completed")
        if successful.any():
            minimum = table.loc[successful, "unrestrained_gaff2_energy_kcal_mol"].min()
            table.loc[
                successful, "relaxed_gaff2_relative_energy_kcal_mol"
            ] = (
                table.loc[successful, "unrestrained_gaff2_energy_kcal_mol"]
                - minimum
            )
        table_path = output_root / "mm_relaxed_scan.csv"
        _atomic_text(table_path, table.to_csv(index=False, float_format="%.12g"))
        writer = Chem.SDWriter(str(output_root / "mm_relaxed_geometries.sdf"))
        for final in final_molecules:
            writer.write(final)
        writer.close()
        point_files = []
        for point_root in sorted(output_root.glob("point_*")):
            for path in sorted(point_root.iterdir()):
                if path.is_file():
                    point_files.append(
                        {
                            "path": str(path.relative_to(output_root)),
                            "bytes": path.stat().st_size,
                            "sha256": sha256(path),
                        }
                    )
        files = {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in [table_path, output_root / "mm_relaxed_geometries.sdf"]
        }
        manifest = {
            **base,
            "status": (
                "completed_mm_constrained_relaxed_points"
                if successful.all()
                else "incomplete_mm_constrained_relaxed_points"
            ),
            "counts": {
                "planned": len(table),
                "completed": int(successful.sum()),
                "failed": int((~successful).sum()),
            },
            "mmff_status_code": mmff_status,
            "torsion_atom_indices_zero_based": list(torsion),
            "maximum_angle_drift_degrees": (
                float(table.loc[successful, "angle_drift_degrees"].max())
                if successful.any()
                else None
            ),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "files": files,
            "point_files": point_files,
            "interpretation_limit": (
                "Harmonically restrained MM minimization; only points with measured "
                "angle drift and unrestrained run-0 energy are admissible."
            ),
        }
        _atomic_text(
            manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        return manifest
    except Exception as exc:
        failure = {
            **base,
            "status": "failed_mm_constrained_relaxed_scan",
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
    parser.add_argument("--联合RESP电荷", type=Path, required=True)
    parser.add_argument("--输出目录", type=Path, required=True)
    parser.add_argument("--片段名", required=True)
    parser.add_argument("--家族", required=True)
    parser.add_argument("--SMILES", required=True)
    parser.add_argument("--角度", type=int, action="append")
    parser.add_argument("--随机种子", type=int, default=20260825)
    parser.add_argument("--约束K", type=float, default=5000.0)
    parser.add_argument("--LAMMPS", default="/opt/tpu-md-venv/bin/lmp")
    parser.add_argument("--发布ID", required=True)
    args = parser.parse_args(argv)
    manifest = run_mm_relaxed_scan(
        args.计划,
        args.联合RESP电荷,
        args.输出目录,
        fragment_name=args.片段名,
        validation_family=args.家族,
        smiles=args.SMILES,
        explicit_angles=args.角度,
        seed=args.随机种子,
        restraint_k=args.约束K,
        lammps_path=args.LAMMPS,
        release_id=args.发布ID,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0 if manifest["counts"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
