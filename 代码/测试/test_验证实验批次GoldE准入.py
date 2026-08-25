from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "验证实验批次GoldE准入.py"
SPEC = importlib.util.spec_from_file_location("experimental_gold_e", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _ready_batch() -> dict:
    sha = "a" * 64
    row = {name: "x" for name in MODULE.BATCH_REQUIRED_TEXT}
    row.update({name: 1.0 for name in MODULE.BATCH_REQUIRED_NUMERIC})
    row.update(
        {
            "formulation_id": "f",
            "experiment_order": 1,
            "experiment_stage": "A_calibration",
            "diisocyanate_coa_sha256": sha,
            "macrodiol_coa_sha256": sha,
            "chain_extender_coa_sha256": sha,
            "actual_component_masses_g_json": json.dumps(
                {"diisocyanate_g": 1, "macrodiol_g": 2, "chain_extender_g": 1}
            ),
            "actual_release_status": "qc_passed",
            "gold_e_ingestion_status": "ready_for_gold_e",
        }
    )
    return row


def _ready_measurement(task: str) -> dict:
    row = {name: "x" for name in MODULE.MEASUREMENT_REQUIRED_TEXT}
    row.update(
        {
            "formulation_id": "f",
            "measurement_task": task,
            "raw_file_sha256": "b" * 64,
            "processed_file_sha256": "c" * 64,
            "unit_status": "verified",
            "qc_status": "passed",
            "gold_e_record_status": "ready_for_gold_e",
        }
    )
    return row


def test_complete_batch_and_mandatory_measurements_become_ready() -> None:
    batches = pd.DataFrame([_ready_batch()])
    measurements = pd.DataFrame(
        [_ready_measurement(task) for task in sorted(MODULE.MANDATORY_TASKS)]
    )
    audit = MODULE.audit_gold_e_admission(batches, measurements)
    assert audit.iloc[0]["gold_e_admission_status"] == "ready_for_gold_e_ingestion"
    assert audit.iloc[0]["mandatory_measurements_ready"] == 5


def test_blank_batch_remains_blocked_without_fabricating_defaults() -> None:
    batch = _ready_batch()
    batch["batch_id"] = pd.NA
    batches = pd.DataFrame([batch])
    measurements = pd.DataFrame(
        [_ready_measurement(task) for task in sorted(MODULE.MANDATORY_TASKS)]
    )
    audit = MODULE.audit_gold_e_admission(batches, measurements)
    assert audit.iloc[0]["gold_e_admission_status"].startswith("blocked_")
    assert "batch_id" in audit.iloc[0]["batch_missing_fields"]


def test_missing_mandatory_measurement_is_blocked() -> None:
    batches = pd.DataFrame([_ready_batch()])
    measurements = pd.DataFrame(
        [
            _ready_measurement(task)
            for task in sorted(MODULE.MANDATORY_TASKS - {"density"})
        ]
    )
    try:
        MODULE.audit_gold_e_admission(batches, measurements)
    except ValueError as exc:
        assert "必需测量计划不闭合" in str(exc)
    else:
        raise AssertionError("missing mandatory task must fail closed")
