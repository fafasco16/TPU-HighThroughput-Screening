from __future__ import annotations

import importlib.util

import numpy as np
import pytest

from GNN模型 import (
    DEFAULT_TASKS,
    MultiTaskGNN,
    TargetStandardizer,
    fit_target_standardizer,
    masked_weighted_mse,
    require_torch,
    set_random_seed,
    validate_graph_batch,
)


TORCH_AVAILABLE = importlib.util.find_spec("torch") is not None


def test_default_tasks_are_fixed_publication_targets():
    assert DEFAULT_TASKS == (
        "Rg",
        "density",
        "bulk_modulus",
        "thermal_conductivity",
    )


def test_validate_graph_batch_accepts_disconnected_atoms_and_empty_edge_list():
    layout = validate_graph_batch(
        np.array([[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]]),
        np.empty((2, 0), dtype=np.int64),
        np.array([0, 0, 1], dtype=np.int64),
    )
    assert layout.node_count == 3
    assert layout.edge_count == 0
    assert layout.graph_count == 2
    assert layout.feature_count == 2
    assert layout.edge_feature_count == 0


def test_validate_graph_batch_accepts_and_describes_edge_features():
    layout = validate_graph_batch(
        [[1.0], [2.0]],
        np.array([[0, 1], [1, 0]], dtype=np.int64),
        np.array([0, 0], dtype=np.int64),
        np.array([[1.0, 0.0], [1.0, 0.0]]),
    )
    assert layout.edge_count == 2
    assert layout.edge_feature_count == 2


@pytest.mark.parametrize(
    ("nodes", "edges", "membership", "count", "message"),
    [
        ([[1.0]], [[0]], [0], None, "edge_index"),
        ([[1.0]], np.empty((2, 0), dtype=int), [0, 1], None, "graph_index"),
        ([[1.0], [2.0]], [[0], [2]], [0, 1], None, "越界"),
        ([[1.0], [2.0]], np.empty((2, 0), dtype=int), [0, 2], None, "连续"),
        ([[1.0]], np.empty((2, 0), dtype=int), [0], 2, "不一致"),
    ],
)
def test_validate_graph_batch_rejects_invalid_layout(
    nodes, edges, membership, count, message
):
    with pytest.raises(ValueError, match=message):
        validate_graph_batch(nodes, edges, membership, graph_count=count)


def test_validate_graph_batch_rejects_noninteger_indices_and_bad_count_type():
    with pytest.raises(ValueError, match="整数"):
        validate_graph_batch([[1.0]], np.empty((2, 0)), [0])
    with pytest.raises(TypeError, match="整数"):
        validate_graph_batch(
            [[1.0]],
            np.empty((2, 0), dtype=int),
            np.array([0]),
            graph_count=True,
        )


def test_validate_graph_batch_rejects_bad_edge_features():
    nodes = [[1.0], [2.0]]
    edges = np.array([[0, 1], [1, 0]], dtype=np.int64)
    membership = np.array([0, 0], dtype=np.int64)
    with pytest.raises(ValueError, match="形状"):
        validate_graph_batch(nodes, edges, membership, [[1.0]])
    with pytest.raises(ValueError, match="有限数"):
        validate_graph_batch(
            nodes, edges, membership, [[1.0, np.nan], [1.0, 0.0]]
        )
    with pytest.raises(ValueError, match="必须为正"):
        validate_graph_batch(nodes, edges, membership, np.empty((2, 0)))


def test_fit_target_standardizer_uses_only_observed_weighted_training_values():
    targets = np.array(
        [
            [1.0, 10.0],
            [3.0, np.nan],
            [100.0, 14.0],
        ]
    )
    mask = np.array([[True, True], [True, False], [False, True]])
    standardizer = fit_target_standardizer(
        targets, mask, np.array([1.0, 3.0, 7.0]), ("a", "b")
    )
    assert standardizer.mean == pytest.approx([2.5, 13.5])
    assert standardizer.scale == pytest.approx([np.sqrt(0.75), np.sqrt(1.75)])
    transformed = standardizer.transform_numpy(targets, mask)
    assert transformed[1, 1] == 0.0
    assert transformed[2, 0] == 0.0
    restored = standardizer.inverse_numpy(transformed)
    assert restored[mask] == pytest.approx(targets[mask])


def test_fit_target_standardizer_supports_task_weights_and_constant_target():
    fitted = fit_target_standardizer(
        [[2.0, 4.0], [2.0, 8.0]],
        [[True, True], [True, True]],
        [[1.0, 0.0], [2.0, 3.0]],
        ("constant", "variable"),
    )
    assert fitted.mean == pytest.approx([2.0, 8.0])
    assert fitted.scale == pytest.approx([1.0, 1.0])


def test_fit_target_standardizer_has_zero_weight_and_input_gates():
    with pytest.raises(ValueError, match="没有正权重"):
        fit_target_standardizer([[1.0, 2.0]], [[True, False]], [1.0], ("a", "b"))
    with pytest.raises(ValueError, match="不得为负"):
        fit_target_standardizer([[1.0]], [[True]], [-1.0], ("a",))
    with pytest.raises(ValueError, match="有限数"):
        fit_target_standardizer([[np.nan]], [[True]], [1.0], ("a",))
    with pytest.raises(ValueError, match="不得重复"):
        fit_target_standardizer([[1.0, 2.0]], [[True, True]], [1.0], ("a", "a"))


def test_target_standardizer_validates_parameters_and_prediction_shape():
    with pytest.raises(ValueError, match="同形"):
        TargetStandardizer(np.array([0.0]), np.array([1.0, 2.0]), ("a",))
    with pytest.raises(ValueError, match="全部为正"):
        TargetStandardizer(np.array([0.0]), np.array([0.0]), ("a",))
    fitted = TargetStandardizer(np.array([0.0]), np.array([2.0]), ("a",))
    with pytest.raises(ValueError, match="任务数"):
        fitted.inverse_numpy([[1.0, 2.0]])
    with pytest.raises(ValueError, match="有限数"):
        fitted.inverse_numpy([[np.nan]])


def test_random_seed_controls_python_and_numpy_without_torch():
    set_random_seed(17, use_torch=False)
    first = np.random.random(4)
    set_random_seed(17, use_torch=False)
    assert np.array_equal(first, np.random.random(4))
    with pytest.raises(TypeError):
        set_random_seed(True, use_torch=False)
    with pytest.raises(ValueError):
        set_random_seed(-1, use_torch=False)


@pytest.mark.skipif(TORCH_AVAILABLE, reason="仅验证缺少 PyTorch 时的错误路径")
def test_missing_torch_has_actionable_error():
    require_torch.cache_clear()
    with pytest.raises(RuntimeError, match="需要 PyTorch"):
        require_torch()


@pytest.mark.skipif(not TORCH_AVAILABLE, reason="本机没有 PyTorch")
def test_masked_weighted_loss_ignores_missing_targets_and_zero_weights():
    torch = require_torch()
    prediction = torch.tensor([[1.0, 2.0], [4.0, 8.0]])
    target = torch.tensor([[0.0, float("nan")], [2.0, 5.0]])
    mask = torch.tensor([[True, False], [True, True]])
    weight = torch.tensor([[1.0, 9.0], [2.0, 0.0]])
    loss = masked_weighted_mse(prediction, target, mask, weight)
    assert loss.item() == pytest.approx((1.0 + 2.0 * 4.0) / 3.0)


@pytest.mark.skipif(not TORCH_AVAILABLE, reason="本机没有 PyTorch")
def test_masked_weighted_loss_rejects_zero_effective_weight():
    torch = require_torch()
    value = torch.zeros((2, 4))
    with pytest.raises(ValueError, match="总权重"):
        masked_weighted_mse(value, value, torch.ones_like(value, dtype=torch.bool), torch.zeros(2))


def _tiny_batch(torch):
    node_features = torch.tensor(
        [[1.0, 0.0], [0.6, 0.0], [0.0, 1.0], [0.0, 0.6]]
    )
    edge_index = torch.tensor([[0, 1, 2, 3], [1, 0, 3, 2]], dtype=torch.long)
    graph_index = torch.tensor([0, 0, 1, 1], dtype=torch.long)
    return node_features, edge_index, graph_index


@pytest.mark.skipif(not TORCH_AVAILABLE, reason="本机没有 PyTorch")
def test_model_has_independent_heads_and_expected_batch_shape():
    torch = require_torch()
    set_random_seed(23)
    model = MultiTaskGNN(2, hidden_dim=8, message_passing_steps=2)
    prediction = model(*_tiny_batch(torch))
    assert prediction.shape == (2, 4)
    head_parameters = [next(model.heads[name].parameters()) for name in DEFAULT_TASKS]
    assert len({parameter.data_ptr() for parameter in head_parameters}) == 4
    prediction.sum().backward()
    assert all(parameter.grad is not None for parameter in head_parameters)


@pytest.mark.skipif(not TORCH_AVAILABLE, reason="本机没有 PyTorch")
def test_edge_features_enter_messages_and_none_uses_zero_compatibility():
    torch = require_torch()
    set_random_seed(29)
    model = MultiTaskGNN(
        2, hidden_dim=8, message_passing_steps=2, edge_dim=3
    ).eval()
    batch = _tiny_batch(torch)
    zero_edges = torch.zeros((batch[1].shape[1], 3))
    aromatic_edges = torch.tensor(
        [[1.0, 0.0, 1.0], [1.0, 0.0, 1.0], [0.0, 1.0, 0.0], [0.0, 1.0, 0.0]]
    )
    with torch.no_grad():
        omitted = model(*batch)
        explicit_zero = model(*batch, zero_edges)
        with_attributes = model(*batch, aromatic_edges)
    assert torch.allclose(omitted, explicit_zero)
    assert not torch.allclose(explicit_zero, with_attributes)


@pytest.mark.skipif(not TORCH_AVAILABLE, reason="本机没有 PyTorch")
def test_cpu_gpu_forward_consistency_when_cuda_is_available():
    torch = require_torch()
    if not torch.cuda.is_available():
        pytest.skip("没有 CUDA 设备")
    set_random_seed(31)
    cpu_model = MultiTaskGNN(2, hidden_dim=8, message_passing_steps=2).eval()
    gpu_model = MultiTaskGNN(2, hidden_dim=8, message_passing_steps=2).cuda().eval()
    gpu_model.load_state_dict(cpu_model.state_dict())
    batch = _tiny_batch(torch)
    with torch.no_grad():
        cpu_prediction = cpu_model(*batch)
        gpu_prediction = gpu_model(*(item.cuda() for item in batch)).cpu()
    assert torch.allclose(cpu_prediction, gpu_prediction, rtol=2e-4, atol=2e-5)


@pytest.mark.skipif(not TORCH_AVAILABLE, reason="本机没有 PyTorch")
def test_single_batch_can_overfit_fixture():
    torch = require_torch()
    set_random_seed(41)
    model = MultiTaskGNN(2, hidden_dim=16, message_passing_steps=2)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.03)
    batch = _tiny_batch(torch)
    target = torch.tensor(
        [[-1.0, 0.5, 1.0, 2.0], [1.0, -0.5, -1.0, -2.0]]
    )
    mask = torch.ones_like(target, dtype=torch.bool)
    weight = torch.ones(2)
    initial = None
    for step in range(350):
        optimizer.zero_grad(set_to_none=True)
        loss = masked_weighted_mse(model(*batch), target, mask, weight)
        if step == 0:
            initial = loss.item()
        loss.backward()
        optimizer.step()
    assert initial is not None and loss.item() < initial * 1e-3
    assert loss.item() < 2e-3
