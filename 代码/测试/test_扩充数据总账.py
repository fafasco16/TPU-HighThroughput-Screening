import subprocess
import sys
from pathlib import Path
import 生成扩充数据总账 as source

ROOT = Path(__file__).resolve().parents[2]


def test_release():
    f = source.build_release()
    assert len(f) == 10
    assert f.package_id.is_unique
    assert f.row_count.gt(0).all()
    assert f.data_sha256.str.len().eq(64).all()


def test_command():
    s = ROOT / "代码" / "生成扩充数据总账.py"
    subprocess.run([sys.executable, str(s)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(s), "--检查"], cwd=ROOT, check=True)
