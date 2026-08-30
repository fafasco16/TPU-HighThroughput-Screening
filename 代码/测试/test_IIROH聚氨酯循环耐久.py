import json
import subprocess
import sys
from pathlib import Path

import 接入IIROH聚氨酯循环耐久 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_cyclic_endpoint_count_and_boundaries():
    cyclic, aging = source.build_release()
    assert len(cyclic) == 600
    assert cyclic["cyclic_run_id"].nunique() == 6
    assert set(cyclic["formulation_id"]) == {"HDI-4", "HMDI-4"}
    assert cyclic.groupby("cyclic_run_id")["cycle_number"].nunique().eq(100).all()
    assert set(cyclic["cycle_number"]) == set(range(1, 101))
    assert cyclic.loc[cyclic["cycle_number"].eq(1), "peak_stress_retention_percent"].eq(100).all()
    cycle_100 = cyclic.loc[cyclic["cycle_number"].eq(100)]
    assert cycle_100["peak_stress_retention_percent"].between(40, 80).all()
    assert cyclic["hysteresis_energy_MJ_m3"].ge(0).all()
    assert cyclic["direct_shape_recovery_available"].eq(False).all()
    assert cyclic["shape_recovery_ratio_percent"].isna().all()
    assert len(aging) == 6
    assert set(aging["formulation_id"]) == {"HDI-4", "HMDI-4"}
    assert aging["replicate_id"].nunique() == 3


def test_hydrolytic_retention_boundaries():
    _, aging = source.build_release()
    assert aging["peak_stress_retention_percent"].gt(0).all()
    assert aging["maximum_strain_retention_percent"].gt(0).all()
    assert aging["curve_area_retention_percent"].gt(0).all()
    assert aging["aging_condition_status"].eq(
        "hydrolytic_aging_source_protocol_unresolved"
    ).all()
    assert aging["pairing_status"].eq(
        "matched_formulation_and_replicate_not_proven_same_specimen"
    ).all()
    assert aging["model_admission_layer"].eq(
        "polyurethane_adjacent_experimental"
    ).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入IIROH聚氨酯循环耐久.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "IIR-OH聚氨酯循环耐久发布清单.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["counts"] == {
        "formulation_count": 2,
        "cyclic_run_count": 6,
        "cycle_endpoint_count": 600,
        "cyclic_source_point_count": 2062350,
        "hydrolytic_aging_pair_count": 6,
        "hydrolytic_source_point_count": 20523,
        "published_compact_row_count": 606,
    }
    assert manifest["source"]["license"] == "CC-BY-4.0"
    assert manifest["policy"]["raw_curves_republished"] is False
    assert manifest["policy"]["cyclic_filename_C0_50_means_strain_range"] is True
