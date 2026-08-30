import subprocess
import sys
from pathlib import Path
import 接入PHCU双目标 as source

ROOT = Path(__file__).resolve().parents[2]


def test_release():
    f = source.build_release()
    assert len(f) == 6
    assert f.toughness_MJ_m3.gt(0).all()
    assert f.T5_degC.notna().all()
    assert f.hu_mol_percent.tolist() == [10, 20, 30, 40, 50, 70]


def test_command():
    s = ROOT / "代码" / "接入PHCU双目标.py"
    subprocess.run([sys.executable, str(s)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(s), "--检查"], cwd=ROOT, check=True)
