import ast
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = ROOT / "代码" / "审计"


def _extract_definitions(filename: str, names: set[str]) -> dict[str, object]:
    """执行审计脚本中的原始定义，而不导入整份审计模块。

    合成文件名刻意不指向 ``代码/审计``，避免根项目 coverage 将审计专用脚本
    计入统计；函数体仍是从被测脚本 AST 原样编译得到的真实实现。
    """

    path = AUDIT_DIR / filename
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    found = {node.name for node in definitions}
    assert found == names, f"{filename} 缺少定义：{sorted(names - found)}"

    namespace: dict[str, object] = {
        "Path": Path,
        "Any": Any,
        "os": os,
        "tempfile": tempfile,
    }
    extracted = ast.fix_missing_locations(ast.Module(body=definitions, type_ignores=[]))
    exec(compile(extracted, f"<audit-safety:{filename}>", "exec"), namespace)
    return namespace


def test_six_source_atomic_write_keeps_previous_output_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _extract_definitions(
        "新增开放数据六源.py",
        {"AuditBlocked", "assert_output_allowed", "atomic_write"},
    )
    target = tmp_path / "内容审计摘要.json"
    target.write_bytes(b"previous")
    monkeypatch.setitem(module, "OUTPUT_WHITELIST", frozenset({target}))

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        module["atomic_write"](target, b"new")

    assert target.read_bytes() == b"previous"
    assert not list(tmp_path.glob("*.audit.tmp"))


def test_six_source_atomic_write_rejects_symbolic_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _extract_definitions(
        "新增开放数据六源.py",
        {"AuditBlocked", "assert_output_allowed", "atomic_write"},
    )
    real = tmp_path / "real.json"
    real.write_text("real", encoding="utf-8")
    link = tmp_path / "内容审计摘要.json"
    try:
        link.symlink_to(real)
    except OSError as exc:
        pytest.skip(f"当前 Windows 策略不允许创建测试符号链接：{exc}")
    monkeypatch.setitem(module, "OUTPUT_WHITELIST", frozenset({link}))

    with pytest.raises(module["AuditBlocked"], match="符号链接"):
        module["atomic_write"](link, b"new")
    assert real.read_text(encoding="utf-8") == "real"


def test_vinylogous_atomic_write_is_reproducible_and_allowlisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _extract_definitions(
        "共轭氨基甲酸酯玻璃体.py", {"assert_audit_output", "atomic_write"}
    )
    target = tmp_path / "内容审计摘要.json"
    monkeypatch.setitem(module, "AUDIT_OUTPUTS", {target})

    module["atomic_write"](target, b"stable\n")
    first = target.read_bytes()
    module["atomic_write"](target, b"stable\n")

    assert first == target.read_bytes() == b"stable\n"
    with pytest.raises(ValueError, match="白名单"):
        module["atomic_write"](tmp_path / "outside.json", b"blocked")
    assert not list(tmp_path.glob("*.audit.tmp"))


def test_drum_atomic_write_keeps_previous_output_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _extract_definitions("DRUM_TPUU.py", {"atomic_write"})
    target = tmp_path / "内容审计摘要.json"
    target.write_bytes(b"previous")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("injected DRUM replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected DRUM replace failure"):
        module["atomic_write"](target, b"new")

    assert target.read_bytes() == b"previous"
    assert not list(tmp_path.glob("*.audit.tmp"))


def test_drum_output_path_rejects_symbolic_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _extract_definitions("DRUM_TPUU.py", {"audit_output_path"})
    real = tmp_path / "real.json"
    real.write_text("real", encoding="utf-8")
    link = tmp_path / "内容审计摘要.json"
    try:
        link.symlink_to(real)
    except OSError as exc:
        pytest.skip(f"当前 Windows 策略不允许创建测试符号链接：{exc}")
    monkeypatch.setitem(module, "AUDIT_OUTPUTS", {link.name})
    monkeypatch.setitem(module, "DATASETS", {"机械回收": {"dir": tmp_path}})

    with pytest.raises(RuntimeError, match="符号链接或联接点"):
        module["audit_output_path"](tmp_path, link.name)
    assert real.read_text(encoding="utf-8") == "real"


def test_drum_weight_recommendations_match_multifidelity_policy():
    module = _extract_definitions("DRUM_TPUU.py", {"dataset_conclusion"})
    weights = module["dataset_conclusion"]("机械回收")["推荐权重"]

    assert "14BDO TPU/热固性PU桥接" not in weights
    assert weights["14BDO线性TPU发表汇总"] == 0.65
    assert weights["热固性PU桥接"] == 0.25
    assert "协议完整、输出可审计、收敛且按目标任务映射" in weights["模拟数据"]
    assert "未收敛" in weights["模拟数据"]


def test_sciencedb_image_audit_is_current_zero_with_conditional_future_ceiling():
    text = (AUDIT_DIR / "新增开放数据六源.py").read_text(encoding="utf-8")

    science_block = text[text.index("def audit_sciencedb") : text.index("def audit_agh")]
    assert '"当前所有任务权重上限": 0.0' in science_block
    assert '"未来非商业视觉任务权重上限": 0.15' in science_block
    assert 'lambda path, _: "0.00"' in science_block
    assert '"视觉表征权重上限": 0.15' not in science_block


def test_historical_audit_alignment_script_uses_authoritative_caps_and_atomic_write():
    text = (AUDIT_DIR / "历史审计策略对齐.py").read_text(encoding="utf-8")

    assert 'POLICY_VERSION = "multi-fidelity-admission-weight-v0.2.6"' in text
    assert '"experimental_Tdeblock_label_ceiling": 1.0' in text
    assert '"experimentally_mapped_DFT_QoI_or_calibrated_descriptor_ceiling": 0.50' in text
    assert '"通用PU力学曲线表征预训练上限": 0.35' in text
    assert 'transfer["suggested_relative_weight"] = 0.25' in text
    assert "tempfile.mkstemp" in text
    assert "os.fsync" in text
    assert "os.replace" in text


def test_figshare_script_uses_atomic_replace_and_reparse_guards():
    text = (AUDIT_DIR / "Figshare_化学辅助源.mjs").read_text(encoding="utf-8")

    assert "AUDIT_OUTPUTS" in text
    assert "fs.lstat" in text
    assert "fs.realpath" in text
    assert 'fs.open(temporary, "wx"' in text
    assert "handle.sync()" in text
    assert "fs.rename(temporary, resolved)" in text


def test_audit_environment_records_external_node_dependency_boundary():
    environment = json.loads((AUDIT_DIR / "审计环境.json").read_text(encoding="utf-8"))

    assert environment["python"]["dependency_group"] == "independent_requirements_lock"
    assert environment["python"]["lock_file"] == "代码/审计/requirements.lock"
    assert environment["python"]["lock_sha256"] == (
        "429088beeea5924ca01c556e9ff71362a215b6cf1f2574a5ee71a5a307b60acf"
    )
    assert hashlib.sha256(
        (AUDIT_DIR / "requirements.lock").read_bytes()
    ).hexdigest() == environment["python"]["lock_sha256"]
    assert environment["artifact_tool"]["package"] == "@oai/artifact-tool"
    assert environment["artifact_tool"]["tested_version"] == "2.8.24"
    assert environment["artifact_tool"]["locked_by_repository"] is False
    assert environment["artifact_tool"]["version_change_gate"].startswith(
        "rerun_two_complete_audits"
    )


def test_tracked_audit_scripts_have_no_machine_absolute_or_runtime_timestamp():
    script_paths = sorted(AUDIT_DIR.glob("*.py")) + sorted(AUDIT_DIR.glob("*.mjs"))
    script_paths += sorted(AUDIT_DIR.glob("*.ps1"))

    assert script_paths
    for path in script_paths:
        text = path.read_text(encoding="utf-8")
        assert "E:\\数据\\TPU" not in text
        assert "临时构建" not in text
        assert "datetime.now" not in text
        assert "Date.now(" not in text
