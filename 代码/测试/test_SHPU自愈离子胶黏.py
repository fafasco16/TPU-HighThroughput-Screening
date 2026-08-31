import json
import subprocess
import sys
from pathlib import Path

import 接入SHPU自愈离子胶黏 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_shpu_modal_counts_and_boundaries():
    tensile, cyclic, interfacial = source.build_release()
    assert len(tensile) == 7
    assert set(tensile["material_code"]) == {
        "PE10/GY3",
        "PE10/GY5",
        "PE10/GY7",
        "SHPU_self_healing_optimized",
    }
    assert set(tensile["healing_time_h"]) == {0.0, 6.0, 12.0, 24.0}
    assert tensile["absolute_stress_available"].all()
    assert tensile["complete_toughness_available"].eq(False).all()
    assert tensile["tpu_core_supervision"].eq(False).all()
    assert len(cyclic) == 2
    assert cyclic["energy_retention_vs_first_load"].between(0.99, 1.02).all()
    assert cyclic["target_role"].str.contains("transfer_proxy").all()
    assert len(interfacial) == 3
    assert interfacial["interfacial_toughness_J_m2"].between(60, 300).all()
    assert interfacial["target_role"].str.contains("not_bulk_tpu").all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入SHPU自愈离子胶黏.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "SHPU自愈离子胶黏发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"] == {
        "tensile_endpoint_count": 7,
        "tensile_curve_point_count": 9836,
        "cyclic_endpoint_count": 2,
        "cyclic_curve_point_count": 57192,
        "interfacial_summary_count": 3,
        "published_compact_row_count": 12,
    }
    assert manifest["policy"]["tpu_core_supervision"] is False
    assert manifest["policy"]["interfacial_toughness_is_bulk_toughness"] is False
