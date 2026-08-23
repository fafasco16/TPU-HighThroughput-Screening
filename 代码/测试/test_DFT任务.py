import json
import sys
from types import SimpleNamespace
from pathlib import Path

import pandas as pd
import pytest

import DFT任务 as dft
import 生成DFT任务 as generator
import 运行CREST任务 as runner
import 汇总CREST结果 as collector


ROOT = Path(__file__).resolve().parents[2]


def _queue_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "formulation_id": "f-1",
                "combination_id": "c-1",
                "diisocyanate_id": "di-1",
                "diisocyanate_smiles": "O=C=NCCCCCCN=C=O",
                "macrodiol_proxy_id": "macro-1",
                "macrodiol_proxy_smiles": "OCCCCCCCCO",
                "chain_extender_id": "ce-1",
                "chain_extender_smiles": "OCCO",
            },
            {
                "formulation_id": "f-2",
                "combination_id": "c-2",
                "diisocyanate_id": "di-1",
                "diisocyanate_smiles": "O=C=NCCCCCCN=C=O",
                "macrodiol_proxy_id": "macro-2",
                "macrodiol_proxy_smiles": "Oc1ccc(O)cc1",
                "chain_extender_id": "ce-1",
                "chain_extender_smiles": "OCCO",
            },
        ]
    )


def test_queue_deduplicates_to_unique_components():
    tasks = dft.build_component_tasks(_queue_fixture())
    assert tasks["candidate_id"].is_unique
    assert set(tasks["component_role"]) == {
        "diisocyanate",
        "macrodiol_proxy",
        "chain_extender",
    }
    assert len(tasks) == 4
    diisocyanate = tasks.loc[tasks["candidate_id"].eq("di-1")].iloc[0]
    assert diisocyanate["formulation_count"] == 2
    assert diisocyanate["formulation_ids"] == "f-1;f-2"


def test_task_builder_fails_closed_on_identity_conflicts():
    with pytest.raises(ValueError, match="缺少字段"):
        dft.build_component_tasks(pd.DataFrame({"x": [1]}))
    duplicate = pd.concat([_queue_fixture().iloc[[0]], _queue_fixture().iloc[[0]]])
    with pytest.raises(ValueError, match="不唯一"):
        dft.build_component_tasks(duplicate)
    conflict = _queue_fixture()
    conflict.loc[1, "diisocyanate_id"] = "di-1"
    conflict.loc[1, "diisocyanate_smiles"] = "O=C=NCCN=C=O"
    with pytest.raises(ValueError, match="多个 SMILES"):
        dft.build_component_tasks(conflict)


def test_initial_geometry_is_deterministic_and_contains_hydrogens(tmp_path):
    first = dft.generate_initial_xyz("OCCO", seed=9, conformer_count=3)
    second = dft.generate_initial_xyz("OCCO", seed=9, conformer_count=3)
    assert first["xyz"] == second["xyz"]
    assert first["atom_count"] == 10
    assert first["initial_force_field"] in {"MMFF94s", "UFF"}
    tasks = dft.build_component_tasks(_queue_fixture(), seed=9)
    published = dft.materialize_initial_structures(tasks, tmp_path)
    assert len(list((tmp_path / "初始结构").glob("*.xyz"))) == 4
    assert published["initial_xyz_sha256"].str.len().eq(64).all()
    assert published["geometry_status"].eq("ready").all()
    with pytest.raises(ValueError, match="无法解析"):
        dft.generate_initial_xyz("not-a-smiles")


def test_materializer_records_embedding_block_and_rejects_stale_files(tmp_path, monkeypatch):
    tasks = dft.build_component_tasks(_queue_fixture().iloc[[0]])
    original = dft.generate_initial_xyz

    def fail_one(smiles, seed=20260823, conformer_count=10):
        if smiles == "OCCO":
            raise ValueError("fixture embedding failure")
        return original(smiles, seed=seed, conformer_count=2)

    monkeypatch.setattr(dft, "generate_initial_xyz", fail_one)
    published = dft.materialize_initial_structures(tasks, tmp_path)
    blocked = published.loc[published["canonical_smiles"].eq("OCCO")].iloc[0]
    assert blocked["geometry_status"] == "blocked_rdkit_3d_embedding"
    assert blocked["initial_xyz_bytes"] == 0
    (tmp_path / "初始结构" / "stale.xyz").write_text("stale", encoding="ascii")
    with pytest.raises(ValueError, match="非本发布文件"):
        dft.materialize_initial_structures(tasks, tmp_path)


def test_completed_task_is_skipped_when_hash_matches(tmp_path):
    state = runner.completed_state(input_sha256="abc")
    assert runner.should_skip(state, input_sha256="abc") is True
    assert runner.should_skip(state, input_sha256="changed") is False
    assert runner.should_skip({"status": "failed", "input_sha256": "abc"}, "abc") is False
    result = tmp_path / "result"
    result.mkdir()
    assert runner.should_skip(state, "abc", result) is False
    (result / "crest_conformers.xyz").write_text("1\n\nH 0 0 0\n", encoding="ascii")
    assert runner.should_skip(state, "abc", result) is True


def test_concurrency_slot_is_transparent_outside_slurm(tmp_path, monkeypatch):
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    monkeypatch.delenv("SLURM_CPUS_PER_TASK", raising=False)
    with runner.slurm_concurrency_slot(tmp_path, threads=4):
        marker = tmp_path / "inside"
        marker.write_text("ok", encoding="ascii")
    assert marker.read_text(encoding="ascii") == "ok"


def _runner_root(tmp_path: Path, geometry_status: str = "ready") -> Path:
    root = tmp_path / "remote"
    (root / "初始结构").mkdir(parents=True)
    initial = root / "初始结构" / "0000_x.xyz"
    initial.write_text("1\nfixture\nH 0 0 0\n", encoding="ascii")
    pd.DataFrame(
        [
            {
                "task_index": 0,
                "task_slug": "0000_x",
                "candidate_id": "x",
                "component_role": "chain_extender",
                "geometry_status": geometry_status,
                "geometry_error": "blocked fixture" if geometry_status != "ready" else "",
                "initial_xyz_file": "初始结构/0000_x.xyz",
                "initial_xyz_sha256": dft.sha256(initial),
                "charge": 0,
                "uhf": 0,
            }
        ]
    ).to_csv(root / "DFT任务清单.csv", index=False)
    return root


def test_runner_completes_and_then_skips_matching_task(tmp_path, monkeypatch):
    root = _runner_root(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if kwargs.get("capture_output"):
            return SimpleNamespace(returncode=0, stdout="crest version 3.0.2", stderr="")
        (Path(kwargs["cwd"]) / "crest_conformers.xyz").write_text(
            "1\nfixture\nH 0 0 0\n", encoding="ascii"
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    state = runner.run_task(root, 0, threads=2, crest_executable="crest-fixture")
    assert state["status"] == "completed"
    assert state["output_sha256"]
    first_call_count = len(calls)
    skipped = runner.run_task(root, 0, threads=2, crest_executable="crest-fixture")
    assert skipped["status"] == "completed"
    assert len(calls) == first_call_count


def test_runner_records_blocked_and_failed_tasks(tmp_path, monkeypatch):
    blocked_root = _runner_root(tmp_path / "blocked", geometry_status="blocked_rdkit_3d_embedding")
    blocked = runner.run_task(blocked_root, 0, 2, "crest-fixture")
    assert blocked["status"] == "blocked_input_geometry"

    failed_root = _runner_root(tmp_path / "failed")

    def fake_failure(command, **kwargs):
        if kwargs.get("capture_output"):
            return SimpleNamespace(returncode=0, stdout="crest version fixture", stderr="")
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(runner.subprocess, "run", fake_failure)
    with pytest.raises(RuntimeError, match="CREST 任务失败"):
        runner.run_task(failed_root, 0, 2, "crest-fixture")
    state = json.loads(
        (failed_root / "结果" / "0000_x" / "运行状态.json").read_text(encoding="utf-8")
    )
    assert state["status"] == "failed"
    assert state["exit_code"] == 7


def test_summary_distinguishes_completed_failed_and_pending(tmp_path):
    tasks = pd.DataFrame(
        [
            {"task_index": 0, "task_slug": "a", "candidate_id": "a", "component_role": "x", "initial_xyz_sha256": "h0"},
            {"task_index": 1, "task_slug": "b", "candidate_id": "b", "component_role": "x", "initial_xyz_sha256": "h1"},
            {"task_index": 2, "task_slug": "c", "candidate_id": "c", "component_role": "x", "initial_xyz_sha256": "h2"},
        ]
    )
    completed = tmp_path / "a"
    completed.mkdir()
    xyz = completed / "ensemble.xyz"
    xyz.write_text("1\nfirst\nH 0 0 0\n1\nsecond\nH 1 0 0\n", encoding="ascii")
    (completed / "运行状态.json").write_text(
        json.dumps({"status": "completed", "conformer_output": "ensemble.xyz", "attempt": 1}),
        encoding="utf-8",
    )
    failed = tmp_path / "b"
    failed.mkdir()
    (failed / "运行状态.json").write_text(
        json.dumps({"status": "failed", "failure_reason": "nonzero_exit_code"}),
        encoding="utf-8",
    )
    summary = collector.collect_status(tasks, tmp_path)
    assert set(summary["status"]) == {"completed", "failed", "pending"}
    assert summary.loc[summary["status"].eq("completed"), "conformer_count"].item() == 2
    assert collector.count_xyz_conformers(tmp_path / "missing.xyz") == 0
    invalid = tmp_path / "invalid.xyz"
    invalid.write_text("not-an-xyz", encoding="ascii")
    assert collector.count_xyz_conformers(invalid) == 0
    bad_state = tmp_path / "c"
    bad_state.mkdir()
    (bad_state / "运行状态.json").write_text("{broken", encoding="utf-8")
    invalid_summary = collector.collect_status(tasks, tmp_path)
    assert "invalid_state" in set(invalid_summary["status"])


def test_collector_cli_writes_machine_readable_summary(tmp_path, monkeypatch, capsys):
    root = tmp_path / "project"
    root.mkdir()
    pd.DataFrame(
        [
            {
                "task_index": 0,
                "task_slug": "a",
                "candidate_id": "a",
                "component_role": "x",
                "initial_xyz_sha256": "h0",
            }
        ]
    ).to_csv(root / "DFT任务清单.csv", index=False)
    output = root / "summary.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        ["汇总CREST结果.py", "--根目录", str(root), "--输出", str(output)],
    )
    collector.main()
    assert output.is_file()
    assert pd.read_csv(output)["status"].tolist() == ["pending"]
    assert "pending" in capsys.readouterr().out


def test_formal_dft_release_is_complete_and_hash_verified():
    manifest_path = ROOT / "计算" / "DFT任务发布清单.json"
    if not manifest_path.exists():
        pytest.skip("DFT 任务发布尚未生成")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["counts"] == {
        "source_formulation_rows": 48,
        "unique_component_tasks": 86,
        "geometry_ready_tasks": 84,
        "geometry_blocked_tasks": 2,
        "diisocyanate_tasks": 48,
        "macrodiol_proxy_tasks": 16,
        "chain_extender_tasks": 22,
        "initial_xyz_files": 84,
    }
    generator.verify(ROOT / "计算")
