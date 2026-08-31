import json
import subprocess
import sys
from pathlib import Path

import 接入植物基PU泡沫温湿老化 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_aged_foam_endpoint_counts_and_gates():
    frame = source.build_release()
    assert len(frame) == 90
    assert frame["sample_id"].is_unique
    assert set(frame["exposure_temperature_degC"]) == {60.0, 75.0, 90.0}
    assert set(frame["direction"]) == {"DIR1", "DIR3"}
    assert frame["raw_point_count"].sum() == 360176
    assert frame["peak_force_N"].between(1500, 8500).all()
    assert frame["force_displacement_work_J"].between(20, 70).all()
    assert frame["absolute_stress_available"].eq(False).all()
    assert frame["complete_toughness_available"].eq(False).all()
    assert frame["training_weight_ceiling"].eq(0.0).sum() == 2
    assert frame["gold_admission_status"].eq(
        "conditional_duplicate_content"
    ).sum() == 2
    assert frame["split_group"].nunique() == 9


def test_release_and_check_command():
    script = ROOT / "代码" / "接入植物基PU泡沫温湿老化.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "植物基PU泡沫温湿老化发布清单.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["counts"] == {
        "batch_count": 9,
        "temperature_condition_count": 3,
        "direction_count": 2,
        "physical_curve_count": 90,
        "default_trainable_curve_count": 88,
        "isolated_duplicate_curve_count": 2,
        "raw_point_count": 360176,
        "published_compact_row_count": 90,
    }
    assert manifest["policy"]["tpu_core_supervision"] is False
    assert manifest["policy"]["exposure_duration_imputed"] is False
