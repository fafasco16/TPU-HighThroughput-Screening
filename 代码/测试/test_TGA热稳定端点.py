import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import 提取TGA热稳定端点 as tga


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_extract_t5_t10_from_normalized_curve():
    curve = pd.DataFrame(
        {
            "temperature": [25.0, 100.0, 200.0, 300.0],
            "mass": [100.0, 98.0, 94.0, 88.0],
        }
    )
    result = tga.extract_tga_endpoints(curve)
    assert result["T5_degC"] == pytest.approx(175.0)
    assert result["T10_degC"] == pytest.approx(266.6666667)
    assert pd.isna(result["T50_degC"])
    assert result["baseline_mass"] == pytest.approx(100.0)


def test_real_release_keeps_identity_conflict_separate():
    source = pd.read_csv(
        OUTPUT / "三目标实验标签.csv.gz",
        low_memory=False,
    )
    endpoints = tga.build_endpoints(source)
    assert len(endpoints) == 5
    assert endpoints["curve_id"].is_unique
    assert endpoints["T5_degC"].notna().all()
    assert endpoints["T10_degC"].notna().all()
    assert endpoints["T50_degC"].notna().all()
    assert endpoints["formulation_id"].notna().sum() == 4
    mapped = endpoints.dropna(subset=["formulation_id"])
    assert set(mapped["dso_polyol_mass_fraction_source_label"]) == {0.0, 0.5, 0.7, 1.0}
    assert mapped["chemistry_mapping_status"].eq("composition_series_mapped").all()
    conflict = endpoints[endpoints["formulation_id"].isna()].iloc[0]
    assert conflict["endpoint_use"] == "reference_only_identity_conflict"
    assert "conflict" in conflict["quality_status"]


def test_release_and_check_command():
    script = ROOT / "代码" / "提取TGA热稳定端点.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "TGA端点发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"] == {
        "curve_count": 5,
        "identity_resolved_curve_count": 4,
        "t5_count": 5,
        "t10_count": 5,
        "t50_count": 5,
    }
