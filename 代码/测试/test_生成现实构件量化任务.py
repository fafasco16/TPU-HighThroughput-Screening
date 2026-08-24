from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "代码"))

import 生成现实构件量化任务 as reality_quantum


def test_real_inputs_build_all_admitted_tasks():
    components = pd.read_csv(ROOT / "数据" / "现实库" / "构件.csv")
    ptmg = pd.read_csv(ROOT / "数据" / "现实库" / "PTMG代表模型.csv")
    tasks = reality_quantum.build_tasks(components, ptmg)
    assert len(tasks) == 19
    assert tasks["candidate_id"].is_unique
    assert tasks["component_role"].value_counts().to_dict() == {
        "diisocyanate": 7,
        "macrodiol_representative": 5,
        "chain_extender": 7,
    }
    assert set(tasks.loc[tasks.component_role.eq("macrodiol_representative"), "initial_conformer_count"]) == {1, 3}


def test_materialize_small_fixture(tmp_path: Path):
    tasks = pd.DataFrame(
        [
            {
                "task_index": 0,
                "candidate_id": "bdo",
                "component_role": "chain_extender",
                "canonical_smiles": "OCCCCO",
                "commercial_identity": "BDO",
                "model_scope": "exact_discrete_commercial_substance",
                "geometry_seed": 1,
                "initial_conformer_count": 2,
                "charge": 0,
                "uhf": 0,
                "task_slug": "0000_bdo",
                "initial_xyz_file": "初始结构/0000_bdo.xyz",
            }
        ]
    )
    result = reality_quantum.materialize(tasks, tmp_path)
    assert result.loc[0, "geometry_status"] == "ready"
    assert result.loc[0, "initial_xyz_bytes"] > 0
    assert len(result.loc[0, "initial_xyz_sha256"]) == 64
    assert reality_quantum._aggregate_hash(result)


def test_materialize_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    tasks = pd.DataFrame(
        [
            {
                "task_index": 0,
                "candidate_id": "bad",
                "component_role": "chain_extender",
                "canonical_smiles": "OCCCCO",
                "commercial_identity": "bad",
                "model_scope": "exact_discrete_commercial_substance",
                "geometry_seed": 1,
                "initial_conformer_count": 1,
                "charge": 0,
                "uhf": 0,
                "task_slug": "0000_bad",
                "initial_xyz_file": "初始结构/0000_bad.xyz",
            }
        ]
    )
    monkeypatch.setattr(reality_quantum.dft, "generate_initial_xyz", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("blocked")))
    result = reality_quantum.materialize(tasks, tmp_path)
    assert result.loc[0, "geometry_status"] == "blocked_rdkit_3d_embedding"
    assert result.loc[0, "initial_xyz_bytes"] == 0


def test_build_and_main(tmp_path: Path, capsys):
    components = ROOT / "数据" / "现实库" / "构件.csv"
    ptmg = ROOT / "数据" / "现实库" / "PTMG代表模型.csv"
    manifest = reality_quantum.build(components, ptmg, tmp_path / "out", seed=20260824)
    assert manifest["counts"]["tasks"] == 19
    assert (
        manifest["counts"]["force_field_converged"]
        + manifest["counts"]["xtb_preoptimization_required"]
        == 19
    )
    assert manifest["counts"]["xtb_preoptimization_required"] >= 1
    assert manifest["status"] == "completed_with_preoptimization_required"
    assert (tmp_path / "out" / "量化任务.csv").is_file()
    assert (tmp_path / "out" / "发布清单.json").is_file()
    second = tmp_path / "main"
    assert reality_quantum.main([
        "--构件", str(components),
        "--PTMG", str(ptmg),
        "--输出目录", str(second),
        "--预优化清单", str(tmp_path / "missing.json"),
    ]) == 0
    assert '"tasks": 19' in capsys.readouterr().out
