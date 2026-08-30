import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

import 接入商业TPU温度疲劳 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_history_and_specimen_counting():
    histories, recovery = source.build_release()
    assert len(histories) == 196
    assert histories["history_id"].is_unique
    assert histories["physical_specimen_id"].nunique() == 190
    assert histories["material_grade"].nunique() == 5
    assert histories["source_point_count"].sum() == 333492
    assert histories["history_role"].value_counts().to_dict() == {
        "on_axis_fatigue_response": 169,
        "off_axis_fatigue_response": 21,
        "ambient_recovery_retest": 6,
    }
    assert len(recovery) == 6
    assert recovery["physical_specimen_id"].is_unique
    assert set(recovery["recovery_elapsed_days"]) == {46, 49}


def test_commercial_tpu_boundaries_and_recalculation():
    histories, recovery = source.build_release()
    assert histories["thermoplastic_tpu_core"].all()
    assert histories["model_admission_layer"].eq(
        "core_tpu_application_experimental"
    ).all()
    assert histories["chemistry_mapping_status"].eq(
        "commercial_grade_identity_only"
    ).all()
    assert set(histories["fatigue_cycles"].dropna().astype(int)) == {0, 1, 10, 100}
    assert set(histories["temperature_C"]) == {-20.0, 20.0, 55.0}
    assert histories["maximum_force_N"].gt(0).all()
    assert histories["maximum_compression_strain_percent"].gt(0).all()
    comparable = histories.dropna(
        subset=[
            "energy_absorption_50_source_J_m3",
            "energy_absorption_50_recomputed_J_m3",
        ]
    )
    assert len(comparable) == 75
    assert histories["energy_absorption_50_recomputed_J_m3"].notna().sum() == 196
    assert comparable["energy_recalculation_relative_error_percent"].lt(1.5).all()
    assert recovery["paired_fatigued_history_id"].notna().all()
    assert recovery["recovery_energy_ratio_vs_fatigued_percent"].gt(0).all()
    assert recovery["direct_property_recovery_available"].all()
    assert recovery["direct_shape_recovery_available"].eq(False).all()
    assert recovery["shape_recovery_ratio_percent"].isna().all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入商业TPU温度疲劳.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "商业TPU温度疲劳发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"] == {
        "commercial_tpu_grade_count": 5,
        "independent_physical_specimen_count": 190,
        "curve_history_count": 196,
        "on_axis_history_count": 169,
        "off_axis_history_count": 21,
        "recovery_retest_history_count": 6,
        "recovery_pair_count": 6,
        "raw_curve_point_count": 333492,
        "source_energy_summary_row_count": 75,
        "recomputed_energy_row_count": 196,
        "source_recomputed_comparable_row_count": 75,
        "published_compact_row_count": 202,
    }
    assert manifest["source"]["license"] == "CC-BY-4.0"
    assert manifest["policy"]["raw_curves_republished"] is False
    published = pd.read_csv(OUTPUT / "商业TPU温度疲劳端点.csv")
    assert len(published) == 196
