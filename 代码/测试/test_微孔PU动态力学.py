import json
import subprocess
import sys
from pathlib import Path

import 接入微孔PU动态力学 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_dma_and_shpb_endpoints():
    dma, shpb = source.build_release()
    assert len(dma) == 3
    assert len(shpb) == 9
    assert set(dma["density_grade"]) == {"400M", "600M", "800M"}
    assert dma["tan_delta_peak_temperature_degC"].between(-100, 60).all()
    assert dma["storage_modulus_at_20C_MPa"].gt(0).all()
    assert set(shpb["impact_velocity_source_label"]) == {30, 48, 62}
    assert shpb["sensor_channel_count"].eq(2).all()
    assert shpb["sigma1_peak_stress_MPa"].gt(0).all()
    assert shpb["sigma2_peak_stress_MPa"].gt(0).all()
    assert shpb["complete_stress_strain_toughness_available"].eq(
        False  # noqa: E712
    ).all()
    for frame in (dma, shpb):
        assert frame["model_admission_layer"].eq(
            "microporous_PU_dynamic_transfer"
        ).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入微孔PU动态力学.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "微孔PU动态力学发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"] == {
        "density_grade_count": 3,
        "DMA_endpoint_row_count": 3,
        "DMA_response_curve_count": 9,
        "SHPB_physical_condition_count": 9,
        "SHPB_sensor_curve_count": 18,
        "published_compact_row_count": 12,
    }
    assert manifest["policy"]["raw_curves_republished"] is False
    assert manifest["policy"]["direct_toughness_available"] is False
