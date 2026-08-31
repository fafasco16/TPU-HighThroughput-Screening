import json
import subprocess
import sys
from pathlib import Path

import 接入4TU自修复TPU as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_recovery_pairs_are_same_specimen_and_target_scoped():
    recovery, _ = source.build_release()
    assert len(recovery) == 10
    assert set(recovery["formulation_id"]) == {"SH-TPU", "Ninjaflex"}
    assert recovery["pair_id"].is_unique
    assert recovery["original_member_sha256"].str.len().eq(64).all()
    assert recovery["healed_member_sha256"].str.len().eq(64).all()
    assert recovery["peak_load_recovery_fraction"].gt(0).all()
    assert recovery["cut_work_recovery_fraction"].gt(0).all()
    assert recovery["complete_tensile_toughness_available"].eq(False).all()
    assert recovery["target_role"].eq(
        "direct_compression_cut_healing_recovery_proxy"
    ).all()
    sh_tpu = recovery.loc[recovery["formulation_id"].eq("SH-TPU")]
    ninjaflex = recovery.loc[recovery["formulation_id"].eq("Ninjaflex")]
    assert len(sh_tpu) == 6
    assert len(ninjaflex) == 4
    assert sh_tpu["component_molar_ratio"].eq("1:0.6:1.7").all()
    assert sh_tpu["cut_work_recovery_fraction"].median() > 0.9
    assert ninjaflex["cut_work_recovery_fraction"].median() < 0.7


def test_tga_endpoints_and_state_grouping():
    _, tga = source.build_release()
    assert len(tga) == 2
    assert set(tga["material_state"]) == {
        "FDM_filament",
        "pristine_polymer",
    }
    assert tga["formulation_id"].eq("SH-TPU").all()
    assert tga["raw_curve_point_count"].sum() == 6584
    assert tga["curve_point_count"].sum() == 6538
    assert tga["T5_degC"].between(300, 330).all()
    assert tga["T10_degC"].between(320, 350).all()
    assert tga["T50_degC"].between(350, 390).all()
    assert tga["split_group"].nunique() == 1


def test_release_and_check_command():
    script = ROOT / "代码" / "接入4TU自修复TPU.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "4TU自修复TPU发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"]["archive_member_count"] == 91
    assert manifest["counts"]["archive_file_member_count"] == 71
    assert manifest["counts"]["mechanical_curve_count"] == 36
    assert manifest["counts"]["original_physical_specimen_key_count"] == 26
    assert manifest["counts"]["healing_pair_count"] == 10
    assert manifest["counts"]["unpaired_original_mechanical_curve_hold_count"] == 16
    assert manifest["counts"]["tga_curve_count"] == 2
    assert manifest["counts"]["tga_raw_point_count"] == 6584
    assert manifest["counts"]["tga_unique_temperature_point_count"] == 6538
    assert manifest["policy"]["compression_cut_work_claimed_as_tensile_toughness"] is False
