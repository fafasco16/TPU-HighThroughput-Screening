from pathlib import Path

import pandas as pd
import pytest

import 更新预反应优先级 as priority


def _formulations() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "high_level_dft_rank": 1,
                "formulation_id": "f-1",
                "planning_tier": "tier1_small_control_matrix",
                "diisocyanate_id": "di-1",
                "macrodiol_id": "m-1",
                "chain_extender_id": "ce-1",
                "performance_claim_status": "no_performance_claim",
            },
            {
                "high_level_dft_rank": 2,
                "formulation_id": "f-2",
                "planning_tier": "tier3_commercial_comparison",
                "diisocyanate_id": "di-2",
                "macrodiol_id": "m-2",
                "chain_extender_id": "ce-2",
                "performance_claim_status": "no_performance_claim",
            },
        ]
    )


def _pairs(*, second_eligible: bool = True) -> pd.DataFrame:
    rows = []
    for index, (dii, macro, extender) in enumerate(
        [("di-1", "m-1", "ce-1"), ("di-2", "m-2", "ce-2")]
    ):
        eligible = True if index == 0 else second_eligible
        for pair_type, oh, energy in [
            ("diisocyanate_macrodiol", macro, -10.0 - index),
            ("diisocyanate_chain_extender", extender, -5.0 - index),
        ]:
            rows.append(
                {
                    "pair_id": f"p-{index}-{pair_type}",
                    "pair_type": pair_type,
                    "diisocyanate_id": dii,
                    "oh_component_id": oh,
                    "pair_status": "complete" if eligible else "incomplete",
                    "pair_release_eligible": eligible,
                    "completed_starts": 4 if eligible else 3,
                    "blocked_starts": 0,
                    "best_association_energy_proxy_kcal_mol": energy,
                    "median_association_energy_proxy_kcal_mol": energy + 1.0,
                    "association_energy_start_span_kcal_mol": 2.0 + index,
                    "best_task_slug": f"best-{index}-{pair_type}",
                }
            )
    return pd.DataFrame(rows)


def test_two_pair_types_join_and_derive_proxy_objectives_without_scalar_score():
    result = priority.build_updated_priorities(_formulations(), _pairs())
    first = result.set_index("formulation_id").loc["f-1"]
    assert first["prereaction_join_status"] == "ready"
    assert first["macrodiol_pair__best_association_energy_proxy_kcal_mol"] == -10.0
    assert first["chain_extender_pair__best_association_energy_proxy_kcal_mol"] == -5.0
    assert first["association_proxy_mean_kcal_mol"] == -7.5
    assert first["association_proxy_balance_abs_difference_kcal_mol"] == 5.0
    assert first["macrodiol_pair_energy_use_status"] == (
        "context_only_size_and_global_deformation_confounded"
    )
    assert (
        "macrodiol_pair__best_association_energy_proxy_kcal_mol"
        not in first["prereaction_pareto_objective_spec"]
    )
    assert result["prereaction_pareto_score"].isna().all()
    assert result["performance_claim_status"].eq("no_performance_claim").all()


def test_ineligible_pair_keeps_formulation_visible_but_closes_priority_gate():
    result = priority.build_updated_priorities(
        _formulations(), _pairs(second_eligible=False)
    ).set_index("formulation_id")
    assert result.loc["f-2", "prereaction_join_status"] == "incomplete_pair_result"
    assert not result.loc["f-2", "prereaction_pareto_eligible"]
    assert not result.loc["f-2", "prereaction_pareto_is_nondominated"]
    assert result.loc["f-1", "prereaction_join_status"] == "ready"


def test_duplicate_missing_pair_and_performance_claim_fail_closed():
    duplicated = pd.concat([_pairs(), _pairs().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="配对身份不唯一"):
        priority.build_updated_priorities(_formulations(), duplicated)
    missing = _pairs().iloc[:-1]
    result = priority.build_updated_priorities(_formulations(), missing)
    assert result.loc[result["formulation_id"].eq("f-2"), "prereaction_join_status"].item() == "missing_pair_result"
    claimed = _formulations()
    claimed.loc[0, "performance_claim_status"] = "high_performance"
    with pytest.raises(ValueError, match="性能宣称"):
        priority.build_updated_priorities(claimed, _pairs())


def test_writer_creates_updated_table_pareto_report_and_manifest(tmp_path: Path):
    formulations = tmp_path / "formulations.csv"
    pairs = tmp_path / "pairs.csv"
    _formulations().to_csv(formulations, index=False)
    _pairs().to_csv(pairs, index=False)
    manifest = priority.write_release(
        formulations,
        pairs,
        tmp_path / "out",
        release_id="test-prereaction-priority",
    )
    assert manifest["counts"]["formulations"] == 2
    assert manifest["counts"]["ready_formulations"] == 2
    assert (tmp_path / "out" / "高层DFT候选_预反应更新.csv").is_file()
    assert (tmp_path / "out" / "预反应Pareto.csv").is_file()
    report = (tmp_path / "out" / "预反应优先级报告.md").read_text(encoding="utf-8")
    assert "不能解释为反应能垒" in report
