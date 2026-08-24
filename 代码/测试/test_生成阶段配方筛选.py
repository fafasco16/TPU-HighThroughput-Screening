from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

import 生成阶段配方筛选 as stage


ROOT = Path(__file__).resolve().parents[2]


def _component(
    candidate_id: str,
    role: str,
    value: float,
    *,
    status: str = "complete",
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "component_role": role,
        "ensemble_status": status,
        "site_charge_e_mean_weighted_mean": value,
        "site_relative_sasa_mean_weighted_mean": 0.2 + value / 100,
        "effective_conformer_count": 2.0 + abs(value),
        "homo_lumo_gap_ev_weighted_sd": 0.1 + abs(value) / 100,
        "site_charge_e_mean_weighted_sd": 0.01 + abs(value) / 1000,
        "site_relative_sasa_mean_weighted_sd": 0.02 + abs(value) / 1000,
    }


def _synthetic_stage() -> tuple[pd.DataFrame, pd.DataFrame]:
    formulations: list[dict[str, object]] = []
    components: list[dict[str, object]] = [
        _component("ce-common", "chain_extender", -0.4),
    ]
    for index in range(48):
        di_id = f"di-{index:02d}"
        macro_id = f"macro-{index:02d}"
        formulations.append(
            {
                "queue_rank": index + 1,
                "formulation_id": f"f-{index:02d}",
                "combination_id": f"combo-{index:02d}",
                "diisocyanate_id": di_id,
                "macrodiol_proxy_id": macro_id,
                "chain_extender_id": "ce-common",
                "macrodiol_nominal_mn_g_mol": 2000.0,
                "hard_segment_mass_fraction_target": 0.45,
                "nco_oh_ratio_target": 1.02,
            }
        )
        components.append(_component(di_id, "diisocyanate", 0.2 + index / 100))
        if index < 40:
            components.append(
                _component(macro_id, "macrodiol_proxy", -0.5 - index / 200)
            )
    return pd.DataFrame(formulations), pd.DataFrame(components)


def test_stage_join_keeps_48_rows_and_closes_missing_components():
    formulations, components = _synthetic_stage()
    result, _ = stage.build_stage_screening(formulations, components)
    assert len(result) == 48
    assert result["formulation_id"].is_unique
    assert result["stage_screen_status"].value_counts().to_dict() == {
        "ready": 40,
        "closed": 8,
    }
    assert result["screening_scope"].eq("stage_only_not_final").all()
    assert result["performance_claim_status"].eq("no_performance_claim").all()


def test_closed_rows_never_enter_standardization_clustering_or_pareto():
    formulations, components = _synthetic_stage()
    result, _ = stage.build_stage_screening(formulations, components)
    closed = result["stage_screen_status"].eq("closed")
    z_columns = [column for column in result if column.startswith("stage_z__")]
    assert result.loc[closed, z_columns].isna().all().all()
    assert result.loc[closed, "stage_cluster_id"].isna().all()
    assert not result.loc[closed, "stage_cluster_representative"].any()
    assert not result.loc[closed, "pareto_eligible"].any()
    assert not result.loc[closed, "pareto_is_nondominated"].any()


def test_objective_directions_are_explicit_and_no_scalar_score_is_created():
    formulations, components = _synthetic_stage()
    result, _ = stage.build_stage_screening(formulations, components)
    expected = ";".join(
        f"{name}:{direction}" for name, direction in stage.DEFAULT_OBJECTIVES.items()
    )
    assert result.loc[result["pareto_eligible"], "pareto_objective_spec"].eq(expected).all()
    assert set(stage.DEFAULT_OBJECTIVES.values()) == {"min", "max"}
    assert result["pareto_score"].isna().all()


def test_standardization_uses_only_eligible_rows_and_constants_map_to_zero():
    frame = pd.DataFrame({"x": [1.0, 3.0, 1000.0], "constant": [4.0, 4.0, -99.0]})
    result, parameters = stage.deterministic_standardize(
        frame, ["x", "constant"], [True, True, False]
    )
    assert parameters["x"] == {"mean": 2.0, "scale": 1.0, "constant": False}
    assert parameters["constant"] == {"mean": 4.0, "scale": 1.0, "constant": True}
    assert result["stage_z__x"].tolist()[:2] == [-1.0, 1.0]
    assert result["stage_z__constant"].tolist()[:2] == [0.0, 0.0]
    assert np.isnan(result.loc[2, "stage_z__x"])


def test_farthest_first_clustering_is_deterministic_under_row_shuffle():
    formulations, components = _synthetic_stage()
    first, _ = stage.build_stage_screening(formulations, components, cluster_count=6)
    shuffled = formulations.sample(frac=1.0, random_state=7).reset_index(drop=True)
    second, _ = stage.build_stage_screening(shuffled, components, cluster_count=6)
    first_map = first.set_index("formulation_id")[
        ["stage_cluster_id", "stage_cluster_representative"]
    ].sort_index()
    second_map = second.set_index("formulation_id")[
        ["stage_cluster_id", "stage_cluster_representative"]
    ].sort_index()
    pd.testing.assert_frame_equal(first_map, second_map)
    assert first.loc[first["pareto_eligible"], "stage_cluster_id"].nunique() == 6
    assert first["stage_cluster_representative"].sum() == 6


def test_role_mismatch_and_duplicate_component_ids_fail_closed():
    formulations, components = _synthetic_stage()
    wrong = components.copy()
    wrong.loc[wrong["candidate_id"].eq("di-00"), "component_role"] = "chain_extender"
    with pytest.raises(ValueError, match="角色不匹配"):
        stage.build_stage_screening(formulations, wrong)
    duplicated = pd.concat([components, components.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="candidate_id 不唯一"):
        stage.build_stage_screening(formulations, duplicated)


def test_invalid_masks_empty_inputs_and_cluster_counts_are_rejected():
    frame = pd.DataFrame({"formulation_id": ["a"], "x": [1.0]})
    with pytest.raises(ValueError, match="行数不一致"):
        stage.deterministic_standardize(frame, ["x"], [])
    with pytest.raises(ValueError, match="没有可用于"):
        stage.deterministic_standardize(frame, ["x"], [False])
    with pytest.raises(ValueError, match="缺失或非有限值"):
        stage.deterministic_standardize(
            pd.DataFrame({"x": [np.nan]}), ["x"], [True]
        )
    with pytest.raises(ValueError, match="至少为1"):
        stage.deterministic_farthest_first_clusters(
            frame, ["x"], [True], cluster_count=0
        )
    with pytest.raises(ValueError, match="行数不一致"):
        stage.deterministic_farthest_first_clusters(frame, ["x"], [])
    labels, representatives = stage.deterministic_farthest_first_clusters(
        frame, ["x"], [False]
    )
    assert labels.isna().all() and not representatives.any()
    with pytest.raises(ValueError, match="非空且唯一"):
        stage.deterministic_farthest_first_clusters(
            pd.DataFrame({"formulation_id": ["a", "a"], "x": [0.0, 1.0]}),
            ["x"],
            [True, True],
        )
    with pytest.raises(ValueError, match="非有限值"):
        stage.deterministic_farthest_first_clusters(
            pd.DataFrame({"formulation_id": ["a"], "x": [np.nan]}),
            ["x"],
            [True],
        )
    formulations, components = _synthetic_stage()
    with pytest.raises(ValueError, match="不能为空"):
        stage.build_stage_screening(formulations.iloc[0:0], components)
    with pytest.raises(ValueError, match="不能为空"):
        stage.build_stage_screening(formulations, components.iloc[0:0])
    no_role = components.drop(columns="component_role")
    result, _ = stage.build_stage_screening(formulations, no_role)
    assert len(result) == 48
    empty_id = components.copy()
    empty_id.loc[0, "candidate_id"] = ""
    with pytest.raises(ValueError, match="存在空值"):
        stage.build_stage_screening(formulations, empty_id)


def test_report_states_stage_boundary_counts_and_objectives():
    formulations, components = _synthetic_stage()
    result, parameters = stage.build_stage_screening(formulations, components)
    report = stage.build_stage_report(result, stage.DEFAULT_OBJECTIVES, parameters)
    assert "stage_only_not_final" in report
    assert "阶段可用：40" in report
    assert "闭门保留：8" in report
    assert "不是最终候选排名" in report
    assert "不能解释为TPU强度、韧性或可合成性结论" in report


def test_write_outputs_produces_full_table_frontier_and_report(tmp_path: Path):
    formulations, components = _synthetic_stage()
    queue_path = tmp_path / "queue.csv"
    component_path = tmp_path / "components.csv"
    features_path = tmp_path / "features.csv.gz"
    frontier_path = tmp_path / "frontier.csv"
    report_path = tmp_path / "report.md"
    formulations.to_csv(queue_path, index=False)
    components.to_csv(component_path, index=False)
    counts = stage.write_stage_outputs(
        queue_path, component_path, features_path, frontier_path, report_path
    )
    full = pd.read_csv(features_path)
    frontier = pd.read_csv(frontier_path)
    assert counts["total"] == len(full) == 48
    assert counts["ready"] == 40
    assert len(frontier) == counts["pareto_frontier"]
    assert frontier["pareto_eligible"].all()
    assert frontier["pareto_is_nondominated"].all()
    assert "stage_only_not_final" in report_path.read_text(encoding="utf-8")


def test_real_stage82_table_closes_exactly_eight_formulations_when_available():
    component_path = ROOT / "tmp" / "xTB构件级系综描述符_stage82.csv"
    queue_path = ROOT / "候选" / "DFT_MD复核队列.csv"
    if not component_path.is_file():
        pytest.skip("真实82构件阶段表不在本地")
    result, _ = stage.build_stage_screening(
        pd.read_csv(queue_path), pd.read_csv(component_path)
    )
    assert len(result) == 48
    assert result["formulation_id"].is_unique
    assert result["stage_screen_status"].value_counts().to_dict() == {
        "ready": 40,
        "closed": 8,
    }


def test_report_rejects_missing_or_incorrect_stage_scope():
    with pytest.raises(ValueError, match="缺少字段"):
        stage.build_stage_report(pd.DataFrame({"x": [1]}), {}, {})
    invalid = pd.DataFrame(
        {
            "screening_scope": ["final"],
            "stage_screen_status": ["ready"],
            "pareto_eligible": [True],
            "pareto_is_nondominated": [True],
            "stage_cluster_id": ["stage_cluster_01"],
        }
    )
    with pytest.raises(ValueError, match="stage_only_not_final"):
        stage.build_stage_report(invalid, {}, {})


def test_main_parses_paths_and_writes_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    formulations, components = _synthetic_stage()
    queue_path = tmp_path / "queue.csv"
    component_path = tmp_path / "components.csv"
    feature_path = tmp_path / "features.csv"
    pareto_path = tmp_path / "pareto.csv"
    report_path = tmp_path / "report.md"
    formulations.to_csv(queue_path, index=False)
    components.to_csv(component_path, index=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "生成阶段配方筛选.py",
            "--配方队列", str(queue_path),
            "--构件系综", str(component_path),
            "--特征输出", str(feature_path),
            "--Pareto输出", str(pareto_path),
            "--报告输出", str(report_path),
            "--聚类数", "5",
        ],
    )
    stage.main()
    assert "'total': 48" in capsys.readouterr().out
    assert feature_path.is_file() and pareto_path.is_file() and report_path.is_file()
