import subprocess
import sys
from pathlib import Path
import 接入标准化弹性体拉伸 as source

ROOT = Path(__file__).resolve().parents[2]


def test_release():
    f = source.build_release()
    assert len(f) == 13
    assert f.formulation_id.nunique() == 2
    assert f.toughness_MJ_m3.gt(0).all()


def test_command():
    s = ROOT / "代码" / "接入标准化弹性体拉伸.py"
    subprocess.run([sys.executable, str(s)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(s), "--检查"], cwd=ROOT, check=True)
