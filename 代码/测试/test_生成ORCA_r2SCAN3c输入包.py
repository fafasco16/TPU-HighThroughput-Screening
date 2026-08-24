import json
from pathlib import Path

import pandas as pd
import pytest

import 生成ORCA_r2SCAN3c输入包 as orca


def _fixture(tmp_path: Path, *, eligible: bool = True):
    root = tmp_path / "results"
    attempt = root / "工作" / "best-task" / "尝试_001"
    attempt.mkdir(parents=True)
    xyz = attempt / "xtbopt.xyz"
    xyz.write_text("2\noptimized\nH 0 0 0\nH 0 0 1\n", encoding="utf-8")
    for name, text in {
        "xtbout.json": "{\"total energy\": -1.0}\n",
        "xtb.out": "*** GEOMETRY OPTIMIZATION CONVERGED AFTER 10 ITERATIONS ***\nnormal termination of xtb\n",
        "wbo": "1 2 0.1\n",
    }.items():
        (attempt / name).write_text(text, encoding="utf-8")
    outputs = {
        name: orca.sha256(attempt / name)
        for name in ("xtbopt.xyz", "xtbout.json", "xtb.out", "wbo")
    }
    state_path = root / "状态" / "best-task.json"
    state_path.parent.mkdir()
    state_path.write_text(
        json.dumps(
            {
                "task_slug": "best-task",
                "pair_id": "pair-1",
                "status": "completed",
                "attempt_directory": attempt.relative_to(root).as_posix(),
                "output_sha256": outputs,
            }
        ),
        encoding="utf-8",
    )
    pair_results = pd.DataFrame(
        [
            {
                "pair_id": "pair-1",
                "pair_type": "diisocyanate_chain_extender",
                "diisocyanate_id": "di-1",
                "oh_component_id": "ce-1",
                "pair_status": "complete" if eligible else "incomplete",
                "pair_release_eligible": eligible,
                "best_task_slug": "best-task",
            }
        ]
    )
    task_results = pd.DataFrame(
        [
            {
                "task_slug": "best-task",
                "pair_id": "pair-1",
                "run_status": "completed",
                "state_file": "状态/best-task.json",
            }
        ]
    )
    return root, pair_results, task_results


def test_eligible_pair_generates_opt_and_dependent_frequency_inputs(tmp_path):
    root, pairs, tasks = _fixture(tmp_path)
    table, manifest = orca.build_input_package(
        pairs,
        tasks,
        root,
        tmp_path / "out",
        release_id="test-orca-inputs",
    )
    assert len(table) == 1
    row = table.iloc[0]
    assert row["input_generation_status"] == "generated_execution_blocked"
    assert row["orca_version_target"] == "6.1"
    assert row["method"] == "R2SCAN-3C"
    assert row["execution_permission"] == "blocked_missing_authorized_executable"
    opt = tmp_path / "out" / row["optimization_input_file"]
    freq = tmp_path / "out" / row["frequency_input_file"]
    geometry = tmp_path / "out" / row["initial_geometry_file"]
    assert orca.sha256(opt) == row["optimization_input_sha256"]
    assert orca.sha256(freq) == row["frequency_input_sha256"]
    assert orca.sha256(geometry) == row["initial_geometry_sha256"]
    assert "! R2SCAN-3C TightSCF Opt" in opt.read_text(encoding="utf-8")
    assert "%pal" in opt.read_text(encoding="utf-8")
    assert "NProcs 8" in opt.read_text(encoding="utf-8")
    assert "! R2SCAN-3C TightSCF Freq" in freq.read_text(encoding="utf-8")
    assert "ORCA优化几何/pair-1.xyz" in freq.read_text(encoding="utf-8")
    assert manifest["counts"] == {
        "pairs": 1,
        "generated_optimization_inputs": 1,
        "blocked_pairs": 0,
    }


def test_ineligible_pair_is_retained_without_misleading_input_files(tmp_path):
    root, pairs, tasks = _fixture(tmp_path, eligible=False)
    table, manifest = orca.build_input_package(
        pairs,
        tasks,
        root,
        tmp_path / "out",
        release_id="test-orca-inputs",
    )
    row = table.iloc[0]
    assert row["input_generation_status"] == "blocked_ineligible_pair"
    assert row["optimization_input_file"] == ""
    assert manifest["counts"]["blocked_pairs"] == 1


def test_state_output_hash_mismatch_fails_closed(tmp_path):
    root, pairs, tasks = _fixture(tmp_path)
    state_path = root / "状态" / "best-task.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["output_sha256"]["xtbopt.xyz"] = "0" * 64
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        orca.build_input_package(
            pairs,
            tasks,
            root,
            tmp_path / "out",
            release_id="test-orca-inputs",
        )


def test_invalid_resource_configuration_is_rejected(tmp_path):
    root, pairs, tasks = _fixture(tmp_path)
    with pytest.raises(ValueError, match="NProcs"):
        orca.build_input_package(
            pairs,
            tasks,
            root,
            tmp_path / "out",
            release_id="test-orca-inputs",
            nprocs=0,
        )
