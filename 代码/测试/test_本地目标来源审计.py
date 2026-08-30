import json
import subprocess
import sys
from pathlib import Path

import 审计本地目标来源 as local_audit


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_classify_source_targets():
    result = local_audit.classify_text(
        "TPU cyclic tensile recovery TGA formulation.xlsx"
    )
    assert result == {
        "toughness": True,
        "cyclic_recovery": True,
        "thermal_stability": True,
        "formulation": True,
        "raw_curve": False,
        "license": False,
    }


def test_local_audit_covers_all_source_directories():
    audit, queue, _ = local_audit.build_release()
    expected = sum(1 for path in local_audit.SOURCE_ROOT.iterdir() if path.is_dir())
    assert len(audit) == expected
    assert audit["source_directory"].is_unique
    assert set(queue["target_family"]) == {
        "toughness",
        "cyclic_recovery",
        "thermal_stability",
    }
    assert set(audit["priority_class"]) <= {"high", "medium", "low", "exclude"}
    assert audit["total_file_count"].gt(0).all()
    assert audit["inventory_fingerprint"].str.len().eq(64).all()
    assert queue["priority_score"].is_monotonic_decreasing
    qub = queue.loc[queue.source_directory.eq("QUB_生物基三重自修复TPU")]
    assert set(qub.target_family) == {
        "toughness",
        "cyclic_recovery",
        "thermal_stability",
    }
    assert qub["already_in_directed_target"].all()
    assert qub["next_action"].str.startswith("已接入").all()
    assert audit.loc[
        audit.source_directory.eq("QUB_生物基三重自修复TPU"), "audit_status"
    ].eq("materialized_all_detected_targets").all()


def test_release_and_check_command():
    script = ROOT / "代码" / "审计本地目标来源.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "本地来源审计发布清单.json").read_text(encoding="utf-8")
    )
    expected = sum(1 for path in local_audit.SOURCE_ROOT.iterdir() if path.is_dir())
    assert manifest["counts"]["source_directory_count"] == expected
    assert manifest["counts"]["queued_source_target_rows"] > 0
