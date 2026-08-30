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
    dib = queue.loc[
        queue.source_directory.eq("DataInBrief_聚氨酯形状记忆多模态原始数据")
    ]
    assert set(dib.target_family) == {
        "toughness",
        "cyclic_recovery",
        "thermal_stability",
    }
    assert dib["already_in_directed_target"].all()
    commercial = queue.loc[
        queue.source_directory.eq("Mendeley_商业TPU温度疲劳多工况")
    ]
    assert set(commercial.target_family) == {"toughness", "cyclic_recovery"}
    assert commercial["already_in_directed_target"].all()
    tecoflex = queue.loc[
        queue.source_directory.eq("Zenodo_Tecoflex药物复合TPU")
    ]
    assert set(tecoflex.target_family) == {"toughness", "thermal_stability"}
    assert tecoflex["already_in_directed_target"].all()
    iir = queue.loc[
        queue.source_directory.eq("第十八批实验_IIR-OH聚氨酯")
    ]
    assert set(iir.target_family) == {"toughness", "cyclic_recovery"}
    assert iir["already_in_directed_target"].all()
    tpu95a = queue.loc[
        queue.source_directory.eq("Mendeley_TPU95A_TPMS应变率力学")
    ]
    assert set(tpu95a.target_family) == {"toughness", "cyclic_recovery"}
    assert tpu95a.loc[
        tpu95a.target_family.eq("cyclic_recovery"),
        "already_in_directed_target",
    ].all()
    assert not tpu95a.loc[
        tpu95a.target_family.eq("toughness"),
        "already_in_directed_target",
    ].any()
    foam = queue.loc[
        queue.source_directory.eq("MaterialsCloud_商用PU泡沫多轴断裂力学")
    ]
    assert set(foam.target_family) == {"toughness", "cyclic_recovery"}
    assert foam.loc[
        foam.target_family.eq("toughness"), "already_in_directed_target"
    ].all()
    assert not foam.loc[
        foam.target_family.eq("cyclic_recovery"),
        "already_in_directed_target",
    ].any()


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
