"""严格防泄漏的 TPU 计算观测图数据准备。

本模块只消费发布层 ``计算观测.csv.gz``，不重新生成划分或权重。默认任务固定
为 Rg、density、bulk_modulus 和 thermal_conductivity；Tg 只有显式声明为
探索任务时才允许进入。图由 RDKit 生成，批处理结果以 NumPy 为主，PyTorch
仅在调用者明确请求时延迟导入。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd
from rdkit import Chem


DEFAULT_TARGETS = (
    "Rg",
    "density",
    "bulk_modulus",
    "thermal_conductivity",
)
EXPLORATORY_TARGET = "Tg"
EXPECTED_TASK_ID = "计算_结构多任务预训练"
TRAINING_USAGE_MODES = ("primary_train", "auxiliary_train")
VALID_SPLITS = frozenset({"train", "validation", "test"})

NODE_FEATURE_NAMES = (
    "atomic_number",
    "total_degree",
    "total_valence",
    "formal_charge",
    "total_hydrogens",
    "hybridization",
    "chirality",
    "is_aromatic",
    "is_in_ring",
)
EDGE_FEATURE_NAMES = (
    "bond_type",
    "stereo",
    "is_conjugated",
    "is_in_ring",
)

REQUIRED_COLUMNS = frozenset(
    {
        "task_id",
        "usage_mode",
        "model_ready",
        "source_id",
        "source_family_id",
        "observation_id",
        "canonical_structure",
        "property_name",
        "value",
        "unit",
        "leakage_group",
        "development_split",
        "recommended_loss_weight",
    }
)


@dataclass(frozen=True)
class MolecularGraph:
    """一个分子的离散、双向图。"""

    canonical_smiles: str
    node_features: np.ndarray
    edge_index: np.ndarray
    edge_features: np.ndarray

    def to_serializable(self) -> dict[str, Any]:
        return {
            "canonical_smiles": self.canonical_smiles,
            "node_features": self.node_features.tolist(),
            "edge_index": self.edge_index.tolist(),
            "edge_features": self.edge_features.tolist(),
        }


@dataclass(frozen=True)
class GraphSample:
    """一个发布观测及其分子图；目标向量用掩码表达缺失任务。"""

    observation_id: str
    source_id: str
    source_family_id: str
    leakage_group: str
    development_split: str
    usage_mode: str
    canonical_structure: str
    target_name: str
    target_unit: str
    recommended_loss_weight: float
    target_names: tuple[str, ...]
    node_features: np.ndarray
    edge_index: np.ndarray
    edge_features: np.ndarray
    targets: np.ndarray
    target_mask: np.ndarray
    target_weights: np.ndarray

    def to_serializable(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "source_id": self.source_id,
            "source_family_id": self.source_family_id,
            "leakage_group": self.leakage_group,
            "development_split": self.development_split,
            "usage_mode": self.usage_mode,
            "canonical_structure": self.canonical_structure,
            "target_name": self.target_name,
            "target_unit": self.target_unit,
            "recommended_loss_weight": self.recommended_loss_weight,
            "target_names": list(self.target_names),
            "node_features": self.node_features.tolist(),
            "edge_index": self.edge_index.tolist(),
            "edge_features": self.edge_features.tolist(),
            "targets": [None if np.isnan(value) else float(value) for value in self.targets],
            "target_mask": self.target_mask.tolist(),
            "target_weights": self.target_weights.tolist(),
        }


def _require_columns(frame: pd.DataFrame) -> None:
    missing = REQUIRED_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(f"计算观测缺少必需字段: {sorted(missing)}")


def _clean_text(series: pd.Series, name: str) -> pd.Series:
    values = series.astype("string").str.strip()
    if values.isna().any() or values.eq("").any():
        raise ValueError(f"{name} 不能为空")
    return values.astype(str)


def _parse_model_ready(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series.dtype):
        if series.isna().any():
            raise ValueError("model_ready 不能为空")
        return series.astype(bool)
    normalized = series.astype("string").str.strip().str.lower()
    mapping = {"true": True, "1": True, "false": False, "0": False}
    invalid = normalized.isna() | ~normalized.isin(mapping)
    if invalid.any():
        raise ValueError("model_ready 必须是明确布尔值")
    return normalized.map(mapping).astype(bool)


def _normalize_targets(
    targets: Sequence[str] | None, allow_exploratory_tg: bool
) -> tuple[str, ...]:
    selected = tuple(DEFAULT_TARGETS if targets is None else map(str, targets))
    if not selected:
        raise ValueError("targets 不能为空")
    if len(set(selected)) != len(selected):
        raise ValueError("targets 不能重复")
    allowed = set(DEFAULT_TARGETS) | {EXPLORATORY_TARGET}
    unsupported = set(selected).difference(allowed)
    if unsupported:
        raise ValueError(f"不支持的计算目标: {sorted(unsupported)}")
    if EXPLORATORY_TARGET in selected and not allow_exploratory_tg:
        raise ValueError("Tg 当前仅为探索目标，必须显式开启 allow_exploratory_tg")
    return selected


def normalize_target_value(property_name: str, value: Any, unit: str) -> tuple[float, str]:
    """把支持的目标转换到发布基线使用的规范单位。"""

    try:
        numeric = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"目标值不是数值: {value!r}") from error
    if not np.isfinite(numeric):
        raise ValueError("目标值必须为有限数")
    normalized_unit = str(unit).strip().replace("·", "*")
    key = normalized_unit.lower().replace(" ", "")

    if property_name == "Rg":
        factors = {"angstrom": 1.0, "å": 1.0, "a": 1.0, "nm": 10.0}
        canonical_unit = "angstrom"
    elif property_name == "density":
        factors = {"g/cm^3": 1.0, "g/cm3": 1.0, "kg/m^3": 1e-3, "kg/m3": 1e-3}
        canonical_unit = "g/cm^3"
    elif property_name == "bulk_modulus":
        factors = {"pa": 1.0, "kpa": 1e3, "mpa": 1e6, "gpa": 1e9}
        canonical_unit = "Pa"
    elif property_name == "thermal_conductivity":
        factors = {
            "w/(m*k)": 1.0,
            "w/(m·k)": 1.0,
            "w/m/k": 1.0,
            "w*m^-1*k^-1": 1.0,
            "wm^-1k^-1": 1.0,
        }
        canonical_unit = "W/(m*K)"
    elif property_name == EXPLORATORY_TARGET:
        if key in {"degc", "°c", "celsius"}:
            return numeric, "degC"
        if key in {"k", "kelvin"}:
            return numeric - 273.15, "degC"
        raise ValueError(f"目标单位不受支持: {property_name} / {unit}")
    else:
        raise ValueError(f"不支持的计算目标: {property_name}")

    if key not in factors:
        raise ValueError(f"目标单位不受支持: {property_name} / {unit}")
    converted = numeric * factors[key]
    if not np.isfinite(converted):
        raise ValueError("规范化目标值必须为有限数")
    return float(converted), canonical_unit


def _validate_group_split(frame: pd.DataFrame) -> None:
    split = _clean_text(frame["development_split"], "development_split")
    invalid = sorted(set(split).difference(VALID_SPLITS))
    if invalid:
        raise ValueError(f"development_split 含非法值: {invalid}")
    group = _clean_text(frame["leakage_group"], "leakage_group")
    audit = pd.DataFrame({"group": group, "split": split})
    leaking = audit.groupby("group", sort=False)["split"].nunique()
    if leaking.gt(1).any():
        examples = ", ".join(map(str, leaking[leaking.gt(1)].index[:5]))
        raise ValueError(f"硬组跨越多个数据折: {examples}")


def _bond_type_code(bond: Chem.Bond) -> int:
    bond_type = bond.GetBondType()
    mapping = {
        Chem.BondType.SINGLE: 1,
        Chem.BondType.DOUBLE: 2,
        Chem.BondType.TRIPLE: 3,
        Chem.BondType.AROMATIC: 4,
    }
    return mapping.get(bond_type, 0)


def smiles_to_graph(smiles: str) -> MolecularGraph:
    """以确定性的原子顺序把 SMILES 转为双向离散图。"""

    if not isinstance(smiles, str) or not smiles.strip():
        raise ValueError("canonical_structure 不能为空")
    molecule = Chem.MolFromSmiles(smiles.strip())
    if molecule is None:
        raise ValueError(f"无法解析 canonical_structure: {smiles}")
    canonical = Chem.MolToSmiles(molecule, canonical=True)
    node_rows = []
    for atom in molecule.GetAtoms():
        node_rows.append(
            [
                atom.GetAtomicNum(),
                atom.GetTotalDegree(),
                atom.GetTotalValence(),
                atom.GetFormalCharge(),
                atom.GetTotalNumHs(includeNeighbors=True),
                int(atom.GetHybridization()),
                int(atom.GetChiralTag()),
                int(atom.GetIsAromatic()),
                int(atom.IsInRing()),
            ]
        )
    nodes = np.asarray(node_rows, dtype=np.int16)
    if nodes.shape != (molecule.GetNumAtoms(), len(NODE_FEATURE_NAMES)):
        raise ValueError("RDKit 节点特征形状异常")

    edge_pairs: list[tuple[int, int]] = []
    edge_rows: list[list[int]] = []
    for bond in molecule.GetBonds():
        begin, end = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        features = [
            _bond_type_code(bond),
            int(bond.GetStereo()),
            int(bond.GetIsConjugated()),
            int(bond.IsInRing()),
        ]
        edge_pairs.extend(((begin, end), (end, begin)))
        edge_rows.extend((features, features.copy()))
    if edge_pairs:
        edge_index = np.asarray(edge_pairs, dtype=np.int64).T
        edge_features = np.asarray(edge_rows, dtype=np.int16)
    else:
        edge_index = np.empty((2, 0), dtype=np.int64)
        edge_features = np.empty((0, len(EDGE_FEATURE_NAMES)), dtype=np.int16)
    return MolecularGraph(canonical, nodes, edge_index, edge_features)


def prepare_observations(
    frame: pd.DataFrame,
    targets: Sequence[str] | None = None,
    usage_modes: Iterable[str] = TRAINING_USAGE_MODES,
    *,
    allow_exploratory_tg: bool = False,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """筛选并严格校验发布计算观测，不重算任何治理字段。"""

    _require_columns(frame)
    target_names = _normalize_targets(targets, allow_exploratory_tg)
    allowed_usage = tuple(map(str, usage_modes))
    if not allowed_usage or not set(allowed_usage).issubset(TRAINING_USAGE_MODES):
        raise ValueError("usage_modes 只能包含 primary_train/auxiliary_train")

    work = frame.copy()
    work["model_ready"] = _parse_model_ready(work["model_ready"])
    work = work.loc[
        work["task_id"].astype(str).eq(EXPECTED_TASK_ID)
        & work["property_name"].astype(str).isin(target_names)
        & work["model_ready"]
        & work["usage_mode"].astype(str).isin(allowed_usage)
    ].copy()
    if work.empty:
        raise ValueError("没有满足目标、任务、model_ready 与 usage_mode 门的观测")

    for column in (
        "task_id",
        "usage_mode",
        "source_id",
        "source_family_id",
        "observation_id",
        "canonical_structure",
        "property_name",
        "unit",
        "leakage_group",
        "development_split",
    ):
        work[column] = _clean_text(work[column], column)

    duplicate = work.duplicated(["observation_id", "property_name"], keep=False)
    if duplicate.any():
        examples = ", ".join(work.loc[duplicate, "observation_id"].unique()[:5])
        raise ValueError(f"重复观测目标: {examples}")
    if work["observation_id"].duplicated().any():
        raise ValueError("observation_id 对多个目标复用，无法保持逐观测主键")

    weights = pd.to_numeric(work["recommended_loss_weight"], errors="coerce")
    if (~np.isfinite(weights.to_numpy(dtype=np.float64)) | weights.le(0)).any():
        raise ValueError("recommended_loss_weight 必须存在、有限且严格为正")
    work["recommended_loss_weight"] = weights.astype(np.float64)
    _validate_group_split(work)

    structure_counts = work.groupby("leakage_group")["canonical_structure"].nunique()
    if structure_counts.gt(1).any():
        examples = ", ".join(map(str, structure_counts[structure_counts.gt(1)].index[:5]))
        raise ValueError(f"同一 leakage_group 对应多个结构: {examples}")

    normalized_values: list[float] = []
    normalized_units: list[str] = []
    for row in work.itertuples(index=False):
        value, unit = normalize_target_value(row.property_name, row.value, row.unit)
        normalized_values.append(value)
        normalized_units.append(unit)
    work["value"] = np.asarray(normalized_values, dtype=np.float64)
    work["unit"] = normalized_units
    work = work.sort_values(["observation_id", "property_name"], kind="mergesort")
    return work.reset_index(drop=True), target_names


def load_computational_observations(
    path: str | Path,
    targets: Sequence[str] | None = None,
    usage_modes: Iterable[str] = TRAINING_USAGE_MODES,
    *,
    allow_exploratory_tg: bool = False,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """从发布 CSV/CSV.GZ 读取严格训练观测。"""

    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    header = pd.read_csv(source, nrows=0)
    missing = REQUIRED_COLUMNS.difference(header.columns)
    if missing:
        raise ValueError(f"计算观测缺少必需字段: {sorted(missing)}")
    frame = pd.read_csv(source, usecols=sorted(REQUIRED_COLUMNS), low_memory=False)
    return prepare_observations(
        frame,
        targets,
        usage_modes,
        allow_exploratory_tg=allow_exploratory_tg,
    )


def build_graph_dataset(
    frame: pd.DataFrame,
    targets: Sequence[str] | None = None,
    usage_modes: Iterable[str] = TRAINING_USAGE_MODES,
    *,
    allow_exploratory_tg: bool = False,
) -> list[GraphSample]:
    """构建逐观测、多任务掩码图样本；不合并或重加权重复结构。"""

    observations, target_names = prepare_observations(
        frame,
        targets,
        usage_modes,
        allow_exploratory_tg=allow_exploratory_tg,
    )
    graph_cache: dict[str, MolecularGraph] = {}
    samples: list[GraphSample] = []
    target_lookup = {name: index for index, name in enumerate(target_names)}
    for row in observations.itertuples(index=False):
        structure = row.canonical_structure
        if structure not in graph_cache:
            graph_cache[structure] = smiles_to_graph(structure)
        graph = graph_cache[structure]
        target_index = target_lookup[row.property_name]
        values = np.full(len(target_names), np.nan, dtype=np.float32)
        mask = np.zeros(len(target_names), dtype=bool)
        weights = np.zeros(len(target_names), dtype=np.float32)
        values[target_index] = np.float32(row.value)
        mask[target_index] = True
        weights[target_index] = np.float32(row.recommended_loss_weight)
        samples.append(
            GraphSample(
                observation_id=row.observation_id,
                source_id=row.source_id,
                source_family_id=row.source_family_id,
                leakage_group=row.leakage_group,
                development_split=row.development_split,
                usage_mode=row.usage_mode,
                canonical_structure=structure,
                target_name=row.property_name,
                target_unit=row.unit,
                recommended_loss_weight=float(row.recommended_loss_weight),
                target_names=target_names,
                node_features=graph.node_features.copy(),
                edge_index=graph.edge_index.copy(),
                edge_features=graph.edge_features.copy(),
                targets=values,
                target_mask=mask,
                target_weights=weights,
            )
        )
    return samples


def sample_metadata_frame(samples: Sequence[GraphSample]) -> pd.DataFrame:
    """生成便于审计划分和权重的逐样本元数据表。"""

    rows = [
        {
            "observation_id": sample.observation_id,
            "source_id": sample.source_id,
            "source_family_id": sample.source_family_id,
            "leakage_group": sample.leakage_group,
            "development_split": sample.development_split,
            "usage_mode": sample.usage_mode,
            "canonical_structure": sample.canonical_structure,
            "target_name": sample.target_name,
            "target_unit": sample.target_unit,
            "recommended_loss_weight": sample.recommended_loss_weight,
        }
        for sample in samples
    ]
    return pd.DataFrame(rows)


def collate_graphs(
    samples: Sequence[GraphSample], *, as_torch: bool = False
) -> dict[str, Any]:
    """拼接可序列化批图；边索引自动增加节点偏移。"""

    if not samples:
        raise ValueError("samples 不能为空")
    target_names = samples[0].target_names
    if any(sample.target_names != target_names for sample in samples):
        raise ValueError("同一批图的 target_names 必须一致")

    node_parts: list[np.ndarray] = []
    edge_index_parts: list[np.ndarray] = []
    edge_feature_parts: list[np.ndarray] = []
    node_graph_parts: list[np.ndarray] = []
    graph_ptr = [0]
    offset = 0
    for graph_index, sample in enumerate(samples):
        node_count = len(sample.node_features)
        node_parts.append(sample.node_features)
        edge_index_parts.append(sample.edge_index + offset)
        edge_feature_parts.append(sample.edge_features)
        node_graph_parts.append(np.full(node_count, graph_index, dtype=np.int64))
        offset += node_count
        graph_ptr.append(offset)

    edge_index = (
        np.concatenate(edge_index_parts, axis=1)
        if any(part.shape[1] for part in edge_index_parts)
        else np.empty((2, 0), dtype=np.int64)
    )
    edge_features = (
        np.concatenate(edge_feature_parts, axis=0)
        if any(len(part) for part in edge_feature_parts)
        else np.empty((0, len(EDGE_FEATURE_NAMES)), dtype=np.int16)
    )
    batch: dict[str, Any] = {
        "node_features": np.concatenate(node_parts, axis=0),
        "edge_index": edge_index,
        "edge_features": edge_features,
        "graph_index": np.concatenate(node_graph_parts),
        "graph_ptr": np.asarray(graph_ptr, dtype=np.int64),
        "targets": np.stack([sample.targets for sample in samples]),
        "target_mask": np.stack([sample.target_mask for sample in samples]),
        "target_weights": np.stack([sample.target_weights for sample in samples]),
        "target_names": list(target_names),
        "observation_id": [sample.observation_id for sample in samples],
        "source_id": [sample.source_id for sample in samples],
        "source_family_id": [sample.source_family_id for sample in samples],
        "leakage_group": [sample.leakage_group for sample in samples],
        "development_split": [sample.development_split for sample in samples],
        "usage_mode": [sample.usage_mode for sample in samples],
    }
    if as_torch:
        try:
            import torch
        except ImportError as error:
            raise RuntimeError("请求 as_torch=True，但当前环境没有安装 PyTorch") from error
        batch["node_features"] = torch.from_numpy(batch["node_features"]).float()
        batch["edge_features"] = torch.from_numpy(batch["edge_features"]).float()
        batch["edge_index"] = torch.from_numpy(batch["edge_index"]).long()
        batch["graph_index"] = torch.from_numpy(batch["graph_index"]).long()
        batch["graph_ptr"] = torch.from_numpy(batch["graph_ptr"]).long()
        batch["targets"] = torch.from_numpy(batch["targets"]).float()
        batch["target_mask"] = torch.from_numpy(batch["target_mask"]).bool()
        batch["target_weights"] = torch.from_numpy(batch["target_weights"]).float()
    return batch


def batch_to_serializable(batch: dict[str, Any]) -> dict[str, Any]:
    """把 NumPy 批图转换为 JSON/msgpack 友好的纯 Python 对象。"""

    serializable: dict[str, Any] = {}
    for key, value in batch.items():
        if isinstance(value, np.ndarray):
            if np.issubdtype(value.dtype, np.floating):
                serializable[key] = np.where(np.isnan(value), None, value).tolist()
            else:
                serializable[key] = value.tolist()
        elif hasattr(value, "detach") and hasattr(value, "cpu"):
            array = value.detach().cpu().numpy()
            if np.issubdtype(array.dtype, np.floating):
                serializable[key] = np.where(np.isnan(array), None, array).tolist()
            else:
                serializable[key] = array.tolist()
        else:
            serializable[key] = value
    return serializable


def torch_available() -> bool:
    """不导入 PyTorch，仅探测其模块是否可用。"""

    import importlib.util

    return importlib.util.find_spec("torch") is not None
