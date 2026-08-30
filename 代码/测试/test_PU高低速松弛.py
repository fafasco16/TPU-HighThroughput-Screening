import json
import subprocess
import sys
from pathlib import Path

import 接入PU高低速松弛 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_curve_and_condition_endpoints():
    curves, conditions = source.build_release()
    assert len(curves) == 59
    assert len(conditions) == 20
    assert set(curves["material_grade"]) == {"Task 3", "Task 11"}
    assert curves.groupby("experiment_family").size().to_dict() == {
        "SHPB_10mm_relaxation": 6,
        "SHPB_6mm_relaxation": 5,
        "slow_large_strain_relaxation": 18,
        "slow_temperature_relaxation": 30,
    }
    assert conditions["replicate_curve_count"].sum() == 59
    assert conditions["replicate_curve_count"].between(2, 3).all()
    assert curves["sample_weight_ceiling"].eq(0.0).all()
    assert conditions["sample_weight_ceiling"].eq(0.20).all()
    assert curves["physical_specimen_count_known"].eq(False).all()  # noqa: E712
    assert curves["retention_at_record_end"].notna().all()
    assert curves["model_admission_layer"].eq(
        "unknown_chemistry_cast_PU_relaxation_auxiliary"
    ).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入PU高低速松弛.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "PU高低速松弛发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"]["curve_evidence_row_count"] == 59
    assert manifest["counts"]["condition_aggregate_row_count"] == 20
    assert manifest["counts"]["slow_temperature_curve_count"] == 30
    assert manifest["counts"]["slow_large_strain_curve_count"] == 18
    assert manifest["counts"]["SHPB_10mm_curve_count"] == 6
    assert manifest["counts"]["SHPB_6mm_curve_count"] == 5
    assert manifest["policy"]["raw_curves_republished"] is False
    assert manifest["policy"]["direct_cyclic_recovery_available"] is False
