"""为现实高层DFT子集生成NCO–OH预反应复合物多起点xTB任务。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
from rdkit import Chem

from 反应位点描述符 import identify_reactive_sites


ROOT = Path(__file__).resolve().parents[1]
PAIR_TYPES = {
    "diisocyanate_chain_extender": "chain_extender_id",
    "diisocyanate_macrodiol": "macrodiol_id",
}
TORSION_STARTS_DEG = (0.0, 90.0, 180.0, 270.0)
TORSION_COLLISION_ADJUSTMENTS_DEG = (0.0, 30.0, -30.0, 60.0, -60.0, 120.0, -120.0, 180.0)
ATTACK_AZIMUTH_ADJUSTMENTS_DEG = (0.0, 45.0, -45.0, 90.0, -90.0, 135.0, -135.0, 180.0)
XTB_BINARY_SHA256 = "debf27a9e0fa4bfb5ca75aafe4b90d8211f08ec2f4a482f375a4987212eaa12a"


@dataclass(frozen=True)
class MonomerGeometry:
    candidate_id: str
    component_role: str
    smiles: str
    molecule: Chem.Mol
    elements: tuple[str, ...]
    coordinates: np.ndarray
    xyz_sha256: str
    energy_hartree: float
    reactive_sites: tuple[int, int]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _required(frame: pd.DataFrame, columns: set[str], label: str) -> None:
    missing = sorted(columns.difference(frame.columns))
    if missing:
        raise ValueError(f"{label}缺少字段: {missing}")


def validate_protocol(*, distance_a: float, attack_angle_deg: float) -> None:
    if not 2.2 <= float(distance_a) <= 3.5:
        raise ValueError("预反应NCO碳–OH氧距离必须在2.2–3.5 Å")
    if not 80.0 <= float(attack_angle_deg) <= 130.0:
        raise ValueError("预反应进攻角度必须在80–130度")


def build_unique_pairs(subset: pd.DataFrame) -> pd.DataFrame:
    _required(
        subset,
        {
            "formulation_id",
            "base_system_id",
            "diisocyanate_id",
            "macrodiol_id",
            "chain_extender_id",
        },
        "高层DFT子集",
    )
    if subset.empty or not subset["formulation_id"].is_unique:
        raise ValueError("高层DFT子集formulation_id必须非空唯一")
    rows: list[dict[str, Any]] = []
    for pair_type, oh_column in PAIR_TYPES.items():
        grouped = subset.groupby(
            ["diisocyanate_id", oh_column], sort=True, dropna=False
        )
        for (diisocyanate_id, oh_component_id), group in grouped:
            if pd.isna(diisocyanate_id) or pd.isna(oh_component_id):
                raise ValueError("高层DFT子集存在空构件ID")
            payload = (
                f"tpu-prereaction-pair-v1\0{pair_type}\0"
                f"{diisocyanate_id}\0{oh_component_id}"
            )
            pair_id = f"pair_{_sha256_text(payload)[:20]}"
            formulation_ids = sorted(group["formulation_id"].astype(str).unique())
            base_system_ids = sorted(group["base_system_id"].astype(str).unique())
            rows.append(
                {
                    "pair_id": pair_id,
                    "pair_type": pair_type,
                    "diisocyanate_id": str(diisocyanate_id),
                    "oh_component_id": str(oh_component_id),
                    "oh_component_role": (
                        "chain_extender"
                        if pair_type == "diisocyanate_chain_extender"
                        else "macrodiol_proxy"
                    ),
                    "formulation_count": len(formulation_ids),
                    "formulation_ids": ";".join(formulation_ids),
                    "base_system_count": len(base_system_ids),
                    "base_system_ids": ";".join(base_system_ids),
                }
            )
    result = pd.DataFrame(rows).sort_values(
        ["pair_type", "diisocyanate_id", "oh_component_id"], kind="stable"
    )
    if result.empty or not result["pair_id"].is_unique:
        raise ValueError("预反应配对身份为空或不唯一")
    result.insert(0, "pair_index", np.arange(len(result), dtype=int))
    return result.reset_index(drop=True)


def _read_xyz(path: Path) -> tuple[tuple[str, ...], np.ndarray]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 3:
        raise ValueError(f"单体XYZ不完整: {path}")
    try:
        atom_count = int(lines[0].strip())
    except ValueError as exc:
        raise ValueError(f"单体XYZ原子数无效: {path}") from exc
    if atom_count <= 0 or len(lines) != atom_count + 2:
        raise ValueError(f"单体XYZ不是严格单帧: {path}")
    elements: list[str] = []
    coordinates: list[list[float]] = []
    for line in lines[2:]:
        fields = line.split()
        if len(fields) < 4:
            raise ValueError(f"单体XYZ原子行无效: {path}")
        elements.append(fields[0])
        try:
            xyz = [float(value) for value in fields[1:4]]
        except ValueError as exc:
            raise ValueError(f"单体XYZ坐标无效: {path}") from exc
        if not all(math.isfinite(value) for value in xyz):
            raise ValueError(f"单体XYZ含非有限坐标: {path}")
        coordinates.append(xyz)
    return tuple(elements), np.asarray(coordinates, dtype=float)


def _component_smiles(
    components: pd.DataFrame, ptmg_models: pd.DataFrame
) -> dict[str, tuple[str, str]]:
    _required(components, {"component_id", "role", "canonical_smiles"}, "现实构件")
    _required(
        ptmg_models,
        {"component_id", "representative_smiles"},
        "PTMG代表模型",
    )
    if not components["component_id"].is_unique or not ptmg_models[
        "component_id"
    ].is_unique:
        raise ValueError("现实构件或PTMG模型ID不唯一")
    model_map = ptmg_models.set_index("component_id")["representative_smiles"].to_dict()
    output: dict[str, tuple[str, str]] = {}
    for row in components.itertuples(index=False):
        role = str(row.role)
        smiles = (
            str(model_map.get(row.component_id, ""))
            if role == "macrodiol"
            else str(row.canonical_smiles)
        )
        computational_role = "macrodiol_proxy" if role == "macrodiol" else role
        if not smiles.strip():
            raise ValueError(f"现实构件缺少预反应结构: {row.component_id}")
        output[str(row.component_id)] = (smiles, computational_role)
    return output


def _energy_map(*frames: pd.DataFrame) -> dict[str, float]:
    rows = pd.concat(frames, ignore_index=True)
    _required(
        rows,
        {"xtb_task_slug", "run_status", "total_energy_hartree"},
        "单体xTB逐构象结果",
    )
    successful = rows.loc[rows["run_status"].astype(str).eq("success")].copy()
    successful["total_energy_hartree"] = pd.to_numeric(
        successful["total_energy_hartree"], errors="coerce"
    )
    if successful["xtb_task_slug"].duplicated().any():
        raise ValueError("单体xTB结果xtb_task_slug不唯一")
    return successful.set_index("xtb_task_slug")["total_energy_hartree"].to_dict()


def load_monomer_geometries(
    manifest: pd.DataFrame,
    monomer_root: Path,
    *,
    smiles_map: dict[str, tuple[str, str]] | None = None,
    energies: dict[str, float] | None = None,
) -> dict[str, MonomerGeometry]:
    _required(
        manifest,
        {
            "candidate_id",
            "component_role",
            "xtb_task_slug",
            "published_xyz_file",
            "published_xyz_sha256",
        },
        "预反应单体清单",
    )
    if not manifest["candidate_id"].is_unique:
        raise ValueError("预反应单体candidate_id不唯一")
    if smiles_map is None:
        smiles_map = {
            str(row.candidate_id): ("[H]", str(row.component_role))
            for row in manifest.itertuples(index=False)
        }
    energies = {} if energies is None else energies
    output: dict[str, MonomerGeometry] = {}
    for row in manifest.itertuples(index=False):
        candidate_id = str(row.candidate_id)
        path = (monomer_root / str(row.published_xyz_file)).resolve()
        if not path.is_file():
            raise ValueError(f"{candidate_id}单体XYZ不存在")
        actual_hash = sha256(path)
        if actual_hash != str(row.published_xyz_sha256):
            raise ValueError(f"{candidate_id}单体XYZ SHA-256不一致")
        if candidate_id not in smiles_map:
            raise ValueError(f"{candidate_id}缺少结构模型")
        smiles, role = smiles_map[candidate_id]
        molecule = Chem.MolFromSmiles(smiles)
        if molecule is None:
            raise ValueError(f"{candidate_id}结构模型无法解析")
        molecule = Chem.AddHs(molecule)
        expected_elements = tuple(atom.GetSymbol() for atom in molecule.GetAtoms())
        elements, coordinates = _read_xyz(path)
        if elements != expected_elements:
            raise ValueError(f"{candidate_id}XYZ原子顺序与结构模型不一致")
        task_slug = str(row.xtb_task_slug)
        if energies and task_slug not in energies:
            raise ValueError(f"{candidate_id}缺少单体xTB能量")
        energy = float(energies.get(task_slug, 0.0))
        sites = identify_reactive_sites(smiles, role)
        output[candidate_id] = MonomerGeometry(
            candidate_id=candidate_id,
            component_role=role,
            smiles=smiles,
            molecule=molecule,
            elements=elements,
            coordinates=coordinates,
            xyz_sha256=actual_hash,
            energy_hartree=energy,
            reactive_sites=sites[1],
        )
    return output


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm < 1e-10:
        raise ValueError("无法定义预反应几何方向")
    return vector / norm


def _perpendicular(axis: np.ndarray, preferred: np.ndarray | None = None) -> np.ndarray:
    if preferred is not None:
        projected = preferred - np.dot(preferred, axis) * axis
        if np.linalg.norm(projected) >= 1e-8:
            return _normalize(projected)
    basis = np.eye(3)[int(np.argmin(np.abs(axis)))]
    return _normalize(basis - np.dot(basis, axis) * axis)


def _rotation_axis_angle(axis: np.ndarray, angle_rad: float) -> np.ndarray:
    x, y, z = _normalize(axis)
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)
    one = 1.0 - c
    return np.array(
        [
            [c + x * x * one, x * y * one - z * s, x * z * one + y * s],
            [y * x * one + z * s, c + y * y * one, y * z * one - x * s],
            [z * x * one - y * s, z * y * one + x * s, c + z * z * one],
        ],
        dtype=float,
    )


def _rotation_from_to(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    source = _normalize(source)
    target = _normalize(target)
    cross = np.cross(source, target)
    sine = float(np.linalg.norm(cross))
    cosine = float(np.clip(np.dot(source, target), -1.0, 1.0))
    if sine < 1e-10:
        if cosine > 0:
            return np.eye(3)
        return _rotation_axis_angle(_perpendicular(source), math.pi)
    return _rotation_axis_angle(cross / sine, math.atan2(sine, cosine))


def _neighbor_index(
    molecule: Chem.Mol, atom_index: int, *, element: str | None = None, exclude: int | None = None
) -> int:
    candidates = [
        atom.GetIdx()
        for atom in molecule.GetAtomWithIdx(atom_index).GetNeighbors()
        if atom.GetIdx() != exclude and (element is None or atom.GetSymbol() == element)
    ]
    if not candidates:
        raise ValueError("反应位点缺少所需相邻原子")
    return min(candidates)


def _initial_complex(
    diisocyanate: MonomerGeometry,
    alcohol: MonomerGeometry,
    *,
    nco_site: int,
    oh_site: int,
    distance_a: float,
    attack_angle_deg: float,
    attack_azimuth_deg: float,
    torsion_deg: float,
    side: float,
) -> tuple[tuple[str, ...], np.ndarray, int, int, float, float]:
    c_index = diisocyanate.reactive_sites[nco_site]
    o_index = alcohol.reactive_sites[oh_site]
    nco_oxygen = _neighbor_index(
        diisocyanate.molecule, c_index, element="O"
    )
    nco_nitrogen = _neighbor_index(
        diisocyanate.molecule, c_index, element="N"
    )
    substituent = _neighbor_index(
        diisocyanate.molecule, nco_nitrogen, exclude=c_index
    )
    axis = _normalize(
        diisocyanate.coordinates[nco_oxygen]
        - diisocyanate.coordinates[c_index]
    )
    preferred = (
        diisocyanate.coordinates[substituent]
        - diisocyanate.coordinates[nco_nitrogen]
    )
    perpendicular = _perpendicular(axis, preferred) * float(side)
    azimuth_rotation = _rotation_axis_angle(
        axis, math.radians(float(attack_azimuth_deg))
    )
    perpendicular = perpendicular @ azimuth_rotation.T
    theta = math.radians(float(attack_angle_deg))
    approach = _normalize(math.cos(theta) * axis + math.sin(theta) * perpendicular)

    alcohol_carbon = _neighbor_index(alcohol.molecule, o_index, element="C")
    oh_to_carbon = _normalize(
        alcohol.coordinates[alcohol_carbon] - alcohol.coordinates[o_index]
    )
    align = _rotation_from_to(oh_to_carbon, approach)
    centered = alcohol.coordinates - alcohol.coordinates[o_index]
    rotated = centered @ align.T
    torsion = _rotation_axis_angle(approach, math.radians(float(torsion_deg)))
    rotated = rotated @ torsion.T
    target_oxygen = diisocyanate.coordinates[c_index] + float(distance_a) * approach
    placed_alcohol = rotated + target_oxygen
    combined = np.vstack([diisocyanate.coordinates, placed_alcohol])
    elements = (*diisocyanate.elements, *alcohol.elements)
    distances = np.linalg.norm(
        diisocyanate.coordinates[:, None, :] - placed_alcohol[None, :, :], axis=2
    )
    minimum = float(np.min(distances))
    actual_distance = float(
        np.linalg.norm(placed_alcohol[o_index] - diisocyanate.coordinates[c_index])
    )
    return (
        elements,
        combined,
        c_index + 1,
        len(diisocyanate.elements) + o_index + 1,
        minimum,
        actual_distance,
    )


def _xyz_text(elements: tuple[str, ...], coordinates: np.ndarray, comment: str) -> str:
    lines = [str(len(elements)), comment]
    lines.extend(
        f"{element:<2s} {xyz[0]: .12f} {xyz[1]: .12f} {xyz[2]: .12f}"
        for element, xyz in zip(elements, coordinates)
    )
    return "\n".join(lines) + "\n"


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _materialize_tasks(
    pairs: pd.DataFrame,
    geometries: dict[str, MonomerGeometry],
    output_root: Path,
    *,
    distance_a: float,
    attack_angle_deg: float,
) -> pd.DataFrame:
    validate_protocol(distance_a=distance_a, attack_angle_deg=attack_angle_deg)
    rows: list[dict[str, Any]] = []
    for pair in pairs.itertuples(index=False):
        if pair.diisocyanate_id not in geometries or pair.oh_component_id not in geometries:
            raise ValueError(f"{pair.pair_id}缺少单体几何")
        diisocyanate = geometries[pair.diisocyanate_id]
        alcohol = geometries[pair.oh_component_id]
        if diisocyanate.component_role != "diisocyanate":
            raise ValueError(f"{pair.diisocyanate_id}不是二异氰酸酯")
        if alcohol.component_role != pair.oh_component_role:
            raise ValueError(f"{pair.oh_component_id}OH角色不一致")
        for start_index, torsion_deg in enumerate(TORSION_STARTS_DEG):
            nco_site = start_index // 2
            oh_site = start_index % 2
            side = 1.0 if start_index in {0, 3} else -1.0
            selected_geometry = None
            best_geometry = None
            best_minimum = -math.inf
            selected_adjustment = 0.0
            selected_azimuth = 0.0
            for azimuth in ATTACK_AZIMUTH_ADJUSTMENTS_DEG:
                for adjustment in TORSION_COLLISION_ADJUSTMENTS_DEG:
                    geometry = _initial_complex(
                        diisocyanate,
                        alcohol,
                        nco_site=nco_site,
                        oh_site=oh_site,
                        distance_a=distance_a,
                        attack_angle_deg=attack_angle_deg,
                        attack_azimuth_deg=azimuth,
                        torsion_deg=torsion_deg + adjustment,
                        side=side,
                    )
                    if geometry[4] > best_minimum:
                        best_geometry = geometry
                        best_minimum = geometry[4]
                    if geometry[4] > 0.7:
                        selected_geometry = geometry
                        selected_adjustment = adjustment
                        selected_azimuth = azimuth
                        break
                if selected_geometry is not None:
                    break
            if selected_geometry is None:
                assert best_geometry is not None
                selected_geometry = best_geometry
            (
                elements,
                coordinates,
                nco_index_1based,
                oh_index_1based,
                minimum_distance,
                actual_distance,
            ) = selected_geometry
            geometry_status = (
                "ready"
                if minimum_distance > 0.7
                else "blocked_initial_interfragment_collision"
            )
            slug = f"{pair.pair_id}_s{start_index + 1:02d}"
            relative_xyz = Path("输入复合物") / f"{slug}.xyz"
            relative_control = Path("约束") / f"{slug}.inp"
            comment = (
                f"pair_id={pair.pair_id} start={start_index + 1} "
                f"nco_site={nco_site + 1} oh_site={oh_site + 1} "
                f"attack_angle_deg={attack_angle_deg:.6f}"
            )
            xyz_text = _xyz_text(elements, coordinates, comment)
            control_text = (
                "$constrain\n"
                " force constant=0.5\n"
                f" distance: {nco_index_1based},{oh_index_1based},{distance_a:.6f}\n"
                "$end\n"
            )
            xyz_path = output_root / relative_xyz
            control_path = output_root / relative_control
            _atomic_text(xyz_path, xyz_text)
            _atomic_text(control_path, control_text)
            rows.append(
                {
                    "task_index": len(rows),
                    "task_slug": slug,
                    "pair_index": int(pair.pair_index),
                    "pair_id": pair.pair_id,
                    "pair_type": pair.pair_type,
                    "diisocyanate_id": pair.diisocyanate_id,
                    "oh_component_id": pair.oh_component_id,
                    "oh_component_role": pair.oh_component_role,
                    "start_index": start_index + 1,
                    "nco_site_number": nco_site + 1,
                    "oh_site_number": oh_site + 1,
                    "torsion_start_deg": torsion_deg,
                    "torsion_collision_adjustment_deg": selected_adjustment,
                    "torsion_actual_deg": (torsion_deg + selected_adjustment) % 360.0,
                    "attack_side": int(side),
                    "attack_angle_deg": float(attack_angle_deg),
                    "attack_azimuth_adjustment_deg": selected_azimuth,
                    "initial_reactive_distance_a": actual_distance,
                    "initial_min_interfragment_distance_a": minimum_distance,
                    "geometry_status": geometry_status,
                    "execution_permission": (
                        "allowed" if geometry_status == "ready" else "blocked"
                    ),
                    "nco_carbon_atom_index_1based": nco_index_1based,
                    "oh_oxygen_atom_index_1based": oh_index_1based,
                    "atom_count": len(elements),
                    "charge": 0,
                    "uhf": 0,
                    "monomer_energy_sum_hartree": (
                        diisocyanate.energy_hartree + alcohol.energy_hartree
                    ),
                    "diisocyanate_xyz_sha256": diisocyanate.xyz_sha256,
                    "oh_component_xyz_sha256": alcohol.xyz_sha256,
                    "input_xyz_file": relative_xyz.as_posix(),
                    "input_xyz_sha256": sha256(xyz_path),
                    "xcontrol_file": relative_control.as_posix(),
                    "xcontrol_sha256": sha256(control_path),
                    "constraint_force_constant": 0.5,
                    "xtb_version": "6.7.1",
                    "xtb_binary_sha256": XTB_BINARY_SHA256,
                    "method": "GFN2-xTB",
                    "environment_model": "gas_phase",
                    "optimization_level": "tight",
                    "calculation_scope": "constrained_prereaction_association_proxy",
                    "performance_claim_status": "no_performance_claim",
                }
            )
    result = pd.DataFrame(rows)
    if not result["task_slug"].is_unique:
        raise ValueError("预反应复合物task_slug不唯一")
    return result


def build_release(
    *,
    subset_path: Path,
    monomer_manifest_path: Path,
    monomer_root: Path,
    components_path: Path,
    ptmg_models_path: Path,
    discrete_results_path: Path,
    ptmg_results_path: Path,
    output_root: Path,
    release_id: str,
    distance_a: float = 2.7,
    attack_angle_deg: float = 105.0,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    inputs = [
        subset_path,
        monomer_manifest_path,
        components_path,
        ptmg_models_path,
        discrete_results_path,
        ptmg_results_path,
    ]
    missing = [str(path) for path in inputs if not path.is_file()]
    if missing:
        raise ValueError(f"预反应任务输入不存在: {missing}")
    pairs = build_unique_pairs(pd.read_csv(subset_path))
    monomer_manifest = pd.read_csv(monomer_manifest_path)
    smiles_map = _component_smiles(
        pd.read_csv(components_path), pd.read_csv(ptmg_models_path)
    )
    energies = _energy_map(
        pd.read_csv(discrete_results_path), pd.read_csv(ptmg_results_path)
    )
    geometries = load_monomer_geometries(
        monomer_manifest,
        monomer_root,
        smiles_map=smiles_map,
        energies=energies,
    )
    required_ids = set(pairs["diisocyanate_id"]) | set(pairs["oh_component_id"])
    missing_geometry = sorted(required_ids - geometries.keys())
    if missing_geometry:
        raise ValueError(f"预反应配对缺少单体几何: {missing_geometry}")
    tasks = _materialize_tasks(
        pairs,
        geometries,
        output_root,
        distance_a=distance_a,
        attack_angle_deg=attack_angle_deg,
    )
    pair_path = output_root / "配对清单.csv"
    task_path = output_root / "预反应复合物任务.csv"
    _atomic_text(pair_path, pairs.to_csv(index=False))
    _atomic_text(task_path, tasks.to_csv(index=False, float_format="%.12g"))
    release = {
        "release_id": release_id,
        "status": "ready",
        "counts": {
            "pairs": len(pairs),
            "tasks": len(tasks),
            "ready_tasks": int(tasks["geometry_status"].eq("ready").sum()),
            "blocked_tasks": int(
                tasks["geometry_status"].ne("ready").sum()
            ),
        },
        "protocol": {
            "starts_per_pair": len(TORSION_STARTS_DEG),
            "reactive_distance_a": distance_a,
            "attack_angle_deg": attack_angle_deg,
            "torsion_starts_deg": list(TORSION_STARTS_DEG),
            "constraint_force_constant": 0.5,
            "xtb_version": "6.7.1",
            "xtb_binary_sha256": XTB_BINARY_SHA256,
            "method": "GFN2-xTB",
            "environment_model": "gas_phase",
            "optimization_level": "tight",
            "interpretation_limit": (
                "constrained prereaction association proxy; not a DFT barrier, "
                "reaction rate, or TPU performance label"
            ),
        },
        "files": {
            pair_path.name: {
                "bytes": pair_path.stat().st_size,
                "sha256": sha256(pair_path),
            },
            task_path.name: {
                "bytes": task_path.stat().st_size,
                "sha256": sha256(task_path),
            },
        },
    }
    _atomic_text(
        output_root / "预反应任务发布清单.json",
        json.dumps(release, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return tasks, pairs, release


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--高层子集",
        type=Path,
        default=ROOT / "结果" / "现实筛选" / "高层DFT候选12.csv",
    )
    parser.add_argument(
        "--单体清单",
        type=Path,
        default=ROOT / "计算" / "现实预反应复合物" / "单体结构清单.csv",
    )
    parser.add_argument(
        "--单体根目录",
        type=Path,
        default=ROOT / "计算" / "现实预反应复合物",
    )
    parser.add_argument(
        "--现实构件", type=Path, default=ROOT / "数据" / "现实库" / "构件.csv"
    )
    parser.add_argument(
        "--PTMG模型",
        type=Path,
        default=ROOT / "数据" / "现实库" / "PTMG代表模型.csv",
    )
    parser.add_argument(
        "--离散逐构象",
        type=Path,
        default=ROOT
        / "计算"
        / "现实xTB系综"
        / "聚合"
        / "逐构象描述符.csv",
    )
    parser.add_argument(
        "--PTMG逐构象",
        type=Path,
        default=ROOT
        / "计算"
        / "现实PTMG_xTB"
        / "聚合"
        / "逐构象描述符.csv",
    )
    parser.add_argument(
        "--输出目录",
        type=Path,
        default=ROOT / "计算" / "现实预反应复合物",
    )
    parser.add_argument("--反应距离", type=float, default=2.7)
    parser.add_argument("--进攻角", type=float, default=105.0)
    parser.add_argument(
        "--发布ID", default="tpu-reality-prereaction-complexes-20260825-v1"
    )
    args = parser.parse_args(argv)
    _, _, release = build_release(
        subset_path=args.高层子集,
        monomer_manifest_path=args.单体清单,
        monomer_root=args.单体根目录,
        components_path=args.现实构件,
        ptmg_models_path=args.PTMG模型,
        discrete_results_path=args.离散逐构象,
        ptmg_results_path=args.PTMG逐构象,
        output_root=args.输出目录,
        release_id=args.发布ID,
        distance_a=args.反应距离,
        attack_angle_deg=args.进攻角,
    )
    print(json.dumps(release["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
