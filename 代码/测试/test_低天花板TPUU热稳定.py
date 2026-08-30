import json
import subprocess
import sys
from pathlib import Path

import 接入低天花板TPUU热稳定 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_tga_endpoints_close_four_existing_tpuu_codes():
    frame = source.build_release()
    assert frame["formulation_id"].tolist() == [
        "TPUU-C",
        "TPUU-D",
        "TPUU-R",
        "TPUU-S",
    ]
    assert len(frame) == 4
    assert frame["point_count"].sum() == 25216
    assert frame["T5_degC"].between(275, 311).all()
    assert frame["T10_degC"].between(284, 327).all()
    assert frame["T50_degC"].between(302, 382).all()
    assert frame["target_role"].eq("direct_thermal_stability").all()
    assert frame["license"].eq("CC0-1.0").all()
    assert frame["source_sha256"].str.len().eq(64).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入低天花板TPUU热稳定.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "低天花板TPUU热稳定发布清单.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["counts"] == {
        "material_count": 4,
        "curve_count": 4,
        "source_point_count": 25216,
        "published_compact_row_count": 4,
    }
    assert manifest["policy"]["raw_curves_republished"] is False
