import json
import subprocess
import sys
from pathlib import Path

import 接入MendeleyTPU压缩DOE as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_directional_discrete_responses_keep_specimen_and_geometry_context():
    frame = source.build_release()
    assert len(frame) == 344
    assert frame["physical_specimen_id"].nunique() == 184
    assert frame["material_key"].nunique() == 2
    assert set(frame["loading_direction"]) == {"dir_1", "dir_2", "vertical", "horizontal"}
    assert frame["direct_stress_observation_count"].sum() == 1372
    assert frame["complete_four_point_response"].sum() == 343
    assert (~frame["complete_four_point_response"]).sum() == 1
    assert frame.loc[frame["complete_four_point_response"], "discrete_compression_energy_to_20pct_kJ_m3"].gt(0).all()
    assert frame["complete_toughness_available"].eq(False).all()
    assert frame["target_role"].eq(
        "discrete_compression_energy_absorption_application_proxy"
    ).all()
    assert frame.loc[
        frame.geometry.eq("solid_cube_control"), "infill_mapping_status"
    ].eq("raw_label_ambiguous_not_numeric_percent").all()
    assert frame.loc[
        frame["missing_stress_observation_count"].gt(0), "sample_weight_ceiling"
    ].eq(0).all()


def test_materials_are_not_overmerged():
    frame = source.build_release()
    assert set(frame.loc[frame.material_key.eq("NinjaFlex_unknown_grade"), "geometry"]) == {
        "cube",
        "cylinder",
        "solid_cube_control",
    }
    assert set(frame.loc[frame.material_key.eq("PolyFlex_unknown_grade"), "geometry"]) == {
        "cube"
    }
    assert frame.loc[frame.material_key.eq("NinjaFlex_unknown_grade"), "material_grade"].str.contains("exact chemistry/hardness unavailable").all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入MendeleyTPU压缩DOE.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "TPU压缩打印DOE发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"]["published_directional_response_row_count"] == 344
    assert manifest["counts"]["physical_specimen_count"] == 184
    assert manifest["counts"]["configuration_family_count"] == 46
    assert manifest["counts"]["direct_stress_observation_count"] == 1372
    assert manifest["counts"]["complete_four_point_response_row_count"] == 343
    assert manifest["counts"]["partial_response_row_count"] == 1
    assert manifest["counts"]["derived_discrete_energy_row_count"] == 343
    assert manifest["policy"]["continuous_stress_strain_history_available"] is False
    assert manifest["policy"]["missing_polyflex_values_imputed"] is False

