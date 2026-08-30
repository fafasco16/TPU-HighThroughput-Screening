import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

import 接入DRUM机械回收 as drum


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_parse_material_code():
    parsed = drum.parse_material_code("P4MCL-1.6k-31HS")
    assert parsed == {
        "polymer_family": "TPUU",
        "macrodiol_family": "P4MCL",
        "macrodiol_nominal_mn_g_mol": 1600.0,
        "macrodiol_repeat_unit_text": "[-O-CH2-CH2-CH(CH3)-CH2-CH2-C(=O)-]n",
        "macrodiol_structure_mapping_status": "repeat_topology_mapped",
        "hard_segment_mass_fraction": 0.31,
        "diisocyanate_family": "IPDI",
        "diisocyanate_smiles": "CC1(C)CC(CC(C)(CN=C=O)C1)N=C=O",
        "chain_extension_route": "water_to_urea",
        "chain_extension_reagent": "water",
    }


def test_real_release_has_expected_independent_tensile_curves():
    frame = drum.build_release()
    assert len(frame) == 107
    assert frame["observation_id"].is_unique
    assert frame["formulation_id"].nunique() == 21
    core = frame[frame["model_admission_layer"].eq("核心实验层")]
    assert len(core) == 107
    assert core["formulation_id"].nunique() == 21
    assert frame["tensile_strength_MPa"].notna().all()
    assert frame["elongation_at_break_percent"].notna().all()
    assert frame["toughness_MJ_m3"].notna().all()
    assert frame["curve_point_count"].gt(3).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入DRUM机械回收.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "DRUM机械回收发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"] == {
        "tensile_curve_rows": 107,
        "formulation_count": 21,
        "core_tpuu_curve_rows": 107,
        "core_tpuu_formulation_count": 21,
    }
    published = pd.read_csv(OUTPUT / "DRUM机械回收拉伸端点.csv")
    assert len(published) == 107
