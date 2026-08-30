import json
import subprocess
import sys
from pathlib import Path

import 接入ElastollanPCL形状记忆 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_direct_shape_memory_table():
    frame = source.build_release()
    assert len(frame) == 3
    assert frame["material_id"].is_unique
    assert frame["TPU_wt_percent"].tolist() == [30.0, 45.0, 60.0]
    assert frame["PCL_wt_percent"].tolist() == [70.0, 55.0, 40.0]
    assert frame["shape_fixity_ratio_percent"].tolist() == [97.1, 90.8, 84.5]
    assert frame["shape_recovery_ratio_percent"].tolist() == [76.8, 81.2, 85.7]
    assert (
        frame["TPU_wt_percent"] + frame["PCL_wt_percent"]
    ).eq(100).all()
    assert frame["TPU_grade"].eq("Elastollan 1154D").all()
    assert frame["PCL_grade"].eq("CAPA 6500").all()
    assert frame["direct_shape_recovery_available"].all()
    assert frame["model_admission_layer"].eq(
        "core_tpu_blend_published_summary"
    ).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入ElastollanPCL形状记忆.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "ElastollanPCL形状记忆发布清单.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["counts"] == {
        "blend_formulation_count": 3,
        "direct_shape_fixity_rows": 3,
        "direct_shape_recovery_rows": 3,
        "published_compact_row_count": 3,
    }
    assert manifest["source"]["evidence_sha256"] == source.evidence_sha256()
    assert len(manifest["source"]["evidence_sha256"]) == 64
    assert (
        manifest["source"]["license_status"]
        == "paper_license_not_verified_numeric_facts_only"
    )
