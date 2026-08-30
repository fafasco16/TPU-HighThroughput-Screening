import subprocess
import sys
from pathlib import Path
import 生成扩充数据总账 as source

ROOT = Path(__file__).resolve().parents[2]


def test_release():
    f = source.build_release()
    assert len(f) == 16
    assert f.package_id.is_unique
    assert f.row_count.gt(0).all()
    assert f.data_sha256.str.len().eq(64).all()
    assert f.mapping_completeness_score.between(0, 1).all()
    assert f.next_mapping_action.str.len().gt(0).all()
    assert f.expansion_priority_score.is_monotonic_decreasing
    assert {
        "qub_self_healing_tensile",
        "qub_self_healing_cycle_proxy",
        "qub_self_healing_tga",
    } <= set(f.package_id)
    assert f.loc[f.package_id.str.startswith("qub_"), "license"].eq("CC-BY-4.0").all()
    assert {
        "dib_shape_memory_tensile",
        "dib_shape_memory_cycle_proxy",
        "dib_shape_memory_thermal",
    } <= set(f.package_id)
    assert f.loc[
        f.package_id.str.startswith("dib_"), "model_admission_layer"
    ].eq("polyurethane_transfer").all()


def test_command():
    s = ROOT / "代码" / "生成扩充数据总账.py"
    subprocess.run([sys.executable, str(s)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(s), "--检查"], cwd=ROOT, check=True)
