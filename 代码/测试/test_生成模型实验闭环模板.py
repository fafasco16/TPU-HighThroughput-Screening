from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "生成模型实验闭环模板.py"
SPEC = importlib.util.spec_from_file_location("model_experiment_loop", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _template_row() -> dict:
    return {
        "prediction_value": 10.0,
        "prediction_unit": "MPa",
        "prediction_std": 1.0,
        "prediction_domain_status": "in_domain",
        "experimental_value": 12.0,
        "experimental_unit": "MPa",
        "experimental_std": 1.0,
        "experimental_qc_status": "passed",
        "training_batch_leakage_status": "batch_not_in_training",
        "calibration_role": "holdout_calibration_only",
        "gold_e_admission_status": "ready_for_gold_e_ingestion",
    }


def test_valid_holdout_pair_computes_residual_and_standardized_residual() -> None:
    result = MODULE.compute_residuals(pd.DataFrame([_template_row()]))
    assert result.iloc[0]["residual_experiment_minus_prediction"] == pytest.approx(2.0)
    assert result.iloc[0]["standardized_residual"] == pytest.approx(2**0.5)
    assert result.iloc[0]["closed_loop_status"] == "ready_holdout_residual"


def test_out_of_domain_prediction_does_not_compute_residual() -> None:
    row = _template_row()
    row["prediction_domain_status"] = "out_of_domain"
    result = MODULE.compute_residuals(pd.DataFrame([row]))
    assert result.iloc[0]["closed_loop_status"] == "blocked_domain"
    assert pd.isna(result.iloc[0]["residual_experiment_minus_prediction"])


def test_training_batch_leakage_blocks_calibration() -> None:
    row = _template_row()
    row["training_batch_leakage_status"] = "batch_present_in_training"
    result = MODULE.compute_residuals(pd.DataFrame([row]))
    assert "leakage" in result.iloc[0]["closed_loop_status"]
