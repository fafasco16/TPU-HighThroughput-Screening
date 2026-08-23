import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "数据/原始" / "外部数据" / "新增开放数据"


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _load_script(relative_path: str):
    path = ROOT / relative_path
    module_name = f"tpu_regression_{path.stem}_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _valid_payload_evidence(oid: str, size: int) -> dict[str, object]:
    return {
        "状态码": 206,
        "初始主机": "objects.githubusercontent.com",
        "最终主机": "objects.githubusercontent.com",
        "初始URL_SHA256": "a" * 64,
        "最终URL_SHA256": "b" * 64,
        "重定向逐跳证据": [],
        "响应头": {
            "Content-Length": str(size),
            "Content-Range": f"bytes 0-{size - 1}/{size}",
        },
        "OID": oid,
        "声明字节": size,
        "本次起点": 0,
        "请求序号": 1,
        "最终载荷SHA256": oid,
        "最终载荷字节": size,
    }


def test_fourth_batch_counts_physical_specimens_by_46_families() -> None:
    directory = RAW / "Mendeley_TPU压缩打印DOE"
    if not directory.is_dir():
        pytest.skip("第四批 Mendeley 原始数据未在当前检出中分发")
    summary = _json(directory / "内容审计摘要.json")
    rows = _tsv(directory / "曲线审计清单.tsv")
    physical = [row for row in rows if row["physical_specimen"] == "true"]

    assert summary["audit_version"] == "1.2"
    assert summary["physical_specimens"] == len(physical) == 184
    assert summary["physical_specimen_family_count"] == 46
    assert len({row["specimen_family_id"] for row in physical}) == 46
    assert summary["ninjaflex_doe_cube_specimens"] == 72
    assert summary["ninjaflex_cylinder_specimens"] == 20
    assert summary["ninjaflex_solid_cube_control_specimens"] == 4
    assert summary["polyflex_cube_specimens"] == 88

    cylinders = [row for row in physical if row["geometry_or_evidence"] == "cylinder"]
    controls = [
        row
        for row in physical
        if row["geometry_or_evidence"] == "solid_cube_control"
    ]
    assert len(cylinders) == 20
    assert len(controls) == 4
    assert {row["specimen_family_id"] for row in controls} == {
        "ninjaflex|solid_cube_control|9999_bottom_layers|1|2"
    }
    assert all(
        int(row["direct_numeric_count"]) == 4
        and int(row["derived_formula_count"]) == 4
        and "raw_source_" in row["source_location"]
        for row in controls
    )


def test_fourth_batch_excludes_four_polyflex_pseudo_zeros_from_valid_derivations() -> None:
    directory = RAW / "Mendeley_TPU压缩打印DOE"
    if not directory.is_dir():
        pytest.skip("第四批 Mendeley 原始数据未在当前检出中分发")
    summary = _json(directory / "内容审计摘要.json")
    rows = _tsv(directory / "曲线审计清单.tsv")
    physical = [row for row in rows if row["physical_specimen"] == "true"]

    assert sum(int(row["direct_numeric_count"]) for row in physical) == 1_500
    assert sum(int(row["derived_formula_count"]) for row in physical) == 1_292
    assert sum(int(row["invalid_cached_formula_count"]) for row in physical) == 4
    assert sum(int(row["missing_numeric_count"]) for row in physical) == 4
    assert summary["valid_derived_formula_values"]["total"] == 1_292
    assert summary["invalid_cached_formula_values"] == {
        "polyflex_missing_input_pseudo_zero": 4
    }
    partial = next(row for row in rows if row["record_id"] == "polyflex_cube_r19")
    assert int(partial["derived_formula_count"]) == 4
    assert int(partial["invalid_cached_formula_count"]) == 4
    assert partial["parse_state"] == "partial_hold"
    assert summary["external_formula_cells_quarantined"] == [
        f"Peak stresses!{column}{row}"
        for row in range(10, 15)
        for column in ("F", "G")
    ]


def test_fourth_batch_acs_sources_remain_evidence_only() -> None:
    directories = sorted(path for path in RAW.glob("ACS_Figshare_*") if path.is_dir())
    if not directories:
        pytest.skip("第四批 ACS 支持信息未在当前检出中分发")
    assert len(directories) == 8
    evidence_rows = 0
    for directory in directories:
        summary = _json(directory / "内容审计摘要.json")
        rows = _tsv(directory / "曲线审计清单.tsv")
        evidence_rows += len(rows)
        assert summary["record_materialization"] == "none"
        assert summary["training_state"] == "evidence_only_hold"
        assert len(rows) == summary["evidence_group_count"]
        assert all(
            row["record_kind"] == "literature_evidence_group"
            and row["parse_state"] == "evidence_only_not_materialized"
            and row["physical_specimen"] == "false"
            and int(row["direct_numeric_count"]) == 0
            and int(row["derived_formula_count"]) == 0
            and int(row["invalid_cached_formula_count"]) == 0
            and row["training_split_materialized"] == "false"
            and row["training_weight_materialized"] == "false"
            for row in rows
        )
    assert evidence_rows == 33


def test_pcl_downloader_recovers_complete_part_after_evidence_first_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script("代码/获取/补采PCL_GitLFS十轨迹.py")
    payload = b"complete-git-lfs-payload"
    oid = hashlib.sha256(payload).hexdigest()
    point = module.指针(
        归档路径="fixed/10mer/vacuum/traj.trr.bz2",
        仓库路径="10mer/vacuum/traj.trr.bz2",
        oid=oid,
        字节=len(payload),
        指针字节=b"",
        Git对象SHA1="0" * 40,
        本地文件名="10mer_真空_默认_轨迹.trr.bz2",
    )
    payload_dir = tmp_path / "轨迹载荷"
    snapshot_dir = tmp_path / "来源快照"
    payload_dir.mkdir()
    snapshot_dir.mkdir()
    monkeypatch.setattr(module, "补采目录", tmp_path)
    monkeypatch.setattr(module, "载荷目录", payload_dir)
    monkeypatch.setattr(module, "快照目录", snapshot_dir)

    target = payload_dir / point.本地文件名
    partial = target.with_suffix(".bz2.part")
    partial.write_bytes(payload)
    evidence_path = snapshot_dir / f"载荷响应_{oid}.json"
    evidence_path.write_text(
        json.dumps(_valid_payload_evidence(oid, len(payload))), encoding="utf-8"
    )

    def network_must_not_run(*_args, **_kwargs):
        raise AssertionError("完整 .part 与预提交证据应无网络恢复")

    monkeypatch.setattr(module, "创建开启器", network_must_not_run)
    result = module.下载一个(point, {})
    assert result["最终载荷SHA256"] == oid
    assert target.read_bytes() == payload
    assert not partial.exists()


def test_pcl_auditor_rejects_inexact_persisted_range_evidence() -> None:
    module = _load_script("代码/审计/审计PCL_GitLFS十轨迹.py")
    payload = b"range-evidence"
    oid = hashlib.sha256(payload).hexdigest()
    point = {"OID": oid, "字节": len(payload)}
    evidence = _valid_payload_evidence(oid, len(payload))
    manifest_row = {"下载最终URL_SHA256": evidence["最终URL_SHA256"]}
    module.核验载荷响应证据(evidence, point, manifest_row)

    bad = json.loads(json.dumps(evidence))
    bad["响应头"]["Content-Range"] = (
        f"bytes 1-{len(payload) - 1}/{len(payload)}"
    )
    with pytest.raises(module.审计阻断, match="Range 证据不精确"):
        module.核验载荷响应证据(bad, point, manifest_row)
