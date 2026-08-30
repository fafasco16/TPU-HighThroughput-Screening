import json
import subprocess
import sys
from pathlib import Path

import pytest

import 接入PCF20泡沫断裂 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_tension_and_fracture_endpoints():
    frame = source.build_release()
    assert len(frame) == 18
    assert frame["specimen_key"].is_unique
    assert frame["test_type"].value_counts().to_dict() == {
        "tension": 12,
        "SENB_fracture": 6,
    }
    assert frame["material_grade"].eq("Sawbones PCF20").all()
    assert frame["thermoplastic_tpu_core"].eq(False).all()
    assert frame["model_admission_layer"].eq("polyurethane_foam_transfer").all()
    tension = frame.loc[frame["test_type"].eq("tension")]
    assert set(tension["direction"]) == {"Direction 11", "Direction 22"}
    assert tension["maximum_tensile_stress_MPa"].between(2.0, 4.0).all()
    assert tension["maximum_DIC_strain_percent"].between(1.0, 4.0).all()
    assert tension["stress_strain_area_MJ_m3"].between(0.015, 0.09).all()
    fracture = frame.loc[frame["test_type"].eq("SENB_fracture")]
    assert fracture["nominal_peak_load_K_MPa_sqrt_m"].between(0.13, 0.35).all()
    assert fracture["nominal_peak_load_K_MPa_sqrt_m"].mean() == pytest.approx(
        0.22, abs=0.03
    )
    assert fracture["published_mean_K_MPa_sqrt_m"].eq(0.24).all()
    assert fracture["K_validity_status"].eq(
        "nominal_peak_load_geometry_not_full_ASTME399_validity"
    ).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入PCF20泡沫断裂.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "PCF20泡沫断裂发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"] == {
        "material_grade_count": 1,
        "independent_physical_specimen_count": 18,
        "tension_specimen_count": 12,
        "fracture_specimen_count": 6,
        "machine_source_point_count": 4270,
        "DIC_source_point_count": 4839,
        "published_compact_row_count": 18,
    }
    assert manifest["source"]["license"] == "CC-BY-4.0"
    assert manifest["policy"]["compression_and_shear_deferred"] is True
    assert manifest["policy"]["raw_curves_and_images_republished"] is False
