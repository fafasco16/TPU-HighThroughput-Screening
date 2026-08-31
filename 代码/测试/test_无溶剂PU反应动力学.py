import json
import subprocess
import sys
from pathlib import Path

import 接入无溶剂PU反应动力学 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_condition_level_kinetic_endpoints():
    frame = source.build_release()
    assert len(frame) == 21
    assert frame["condition_id"].is_unique
    assert set(frame["macrodiol_code"]) == {"PCL", "PDEA", "PEA", "PEG"}
    assert set(frame["diisocyanate_code"]) == {"TDI", "HDI"}
    assert frame["source_measurement_row_count"].sum() == 171
    assert frame["missing_time_measurement_count"].sum() == 2
    assert frame["last_measurement_time_h"].ge(
        frame["first_measurement_time_h"]
    ).all()
    assert frame["NCO_retention_at_last_measurement"].ge(0).all()
    assert frame["target_role"].eq(
        "synthesis_feasibility_NCO_consumption_kinetics"
    ).all()
    assert frame["direct_mechanical_supervision"].eq(False).all()  # noqa: E712


def test_release_and_check_command():
    script = ROOT / "代码" / "接入无溶剂PU反应动力学.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "无溶剂PU反应动力学发布清单.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["counts"] == {
        "reaction_condition_count": 21,
        "reaction_system_count": 5,
        "macrodiol_identity_count": 4,
        "diisocyanate_identity_count": 2,
        "source_measurement_row_count": 171,
        "admitted_measurement_count": 169,
        "conditional_missing_time_count": 2,
        "zero_NCO_measurement_count": 23,
        "published_compact_row_count": 21,
    }
    assert manifest["policy"]["missing_times_imputed"] is False
    assert manifest["policy"]["direct_toughness_or_cycle_label"] is False
