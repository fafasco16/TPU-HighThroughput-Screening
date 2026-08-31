import json
import subprocess
import sys
from pathlib import Path

import 接入SLS_TPU银层 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_silver_process_specimens():
    frame = source.build_release()
    assert len(frame) == 210
    assert frame["condition_id"].nunique() == 44
    assert frame["sample_id"].is_unique
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
    assert frame["model_admission_layer"].eq("SLS_TPU_process_silver").all()
    assert frame["curve_sha256"].nunique() == 210
    assert frame["cross_gold_curve_duplicate"].sum() == 4
    assert frame["source_young_modulus_qc_status"].eq(
        "quarantined_negative_source_modulus"
    ).sum() == 2
    assert frame["sample_weight_ceiling"].eq(0).sum() == 9
    assert frame["sample_weight_ceiling"].gt(0).sum() == 201
    assert frame["process_model_weight_ceiling"].eq(0).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入SLS_TPU银层.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "SLS_TPU1301银层发布清单.json").read_text(
            encoding="utf-8"
        )
    )
    counts = manifest["counts"]
    assert counts["silver_process_condition_count"] == 44
    assert counts["silver_physical_specimen_count"] == 210
    assert counts["silver_curve_point_count"] == 643157
    assert counts["direct_endpoint_scalar_count"] == 1050
    assert counts["canonical_legacy_xls_count"] == 38
    assert counts["canonical_direct_xlsx_count"] == 6
    assert counts["scientific_duplicate_sequence_count_excluded"] == 2
    assert counts["exact_duplicate_xlsx_group_count_excluded"] == 4
    assert counts["cross_gold_duplicate_curve_count"] == 4
    assert counts["unique_silver_curve_count"] == 210
    assert counts["new_unique_curve_count_beyond_gold"] == 206
    assert counts["combined_gold_silver_unique_curve_count"] == 346
    assert counts["negative_source_modulus_count"] == 2
    assert counts["zero_weight_row_count"] == 9
    assert counts["positive_weight_row_count"] == 201
    assert counts["published_compact_row_count"] == 210
    assert manifest["policy"]["gold_specimens_duplicated"] is False
    assert manifest["policy"]["cross_source_canonical_material_key"] == (
        "EOS TPU 1301"
    )
