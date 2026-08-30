import json
import subprocess
import sys
from pathlib import Path

import 接入生物基玻璃体三目标 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_three_target_transfer_endpoints():
    tensile, relaxation, tga = source.build_release()
    assert len(tensile) == 20
    assert tensile["tensile_stress_at_break_MPa"].notna().sum() == 20
    assert tensile["elongation_at_break_percent"].notna().sum() == 18
    assert tensile["young_modulus_admission_status"].str.startswith(
        "quarantined"
    ).all()
    assert tensile["toughness_admission_status"].str.startswith(
        "quarantined"
    ).all()
    assert len(relaxation) == 16
    assert relaxation["curve_point_count"].sum() == 38953
    assert set(relaxation["formulation_id"]) == {"P1T", "P3T", "X1T", "X3T"}
    assert relaxation["time_to_50pct_retention_s"].notna().all()
    assert relaxation["extra_overlay_columns_ignored"].sum() == 1
    assert len(tga) == 4
    assert tga["source_curve_row_count"].sum() == 18481
    assert tga["point_count"].sum() == 18436
    assert tga["T5_degC"].notna().all()
    assert tga["T10_degC"].notna().all()
    assert tga["T50_degC"].notna().all()
    for frame in (tensile, relaxation, tga):
        assert frame["model_admission_layer"].eq(
            "dynamic_network_vitrimer_transfer"
        ).all()
        assert frame["thermoplastic_tpu_core"].eq(False).all()  # noqa: E712


def test_release_and_check_command():
    script = ROOT / "代码" / "接入生物基玻璃体三目标.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "生物基玻璃体发布清单.json").read_text(encoding="utf-8")
    )
    counts = manifest["counts"]
    assert counts["physical_tensile_specimen_count"] == 20
    assert counts["relaxation_curve_count"] == 16
    assert counts["relaxation_source_point_count"] == 38953
    assert counts["tga_curve_count"] == 4
    assert counts["tga_source_row_count"] == 18481
    assert counts["tga_processed_unique_temperature_point_count"] == 18436
    assert counts["published_compact_row_count"] == 40
    assert manifest["policy"]["tpu_core_weight"] == 0.0
    assert manifest["policy"]["raw_curves_republished"] is False
