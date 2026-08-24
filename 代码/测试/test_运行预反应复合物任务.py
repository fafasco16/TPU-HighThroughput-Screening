import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import 运行预反应复合物任务 as runner


def _root(tmp_path: Path, *, status: str = "ready") -> tuple[Path, Path]:
    root = tmp_path / "root"
    (root / "输入复合物").mkdir(parents=True)
    (root / "约束").mkdir()
    xyz = root / "输入复合物" / "task.xyz"
    xyz.write_text(
        "4\nfixture\nC 0 0 0\nO 1 0 0\nO 2.7 0 0\nH 3.7 0 0\n",
        encoding="utf-8",
    )
    control = root / "约束" / "task.inp"
    control.write_text(
        "$constrain\n force constant=0.5\n distance: 1,3,2.700000\n$end\n",
        encoding="utf-8",
    )
    executable = tmp_path / "xtb"
    executable.write_bytes(b"fixture executable")
    task = {
        "task_index": 0,
        "task_slug": "task",
        "pair_id": "pair-1",
        "pair_type": "diisocyanate_chain_extender",
        "diisocyanate_id": "di-1",
        "oh_component_id": "ce-1",
        "geometry_status": status,
        "execution_permission": "allowed" if status == "ready" else "blocked",
        "nco_carbon_atom_index_1based": 1,
        "oh_oxygen_atom_index_1based": 3,
        "monomer_energy_sum_hartree": -9.0,
        "charge": 0,
        "uhf": 0,
        "input_xyz_file": "输入复合物/task.xyz",
        "input_xyz_sha256": runner.sha256(xyz),
        "xcontrol_file": "约束/task.inp",
        "xcontrol_sha256": runner.sha256(control),
        "xtb_version": "6.7.1",
        "xtb_binary_sha256": runner.sha256(executable),
        "method": "GFN2-xTB",
        "environment_model": "gas_phase",
        "optimization_level": "tight",
    }
    pd.DataFrame([task]).to_csv(root / "预反应复合物任务.csv", index=False)
    return root, executable


def test_ready_task_runs_validates_distance_and_records_association_proxy(
    tmp_path, monkeypatch
):
    root, executable = _root(tmp_path)

    def fake_run(command, **kwargs):
        if kwargs.get("capture_output"):
            return SimpleNamespace(
                returncode=0,
                stdout="* xtb version 6.7.1 (edcfbbe)\nnormal termination of xtb\n",
                stderr="",
            )
        work = Path(kwargs["cwd"])
        (work / "xtbopt.xyz").write_text(
            "4\noptimized\nC 0 0 0\nO 1 0 0\nO 2.72 0 0\nH 3.72 0 0\n",
            encoding="utf-8",
        )
        (work / "xtbout.json").write_text(
            json.dumps({"total energy": -9.01}), encoding="utf-8"
        )
        (work / "wbo").write_text("1 2 1.0\n", encoding="utf-8")
        kwargs["stdout"].write("normal termination of xtb\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    state = runner.run_task(root, 0, str(executable))
    assert state["status"] == "completed"
    assert state["final_reactive_distance_a"] == pytest.approx(2.72)
    assert state["association_energy_proxy_kcal_mol"] == pytest.approx(
        -0.01 * runner.HARTREE_TO_KCAL_MOL
    )
    assert set(state["output_sha256"]) == {"xtbopt.xyz", "xtbout.json", "xtb.out", "wbo"}
    skipped = runner.run_task(root, 0, str(executable))
    assert skipped["status"] == "completed"


def test_blocked_geometry_is_recorded_without_invoking_xtb(tmp_path, monkeypatch):
    root, executable = _root(
        tmp_path, status="blocked_initial_interfragment_collision"
    )

    def unexpected(*args, **kwargs):
        raise AssertionError("blocked task must not invoke xTB")

    monkeypatch.setattr(runner.subprocess, "run", unexpected)
    state = runner.run_task(root, 0, str(executable))
    assert state["status"] == "blocked_input_geometry"
    assert state["failure_reason"] == "blocked_initial_interfragment_collision"


def test_hash_mismatch_and_existing_failed_state_require_manual_action(tmp_path):
    root, executable = _root(tmp_path)
    tasks = pd.read_csv(root / "预反应复合物任务.csv")
    tasks.loc[0, "input_xyz_sha256"] = "0" * 64
    tasks.to_csv(root / "预反应复合物任务.csv", index=False)
    with pytest.raises(runner.PrereactionRunError, match="SHA-256"):
        runner.run_task(root, 0, str(executable))
    state_path = root / "状态" / "task.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"status": "failed", "task_index": 0}), encoding="utf-8"
    )
    with pytest.raises(runner.PrereactionRunError, match="人工决定"):
        runner.run_task(root, 0, str(executable))


def test_batch_assignment_is_deterministic():
    tasks = pd.DataFrame({"task_index": list(range(10))})
    assert runner.assigned_task_indices(tasks, 0, 3) == [0, 3, 6, 9]
    assert runner.assigned_task_indices(tasks, 1, 3) == [1, 4, 7]
    with pytest.raises(ValueError, match="worker_count"):
        runner.assigned_task_indices(tasks, 0, 0)
