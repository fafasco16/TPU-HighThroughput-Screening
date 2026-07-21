import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

CODE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_DIR))

ROOT = CODE_DIR.parent
INVENTORY_SCRIPT = CODE_DIR / "生成数据总账.py"
INVENTORY_OUTPUTS = (
    ROOT / "结果" / "Gold_V_候选.csv.gz",
    ROOT / "结果" / "Gold_E_实验表格.csv.gz",
    ROOT / "结果" / "Gold_C_计算性能.csv.gz",
    ROOT / "结果" / "数据规模总账.csv",
    ROOT / "结果" / "样本清单.csv.gz",
    ROOT / "结果" / "数据总账.json",
    ROOT / "结果" / "数据总账说明.md",
)


def _inventory_hashes() -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in INVENTORY_OUTPUTS
    }


@pytest.fixture(scope="session")
def generated_inventory_outputs() -> dict[str, str]:
    """全套测试共享一次总账重建，避免百万行产物被每个模块重复生成。"""

    subprocess.run(
        [sys.executable, str(INVENTORY_SCRIPT)], cwd=ROOT, check=True
    )
    return _inventory_hashes()


@pytest.fixture(scope="session")
def regenerated_inventory_outputs(
    generated_inventory_outputs: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """全套测试只额外重建一次，用于跨模块字节确定性门禁。"""

    subprocess.run(
        [sys.executable, str(INVENTORY_SCRIPT)], cwd=ROOT, check=True
    )
    return generated_inventory_outputs, _inventory_hashes()
