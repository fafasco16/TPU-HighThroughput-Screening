import json
import subprocess
import sys
from pathlib import Path

import 接入TPU1301机械代理 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_tensile_and_relaxation_materialization():
    tensile, relaxation = source.build_release()
    assert len(tensile) == 17
    assert len(relaxation) == 3
    assert tensile["curve_point_count"].sum() == 77450
    assert relaxation["curve_point_count"].sum() == 14901
    assert tensile["material_grade"].eq("EOS TPU 1301").all()
    assert tensile["tensile_strength_MPa"].gt(0).all()
    assert tensile["toughness_MJ_m3"].gt(0).all()
    assert relaxation["retention_at_100s"].between(0, 1).all()
    assert relaxation["source_member"].str.contains("Relaxation_7H").sum() == 0
    assert relaxation["target_role"].eq(
        "stress_relaxation_recovery_proxy"
    ).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入TPU1301机械代理.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "TPU1301机械代理发布清单.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["counts"] == {
        "material_grade_count": 1,
        "tensile_run_count": 17,
        "relaxation_run_count": 3,
        "quarantined_identity_conflict_count": 1,
        "source_point_count": 92351,
        "published_compact_row_count": 20,
    }
    assert manifest["policy"]["raw_curves_republished"] is False
    assert manifest["policy"]["relaxation_is_proxy_not_direct_cycles"] is True
