import json
import sys
from pathlib import Path

import pandas as pd
import pytest

import 发布CREST结果 as release


def _tasks() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "task_index": 0,
                "task_slug": "0000_ready",
                "candidate_id": "ready",
                "component_role": "chain_extender",
                "geometry_status": "ready",
                "initial_xyz_sha256": "input-hash",
            },
            {
                "task_index": 1,
                "task_slug": "0001_blocked",
                "candidate_id": "blocked",
                "component_role": "diisocyanate",
                "geometry_status": "blocked_rdkit_3d_embedding",
                "initial_xyz_sha256": "",
            },
        ]
    )


def _fixture_results(root: Path) -> None:
    completed = root / "0000_ready"
    attempt = completed / "尝试_001"
    attempt.mkdir(parents=True)
    conformers = attempt / "crest_conformers.xyz"
    conformers.write_text("1\n-1.0\nH 0 0 0\n", encoding="ascii")
    (completed / "运行状态.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "attempt": 1,
                "input_sha256": "input-hash",
                "output_sha256": release.sha256(conformers),
                "conformer_output": "尝试_001/crest_conformers.xyz",
                "runtime_seconds": 12.3,
            }
        ),
        encoding="utf-8",
    )
    blocked = root / "0001_blocked"
    blocked.mkdir()
    (blocked / "运行状态.json").write_text(
        json.dumps(
            {
                "status": "blocked_input_geometry",
                "failure_reason": "fixture",
            }
        ),
        encoding="utf-8",
    )


def test_audit_verifies_completed_and_expected_blocked(tmp_path):
    _fixture_results(tmp_path)
    audit = release.audit_results(_tasks(), tmp_path)
    assert audit["audit_status"].eq("verified").all()
    assert audit.loc[audit["run_status"].eq("completed"), "conformer_count"].item() == 1
    assert set(audit["run_status"]) == {"completed", "blocked_input_geometry"}


def test_audit_fails_closed_on_hash_mismatch_and_pending(tmp_path):
    tasks = _tasks()
    _fixture_results(tmp_path)
    state_path = tmp_path / "0000_ready" / "运行状态.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["output_sha256"] = "wrong"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "0001_blocked" / "运行状态.json").unlink()
    audit = release.audit_results(tasks, tmp_path)
    assert audit["audit_status"].eq("not_verified").all()
    assert "output_sha256_mismatch" in audit.iloc[0]["audit_issues"]
    assert audit.iloc[1]["run_status"] == "pending"


def test_build_is_deterministic_and_verifiable(tmp_path):
    result_root = tmp_path / "results"
    result_root.mkdir()
    _fixture_results(result_root)
    tasks_path = tmp_path / "tasks.csv"
    _tasks().to_csv(tasks_path, index=False)
    output = tmp_path / "release"
    first = release.build(tasks_path, result_root, output)
    first_hash = first["outputs"]["package"]["sha256"]
    second = release.build(tasks_path, result_root, output)
    assert second["outputs"]["package"]["sha256"] == first_hash
    assert second["counts"] == {
        "task_rows": 2,
        "completed": 1,
        "blocked_input_geometry": 1,
        "failed": 0,
        "verified": 2,
        "package_members": 5,
    }
    release.verify(output)


def test_build_rejects_nonterminal_release(tmp_path):
    tasks_path = tmp_path / "tasks.csv"
    _tasks().to_csv(tasks_path, index=False)
    with pytest.raises(ValueError, match="尚未达到最终发布门"):
        release.build(tasks_path, tmp_path / "missing-results", tmp_path / "output")


def test_audit_rejects_bad_schema_and_invalid_state(tmp_path):
    with pytest.raises(ValueError, match="缺少字段"):
        release.audit_results(pd.DataFrame({"x": [1]}), tmp_path)
    duplicate = pd.concat([_tasks().iloc[[0]], _tasks().iloc[[0]]])
    with pytest.raises(ValueError, match="不唯一"):
        release.audit_results(duplicate, tmp_path)
    tasks = _tasks().iloc[[0]]
    state_root = tmp_path / "0000_ready"
    state_root.mkdir()
    (state_root / "运行状态.json").write_text("{broken", encoding="utf-8")
    audit = release.audit_results(tasks, tmp_path)
    assert audit.iloc[0]["run_status"] == "invalid_state"


def test_cli_build_and_check(tmp_path, monkeypatch, capsys):
    result_root = tmp_path / "results"
    result_root.mkdir()
    _fixture_results(result_root)
    tasks_path = tmp_path / "tasks.csv"
    _tasks().to_csv(tasks_path, index=False)
    output = tmp_path / "release"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "发布CREST结果.py",
            "--任务清单",
            str(tasks_path),
            "--结果目录",
            str(result_root),
            "--输出目录",
            str(output),
        ],
    )
    release.main()
    assert "completed" in capsys.readouterr().out
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "发布CREST结果.py",
            "--任务清单",
            str(tasks_path),
            "--结果目录",
            str(result_root),
            "--输出目录",
            str(output),
            "--检查",
        ],
    )
    release.main()
    assert "核验通过" in capsys.readouterr().out
