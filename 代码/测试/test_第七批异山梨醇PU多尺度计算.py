"""第七批异山梨醇动态PU多尺度计算源的定向回归测试。"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "代码" / "审计" / "第七批异山梨醇PU多尺度计算.py"
SOURCE = ROOT / "数据/原始/外部数据/新增开放数据/第七批计算_异山梨醇动态聚氨酯多尺度力学"
ARCHIVE = SOURCE / "poly-mech-props_d2d3229.zip"


def _require_archive() -> None:
    if not ARCHIVE.is_file():
        pytest.skip("第七批计算官方固定提交归档未在当前检出中分发")


def _read_tsv(name: str) -> list[dict[str, str]]:
    with (SOURCE / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _load_module():
    spec = importlib.util.spec_from_file_location("batch7_isohexide_pu_audit", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_archive_identity_and_materialized_counts_are_frozen() -> None:
    _require_archive()
    assert ARCHIVE.stat().st_size == 73_579_903
    assert hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() == (
        "df4965564fdb0aa39ddf84cb3969509f6ceb9a87f9e21709ede60a75a845d68b"
    )
    summary = json.loads((SOURCE / "内容审计摘要.json").read_text(encoding="utf-8"))
    assert summary["content"]["internal_files"] == 2524
    assert summary["content"]["uncompressed_bytes"] == 171_572_528
    assert summary["content"]["unique_chemical_designs"] == 72
    assert summary["content"]["observation_rows"] == 202
    assert summary["content"]["numeric_point_counts"] == {
        "double_chain_force_extension_curve": 3248,
        "double_chain_hydrogen_bonds_vs_force": 3019,
        "high_rate_bulk_stress_strain_curve": 40892,
        "interchain_hydrogen_bond_count_distribution": 88000,
        "single_chain_scission_force": 71,
    }


def test_all_202_observations_are_traceable_gold_c_reference_rows() -> None:
    _require_archive()
    rows = _read_tsv("计算观测清单.tsv")
    systems = _read_tsv("计算体系清单.tsv")
    assert len(rows) == 202
    assert len(systems) == 72
    assert len({row["observation_id"] for row in rows}) == 202
    assert {row["system_id"] for row in rows} <= {row["system_id"] for row in systems}
    assert Counter(row["property_name"] for row in rows) == {
        "single_chain_scission_force": 71,
        "double_chain_force_extension_curve": 59,
        "double_chain_hydrogen_bonds_vs_force": 55,
        "interchain_hydrogen_bond_count_distribution": 11,
        "high_rate_bulk_stress_strain_curve": 6,
    }
    assert sum(int(row["point_count"]) for row in rows) == 135_230
    assert Counter(row["target_origin"] for row in rows) == {
        "dft": 71,
        "computational": 114,
        "md": 17,
    }
    assert all(
        row["decision"] == "admitted_gold_c_science_rights_pending"
        for row in rows
    )
    assert all(row["source_location"] for row in rows)


def test_curves_and_frames_do_not_multiply_independent_designs() -> None:
    _require_archive()
    rows = _read_tsv("计算观测清单.tsv")
    hbond = [
        row
        for row in rows
        if row["property_name"] == "interchain_hydrogen_bond_count_distribution"
    ]
    reaxff = [
        row
        for row in rows
        if row["property_name"] == "high_rate_bulk_stress_strain_curve"
    ]
    assert len(hbond) == 11
    assert sum(int(row["point_count"]) for row in hbond) == 88_000
    assert len(reaxff) == 6
    assert len({row["system_id"] for row in reaxff}) == 2
    assert sum(int(row["point_count"]) for row in reaxff) == 40_892
    assert all(row["independence_note"] for row in rows)
    assert all(
        any(
            token in row["independence_note"]
            for token in ("correlated", "not independent", "same double-chain")
        )
        for row in rows
    )


def test_audit_script_is_byte_reproducible_and_uses_atomic_replace() -> None:
    _require_archive()
    tracked_outputs = [
        SOURCE / name
        for name in (
            "来源元数据.json",
            "内容审计摘要.json",
            "下载清单.tsv",
            "文件内容计数.tsv",
            "计算体系清单.tsv",
            "计算观测清单.tsv",
            "计算输入参数清单.tsv",
            "候选来源核验.tsv",
        )
    ]
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked_outputs}
    subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, check=True)
    after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked_outputs}
    assert before == after
    source = SCRIPT.read_text(encoding="utf-8")
    assert "os.replace" in source
    assert ".write_text(" not in source


def test_atomic_write_preserves_old_file_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    target = tmp_path / "output.json"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic replace failure"):
        module.atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
