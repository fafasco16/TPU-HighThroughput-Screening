import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import xTB系综任务 as tasks
import 运行xTB构象任务 as runner


def _ensemble() -> str:
    return (
        "3\n-10.0 first\nO 0 0 0\nC 1 0 0\nH 0 1 0\n"
        "3\n-9.999 second\nO 0 0 0.1\nC 1 0 0\nH 0 1 0\n"
    )


def _crest_fixture(root: Path, *, state_status: str = "completed") -> tuple[pd.DataFrame, Path]:
    result_root = root / "crest-results"
    task_root = result_root / "0000_component"
    attempt = task_root / "尝试_001"
    attempt.mkdir(parents=True)
    ensemble = attempt / "crest_conformers.xyz"
    ensemble.write_text(_ensemble(), encoding="utf-8")
    task_table = pd.DataFrame(
        [
            {
                "task_index": 0,
                "task_slug": "0000_component",
                "candidate_id": "component",
                "component_role": "chain_extender",
                "initial_xyz_sha256": "a" * 64,
                "charge": 0,
                "uhf": 0,
            }
        ]
    )
    state = {
        "status": state_status,
        "task_slug": "0000_component",
        "candidate_id": "component",
        "component_role": "chain_extender",
        "input_sha256": "a" * 64,
        "conformer_output": "尝试_001/crest_conformers.xyz",
        "output_sha256": tasks.sha256(ensemble),
    }
    (task_root / "运行状态.json").write_text(json.dumps(state), encoding="utf-8")
    return task_table, result_root


def _materialized(tmp_path: Path) -> Path:
    source, results = _crest_fixture(tmp_path)
    root = tmp_path / "xtb"
    table = tasks.build_conformer_tasks(
        source,
        results,
        root,
        descriptor_release_id="xtb-test-v1",
        xtb_version="6.7.1",
        xtb_binary_sha256="b" * 64,
    )
    table.to_csv(root / "xTB构象任务清单.csv", index=False)
    return root


def test_strict_split_and_stable_materialization(tmp_path):
    source, results = _crest_fixture(tmp_path)
    output = tmp_path / "xtb"
    first = tasks.build_conformer_tasks(
        source,
        results,
        output,
        descriptor_release_id="release-1",
        xtb_version="6.7.1",
        xtb_binary_sha256="b" * 64,
    )
    second = tasks.build_conformer_tasks(
        source,
        results,
        output,
        descriptor_release_id="release-1",
        xtb_version="6.7.1",
        xtb_binary_sha256="b" * 64,
    )
    assert len(first) == 2
    assert first.equals(second)
    assert first["conformer_id"].is_unique
    assert first["crest_rank"].tolist() == [1, 2]
    assert first["atom_order_sha256"].nunique() == 1
    for row in first.itertuples(index=False):
        path = output / row.conformer_xyz_file
        assert len(tasks.split_crest_xyz(path)) == 1
        assert tasks.sha256(path) == row.conformer_xyz_sha256


@pytest.mark.parametrize(
    "text,error",
    [
        ("3\n-1\nO 0 0 0\nC 1 0 0\n", "truncated"),
        ("1\nno-energy\nH 0 0 0\n", "energy"),
        ("1\n-1\nH nan 0 0\n", "non-finite"),
        (
            "2\n-1\nO 0 0 0\nH 0 0 1\n2\n-0.9\nH 0 0 1\nO 0 0 0\n",
            "atom order",
        ),
    ],
)
def test_missing_or_invalid_frame_fails_closed(tmp_path, text, error):
    path = tmp_path / "bad.xyz"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(tasks.XtbTaskError, match=error):
        tasks.split_crest_xyz(path)


def test_noncompleted_source_is_not_materialized(tmp_path):
    source, results = _crest_fixture(tmp_path, state_status="running")
    table = tasks.build_conformer_tasks(
        source,
        results,
        tmp_path / "xtb",
        descriptor_release_id="release-1",
        xtb_version="6.7.1",
        xtb_binary_sha256="b" * 64,
    )
    assert table.empty


def test_completed_source_hash_or_identity_mismatch_is_rejected(tmp_path):
    source, results = _crest_fixture(tmp_path)
    state_path = results / "0000_component" / "运行状态.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["output_sha256"] = "0" * 64
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(tasks.XtbTaskError, match="SHA-256"):
        tasks.build_conformer_tasks(
            source,
            results,
            tmp_path / "xtb",
            descriptor_release_id="release-1",
            xtb_version="6.7.1",
            xtb_binary_sha256="b" * 64,
        )


def _fake_executable(root: Path) -> Path:
    executable = root / "xtb-fixture"
    executable.write_bytes(b"fixture executable")
    return executable


def _bind_executable_identity(root: Path, executable: Path) -> None:
    table_path = root / "xTB构象任务清单.csv"
    table = pd.read_csv(table_path)
    table["xtb_binary_sha256"] = tasks.sha256(executable)
    table.to_csv(table_path, index=False)


def test_runner_success_then_hash_verified_skip(tmp_path, monkeypatch):
    root = _materialized(tmp_path)
    executable = _fake_executable(tmp_path)
    _bind_executable_identity(root, executable)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if "--version" in command:
            return SimpleNamespace(returncode=0, stdout="xtb version 6.7.1", stderr="")
        work = Path(kwargs["cwd"])
        (work / "xtbout.json").write_text(
            json.dumps({"total energy": -10.0}), encoding="utf-8"
        )
        (work / "wbo").write_text("1 2 1.0\n", encoding="utf-8")
        kwargs["stdout"].write("normal termination of xtb\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    state = runner.run_task(root, 0, str(executable))
    assert state["status"] == "completed"
    assert "--sp" in state["command"] and "--norestart" in state["command"]
    assert state["command"][-2:] == ["-P", "1"]
    first_calls = len(calls)
    skipped = runner.run_task(root, 0, str(executable))
    assert skipped["status"] == "completed"
    assert len(calls) == first_calls
    row = pd.read_csv(root / "xTB构象任务清单.csv").iloc[0]
    layout = runner.task_layout(root, row)
    assert layout["archive"].is_file()
    assert not layout["work"].exists()
    assert not layout["lock"].exists()
    assert not layout["state"].with_name(layout["state"].name + ".tmp").exists()


@pytest.mark.parametrize("mode,reason", [("exit", "nonzero"), ("missing", "missing_required")])
def test_runner_records_failure_without_false_completion(tmp_path, monkeypatch, mode, reason):
    root = _materialized(tmp_path)
    executable = _fake_executable(tmp_path)
    _bind_executable_identity(root, executable)

    def fake_run(command, **kwargs):
        if "--version" in command:
            return SimpleNamespace(returncode=0, stdout="xtb version 6.7.1", stderr="")
        if mode == "missing":
            (Path(kwargs["cwd"]) / "xtbout.json").write_text(
                json.dumps({"total energy": -1}), encoding="utf-8"
            )
            kwargs["stdout"].write("normal termination of xtb\n")
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    with pytest.raises(runner.XtbRunError, match="xTB task failed"):
        runner.run_task(root, 0, str(executable))
    task = pd.read_csv(root / "xTB构象任务清单.csv").iloc[0]
    layout = runner.task_layout(root, task)
    state_path = layout["state"]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert reason in state["failure_reason"]
    assert layout["work"].is_dir()


def test_runner_rejects_missing_or_multiframe_input_before_subprocess(tmp_path, monkeypatch):
    root = _materialized(tmp_path)
    executable = _fake_executable(tmp_path)
    _bind_executable_identity(root, executable)
    table_path = root / "xTB构象任务清单.csv"
    table = pd.read_csv(table_path)
    input_path = root / table.loc[0, "conformer_xyz_file"]
    input_path.write_text(_ensemble(), encoding="utf-8")
    table.loc[0, "conformer_xyz_sha256"] = tasks.sha256(input_path)
    table.to_csv(table_path, index=False)
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("subprocess must not run for invalid input"),
    )
    with pytest.raises(runner.XtbRunError, match="exactly one"):
        runner.run_task(root, 0, str(executable))


def test_version_and_manifest_identity_gates(tmp_path):
    source, results = _crest_fixture(tmp_path)
    with pytest.raises(tasks.XtbTaskError, match="version"):
        tasks.build_conformer_tasks(
            source,
            results,
            tmp_path / "xtb",
            descriptor_release_id="release-1",
            xtb_version="6.6.1",
            xtb_binary_sha256="b" * 64,
        )
    with pytest.raises(tasks.XtbTaskError, match="SHA-256"):
        tasks.build_conformer_tasks(
            source,
            results,
            tmp_path / "xtb",
            descriptor_release_id="release-1",
            xtb_version="6.7.1",
            xtb_binary_sha256="bad",
        )


@pytest.mark.parametrize(
    "text,error",
    [
        ("\n\n", "no XYZ frames"),
        ("bad\n", "invalid atom count"),
        ("0\n-1\n", "positive"),
        ("1\n-1\nxx 0 0 0\n", "invalid atom row"),
        ("1\n-1\nH bad 0 0\n", "invalid coordinate"),
        ("1\n\nH 0 0 0\n", "missing CREST energy"),
    ],
)
def test_additional_xyz_gates(tmp_path, text, error):
    path = tmp_path / "bad-extra.xyz"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(tasks.XtbTaskError, match=error):
        tasks.split_crest_xyz(path)


def test_missing_file_and_invalid_task_schema_are_rejected(tmp_path):
    with pytest.raises(tasks.XtbTaskError, match="missing CREST ensemble"):
        tasks.split_crest_xyz(tmp_path / "absent.xyz")
    with pytest.raises(tasks.XtbTaskError, match="missing fields"):
        tasks.build_conformer_tasks(
            pd.DataFrame({"x": [1]}),
            tmp_path,
            tmp_path / "out",
            descriptor_release_id="r",
            xtb_version="6.7.1",
            xtb_binary_sha256="b" * 64,
        )
    source, results = _crest_fixture(tmp_path / "duplicate")
    duplicate = pd.concat([source, source], ignore_index=True)
    with pytest.raises(tasks.XtbTaskError, match="not unique"):
        tasks.build_conformer_tasks(
            duplicate,
            results,
            tmp_path / "out2",
            descriptor_release_id="r",
            xtb_version="6.7.1",
            xtb_binary_sha256="b" * 64,
        )
    with pytest.raises(tasks.XtbTaskError, match="must not be empty"):
        tasks.build_conformer_tasks(
            source,
            results,
            tmp_path / "out3",
            descriptor_release_id=" ",
            xtb_version="6.7.1",
            xtb_binary_sha256="b" * 64,
        )


@pytest.mark.parametrize(
    "mutation,error",
    [
        ({"candidate_id": "other"}, "identity mismatch"),
        ({"input_sha256": "other"}, "input_sha256 mismatch"),
        ({"conformer_output": "../../escape.xyz"}, "escapes"),
        ({"conformer_output": "missing.xyz"}, "missing ensemble"),
        ({"conformer_output": ""}, "missing conformer_output"),
    ],
)
def test_completed_state_metadata_gates(tmp_path, mutation, error):
    source, results = _crest_fixture(tmp_path)
    state_path = results / "0000_component" / "运行状态.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(mutation)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(tasks.XtbTaskError, match=error):
        tasks.build_conformer_tasks(
            source,
            results,
            tmp_path / "xtb",
            descriptor_release_id="r",
            xtb_version="6.7.1",
            xtb_binary_sha256="b" * 64,
        )


def test_generator_cli_writes_manifest(tmp_path, monkeypatch, capsys):
    source, results = _crest_fixture(tmp_path)
    source_path = tmp_path / "crest.csv"
    source.to_csv(source_path, index=False)
    output = tmp_path / "xtb"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "xTB系综任务.py",
            "--CREST任务清单",
            str(source_path),
            "--CREST结果目录",
            str(results),
            "--输出目录",
            str(output),
            "--发布ID",
            "cli-v1",
            "--xTB二进制SHA256",
            "b" * 64,
        ],
    )
    tasks.main()
    assert len(pd.read_csv(output / "xTB构象任务清单.csv")) == 2
    assert "xtb_conformer_tasks" in capsys.readouterr().out


def test_runner_cli_delegates_arguments(tmp_path, monkeypatch):
    captured = {}

    def fake_run(root, index, executable):
        captured.update(root=root, index=index, executable=executable)

    monkeypatch.setattr(runner, "run_task", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["运行xTB构象任务.py", "--根目录", str(tmp_path), "--索引", "7", "--xtb", "x"],
    )
    runner.main()
    assert captured == {"root": tmp_path.resolve(), "index": 7, "executable": "x"}


def test_runner_manifest_and_executable_gates(tmp_path):
    root = _materialized(tmp_path)
    table_path = root / "xTB构象任务清单.csv"
    full = pd.read_csv(table_path)
    full.drop(columns=["method"]).to_csv(table_path, index=False)
    with pytest.raises(runner.XtbRunError, match="missing fields"):
        runner.run_task(root, 0, "absent")
    full.to_csv(table_path, index=False)
    with pytest.raises(runner.XtbRunError, match="absent"):
        runner.run_task(root, 99, "absent")
    with pytest.raises(runner.XtbRunError, match="executable not found"):
        runner.run_task(root, 0, str(tmp_path / "absent"))


def test_runner_method_environment_temperature_and_binary_gates(tmp_path):
    root = _materialized(tmp_path)
    executable = _fake_executable(tmp_path)
    table_path = root / "xTB构象任务清单.csv"
    original = pd.read_csv(table_path)
    for column, value, error in (
        ("method", "other", "method/version"),
        ("environment_model", "ALPB_thf", "gas_phase"),
        ("electronic_temperature_k", 301, "300 K"),
        ("xtb_binary_sha256", "0" * 64, "binary SHA-256"),
    ):
        changed = original.copy()
        changed.loc[0, column] = value
        if column != "xtb_binary_sha256":
            changed["xtb_binary_sha256"] = tasks.sha256(executable)
        changed.to_csv(table_path, index=False)
        with pytest.raises(runner.XtbRunError, match=error):
            runner.run_task(root, 0, str(executable))


def test_runner_version_scc_and_invalid_json_fail_closed(tmp_path, monkeypatch):
    for mode in ("version", "scc", "json"):
        case = tmp_path / mode
        root = _materialized(case)
        executable = _fake_executable(case)
        _bind_executable_identity(root, executable)

        def fake_run(command, **kwargs):
            if "--version" in command:
                text = "xtb version 6.6.1" if mode == "version" else "xtb version 6.7.1"
                return SimpleNamespace(returncode=0, stdout=text, stderr="")
            work = Path(kwargs["cwd"])
            (work / "xtbout.json").write_text(
                "{broken" if mode == "json" else json.dumps({"total energy": -1}),
                encoding="utf-8",
            )
            (work / "wbo").write_text("1 2 1\n", encoding="utf-8")
            kwargs["stdout"].write("normal termination of xtb\n")
            if mode == "scc":
                (work / ".sccnotconverged").write_text("", encoding="utf-8")
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        error = "version gate" if mode == "version" else "xTB task failed"
        with pytest.raises(runner.XtbRunError, match=error):
            runner.run_task(root, 0, str(executable))


def test_lock_and_archive_path_reject_unsafe_state(tmp_path):
    lock = tmp_path / "locks" / "task.lock"
    with runner.task_lock(lock):
        with pytest.raises(runner.XtbRunError, match="already locked"):
            with runner.task_lock(lock):
                pass
    assert not lock.exists()
    assert runner._safe_archive_path(tmp_path, "../../escape.tar.gz") is None
    assert runner._safe_archive_path(tmp_path, None) is None
    with pytest.raises(runner.XtbRunError, match="invalid conformer_id"):
        runner._task_shard("bad-id")
    unsafe_row = pd.Series(
        {"xtb_task_slug": "../../escape", "conformer_id": "cf_" + "a" * 20}
    )
    with pytest.raises(runner.XtbRunError, match="invalid xtb_task_slug"):
        runner.task_layout(tmp_path, unsafe_row)
    with pytest.raises(runner.XtbRunError, match="outside controlled root"):
        runner._remove_verified_work_root(tmp_path, tmp_path.parent)
    unexpected = tmp_path / "工作" / "not-a-shard" / "task"
    unexpected.mkdir(parents=True)
    with pytest.raises(runner.XtbRunError, match="unexpected work directory"):
        runner._remove_verified_work_root(tmp_path, unexpected)
