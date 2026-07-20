"""第七批孢子填充 TPU Source Data 的定向回归测试。"""

from __future__ import annotations

import csv
import importlib.util
import io
from collections import Counter
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "审计/第七批孢子填充TPU.py"
SPEC = importlib.util.spec_from_file_location("batch7_spore_filled_tpu_audit", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def _require_raw_source() -> None:
    if not audit.SOURCE_XLSX.is_file():
        pytest.skip(f"原始 Source Data 未在当前检出中分发：{audit.SOURCE_XLSX}")


@pytest.fixture(scope="module")
def result() -> dict[str, object]:
    _require_raw_source()
    return audit.run_audit(write_outputs=False)


def test_frozen_source_and_scientific_contract() -> None:
    assert audit.SOURCE_XLSX_BYTES == 6_539_183
    assert audit.SOURCE_XLSX_SHA256 == (
        "39ed4045fd71f89547d6c54977a69838b0895c815cb7b7b68bc0dee039a012a3"
    )
    assert audit.DOI == "10.1038/s41467-024-47132-8"
    assert audit.LICENSE == "CC BY 4.0"
    assert audit.EXPECTED_CURVES == 36
    assert len(audit.EXPECTED_CURVE_POINT_COUNTS) == 36
    assert sum(audit.EXPECTED_CURVE_POINT_COUNTS) == 280_288
    assert min(audit.EXPECTED_CURVE_POINT_COUNTS) == 6_985
    assert max(audit.EXPECTED_CURVE_POINT_COUNTS) == 8_600
    assert audit.EXPECTED_FORMULATION_CONDITIONS == 12
    assert audit.EXPECTED_REPLICATES_PER_CONDITION == 3
    assert audit.EXPECTED_SCALARS == 144
    assert audit.OUTPUT_NAMES == (
        "内容审计摘要.json",
        "曲线审计清单.tsv",
        "标量审计清单.tsv",
    )


def test_recomputed_curve_and_scalar_counts(result: dict[str, object]) -> None:
    summary = result["summary"]
    curves = result["curves"]
    scalars = result["scalars"]
    counts = summary["counts"]

    assert counts == {
        "curve_sheets": 1,
        "curves": 36,
        "curve_points": 280_288,
        "partial_curve_pair_rows": 0,
        "formulation_conditions": 12,
        "replicates_per_condition": 3,
        "scalar_measurements": 144,
        "scalar_metrics": 4,
    }
    assert [row["point_count"] for row in curves] == list(
        audit.EXPECTED_CURVE_POINT_COUNTS
    )
    assert all(row["partial_pair_rows"] == 0 for row in curves)
    assert Counter(row["formulation_id"] for row in curves) == Counter(
        {
            f"{spore_type}_{loading:g}wtpct": 3
            for spore_type in ("WT", "HST")
            for loading in audit.LOADINGS
        }
    )
    assert Counter(row["metric"] for row in scalars) == {
        "toughness": 36,
        "tensile_stress": 36,
        "elongation_at_break": 36,
        "young_modulus": 36,
    }
    assert len(
        {
            (row["formulation_id"], row["metric"], row["replicate_source_order"])
            for row in scalars
        }
    ) == 144


def test_gold_scope_and_cross_table_replicate_warning(result: dict[str, object]) -> None:
    summary = result["summary"]
    coverage = summary["field_coverage"]
    classification = summary["scientific_classification"]

    assert classification["gold_layer"] == "Gold-实验/条件化力学曲线层"
    assert classification["evidence_type"] == "experiment"
    assert coverage["repeat_unit_smiles"] == "missing"
    assert coverage["nco_oh_ratio"] == "missing"
    assert coverage["hard_segment_fraction"] == "missing"
    assert coverage["stress_strain_curve"] == "complete_with_replicates"

    # 源工作簿的两个表并未给出跨表 specimen ID。WT 0% 的首条曲线积分值
    # 对应 Figure 4A 的第二个数，而不是第一行数值；冻结此事实，阻止未来仅按
    # replicate_source_order 做未经证明的跨表 join。
    first_curve = result["curves"][0]
    first_scalar = next(
        row
        for row in result["scalars"]
        if row["source_figure"] == "Figure 4A"
        and row["spore_type"] == "WT"
        and row["spore_wt_pct"] == 0.0
        and row["replicate_source_order"] == 1
    )
    assert first_curve["trapezoid_toughness_MJ_m3"] == pytest.approx(
        114.476448255673, abs=1e-9
    )
    assert first_scalar["value"] == 126.4
    assert "不凭顺序强制配对" in classification["cross_table_replicate_warning"]


def test_rendered_outputs_are_lightweight_and_match_checked_in_audit(
    result: dict[str, object],
) -> None:
    outputs = result["outputs"]
    assert set(outputs) == set(audit.OUTPUT_NAMES)
    assert len(outputs["内容审计摘要.json"]) < 10_000
    assert len(outputs["曲线审计清单.tsv"]) < 30_000
    assert len(outputs["标量审计清单.tsv"]) < 40_000

    curves = list(
        csv.DictReader(
            io.StringIO(outputs["曲线审计清单.tsv"].decode("utf-8")),
            delimiter="\t",
        )
    )
    scalars = list(
        csv.DictReader(
            io.StringIO(outputs["标量审计清单.tsv"].decode("utf-8")),
            delimiter="\t",
        )
    )
    assert len(curves) == 36
    assert sum(int(row["point_count"]) for row in curves) == 280_288
    assert len(scalars) == 144

    # 若审计文件已经生成，必须与离线复算字节一致。
    for name, payload in outputs.items():
        path = audit.SOURCE_DIR / name
        if path.is_file():
            assert path.read_bytes() == payload


def test_atomic_write_preserves_previous_file_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "内容审计摘要.json"
    target.write_bytes(b"old")
    monkeypatch.setattr(audit, "SOURCE_DIR", tmp_path)

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(audit.os, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic replace failure"):
        audit.atomic_write(target, b"new")
    assert target.read_bytes() == b"old"
    assert list(tmp_path.glob("*.audit.tmp")) == []


def test_audit_is_offline_and_writes_only_by_atomic_replace() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "requests" not in source
    assert "urllib" not in source
    assert "http://" not in source
    assert "https://" not in source
    assert "os.replace" in source
