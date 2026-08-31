import json
import subprocess
import sys
from pathlib import Path

import 接入ZenodoTPU鞋材 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_tga_endpoints_keep_material_classes_separate():
    tga, _ = source.build_release()
    assert len(tga) == 3
    assert set(tga["material_id"]) == {
        "eTPU_eSUN_95A_orange",
        "TPU_Rosh_95A_white",
        "PEBA_XinboChuan_85A_yellow",
    }
    assert tga["raw_curve_point_count"].eq(4620).all()
    assert tga["unique_temperature_point_count"].eq(4620).all()
    assert tga["T5_degC"].between(300, 400).all()
    assert tga["T10_degC"].between(315, 410).all()
    assert tga["T50_degC"].between(360, 450).all()
    assert tga["DTG_peak_temperature_degC"].between(300, 460).all()
    assert tga["DTG_peak_rate_pct_degC"].gt(0).all()
    peba = tga.loc[tga["material_family"].eq("polyether_block_amide")]
    assert len(peba) == 1
    assert peba.iloc[0].model_admission_layer == "commercial_elastomer_auxiliary"
    assert tga.loc[
        ~tga["material_family"].eq("polyether_block_amide"),
        "model_admission_layer",
    ].eq("core_TPU_application_experimental").all()
    assert not tga.loc[
        tga["terminal_raw_remaining_mass_pct"].lt(0),
        "terminal_residue_reliable",
    ].any()
    assert not tga.loc[
        tga["remaining_mass_at_600C_pct"].lt(0),
        "remaining_mass_at_600C_reliable",
    ].any()


def test_wear_summary_is_condition_not_chemistry_expansion():
    _, wear = source.build_release()
    assert len(wear) == 12
    assert set(wear["relative_density_pct"]) == {30, 40, 50, 70}
    assert wear["material_id"].nunique() == 3
    assert wear["reported_replicate_count"].eq(3).all()
    means = wear.groupby("material_id")["abrasion_mass_loss_pct"].mean()
    assert means["TPU_Rosh_95A_white"] > means["eTPU_eSUN_95A_orange"]
    assert means["eTPU_eSUN_95A_orange"] > means["PEBA_XinboChuan_85A_yellow"]
    assert wear["raw_abrasion_workbook_available"].eq(False).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入ZenodoTPU鞋材.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "TPU鞋材发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"]["commercial_material_count"] == 3
    assert manifest["counts"]["tga_curve_count"] == 3
    assert manifest["counts"]["tga_curve_point_count"] == 13860
    assert manifest["counts"]["wear_condition_count"] == 12
    assert manifest["counts"]["published_compact_row_count"] == 15
    assert manifest["policy"]["PEBA_counted_as_TPU"] is False
    assert manifest["policy"]["compression_curve_claimed_without_numeric_source"] is False
