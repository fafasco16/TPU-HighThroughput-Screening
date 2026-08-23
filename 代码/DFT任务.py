"""从 TPU DFT 配方队列构建唯一构件任务与确定性三维初始结构。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
from rdkit import Chem
from rdkit.Chem import AllChem


ROLE_COLUMNS = {
    "diisocyanate": ("diisocyanate_id", "diisocyanate_smiles"),
    "macrodiol_proxy": ("macrodiol_proxy_id", "macrodiol_proxy_smiles"),
    "chain_extender": ("chain_extender_id", "chain_extender_smiles"),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_component_tasks(queue: pd.DataFrame, seed: int = 20260823) -> pd.DataFrame:
    """把配方队列按构件 ID 去重，并保留全部配方反向连接。"""

    required = {"formulation_id", "combination_id"}
    for id_column, smiles_column in ROLE_COLUMNS.values():
        required.update((id_column, smiles_column))
    missing = required.difference(queue.columns)
    if missing:
        raise ValueError(f"DFT 队列缺少字段: {sorted(missing)}")
    if not queue["formulation_id"].is_unique:
        raise ValueError("DFT 队列 formulation_id 不唯一")

    rows: list[dict[str, object]] = []
    for role, (id_column, smiles_column) in ROLE_COLUMNS.items():
        for candidate_id, group in queue.groupby(id_column, sort=True):
            smiles_values = sorted(set(group[smiles_column].astype(str)))
            if len(smiles_values) != 1:
                raise ValueError(f"构件 {candidate_id} 对应多个 SMILES")
            formulation_ids = sorted(set(group["formulation_id"].astype(str)))
            combination_ids = sorted(set(group["combination_id"].astype(str)))
            rows.append(
                {
                    "candidate_id": str(candidate_id),
                    "component_role": role,
                    "canonical_smiles": smiles_values[0],
                    "charge": 0,
                    "uhf": 0,
                    "formulation_count": len(formulation_ids),
                    "formulation_ids": ";".join(formulation_ids),
                    "combination_count": len(combination_ids),
                    "combination_ids": ";".join(combination_ids),
                    "geometry_seed": int(seed),
                }
            )
    tasks = pd.DataFrame(rows).sort_values(
        ["component_role", "candidate_id"], kind="stable"
    ).reset_index(drop=True)
    tasks.insert(0, "task_index", np.arange(len(tasks), dtype=int))
    tasks["task_slug"] = tasks.apply(
        lambda row: f"{int(row['task_index']):04d}_{row['candidate_id']}", axis=1
    )
    tasks["initial_xyz_file"] = tasks["task_slug"].map(
        lambda slug: f"初始结构/{slug}.xyz"
    )
    return tasks


def generate_initial_xyz(
    smiles: str,
    seed: int = 20260823,
    conformer_count: int = 10,
) -> dict[str, object]:
    """用 ETKDGv3 加力场优化生成最低能的含氢三维构象。"""

    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError(f"无法解析 SMILES: {smiles}")
    molecule = Chem.AddHs(molecule)
    params = AllChem.ETKDGv3()
    params.randomSeed = int(seed)
    params.useRandomCoords = False
    conformer_ids = list(
        AllChem.EmbedMultipleConfs(
            molecule, numConfs=int(conformer_count), params=params
        )
    )
    if not conformer_ids:
        params.useRandomCoords = True
        conformer_ids = list(
            AllChem.EmbedMultipleConfs(
                molecule, numConfs=1, params=params
            )
        )
    if not conformer_ids:
        raise ValueError("ETKDG 无法生成三维构象")

    if AllChem.MMFFHasAllMoleculeParams(molecule):
        optimization = AllChem.MMFFOptimizeMoleculeConfs(
            molecule, numThreads=1, maxIters=1000, mmffVariant="MMFF94s"
        )
        force_field = "MMFF94s"
    elif AllChem.UFFHasAllMoleculeParams(molecule):
        optimization = AllChem.UFFOptimizeMoleculeConfs(
            molecule, numThreads=1, maxIters=1000
        )
        force_field = "UFF"
    else:
        raise ValueError("MMFF94s 与 UFF 均缺少该构件参数")
    energies = np.asarray([float(result[1]) for result in optimization])
    best_position = int(np.argmin(energies))
    best_conformer = int(conformer_ids[best_position])
    return {
        "xyz": Chem.MolToXYZBlock(molecule, confId=best_conformer),
        "atom_count": molecule.GetNumAtoms(),
        "embedded_conformer_count": len(conformer_ids),
        "initial_force_field": force_field,
        "initial_force_field_energy": float(energies[best_position]),
        "initial_force_field_converged": int(optimization[best_position][0]) == 0,
    }


def materialize_initial_structures(
    tasks: pd.DataFrame,
    output_root: Path,
) -> pd.DataFrame:
    """生成全部初始 XYZ，并返回带哈希和几何元数据的任务清单。"""

    structure_dir = output_root / "初始结构"
    structure_dir.mkdir(parents=True, exist_ok=True)
    output = tasks.copy()
    metadata: list[dict[str, object]] = []
    expected_names = set(output["initial_xyz_file"].map(lambda value: Path(value).name))
    unexpected = sorted(
        path.name for path in structure_dir.glob("*.xyz") if path.name not in expected_names
    )
    if unexpected:
        raise ValueError(f"初始结构目录含非本发布文件: {unexpected[:3]}")
    for row in output.itertuples(index=False):
        path = output_root / row.initial_xyz_file
        try:
            geometry = generate_initial_xyz(
                row.canonical_smiles, seed=int(row.geometry_seed)
            )
        except ValueError as exc:
            molecule = Chem.AddHs(Chem.MolFromSmiles(row.canonical_smiles))
            geometry = {
                "geometry_status": "blocked_rdkit_3d_embedding",
                "geometry_error": str(exc),
                "atom_count": molecule.GetNumAtoms(),
                "embedded_conformer_count": 0,
                "initial_force_field": "",
                "initial_force_field_energy": np.nan,
                "initial_force_field_converged": False,
                "initial_xyz_sha256": "",
                "initial_xyz_bytes": 0,
            }
        else:
            path.write_text(str(geometry.pop("xyz")), encoding="ascii")
            geometry["geometry_status"] = "ready"
            geometry["geometry_error"] = ""
            geometry["initial_xyz_sha256"] = sha256(path)
            geometry["initial_xyz_bytes"] = path.stat().st_size
        metadata.append(geometry)
    return pd.concat([output.reset_index(drop=True), pd.DataFrame(metadata)], axis=1)
