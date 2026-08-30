import json
import subprocess
import sys
from pathlib import Path

import 接入单纤维循环迁移 as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_nested_single_fiber_endpoints():
    frame = source.build_release()
    assert len(frame) == 152
    assert frame["fiber_csv_id"].nunique() == 38
    assert frame.groupby("fiber_csv_id").size().eq(4).all()
    assert set(frame["nominal_strain_percent"]) == {10, 15, 20, 30}
    assert frame.groupby("hydration_condition").size().to_dict() == {
        "Dry": 60,
        "Soaked": 44,
        "Submerged": 48,
    }
    assert frame["diameter_um"].notna().all()
    assert frame["initialization_transient_rows_excluded"].eq(1).all()
    assert frame["peak_force_cycle1_uN"].max() < 300
    assert frame["sample_weight_ceiling"].eq(0.0625).all()
    assert frame["absolute_stress_available"].eq(False).all()  # noqa: E712
    assert frame["complete_toughness_available"].eq(False).all()  # noqa: E712
    assert frame["target_role"].eq(
        "single_fiber_cyclic_force_displacement_transfer"
    ).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入单纤维循环迁移.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "PCU85单纤维循环发布清单.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["counts"] == {
        "material_code_count": 1,
        "physical_fiber_count": 38,
        "condition_count": 3,
        "test_date_batch_count": 13,
        "nested_endpoint_row_count": 152,
        "curve_segment_count": 646,
        "machine_source_row_count": 53846,
        "initialization_transient_row_count": 38,
        "sem_image_count": 85,
        "sem_images_mapped_to_mechanical_fibers": 83,
        "sem_images_without_mechanical_csv": 2,
    }
    assert manifest["policy"]["raw_curves_republished"] is False
    assert manifest["policy"]["bulk_TPU_toughness_available"] is False
