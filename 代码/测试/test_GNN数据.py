import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

import GNN数据 as graph_data


def _frame() -> pd.DataFrame:
    rows = []
    properties = [
        ("Rg", 12.5, "angstrom"),
        ("density", 1.08, "g/cm^3"),
        ("bulk_modulus", 1.7e9, "Pa"),
        ("thermal_conductivity", 0.21, "W/(m*K)"),
    ]
    for index, (name, value, unit) in enumerate(properties):
        rows.append(
            {
                "task_id": "计算_结构多任务预训练",
                "usage_mode": "primary_train" if index < 2 else "auxiliary_train",
                "model_ready": True,
                "source_id": "source-a",
                "source_family_id": "family-a",
                "observation_id": f"obs-{index}",
                "canonical_structure": "CCO",
                "property_name": name,
                "value": value,
                "unit": unit,
                "leakage_group": "group-a",
                "development_split": "train",
                "recommended_loss_weight": 0.25 + index * 0.1,
            }
        )
    rows.append(
        {
            **rows[0],
            "observation_id": "obs-tg",
            "property_name": "Tg",
            "value": 65.0,
            "unit": "degC",
        }
    )
    return pd.DataFrame(rows)


def test_default_targets_exclude_exploratory_tg_and_preserve_release_fields():
    samples = graph_data.build_graph_dataset(_frame())

    assert len(samples) == 4
    assert graph_data.DEFAULT_TARGETS == (
        "Rg",
        "density",
        "bulk_modulus",
        "thermal_conductivity",
    )
    assert {sample.target_name for sample in samples} == set(graph_data.DEFAULT_TARGETS)
    assert samples[0].leakage_group == "group-a"
    assert samples[0].development_split == "train"
    assert samples[0].usage_mode in {"primary_train", "auxiliary_train"}


def test_rdkit_graph_has_directed_edges_and_discrete_features():
    graph = graph_data.smiles_to_graph("CCO")

    assert graph.node_features.shape == (3, len(graph_data.NODE_FEATURE_NAMES))
    assert graph.edge_index.shape == (2, 4)
    assert graph.edge_features.shape == (4, len(graph_data.EDGE_FEATURE_NAMES))
    assert np.issubdtype(graph.node_features.dtype, np.integer)
    assert np.issubdtype(graph.edge_features.dtype, np.integer)
    assert graph.edge_index.dtype == np.int64
    assert np.array_equal(graph.edge_index[:, 0], np.array([0, 1]))
    assert np.array_equal(graph.edge_index[:, 1], np.array([1, 0]))
    assert graph.to_serializable()["canonical_smiles"] == "CCO"

    atom = graph_data.smiles_to_graph("[He]")
    assert atom.edge_index.shape == (2, 0)
    assert atom.edge_features.shape == (0, len(graph_data.EDGE_FEATURE_NAMES))

    with pytest.raises(ValueError, match="不能为空"):
        graph_data.smiles_to_graph("")


def test_graph_dataset_is_deterministic_under_input_row_order():
    left = graph_data.build_graph_dataset(_frame())
    right = graph_data.build_graph_dataset(
        _frame().sample(frac=1.0, random_state=7).reset_index(drop=True)
    )

    assert [sample.observation_id for sample in left] == [
        sample.observation_id for sample in right
    ]
    for first, second in zip(left, right, strict=True):
        assert np.array_equal(first.node_features, second.node_features)
        assert np.array_equal(first.edge_index, second.edge_index)
        assert np.array_equal(first.targets, second.targets, equal_nan=True)
        assert np.array_equal(first.target_mask, second.target_mask)
        assert np.array_equal(first.target_weights, second.target_weights)


def test_collate_builds_serializable_multitask_batch_with_masks_and_weights():
    samples = graph_data.build_graph_dataset(_frame())
    batch = graph_data.collate_graphs(samples)

    assert batch["graph_ptr"].tolist() == [0, 3, 6, 9, 12]
    assert batch["node_features"].shape[0] == 12
    assert batch["edge_index"].shape == (2, 16)
    assert batch["graph_index"].shape == (12,)
    assert batch["edge_features"].shape[1] == 4
    assert batch["targets"].shape == (4, 4)
    assert batch["target_mask"].sum(axis=1).tolist() == [1, 1, 1, 1]
    assert np.all(batch["target_weights"][~batch["target_mask"]] == 0)
    for row, sample in enumerate(samples):
        target_index = graph_data.DEFAULT_TARGETS.index(sample.target_name)
        assert batch["target_mask"][row, target_index]
        assert batch["target_weights"][row, target_index] == pytest.approx(
            sample.recommended_loss_weight
        )
    json.dumps(graph_data.batch_to_serializable(batch), ensure_ascii=False)
    json.dumps(samples[0].to_serializable(), ensure_ascii=False)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda frame: frame.assign(
                development_split=["train", "validation", "train", "train", "train"]
            ),
            "硬组跨越多个数据折",
        ),
        (
            lambda frame: pd.concat([frame, frame.iloc[[0]]], ignore_index=True),
            "重复观测目标",
        ),
        (
            lambda frame: frame.assign(
                recommended_loss_weight=[np.nan, 0.35, 0.45, 0.55, 0.25]
            ),
            "recommended_loss_weight",
        ),
        (
            lambda frame: frame.assign(
                canonical_structure=["not-a-smiles"] * len(frame)
            ),
            "无法解析",
        ),
    ],
)
def test_strict_gates_fail_closed(mutator, message):
    with pytest.raises(ValueError, match=message):
        graph_data.build_graph_dataset(mutator(_frame()))


def test_target_unit_normalization_and_mismatch_gate():
    frame = _frame().iloc[[1]].copy()
    frame.loc[:, "value"] = 1080.0
    frame.loc[:, "unit"] = "kg/m^3"
    sample = graph_data.build_graph_dataset(frame)[0]
    assert sample.targets[1] == pytest.approx(1.08)

    frame.loc[:, "unit"] = "MPa"
    with pytest.raises(ValueError, match="目标单位不受支持"):
        graph_data.build_graph_dataset(frame)


def test_tg_requires_explicit_exploratory_opt_in():
    frame = _frame().loc[lambda value: value["property_name"].eq("Tg")]
    with pytest.raises(ValueError, match="Tg.*探索"):
        graph_data.build_graph_dataset(frame, targets=("Tg",))

    sample = graph_data.build_graph_dataset(
        frame, targets=("Tg",), allow_exploratory_tg=True
    )[0]
    assert sample.target_names == ("Tg",)
    assert sample.target_mask.tolist() == [True]


def test_configuration_and_schema_gates_are_explicit(tmp_path):
    frame = _frame()
    with pytest.raises(ValueError, match="缺少必需字段"):
        graph_data.build_graph_dataset(frame.drop(columns="source_id"))
    with pytest.raises(ValueError, match="targets 不能为空"):
        graph_data.build_graph_dataset(frame, targets=())
    with pytest.raises(ValueError, match="targets 不能重复"):
        graph_data.build_graph_dataset(frame, targets=("Rg", "Rg"))
    with pytest.raises(ValueError, match="不支持的计算目标"):
        graph_data.build_graph_dataset(frame, targets=("yield_strength",))
    with pytest.raises(ValueError, match="usage_modes"):
        graph_data.build_graph_dataset(frame, usage_modes=("reference_only",))
    with pytest.raises(ValueError, match="没有满足"):
        graph_data.build_graph_dataset(frame.assign(model_ready=False))
    with pytest.raises(ValueError, match="不能为空"):
        graph_data.build_graph_dataset(frame.assign(source_id=""))

    path = tmp_path / "observations.csv.gz"
    frame.to_csv(path, index=False, compression="gzip")
    loaded, names = graph_data.load_computational_observations(path)
    assert len(loaded) == 4
    assert names == graph_data.DEFAULT_TARGETS
    with pytest.raises(FileNotFoundError):
        graph_data.load_computational_observations(tmp_path / "missing.csv")


def test_string_boolean_units_and_additional_identity_gates():
    frame = _frame()
    frame["model_ready"] = ["true", "1", "TRUE", "1", "false"]
    prepared, _ = graph_data.prepare_observations(frame)
    assert len(prepared) == 4

    frame.loc[0, "model_ready"] = "maybe"
    with pytest.raises(ValueError, match="明确布尔值"):
        graph_data.prepare_observations(frame)

    reused = _frame()
    reused.loc[1, "observation_id"] = reused.loc[0, "observation_id"]
    with pytest.raises(ValueError, match="多个目标复用"):
        graph_data.prepare_observations(reused)

    mixed = _frame()
    mixed.loc[1, "canonical_structure"] = "CCN"
    with pytest.raises(ValueError, match="对应多个结构"):
        graph_data.prepare_observations(mixed)

    invalid_split = _frame().assign(development_split="holdout")
    with pytest.raises(ValueError, match="非法值"):
        graph_data.prepare_observations(invalid_split)


def test_all_supported_unit_conversions_and_value_gates():
    assert graph_data.normalize_target_value("Rg", 1.2, "nm") == (12.0, "angstrom")
    assert graph_data.normalize_target_value("bulk_modulus", 1.5, "GPa") == (
        1.5e9,
        "Pa",
    )
    assert graph_data.normalize_target_value(
        "thermal_conductivity", 0.2, "W/m/K"
    ) == (0.2, "W/(m*K)")
    value, unit = graph_data.normalize_target_value("Tg", 300.0, "K")
    assert value == pytest.approx(26.85)
    assert unit == "degC"
    with pytest.raises(ValueError, match="不是数值"):
        graph_data.normalize_target_value("Rg", "bad", "angstrom")
    with pytest.raises(ValueError, match="有限数"):
        graph_data.normalize_target_value("Rg", np.inf, "angstrom")
    with pytest.raises(ValueError, match="不支持的计算目标"):
        graph_data.normalize_target_value("unknown", 1.0, "1")


def test_metadata_and_batch_target_name_gate():
    samples = graph_data.build_graph_dataset(_frame())
    metadata = graph_data.sample_metadata_frame(samples)
    assert metadata["observation_id"].tolist() == ["obs-0", "obs-1", "obs-2", "obs-3"]
    assert metadata.groupby("leakage_group")["development_split"].nunique().max() == 1

    incompatible = replace(samples[1], target_names=("density",))
    with pytest.raises(ValueError, match="target_names"):
        graph_data.collate_graphs([samples[0], incompatible])


def test_empty_and_optional_torch_collate_are_explicit():
    with pytest.raises(ValueError, match="samples 不能为空"):
        graph_data.collate_graphs([])

    if graph_data.torch_available():
        batch = graph_data.collate_graphs(graph_data.build_graph_dataset(_frame()), as_torch=True)
        import torch

        assert isinstance(batch["node_features"], torch.Tensor)
        assert batch["node_features"].dtype == torch.float32
        assert batch["edge_features"].dtype == torch.float32
        assert batch["edge_index"].dtype == torch.long
        assert batch["graph_index"].dtype == torch.long
        assert batch["observation_id"] == ["obs-0", "obs-1", "obs-2", "obs-3"]
    else:
        with pytest.raises(RuntimeError, match="PyTorch"):
            graph_data.collate_graphs(
                graph_data.build_graph_dataset(_frame()), as_torch=True
            )
