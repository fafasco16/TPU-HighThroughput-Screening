import json
import subprocess
import sys
from pathlib import Path

import 接入再生PU泡沫 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_modal_counts_and_source_boundaries():
    frames = source.build_release()
    assert {key: len(frame) for key, frame in frames.items()} == {
        "compression_points": 13159,
        "compression_endpoints": 118,
        "viscosity_points": 581,
        "thermal_conductivity": 7,
        "formulation_components": 39,
        "aggregate_scalars": 27,
    }
    assert frames["compression_points"]["formulation_id"].nunique() == 9
    assert frames["compression_endpoints"]["gold_admission_status"].eq(
        "conditional_reference"
    ).all()
    assert frames["formulation_components"]["gold_admission_status"].eq(
        "admitted_reference"
    ).all()
    assert frames["thermal_conductivity"]["property_name"].eq(
        "thermal_conductivity"
    ).all()
    assert frames["compression_points"]["split_group"].nunique() == 1


def test_release_and_check_command():
    script = ROOT / "代码" / "接入再生PU泡沫.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "再生PU泡沫发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"] == {
        "gold_e_row_count": 13931,
        "compression_curve_point_count": 13159,
        "compression_curve_count": 59,
        "compression_endpoint_row_count": 118,
        "viscosity_curve_point_count": 581,
        "viscosity_curve_count": 10,
        "thermal_conductivity_row_count": 7,
        "formulation_component_row_count": 39,
        "aggregate_scalar_row_count": 27,
        "published_output_count": 6,
        "published_compact_row_count": 13931,
    }
    assert manifest["policy"]["tpu_core_supervision"] is False
    assert manifest["policy"]["compression_energy_is_fracture_toughness"] is False
    assert manifest["policy"]["thermal_conductivity_is_thermal_decomposition"] is False
