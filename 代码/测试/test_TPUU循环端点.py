import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import 提取TPUU循环端点 as cyclic


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_segment_and_extract_two_synthetic_cycles():
    strain = np.array([0, 25, 50, 25, 0, 25, 50, 25, 0], dtype=float)
    stress = np.array([0, 500, 1000, 300, -10, 480, 900, 250, -20], dtype=float)
    cycles = cyclic.extract_cycle_endpoints(strain, stress)
    assert len(cycles) == 2
    assert cycles[0]["cycle_number"] == 1
    assert cycles[1]["cycle_number"] == 2
    assert cycles[0]["maximum_strain_percent"] == 50
    assert cycles[0]["loading_energy_MJ_m3"] > 0
    assert cycles[0]["elastic_recovery_percent"] > 90


def test_real_tpuu_release_has_four_formulations_and_twenty_cycles_each():
    source = pd.read_csv(OUTPUT / "三目标实验标签.csv.gz", low_memory=False)
    endpoints = cyclic.build_endpoints(source)
    assert len(endpoints) == 80
    assert endpoints["formulation_id"].nunique() == 4
    assert endpoints.groupby("curve_id")["cycle_number"].max().eq(20).all()
    assert endpoints["elastic_recovery_percent"].notna().all()
    assert endpoints["hysteresis_energy_MJ_m3"].notna().all()
    assert set(endpoints["endpoint_use"]) == {"family_calibration_only"}


def test_release_and_check_command():
    script = ROOT / "代码" / "提取TPUU循环端点.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "TPUU循环端点发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"] == {
        "curve_count": 4,
        "formulation_count": 4,
        "cycle_endpoint_rows": 80,
        "valid_energy_rows": 80,
    }
