import json
import subprocess
import sys
from pathlib import Path

import 接入TPMS_FEA输入 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_fea_catalog_is_input_only():
    frame = source.build_release()
    assert len(frame) == 9
    assert set(frame["topology"]) == {"P", "D", "IWP"}
    assert set(frame["strain_rate_s-1"]) == {0.001, 0.01, 0.1}
    assert frame["input_filename"].is_unique
    assert frame["input_only"].all()
    assert (~frame["simulation_output_available"]).all()
    assert frame["reported_response_count"].eq(0).all()
    assert frame["model_admission_layer"].eq("simulation_input_reference").all()
    assert frame["split_group"].nunique() == 3


def test_release_and_check_command():
    script = ROOT / "代码" / "接入TPMS_FEA输入.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "TPMS_FEA输入发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"] == {
        "input_file_count": 9,
        "topology_count": 3,
        "strain_rate_count": 3,
        "simulation_output_available_count": 0,
        "reported_response_count": 0,
        "published_compact_row_count": 9,
    }
    assert manifest["policy"]["simulation_input_is_performance_label"] is False
