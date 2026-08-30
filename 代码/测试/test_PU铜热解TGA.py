import json
import subprocess
import sys
from pathlib import Path

import 接入PU铜热解TGA as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_multirate_tga_endpoints():
    frame = source.build_release()
    assert len(frame) == 6
    assert set(frame["material_state"]) == {
        "commercial_PU_enamelled_copper_wire",
        "PU_enamel_Cu-free_reference",
    }
    copper = frame.loc[frame["copper_status"].eq("Cu-containing")]
    assert set(copper["heating_rate_degC_min"]) == {5, 10, 15, 20, 25}
    assert frame["T5_degC"].notna().all()
    assert frame["T10_degC"].notna().all()
    assert frame.loc[frame["copper_status"].eq("Cu-free"), "T50_degC"].notna().all()
    assert frame.loc[
        frame["copper_status"].eq("Cu-containing"), "T50_degC"
    ].isna().all()
    assert frame.loc[
        frame["copper_status"].eq("Cu-containing"), "T50_status"
    ].eq("right_censored_by_residual_mass_or_temperature_range").all()
    assert frame["DTG_peak_temperature_degC"].notna().all()
    assert frame["source_TG_point_count"].sum() == 63140
    conditional = frame.loc[frame["admission_status"].eq("conditional_reference")]
    assert len(conditional) == 1
    assert conditional.iloc[0].heating_rate_degC_min == 20
    assert conditional.iloc[0].sample_weight_ceiling == 0.10
    assert frame["model_admission_layer"].eq(
        "pu_pyrolysis_thermal_transfer"
    ).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入PU铜热解TGA.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "PU铜热解TGA发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"]["TGA_curve_count"] == 6
    assert manifest["counts"]["admitted_curve_count"] == 5
    assert manifest["counts"]["conditional_curve_count"] == 1
    assert manifest["counts"]["T50_observed_count"] == 1
    assert manifest["counts"]["T50_right_censored_count"] == 5
    assert manifest["counts"]["source_TG_point_count"] == 63140
    assert manifest["counts"]["computational_audit_record_count_not_republished"] == 14
    assert manifest["policy"]["raw_curves_republished"] is False
    assert manifest["policy"]["TPU_core_weight"] == 0.0
