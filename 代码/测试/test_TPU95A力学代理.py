import json
import subprocess
import sys
from pathlib import Path

import 接入TPU95A力学代理 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_tensile_load_extension_endpoints():
    tensile, relaxation = source.build_release()
    assert len(tensile) == 3
    assert tensile["test_run_id"].is_unique
    assert tensile["material_grade"].eq("eSUN eTPU-95A").all()
    assert tensile["maximum_engineering_strain_percent"].between(280, 305).all()
    assert tensile["maximum_load_N"].between(700, 760).all()
    assert tensile["load_extension_work_to_max_extension_J"].between(34, 37).all()
    assert tensile["absolute_tensile_stress_available"].eq(False).all()
    assert tensile["complete_toughness_available"].eq(False).all()
    assert tensile["historical_mirror_rematerialized"].all()
    assert tensile["incremental_scientific_sample_contribution"].eq(0).all()
    assert len(relaxation) == 6


def test_relaxation_endpoints():
    _, relaxation = source.build_release()
    assert set(relaxation["nominal_strain_fraction"]) == {0.1, 0.2}
    assert relaxation.groupby("nominal_strain_fraction")["replicate_id"].nunique().eq(3).all()
    assert relaxation["retention_at_1s"].between(0.70, 0.77).all()
    assert relaxation["retention_at_10s"].between(0.55, 0.62).all()
    assert relaxation["retention_at_50s"].between(0.48, 0.55).all()
    assert relaxation["retention_at_100s_nearest"].between(0.47, 0.51).all()
    assert relaxation["time_to_50pct_retention_s"].between(50, 100).all()
    assert relaxation["time_to_50pct_status"].eq(
        "observed_within_100s"
    ).all()
    assert relaxation["cyclic_target_role"].eq(
        "stress_relaxation_transfer_proxy"
    ).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入TPU95A力学代理.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "TPU95A力学代理发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"] == {
        "material_grade_count": 1,
        "tensile_run_count": 3,
        "tensile_source_point_count": 15468,
        "relaxation_run_count": 6,
        "relaxation_source_point_count": 21232,
        "published_compact_row_count": 9,
        "incremental_scientific_sample_contribution": 0,
    }
    assert manifest["source"]["license"] == "CC-BY-4.0"
    assert manifest["policy"]["historical_mirror_rematerialized"] is True
    assert manifest["policy"]["raw_curves_republished"] is False
