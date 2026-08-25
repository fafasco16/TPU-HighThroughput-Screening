"""固定联合RESP核心并最小L2中性化未映射原子，诊断12条链的完整电荷缺口。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
from rdkit import Chem
from rdkit.Chem import rdPartialCharges


ROOT = Path(__file__).resolve().parents[1]
E_ANGSTROM_TO_DEBYE = 4.80320471257


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


def complete_charges_minimum_l2(
    initial: np.ndarray,
    fixed_indices: np.ndarray,
    fixed_values: np.ndarray,
    *,
    target_total_charge: float = 0.0,
) -> tuple[np.ndarray, float]:
    charges = np.asarray(initial, dtype=float).copy()
    fixed_indices = np.asarray(fixed_indices, dtype=int)
    fixed_values = np.asarray(fixed_values, dtype=float)
    if charges.ndim != 1 or len(fixed_indices) != len(fixed_values):
        raise ValueError("混合电荷输入维度不一致")
    if not np.isfinite(charges).all() or not np.isfinite(fixed_values).all():
        raise ValueError("混合电荷输入含非有限值")
    if len(set(fixed_indices.tolist())) != len(fixed_indices):
        raise ValueError("RESP固定核心原子索引重复")
    if np.any(fixed_indices < 0) or np.any(fixed_indices >= len(charges)):
        raise ValueError("RESP固定核心原子索引越界")
    fixed_mask = np.zeros(len(charges), dtype=bool)
    fixed_mask[fixed_indices] = True
    free_indices = np.flatnonzero(~fixed_mask)
    if len(free_indices) == 0:
        raise ValueError("没有可用于中性化的未映射原子")
    charges[fixed_indices] = fixed_values
    residual = target_total_charge - float(math.fsum(charges.tolist()))
    uniform_correction = residual / len(free_indices)
    charges[free_indices] += uniform_correction
    final_residual = target_total_charge - float(math.fsum(charges.tolist()))
    charges[free_indices[-1]] += final_residual
    if abs(float(math.fsum(charges.tolist())) - target_total_charge) > 1.0e-10:
        raise RuntimeError("混合电荷中性化未闭合")
    if not np.allclose(charges[fixed_indices], fixed_values, atol=1.0e-14, rtol=0):
        raise RuntimeError("混合电荷中性化改变了固定RESP核心")
    return charges, uniform_correction


def point_charge_dipole_debye(charges: np.ndarray, coordinates: np.ndarray) -> np.ndarray:
    charges = np.asarray(charges, dtype=float)
    coordinates = np.asarray(coordinates, dtype=float)
    if coordinates.shape != (len(charges), 3):
        raise ValueError("点电荷偶极坐标维度不一致")
    if not np.isfinite(charges).all() or not np.isfinite(coordinates).all():
        raise ValueError("点电荷偶极输入含非有限值")
    centered = coordinates - coordinates.mean(axis=0)
    return np.sum(charges[:, None] * centered, axis=0) * E_ANGSTROM_TO_DEBYE


def read_xyz(path: Path) -> tuple[list[str], np.ndarray]:
    lines = path.read_text(encoding="ascii").splitlines()
    atom_count = int(lines[0].strip())
    atom_lines = lines[2 : 2 + atom_count]
    if len(atom_lines) != atom_count:
        raise ValueError(f"XYZ原子行数不闭合: {path}")
    elements = []
    coordinates = []
    for line in atom_lines:
        parts = line.split()
        elements.append(parts[0])
        coordinates.append([float(value) for value in parts[1:4]])
    return elements, np.asarray(coordinates, dtype=float)


def write_release(
    graph_path: Path,
    core_mapping_path: Path,
    seed_table_path: Path,
    seed_root: Path,
    output_root: Path,
    *,
    release_id: str,
) -> dict[str, Any]:
    for path in (graph_path, core_mapping_path, seed_table_path):
        if not path.is_file():
            raise ValueError(f"混合电荷诊断输入不存在: {path}")
    graphs = pd.read_csv(graph_path)
    mappings = pd.read_csv(core_mapping_path)
    seeds = pd.read_csv(seed_table_path)
    merged = graphs.merge(
        seeds[["formulation_id", "xyz_file", "xyz_sha256", "atom_order_sha256"]],
        on="formulation_id",
        how="inner",
        validate="one_to_one",
    )
    if len(merged) != 12:
        raise ValueError(f"混合电荷诊断不是12条链: {len(merged)}")
    atom_frames: list[pd.DataFrame] = []
    summary_rows: list[dict[str, Any]] = []
    xyz_records: dict[str, dict[str, Any]] = {}
    for source in merged.sort_values("formulation_id", kind="stable").to_dict(
        orient="records"
    ):
        formulation_id = str(source["formulation_id"])
        molecule = Chem.AddHs(Chem.MolFromSmiles(str(source["canonical_smiles"])))
        if molecule is None:
            raise ValueError(f"混合电荷链SMILES无法解析: {formulation_id}")
        rdPartialCharges.ComputeGasteigerCharges(molecule)
        initial = np.asarray(
            [float(atom.GetProp("_GasteigerCharge")) for atom in molecule.GetAtoms()],
            dtype=float,
        )
        core = mappings.loc[
            mappings["formulation_id"].astype(str).eq(formulation_id)
        ].sort_values("chain_atom_index_zero_based", kind="stable")
        fixed_indices = core["chain_atom_index_zero_based"].astype(int).to_numpy()
        fixed_values = core["joint_resp_transfer_charge_e"].astype(float).to_numpy()
        hybrid, correction = complete_charges_minimum_l2(
            initial, fixed_indices, fixed_values
        )
        xyz_path = seed_root / str(source["xyz_file"])
        if not xyz_path.is_file() or sha256(xyz_path) != source["xyz_sha256"]:
            raise ValueError(f"混合电荷XYZ不存在或哈希错误: {formulation_id}")
        elements, coordinates = read_xyz(xyz_path)
        molecule_elements = [atom.GetSymbol() for atom in molecule.GetAtoms()]
        if elements != molecule_elements or len(elements) != len(hybrid):
            raise ValueError(f"混合电荷XYZ原子顺序不闭合: {formulation_id}")
        fixed_mask = np.zeros(len(hybrid), dtype=bool)
        fixed_mask[fixed_indices] = True
        gasteiger_dipole = point_charge_dipole_debye(initial, coordinates)
        hybrid_dipole = point_charge_dipole_debye(hybrid, coordinates)
        dipole_delta = hybrid_dipole - gasteiger_dipole
        atom_frames.append(
            pd.DataFrame(
                {
                    "formulation_id": formulation_id,
                    "atom_index_zero_based": np.arange(len(hybrid)),
                    "element": elements,
                    "resp_core_fixed": fixed_mask,
                    "gasteiger_charge_e": initial,
                    "hybrid_diagnostic_charge_e": hybrid,
                    "hybrid_minus_gasteiger_e": hybrid - initial,
                    "charge_role": np.where(
                        fixed_mask,
                        "fixed_joint_resp_core",
                        "unmapped_gasteiger_plus_uniform_l2_correction",
                    ),
                    "production_charge_permission": "blocked_diagnostic_only",
                }
            )
        )
        summary_rows.append(
            {
                "formulation_id": formulation_id,
                "atom_count": len(hybrid),
                "fixed_resp_core_atom_count": len(fixed_indices),
                "unmapped_atom_count": len(hybrid) - len(fixed_indices),
                "gasteiger_total_charge_e": float(math.fsum(initial.tolist())),
                "hybrid_total_charge_e": float(math.fsum(hybrid.tolist())),
                "uniform_unmapped_correction_e": correction,
                "maximum_absolute_atom_charge_change_e": float(
                    np.max(np.abs(hybrid - initial))
                ),
                "gasteiger_dipole_x_debye": gasteiger_dipole[0],
                "gasteiger_dipole_y_debye": gasteiger_dipole[1],
                "gasteiger_dipole_z_debye": gasteiger_dipole[2],
                "gasteiger_dipole_norm_debye": float(
                    np.linalg.norm(gasteiger_dipole)
                ),
                "hybrid_dipole_x_debye": hybrid_dipole[0],
                "hybrid_dipole_y_debye": hybrid_dipole[1],
                "hybrid_dipole_z_debye": hybrid_dipole[2],
                "hybrid_dipole_norm_debye": float(np.linalg.norm(hybrid_dipole)),
                "dipole_change_norm_debye": float(np.linalg.norm(dipole_delta)),
                "charge_status": "neutral_hybrid_diagnostic_full_chain",
                "production_md_permission": "blocked_unvalidated_unmapped_charges",
            }
        )
        xyz_records[formulation_id] = {
            "path": str(xyz_path),
            "bytes": xyz_path.stat().st_size,
            "sha256": sha256(xyz_path),
        }
    atoms = pd.concat(atom_frames, ignore_index=True).sort_values(
        ["formulation_id", "atom_index_zero_based"], kind="stable"
    )
    summary = pd.DataFrame(summary_rows).sort_values(
        "formulation_id", kind="stable"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    atom_out = output_root / "混合诊断电荷逐原子.csv.gz"
    summary_out = output_root / "混合诊断电荷逐配方.csv"
    report_out = output_root / "混合诊断电荷说明.md"
    _atomic_gzip_text(atom_out, atoms.to_csv(index=False, float_format="%.12g"))
    _atomic_text(summary_out, summary.to_csv(index=False, float_format="%.12g"))
    _atomic_text(
        report_out,
        "\n".join(
            [
                "# RESP核心约束下的完整链电荷缺口诊断",
                "",
                "联合RESP核心电荷保持完全固定；其余未映射原子从整链Gasteiger出发，在总电荷为0约束下施加相同的最小L2均匀修正。最后一个未映射原子只吸收浮点残差。",
                "该构造是诊断工具：它量化核心替换后完成整链中性化需要多大修正，并比较相同ETKDG/MMFF三维种子上的点电荷偶极变化。",
                "",
                "均匀修正没有量化或实验依据，也未施加化学等价原子、片段边界或重复单元约束。输出不得写入生产LAMMPS数据；下一步需用多片段联合RESP/等价约束替代该诊断补全。",
                "",
            ]
        ),
    )
    files = [atom_out, summary_out, report_out]
    manifest = {
        "release_id": release_id,
        "status": "neutral_full_chain_charge_diagnostic_completed_not_production",
        "counts": {
            "formulations": len(summary),
            "atomic_charge_rows": len(atoms),
            "fixed_resp_core_atoms": int(summary["fixed_resp_core_atom_count"].sum()),
            "unmapped_atoms": int(summary["unmapped_atom_count"].sum()),
        },
        "maximum_absolute_hybrid_total_charge_e": float(
            summary["hybrid_total_charge_e"].abs().max()
        ),
        "maximum_absolute_uniform_unmapped_correction_e": float(
            summary["uniform_unmapped_correction_e"].abs().max()
        ),
        "maximum_dipole_change_norm_debye": float(
            summary["dipole_change_norm_debye"].max()
        ),
        "inputs": {
            path.name: {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in (graph_path, core_mapping_path, seed_table_path)
        },
        "aggregate_xyz_manifest_sha256": hashlib.sha256(
            "".join(xyz_records[key]["sha256"] for key in sorted(xyz_records)).encode(
                "ascii"
            )
        ).hexdigest(),
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "production_md_permission": "blocked_unvalidated_unmapped_charges",
        "performance_claim_status": "no_performance_claim",
    }
    _atomic_text(
        output_root / "混合诊断电荷发布清单.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--化学图",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "低聚链化学图.csv.gz",
    )
    parser.add_argument(
        "--核心映射",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "RESP核心转移" / "RESP核心转移映射.csv.gz",
    )
    parser.add_argument(
        "--三维种子表",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "三维种子清单.csv",
    )
    parser.add_argument(
        "--三维种子根目录", type=Path, default=ROOT / "计算" / "现实MD"
    )
    parser.add_argument(
        "--输出目录",
        type=Path,
        default=ROOT / "计算" / "现实MD" / "混合电荷诊断",
    )
    parser.add_argument(
        "--发布ID", default="tpu-reality-md-hybrid-charge-diagnostic-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.化学图,
        args.核心映射,
        args.三维种子表,
        args.三维种子根目录,
        args.输出目录,
        release_id=args.发布ID,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
