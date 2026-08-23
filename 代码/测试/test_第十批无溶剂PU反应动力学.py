"""第十批 Zenodo 无溶剂 PU 反应动力学的定向回归测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "审计/第十批无溶剂PU反应动力学.py"
SPEC = importlib.util.spec_from_file_location("batch10_solvent_free_pu_kinetics", SCRIPT)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


@pytest.fixture(scope="module")
def result() -> dict[str, object]:
    if not audit.SOURCE_ZIP.is_file():
        pytest.skip("第十批 Zenodo 官方 ZIP 不在当前检出中")
    return audit.run_audit(write_outputs=False)


def test_source_identity_license_hashes_and_read_only_outputs(result: dict[str, object]) -> None:
    summary = result["summary"]
    assert summary["source"]["dataset_doi"] == "10.5281/zenodo.6406174"
    assert summary["source"]["paper_doi"] == "10.1039/D2RA08326D"
    assert summary["source"]["license"] == "CC-BY-4.0"
    assert summary["source"]["access"] == "open"
    files = {row["文件"]: row for row in result["files"]}
    assert files["Solvent_Free_Adhesives_Dataset_5-2.zip"]["字节数"] == 580_544
    assert files["Solvent_Free_Adhesives_Dataset_5-2.zip"]["MD5"] == (
        "6282bca96c6961619b4e82ff0e4da735"
    )
    assert files["ZIP!/Solvent_Free_Adhesives_Dataset_5-2.xls"]["SHA256"] == (
        "ad4b98e3ae69668ce2542038c100d002aa01929b0da45d2eaf62049f2469ed0f"
    )
    assert files["ZIP!/Metadata_Dataset_5-2_Solvent_Free_Adhesives.pdf"]["SHA256"] == (
        "d214ee10f250680070bd7d3b089067307de1ced77ef24b0cc49840300e08ed1d"
    )


def test_material_identity_cas_and_molecular_masses_are_preserved(
    result: dict[str, object],
) -> None:
    materials = {row["原料代码"]: row for row in result["materials"]}
    assert set(materials) == {"PEA", "PDEA", "PCL", "PEG", "HDI", "TDI"}
    assert (materials["PEA"]["CAS"], materials["PEA"]["Mn_g_mol"]) == (
        "24937-05-1",
        2050,
    )
    assert (materials["PDEA"]["CAS"], materials["PDEA"]["Mn_g_mol"]) == (
        "25036-49-1",
        2700,
    )
    assert (materials["PCL"]["CAS"], materials["PCL"]["Mn_g_mol"]) == (
        "36890-68-3",
        2000,
    )
    assert (materials["PEG"]["CAS"], materials["PEG"]["Mn_g_mol"]) == (
        "25322-68-3",
        1000,
    )
    assert (materials["HDI"]["CAS"], materials["HDI"]["分子量_g_mol"]) == (
        "822-06-0",
        168.19,
    )
    assert materials["TDI"]["CAS"] == "584-84-9;91-08-7"
    assert materials["TDI"]["分子量_g_mol"] == 174.16
    assert "4:1" in materials["TDI"]["组成说明"]
    assert {row["训练权重"] for row in materials.values()} == {""}


def test_21_unique_conditions_keep_both_theoretical_nco_sources(
    result: dict[str, object],
) -> None:
    conditions = result["conditions"]
    assert len(conditions) == len({row["条件ID"] for row in conditions}) == 21
    assert Counter(row["反应体系"] for row in conditions) == {
        "PEA+HDI": 6,
        "PDEA+HDI": 6,
        "PDEA+TDI": 3,
        "PCL+TDI": 3,
        "PEG+TDI": 3,
    }
    assert {row["准入状态"] for row in conditions} == {"admitted_reference"}
    assert {row["训练权重"] for row in conditions} == {""}
    pea_03_70 = next(
        row
        for row in conditions
        if row["反应体系"] == "PEA+HDI"
        and row["摩尔比"] == "1:0.3"
        and row["温度_C"] == 70
    )
    assert pea_03_70["工作簿理论初始NCO_pct"] == 1.2
    assert pea_03_70["论文批次理论初始NCO_pct"] == 1.256
    pdea_tdi = next(row for row in conditions if row["反应体系"] == "PDEA+TDI")
    assert pdea_tdi["工作簿理论初始NCO_pct"] == 3.599
    assert pdea_tdi["论文批次理论初始NCO_pct"] == ""
    assert pdea_tdi["理论值关系"] == "论文批次表未报告_不代填"


def test_34_measured_columns_preserve_replicate_relationships(
    result: dict[str, object],
) -> None:
    columns = result["columns"]
    assert len(columns) == len({row["测量列ID"] for row in columns}) == 34
    assert Counter(row["工作表"] for row in columns) == {
        "PEA+HDI": 12,
        "PDEA+HDI": 6,
        "PDEA+TDI": 6,
        "PCL+TDI": 5,
        "PEG+TDI": 5,
    }
    assert Counter(row["重复关系"] for row in columns) == {
        "同一条件重复滴定": 26,
        "该条件仅一个滴定列": 8,
    }
    peg = [row for row in columns if row["工作表"] == "PEG+TDI"]
    assert {row["工作簿列字母"] for row in peg} == {"C", "D", "E", "F", "G"}
    assert "H" not in {row["工作簿列字母"] for row in peg}
    assert {row["训练权重"] for row in columns} == {""}


def test_171_nco_points_are_retained_without_time_imputation(
    result: dict[str, object],
) -> None:
    measurements = result["measurements"]
    assert len(measurements) == len({row["测量点ID"] for row in measurements}) == 171
    assert Counter(row["工作表"] for row in measurements) == {
        "PEA+HDI": 61,
        "PDEA+HDI": 32,
        "PDEA+TDI": 38,
        "PCL+TDI": 21,
        "PEG+TDI": 19,
    }
    assert sum(row["实测NCO_pct"] == 0 for row in measurements) == 23
    missing_time = [row for row in measurements if row["时间_h_原始"] == ""]
    assert len(missing_time) == 2
    assert {row["工作表"] for row in missing_time} == {"PDEA+TDI"}
    assert {row["前一非空时间_h_仅上下文"] for row in missing_time} == {24.0}
    assert {row["时间状态"] for row in missing_time} == {"源表缺失_未插补"}
    assert {row["准入状态"] for row in missing_time} == {"conditional_reference"}
    assert Counter(row["准入状态"] for row in measurements) == {
        "admitted_reference": 169,
        "conditional_reference": 2,
    }
    assert all(float(row["实测NCO_pct"]) >= 0 for row in measurements)
    assert {row["训练权重"] for row in measurements} == {""}


def test_paper_protocol_preserves_solvent_free_titration_and_model_boundaries(
    result: dict[str, object],
) -> None:
    protocol = result["protocol"]
    assert protocol["反应性质"]["溶剂"] == "无外加溶剂"
    assert "无外加催化剂" in protocol["反应性质"]["催化剂"]
    assert protocol["反应性质"]["气氛"] == "干燥氮气吹扫"
    assert protocol["取样与滴定"]["标准"] == "改编自ASTM D5155"
    assert protocol["取样与滴定"]["单次取样量_mL"] == "0.1-0.9"
    assert protocol["取样与滴定"]["NCO公式"] == "%NCO = 0.42 × (V_B - V_S) / m_S"
    assert "理论初始%NCO不是实测点" in protocol["建模边界"]["理论t0"]
    assert "排除零值" in protocol["建模边界"]["零值"]
    assert protocol["建模边界"]["缺失时间"].startswith("不插补")


def test_outputs_are_deterministic_complete_and_weight_free(result: dict[str, object]) -> None:
    assert tuple(result["outputs"]) == audit.OUTPUT_NAMES
    rerun = audit.run_audit(write_outputs=False)
    assert rerun["outputs"] == result["outputs"]
    assert result["summary"]["scientific_classification"]["training_weight_materialized"] is False
    for name, payload in result["outputs"].items():
        checked = audit.SOURCE_DIR / name
        if checked.is_file():
            assert checked.read_bytes() == payload
    summary = json.loads(result["outputs"]["内容审计摘要.json"].decode("utf-8"))
    assert summary["counts"]["unique_reaction_conditions"] == 21
    assert summary["counts"]["measured_columns"] == 34
    assert summary["counts"]["nonempty_nco_points"] == 171
