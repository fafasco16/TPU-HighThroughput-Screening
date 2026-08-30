import subprocess
import sys
from pathlib import Path
import 接入DRUM机械回收TGA as source

ROOT = Path(__file__).resolve().parents[2]


def test_release():
    f = source.build_release()
    assert len(f) == 19
    assert f.macrodiol_family.nunique() == 4
    assert f.T5_degC.notna().all()
    assert f.T10_degC.notna().all()
    assert f.T50_degC.notna().all()


def test_command():
    s = ROOT / "代码" / "接入DRUM机械回收TGA.py"
    subprocess.run([sys.executable, str(s)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(s), "--检查"], cwd=ROOT, check=True)
