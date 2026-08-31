import json
import subprocess
import sys
from pathlib import Path

import 接入FDM_TPU晶格力学 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_selected_source_curves_only():
    frame = source.build_release()
    assert len(frame) == 57
    assert frame["curve_point_count"].sum() == 544052
    assert frame.groupby("test_type").size().to_dict() == {
        "基材弯曲": 12,
        "基材拉伸": 17,
        "晶格压缩": 14,
        "晶格弯曲": 14,
    }
    assert frame["source_summary_state"].eq("selected").all()
    assert frame["peak_stress_MPa"].gt(0).all()
    assert frame["curve_area_MJ_m3"].ge(0).all()
    assert frame["material_grade"].eq(
        "FDM_printed_TPU_unknown_grade"
    ).all()
    assert frame["curve_area_semantics"].eq(
        "stress_strain_energy_absorption_proxy_not_fracture_toughness"
    ).all()
    assert frame["model_admission_layer"].eq(
        "FDM_TPU_application_transfer"
    ).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入FDM_TPU晶格力学.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "FDM_TPU晶格力学发布清单.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["counts"] == {
        "material_grade_count": 1,
        "source_curve_count": 76,
        "selected_curve_count": 57,
        "conflict_curve_count": 10,
        "not_selected_curve_count": 9,
        "selected_curve_point_count": 544052,
        "published_compact_row_count": 57,
        "recognized_simulation_run_count": 0,
    }
    assert manifest["policy"]["raw_curves_republished"] is False
    assert manifest["policy"]["curve_area_is_fracture_toughness"] is False
