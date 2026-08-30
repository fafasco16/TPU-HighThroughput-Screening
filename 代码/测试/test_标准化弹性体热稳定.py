import subprocess
import sys
from pathlib import Path
import 接入标准化弹性体热稳定 as source

ROOT = Path(__file__).resolve().parents[2]


def test_release():
    f = source.build_release()
    assert len(f) == 2
    assert f.T5_degC.notna().all()
    assert f.T10_degC.notna().all()
    assert set(f.formulation_id) == {"Cheetah", "Filaflex 60A"}


def test_command():
    s = ROOT / "代码" / "接入标准化弹性体热稳定.py"
    subprocess.run([sys.executable, str(s)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(s), "--检查"], cwd=ROOT, check=True)
