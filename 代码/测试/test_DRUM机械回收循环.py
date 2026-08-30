import subprocess
import sys
from pathlib import Path
import 接入DRUM机械回收循环 as source

ROOT = Path(__file__).resolve().parents[2]


def test_release():
    f = source.build_release()
    assert len(f) == 240
    assert f.formulation_id.nunique() == 22
    assert f.hysteresis_energy_MJ_m3.notna().all()
    assert f.elastic_recovery_percent.notna().all()


def test_command():
    s = ROOT / "代码" / "接入DRUM机械回收循环.py"
    subprocess.run([sys.executable, str(s)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(s), "--检查"], cwd=ROOT, check=True)
