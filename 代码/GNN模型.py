"""无 PyG 依赖的轻量多任务图神经网络核心。

模块导入不要求安装 PyTorch。数据校验和目标标准化只依赖 NumPy；创建模型、
计算损失或启用 Torch 随机种子时才会加载 PyTorch。模型的批图接口为：

``node_features``
    ``[节点数, 节点特征数]`` 浮点张量。
``edge_index``
    ``[2, 边数]`` 长整型张量。消息从第一行索引流向第二行索引；无向键应由
    数据层展开为两条有向边。
``graph_index``
    ``[节点数]`` 长整型张量，给出每个节点所属的图。
``edge_features``
    可选的 ``[边数, edge_dim]`` 浮点张量，例如键类型、共轭、芳香性和环
    信息。模型配置 ``edge_dim > 0`` 且省略该输入时使用全零边特征。
``graph_count``
    可选的图数量；省略时由 ``graph_index`` 推断。

本模块不负责读取数据、拆分数据或提供训练命令行，避免把发布数据治理逻辑
耦合进模型实现。
"""

from __future__ import annotations

import importlib
import random
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Sequence

import numpy as np


DEFAULT_TASKS = (
    "Rg",
    "density",
    "bulk_modulus",
    "thermal_conductivity",
)


@lru_cache(maxsize=1)
def require_torch() -> Any:
    """延迟导入 PyTorch，并在缺失时给出可执行的错误信息。"""

    try:
        return importlib.import_module("torch")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "GNN 运行需要 PyTorch；请在专用训练环境安装与 CUDA/CPU 匹配的 "
            "PyTorch 后再创建模型或计算损失。"
        ) from exc


def set_random_seed(seed: int, *, use_torch: bool = True) -> None:
    """固定 Python、NumPy 以及可选 PyTorch 的随机种子。"""

    if isinstance(seed, bool) or not isinstance(seed, (int, np.integer)):
        raise TypeError("seed 必须是整数")
    if seed < 0:
        raise ValueError("seed 必须为非负整数")
    value = int(seed)
    random.seed(value)
    np.random.seed(value)
    if not use_torch:
        return
    torch = require_torch()
    torch.manual_seed(value)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(value)
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False


def _as_2d_float(values: Any, name: str) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 2:
        raise ValueError(f"{name} 必须是二维数组")
    return array


def _broadcast_weights(weights: Any, shape: tuple[int, int]) -> np.ndarray:
    array = np.asarray(weights, dtype=np.float64)
    if array.ndim == 1:
        if len(array) != shape[0]:
            raise ValueError("一维 weights 的长度必须等于样本数")
        array = np.broadcast_to(array[:, None], shape)
    elif array.shape != shape:
        raise ValueError("二维 weights 必须与 targets 同形")
    if not np.isfinite(array).all():
        raise ValueError("weights 必须全部为有限数")
    if (array < 0).any():
        raise ValueError("weights 不得为负")
    return np.asarray(array, dtype=np.float64)


@dataclass(frozen=True)
class TargetStandardizer:
    """仅由训练折拟合的逐任务加权标准化参数。"""

    mean: np.ndarray
    scale: np.ndarray
    task_names: tuple[str, ...]

    def __post_init__(self) -> None:
        mean = np.asarray(self.mean, dtype=np.float64)
        scale = np.asarray(self.scale, dtype=np.float64)
        if mean.ndim != 1 or scale.shape != mean.shape:
            raise ValueError("mean 和 scale 必须是同形一维数组")
        if len(self.task_names) != len(mean):
            raise ValueError("task_names 数量与标准化参数不一致")
        if not np.isfinite(mean).all() or not np.isfinite(scale).all():
            raise ValueError("标准化参数必须全部为有限数")
        if (scale <= 0).any():
            raise ValueError("scale 必须全部为正")
        object.__setattr__(self, "mean", mean.copy())
        object.__setattr__(self, "scale", scale.copy())

    def transform_numpy(self, targets: Any, mask: Any) -> np.ndarray:
        """标准化观测目标，并把缺失位置稳定填为零。"""

        values = _as_2d_float(targets, "targets")
        observed = np.asarray(mask, dtype=bool)
        if observed.shape != values.shape or values.shape[1] != len(self.mean):
            raise ValueError("targets/mask 形状与标准化任务数不一致")
        if not np.isfinite(values[observed]).all():
            raise ValueError("mask=True 的 targets 必须为有限数")
        standardized = (values - self.mean[None, :]) / self.scale[None, :]
        return np.where(observed, standardized, 0.0)

    def inverse_numpy(self, standardized: Any) -> np.ndarray:
        """把二维标准化预测还原到各任务原始单位。"""

        values = _as_2d_float(standardized, "standardized")
        if values.shape[1] != len(self.mean):
            raise ValueError("预测任务数与标准化参数不一致")
        if not np.isfinite(values).all():
            raise ValueError("standardized 必须全部为有限数")
        return values * self.scale[None, :] + self.mean[None, :]

    def transform_tensor(self, targets: Any, mask: Any) -> Any:
        """在目标张量设备上标准化，缺失位置填零且不传播 NaN。"""

        torch = require_torch()
        if targets.ndim != 2 or mask.shape != targets.shape:
            raise ValueError("targets 与 mask 必须是同形二维张量")
        if targets.shape[1] != len(self.mean):
            raise ValueError("目标任务数与标准化参数不一致")
        observed = mask.to(dtype=torch.bool)
        if not torch.isfinite(targets[observed]).all().item():
            raise ValueError("mask=True 的 targets 必须为有限数")
        mean = torch.as_tensor(self.mean, dtype=targets.dtype, device=targets.device)
        scale = torch.as_tensor(self.scale, dtype=targets.dtype, device=targets.device)
        standardized = (targets - mean) / scale
        return torch.where(observed, standardized, torch.zeros_like(standardized))

    def inverse_tensor(self, standardized: Any) -> Any:
        """在预测张量设备上还原目标单位。"""

        torch = require_torch()
        if standardized.ndim != 2 or standardized.shape[1] != len(self.mean):
            raise ValueError("预测必须是任务数匹配的二维张量")
        mean = torch.as_tensor(
            self.mean, dtype=standardized.dtype, device=standardized.device
        )
        scale = torch.as_tensor(
            self.scale, dtype=standardized.dtype, device=standardized.device
        )
        return standardized * scale + mean


def fit_target_standardizer(
    targets: Any,
    mask: Any,
    weights: Any,
    task_names: Sequence[str] = DEFAULT_TASKS,
) -> TargetStandardizer:
    """用训练观测的发布权重拟合逐任务均值和标准差。

    缺失目标不会进入统计。任一任务若没有正权重观测会立即失败，防止训练时
    静默产生无定义的标准化参数。常量任务的尺度固定为 1。
    """

    values = _as_2d_float(targets, "targets")
    observed = np.asarray(mask, dtype=bool)
    if observed.shape != values.shape:
        raise ValueError("mask 必须与 targets 同形")
    names = tuple(str(name) for name in task_names)
    if len(names) != values.shape[1] or any(not name.strip() for name in names):
        raise ValueError("task_names 必须与 targets 列数一致且不得为空")
    if len(set(names)) != len(names):
        raise ValueError("task_names 不得重复")
    sample_weights = _broadcast_weights(weights, values.shape)
    if not np.isfinite(values[observed]).all():
        raise ValueError("mask=True 的 targets 必须为有限数")

    effective = sample_weights * observed
    totals = effective.sum(axis=0)
    missing_tasks = [names[i] for i, total in enumerate(totals) if total <= 0]
    if missing_tasks:
        raise ValueError(f"任务没有正权重训练观测: {missing_tasks}")
    safe_values = np.where(observed, values, 0.0)
    means = (safe_values * effective).sum(axis=0) / totals
    centered = np.where(observed, values - means[None, :], 0.0)
    variances = (centered**2 * effective).sum(axis=0) / totals
    scales = np.sqrt(np.maximum(variances, 0.0))
    scales[scales <= np.finfo(np.float64).eps] = 1.0
    return TargetStandardizer(means, scales, names)


@dataclass(frozen=True)
class GraphBatchLayout:
    """经过验证的批图形状摘要。"""

    node_count: int
    edge_count: int
    graph_count: int
    feature_count: int
    edge_feature_count: int


def validate_graph_batch(
    node_features: Any,
    edge_index: Any,
    graph_index: Any,
    edge_features: Any | None = None,
    graph_count: int | None = None,
) -> GraphBatchLayout:
    """使用 NumPy 语义验证批图布局，不依赖 PyTorch。"""

    nodes = np.asarray(node_features)
    edges = np.asarray(edge_index)
    membership = np.asarray(graph_index)
    if nodes.ndim != 2 or nodes.shape[0] == 0 or nodes.shape[1] == 0:
        raise ValueError("node_features 必须是非空二维矩阵")
    if not np.issubdtype(nodes.dtype, np.number) or not np.isfinite(nodes).all():
        raise ValueError("node_features 必须全部为有限数")
    if edges.ndim != 2 or edges.shape[0] != 2:
        raise ValueError("edge_index 必须具有形状 [2, 边数]")
    if membership.ndim != 1 or len(membership) != len(nodes):
        raise ValueError("graph_index 必须是一维且与节点数一致")
    if not np.issubdtype(edges.dtype, np.integer):
        raise ValueError("edge_index 必须为整数索引")
    if not np.issubdtype(membership.dtype, np.integer):
        raise ValueError("graph_index 必须为整数索引")
    if edges.size and ((edges < 0).any() or (edges >= len(nodes)).any()):
        raise ValueError("edge_index 含越界节点")
    if edge_features is None:
        edge_feature_count = 0
    else:
        edge_attributes = np.asarray(edge_features)
        if edge_attributes.ndim != 2 or edge_attributes.shape[0] != edges.shape[1]:
            raise ValueError("edge_features 必须具有形状 [边数, edge_dim]")
        if edge_attributes.shape[1] == 0:
            raise ValueError("显式 edge_features 的 edge_dim 必须为正")
        if not np.issubdtype(edge_attributes.dtype, np.number) or not np.isfinite(
            edge_attributes
        ).all():
            raise ValueError("edge_features 必须全部为有限数")
        edge_feature_count = edge_attributes.shape[1]
    if (membership < 0).any():
        raise ValueError("graph_index 不得为负")
    inferred = int(membership.max()) + 1
    expected_ids = np.arange(inferred)
    if not np.array_equal(np.unique(membership), expected_ids):
        raise ValueError("graph_index 必须从0开始连续且每个图至少有一个节点")
    if graph_count is None:
        count = inferred
    else:
        if isinstance(graph_count, bool) or not isinstance(
            graph_count, (int, np.integer)
        ):
            raise TypeError("graph_count 必须是整数")
        count = int(graph_count)
        if count != inferred:
            raise ValueError("graph_count 与 graph_index 推断结果不一致")
    return GraphBatchLayout(
        len(nodes), edges.shape[1], count, nodes.shape[1], edge_feature_count
    )


def masked_weighted_mse(
    prediction: Any,
    target: Any,
    mask: Any,
    weight: Any,
) -> Any:
    """计算多任务 masked weighted MSE。

    ``weight`` 可以是每样本一维权重或与目标同形的逐任务权重。缺失目标允许
    存放 NaN，因为 ``mask=False`` 的位置会在差值前被清零。若所有有效观测
    权重为零则立即失败，避免返回伪造的零损失。
    """

    torch = require_torch()
    if prediction.ndim != 2 or target.shape != prediction.shape:
        raise ValueError("prediction 和 target 必须是同形二维张量")
    if mask.shape != prediction.shape:
        raise ValueError("mask 必须与 prediction 同形")
    observed = mask.to(dtype=torch.bool)
    if weight.ndim == 1:
        if weight.shape[0] != prediction.shape[0]:
            raise ValueError("一维 weight 的长度必须等于样本数")
        expanded_weight = weight[:, None].expand_as(prediction)
    elif weight.shape == prediction.shape:
        expanded_weight = weight
    else:
        raise ValueError("weight 必须是一维样本权重或与 prediction 同形")
    if not torch.isfinite(prediction).all().item():
        raise ValueError("prediction 必须全部为有限数")
    if not torch.isfinite(target[observed]).all().item():
        raise ValueError("mask=True 的 target 必须为有限数")
    if not torch.isfinite(expanded_weight).all().item():
        raise ValueError("weight 必须全部为有限数")
    if (expanded_weight < 0).any().item():
        raise ValueError("weight 不得为负")
    effective_weight = expanded_weight * observed.to(expanded_weight.dtype)
    total_weight = effective_weight.sum()
    if total_weight.item() <= 0:
        raise ValueError("有效观测的总权重必须为正")
    safe_target = torch.where(observed, target, prediction.detach())
    squared_error = (prediction - safe_target).square()
    return (squared_error * effective_weight).sum() / total_weight


@lru_cache(maxsize=1)
def _model_class() -> type:
    torch = require_torch()
    nn = torch.nn

    class _MessagePassingLayer(nn.Module):
        def __init__(self, hidden_dim: int, edge_dim: int) -> None:
            super().__init__()
            self.edge_dim = edge_dim
            self.self_linear = nn.Linear(hidden_dim, hidden_dim)
            self.message_linear = nn.Linear(
                hidden_dim + edge_dim, hidden_dim, bias=False
            )
            self.normalization = nn.LayerNorm(hidden_dim)

        def forward(
            self, node_state: Any, edge_index: Any, edge_features: Any | None
        ) -> Any:
            source, destination = edge_index[0], edge_index[1]
            aggregate = torch.zeros_like(node_state)
            if source.numel():
                source_state = node_state.index_select(0, source)
                if self.edge_dim:
                    if edge_features is None:
                        edge_features = torch.zeros(
                            (source.shape[0], self.edge_dim),
                            dtype=node_state.dtype,
                            device=node_state.device,
                        )
                    message_input = torch.cat((source_state, edge_features), dim=1)
                else:
                    message_input = source_state
                messages = self.message_linear(message_input)
                aggregate.index_add_(0, destination, messages)
                degree = torch.zeros(
                    node_state.shape[0],
                    dtype=node_state.dtype,
                    device=node_state.device,
                )
                degree.index_add_(
                    0,
                    destination,
                    torch.ones_like(destination, dtype=node_state.dtype),
                )
                aggregate = aggregate / degree.clamp_min(1).unsqueeze(1)
            updated = self.self_linear(node_state) + aggregate
            return torch.relu(self.normalization(updated))

    class _MultiTaskGNN(nn.Module):
        def __init__(
            self,
            input_dim: int,
            hidden_dim: int,
            tasks: Sequence[str],
            message_passing_steps: int,
            dropout: float,
            edge_dim: int,
        ) -> None:
            super().__init__()
            self.task_names = tuple(tasks)
            self.edge_dim = edge_dim
            self.input_projection = nn.Linear(input_dim, hidden_dim)
            self.layers = nn.ModuleList(
                _MessagePassingLayer(hidden_dim, edge_dim)
                for _ in range(message_passing_steps)
            )
            self.heads = nn.ModuleDict(
                {
                    task: nn.Sequential(
                        nn.Linear(hidden_dim * 2, hidden_dim),
                        nn.ReLU(),
                        nn.Dropout(dropout),
                        nn.Linear(hidden_dim, 1),
                    )
                    for task in self.task_names
                }
            )

        def forward(
            self,
            node_features: Any,
            edge_index: Any,
            graph_index: Any,
            edge_features: Any | None = None,
            graph_count: int | None = None,
        ) -> Any:
            if node_features.ndim != 2 or node_features.shape[1] != self.input_projection.in_features:
                raise ValueError("node_features 形状与模型 input_dim 不一致")
            if edge_index.ndim != 2 or edge_index.shape[0] != 2:
                raise ValueError("edge_index 必须具有形状 [2, 边数]")
            if graph_index.ndim != 1 or len(graph_index) != len(node_features):
                raise ValueError("graph_index 必须是一维且与节点数一致")
            if node_features.shape[0] == 0:
                raise ValueError("批图不得为空")
            if edge_index.dtype != torch.long or graph_index.dtype != torch.long:
                raise TypeError("edge_index 和 graph_index 必须为 torch.long")
            if edge_features is not None:
                if (
                    edge_features.ndim != 2
                    or edge_features.shape[0] != edge_index.shape[1]
                    or edge_features.shape[1] != self.edge_dim
                ):
                    raise ValueError(
                        "edge_features 必须具有形状 [边数, 模型 edge_dim]"
                    )
                if not torch.is_floating_point(edge_features):
                    raise TypeError("edge_features 必须为浮点张量")
                if edge_features.device != node_features.device:
                    raise ValueError("edge_features 必须与 node_features 位于同一设备")
            if edge_index.numel() and (
                edge_index.min().item() < 0
                or edge_index.max().item() >= node_features.shape[0]
            ):
                raise ValueError("edge_index 含越界节点")
            if graph_index.min().item() < 0:
                raise ValueError("graph_index 不得为负")
            inferred_count = int(graph_index.max().item()) + 1
            if graph_count is None:
                count = inferred_count
            else:
                count = int(graph_count)
                if count != inferred_count:
                    raise ValueError("graph_count 与 graph_index 不一致")
            present = torch.bincount(graph_index, minlength=count)
            if present.shape[0] != count or (present == 0).any().item():
                raise ValueError("graph_index 必须从0开始连续且每个图非空")

            state = torch.relu(self.input_projection(node_features))
            for layer in self.layers:
                state = layer(state, edge_index, edge_features)

            pooled_sum = torch.zeros(
                (count, state.shape[1]), dtype=state.dtype, device=state.device
            )
            pooled_sum.index_add_(0, graph_index, state)
            counts = torch.zeros(count, dtype=state.dtype, device=state.device)
            counts.index_add_(
                0, graph_index, torch.ones_like(graph_index, dtype=state.dtype)
            )
            pooled_mean = pooled_sum / counts.clamp_min(1).unsqueeze(1)
            graph_state = torch.cat((pooled_mean, pooled_sum), dim=1)
            return torch.cat(
                [self.heads[task](graph_state) for task in self.task_names], dim=1
            )

    return _MultiTaskGNN


def MultiTaskGNN(
    input_dim: int,
    hidden_dim: int = 64,
    tasks: Sequence[str] = DEFAULT_TASKS,
    message_passing_steps: int = 3,
    dropout: float = 0.0,
    edge_dim: int = 0,
) -> Any:
    """创建使用 ``index_add_`` 聚合与均值/和池化的多任务模型。"""

    for value, name in (
        (input_dim, "input_dim"),
        (hidden_dim, "hidden_dim"),
        (message_passing_steps, "message_passing_steps"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} 必须是正整数")
    if isinstance(edge_dim, bool) or not isinstance(edge_dim, int) or edge_dim < 0:
        raise ValueError("edge_dim 必须是非负整数")
    names = tuple(str(task).strip() for task in tasks)
    if not names or any(not task for task in names) or len(set(names)) != len(names):
        raise ValueError("tasks 必须是非空且不重复的名称序列")
    if not np.isfinite(dropout) or not 0 <= dropout < 1:
        raise ValueError("dropout 必须位于 [0, 1)")
    model_type = _model_class()
    return model_type(
        input_dim,
        hidden_dim,
        names,
        message_passing_steps,
        float(dropout),
        edge_dim,
    )


__all__ = [
    "DEFAULT_TASKS",
    "GraphBatchLayout",
    "MultiTaskGNN",
    "TargetStandardizer",
    "fit_target_standardizer",
    "masked_weighted_mse",
    "require_torch",
    "set_random_seed",
    "validate_graph_batch",
]
