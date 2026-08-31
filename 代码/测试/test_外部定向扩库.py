import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

import 获取定向外部来源 as acquisition


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_source_specs_are_open_and_targeted():
    assert len(acquisition.SOURCE_SPECS) == 4
    assert len({spec["source_id"] for spec in acquisition.SOURCE_SPECS}) == 4
    allowed_targets = {
        "toughness",
        "cyclic_recovery",
        "thermal_stability",
        "environmental_recycling",
        "environmental_wear",
    }
    for spec in acquisition.SOURCE_SPECS:
        assert spec["license"] == "CC-BY-4.0"
        assert spec["target_families"]
        assert set(spec["target_families"]) <= allowed_targets
        assert spec["files"]


def test_downloaded_candidate_release_is_complete():
    frame = pd.read_csv(OUTPUT / "外部来源候选.csv")
    assert len(frame) == 4
    assert frame["source_id"].is_unique
    assert frame["acquisition_status"].eq("materialized").all()
    assert frame["local_source_manifest_sha256"].str.len().eq(64).all()
    for relative in frame["local_directory"]:
        assert (ROOT / relative / "来源清单.json").is_file()


def test_check_command():
    script = ROOT / "代码" / "获取定向外部来源.py"
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "外部来源候选发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"] == {
        "source_count": 4,
        "downloaded_file_count": 9,
    }
