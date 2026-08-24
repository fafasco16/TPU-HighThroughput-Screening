import json
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

import CREST系综分析 as ensemble


def _xyz(energies: list[str]) -> str:
    return "".join(f"2\n{energy}\nH 0 0 0\nH 0 0 0.7\n" for energy in energies)


def _task() -> pd.DataFrame:
    return pd.DataFrame([{"task_index": 0, "task_slug": "0000_ready", "candidate_id": "ready", "component_role": "chain_extender", "initial_xyz_sha256": "input-hash"}])


def _completed(root: Path, text: str) -> None:
    task_root, attempt = root / "0000_ready", root / "0000_ready" / "尝试_001"
    attempt.mkdir(parents=True)
    output = attempt / "crest_conformers.xyz"
    output.write_text(text, encoding="utf-8")
    (task_root / "运行状态.json").write_text(json.dumps({"status": "completed", "task_slug": "0000_ready", "candidate_id": "ready", "component_role": "chain_extender", "input_sha256": "input-hash", "output_sha256": ensemble.sha256(output), "conformer_output": "尝试_001/crest_conformers.xyz"}), encoding="utf-8")


def test_parse_multiframe_xyz_and_energy_variants(tmp_path):
    path = tmp_path / "ensemble.xyz"
    path.write_text(_xyz(["-10.0", "energy = -9.999 Eh"]), encoding="utf-8")
    frames = ensemble.parse_crest_xyz(path)
    assert [frame.energy_hartree for frame in frames] == [-10.0, -9.999]
    assert [frame.atom_count for frame in frames] == [2, 2]


@pytest.mark.parametrize("text, error", [("bad\n", "invalid atom count"), ("2\n-1\nH 0 0 0\n", "truncated"), ("1\n-1\nH nan 0 0\n", "non-finite"), ("1\n-1\nH 0 0 0\n2\n-1\nH 0 0 0\nH 0 0 1\n", "atom count")])
def test_malformed_xyz_is_rejected(tmp_path, text, error):
    path = tmp_path / "bad.xyz"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ensemble.CrestEnsembleError, match=error):
        ensemble.parse_crest_xyz(path)


def test_missing_energy_is_rejected(tmp_path):
    path = tmp_path / "bad.xyz"
    path.write_text(_xyz(["generated"]), encoding="utf-8")
    with pytest.raises(ensemble.CrestEnsembleError, match="missing.*energy"):
        ensemble.parse_crest_xyz(path)


def test_boltzmann_normalization_entropy_and_windows():
    unit = 1 / ensemble.HARTREE_TO_KCAL_MOL
    stats = ensemble.boltzmann_statistics([0, unit, 3 * unit, 6 * unit])
    assert math.fsum(stats["boltzmann_weights"]) == pytest.approx(1, abs=1e-15)
    assert [stats[f"conformer_count_{window}kcal"] for window in (1, 3, 6)] == [2, 3, 4]
    assert stats["dominant_conformer_index"] == 1
    assert 1 < stats["effective_conformer_count"] < 4
    assert stats["conformational_entropy_J_mol_K"] > 0


def test_completed_state_generates_summary(tmp_path):
    _completed(tmp_path, _xyz(["-5", "-4.9995"]))
    row = ensemble.build_component_summary(_task(), tmp_path).iloc[0]
    assert row["analysis_status"] == "analyzed"
    assert row["conformer_count"] == 2
    assert row["boltzmann_weight_sum"] == pytest.approx(1)


@pytest.mark.parametrize("status", ["pending", "running", "failed"])
def test_noncompleted_state_has_no_metrics(tmp_path, status):
    task_root = tmp_path / "0000_ready"
    task_root.mkdir()
    (task_root / "运行状态.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    row = ensemble.build_component_summary(_task(), tmp_path).iloc[0]
    assert row["analysis_status"] == "not_analyzed"
    assert pd.isna(row["conformer_count"])
    assert pd.isna(row["minimum_energy_hartree"])


def test_missing_frame_energy_fails_closed(tmp_path):
    _completed(tmp_path, _xyz(["no energy"] ))
    row = ensemble.build_component_summary(_task(), tmp_path).iloc[0]
    assert row["analysis_status"] == "rejected"
    assert pd.isna(row["conformer_count"])


def test_hash_mismatch_and_path_escape_fail_closed(tmp_path):
    _completed(tmp_path, _xyz(["-1"]))
    state_path = tmp_path / "0000_ready" / "运行状态.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["output_sha256"] = "wrong"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    assert ensemble.build_component_summary(_task(), tmp_path).iloc[0]["analysis_issue"] == "output_sha256 mismatch"
    state["conformer_output"] = "../../outside.xyz"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    row = ensemble.build_component_summary(_task(), tmp_path).iloc[0]
    assert row["analysis_status"] == "rejected"
    assert "escapes" in row["analysis_issue"]


def test_blocked_state_is_retained_without_imputation(tmp_path):
    task_root = tmp_path / "0000_ready"
    task_root.mkdir()
    (task_root / "运行状态.json").write_text(json.dumps({"status": "blocked_input_geometry", "failure_reason": "embedding failed"}), encoding="utf-8")
    row = ensemble.build_component_summary(_task(), tmp_path).iloc[0]
    assert row["analysis_status"] == "blocked_input_geometry"
    assert pd.isna(row["effective_conformer_count"])


def test_empty_missing_and_additional_malformed_inputs_are_rejected(tmp_path):
    with pytest.raises(ensemble.CrestEnsembleError, match="missing conformer"):
        ensemble.parse_crest_xyz(tmp_path / "missing.xyz")
    for name, text, error in (
        ("empty.xyz", "\n\n", "no XYZ frames"),
        ("zero.xyz", "0\n-1\n", "positive"),
        ("short-row.xyz", "1\n-1\nH 0 0\n", "invalid atom row"),
        ("bad-coordinate.xyz", "1\n-1\nH nope 0 0\n", "invalid coordinate"),
    ):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ensemble.CrestEnsembleError, match=error):
            ensemble.parse_crest_xyz(path)


def test_boltzmann_rejects_invalid_energy_or_temperature():
    for values, temperature, error in (
        ([], 298.15, "non-empty"),
        ([math.nan], 298.15, "finite"),
        ([0.0], 0.0, "temperature"),
    ):
        with pytest.raises(ensemble.CrestEnsembleError, match=error):
            ensemble.boltzmann_statistics(values, temperature)


@pytest.mark.parametrize(
    "state_text, expected_status",
    [("{broken", "invalid_state"), ("[]", "invalid_state"), ("{}", "invalid_state")],
)
def test_invalid_state_json_is_closed(tmp_path, state_text, expected_status):
    task_root = tmp_path / "0000_ready"
    task_root.mkdir()
    (task_root / "运行状态.json").write_text(state_text, encoding="utf-8")
    row = ensemble.build_component_summary(_task(), tmp_path).iloc[0]
    assert row["run_status"] == expected_status
    assert row["analysis_status"] == "not_analyzed"
    assert pd.isna(row["conformer_count"])


@pytest.mark.parametrize(
    "mutation, expected",
    [
        ({"candidate_id": "other"}, "identity mismatch"),
        ({"input_sha256": "other"}, "input_sha256 mismatch"),
        ({"conformer_output": "尝试_001/missing.xyz"}, "missing conformer file"),
        ({"output_sha256": ""}, "missing output_sha256"),
    ],
)
def test_completed_state_identity_and_output_gates(tmp_path, mutation, expected):
    _completed(tmp_path, _xyz(["-1"]))
    state_path = tmp_path / "0000_ready" / "运行状态.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(mutation)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    row = ensemble.build_component_summary(_task(), tmp_path).iloc[0]
    assert row["analysis_status"] == "rejected"
    assert expected in row["analysis_issue"]
    assert pd.isna(row["conformer_count"])


def test_task_table_schema_and_uniqueness_gates(tmp_path):
    with pytest.raises(ensemble.CrestEnsembleError, match="missing required fields"):
        ensemble.build_component_summary(pd.DataFrame({"x": [1]}), tmp_path)
    duplicate = pd.concat([_task(), _task()], ignore_index=True)
    with pytest.raises(ensemble.CrestEnsembleError, match="not unique"):
        ensemble.build_component_summary(duplicate, tmp_path)
    with pytest.raises(ensemble.CrestEnsembleError, match="missing required field"):
        ensemble.analyze_task({}, tmp_path)


def test_cli_writes_machine_readable_summary(tmp_path, monkeypatch, capsys):
    result_root = tmp_path / "results"
    _completed(result_root, _xyz(["-1"]))
    tasks_path, output = tmp_path / "tasks.csv", tmp_path / "out" / "summary.csv"
    _task().to_csv(tasks_path, index=False)
    monkeypatch.setattr(sys, "argv", ["CREST系综分析.py", "--任务清单", str(tasks_path), "--结果目录", str(result_root), "--输出", str(output)])
    ensemble.main()
    assert output.is_file()
    assert pd.read_csv(output)["analysis_status"].tolist() == ["analyzed"]
    assert "analyzed" in capsys.readouterr().out
