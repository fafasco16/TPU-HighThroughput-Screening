import json
import subprocess
import sys
from pathlib import Path

import pytest

import 接入Tecoflex药物复合TPU as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_tecoflex_multi_property_release():
    frame = source.build_release()
    assert len(frame) == 4
    assert frame["material_id"].tolist() == [
        "Tecoflex_EG60D",
        "Tecoflex_EG60D_NIC2",
        "Tecoflex_EG60D_NIC5",
        "Tecoflex_EG60D_NIC10",
    ]
    assert frame["niclosamide_wt_percent"].tolist() == [0.0, 2.0, 5.0, 10.0]
    assert frame["TPU_grade"].eq("Tecoflex EG-60D").all()
    assert (frame["T5_C"] < frame["T10_C"]).all()
    assert (frame["T10_C"] < frame["T50_C"]).all()
    assert frame["T5_C"].between(290, 315).all()
    assert frame["partial_tensile_curve_area_MJ_m3"].between(18, 24).all()
    assert frame["maximum_observed_strain_percent"].between(189, 200).all()
    assert frame["stress_at_100_percent_mean_MPa"].notna().all()
    pure = frame.loc[frame["material_id"].eq("Tecoflex_EG60D")].iloc[0]
    assert pure["T5_C"] == pytest.approx(295.13234247)
    assert pure["stress_at_100_percent_mean_MPa"] == pytest.approx(12.408593956)
    assert pure["elastic_modulus_mean_MPa"] == pytest.approx(4.775582088)
    assert frame["model_admission_layer"].eq(
        "core_tpu_composite_experimental"
    ).all()
    assert frame["toughness_evidence_level"].eq(
        "partial_tensile_curve_area_lower_bound"
    ).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入Tecoflex药物复合TPU.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "Tecoflex药物复合TPU发布清单.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["counts"] == {
        "formulation_count": 4,
        "tga_curve_count": 4,
        "tga_source_point_count": 22463,
        "tensile_curve_count": 4,
        "tensile_source_point_count": 1136,
        "mechanical_direct_specimen_slot_count": 22,
        "published_compact_row_count": 4,
    }
    assert manifest["source"]["license"] == "CC-BY-4.0"
    assert manifest["policy"]["pure_niclosamide_tga_excluded"] is True
    assert manifest["policy"]["raw_curves_republished"] is False
