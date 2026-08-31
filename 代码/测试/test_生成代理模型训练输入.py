import json
import subprocess
import sys
from pathlib import Path

import 生成代理模型训练输入 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_proxy_training_summary_is_ready_but_core_gated():
    summary = source._summary(*source._load_inputs()[:3])
    assert set(summary["objective_id"]) == {
        "toughness",
        "cyclic_recovery",
        "thermal_stability",
        "computed_multitask_auxiliary",
    }
    assert summary["proxy_training_ready"].all()
    assert summary.loc[
        summary.objective_id.isin(source.OBJECTIVES),
        "strict_core_structure_model_ready",
    ].eq(False).all()
    assert summary.loc[
        summary.objective_id.eq("toughness"),
        "independent_unit_count",
    ].iloc[0] == 185


def test_release_and_check_command():
    script = ROOT / "代码" / "生成代理模型训练输入.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "代理模型训练输入发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"] == {
        "objective_count": 3,
        "summary_row_count": 4,
        "source_package_row_count": 67,
        "proxy_ready_objective_count": 3,
        "strict_core_structure_model_ready_objective_count": 0,
    }
    assert manifest["policy"]["group_split_required"] is True
    assert manifest["policy"]["model_training_started"] is False
