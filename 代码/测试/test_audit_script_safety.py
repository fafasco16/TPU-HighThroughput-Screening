import ast
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.request import Request

import pytest


ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = ROOT / "代码" / "审计"


def _load_script(relative_path: str):
    path = ROOT / relative_path
    module_name = f"tpu_safety_{path.stem}_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


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

    assert 'POLICY_VERSION = "multi-fidelity-admission-weight-v0.2.9"' in text
    assert '"experimental_Tdeblock_label_ceiling": 1.0' in text
    assert '"experimentally_mapped_DFT_QoI_or_calibrated_descriptor_ceiling": 0.50' in text
    assert '"通用PU力学曲线表征预训练上限": 0.35' in text
    assert 'transfer["suggested_relative_weight"] = 0.25' in text
    assert '"accepted_previous_alignment_sha256"' in text
    assert '"multi-fidelity-admission-weight-v0.2.6"' in text
    assert '"multi-fidelity-admission-weight-v0.2.7"' in text
    assert "旧策略摘要不在精确迁移白名单" in text
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
        assert "数据/临时/构建缓存" not in text
        assert "datetime.now" not in text
        assert "Date.now(" not in text


@pytest.mark.parametrize(
    ("filename", "source_names"),
    [
        (
            "新增开放数据核心与镜像.py",
            ("QUB_生物基三重自修复TPU", "Mendeley_TPU95A_TPMS应变率力学"),
        ),
        (
            "新增开放数据标准力学三源.py",
            (
                "MaterialsCloud_商用PU泡沫多轴断裂力学",
                "ScienceDB_微孔PU动态力学",
                "Texas_湿干单根电纺PU纤维力学",
            ),
        ),
        (
            "新增开放数据工作簿双源.py",
            ("Mendeley_SLS_TPU工艺力学", "Figshare_热固PU原子经济升级回收"),
        ),
        (
            "新增开放数据受限与专有格式两源.py",
            ("Mendeley_热可逆超分子PU宽应变率", "Bath_多牌号PU泡沫多模态表征"),
        ),
    ],
)
def test_remaining_source_audit_scripts_are_fail_closed_and_atomic(
    filename: str, source_names: tuple[str, ...]
):
    text = (AUDIT_DIR / filename).read_text(encoding="utf-8")

    for source_name in source_names:
        assert source_name in text
    assert "OUTPUT_WHITELIST" in text
    assert "tempfile.mkstemp" in text
    assert "os.fsync" in text
    assert "os.replace" in text
    assert "datetime.now" not in text


def test_sls_legacy_excel_reader_is_read_only_and_never_saves():
    text = (AUDIT_DIR / "读取SLS旧版XLS.ps1").read_text(encoding="utf-8")

    assert "ReadOnly" in text
    assert "Workbooks.Open" in text
    assert ".Save(" not in text
    assert "SaveAs" not in text
    assert "BitConverter" in text
    assert "ToHexString" not in text


def test_standard_elastomer_legacy_excel_reader_is_fixed_read_only_and_never_saves():
    helper = AUDIT_DIR / "读取标准弹性体旧版XLS.ps1"
    text = helper.read_text(encoding="utf-8")
    audit_text = (AUDIT_DIR / "新增开放数据第二批四源.py").read_text(
        encoding="utf-8"
    )

    assert "Melting.zip" in text
    assert "Melting/viscosity/Filaflex 60A.xls" in text
    assert "Workbooks.Open" in text
    assert "ReadOnly" in text
    assert ".Save(" not in text
    assert "SaveAs" not in text
    assert "AutomationSecurity" in text
    assert "CreateDirectory" in text
    assert "Remove-Item" in text
    assert "curve_point_count" in text
    assert "XLS_READER" in audit_text
    assert "subprocess.run" in audit_text
    assert '"curve_point_count": 2_094' in audit_text


def test_workbook_audit_uses_streaming_ooxml_and_authoritative_weight_caps():
    text = (AUDIT_DIR / "新增开放数据工作簿双源.py").read_text(encoding="utf-8")

    assert "zipfile.ZipFile" in text
    assert "ElementTree.iterparse" in text
    assert 'events=("start", "end")' in text
    assert "parent.remove(element)" in text
    assert "resident_rows != 0 or element_stack" in text
    assert '"maximum_active_row_value_buffers": 1' in text
    assert '"completed_row_elements_retained_after_parse": 0' in text
    assert "maximum_buffered_sheet_rows" not in text
    assert '"policy_authority": "multi-fidelity-admission-weight-v0.2.9"' in text
    assert '"source_weight_ceiling": 0.35' in text
    assert '"source_weight_ceiling": 0.25' in text
    assert '"candidate_eligible_after_governance_materialization": True' in text
    assert '"training_weight_materialized": False' in text
    assert '"split_materialized": False' in text
    assert '"split_group_key": "dataset_doi|feedstock"' in text
    assert '"split_group_key": "dataset_doi|feedstock_id"' not in text
    assert '"admit": True' not in text
    assert "0.20-0.30" not in text


def test_restricted_and_proprietary_sources_remain_current_zero():
    text = (AUDIT_DIR / "新增开放数据受限与专有格式两源.py").read_text(
        encoding="utf-8"
    )

    assert '"current_weight_ceiling": 0.0' in text
    assert '"rights_evidence_state": "evidence_missing"' in text
    assert "requests." not in text
    assert "urlopen(" not in text


def test_standard_mechanics_audit_fails_closed_if_excluded_sffe_appears():
    text = (AUDIT_DIR / "新增开放数据标准力学三源.py").read_text(
        encoding="utf-8"
    )

    assert 'set(official_entries) != {"PUF.zip", "README.txt", "SFFE.zip"}' in text
    assert 'local_root_archives != {"PUF.zip"}' in text
    assert 'base / "SFFE.zip"' in text
    assert "必须先建立独立范围和审计规则" in text


def test_audit_readme_declares_current_source_identity_coverage():
    text = (AUDIT_DIR / "README.md").read_text(encoding="utf-8")

    assert "46 个一级目录" in text
    assert "45 个独立来源身份" in text
    assert "PCL_GitLFS轨迹补采" in text
    assert "不增加来源身份" in text
    for filename in (
        "新增开放数据核心与镜像.py",
        "新增开放数据标准力学三源.py",
        "新增开放数据工作簿双源.py",
        "新增开放数据受限与专有格式两源.py",
        "新增开放数据第二批四源.py",
        "新增开放数据第三批Zenodo实验双源.py",
        "新增开放数据第三批Mendeley三源.py",
        "新增开放数据第三批模拟四源.py",
        "审计PCL_GitLFS十轨迹.py",
        "新增开放数据第四批精选源.py",
        "读取SLS旧版XLS.ps1",
    ):
        assert (AUDIT_DIR / filename).is_file()


def test_restricted_audit_atomic_write_is_allowlisted_and_reproducible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _extract_definitions(
        "新增开放数据受限与专有格式两源.py",
        {"AuditBlocked", "assert_output_allowed", "atomic_write"},
    )
    target = tmp_path / "内容审计摘要.json"
    monkeypatch.setitem(module, "OUTPUT_WHITELIST", frozenset({target}))

    module["atomic_write"](target, b"stable\n")
    first = target.read_bytes()
    module["atomic_write"](target, b"stable\n")

    assert first == target.read_bytes() == b"stable\n"
    with pytest.raises(module["AuditBlocked"], match="白名单"):
        module["atomic_write"](tmp_path / "outside.json", b"blocked")
    assert not list(tmp_path.glob("*.audit.tmp"))


@pytest.mark.parametrize(
    "filename",
    [
        "新增开放数据核心与镜像.py",
        "新增开放数据标准力学三源.py",
        "新增开放数据工作簿双源.py",
    ],
)
def test_remaining_audit_atomic_replace_failure_preserves_previous_output(
    filename: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _extract_definitions(
        filename,
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


def test_second_batch_downloader_is_fixed_allowlist_and_fail_closed():
    path = ROOT / "代码" / "获取" / "下载第二批开放数据四源.py"
    text = path.read_text(encoding="utf-8")

    for record_id in ("14983287", "15370425", "6390478", "23635998"):
        assert record_id in text
    for source_directory in (
        "Zenodo_标准化弹性体表征",
        "Zenodo_PU微球复合材料拉伸",
        "Figshare_PU高低速变形后应力松弛",
        "Zenodo_TPU1301热黏弹黏塑本构",
    ):
        assert source_directory in text
    assert "EXPECTED_FILE_COUNT = 11" in text
    assert "EXPECTED_EXCLUDED_FILE_COUNT = 12" in text
    assert '"https"' in text
    assert "ALLOWED_DOWNLOAD_HOSTS" in text
    assert 'part_suffix = ".part"' in text
    assert "os.replace" in text
    assert "datetime.now" not in text
    assert 'item["local_state"] = "verified_present"' in text
    assert 'item["local_state"], item["local_sha256"]' not in text


@pytest.mark.parametrize(
    "target_url",
    [
        "https://example.invalid/payload",
        "http://zenodo.org/payload",
    ],
)
def test_second_batch_downloader_rejects_cross_host_and_https_downgrade_redirects(
    target_url: str,
):
    module = _load_script("代码/获取/下载第二批开放数据四源.py")
    handler = module.StrictRedirectHandler()
    request = Request("https://zenodo.org/api/records/14983287")

    with pytest.raises(module.AcquisitionBlocked, match="拒绝非白名单下载端点"):
        handler.redirect_request(request, None, 302, "Found", {}, target_url)


def test_second_batch_downloader_rejects_disallowed_final_response_endpoint():
    module = _load_script("代码/获取/下载第二批开放数据四源.py")

    class FakeResponse:
        @staticmethod
        def geturl() -> str:
            return "https://example.invalid/final"

    with pytest.raises(module.AcquisitionBlocked, match="拒绝非白名单下载端点"):
        module.require_allowed_response_endpoint(FakeResponse())


@pytest.mark.parametrize(
    ("headers", "expected_message"),
    [
        (
            {"Content-Length": "900", "Content-Range": "bytes 99-998/1000"},
            "Content-Range",
        ),
        (
            {"Content-Length": "899", "Content-Range": "bytes 100-999/1000"},
            "Content-Length",
        ),
    ],
)
def test_second_batch_downloader_rejects_inexact_resume_headers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    expected_message: str,
):
    module = _load_script("代码/获取/下载第二批开放数据四源.py")
    target = tmp_path / "payload.bin"
    partial = tmp_path / "payload.bin.part"
    partial.write_bytes(b"x" * 100)

    class FakeResponse:
        status = 206

        def __init__(self) -> None:
            self.headers = headers
            self.closed = False

        @staticmethod
        def getcode() -> int:
            return 206

        def close(self) -> None:
            self.closed = True

    response = FakeResponse()
    monkeypatch.setattr(module, "open_request", lambda request, timeout: response)

    with pytest.raises(module.AcquisitionBlocked, match=expected_message):
        module.download_file(
            "https://zenodo.org/api/files/frozen/payload.bin",
            target,
            1000,
            "0" * 32,
        )

    assert response.closed is True
    assert partial.read_bytes() == b"x" * 100


def test_second_batch_audit_is_fail_closed_atomic_and_training_closed():
    path = AUDIT_DIR / "新增开放数据第二批四源.py"
    text = path.read_text(encoding="utf-8")

    for source_directory in (
        "Zenodo_标准化弹性体表征",
        "Zenodo_PU微球复合材料拉伸",
        "Figshare_PU高低速变形后应力松弛",
        "Zenodo_TPU1301热黏弹黏塑本构",
    ):
        assert source_directory in text
    for boundary_marker in (
        "NinjaFlex 90A",
        "__MACOSX",
        "Uniaxial_compression_2CV_2p78E-3_RT_PA12.csv",
        "Relaxation_7H_1E-1_RT_TPU.csv",
        '"header_label": "6V"',
    ):
        assert boundary_marker in text
    assert "OUTPUT_WHITELIST" in text
    assert "tempfile.mkstemp" in text
    assert "os.fsync" in text
    assert "os.replace" in text
    assert "zipfile.ZipFile" in text
    assert ".testzip()" in text
    assert "MAX_COMPRESSION_RATIO" in text
    assert "MAX_UNCOMPRESSED_BYTES" in text
    assert "pickletools.genops" in text
    assert "pickle.load" not in text
    assert "112_358_792" in text
    assert "5_818_564" in text
    assert "1_459_510" in text
    assert "Figure_4b" in text
    assert "protocol_consistency" in text
    assert '"training_split_created": False' in text
    assert '"training_weight_materialized": False' in text
    assert "urlopen(" not in text
    assert "requests." not in text
    assert "datetime.now" not in text


def test_second_batch_audit_atomic_replace_failure_preserves_previous_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _extract_definitions(
        "新增开放数据第二批四源.py",
        {"AuditBlocked", "assert_output_allowed", "atomic_write"},
    )
    target = tmp_path / "内容审计摘要.json"
    target.write_bytes(b"previous")
    monkeypatch.setitem(module, "OUTPUT_WHITELIST", frozenset({target}))

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("injected second-batch replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected second-batch replace failure"):
        module["atomic_write"](target, b"new")

    assert target.read_bytes() == b"previous"
    assert not list(tmp_path.glob("*.audit.tmp"))
