import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

import 接入Zenodo多孔TPU as porous


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_release_has_five_material_codes_and_five_replicates():
    frame = porous.build_release()
    assert len(frame) == 25
    assert frame["observation_id"].is_unique
    assert frame["formulation_id"].nunique() == 5
    assert frame.groupby("formulation_id")["replicate_index"].nunique().eq(5).all()
    assert frame["toughness_MJ_m3"].gt(0).all()
    assert frame["chemistry_mapping_status"].eq(
        "commercial_TPU_identity_unresolved"
    ).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入Zenodo多孔TPU.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "Zenodo多孔TPU发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"] == {
        "tensile_curve_rows": 25,
        "formulation_count": 5,
        "replicates_per_formulation": 5,
    }
    assert len(pd.read_csv(OUTPUT / "Zenodo多孔TPU拉伸端点.csv")) == 25
