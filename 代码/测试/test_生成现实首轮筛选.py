from pathlib import Path

import pandas as pd
import pytest

import 生成现实首轮筛选 as screen


ROOT = Path(__file__).resolve().parents[2]


def _row(index: int, *, tier: str = "tier3_commercial_comparison") -> dict[str, object]:
    shift = index / 100.0
    return {
        "formulation_id": f"f-{index:03d}",
        "base_system_id": f"b-{index:03d}",
        "combination_id": f"b-{index:03d}",
        "planning_tier": tier,
        "baseline_priority": 1 if tier == "tier1_small_control_matrix" else 3,
        "diisocyanate_id": f"di-{index % 3}",
        "macrodiol_id": f"macro-{index % 2}",
        "chain_extender_id": f"ce-{index % 4}",
        "hard_segment_mass_fraction_target": 0.30 + 0.05 * (index % 4),
        "macrodiol_nominal_mn_g_mol": 1000.0 + 400.0 * (index % 2),
        "nco_oh_ratio_target": 1.0,
        "formulation_domain_floor": 0.2 + shift / 10,
        "formulation_applicability_status": "blocked_component_outside_training_domain",
        "screening_input_status": "ready_for_quantum_proxy_screen_outside_gnn_domain",
        "performance_claim_status": "no_performance_claim",
        "diisocyanate__site_charge_e_mean_weighted_mean": 0.30 + shift,
        "macrodiol__site_charge_e_mean_weighted_mean": -0.46 + shift / 10,
        "chain_extender__site_charge_e_mean_weighted_mean": -0.44 - shift / 10,
        "diisocyanate__site_relative_sasa_mean_weighted_mean": 0.45 + shift,
        "macrodiol__site_relative_sasa_mean_weighted_mean": 0.35 + shift / 2,
        "chain_extender__site_relative_sasa_mean_weighted_mean": 0.40 + shift / 3,
        "diisocyanate__homo_lumo_gap_ev_weighted_mean": 5.0 + shift,
        "macrodiol__homo_lumo_gap_ev_weighted_mean": 6.0 - shift,
        "chain_extender__homo_lumo_gap_ev_weighted_mean": 5.5 + shift / 2,
        "diisocyanate__conformer_uncertainty": 0.10 + shift / 10,
        "chain_extender__conformer_uncertainty": 0.12 + shift / 10,
        "diisocyanate__effective_conformer_count": 2.0 + index % 3,
        "chain_extender__effective_conformer_count": 1.5 + index % 4,
    }


def _frame(count: int = 48) -> pd.DataFrame:
    rows = [
        _row(index, tier="tier1_small_control_matrix" if index < 4 else "tier3_commercial_comparison")
        for index in range(count)
    ]
    return pd.DataFrame(rows)


def test_objectives_have_explicit_physical_proxy_meanings_and_directions():
    result = screen.derive_quantum_objectives(_frame(2))
    first = result.iloc[0]
    assert first["objective_nco_oh_charge_complementarity"] == pytest.approx(0.75)
    assert first["objective_reactive_site_accessibility_floor"] == pytest.approx(0.35)
    assert first["objective_homo_lumo_gap_floor"] == pytest.approx(5.0)
    assert first["objective_discrete_conformer_uncertainty"] > 0
    assert first["objective_discrete_conformer_burden"] == pytest.approx(3.5)
    assert set(screen.DEFAULT_OBJECTIVES.values()) == {"min", "max"}


def test_out_of_gnn_domain_rows_remain_quantum_eligible_but_no_performance_claim():
    result, _, _ = screen.build_first_round(_frame(24), cluster_count=8, queue_size=12)
    assert result["quantum_screen_eligible"].all()
    assert result["gnn_prediction_permission"].eq(
        "blocked_outside_training_structure_domain"
    ).all()
    assert result["performance_claim_status"].eq("no_performance_claim").all()
    assert result["screening_scope"].eq("quantum_proxy_stage_not_final").all()


def test_closed_descriptor_row_never_enters_pareto_cluster_or_review_queue():
    frame = _frame(24)
    frame.loc[7, "screening_input_status"] = "closed_quantum_descriptor_gate"
    result, _, queue = screen.build_first_round(frame, cluster_count=8, queue_size=12)
    row = result.loc[result["formulation_id"].eq("f-007")].iloc[0]
    assert not row["quantum_screen_eligible"]
    assert not row["pareto_is_nondominated"]
    assert pd.isna(row["screening_cluster_id"])
    assert "f-007" not in set(queue["formulation_id"])


def test_queue_is_deterministic_bounded_and_retains_all_small_control_rows():
    frame = _frame(60)
    first, _, first_queue = screen.build_first_round(
        frame, cluster_count=16, queue_size=20
    )
    shuffled = frame.sample(frac=1.0, random_state=19).reset_index(drop=True)
    second, _, second_queue = screen.build_first_round(
        shuffled, cluster_count=16, queue_size=20
    )
    assert len(first_queue) == len(second_queue) == 20
    assert first_queue["formulation_id"].tolist() == second_queue["formulation_id"].tolist()
    controls = set(frame.loc[frame["planning_tier"].eq("tier1_small_control_matrix"), "formulation_id"])
    assert controls.issubset(set(first_queue["formulation_id"]))
    for column in ("diisocyanate_id", "macrodiol_id", "chain_extender_id"):
        assert set(first_queue[column]) == set(frame[column])
    assert first["pareto_score"].isna().all()


def test_invalid_queue_and_incomplete_objectives_fail_closed():
    with pytest.raises(ValueError, match="queue_size"):
        screen.build_first_round(_frame(8), cluster_count=4, queue_size=0)
    with pytest.raises(ValueError, match="cluster_count"):
        screen.build_first_round(_frame(8), cluster_count=0, queue_size=4)
    missing = _frame(8).drop(columns="macrodiol__site_charge_e_mean_weighted_mean")
    with pytest.raises(ValueError, match="缺少字段"):
        screen.derive_quantum_objectives(missing)


def test_writer_outputs_full_frontier_queue_manifest_and_report(tmp_path: Path):
    source = tmp_path / "formulations.csv"
    _frame(48).to_csv(source, index=False)
    manifest = screen.write_release(
        source,
        tmp_path / "out",
        release_id="test-first-round",
        cluster_count=12,
        queue_size=20,
    )
    assert manifest["counts"]["total_formulations"] == 48
    assert manifest["counts"]["review_queue"] == 20
    assert (tmp_path / "out" / "首轮候选.csv").is_file()
    assert (tmp_path / "out" / "Pareto前沿.csv").is_file()
    assert (tmp_path / "out" / "DFT_MD复核队列.csv").is_file()
    report = (tmp_path / "out" / "筛选报告.md").read_text(encoding="utf-8")
    assert "不能解释为拉伸强度、韧性或最终性能排名" in report
    assert "GNN结构域外" in report


def test_current_first_round_release_has_full_role_coverage_and_no_performance_claim():
    full_path = ROOT / "结果" / "现实筛选" / "首轮候选.csv"
    queue_path = ROOT / "结果" / "现实筛选" / "DFT_MD复核队列.csv"
    if not full_path.is_file() or not queue_path.is_file():
        pytest.skip("现实首轮筛选发布尚未物化")
    full = pd.read_csv(full_path)
    queue = pd.read_csv(queue_path)
    assert len(full) == 980
    assert len(queue) == 40
    assert full["quantum_screen_eligible"].all()
    assert full["performance_claim_status"].eq("no_performance_claim").all()
    assert queue["diisocyanate_id"].nunique() == 7
    assert queue["macrodiol_id"].nunique() == 5
    assert queue["chain_extender_id"].nunique() == 7
    assert queue["base_system_id"].nunique() >= 20
