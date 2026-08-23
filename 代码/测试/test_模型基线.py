import copy
import gzip
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

import 模型基线 as baseline
import 生成模型基线 as orchestrator


def _minimal_orchestrator_config() -> dict:
    return {
        "release": {"id": "test-release", "manifest": "发布清单.json"},
        "computational_task": {
            "task_id": "计算_结构多任务预训练",
            "targets": [{"name": "density", "unit": "g/cm^3", "role": "primary_baseline"}],
            "training_modes": ["primary_only", "primary_plus_aux"],
            "minimum_groups": {"train": 1, "validation": 1, "test": 1},
            "source_holdout_minimum_groups": {
                "train": 1,
                "validation": 1,
                "test": 1,
            },
        },
        "features": {"morgan_radius": 2, "morgan_bits": 64},
        "ridge": {"alphas": [0.1, 1.0]},
        "curves": {
            "task_id": "实验_曲线建模",
            "derive_only_when_release_model_ready": True,
        },
        "pue643": {
            "enabled": True,
            "source_family_id": "family_pue643",
            "source_id": "source_pue643",
            "condition_name": "published_input_feature_vector",
            "targets": ["logYM"],
            "expected_input_fields": 20,
            "categorical_fields": ["Form_Method", "PMStep"],
            "split_seed": 20260722,
            "minimum_groups": {"train": 1, "validation": 1, "test": 1},
        },
        "run": {"random_seed": 20260722, "csv_float_format": "%.12g"},
    }


def _write_yaml(path: Path, value: dict) -> None:
    path.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _pue_payload(index: int, *, form_method: int, process_step: int) -> dict:
    payload = {f"x{number:02d}": float(index + number) for number in range(18)}
    # 混合使用数字和可转为数字的 JSON 字符串，贴近公开表中的编码类别。
    payload["Form_Method"] = str(form_method)
    payload["PMStep"] = process_step
    return payload


def test_weighted_mean_and_ridge_recover_simple_linear_relation():
    x = np.array([[0.0], [1.0], [2.0], [3.0]])
    y = np.array([1.0, 3.0, 5.0, 7.0])
    weights = np.array([1.0, 1.0, 2.0, 2.0])

    assert baseline.weighted_mean(y, weights) == pytest.approx(14.0 / 3.0)
    model = baseline.fit_weighted_ridge(x, y, weights, alpha=1e-10)
    prediction = baseline.predict_weighted_ridge(model, x)

    assert prediction == pytest.approx(y, abs=1e-7)
    assert model["feature_mean"].shape == (1,)
    assert model["feature_scale"].shape == (1,)


def test_weighted_ridge_rejects_invalid_inputs():
    with pytest.raises(ValueError, match="二维"):
        baseline.fit_weighted_ridge(
            np.array([1.0, 2.0]),
            np.array([1.0, 2.0]),
            np.ones(2),
            alpha=1.0,
        )
    with pytest.raises(ValueError, match="正权重"):
        baseline.weighted_mean(np.array([1.0, 2.0]), np.zeros(2))
    with pytest.raises(ValueError, match="非负"):
        baseline.fit_weighted_ridge(
            np.eye(2), np.array([1.0, 2.0]), np.ones(2), alpha=-1.0
        )
    with pytest.raises(ValueError, match="观测数量"):
        baseline.fit_weighted_ridge(
            np.eye(2), np.array([1.0]), np.ones(1), alpha=1.0
        )
    with pytest.raises(ValueError, match="有限数"):
        baseline.fit_weighted_ridge(
            np.array([[1.0], [np.nan]]),
            np.array([1.0, 2.0]),
            np.ones(2),
            alpha=1.0,
        )
    with pytest.raises(ValueError, match="观测数量"):
        baseline.weighted_mean(np.array([1.0, 2.0]), np.ones(3))
    with pytest.raises(ValueError, match="有限数"):
        baseline.weighted_mean(np.array([1.0, np.nan]), np.ones(2))


def test_ridge_constant_feature_fallback_and_prediction_validation(monkeypatch):
    monkeypatch.setattr(
        baseline.np.linalg,
        "solve",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(np.linalg.LinAlgError()),
    )
    model = baseline.fit_weighted_ridge(
        np.ones((3, 1)), np.array([1.0, 2.0, 3.0]), np.ones(3), alpha=0.0
    )
    assert model["feature_scale"] == pytest.approx([1.0])
    assert baseline.predict_weighted_ridge(model, np.ones((1, 1))) == pytest.approx([2.0])
    with pytest.raises(ValueError, match="二维"):
        baseline.predict_weighted_ridge(model, np.ones(1))
    with pytest.raises(ValueError, match="维度"):
        baseline.predict_weighted_ridge(model, np.ones((1, 2)))
    with pytest.raises(ValueError, match="有限数"):
        baseline.predict_weighted_ridge(model, np.array([[np.nan]]))


def test_regression_metrics_are_weighted_and_spearman_is_rank_based():
    metrics = baseline.regression_metrics(
        np.array([0.0, 1.0, 2.0]),
        np.array([0.0, 1.0, 2.0]),
        np.array([1.0, 2.0, 3.0]),
    )
    assert metrics == pytest.approx(
        {"mae": 0.0, "rmse": 0.0, "r2": 1.0, "spearman": 1.0}
    )

    weighted = baseline.regression_metrics(
        np.array([0.0, 2.0]),
        np.array([1.0, 2.0]),
        np.array([3.0, 1.0]),
    )
    assert weighted["mae"] == pytest.approx(0.75)
    assert weighted["rmse"] == pytest.approx(math.sqrt(0.75))
    constant = baseline.regression_metrics(
        np.ones(3), np.ones(3), np.ones(3)
    )
    assert math.isnan(constant["r2"])
    assert math.isnan(constant["spearman"])
    with pytest.raises(ValueError, match="观测数量"):
        baseline.regression_metrics([1.0], [1.0, 2.0], [1.0])
    positive_weight_only = baseline.regression_metrics(
        np.array([0.0, 1.0, 2.0]),
        np.array([3.0, 1.0, 2.0]),
        np.array([0.0, 1.0, 1.0]),
    )
    assert positive_weight_only["spearman"] == pytest.approx(1.0)


def test_morgan_fingerprint_is_deterministic_binary_and_validated():
    first = baseline.featurize_smiles("CCO", radius=2, n_bits=64)
    second = baseline.featurize_smiles("CCO", radius=2, n_bits=64)
    other = baseline.featurize_smiles("c1ccccc1", radius=2, n_bits=64)

    assert first.shape == (64,)
    assert first.dtype == np.float64
    assert set(np.unique(first)).issubset({0.0, 1.0})
    assert np.array_equal(first, second)
    assert not np.array_equal(first, other)
    with pytest.raises(ValueError, match="SMILES"):
        baseline.featurize_smiles("not-a-smiles", radius=2, n_bits=64)
    with pytest.raises(ValueError, match="不能为空"):
        baseline.featurize_smiles("", radius=2, n_bits=64)
    with pytest.raises(ValueError, match="radius"):
        baseline.featurize_smiles("CC", radius=-1, n_bits=64)


def test_feature_group_and_split_prevent_identical_input_leakage():
    group_a = baseline.stable_numeric_feature_group([1.0, 2.0, 3.0])
    group_b = baseline.stable_numeric_feature_group(np.array([1, 2, 3]))
    group_c = baseline.stable_numeric_feature_group([1.0, 2.0, 3.1])

    assert group_a == group_b
    assert group_a != group_c
    assert baseline.deterministic_split(group_a, seed=20260722) == baseline.deterministic_split(
        group_b, seed=20260722
    )
    assert baseline.deterministic_split(group_a, seed=20260722) in {
        "train",
        "validation",
        "test",
    }

    mixed_a = baseline.stable_mixed_feature_group({"method": "A", "x": 1})
    mixed_b = baseline.stable_mixed_feature_group({"x": 1.0, "method": "A"})
    mixed_c = baseline.stable_mixed_feature_group({"method": "B", "x": 1})
    assert mixed_a == mixed_b
    assert mixed_a != mixed_c
    assert baseline.stable_mixed_feature_group({"flag": True}).startswith(
        "mixed-feature-"
    )
    with pytest.raises(ValueError, match="非空"):
        baseline.stable_mixed_feature_group({})
    with pytest.raises(ValueError, match="有限数"):
        baseline.stable_mixed_feature_group({"x": np.inf})
    with pytest.raises(ValueError, match="不受支持"):
        baseline.stable_mixed_feature_group({"x": None})
    with pytest.raises(ValueError, match="不能为空"):
        baseline.deterministic_split("")

    observed = {
        baseline.deterministic_split(f"group-{index}", seed=1)
        for index in range(100)
    }
    assert observed == {"train", "validation", "test"}


def test_group_split_validation_fails_closed_on_leakage():
    safe = pd.DataFrame(
        {
            "leakage_group": ["g1", "g1", "g2"],
            "development_split": ["train", "train", "test"],
        }
    )
    baseline.validate_group_split(safe, "leakage_group", "development_split")

    leaking = safe.copy()
    leaking.loc[1, "development_split"] = "validation"
    with pytest.raises(ValueError, match="跨越"):
        baseline.validate_group_split(
            leaking, "leakage_group", "development_split"
        )
    with pytest.raises(ValueError, match="缺少"):
        baseline.validate_group_split(pd.DataFrame({"x": [1]}), "g", "s")
    with pytest.raises(ValueError, match="为空"):
        baseline.validate_group_split(
            pd.DataFrame({"g": ["  "], "s": ["train"]}), "g", "s"
        )


def test_choose_ridge_alpha_uses_validation_rmse_and_returns_a_model():
    train_x = np.arange(8, dtype=float).reshape(-1, 1)
    train_y = 1.0 + 2.0 * train_x[:, 0]
    validation_x = np.array([[8.0], [9.0]])
    validation_y = np.array([17.0, 19.0])
    result = baseline.choose_ridge_alpha(
        train_x,
        train_y,
        np.ones(8),
        validation_x,
        validation_y,
        np.ones(2),
        alphas=[1000.0, 1e-10],
    )

    assert result["alpha"] == pytest.approx(1e-10)
    assert result["validation_rmse"] < 1e-6
    assert baseline.predict_weighted_ridge(result["model"], validation_x) == pytest.approx(
        validation_y, abs=1e-6
    )
    with pytest.raises(ValueError, match="不能为空"):
        baseline.choose_ridge_alpha(
            train_x,
            train_y,
            np.ones(8),
            validation_x,
            validation_y,
            np.ones(2),
            alphas=[],
        )
    with pytest.raises(ValueError, match="非负"):
        baseline.choose_ridge_alpha(
            train_x,
            train_y,
            np.ones(8),
            validation_x,
            validation_y,
            np.ones(2),
            alphas=[-1.0],
        )


def test_tensile_curve_endpoints_use_fractional_strain_for_modulus_and_area():
    points = pd.DataFrame(
        {
            "property_name": ["tensile_stress"] * 6,
            "unit": ["MPa"] * 6,
            "condition_name": ["tensile_strain"] * 6,
            "condition_value": [0.0, 0.5, 1.0, 1.5, 2.0, 3.0],
            "condition_unit": ["%"] * 6,
            "value": [0.0, 5.0, 10.0, 15.0, 20.0, 25.0],
            "point_index": [0, 1, 2, 3, 4, 5],
        }
    )

    endpoints = baseline.derive_curve_endpoints(points)

    assert endpoints["endpoint_status"] == "physical_endpoints_ready"
    assert endpoints["curve_type"] == "tensile"
    assert endpoints["max_observed_stress_mpa"] == pytest.approx(25.0)
    assert endpoints["max_observed_strain"] == pytest.approx(3.0)
    assert endpoints["max_observed_strain_unit"] == "%"
    assert endpoints["low_strain_modulus_status"] == "linear_fit_passed"
    assert endpoints["low_strain_linear_modulus_mpa"] == pytest.approx(1000.0)
    assert endpoints["low_strain_linear_r2"] == pytest.approx(1.0)
    assert endpoints["recorded_energy_density_mj_m3"] == pytest.approx(0.425)


def test_compressive_curve_interpolates_fixed_strain_endpoints():
    points = pd.DataFrame(
        {
            "property_name": ["compressive_stress"] * 3,
            "unit": ["MPa"] * 3,
            "condition_name": ["strain"] * 3,
            "condition_value": [0.0, 0.1, 0.25],
            "condition_unit": ["1"] * 3,
            "value": [0.0, 2.0, 5.0],
            "point_index": [0, 1, 2],
        }
    )

    endpoints = baseline.derive_curve_endpoints(points)

    assert endpoints["endpoint_status"] == "physical_endpoints_ready"
    assert endpoints["curve_type"] == "compression"
    assert endpoints["stress_at_10pct_mpa"] == pytest.approx(2.0)
    assert endpoints["stress_at_25pct_mpa"] == pytest.approx(5.0)
    assert endpoints["secant_modulus_10pct_mpa"] == pytest.approx(20.0)
    assert endpoints["recorded_energy_density_mj_m3"] == pytest.approx(0.625)
    assert endpoints["energy_density_to_25pct_mj_m3"] == pytest.approx(0.625)


def test_sparse_tensile_curve_does_not_invent_young_modulus():
    points = pd.DataFrame(
        {
            "property_name": ["tensile_stress"] * 4,
            "unit": ["MPa"] * 4,
            "condition_name": ["tensile_strain"] * 4,
            "condition_value": [0.0, 2.0, 50.0, 100.0],
            "condition_unit": ["%"] * 4,
            "value": [0.0, 2.0, 10.0, 12.0],
            "point_index": [0, 1, 2, 3],
        }
    )

    endpoints = baseline.derive_curve_endpoints(points)

    assert endpoints["low_strain_modulus_status"] == "insufficient_low_strain_points"
    assert "low_strain_linear_modulus_mpa" not in endpoints
    assert endpoints["stress_at_50pct_mpa"] == pytest.approx(10.0)
    assert endpoints["stress_at_100pct_mpa"] == pytest.approx(12.0)
    assert "stress_at_300pct_mpa" not in endpoints


@pytest.mark.parametrize(
    "strain, stress",
    [
        ([1.80, 1.85, 1.90, 1.95, 2.00], [1, 2, 3, 4, 5]),
        ([0.0, 0.5, 1.0, 1.5, 2.0], [50, 55, 60, 65, 70]),
    ],
)
def test_low_strain_modulus_requires_zero_span_and_small_intercept(strain, stress):
    points = pd.DataFrame(
        {
            "property_name": ["tensile_stress"] * 5,
            "unit": ["MPa"] * 5,
            "condition_name": ["tensile_strain"] * 5,
            "condition_value": strain,
            "condition_unit": ["%"] * 5,
            "value": stress,
            "point_index": range(5),
        }
    )

    endpoints = baseline.derive_curve_endpoints(points)
    assert endpoints["low_strain_modulus_status"] == "zero_span_or_intercept_gate_failed"
    assert "low_strain_linear_modulus_mpa" not in endpoints


def test_cyclic_curve_is_fail_closed_until_cycle_segmentation_is_applied():
    points = pd.DataFrame(
        {
            "property_name": ["cyclic_tensile_stress"] * 3,
            "unit": ["kPa"] * 3,
            "condition_name": ["tensile_strain"] * 3,
            "condition_value": [0.0, 50.0, 0.0],
            "condition_unit": ["%"] * 3,
            "value": [0.0, 500.0, -10.0],
            "point_index": [0, 1, 2],
        }
    )

    assert baseline.derive_curve_endpoints(points) == {
        "endpoint_status": "cycle_segmentation_required",
        "curve_type": "cyclic_tensile",
    }


def test_negative_compression_sign_convention_is_rejected():
    points = pd.DataFrame(
        {
            "property_name": ["compressive_stress"] * 3,
            "unit": ["MPa"] * 3,
            "condition_name": ["compressive_strain"] * 3,
            "condition_value": [0.0, 0.1, 0.25],
            "condition_unit": ["dimensionless"] * 3,
            "value": [0.0, -2.0, -5.0],
            "point_index": [0, 1, 2],
        }
    )
    assert baseline.derive_curve_endpoints(points) == {
        "endpoint_status": "unsupported_compression_sign_convention",
        "curve_type": "compression",
    }


def test_curve_endpoint_gate_rejects_unknown_units_without_guessing():
    points = pd.DataFrame(
        {
            "property_name": ["tensile_stress", "tensile_stress"],
            "unit": ["unknown", "unknown"],
            "condition_name": ["tensile_strain", "tensile_strain"],
            "condition_value": [0.0, 1.0],
            "condition_unit": ["%", "%"],
            "value": [0.0, 1.0],
            "point_index": [0, 1],
        }
    )

    endpoints = baseline.derive_curve_endpoints(points)

    assert endpoints == {
        "endpoint_status": "unsupported_axis_or_unit",
        "curve_type": "unsupported",
    }


@pytest.mark.parametrize("condition_units", [["", ""], ["%", "1"]])
def test_curve_endpoint_gate_rejects_missing_or_conflicting_strain_units(
    condition_units,
):
    points = pd.DataFrame(
        {
            "property_name": ["tensile_stress"] * 2,
            "unit": ["MPa"] * 2,
            "condition_name": ["tensile_strain"] * 2,
            "condition_value": [0.0, 1.0],
            "condition_unit": condition_units,
            "value": [0.0, 1.0],
            "point_index": [0, 1],
        }
    )

    assert baseline.derive_curve_endpoints(points) == {
        "endpoint_status": "unsupported_axis_or_unit",
        "curve_type": "unsupported",
    }


@pytest.mark.parametrize(
    "points",
    [
        pd.DataFrame({"value": [1.0]}),
        pd.DataFrame(
            {
                "property_name": ["ftir_signal"] * 2,
                "unit": ["a.u."] * 2,
                "condition_name": ["wavenumber"] * 2,
                "condition_value": [1.0, 2.0],
                "condition_unit": ["cm-1"] * 2,
                "value": [1.0, 2.0],
            }
        ),
        pd.DataFrame(
            {
                "property_name": ["tensile_stress"] * 2,
                "unit": ["MPa"] * 2,
                "condition_name": ["tensile_strain"] * 2,
                "condition_value": [np.nan, 1.0],
                "condition_unit": ["%"] * 2,
                "value": [0.0, 1.0],
            }
        ),
        pd.DataFrame(
            {
                "property_name": ["tensile_stress"] * 2,
                "unit": ["MPa"] * 2,
                "condition_name": ["tensile_strain"] * 2,
                "condition_value": [0.0, 1.0],
                "condition_unit": ["mystery"] * 2,
                "value": [0.0, 1.0],
            }
        ),
    ],
)
def test_curve_endpoint_gate_handles_incomplete_or_unsupported_inputs(points):
    assert baseline.derive_curve_endpoints(points)["endpoint_status"] == (
        "unsupported_axis_or_unit"
    )


def test_monotonic_curve_gate_does_not_sort_away_loading_unloading_path():
    points = pd.DataFrame(
        {
            "property_name": ["tensile_stress"] * 3,
            "unit": ["MPa"] * 3,
            "condition_name": ["tensile_strain"] * 3,
            "condition_value": [0.0, 10.0, 5.0],
            "condition_unit": ["%"] * 3,
            "value": [0.0, 2.0, 1.0],
            "point_index": [0, 1, 2],
        }
    )

    assert baseline.derive_curve_endpoints(points) == {
        "endpoint_status": "nonmonotonic_axis_requires_specialized_processing",
        "curve_type": "tensile",
    }


def test_orchestrator_config_rejects_missing_section_and_invalid_alpha(tmp_path):
    valid = _minimal_orchestrator_config()
    missing_section = copy.deepcopy(valid)
    del missing_section["curves"]
    missing_path = tmp_path / "缺分区.yaml"
    _write_yaml(missing_path, missing_section)

    with pytest.raises(ValueError, match="缺少分区.*curves"):
        orchestrator.load_config(missing_path)

    invalid_alpha = copy.deepcopy(valid)
    invalid_alpha["ridge"]["alphas"] = [0.1, -1.0]
    invalid_path = tmp_path / "无效alpha.yaml"
    _write_yaml(invalid_path, invalid_alpha)

    with pytest.raises(ValueError, match="有限非负数"):
        orchestrator.load_config(invalid_path)


def test_atomic_csv_outputs_are_byte_deterministic_and_gzip_mtime_is_zero(tmp_path):
    frame = pd.DataFrame(
        {
            "name": ["聚氨酯", "TPU"],
            "value": [1.0 / 3.0, 2.0],
        }
    )
    plain_path = tmp_path / "结果.csv"
    gzip_path = tmp_path / "结果.csv.gz"

    orchestrator._atomic_write_csv(
        frame, plain_path, gzip_output=False, float_format="%.12g"
    )
    first_plain = plain_path.read_bytes()
    orchestrator._atomic_write_csv(
        frame, plain_path, gzip_output=False, float_format="%.12g"
    )
    assert plain_path.read_bytes() == first_plain
    assert first_plain.startswith(b"\xef\xbb\xbf")

    orchestrator._atomic_write_csv(
        frame, gzip_path, gzip_output=True, float_format="%.12g"
    )
    first_gzip = gzip_path.read_bytes()
    orchestrator._atomic_write_csv(
        frame, gzip_path, gzip_output=True, float_format="%.12g"
    )
    assert gzip_path.read_bytes() == first_gzip
    assert int.from_bytes(first_gzip[4:8], byteorder="little") == 0
    assert gzip.decompress(first_gzip).decode("utf-8").startswith("name,value\n")


def test_pue_parser_groups_identical_mixed_json_and_one_hot_encodes_categories():
    config = _minimal_orchestrator_config()
    first = _pue_payload(1, form_method=1, process_step=10)
    same_reordered = dict(reversed(list(first.items())))
    other_categories = _pue_payload(1, form_method=2, process_step=20)
    rows = pd.DataFrame(
        [
            {"observation_id": "pue-a", "condition_value": json.dumps(first)},
            {
                "observation_id": "pue-b",
                "condition_value": json.dumps(same_reordered),
            },
            {
                "observation_id": "pue-c",
                "condition_value": json.dumps(other_categories),
            },
        ]
    )

    parsed, fields = orchestrator._parse_pue_features(rows, config)

    assert len(fields) == 20
    assert parsed.loc[0, "leakage_group"] == parsed.loc[1, "leakage_group"]
    assert parsed.loc[0, "development_split"] == parsed.loc[1, "development_split"]
    assert parsed.loc[0, "leakage_group"] != parsed.loc[2, "leakage_group"]

    matrix = orchestrator._pue_design_matrix(
        parsed.iloc[[0, 2]],
        fields,
        ["Form_Method", "PMStep"],
        {"Form_Method": [1.0, 2.0], "PMStep": [10.0, 20.0]},
    )
    assert matrix.shape == (2, 22)  # 18 连续字段 + 2×2 类别哑变量
    assert matrix[0, -4:] == pytest.approx([1.0, 0.0, 1.0, 0.0])
    assert matrix[1, -4:] == pytest.approx([0.0, 1.0, 0.0, 1.0])


def test_pue_category_vocabulary_is_fitted_on_train_split_only():
    config = _minimal_orchestrator_config()
    seed = config["pue643"]["split_seed"]

    def payload_for_split(
        desired_split: str, *, form_method: int, process_step: int
    ) -> dict:
        for index in range(10_000):
            payload = _pue_payload(
                index, form_method=form_method, process_step=process_step
            )
            normalized = {key: float(value) for key, value in payload.items()}
            group = baseline.stable_mixed_feature_group(normalized)
            if baseline.deterministic_split(group, seed=seed) == desired_split:
                return payload
        raise AssertionError(f"无法构造 {desired_split} PUE 夹具")

    payloads = {
        "train": payload_for_split("train", form_method=1, process_step=10),
        "validation": payload_for_split(
            "validation", form_method=2, process_step=20
        ),
        "test": payload_for_split("test", form_method=2, process_step=20),
    }
    observations = pd.DataFrame(
        [
            {
                "observation_id": f"pue-{split}",
                "source_id": config["pue643"]["source_id"],
                "source_family_id": config["pue643"]["source_family_id"],
                "property_name": "logYM",
                "condition_name": config["pue643"]["condition_name"],
                "condition_value": json.dumps(payload, sort_keys=True),
                "value": float(index + 1),
                "source_locator": "fixture",
                "citation_keys": "fixture",
            }
            for index, (split, payload) in enumerate(payloads.items())
        ]
    )

    metrics, predictions, metadata = orchestrator.run_pue_smoke(
        observations, config
    )

    assert not metrics.empty
    assert not predictions.empty
    assert metadata["status"] == "completed"
    assert metadata["published_input_field_count"] == 20
    assert metadata["numeric_field_count"] == 18
    assert metadata["category_levels_from_train_only"]["logYM"] == {
        "Form_Method": [1.0],
        "PMStep": [10.0],
    }
    unknown = metadata["unknown_category_rows_encoded_all_zero"]["logYM"]
    assert unknown["train"] == {"Form_Method": 0, "PMStep": 0}
    assert unknown["validation"] == {"Form_Method": 1, "PMStep": 1}
    assert unknown["test"] == {"Form_Method": 1, "PMStep": 1}


def test_curve_index_emits_exactly_one_row_and_fails_closed():
    config = _minimal_orchestrator_config()
    curve_index = pd.DataFrame(
        [
            {
                "curve_id": "curve-1",
                "source_id": "source-1",
                "source_family_id": "family-1",
                "formulation_id": "form-1",
                "sample_id": "sample-1",
                "property_name": "tensile_stress",
                "unit": "MPa",
                "condition_name": "tensile_strain",
                "condition_unit": "%",
                "model_ready": False,
                "leakage_group": "group-1",
                "development_split": "test",
                "source_locator": "fixture",
                "citation_keys": "fixture",
            }
        ]
    )
    observations = pd.DataFrame(
        [
            {
                "task_id": "实验_曲线建模",
                "curve_id": "curve-1",
                "development_split": "test",
                "leakage_group": "group-1",
                "property_name": "tensile_stress",
                "unit": "MPa",
                "condition_name": "tensile_strain",
                "condition_unit": "%",
                "condition_value": strain,
                "value": stress,
                "point_index": point_index,
            }
            for point_index, (strain, stress) in enumerate([(0.0, 0.0), (1.0, 5.0)])
        ]
    )
    # 同一 curve_id 的派生标量不能导致重复输出，也不能被混入曲线点。
    observations.loc[len(observations)] = {
        "task_id": "实验_曲线建模",
        "curve_id": "curve-1",
        "development_split": "test",
        "leakage_group": "group-1",
        "property_name": "tensile_strength",
        "unit": "MPa",
        "condition_name": "",
        "condition_unit": "",
        "condition_value": np.nan,
        "value": 5.0,
        "point_index": np.nan,
    }

    endpoints = orchestrator.build_curve_endpoints(
        observations, curve_index, config
    )

    assert len(endpoints) == len(curve_index) == 1
    assert endpoints.loc[0, "point_count"] == 2
    assert endpoints.loc[0, "endpoint_status"] == "release_not_model_ready"

    missing = orchestrator.build_curve_endpoints(
        observations.loc[observations["curve_id"].eq("not-present")],
        curve_index,
        config,
    )
    assert len(missing) == 1
    assert missing.loc[0, "endpoint_status"] == "missing_curve_points"
    assert missing.loc[0, "point_count"] == 0


def test_generated_output_hash_verification_detects_tampering(tmp_path):
    result_path = tmp_path / "指标.csv"
    result_path.write_text("metric,value\nmae,1\n", encoding="utf-8")
    manifest = {
        "release_id": "test-release",
        "output_files": {
            "metrics": {
                "path": result_path.name,
                "sha256": orchestrator._sha256(result_path),
            }
        },
    }
    (tmp_path / orchestrator.OUTPUT_NAMES["manifest"]).write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    verified = orchestrator.verify_generated_outputs(
        tmp_path, expected_release_id="test-release"
    )
    assert verified["status"] == "verified"
    assert set(verified["checked"]) == {"metrics"}

    result_path.write_text("metric,value\nmae,999\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 漂移"):
        orchestrator.verify_generated_outputs(
            tmp_path, expected_release_id="test-release"
        )
