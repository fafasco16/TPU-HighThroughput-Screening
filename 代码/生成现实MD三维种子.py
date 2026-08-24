"""从现实TPU低聚链二维图生成确定性单链3D种子，不宣称MD就绪。"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
from rdkit import Chem
from rdkit.Chem import AllChem


ROOT = Path(__file__).resolve().parents[1]
BASE_SEED = 20260825


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _required(frame: pd.DataFrame, columns: set[str]) -> None:
    missing = sorted(columns.difference(frame.columns))
    if missing:
        raise ValueError(f"低聚链化学图缺少字段: {missing}")


def _atomic_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding=encoding, newline="\n")
    temporary.replace(path)


def _seed_for(formulation_id: str) -> int:
    return BASE_SEED + int(_sha256_text(formulation_id)[:8], 16) % 1_000_000


def generate_seed_table(
    graphs: pd.DataFrame,
    output_root: Path,
    *,
    max_embedding_iterations: int = 1000,
    max_mmff_iterations: int = 1000,
) -> pd.DataFrame:
    _required(
        graphs,
        {
            "formulation_id",
            "canonical_smiles",
            "atom_count",
            "chemical_graph_status",
            "performance_claim_status",
        },
    )
    if graphs.empty or not graphs["formulation_id"].is_unique:
        raise ValueError("低聚链化学图formulation_id必须非空唯一")
    if not graphs["chemical_graph_status"].astype(str).eq("completed").all():
        raise ValueError("只有completed化学图状态可生成3D种子")
    if not graphs["performance_claim_status"].astype(str).eq(
        "no_performance_claim"
    ).all():
        raise ValueError("3D种子输入不得包含性能宣称")
    if max_embedding_iterations < 1 or max_mmff_iterations < 1:
        raise ValueError("嵌入和MMFF最大步数必须为正")
    rows: list[dict[str, Any]] = []
    for source in graphs.sort_values("formulation_id", kind="stable").to_dict(
        orient="records"
    ):
        formulation_id = str(source["formulation_id"])
        canonical_smiles = str(source["canonical_smiles"])
        molecule = Chem.MolFromSmiles(canonical_smiles)
        if molecule is None:
            raise ValueError(f"{formulation_id}规范SMILES无法解析")
        molecule = Chem.AddHs(molecule)
        atom_count = molecule.GetNumAtoms()
        if atom_count != int(source["atom_count"]):
            raise ValueError(f"{formulation_id}化学图原子数与输入不一致")
        seed = _seed_for(formulation_id)
        params = AllChem.ETKDGv3()
        params.randomSeed = seed
        params.useRandomCoords = True
        params.maxIterations = int(max_embedding_iterations)
        started = time.monotonic()
        conformer_id = int(AllChem.EmbedMolecule(molecule, params))
        embedding_seconds = round(time.monotonic() - started, 3)
        relative = Path("三维种子") / f"{formulation_id}.xyz"
        if conformer_id < 0:
            rows.append(
                {
                    "formulation_id": formulation_id,
                    "canonical_smiles_sha256": _sha256_text(canonical_smiles),
                    "atom_count": atom_count,
                    "embedding_seed": seed,
                    "embedding_method": "ETKDGv3_random_coordinates",
                    "embedding_seconds": embedding_seconds,
                    "mmff_seconds": 0.0,
                    "mmff_status_code": pd.NA,
                    "mmff_energy": pd.NA,
                    "geometry_status": "blocked_etkdg_embedding_failed",
                    "xyz_file": "",
                    "xyz_sha256": "",
                    "xyz_bytes": 0,
                    "md_execution_status": "blocked_no_3d_seed",
                    "performance_claim_status": "no_performance_claim",
                }
            )
            continue
        mmff_started = time.monotonic()
        if AllChem.MMFFHasAllMoleculeParams(molecule):
            status_code = int(
                AllChem.MMFFOptimizeMolecule(
                    molecule,
                    confId=conformer_id,
                    maxIters=int(max_mmff_iterations),
                    mmffVariant="MMFF94s",
                )
            )
            properties = AllChem.MMFFGetMoleculeProperties(
                molecule, mmffVariant="MMFF94s"
            )
            force_field = AllChem.MMFFGetMoleculeForceField(
                molecule, properties, confId=conformer_id
            )
            energy = float(force_field.CalcEnergy())
            geometry_status = (
                "mmff_converged_seed"
                if status_code == 0
                else "mmff_max_iterations_seed"
                if status_code == 1
                else "mmff_optimization_failed_seed_retained"
            )
        else:
            status_code = -1
            energy = pd.NA
            geometry_status = "embedded_mmff_parameters_missing"
        mmff_seconds = round(time.monotonic() - mmff_started, 3)
        xyz_path = output_root / relative
        _atomic_text(
            xyz_path,
            Chem.MolToXYZBlock(molecule, confId=conformer_id),
            encoding="ascii",
        )
        element_order = "\0".join(
            atom.GetSymbol() for atom in molecule.GetAtoms()
        )
        rows.append(
            {
                "formulation_id": formulation_id,
                "canonical_smiles_sha256": _sha256_text(canonical_smiles),
                "atom_count": atom_count,
                "atom_order_sha256": _sha256_text(element_order),
                "embedding_seed": seed,
                "embedding_method": "ETKDGv3_random_coordinates",
                "embedding_seconds": embedding_seconds,
                "mmff_variant": "MMFF94s",
                "mmff_max_iterations": int(max_mmff_iterations),
                "mmff_seconds": mmff_seconds,
                "mmff_status_code": status_code,
                "mmff_energy": energy,
                "geometry_status": geometry_status,
                "xyz_file": relative.as_posix(),
                "xyz_sha256": sha256(xyz_path),
                "xyz_bytes": xyz_path.stat().st_size,
                "seed_scope": "initial_single_chain_geometry_only",
                "md_execution_status": (
                    "blocked_seed_only_forcefield_and_bulk_protocol_missing"
                ),
                "performance_claim_status": "no_performance_claim",
            }
        )
    return pd.DataFrame(rows).sort_values("formulation_id").reset_index(drop=True)


def write_release(
    graph_path: Path,
    output_root: Path,
    *,
    release_id: str,
    max_embedding_iterations: int = 1000,
    max_mmff_iterations: int = 1000,
) -> dict[str, Any]:
    if not graph_path.is_file():
        raise ValueError(f"低聚链化学图不存在: {graph_path}")
    table = generate_seed_table(
        pd.read_csv(graph_path),
        output_root,
        max_embedding_iterations=max_embedding_iterations,
        max_mmff_iterations=max_mmff_iterations,
    )
    table_path = output_root / "三维种子清单.csv"
    _atomic_text(table_path, table.to_csv(index=False, float_format="%.12g"))
    successful = table["xyz_file"].astype(str).str.len().gt(0)
    manifest = {
        "release_id": release_id,
        "status": (
            "seed_geometry_completed_md_execution_blocked"
            if successful.all()
            else "incomplete_seed_geometry"
        ),
        "counts": {
            "graphs": len(table),
            "embedded": int(successful.sum()),
            "mmff_converged": int(
                table["geometry_status"].eq("mmff_converged_seed").sum()
            ),
            "mmff_not_converged_or_unavailable": int(
                (~table["geometry_status"].eq("mmff_converged_seed") & successful).sum()
            ),
        },
        "protocol": {
            "embedding": "ETKDGv3_random_coordinates",
            "base_seed": BASE_SEED,
            "max_embedding_iterations": max_embedding_iterations,
            "mmff_variant": "MMFF94s",
            "max_mmff_iterations": max_mmff_iterations,
            "interpretation_limit": (
                "single-chain 3D seed only; not a forcefield-validated, packed, "
                "equilibrated, or production-MD structure"
            ),
        },
        "input": {
            "path": str(graph_path),
            "bytes": graph_path.stat().st_size,
            "sha256": sha256(graph_path),
        },
        "table": {
            "path": table_path.name,
            "bytes": table_path.stat().st_size,
            "sha256": sha256(table_path),
        },
        "aggregate_xyz_sha256": hashlib.sha256(
            "".join(table.loc[successful, "xyz_sha256"]).encode("ascii")
        ).hexdigest(),
        "md_execution_status": "blocked_forcefield_and_bulk_protocol_missing",
    }
    _atomic_text(
        output_root / "三维种子发布清单.json",
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
        "--输出目录", type=Path, default=ROOT / "计算" / "现实MD"
    )
    parser.add_argument("--嵌入最大步数", type=int, default=1000)
    parser.add_argument("--MMFF最大步数", type=int, default=1000)
    parser.add_argument(
        "--发布ID", default="tpu-reality-md-3d-seeds-20260825-v1"
    )
    args = parser.parse_args(argv)
    manifest = write_release(
        args.化学图,
        args.输出目录,
        release_id=args.发布ID,
        max_embedding_iterations=args.嵌入最大步数,
        max_mmff_iterations=args.MMFF最大步数,
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))
    return 0 if manifest["status"] != "incomplete_seed_geometry" else 1


if __name__ == "__main__":
    raise SystemExit(main())
