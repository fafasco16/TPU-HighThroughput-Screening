import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

import 生成三目标配方特征 as feature_builder


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_reality_features_stop_before_calculation():
    formulations = pd.read_csv(OUTPUT / "现实配方候选.csv", low_memory=False)
    components = pd.read_csv(OUTPUT / "现实构件约束.csv", low_memory=False)
    features = feature_builder.build_formulation_features(formulations, components)
    assert len(features) == 980
    assert features["formulation_id"].is_unique
    assert features["component_identity_complete"].all()
    assert features["stoichiometry_context_complete"].all()
    assert features["precalculation_rule_ready"].all()
    assert not features["cost_inputs_complete"].any()
    assert not features["environment_inputs_complete"].any()
    assert not features["calculation_allowed"].any()
    assert not features["model_prediction_available"].any()
    assert set(features["polymer_family"]) == {"TPU"}


def test_training_tasks_keep_fidelity_roles_separate():
    directed_tasks = pd.read_csv(OUTPUT / "筛选任务清单.csv")
    endpoints = pd.read_csv(OUTPUT / "TGA热稳定端点.csv")
    cyclic_endpoints = pd.read_csv(OUTPUT / "TPUU循环端点.csv")
    drum_cyclic = pd.read_csv(OUTPUT / "DRUM机械回收循环端点.csv")
    drum_recycling = pd.read_csv(OUTPUT / "DRUM机械回收拉伸端点.csv")
    zenodo_porous_tpu = pd.read_csv(OUTPUT / "Zenodo多孔TPU拉伸端点.csv")
    figshare_healing_tpu = pd.read_csv(OUTPUT / "Figshare强韧自愈端点.csv")
    tasks = feature_builder.build_training_tasks(
        directed_tasks,
        endpoints,
        cyclic_endpoints,
        drum_cyclic,
        drum_recycling,
        zenodo_porous_tpu,
        figshare_healing_tpu,
    )
    assert tasks["objective_id"].tolist() == [
        "toughness",
        "cyclic_recovery",
        "thermal_stability",
        "cost",
        "environment",
    ]
    assert tasks["model_training_status"].eq("not_started_by_user_instruction").all()
    thermal = tasks[tasks["objective_id"].eq("thermal_stability")].iloc[0]
    assert thermal["tga_endpoint_curve_count"] == 5
    assert thermal["tga_identity_resolved_curve_count"] == 4
    cyclic = tasks[tasks["objective_id"].eq("cyclic_recovery")].iloc[0]
    assert cyclic["direct_low_fidelity_hard_groups"] == 0
    assert cyclic["cyclic_endpoint_rows"] == 320
    assert cyclic["cyclic_endpoint_formulation_count"] == 26
    toughness = tasks[tasks["objective_id"].eq("toughness")].iloc[0]
    assert toughness["local_expansion_endpoint_rows"] == 137
    assert toughness["local_expansion_formulation_count"] == 29


def test_release_and_check_command():
    script = ROOT / "代码" / "生成三目标配方特征.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "训练前发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"] == {
        "formulation_feature_rows": 980,
        "objective_rows": 5,
        "precalculation_rule_ready_rows": 980,
        "calculation_allowed_rows": 0,
        "model_prediction_available_rows": 0,
    }
