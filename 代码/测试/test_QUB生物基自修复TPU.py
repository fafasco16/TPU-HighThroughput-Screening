import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

import 接入QUB生物基自修复TPU as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_qub_release_counts_and_targets():
    tensile, cyclic, thermal, curves = source.build_release()
    assert len(tensile) == 41
    assert tensile["curve_id"].nunique() == 41
    assert set(tensile["formulation_id"]) == {"P35", "P40", "P45", "P40-HDO"}
    assert cyclic["cycle_number"].tolist() == [1, 2, 3, 4, 5, 6]
    assert set(thermal["formulation_id"]) == {"P35", "P40", "P45"}
    assert set(curves["modality"]) == {"bulk_tensile", "cyclic_tensile", "TGA"}


def test_qub_endpoints_are_physical_and_mapped():
    tensile, cyclic, thermal, _ = source.build_release()
    assert tensile["tensile_strength_MPa"].between(0.05, 2.5).all()
    assert tensile["elongation_at_break_percent"].between(100, 4000).all()
    assert tensile["toughness_MJ_m3"].gt(0).all()
    assert tensile["split_group"].nunique() == 4
    assert tensile["chemistry_mapping_status"].eq(
        "monomer_set_hard_segment_mapped"
    ).all()
    assert cyclic["maximum_strain_percent"].between(95, 105).all()
    assert cyclic["hysteresis_energy_MJ_m3"].ge(0).all()
    assert cyclic["strain_recovery_percent"].isna().all()
    assert cyclic["recovery_metric_status"].eq(
        "not_identifiable_from_imposed_strain_cycle"
    ).all()
    assert cyclic["cyclic_target_role"].eq(
        "hysteresis_and_energy_dissipation_proxy"
    ).all()
    assert thermal["T5_C"].between(200, 400).all()
    assert (thermal["T5_C"] < thermal["T10_C"]).all()
    assert (thermal["T10_C"] < thermal["T50_C"]).all()
    assert thermal["T50_C"].between(330, 420).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入QUB生物基自修复TPU.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "QUB生物基自修复TPU发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"]["tensile_curve_count"] == 41
    assert manifest["counts"]["cyclic_sequence_count"] == 1
    assert manifest["counts"]["dependent_cycle_count"] == 6
    assert manifest["counts"]["tga_curve_count"] == 3
    assert manifest["source"]["license"] == "CC-BY-4.0"
    published = pd.read_csv(OUTPUT / "QUB生物基自修复TPU拉伸端点.csv")
    assert len(published) == 41
