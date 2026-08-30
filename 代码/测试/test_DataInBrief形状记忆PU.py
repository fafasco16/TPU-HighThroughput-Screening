import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

import 接入DataInBrief形状记忆PU as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_release_counts_and_independent_units():
    tensile, cyclic, thermal = source.build_release()
    assert len(tensile) == 37
    assert tensile["test_run_id"].is_unique
    assert tensile["formulation_id"].nunique() == 12
    assert tensile["excluded_tail_contamination_point_count"].sum() == 1702
    assert len(cyclic) == 240
    assert cyclic["test_run_id"].nunique() == 24
    assert cyclic.groupby("test_run_id")["cycle_number"].nunique().eq(10).all()
    assert set(cyclic["cycle_number"]) == set(range(1, 11))
    assert len(thermal) == 12
    assert thermal["formulation_id"].nunique() == 12


def test_transfer_boundary_and_derived_targets():
    tensile, cyclic, thermal = source.build_release()
    for frame in (tensile, cyclic, thermal):
        assert frame["thermoplastic_tpu_core"].eq(False).all()
        assert frame["model_admission_layer"].eq("polyurethane_transfer").all()
        assert frame["split_group"].nunique() == 12
        fractions = frame[
            ["HDI_mol_percent", "HPED_mol_percent", "TEA_mol_percent"]
        ].sum(axis=1)
        assert fractions.between(99.9, 100.1).all()
    assert tensile["tensile_strength_MPa"].gt(0).all()
    assert tensile["elongation_at_break_percent"].gt(0).all()
    assert tensile["toughness_MJ_m3"].gt(0).all()
    cycle_one = cyclic.loc[cyclic["cycle_number"].eq(1)]
    assert cycle_one["peak_stress_retention_percent"].eq(100).all()
    assert cyclic["direct_shape_recovery_available"].eq(False).all()
    assert cyclic["shape_recovery_ratio_percent"].isna().all()
    assert cyclic["cyclic_target_role"].eq(
        "stress_retention_and_hysteresis_transfer_proxy"
    ).all()
    assert (thermal["T5_C"] < thermal["T10_C"]).all()
    assert (thermal["T10_C"] < thermal["T50_C"]).all()
    assert thermal["T5_C"].between(200, 450).all()
    assert thermal["Tg_DMA_C"].between(30, 130).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入DataInBrief形状记忆PU.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "DataInBrief形状记忆PU发布清单.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["counts"] == {
        "formulation_count": 12,
        "failure_test_run_count": 37,
        "cyclic_test_run_count": 24,
        "measurement_cycle_endpoint_count": 240,
        "tga_curve_count": 12,
        "raw_source_point_rows": 975903,
        "published_endpoint_rows": 289,
    }
    assert manifest["source"]["license"] == "CC-BY-4.0"
    assert manifest["policy"]["direct_shape_recovery_available"] is False
    published = pd.read_csv(OUTPUT / "DataInBrief形状记忆PU循环端点.csv")
    assert len(published) == 240
