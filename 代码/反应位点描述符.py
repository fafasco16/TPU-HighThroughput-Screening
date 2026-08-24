"""从含氢XYZ构象计算TPU构件反应位点的几何可及性描述符。

本模块只接受两类构件：恰好含两个异氰酸酯基的二异氰酸酯，或恰好
含两个羟基的二醇。RDKit含氢原子顺序、XYZ元素顺序、Bondi半径及
反应位点数均为关闭式数据门；任一项不满足时不返回部分描述符。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np
from rdkit import Chem


DEFAULT_PROBE_RADIUS_A = 1.4
DEFAULT_SPHERE_POINT_COUNT = 960

# Bondi, J. Phys. Chem. 1964, 68, 441--451；未收录元素必须关闭计算门。
BONDI_RADII_A = MappingProxyType(
    {
        "H": 1.20,
        "B": 1.92,
        "C": 1.70,
        "N": 1.55,
        "O": 1.52,
        "F": 1.47,
        "Si": 2.10,
        "P": 1.80,
        "S": 1.80,
        "Cl": 1.75,
        "Br": 1.85,
        "I": 1.98,
    }
)

_NCO_PATTERN = Chem.MolFromSmarts("[N:1]=[C:2]=[O:3]")
_HYDROXYL_PATTERN = Chem.MolFromSmarts("[O;H1;+0]")
_DIISOCYANATE_ROLES = frozenset({"diisocyanate"})
_DIOL_ROLES = frozenset({"macrodiol_proxy", "chain_extender", "chain_extender_diol"})


class ReactiveSiteDescriptorError(ValueError):
    """反应位点或几何输入不能通过严格数据门。"""


@dataclass(frozen=True)
class ReactiveSiteModel:
    molecule: Chem.Mol
    site_kind: str
    site_atom_indices: tuple[int, int]
    element_symbols: tuple[str, ...]
    excluded_topological_indices: tuple[frozenset[int], frozenset[int]]


@dataclass(frozen=True)
class XYZConformer:
    conformer_index: int
    comment: str
    element_symbols: tuple[str, ...]
    coordinates_a: np.ndarray


def bondi_radius(element_symbol: str) -> float:
    """返回元素的Bondi半径；缺少半径时禁止继续。"""

    try:
        return BONDI_RADII_A[str(element_symbol)]
    except KeyError as exc:
        raise ReactiveSiteDescriptorError(
            f"缺少元素 {element_symbol!r} 的Bondi半径"
        ) from exc


def _site_matches(molecule: Chem.Mol) -> tuple[tuple[int, ...], tuple[int, ...]]:
    nco_carbons = tuple(
        sorted({match[1] for match in molecule.GetSubstructMatches(_NCO_PATTERN)})
    )
    hydroxyl_oxygens = tuple(
        sorted({match[0] for match in molecule.GetSubstructMatches(_HYDROXYL_PATTERN)})
    )
    return nco_carbons, hydroxyl_oxygens


def identify_reactive_sites(
    canonical_smiles: str, component_role: str | None = None
) -> tuple[str, tuple[int, int]]:
    """识别恰好两个NCO碳或两个羟基氧，返回位点类型与0基原子索引。"""

    molecule = Chem.MolFromSmiles(str(canonical_smiles))
    if molecule is None:
        raise ReactiveSiteDescriptorError(f"无法解析canonical_smiles: {canonical_smiles}")
    nco_carbons, hydroxyl_oxygens = _site_matches(molecule)
    role = None if component_role is None else str(component_role)

    if role in _DIISOCYANATE_ROLES:
        kind, sites = "nco_carbon", nco_carbons
    elif role in _DIOL_ROLES:
        kind, sites = "hydroxyl_oxygen", hydroxyl_oxygens
    elif role is not None:
        raise ReactiveSiteDescriptorError(f"不支持的component_role: {role}")
    elif len(nco_carbons) == 2 and len(hydroxyl_oxygens) != 2:
        kind, sites = "nco_carbon", nco_carbons
    elif len(hydroxyl_oxygens) == 2 and len(nco_carbons) != 2:
        kind, sites = "hydroxyl_oxygen", hydroxyl_oxygens
    else:
        raise ReactiveSiteDescriptorError(
            "无法唯一确定反应位点类型: "
            f"NCO碳={len(nco_carbons)}, 羟基氧={len(hydroxyl_oxygens)}"
        )

    if len(sites) != 2:
        raise ReactiveSiteDescriptorError(
            f"{kind}位点数必须恰好为2，实际为{len(sites)}"
        )
    return kind, (int(sites[0]), int(sites[1]))


def prepare_reactive_site_model(
    canonical_smiles: str, component_role: str | None = None
) -> ReactiveSiteModel:
    """建立含氢RDKit原子顺序、位点及1-2/1-3拓扑排除集合。"""

    molecule = Chem.MolFromSmiles(str(canonical_smiles))
    if molecule is None:
        raise ReactiveSiteDescriptorError(f"无法解析canonical_smiles: {canonical_smiles}")
    site_kind, site_indices = identify_reactive_sites(canonical_smiles, component_role)
    molecule = Chem.AddHs(molecule)
    symbols = tuple(atom.GetSymbol() for atom in molecule.GetAtoms())
    # 在真正读取几何前就核验所有元素半径，防止产生部分结果。
    for symbol in symbols:
        bondi_radius(symbol)
    distance_matrix = Chem.GetDistanceMatrix(molecule)
    excluded = tuple(
        frozenset(
            int(index)
            for index, topological_distance in enumerate(distance_matrix[site_index])
            if topological_distance <= 2.0
        )
        for site_index in site_indices
    )
    return ReactiveSiteModel(
        molecule=molecule,
        site_kind=site_kind,
        site_atom_indices=site_indices,
        element_symbols=symbols,
        excluded_topological_indices=(excluded[0], excluded[1]),
    )


@lru_cache(maxsize=16)
def fibonacci_sphere(point_count: int = DEFAULT_SPHERE_POINT_COUNT) -> tuple[tuple[float, float, float], ...]:
    """生成确定性的单位Fibonacci球面点。"""

    if isinstance(point_count, bool) or int(point_count) != point_count or point_count < 4:
        raise ReactiveSiteDescriptorError("sphere_point_count必须是至少4的整数")
    point_count = int(point_count)
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    points: list[tuple[float, float, float]] = []
    for index in range(point_count):
        y = 1.0 - 2.0 * (index + 0.5) / point_count
        radial = math.sqrt(max(0.0, 1.0 - y * y))
        angle = golden_angle * index
        points.append((math.cos(angle) * radial, y, math.sin(angle) * radial))
    return tuple(points)


def validate_xyz_geometry(
    model: ReactiveSiteModel,
    element_symbols: Sequence[str],
    coordinates_a: Sequence[Sequence[float]] | np.ndarray,
) -> np.ndarray:
    """核对XYZ元素序列、坐标形状与有限性。"""

    actual_symbols = tuple(str(symbol) for symbol in element_symbols)
    if actual_symbols != model.element_symbols:
        mismatch = next(
            (
                index
                for index, pair in enumerate(zip(actual_symbols, model.element_symbols))
                if pair[0] != pair[1]
            ),
            min(len(actual_symbols), len(model.element_symbols)),
        )
        raise ReactiveSiteDescriptorError(
            "XYZ元素序列与含氢RDKit原子顺序不匹配: "
            f"index={mismatch}, actual_count={len(actual_symbols)}, "
            f"expected_count={len(model.element_symbols)}"
        )
    coordinates = np.asarray(coordinates_a, dtype=float)
    expected_shape = (len(model.element_symbols), 3)
    if coordinates.shape != expected_shape:
        raise ReactiveSiteDescriptorError(
            f"XYZ坐标形状必须为{expected_shape}，实际为{coordinates.shape}"
        )
    if not np.isfinite(coordinates).all():
        raise ReactiveSiteDescriptorError("XYZ坐标包含非有限值")
    return coordinates


def _site_sasa(
    site_index: int,
    coordinates: np.ndarray,
    expanded_radii: np.ndarray,
    sphere_points: np.ndarray,
) -> tuple[float, float]:
    radius = float(expanded_radii[site_index])
    surface_points = coordinates[site_index] + radius * sphere_points
    other_indices = np.arange(len(coordinates)) != site_index
    blockers = coordinates[other_indices]
    blocker_radii_squared = np.square(expanded_radii[other_indices])
    squared_distances = np.sum(
        np.square(surface_points[:, np.newaxis, :] - blockers[np.newaxis, :, :]), axis=2
    )
    accessible = ~np.any(squared_distances < blocker_radii_squared[np.newaxis, :], axis=1)
    relative_sasa = float(np.count_nonzero(accessible) / len(surface_points))
    sasa = relative_sasa * 4.0 * math.pi * radius * radius
    return sasa, relative_sasa


def _site_nonbonded_gap(
    site_position: int,
    model: ReactiveSiteModel,
    coordinates: np.ndarray,
    bondi_radii: np.ndarray,
) -> float:
    site_index = model.site_atom_indices[site_position]
    excluded = model.excluded_topological_indices[site_position]
    eligible = [index for index in range(len(coordinates)) if index not in excluded]
    if not eligible:
        raise ReactiveSiteDescriptorError(
            f"反应位点{site_position + 1}不存在排除1-2和1-3后的非键接原子"
        )
    distances = np.linalg.norm(coordinates[eligible] - coordinates[site_index], axis=1)
    gaps = distances - bondi_radii[site_index] - bondi_radii[eligible]
    return float(np.min(gaps))


def _aggregate(prefix: str, values: Sequence[float]) -> dict[str, float]:
    first, second = (float(values[0]), float(values[1]))
    return {
        f"{prefix}_mean": math.fsum((first, second)) / 2.0,
        f"{prefix}_min": min(first, second),
        f"{prefix}_max": max(first, second),
        f"{prefix}_abs_difference": abs(first - second),
    }


def describe_reactive_sites(
    model: ReactiveSiteModel,
    element_symbols: Sequence[str],
    coordinates_a: Sequence[Sequence[float]] | np.ndarray,
    *,
    probe_radius_a: float = DEFAULT_PROBE_RADIUS_A,
    sphere_point_count: int = DEFAULT_SPHERE_POINT_COUNT,
) -> dict[str, Any]:
    """计算一个构象中两个反应位点的SASA、相对SASA和非键接净间隙。"""

    if not math.isfinite(probe_radius_a) or probe_radius_a <= 0:
        raise ReactiveSiteDescriptorError("probe_radius_a必须是有限正数")
    coordinates = validate_xyz_geometry(model, element_symbols, coordinates_a)
    unit_sphere = np.asarray(fibonacci_sphere(sphere_point_count), dtype=float)
    bondi_radii = np.asarray([bondi_radius(symbol) for symbol in model.element_symbols])
    expanded_radii = bondi_radii + float(probe_radius_a)

    sasa_values: list[float] = []
    relative_values: list[float] = []
    gap_values: list[float] = []
    result: dict[str, Any] = {
        "reactive_site_kind": model.site_kind,
        "reactive_site_count": 2,
        "probe_radius_a": float(probe_radius_a),
        "sphere_point_count": int(sphere_point_count),
    }
    for position, site_index in enumerate(model.site_atom_indices, start=1):
        sasa, relative = _site_sasa(
            site_index, coordinates, expanded_radii, unit_sphere
        )
        gap = _site_nonbonded_gap(position - 1, model, coordinates, bondi_radii)
        sasa_values.append(sasa)
        relative_values.append(relative)
        gap_values.append(gap)
        result.update(
            {
                f"site_{position}_atom_index": site_index,
                f"site_{position}_element": model.element_symbols[site_index],
                f"site_{position}_sasa_a2": sasa,
                f"site_{position}_relative_sasa": relative,
                f"site_{position}_nonbonded_net_gap_a": gap,
            }
        )
    result["reactive_site_distance_a"] = float(
        np.linalg.norm(
            coordinates[model.site_atom_indices[0]]
            - coordinates[model.site_atom_indices[1]]
        )
    )
    result.update(_aggregate("site_sasa_a2", sasa_values))
    result.update(_aggregate("site_relative_sasa", relative_values))
    result.update(_aggregate("site_nonbonded_net_gap_a", gap_values))
    return result


def parse_xyz_conformers(path: Path) -> list[XYZConformer]:
    """严格解析单帧或多帧XYZ，保留元素顺序与坐标。"""

    if not path.is_file():
        raise ReactiveSiteDescriptorError(f"XYZ文件不存在: {path}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ReactiveSiteDescriptorError(f"无法读取XYZ文件: {path}") from exc
    frames: list[XYZConformer] = []
    position = 0
    while position < len(lines):
        while position < len(lines) and not lines[position].strip():
            position += 1
        if position >= len(lines):
            break
        frame_index = len(frames) + 1
        try:
            atom_count = int(lines[position].strip())
        except ValueError as exc:
            raise ReactiveSiteDescriptorError(
                f"frame {frame_index}: 无效原子数"
            ) from exc
        if atom_count <= 0 or position + atom_count + 1 >= len(lines):
            raise ReactiveSiteDescriptorError(f"frame {frame_index}: XYZ帧截断或为空")
        comment = lines[position + 1]
        symbols: list[str] = []
        coordinates: list[list[float]] = []
        for line in lines[position + 2 : position + atom_count + 2]:
            fields = line.split()
            if len(fields) < 4:
                raise ReactiveSiteDescriptorError(f"frame {frame_index}: 无效原子行")
            symbols.append(fields[0])
            try:
                coordinates.append([float(value) for value in fields[1:4]])
            except ValueError as exc:
                raise ReactiveSiteDescriptorError(
                    f"frame {frame_index}: 无效坐标"
                ) from exc
        coordinate_array = np.asarray(coordinates, dtype=float)
        if not np.isfinite(coordinate_array).all():
            raise ReactiveSiteDescriptorError(f"frame {frame_index}: 坐标包含非有限值")
        frames.append(
            XYZConformer(
                conformer_index=frame_index,
                comment=comment,
                element_symbols=tuple(symbols),
                coordinates_a=coordinate_array,
            )
        )
        position += atom_count + 2
    if not frames:
        raise ReactiveSiteDescriptorError("XYZ文件不含构象")
    return frames


def describe_task_xyz(
    task: Mapping[str, Any],
    xyz_path: Path,
    *,
    probe_radius_a: float = DEFAULT_PROBE_RADIUS_A,
    sphere_point_count: int = DEFAULT_SPHERE_POINT_COUNT,
) -> list[dict[str, Any]]:
    """对任务的全部XYZ构象生成逐构象反应位点描述符。"""

    if "canonical_smiles" not in task:
        raise ReactiveSiteDescriptorError("任务缺少canonical_smiles")
    model = prepare_reactive_site_model(
        str(task["canonical_smiles"]),
        None if "component_role" not in task else str(task["component_role"]),
    )
    identity = {
        key: task[key]
        for key in ("task_index", "task_slug", "candidate_id", "component_role")
        if key in task
    }
    rows: list[dict[str, Any]] = []
    for frame in parse_xyz_conformers(xyz_path):
        descriptor = describe_reactive_sites(
            model,
            frame.element_symbols,
            frame.coordinates_a,
            probe_radius_a=probe_radius_a,
            sphere_point_count=sphere_point_count,
        )
        rows.append(
            {
                **identity,
                "conformer_index": frame.conformer_index,
                "xyz_comment": frame.comment,
                **descriptor,
            }
        )
    return rows
