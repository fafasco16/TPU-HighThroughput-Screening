import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

import 生成定向筛选数据集 as directed


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_property_classification():
    assert directed.classify_property("toughness") == (
        "toughness",
        "primary_direct_scalar",
    )
    assert directed.classify_property("tga_mass_signal") == (
        "thermal_stability",
        "primary_curve_for_endpoint",
    )
    assert directed.classify_property("elastic_recovery") == (
        "cyclic_recovery",
        "primary_conditioned_scalar",
    )
    assert directed.classify_property("cyclic_tensile_stress") == (
        "cyclic_recovery",
        "primary_cyclic_curve",
    )
    assert directed.classify_property("shore_hardness") is None


def test_computational_property_classification():
    assert directed.classify_computational_property("Tg") == [
        ("thermal_stability", "low_fidelity_target")
    ]
    assert directed.classify_computational_property(
        "cohesive_energy_per_chain"
    ) == [
        ("toughness", "mechanistic_proxy"),
        ("cyclic_recovery", "mechanistic_proxy"),
        ("thermal_stability", "mechanistic_proxy"),
    ]
    assert directed.classify_computational_property("thermal_conductivity") == []


def test_release_tables_preserve_evidence_and_missingness():
    release = directed.build_release()
    labels = release["labels"]
    audit = release["audit"]
    computational = release["computational_evidence"]
    computational_audit = release["computational_audit"]
    components = release["components"]
    formulations = release["formulations"]

    assert labels["target_family"].isin(
        ["toughness", "cyclic_recovery", "thermal_stability"]
    ).all()
    assert set(labels["chemistry_mapping_status"]) <= {
        "component_table_closed",
        "formulation_id_only",
        "unmapped",
    }
    assert set(audit["property_name"]) == set(labels["property_name"])
    assert "thermal_conductivity" not in set(computational["property_name"])
    assert set(computational["evidence_role"]) <= {
        "direct_low_fidelity_target",
        "transfer_low_fidelity_target",
        "process_response_proxy",
        "mechanistic_proxy",
    }
    assert not computational["allowed_use"].eq("experimental_truth").any()
    assert set(computational_audit["property_name"]) == set(
        computational["property_name"]
    )
    cyclic_direct = computational[
        computational["target_family"].eq("cyclic_recovery")
        & computational["evidence_role"].eq("direct_low_fidelity_target")
    ]
    assert cyclic_direct.empty
    assert len(components) == 24
    assert len(formulations) == 980
    assert components["price_per_kg"].isna().all()
    assert components["ghs_hazard_score"].isna().all()
    assert set(components["cost_data_status"]) == {"missing_quote"}
    assert set(formulations["polymer_family"]) == {"TPU"}
    assert not formulations["tpuu_route_ready"].any()
    assert set(formulations["expensive_calculation_status"]) == {
        "deferred_until_multitarget_prefilter"
    }


def test_generated_release_and_check_command():
    subprocess.run(
        [sys.executable, str(ROOT / "代码" / "生成定向筛选数据集.py")],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "代码" / "生成定向筛选数据集.py"),
            "--检查",
        ],
        cwd=ROOT,
        check=True,
    )

    manifest = json.loads(
        (OUTPUT / "发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["release_id"] == "tpu-directed-five-objective-2026-08-30-v1"
    assert manifest["counts"]["commercial_component_rows"] == 24
    assert manifest["counts"]["realistic_formulation_rows"] == 980
    assert manifest["counts"]["target_family_count"] == 3
    assert manifest["counts"]["computational_evidence_rows"] > 0
    assert manifest["counts"]["computational_evidence_property_count"] >= 8

    tasks = pd.read_csv(OUTPUT / "筛选任务清单.csv")
    assert tasks["objective_id"].tolist() == [
        "toughness",
        "cyclic_recovery",
        "thermal_stability",
        "cost",
        "environment",
    ]
    assert tasks.loc[
        tasks["objective_id"].isin(["cost", "environment"]), "objective_type"
    ].eq("deterministic_constraint").all()
