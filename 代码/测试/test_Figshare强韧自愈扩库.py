import json
import subprocess
import sys
from pathlib import Path

import pytest

import 接入Figshare强韧自愈TPU as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_summary_and_curve_release():
    summary, curves = source.build_release()
    assert len(summary) == 5
    assert curves["curve_id"].nunique() == 7
    virgin = summary.query("material_code == 'C-IP-SS' and state == 'Virgin'").iloc[0]
    assert virgin["tensile_strength_MPa"] == pytest.approx(42.88)
    assert virgin["elongation_at_break_percent"] == pytest.approx(480)
    assert virgin["toughness_MJ_m3"] == pytest.approx(75.054)
    healed = summary.query("material_code == 'C-IP-SS' and state == 'Healed'").iloc[0]
    assert healed["toughness_retention_percent"] == pytest.approx(100 * 48.348 / 75.054)


def test_release_and_check_command():
    script = ROOT / "代码" / "接入Figshare强韧自愈TPU.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "Figshare强韧自愈发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"]["summary_rows"] == 5
    assert manifest["counts"]["stress_strain_curve_count"] == 7
