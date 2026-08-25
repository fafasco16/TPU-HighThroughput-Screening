from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "生成实验回填模板.py"
SPEC = importlib.util.spec_from_file_location("experiment_capture_template", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _shortlist() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "experiment_order": index,
                "experiment_stage": "A_calibration",
                "formulation_id": f"f{index}",
                "diisocyanate_id": "d",
                "macrodiol_id": "m",
                "chain_extender_id": "c",
                "hard_segment_mass_fraction_target": 0.35,
                "nco_oh_ratio_target": 1.0,
                "experiment_release_status_current": "blocked",
            }
            for index in range(1, 7)
        ]
    )


def test_batch_template_has_six_empty_real_batch_ids() -> None:
    table = MODULE.build_batch_template(_shortlist())
    assert len(table) == 6
    assert table["batch_id"].eq("").all()
    assert table["gold_e_ingestion_status"].eq("not_ready_missing_real_batch").all()


def test_measurement_template_has_eight_tasks_per_formulation() -> None:
    table = MODULE.build_measurement_template(_shortlist())
    assert len(table) == 48
    assert table.groupby("formulation_id").size().eq(8).all()
    assert table["raw_file_sha256"].eq("").all()
