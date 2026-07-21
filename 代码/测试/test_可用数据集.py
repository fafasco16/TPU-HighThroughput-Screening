import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import 生成可用数据集 as usable


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "可用数据集"
MANIFEST_PATH = OUTPUT / "发布清单.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_split_and_fold_are_deterministic():
    values = ["structure-A", "同一配方", "curve-001"]
    assert [usable.deterministic_split(value) for value in values] == [
        usable.deterministic_split(value) for value in values
    ]
    assert [usable.deterministic_fold(value) for value in values] == [
        usable.deterministic_fold(value) for value in values
    ]
    assert all(
        usable.deterministic_split(value) in {"train", "validation", "test"}
        for value in values
    )
    assert all(0 <= usable.deterministic_fold(value) < 5 for value in values)


def test_release_manifest_hashes_and_github_size_gate():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["release_id"] == "tpu-usable-2026-07-21-v1"
    assert manifest["schema_version"] == "1.0.0"
    assert manifest["counts"] == {
        "active_source_count": 27,
        "active_source_family_count": 26,
        "candidate_rows": 117_629,
        "computational_hard_groups": 55_760,
        "computational_model_ready_rows": 1_378_201,
        "computational_rows": 1_435_243,
        "curve_count": 201,
        "experimental_hard_groups": 818,
        "experimental_model_ready_rows": 214_704,
        "experimental_rows": 305_108,
        "linear_tpu_building_blocks": 8_365,
        "screening_ready_candidates": 110_807,
        "task_count": 6,
    }
    for section in ("input_files", "output_files"):
        for entry in manifest[section].values():
            path = ROOT / entry["path"]
            assert path.is_file()
            assert path.stat().st_size == entry["bytes"]
            assert path.stat().st_size < 95 * 1024 * 1024
            assert _sha256(path) == entry["sha256"]


def test_candidate_release_has_clean_linear_tpu_pool():
    frame = pd.read_csv(
        OUTPUT / "候选结构.csv.gz",
        usecols=[
            "candidate_id",
            "candidate_use",
            "screening_ready",
            "linear_component_class",
            "linear_tpu_building_block_ready",
            "candidate_priority_weight",
        ],
        low_memory=False,
    )
    assert len(frame) == 117_629
    assert frame["candidate_id"].is_unique
    assert int(frame["screening_ready"].sum()) == 110_807
    assert frame["candidate_use"].value_counts().to_dict() == {
        "virtual_repeat_unit": 100_998,
        "direct_building_block": 9_490,
        "transfer_reference": 3_501,
        "reference_only": 3_321,
        "adjacent_chemistry": 319,
    }
    linear = frame[frame["linear_tpu_building_block_ready"]]
    assert linear["linear_component_class"].value_counts().to_dict() == {
        "diisocyanate": 8_237,
        "chain_extender_diol": 78,
        "macrodiol": 50,
    }
    assert linear["candidate_priority_weight"].gt(0).all()


def _assert_observation_release(path: Path, expected_rows: int, expected_groups: int):
    frame = pd.read_csv(
        path,
        usecols=[
            "observation_id",
            "task_id",
            "usage_mode",
            "model_ready",
            "source_id",
            "source_family_id",
            "leakage_group",
            "development_split",
            "source_holdout_fold",
            "recommended_loss_weight",
            "source_balanced_sampling_probability",
        ],
        low_memory=False,
    )
    assert len(frame) == expected_rows
    assert frame["observation_id"].is_unique
    assert frame["leakage_group"].nunique() == expected_groups
    assert frame.groupby("leakage_group")["development_split"].nunique().max() == 1
    assert frame.groupby("source_family_id")["source_holdout_fold"].nunique().max() == 1
    ready = frame["model_ready"]
    assert frame.loc[ready, "recommended_loss_weight"].gt(0).all()
    assert frame.loc[~ready, "recommended_loss_weight"].eq(0).all()
    assert frame.loc[ready, "usage_mode"].isin(
        ["primary_train", "auxiliary_train"]
    ).all()
    sums = (
        frame.loc[ready]
        .groupby("task_id")["source_balanced_sampling_probability"]
        .sum()
    )
    assert np.allclose(sums.to_numpy(), 1.0, rtol=0, atol=1e-8)
    return frame


def test_computational_and_experimental_release_weights_and_splits():
    computational = _assert_observation_release(
        OUTPUT / "计算观测.csv.gz", 1_435_243, 55_760
    )
    experimental = _assert_observation_release(
        OUTPUT / "实验观测.csv.gz", 305_108, 818
    )
    assert int(computational["model_ready"].sum()) == 1_378_201
    assert int(experimental["model_ready"].sum()) == 214_704

    combined = pd.concat(
        [
            computational[["leakage_group", "development_split"]],
            experimental[["leakage_group", "development_split"]],
        ],
        ignore_index=True,
    )
    assert combined.groupby("leakage_group")["development_split"].nunique().max() == 1


def test_task_curve_reference_and_dictionary_views_are_consistent():
    tasks = pd.read_csv(OUTPUT / "任务清单.csv")
    assert set(tasks["task_id"]) == set(usable.TASK_DESCRIPTIONS)
    assert len(tasks) == 6
    assert tasks.loc[
        tasks["task_id"].eq("实验_标量校准"), "rows_model_ready"
    ].item() == 2_735
    assert tasks.loc[
        tasks["task_id"].eq("实验_曲线建模"), "independent_units_model_ready"
    ].item() == 181

    curves = pd.read_csv(OUTPUT / "曲线索引.csv")
    assert len(curves) == 201
    assert curves["curve_id"].is_unique
    assert curves["point_count"].sum() == 299_448
    assert curves.groupby("leakage_group")["development_split"].nunique().max() == 1

    references = pd.read_csv(OUTPUT / "来源与引用.csv")
    assert len(references) == 27
    assert references["source_id"].is_unique
    assert references["source_family_id"].nunique() == 26
    assert references["citation_keys"].fillna("").str.strip().ne("").all()

    dictionary = pd.read_csv(OUTPUT / "字段字典.csv")
    assert set(dictionary["file_name"]) == {
        "候选结构.csv.gz",
        "计算观测.csv.gz",
        "实验观测.csv.gz",
        "曲线索引.csv",
        "任务清单.csv",
        "来源与引用.csv",
    }
    assert dictionary["definition"].fillna("").str.strip().ne("").all()


def test_release_check_command_passes():
    subprocess.run(
        [sys.executable, str(ROOT / "代码" / "生成可用数据集.py"), "--检查"],
        cwd=ROOT,
        check=True,
    )
