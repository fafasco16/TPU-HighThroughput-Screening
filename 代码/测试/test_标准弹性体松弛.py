import json
import subprocess
import sys
from pathlib import Path

import 接入标准弹性体松弛 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_relaxation_endpoints():
    frame = source.build_release()
    assert len(frame) == 2
    assert frame["material_grade"].tolist() == ["Cheetah", "Filaflex 60A"]
    assert frame["nominal_hold_strain_percent"].eq(25).all()
    assert frame["retention_at_1s"].between(0.97, 1.0).all()
    assert frame["retention_at_10s"].between(0.92, 0.95).all()
    assert frame["retention_at_100s"].between(0.85, 0.90).all()
    assert frame["retention_at_1000s"].between(0.79, 0.85).all()
    assert frame["retention_at_10000s"].between(0.72, 0.80).all()
    assert frame["time_to_90pct_retention_s"].notna().all()
    assert frame["time_to_80pct_retention_s"].notna().all()
    assert frame["time_to_50pct_retention_s"].isna().all()
    assert frame["time_to_50pct_status"].eq(
        "right_censored_at_record_end"
    ).all()
    cheetah = frame.loc[frame["material_grade"].eq("Cheetah")].iloc[0]
    filaflex = frame.loc[frame["material_grade"].eq("Filaflex 60A")].iloc[0]
    assert filaflex["retention_at_10000s"] > cheetah["retention_at_10000s"]
    assert frame["stable_hold_detection"].eq(
        "first_100_consecutive_points_within_25_plusminus_0.01pct"
    ).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入标准弹性体松弛.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "标准热塑性弹性体松弛发布清单.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["counts"] == {
        "material_grade_count": 2,
        "relaxation_curve_count": 2,
        "source_point_count": 1220406,
        "published_compact_row_count": 2,
    }
    assert manifest["source"]["license"] == "CC-BY-4.0"
    assert manifest["policy"]["raw_curves_republished"] is False
    assert manifest["policy"]["physical_specimen_count_known"] is False
