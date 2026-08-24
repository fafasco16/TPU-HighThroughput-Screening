import json
from pathlib import Path

import pandas as pd

import 汇总预反应复合物 as aggregate


def _tasks() -> pd.DataFrame:
    rows = []
    for index in range(8):
        blocked = index == 3
        rows.append(
            {
                "task_index": index,
                "task_slug": f"task-{index}",
                "pair_id": "pair-a" if index < 4 else "pair-b",
                "pair_type": "diisocyanate_macrodiol",
                "diisocyanate_id": "di-1",
                "oh_component_id": "m-1" if index < 4 else "m-2",
                "start_index": index % 4 + 1,
                "geometry_status": (
                    "blocked_initial_interfragment_collision" if blocked else "ready"
                ),
                "execution_permission": "blocked" if blocked else "allowed",
            }
        )
    return pd.DataFrame(rows)


def _states(root: Path) -> None:
    (root / "状态").mkdir(parents=True)
    for index in range(8):
        if index == 3:
            state = {
                "task_index": index,
                "task_slug": f"task-{index}",
                "pair_id": "pair-a",
                "status": "blocked_input_geometry",
                "failure_reason": "blocked_initial_interfragment_collision",
            }
        elif index == 7:
            state = {
                "task_index": index,
                "task_slug": f"task-{index}",
                "pair_id": "pair-b",
                "status": "failed",
                "failure_reason": "nonzero_exit_code",
            }
        else:
            pair = "pair-a" if index < 4 else "pair-b"
            energy = -5.0 - index / 10
            state = {
                "task_index": index,
                "task_slug": f"task-{index}",
                "pair_id": pair,
                "status": "completed",
                "association_energy_proxy_kcal_mol": energy,
                "complex_total_energy_hartree": -100.0,
                "final_reactive_distance_a": 2.7,
                "runtime_seconds": 1.0,
            }
        (root / "状态" / f"task-{index}.json").write_text(
            json.dumps(state), encoding="utf-8"
        )


def test_collect_and_pair_aggregation_keep_blocked_and_failed_starts(tmp_path):
    tasks = _tasks()
    _states(tmp_path)
    statuses = aggregate.collect_task_states(tasks, tmp_path)
    assert statuses["run_status"].value_counts().to_dict() == {
        "completed": 6,
        "blocked_input_geometry": 1,
        "failed": 1,
    }
    pairs = aggregate.aggregate_pair_results(statuses)
    first = pairs.set_index("pair_id").loc["pair-a"]
    assert first["pair_status"] == "complete_with_blocked_starts"
    assert first["pair_release_eligible"]
    assert first["completed_starts"] == 3
    assert first["blocked_starts"] == 1
    assert first["best_association_energy_proxy_kcal_mol"] == -5.2
    second = pairs.set_index("pair_id").loc["pair-b"]
    assert second["pair_status"] == "incomplete"
    assert not second["pair_release_eligible"]


def test_missing_and_identity_mismatched_states_are_not_treated_as_success(tmp_path):
    tasks = _tasks().iloc[:2]
    (tmp_path / "状态").mkdir()
    (tmp_path / "状态" / "task-0.json").write_text(
        json.dumps(
            {
                "task_index": 999,
                "task_slug": "task-0",
                "pair_id": "pair-a",
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )
    statuses = aggregate.collect_task_states(tasks, tmp_path)
    assert statuses["run_status"].tolist() == ["invalid_state_identity", "pending"]


def test_writer_outputs_task_pair_tables_and_manifest(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    tasks = _tasks()
    tasks.to_csv(root / "预反应复合物任务.csv", index=False)
    _states(root)
    manifest = aggregate.write_outputs(root)
    assert manifest["counts"] == {
        "tasks": 8,
        "pairs": 2,
        "completed_tasks": 6,
        "eligible_pairs": 1,
    }
    assert (root / "聚合" / "逐任务结果.csv").is_file()
    assert (root / "聚合" / "逐配对结果.csv").is_file()
    assert (root / "聚合" / "聚合发布清单.json").is_file()
