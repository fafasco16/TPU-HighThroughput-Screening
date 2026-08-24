from pathlib import Path

import pandas as pd
import pytest

import 运行xTB批次 as batch


def test_assigned_indices_are_disjoint_complete_and_stable():
    tasks = pd.DataFrame({"xtb_task_index": [20, 0, 10, 30, 40]})
    parts = [batch.assigned_task_indices(tasks, index, 3) for index in range(3)]
    assert parts == [[0, 30], [10, 40], [20]]
    assert sorted(value for part in parts for value in part) == [0, 10, 20, 30, 40]


def test_assignment_rejects_invalid_worker_and_schema():
    tasks = pd.DataFrame({"xtb_task_index": [0, 1]})
    with pytest.raises(ValueError, match="worker_count"):
        batch.assigned_task_indices(tasks, 0, 0)
    with pytest.raises(ValueError, match="worker_index"):
        batch.assigned_task_indices(tasks, 2, 2)
    with pytest.raises(ValueError, match="缺少"):
        batch.assigned_task_indices(pd.DataFrame({"x": [1]}), 0, 1)
    with pytest.raises(ValueError, match="不唯一"):
        batch.assigned_task_indices(
            pd.DataFrame({"xtb_task_index": [1, 1]}), 0, 1
        )


def test_batch_continues_after_failure_and_writes_summary(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    pd.DataFrame({"xtb_task_index": [0, 1, 2]}).to_csv(
        root / "xTB构象任务清单.csv", index=False
    )

    def fake_run(root_path, index, executable, *, tasks):
        assert root_path == root
        assert executable == "xtb-fixture"
        assert len(tasks) == 3
        if index == 1:
            raise batch.XtbRunError("fixture failure")
        return {"status": "completed"}

    monkeypatch.setattr(batch, "run_task", fake_run)
    summary = batch.run_batch(root, 0, 1, "xtb-fixture")
    assert summary["assigned"] == 3
    assert summary["completed_or_skipped"] == 2
    assert summary["failed"] == 1
    assert summary["failures"] == [
        {"xtb_task_index": 1, "error": "fixture failure"}
    ]
    assert (root / "worker汇总" / "worker_000.json").is_file()
