import hashlib
import json
import sys
import tarfile
from pathlib import Path

import pandas as pd
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

import 汇总xTB结果包 as aggregate
from xTB系综任务 import atom_order_sha256


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _molecule_payload(smiles: str) -> tuple[dict[str, bytes], tuple[str, ...]]:
    molecule = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(molecule, randomSeed=17) == 0
    conformer = molecule.GetConformer()
    symbols = tuple(atom.GetSymbol() for atom in molecule.GetAtoms())
    xyz_lines = [str(len(symbols)), "-10.0"]
    for index, symbol in enumerate(symbols):
        point = conformer.GetAtomPosition(index)
        xyz_lines.append(f"{symbol} {point.x:.10f} {point.y:.10f} {point.z:.10f}")
    xyz = ("\n".join(xyz_lines) + "\n").encode()
    xtbout = json.dumps(
        {
            "method": "GFN2-xTB",
            "xtb version": "6.7.1 (fixture)",
            "total energy": -10.0,
            "HOMO-LUMO gap / eV": 2.0,
            "orbital energies / eV": [-10.0, -1.0, 1.0, 5.0],
            "fractional occupation": [2.0, 2.0, 0.0, 0.0],
            "dipole / a.u.": [0.1, 0.2, 0.3],
            "partial charges": [0.0] * len(symbols),
        }
    ).encode()
    stdout = ["Mol. α(0) /au : 12.5", "# Z covCN q C6AA α(0)"]
    for index, atom in enumerate(molecule.GetAtoms(), start=1):
        stdout.append(f"{index} {atom.GetAtomicNum()} 1 0.0 0.0 0.0 {1 + index / 10:.3f}")
    stdout.extend(["", "normal termination of xtb"])
    wbo = []
    for bond in molecule.GetBonds():
        wbo.append(
            f"{bond.GetBeginAtomIdx() + 1} {bond.GetEndAtomIdx() + 1} 1.000000"
        )
    return {
        "conformer.xyz": xyz,
        "xtbout.json": xtbout,
        "xtb.out": ("\n".join(stdout) + "\n").encode(),
        "wbo": ("\n".join(wbo) + "\n").encode(),
    }, symbols


def _install_task(
    root: Path,
    *,
    source_index: int,
    rank: int,
    source_slug: str,
    candidate: str,
    role: str,
    smiles: str,
    status: str = "completed",
    members_override: dict[str, bytes] | None = None,
) -> tuple[dict, dict]:
    members, symbols = _molecule_payload(smiles)
    if members_override is not None:
        members = members_override
    digest = hashlib.sha256(f"{source_slug}:{rank}".encode()).hexdigest()[:20]
    conformer_id = f"cf_{digest}"
    slug = f"{source_index:04d}_{rank:06d}_{conformer_id}"
    task = {
        "xtb_task_index": source_index * 1_000_000 + rank - 1,
        "xtb_task_slug": slug,
        "source_task_slug": source_slug,
        "candidate_id": candidate,
        "component_role": role,
        "conformer_id": conformer_id,
        "crest_rank": rank,
        "conformer_xyz_sha256": _sha_bytes(members.get("conformer.xyz", b"")),
        "atom_count": len(symbols),
        "atom_order_sha256": atom_order_sha256(symbols),
        "charge": 0,
        "uhf": 0,
        "xtb_version": "6.7.1",
        "xtb_binary_sha256": "a" * 64,
        "method": "GFN2-xTB",
        "environment_model": "gas_phase",
    }
    shard = digest[:2]
    state_path = root / "状态" / shard / f"{slug}.json"
    archive_path = root / "结果包" / shard / f"{slug}.tar.gz"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    member_hashes = {name: _sha_bytes(value) for name, value in members.items()}
    if status == "completed":
        with tarfile.open(archive_path, "w:gz") as archive:
            for name, value in members.items():
                source = root / f"fixture-{name}"
                source.write_bytes(value)
                archive.add(source, arcname=name)
                source.unlink()
        archive_hash = aggregate.sha256(archive_path)
    else:
        archive_hash = ""
    state = {
        "status": status,
        "xtb_task_index": task["xtb_task_index"],
        "xtb_task_slug": slug,
        "candidate_id": candidate,
        "component_role": role,
        "conformer_id": conformer_id,
        "input_sha256": task["conformer_xyz_sha256"],
        "atom_order_sha256": task["atom_order_sha256"],
        "charge": 0,
        "uhf": 0,
        "xtb_version": "6.7.1",
        "xtb_binary_sha256": "a" * 64,
        "method": "GFN2-xTB",
        "environment_model": "gas_phase",
        "archive_file": f"结果包/{shard}/{slug}.tar.gz",
        "archive_sha256": archive_hash,
        "archive_member_sha256": member_hashes,
        "output_sha256": {
            name: value
            for name, value in member_hashes.items()
            if name != "conformer.xyz"
        },
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return task, state


def _tables(tasks: list[dict], sources: list[tuple[str, str]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    return pd.DataFrame(tasks), pd.DataFrame(
        {"task_slug": [item[0] for item in sources], "canonical_smiles": [item[1] for item in sources]}
    )


def test_normal_diol_and_diisocyanate_are_parsed_and_aggregated(tmp_path: Path):
    root = tmp_path / "results"
    diol, _ = _install_task(
        root, source_index=0, rank=1, source_slug="crest-diol", candidate="diol",
        role="chain_extender", smiles="OCCO",
    )
    diiso, _ = _install_task(
        root, source_index=1, rank=1, source_slug="crest-nco", candidate="nco",
        role="diisocyanate", smiles="O=C=NCCN=C=O",
    )
    tasks, sources = _tables(
        [diol, diiso], [("crest-diol", "OCCO"), ("crest-nco", "O=C=NCCN=C=O")]
    )
    rows, components, failures = aggregate.build_summaries(tasks, root, sources)
    assert len(rows) == 2 and failures.empty
    assert set(rows["reactive_site_kind"]) == {"hydroxyl_oxygen", "nco_carbon"}
    assert rows["site_1_charge_e"].notna().all()
    assert rows["site_1_atomic_alpha0_au"].notna().all()
    assert rows["site_1_incident_wbo_sum"].gt(0).all()
    assert rows["site_1_relative_sasa"].between(0, 1).all()
    assert components["complete_weighted_release"].all()
    assert components["boltzmann_weight_sum"].eq(1.0).all()


def test_incomplete_component_closes_all_weights(tmp_path: Path):
    root = tmp_path / "results"
    first, _ = _install_task(
        root, source_index=2, rank=1, source_slug="crest-two", candidate="two",
        role="chain_extender", smiles="OCCO",
    )
    second, _ = _install_task(
        root, source_index=2, rank=2, source_slug="crest-two", candidate="two",
        role="chain_extender", smiles="OCCO", status="failed",
    )
    tasks, sources = _tables([first, second], [("crest-two", "OCCO")])
    rows, components, failures = aggregate.build_summaries(tasks, root, sources)
    assert len(rows) == 2 and len(failures) == 1
    assert components.iloc[0]["ensemble_status"] == "incomplete"
    assert not bool(components.iloc[0]["complete_weighted_release"])
    assert pd.isna(components.iloc[0]["boltzmann_weight_sum"])
    assert rows["boltzmann_proxy_weight_298K"].isna().all()


def test_archive_path_traversal_is_rejected(tmp_path: Path):
    root = tmp_path / "results"
    task, state = _install_task(
        root, source_index=3, rank=1, source_slug="crest", candidate="x",
        role="chain_extender", smiles="OCCO",
    )
    state["archive_file"] = "../escape.tar.gz"
    shard = task["conformer_id"][3:5]
    (root / "状态" / shard / f"{task['xtb_task_slug']}.json").write_text(
        json.dumps(state), encoding="utf-8"
    )
    tasks, sources = _tables([task], [("crest", "OCCO")])
    rows, components, failures = aggregate.build_summaries(tasks, root, sources)
    assert rows.iloc[0]["run_status"] == "failed"
    assert "path mismatch" in failures.iloc[0]["failure_message"]
    assert components.iloc[0]["ensemble_status"] == "incomplete"


def test_archive_and_member_hash_mismatch_are_rejected(tmp_path: Path):
    root = tmp_path / "archive"
    task, state = _install_task(
        root, source_index=4, rank=1, source_slug="crest", candidate="x",
        role="chain_extender", smiles="OCCO",
    )
    shard = task["conformer_id"][3:5]
    state_path = root / "状态" / shard / f"{task['xtb_task_slug']}.json"
    state["archive_sha256"] = "0" * 64
    state_path.write_text(json.dumps(state), encoding="utf-8")
    tasks, sources = _tables([task], [("crest", "OCCO")])
    _, _, failures = aggregate.build_summaries(tasks, root, sources)
    assert "archive_sha256 mismatch" in failures.iloc[0]["failure_message"]

    task2, state2 = _install_task(
        root, source_index=5, rank=1, source_slug="crest2", candidate="y",
        role="chain_extender", smiles="OCCO",
    )
    shard2 = task2["conformer_id"][3:5]
    state2["archive_member_sha256"]["wbo"] = "f" * 64
    state2["output_sha256"]["wbo"] = "f" * 64
    (root / "状态" / shard2 / f"{task2['xtb_task_slug']}.json").write_text(
        json.dumps(state2), encoding="utf-8"
    )
    tasks2, sources2 = _tables([task2], [("crest2", "OCCO")])
    _, _, failures2 = aggregate.build_summaries(tasks2, root, sources2)
    assert "member SHA-256 mismatch" in failures2.iloc[0]["failure_message"]


def test_missing_required_member_is_rejected(tmp_path: Path):
    root = tmp_path / "results"
    members, _ = _molecule_payload("OCCO")
    members.pop("wbo")
    task, _ = _install_task(
        root, source_index=6, rank=1, source_slug="crest", candidate="x",
        role="chain_extender", smiles="OCCO", members_override=members,
    )
    tasks, sources = _tables([task], [("crest", "OCCO")])
    _, components, failures = aggregate.build_summaries(tasks, root, sources)
    assert "missing required member" in failures.iloc[0]["failure_message"]
    assert components.iloc[0]["failure_count"] == 1


def test_streaming_writer_outputs_three_atomic_tables(tmp_path: Path):
    root = tmp_path / "results"
    task, _ = _install_task(
        root, source_index=7, rank=1, source_slug="crest", candidate="x",
        role="chain_extender", smiles="OCCO",
    )
    tasks, sources = _tables([task], [("crest", "OCCO")])
    outputs = [tmp_path / "conformers.csv", tmp_path / "components.csv", tmp_path / "failures.csv"]
    counts = aggregate.write_aggregate_outputs(tasks, root, sources, *outputs)
    assert counts == {"conformers": 1, "components": 1, "failures": 0}
    assert len(pd.read_csv(outputs[0])) == 1
    assert len(pd.read_csv(outputs[1])) == 1
    assert pd.read_csv(outputs[2]).empty
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(
    "tasks,sources,error",
    [
        (pd.DataFrame({"x": [1]}), pd.DataFrame({"task_slug": [], "canonical_smiles": []}), "缺少字段"),
        (
            pd.DataFrame(
                {
                    "xtb_task_index": [1, 1], "xtb_task_slug": ["a", "b"],
                    "source_task_slug": ["s", "s"], "candidate_id": ["x", "x"],
                    "component_role": ["chain_extender"] * 2, "conformer_id": ["c", "d"],
                    "crest_rank": [1, 2], "conformer_xyz_sha256": ["a"] * 2,
                    "atom_count": [1] * 2, "atom_order_sha256": ["b"] * 2,
                    "charge": [0] * 2, "uhf": [0] * 2, "xtb_version": ["6.7.1"] * 2,
                    "xtb_binary_sha256": ["c"] * 2, "method": ["GFN2-xTB"] * 2,
                    "environment_model": ["gas_phase"] * 2,
                }
            ),
            pd.DataFrame({"task_slug": ["s"], "canonical_smiles": ["OCCO"]}),
            "不唯一",
        ),
    ],
)
def test_input_schema_failures(tasks, sources, error):
    with pytest.raises(aggregate.XtbArchiveAggregateError, match=error):
        list(aggregate.iter_component_results(tasks, Path("."), sources))


def test_scalar_pair_and_source_table_guards():
    assert aggregate._scalar(pd.Series([2], dtype="int64").iloc[0]) == 2
    with pytest.raises(aggregate.XtbArchiveAggregateError, match="non-scalar"):
        aggregate._scalar([1, 2])
    assert aggregate._aggregate_pair("x", [1.0, None]) == {
        "x_mean": None, "x_min": None, "x_max": None, "x_abs_difference": None
    }
    task = {
        "xtb_task_index": 1, "xtb_task_slug": "a", "source_task_slug": "missing",
        "candidate_id": "x", "component_role": "chain_extender", "conformer_id": "c",
        "crest_rank": 1, "conformer_xyz_sha256": "a", "atom_count": 1,
        "atom_order_sha256": "b", "charge": 0, "uhf": 0, "xtb_version": "6.7.1",
        "xtb_binary_sha256": "c", "method": "GFN2-xTB", "environment_model": "gas_phase",
    }
    with pytest.raises(aggregate.XtbArchiveAggregateError, match="缺少CREST源记录"):
        aggregate._validate_inputs(
            pd.DataFrame([task]), pd.DataFrame({"task_slug": ["s"], "canonical_smiles": ["OCCO"]})
        )
    task["source_task_slug"] = "s"
    with pytest.raises(aggregate.XtbArchiveAggregateError, match="canonical_smiles为空"):
        aggregate._validate_inputs(
            pd.DataFrame([task]), pd.DataFrame({"task_slug": ["s"], "canonical_smiles": [""]})
        )
    with pytest.raises(aggregate.XtbArchiveAggregateError, match="task_slug不唯一"):
        aggregate._validate_inputs(
            pd.DataFrame([task]),
            pd.DataFrame({"task_slug": ["s", "s"], "canonical_smiles": ["OCCO", "OCCO"]}),
        )


def test_layout_and_json_object_guards(tmp_path: Path):
    with pytest.raises(aggregate.XtbArchiveAggregateError, match="invalid conformer_id"):
        aggregate._task_shard("bad")
    with pytest.raises(aggregate.XtbArchiveAggregateError, match="invalid xtb_task_slug"):
        aggregate._layout(tmp_path, {"xtb_task_slug": "bad", "conformer_id": "cf_" + "a" * 20})
    with pytest.raises(aggregate.XtbArchiveAggregateError, match="missing state"):
        aggregate._read_json_object(tmp_path / "missing.json", "state")
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(aggregate.XtbArchiveAggregateError, match="invalid state"):
        aggregate._read_json_object(invalid, "state")
    invalid.write_text("[]", encoding="utf-8")
    with pytest.raises(aggregate.XtbArchiveAggregateError, match="root must be an object"):
        aggregate._read_json_object(invalid, "state")


@pytest.mark.parametrize(
    "mutation,error",
    [
        (lambda state: state.update(candidate_id="wrong"), "identity mismatch"),
        (lambda state: state.update(archive_sha256="bad"), "invalid archive_sha256"),
        (lambda state: state.update(archive_member_sha256=None), "missing archive_member"),
        (
            lambda state: state["archive_member_sha256"].update({"evil": "a" * 64}),
            "unknown member",
        ),
        (
            lambda state: state["archive_member_sha256"].update({"wbo": "bad"}),
            "invalid archive member",
        ),
        (
            lambda state: state["archive_member_sha256"].update({"conformer.xyz": "b" * 64}),
            "differs from task input",
        ),
        (lambda state: state.update(output_sha256=None), "output/member"),
    ],
)
def test_completed_state_fail_closed_variants(tmp_path: Path, mutation, error):
    root = tmp_path / "results"
    task, state = _install_task(
        root, source_index=8, rank=1, source_slug="crest", candidate="x",
        role="chain_extender", smiles="OCCO",
    )
    mutation(state)
    shard = task["conformer_id"][3:5]
    (root / "状态" / shard / f"{task['xtb_task_slug']}.json").write_text(
        json.dumps(state), encoding="utf-8"
    )
    with pytest.raises(aggregate.XtbArchiveAggregateError, match=error):
        aggregate._validate_state(root, task)


def test_main_cli_and_empty_writer_cleanup(tmp_path: Path, monkeypatch, capsys):
    root = tmp_path / "results"
    task, _ = _install_task(
        root, source_index=9, rank=1, source_slug="crest", candidate="x",
        role="chain_extender", smiles="OCCO",
    )
    tasks, sources = _tables([task], [("crest", "OCCO")])
    task_path, source_path = tmp_path / "tasks.csv", tmp_path / "sources.csv"
    tasks.to_csv(task_path, index=False)
    sources.to_csv(source_path, index=False)
    outputs = [tmp_path / "c.csv", tmp_path / "g.csv", tmp_path / "f.csv"]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "aggregate", "--xTB任务清单", str(task_path), "--结果目录", str(root),
            "--CREST任务清单", str(source_path), "--逐构象输出", str(outputs[0]),
            "--构件输出", str(outputs[1]), "--失败输出", str(outputs[2]),
            "--临时目录", str(tmp_path / "controlled-temp"),
        ],
    )
    aggregate.main()
    assert "'conformers': 1" in capsys.readouterr().out

    empty = tasks.iloc[0:0]
    with pytest.raises(aggregate.XtbArchiveAggregateError, match="任务表为空"):
        aggregate.write_aggregate_outputs(
            empty, root, sources, tmp_path / "e1.csv", tmp_path / "e2.csv", tmp_path / "e3.csv"
        )
    assert not list(tmp_path.glob("e*.tmp"))
