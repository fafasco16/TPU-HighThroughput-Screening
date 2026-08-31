import json
import subprocess
import sys
from pathlib import Path

import 接入MendeleyTPU实验仿真 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_experimental_replicates_are_partial_direct_labels():
    experiments, _ = source.build_release()
    assert len(experiments) == 3
    assert experiments["replicate_id"].is_unique
    assert experiments["curve_point_count"].sum() == 144009
    assert experiments["maximum_observed_strain"].between(0.99, 1.01).all()
    assert experiments["stress_at_100pct_strain_MPa"].between(5.0, 6.0).all()
    assert experiments["partial_tensile_energy_MJ_m3"].between(4.0, 5.0).all()
    assert experiments["complete_fracture_observed"].eq(False).all()
    assert experiments["complete_toughness_available"].eq(False).all()
    assert experiments["target_role"].eq(
        "direct_partial_tensile_energy_to_100pct"
    ).all()


def test_simulations_are_one_zero_weight_calibration_family():
    _, simulations = source.build_release()
    assert len(simulations) == 13
    assert simulations["simulation_condition_id"].is_unique
    assert simulations["source_curve_point_count"].sum() == 6453
    assert simulations["curve_point_count"].sum() == 5970
    assert simulations["simulation_family_id"].nunique() == 1
    assert simulations["actual_training_weight"].eq(0).all()
    assert simulations["model_ready"].eq(False).all()
    assert simulations["simulation_protocol_complete"].eq(False).all()
    assert simulations["experimental_overlap_fraction"].between(0, 1).all()
    assert simulations["experimental_RMSE_MPa"].dropna().ge(0).all()
    assert simulations["experimental_MAE_MPa"].dropna().ge(0).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入MendeleyTPU实验仿真.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "TPU实验仿真发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"]["experimental_specimen_count"] == 3
    assert manifest["counts"]["experimental_curve_point_count"] == 144009
    assert manifest["counts"]["simulation_family_count"] == 1
    assert manifest["counts"]["simulation_run_count"] == 13
    assert manifest["counts"]["simulation_source_curve_point_count"] == 6453
    assert manifest["counts"]["simulation_unique_strain_point_count"] == 5970
    assert manifest["counts"]["published_compact_row_count"] == 16
    assert manifest["policy"]["partial_100pct_energy_claimed_as_fracture_toughness"] is False
    assert manifest["policy"]["simulation_weight_positive_without_protocol"] is False
