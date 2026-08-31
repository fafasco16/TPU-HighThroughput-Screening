import json
import subprocess
import sys
from pathlib import Path

import 接入SLS_TPU工艺力学 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_gold_process_specimens():
    frame = source.build_release()
    assert len(frame) == 140
    assert frame["condition_id"].nunique() == 31
    assert frame["sample_id"].is_unique
    assert frame["curve_point_count"].sum() == 1144295
    assert frame["material_grade"].eq("EOS TPU 1301").all()
    assert frame["curve_point_count"].gt(10).all()
    assert frame["tensile_strength_MPa"].gt(0).all()
    assert frame["toughness_MJ_m3"].ge(0).all()
    assert frame[
        [
            "source_Fmax_MPa",
            "source_strain_at_Fmax_percent",
            "source_fracture_stress_MPa",
            "source_elongation_at_break_percent",
            "source_young_modulus_MPa",
        ]
    ].notna().all().all()
    assert frame["model_admission_layer"].eq(
        "core_tpu_application_experimental"
    ).all()
    assert frame["process_mapping_status"].eq(
        "paper_condition_mapped_detailed_parameters_partial"
    ).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入SLS_TPU工艺力学.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "SLS_TPU1301工艺力学发布清单.json").read_text(
            encoding="utf-8"
        )
    )
    counts = manifest["counts"]
    assert counts["gold_process_condition_count"] == 31
    assert counts["gold_physical_specimen_count"] == 140
    assert counts["gold_curve_point_count"] == 1144295
    assert counts["direct_endpoint_scalar_count"] == 700
    assert counts["published_compact_row_count"] == 140
    assert counts["all_deduplicated_curve_count_in_source"] == 350
    assert counts["silver_specimen_count_not_published"] == 210
    assert manifest["policy"]["raw_curves_republished"] is False
    assert manifest["policy"]["cross_source_canonical_material_key"] == (
        "EOS TPU 1301"
    )
