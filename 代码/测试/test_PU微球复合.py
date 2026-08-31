import json
import subprocess
import sys
from pathlib import Path

import 接入PU微球复合拉伸 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_six_composition_condition_endpoints():
    frame = source.build_release()
    assert len(frame) == 6
    assert frame["microsphere_volume_fraction_percent"].tolist() == [
        0,
        5,
        10,
        15,
        20,
        25,
    ]
    assert frame["physical_specimen_count"].eq(2).all()
    assert frame["curve_point_count"].eq(500).all()
    assert frame["peak_nominal_stress_source_unit"].gt(0).all()
    assert frame["loading_area_source_stress_unit"].gt(0).all()
    assert frame["energy_recovery_ratio"].between(0, 1).all()
    assert frame["stress_unit_status"].eq(
        "unresolved_in_deposit_metadata_no_MPa_claim"
    ).all()
    assert frame["model_admission_layer"].eq(
        "PU_microsphere_composite_transfer"
    ).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入PU微球复合拉伸.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "PU微球复合发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"]["composition_condition_count"] == 6
    assert manifest["counts"]["physical_specimen_count"] == 12
    assert manifest["counts"]["condition_mean_curve_point_count"] == 3000
    assert manifest["counts"]["published_compact_row_count"] == 6
    assert manifest["policy"]["raw_curves_republished"] is False
    assert manifest["policy"]["absolute_MPa_or_MJ_m3_values_published"] is False
