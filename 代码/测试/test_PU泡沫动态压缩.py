import json
import subprocess
import sys
from pathlib import Path

import 接入PU泡沫动态压缩 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_dynamic_compression_endpoints():
    frame = source.build_release()
    assert len(frame) == 12
    assert set(frame["material_code"]) == {"HDB_foam", "HA_foam"}
    assert set(frame["temperature_degC"]) == {-20, -10, 0, 10, 20, 40}
    assert frame["curve_point_count"].sum() == 29707
    assert frame["peak_stress_MPa"].gt(0).all()
    assert frame["source_65pct_energy_mean_J_m3"].gt(0).all()
    assert frame["curve_reaches_65pct_strain"].sum() == 6
    assert frame.loc[
        frame["curve_reaches_65pct_strain"],
        "energy_absorption_to_65pct_MJ_m3",
    ].gt(0).all()
    assert frame.loc[
        ~frame["curve_reaches_65pct_strain"],
        "energy_absorption_to_65pct_MJ_m3",
    ].isna().all()
    assert frame["curve_full_energy_matches_source_within_2pct"].all()
    corrected = frame.loc[frame["temperature_label_status"].eq(
        "resolved_duplicate_header_by_energy_match"
    )]
    assert len(corrected) == 2
    assert set(corrected["temperature_degC"]) == {-20, -10}
    assert frame["model_admission_layer"].eq(
        "dynamic_PU_foam_transfer"
    ).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入PU泡沫动态压缩.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "PU泡沫动态压缩发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"]["material_code_count"] == 2
    assert manifest["counts"]["temperature_condition_count"] == 12
    assert manifest["counts"]["stress_strain_curve_point_count"] == 29707
    assert manifest["counts"]["source_energy_numeric_observation_count"] == 105
    assert manifest["counts"]["resolved_duplicate_temperature_header_count"] == 2
    assert manifest["counts"]["curve_energy_source_match_count"] == 12
    assert manifest["counts"]["curve_reaches_65pct_strain_count"] == 6
    assert manifest["policy"]["quasistatic_TPU_toughness_claim"] is False
    assert manifest["policy"]["curve_65pct_extrapolation_allowed"] is False
    assert manifest["policy"]["local_subset_claimed_as_full_repository"] is False
