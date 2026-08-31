import json
import subprocess
import sys
from pathlib import Path

import 接入Zenodo木质素TPU as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_precursor_mechanical_summaries_keep_censoring():
    mechanical, _ = source.build_release()
    assert len(mechanical) == 8
    assert mechanical["formulation_id"].nunique() == 8
    assert set(mechanical["lignin_code"]) == {"TcA", "TcC"}
    assert mechanical["tpu_wt_pct"].isin({35, 40, 45, 50}).all()
    assert mechanical["elongation_right_censored"].sum() == 2
    censored = mechanical.loc[mechanical["elongation_right_censored"]]
    assert censored["elongation_at_break_pct"].isna().all()
    assert censored["elongation_lower_bound_pct"].eq(200).all()
    assert mechanical["complete_stress_strain_curve_available"].eq(False).all()
    assert mechanical["complete_toughness_available"].eq(False).all()
    assert mechanical["thermoplastic_TPU_core"].eq(False).all()


def test_tga_curves_map_to_exact_blend_formulations():
    _, tga = source.build_release()
    assert len(tga) == 6
    assert tga["formulation_id"].nunique() == 6
    assert tga["raw_curve_point_count"].sum() == 8130
    assert tga["unique_temperature_point_count"].eq(1355).all()
    assert set(tga["tpu_wt_pct"]) == {30, 40, 50}
    assert tga["T5_degC"].between(130, 300).all()
    assert tga["T10_degC"].between(250, 320).all()
    assert tga["T50_degC"].between(395, 435).all()
    assert tga["temperature_unit_status"].eq(
        "primary_article_supported_Celsius_workbook_header_missing"
    ).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入Zenodo木质素TPU.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "木质素TPU发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"]["mechanical_summary_row_count"] == 8
    assert manifest["counts"]["right_censored_elongation_count"] == 2
    assert manifest["counts"]["tga_curve_count"] == 6
    assert manifest["counts"]["tga_curve_point_count"] == 8130
    assert manifest["counts"]["published_compact_row_count"] == 14
    assert manifest["policy"]["precursor_fibers_claimed_as_bulk_TPU"] is False
    assert manifest["policy"]["right_censored_elongation_imputed_as_200"] is False

