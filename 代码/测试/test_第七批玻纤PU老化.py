"""第七批玻纤 PU 老化归档的定向回归测试。"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "审计/第七批玻纤PU老化.py"
SPEC = importlib.util.spec_from_file_location("batch7_gfrpu_aging_audit", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def _require_raw_archive() -> None:
    if not audit.ARCHIVE.is_file():
        pytest.skip(f"原始官方包缺失：{audit.ARCHIVE}")
    if not (shutil.which("7z") or shutil.which("7zz")):
        pytest.skip("未安装 7z，无法读取内嵌 RAR")


def test_frozen_archive_identity_and_scientific_contract() -> None:
    assert audit.ARCHIVE_SIZE == 3_694_147
    assert audit.ARCHIVE_SHA256 == "84dcc881697d9ead4baa25c659f4d64587f7fcedc1ef056bf333c3e7b5b6325b"
    assert audit.NESTED_RAR_SIZE == 3_692_762
    assert audit.NESTED_RAR_SHA256 == "5fa18cc739d0db166ece9a06bca186d2f033bde72a2f2bbaa487b6a5be02d39a"
    assert len(audit.EXPECTED_PAYLOAD) == 23
    assert audit.EXPECTED_3PB_UNIQUE_CURVES == 29
    assert audit.EXPECTED_3PB_DUPLICATE_OCCURRENCES == 9
    assert audit.EXPECTED_3PB_UNIQUE_POINTS == 96_255
    assert audit.EXPECTED_3PB_PLACEHOLDERS == 2_668
    assert audit.EXPECTED_DMA_RUNS == 17
    assert audit.EXPECTED_DMA_POINTS == 39_097
    assert audit.EXPECTED_DMA_SENTINELS == 1
    assert audit.EXPECTED_DMA_PARTIAL_FILE == "W70_10d.txt"


def test_atomic_write_preserves_old_file_when_replace_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "内容审计摘要.json"
    target.write_bytes(b"old")
    monkeypatch.setattr(audit, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(audit, "OUTPUT_WHITELIST", frozenset({target}))

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(audit.os, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic replace failure"):
        audit.atomic_write(target, b"new")
    assert target.read_bytes() == b"old"
    assert list(tmp_path.glob("*.audit.tmp")) == []


def test_recomputed_counts_partial_channel_and_deterministic_outputs() -> None:
    _require_raw_archive()
    first = audit.run_audit(write_outputs=False)
    second = audit.run_audit(write_outputs=False)
    payload = first["summary"]["payload"]

    assert payload["official_zip_bytes"] == 3_694_147
    assert payload["official_zip_sha256"] == audit.ARCHIVE_SHA256
    assert payload["three_point_bending_curve_occurrences"] == 38
    assert payload["three_point_bending_unique_curves"] == 29
    assert payload["three_point_bending_duplicate_occurrences"] == 9
    assert payload["three_point_bending_unique_points"] == 96_255
    assert payload["three_point_bending_displayed_points"] == 126_459
    assert payload["three_point_bending_placeholder_rows_excluded"] == 2_668
    assert payload["dma_runs"] == 17
    assert payload["dma_points"] == 39_097
    assert payload["dma_complete_five_channel_points"] == 36_800
    assert payload["dma_partial_four_channel_points"] == 2_297
    assert payload["dma_sentinel_rows_excluded"] == 1

    partial = [row for row in first["dma"] if row["column_count"] == 4]
    assert len(partial) == 1
    assert partial[0]["source_file"] == "W70_10d.txt"
    assert partial[0]["field_mapping"] == "time_min|temperature_C|storage_modulus_MPa|tan_delta"
    complete = [row for row in first["dma"] if row["column_count"] == 5]
    assert len(complete) == 16
    assert max(float(row["tan_delta_identity_max_abs_error"]) for row in complete) < 2.5e-7

    assert set(first["outputs"]) == set(audit.OUTPUT_NAMES)
    assert first["outputs"] == second["outputs"]


def test_no_network_dependency_and_atomic_replace_are_explicit() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "requests" not in source
    assert "urllib" not in source
    assert "os.replace" in source
    assert "shell=False" in source
