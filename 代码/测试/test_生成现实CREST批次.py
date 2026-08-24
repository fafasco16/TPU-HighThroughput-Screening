from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "代码"))

import 生成现实CREST批次 as crest_batch


def test_real_batch_contains_stable_nineteen_tasks(tmp_path: Path):
    manifest = crest_batch.build(
        ROOT / "计算" / "现实构件" / "量化任务.csv",
        ROOT / "计算" / "现实构件",
        tmp_path,
    )
    tasks = pd.read_csv(tmp_path / "DFT任务清单.csv")
    assert manifest["status"] == "ready"
    assert manifest["counts"] == {
        "tasks": 19,
        "discrete_tasks": 14,
        "ptmg_representative_tasks": 5,
        "gfnff_preoptimized_inputs": 3,
    }
    assert tasks["candidate_id"].is_unique
    assert tasks["task_slug"].str.startswith("reality_").all()
    assert tasks["geometry_status"].eq("ready").all()
    assert set(tasks["input_geometry_source"]) == {
        "rdkit_force_field_converged",
        "gfnff_preoptimized",
    }
    saved = json.loads((tmp_path / "发布清单.json").read_text(encoding="utf-8"))
    assert saved["execution_policy"]["first_wave"].startswith("14")
