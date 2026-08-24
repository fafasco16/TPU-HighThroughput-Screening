from pathlib import Path

import pandas as pd
import pytest

import 生成高层DFT子集 as subset


ROOT = Path(__file__).resolve().parents[2]


def _queue() -> pd.DataFrame:
    rows = []
    for index in range(20):
        rows.append(
            {
                "review_queue_rank": index + 1,
                "formulation_id": f"f-{index:02d}",
                "base_system_id": f"b-{index:02d}",
                "planning_tier": "tier1_small_control_matrix" if index < 2 else "tier3_commercial_comparison",
                "diisocyanate_id": f"di-{index % 4}",
                "macrodiol_id": f"macro-{index % 3}",
                "chain_extender_id": f"ce-{index % 5}",
                "hard_segment_mass_fraction_target": 0.30 + 0.05 * (index % 4),
                "pareto_is_nondominated": index % 2 == 0,
                "screening_cluster_representative": index % 3 == 0,
                "performance_claim_status": "no_performance_claim",
            }
        )
    return pd.DataFrame(rows)


def test_subset_retains_controls_and_covers_every_component_deterministically():
    queue = _queue()
    first = subset.select_high_level_subset(queue, target_size=8)
    second = subset.select_high_level_subset(
        queue.sample(frac=1.0, random_state=5).reset_index(drop=True),
        target_size=8,
    )
    assert first["formulation_id"].tolist() == second["formulation_id"].tolist()
    controls = set(queue.loc[queue["planning_tier"].eq("tier1_small_control_matrix"), "formulation_id"])
    assert controls.issubset(set(first["formulation_id"]))
    for column in ("diisocyanate_id", "macrodiol_id", "chain_extender_id"):
        assert set(first[column]) == set(queue[column])
    assert first["high_level_dft_rank"].tolist() == list(range(1, 9))
    assert first["dft_engine_status"].eq(
        "blocked_no_authorized_r2scan3c_engine"
    ).all()
    assert first["pre_reaction_xtb_status"].eq("ready").all()


def test_too_small_target_duplicate_ids_and_performance_claim_fail_closed():
    with pytest.raises(ValueError, match="无法覆盖"):
        subset.select_high_level_subset(_queue(), target_size=2)
    duplicated = pd.concat([_queue(), _queue().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="formulation_id"):
        subset.select_high_level_subset(duplicated, target_size=8)
    claimed = _queue()
    claimed.loc[0, "performance_claim_status"] = "high_performance"
    with pytest.raises(ValueError, match="性能宣称"):
        subset.select_high_level_subset(claimed, target_size=8)


def test_writer_creates_hashed_subset_manifest_and_protocol_note(tmp_path: Path):
    source = tmp_path / "queue.csv"
    _queue().to_csv(source, index=False)
    manifest = subset.write_release(
        source,
        tmp_path / "out",
        release_id="test-high-level-dft-subset",
        target_size=8,
    )
    assert manifest["counts"] == {
        "source_queue": 20,
        "selected_formulations": 8,
        "diisocyanates": 4,
        "macrodiols": 3,
        "chain_extenders": 5,
    }
    assert (tmp_path / "out" / "高层DFT候选8.csv").is_file()
    note = (tmp_path / "out" / "高层DFT执行门.md").read_text(encoding="utf-8")
    assert "当前没有授权可执行的r2SCAN-3c程序" in note
    assert "xTB预反应复合物" in note


def test_current_40_row_queue_can_form_12_row_full_coverage_subset():
    queue_path = ROOT / "结果" / "现实筛选" / "DFT_MD复核队列.csv"
    if not queue_path.is_file():
        pytest.skip("现实DFT/MD复核队列尚未物化")
    result = subset.select_high_level_subset(pd.read_csv(queue_path), target_size=12)
    assert len(result) == 12
    assert result["diisocyanate_id"].nunique() == 7
    assert result["macrodiol_id"].nunique() == 5
    assert result["chain_extender_id"].nunique() == 7
