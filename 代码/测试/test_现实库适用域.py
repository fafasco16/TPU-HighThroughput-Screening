from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "代码"))

import 现实库适用域 as domain


def test_component_domain_finds_identical_reference():
    reference = pd.DataFrame(
        [
            {"canonical_structure": "OCCCCO", "leakage_group": "g1", "development_split": "train"},
            {"canonical_structure": "c1ccccc1", "leakage_group": "g2", "development_split": "test"},
        ]
    )
    components = pd.DataFrame(
        [
            {
                "component_id": "bdo",
                "preferred_name": "BDO",
                "role": "chain_extender",
                "representation_smiles": "OCCCCO",
                "representation_scope": "discrete_substance",
            }
        ]
    )
    result = domain.evaluate_components(components, reference)
    assert result.loc[0, "max_morgan_tanimoto"] == pytest.approx(1.0)
    assert result.loc[0, "applicability_domain_status"] == "within_training_structure_domain"


def test_formulation_floor_uses_weakest_component():
    components = pd.DataFrame(
        [
            {"component_id": "d", "max_morgan_tanimoto": 0.7, "applicability_domain_status": "within"},
            {"component_id": "m", "max_morgan_tanimoto": 0.2, "applicability_domain_status": "outside"},
            {"component_id": "e", "max_morgan_tanimoto": 0.8, "applicability_domain_status": "within"},
        ]
    )
    formulations = pd.DataFrame(
        [{"formulation_id": "f", "diisocyanate_id": "d", "macrodiol_id": "m", "chain_extender_id": "e"}]
    )
    result = domain.evaluate_formulations(formulations, components)
    assert result.loc[0, "formulation_domain_floor"] == pytest.approx(0.2)
    assert result.loc[0, "weakest_domain_role"] == "macrodiol"
    assert result.loc[0, "ml_prediction_status"].startswith("blocked")


def test_real_outputs_are_complete():
    manifest = domain.write_outputs(
        ROOT / "结果" / "可用数据集" / "计算观测.csv.gz",
        ROOT / "数据" / "现实库" / "构件.csv",
        ROOT / "数据" / "现实库" / "PTMG代表模型.csv",
        ROOT / "数据" / "现实库" / "配方.csv",
        ROOT / "数据" / "现实库" / "构件适用域.csv",
        ROOT / "数据" / "现实库" / "配方适用域.csv",
        ROOT / "数据" / "现实库" / "适用域运行清单.json",
    )
    assert manifest["counts"]["reality_components"] == 19
    assert manifest["counts"]["reality_formulations"] == 980
    assert sum(manifest["component_status_counts"].values()) == 19


def test_fail_closed_error_paths():
    with pytest.raises(ValueError, match="无法解析"):
        domain.fingerprint("not smiles")
    with pytest.raises(ValueError, match="缺少字段"):
        domain.build_reference_structures(pd.DataFrame({"x": [1]}))
    with pytest.raises(ValueError, match="为空"):
        domain.build_reference_structures(
            pd.DataFrame(columns=["canonical_structure", "leakage_group", "development_split"])
        )
    with pytest.raises(ValueError, match="阈值"):
        domain.evaluate_components(
            pd.DataFrame(columns=["component_id", "representation_smiles"]),
            pd.DataFrame([{"canonical_structure": "CC", "leakage_group": "g", "development_split": "train"}]),
            boundary_threshold=0.8,
            in_domain_threshold=0.6,
        )
    with pytest.raises(ValueError, match="未评估构件"):
        domain.evaluate_formulations(
            pd.DataFrame([{"diisocyanate_id": "x", "macrodiol_id": "x", "chain_extender_id": "x"}]),
            pd.DataFrame([{"component_id": "y", "max_morgan_tanimoto": 1.0, "applicability_domain_status": "within"}]),
        )


def test_component_structure_requires_macro_model_and_unique_id():
    components = pd.DataFrame(
        [
            {"component_id": "m", "preferred_name": "M", "role": "macrodiol", "identity_kind": "commercial_polyol_grade", "canonical_smiles": ""},
        ]
    )
    with pytest.raises(ValueError, match="缺少适用域结构"):
        domain.component_structure_table(
            components,
            pd.DataFrame(columns=["component_id", "representative_smiles"]),
        )
    duplicated = pd.concat([components.assign(canonical_smiles="OCCCCO", role="chain_extender")] * 2)
    with pytest.raises(ValueError, match="不唯一"):
        domain.component_structure_table(
            duplicated,
            pd.DataFrame(columns=["component_id", "representative_smiles"]),
        )


def test_main_uses_cli_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys):
    monkeypatch.setattr(
        domain,
        "write_outputs",
        lambda *args: {"counts": {"reality_components": 1}, "status": "completed"},
    )
    assert domain.main(
        [
            "--计算观测", str(tmp_path / "a.csv"),
            "--构件", str(tmp_path / "b.csv"),
            "--宏二醇模型", str(tmp_path / "c.csv"),
            "--配方", str(tmp_path / "d.csv"),
            "--构件输出", str(tmp_path / "e.csv"),
            "--配方输出", str(tmp_path / "f.csv"),
            "--清单输出", str(tmp_path / "g.json"),
        ]
    ) == 0
    assert "reality_components" in capsys.readouterr().out
