from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
CODE = ROOT / "代码"
sys.path.insert(0, str(CODE))
SPEC = importlib.util.spec_from_file_location("train_gnn", CODE / "训练GNN基线.py")
assert SPEC and SPEC.loader
train_gnn = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = train_gnn
SPEC.loader.exec_module(train_gnn)

from GNN数据 import DEFAULT_TARGETS, GraphSample, smiles_to_graph  # noqa: E402


def sample(
    index: int,
    target_name: str,
    split: str,
    usage: str = "primary_train",
    family: str = "family-a",
    group: str | None = None,
) -> GraphSample:
    graph = smiles_to_graph("CCO" if index % 2 else "CC")
    target_index = DEFAULT_TARGETS.index(target_name)
    targets = np.full(4, np.nan, dtype=np.float32)
    mask = np.zeros(4, dtype=bool)
    weights = np.zeros(4, dtype=np.float32)
    targets[target_index] = float(index + target_index + 1)
    mask[target_index] = True
    weights[target_index] = 0.5 + index / 10
    return GraphSample(
        observation_id=f"obs-{index}-{target_name}",
        source_id=f"source-{family}",
        source_family_id=family,
        leakage_group=group or f"group-{index}",
        development_split=split,
        usage_mode=usage,
        canonical_structure=graph.canonical_smiles,
        target_name=target_name,
        target_unit={
            "Rg": "angstrom",
            "density": "g/cm^3",
            "bulk_modulus": "Pa",
            "thermal_conductivity": "W/(m*K)",
        }[target_name],
        recommended_loss_weight=float(weights[target_index]),
        target_names=tuple(DEFAULT_TARGETS),
        node_features=graph.node_features,
        edge_index=graph.edge_index,
        edge_features=graph.edge_features,
        targets=targets,
        target_mask=mask,
        target_weights=weights,
    )


def config(maximum_epochs: int = 3) -> dict:
    return {
        "release": {"id": "fixture", "data": "fixture.csv"},
        "task": {
            "task_id": "计算_结构多任务预训练",
            "targets": [{"name": name, "unit": "fixture"} for name in DEFAULT_TARGETS],
            "training_modes": ["primary_only", "primary_plus_aux"],
        },
        "model": {
            "hidden_dim": 8,
            "message_passing_steps": 1,
            "dropout": 0.0,
            "use_edge_features": True,
        },
        "training": {
            "random_seed": 7,
            "batch_size": 4,
            "learning_rate": 0.01,
            "weight_decay": 0.0,
            "maximum_epochs": maximum_epochs,
            "early_stopping_patience": 2,
            "minimum_improvement": 1e-8,
        },
        "source_family_holdout": {
            "enabled": False,
            "minimum_groups": {"train": 2, "validation": 1, "test": 1},
        },
        "output": {"csv_float_format": "%.8g"},
    }


def fixture_samples() -> list[GraphSample]:
    rows = []
    index = 0
    for split in ("train", "validation", "test"):
        for target in DEFAULT_TARGETS:
            rows.append(sample(index, target, split))
            index += 1
    rows.append(sample(index, "Rg", "train", usage="auxiliary_train", family="family-b"))
    return rows


def test_load_config_accepts_formal_targets_and_rejects_tg(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config(), allow_unicode=True), encoding="utf-8")
    loaded = train_gnn.load_config(path)
    assert loaded["training"]["random_seed"] == 7

    broken = config()
    broken["task"]["targets"][-1]["name"] = "Tg"
    path.write_text(yaml.safe_dump(broken, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="不得包含 Tg"):
        train_gnn.load_config(path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda c: c.pop("model"), "缺少分区"),
        (lambda c: c["task"].update(training_modes=["primary_only"]), "training_modes"),
        (lambda c: c["training"].update(batch_size=0), "batch_size"),
        (lambda c: c["training"].update(random_seed=-1), "random_seed"),
        (lambda c: c["training"].update(learning_rate=0), "learning_rate"),
        (lambda c: c["training"].update(weight_decay=-1), "weight_decay"),
        (lambda c: c["model"].update(hidden_dim=0), "hidden_dim"),
        (lambda c: c["model"].update(dropout=1), "dropout"),
    ],
)
def test_load_config_rejects_invalid_values(tmp_path: Path, mutation, message):
    value = config()
    mutation(value)
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        train_gnn.load_config(path)


def test_mode_selection_keeps_evaluation_primary_and_adds_aux_only_to_train():
    rows = fixture_samples()
    primary = train_gnn.select_mode_samples(rows, "primary_only")
    plus_aux = train_gnn.select_mode_samples(rows, "primary_plus_aux")
    assert len(plus_aux["train"]) == len(primary["train"]) + 1
    assert primary["validation"] == plus_aux["validation"]
    assert primary["test"] == plus_aux["test"]
    assert {row.usage_mode for row in plus_aux["validation"] + plus_aux["test"]} == {
        "primary_train"
    }
    with pytest.raises(ValueError, match="未知训练模式"):
        train_gnn.select_mode_samples(rows, "anything")
    with pytest.raises(ValueError, match="缺少数据折"):
        train_gnn.select_mode_samples(rows[:4], "primary_only")


def test_minibatches_are_deterministic_and_validate_size():
    rows = fixture_samples()[:7]
    first = [[item.observation_id for item in batch] for batch in train_gnn.iter_minibatches(rows, 3, shuffle=True, seed=5)]
    second = [[item.observation_id for item in batch] for batch in train_gnn.iter_minibatches(rows, 3, shuffle=True, seed=5)]
    assert first == second
    assert [len(batch) for batch in first] == [3, 3, 1]
    with pytest.raises(ValueError, match="必须为正"):
        list(train_gnn.iter_minibatches(rows, 0, shuffle=False, seed=0))


def test_regression_metrics_and_group_macro_are_distinct():
    metrics = train_gnn.regression_metrics([1, 3], [2, 3], [3, 1])
    assert metrics["mae"] == pytest.approx(0.75)
    assert metrics["rmse"] == pytest.approx(np.sqrt(0.75))
    assert metrics["spearman"] == pytest.approx(1.0)
    with pytest.raises(ValueError, match="同形"):
        train_gnn.regression_metrics([1], [1, 2], [1])
    with pytest.raises(ValueError, match="总和为正"):
        train_gnn.regression_metrics([1], [1], [0])

    rows = [sample(0, "Rg", "test", group="same"), sample(1, "Rg", "test", group="same"), sample(2, "Rg", "test", group="other")]
    matrix = np.zeros((3, 4), dtype=float)
    matrix[:, 0] = [1.5, 2.5, 4]
    frame = train_gnn.prediction_frame(rows, matrix, evaluation_id="dev", evaluation_scheme="development_test", training_mode="primary_only")
    summary = train_gnn.metric_rows(frame)
    assert set(summary["aggregation_level"]) == {"row_weighted", "hard_group_macro"}
    assert summary.loc[summary["aggregation_level"].eq("hard_group_macro"), "n_groups"].item() == 2
    with pytest.raises(ValueError, match="形状"):
        train_gnn.prediction_frame(rows, np.zeros((2, 4)), evaluation_id="x", evaluation_scheme="x", training_mode="primary_only")


def test_eligible_source_holdout_excludes_family_and_shared_groups():
    rows = []
    for index, split in enumerate(("train", "train", "validation")):
        rows.append(sample(index, "Rg", split, family="train-family"))
    rows.append(sample(10, "Rg", "test", family="held", group="held-group"))
    rows.append(sample(11, "density", "test", family="held", group="held-group-2"))
    rows.append(sample(12, "Rg", "train", family="train-family", group="held-group"))
    eligible = train_gnn.eligible_source_holdouts(rows, "primary_only", {"train": 2, "validation": 1, "test": 2})
    assert [family for family, _ in eligible] == ["held"]
    train_rows = eligible[0][1]["train"]
    assert all(row.leakage_group != "held-group" for row in train_rows)


def test_sha_and_parser(tmp_path: Path):
    path = tmp_path / "value.txt"
    path.write_text("abc", encoding="ascii")
    assert train_gnn.sha256_file(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    parsed = train_gnn.build_parser().parse_args(["--设备", "cpu"])
    assert parsed.设备 == "cpu"


def test_run_training_writes_auditable_outputs_and_predicts_each_test_once(tmp_path: Path, monkeypatch):
    data_path = tmp_path / "data.csv.gz"
    data_path.write_bytes(b"fixture")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config(), allow_unicode=True), encoding="utf-8")
    rows = fixture_samples()
    calls = []

    monkeypatch.setattr(train_gnn, "resolve_device", lambda requested: "cpu")
    monkeypatch.setattr(train_gnn, "set_random_seed", lambda seed: None)
    monkeypatch.setattr(train_gnn, "load_computational_observations", lambda *args, **kwargs: (pd.DataFrame(), tuple(DEFAULT_TARGETS)))
    monkeypatch.setattr(train_gnn, "build_graph_dataset", lambda *args, **kwargs: rows)
    monkeypatch.setattr(train_gnn, "_environment_manifest", lambda device: {"device": device})

    def fake_train(train_rows, validation_rows, cfg, *, mode, device, checkpoint_path):
        checkpoint = Path(checkpoint_path)
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_bytes(mode.encode())
        return train_gnn.TrainingResult(None, None, 2, 0.1, 3, checkpoint)

    def fake_predict(result, test_rows, *, batch_size, device):
        calls.append([row.observation_id for row in test_rows])
        matrix = np.zeros((len(test_rows), 4), dtype=float)
        for row_index, row in enumerate(test_rows):
            task_index = DEFAULT_TARGETS.index(row.target_name)
            matrix[row_index, task_index] = row.targets[task_index]
        return matrix

    monkeypatch.setattr(train_gnn, "train_model", fake_train)
    monkeypatch.setattr(train_gnn, "predict_samples", fake_predict)
    output = tmp_path / "output"
    manifest = train_gnn.run_training(data_path, config_path, output, requested_device="auto")
    assert len(calls) == 2
    assert manifest["status"] == "completed"
    assert (output / "指标.csv").is_file()
    assert (output / "逐样本预测.csv.gz").is_file()
    stored = json.loads((output / "运行清单.json").read_text(encoding="utf-8"))
    assert stored["inputs"]["data"]["sha256"] == train_gnn.sha256_file(data_path)
    assert set(stored["checkpoints"]) == {"primary_only", "primary_plus_aux"}


@pytest.mark.skipif(not train_gnn.__dict__.get("require_torch"), reason="PyTorch API unavailable")
def test_cpu_training_uses_validation_checkpoint_and_predicts(tmp_path: Path):
    try:
        torch = train_gnn.require_torch()
    except RuntimeError:
        pytest.skip("PyTorch 未安装")
    rows = fixture_samples()
    split = train_gnn.select_mode_samples(rows, "primary_only")
    checkpoint = tmp_path / "model.pt"
    result = train_gnn.train_model(split["train"], split["validation"], config(2), mode="primary_only", device="cpu", checkpoint_path=checkpoint)
    prediction = train_gnn.predict_samples(result, split["test"], batch_size=2, device="cpu")
    assert checkpoint.is_file()
    assert result.best_epoch <= result.epochs_ran <= 2
    assert prediction.shape == (4, 4)
    assert torch.isfinite(torch.from_numpy(prediction)).all()
