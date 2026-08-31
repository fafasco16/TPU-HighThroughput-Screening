import json
import subprocess
import sys
from pathlib import Path

import 接入MDPI_PU分子动力学描述符 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_candidate_descriptor_records():
    frame = source.build_release()
    assert len(frame) == 79
    assert set(frame["system_id"]) == {"PB1", "PB2", "PB3", "polyol_reference"}
    assert set(frame["temperature_K"]) == {243, 258, 293, 333}
    assert frame["property_name"].value_counts().to_dict() == {
        "solubility_parameter": 16,
        "diffusion_coefficient": 15,
        "lame_lambda": 12,
        "lame_mu": 12,
        "young_modulus": 12,
        "bulk_modulus": 12,
    }
    assert frame["record_id"].is_unique
    assert frame["model_ready"].eq(False).all()  # noqa: E712
    assert frame["training_weight"].eq(0).all()
    assert frame["direct_toughness_label"].eq(False).all()  # noqa: E712
    assert frame["model_admission_layer"].eq(
        "md_computed_descriptor_reference"
    ).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入MDPI_PU分子动力学描述符.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "MDPI_PU分子动力学发布清单.json").read_text(
            encoding="utf-8"
        )
    )
    counts = manifest["counts"]
    assert counts["source_observation_count"] == 120
    assert counts["published_candidate_record_count"] == 79
    assert counts["intermediate_energy_record_count_excluded"] == 28
    assert counts["definition_duplicate_record_count_excluded"] == 12
    assert counts["low_fit_quality_record_count_excluded"] == 1
    assert counts["published_compact_row_count"] == 79
    assert manifest["policy"]["training_weight_before_mapping"] == 0.0
    assert manifest["policy"]["mu_and_G_double_supervision"] is False
