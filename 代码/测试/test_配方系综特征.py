import numpy as np
import pandas as pd
import pytest

import 配方系综特征 as features


def _formulations():
    return pd.DataFrame(
        [
            {
                "queue_rank": 1,
                "formulation_id": "f-1",
                "combination_id": "c-1",
                "diisocyanate_id": "di-1",
                "macrodiol_proxy_id": "macro-1",
                "chain_extender_id": "ce-1",
                "macrodiol_nominal_mn_g_mol": 2000.0,
                "hard_segment_mass_fraction_target": 0.45,
                "nco_oh_ratio_target": 1.02,
                "measured_tensile_strength_mpa": 99.0,
            },
            {
                "queue_rank": 2,
                "formulation_id": "f-2",
                "combination_id": "c-2",
                "diisocyanate_id": "di-2",
                "macrodiol_proxy_id": "macro-1",
                "chain_extender_id": "ce-1",
                "macrodiol_nominal_mn_g_mol": 2000.0,
                "hard_segment_mass_fraction_target": 0.45,
                "nco_oh_ratio_target": 1.02,
                "measured_tensile_strength_mpa": 1.0,
            },
        ]
    )


def _wide_descriptors():
    return pd.DataFrame(
        [
            {"component_id": "di-1", "homo_ev": -6.0, "conformer_uncertainty": 0.1, "calculation_status": "completed"},
            {"component_id": "di-2", "homo_ev": -5.5, "conformer_uncertainty": 0.2, "calculation_status": "completed"},
            {"component_id": "macro-1", "homo_ev": -5.0, "conformer_uncertainty": 0.3, "calculation_status": "completed"},
            {"component_id": "ce-1", "homo_ev": -4.5, "conformer_uncertainty": 0.4, "calculation_status": "completed"},
        ]
    )


def test_strict_role_join_and_formula_variables_are_retained():
    result = features.aggregate_formulation_features(_formulations(), _wide_descriptors())
    row = result.set_index("formulation_id").loc["f-1"]
    assert row["diisocyanate__homo_ev"] == -6.0
    assert row["macrodiol_proxy__homo_ev"] == -5.0
    assert row["chain_extender__homo_ev"] == -4.5
    assert row["nco_oh_ratio_target"] == 1.02
    assert row["descriptor_join_status"] == "ready"
    assert row["conformer_uncertainty_status"] == "complete"


def test_long_table_is_supported_and_duplicate_component_feature_is_rejected():
    long = pd.DataFrame(
        [
            {"component_id": "x", "feature_name": "homo_ev", "feature_value": -5.0, "calculation_status": "completed"},
            {"component_id": "x", "feature_name": "dipole_debye", "feature_value": 2.0, "calculation_status": "completed"},
        ]
    )
    wide = features.normalize_component_descriptors(long)
    assert wide.loc[0, "dipole_debye"] == 2.0
    duplicated = pd.concat([long, long.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="ID/特征重复"):
        features.normalize_component_descriptors(duplicated)


def test_duplicate_component_id_is_closed_not_silently_multiplied():
    duplicated = pd.concat([_wide_descriptors(), _wide_descriptors().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="component_id 不唯一"):
        features.aggregate_formulation_features(_formulations(), duplicated)


def test_missing_and_blocked_components_remain_visible_but_ineligible():
    descriptors = _wide_descriptors().query("component_id != 'di-2'").copy()
    descriptors.loc[descriptors["component_id"].eq("ce-1"), "calculation_status"] = "blocked_input"
    joined = features.aggregate_formulation_features(_formulations(), descriptors)
    assert joined.loc[0, "descriptor_join_status"] == "blocked"
    assert joined.loc[1, "descriptor_join_status"] == "missing_component_descriptor"
    assert joined.loc[1, "component_descriptor_missing_count"] == 1
    assert np.isnan(joined.loc[1, "diisocyanate__homo_ev"])


def test_component_descriptor_leakage_is_rejected_and_formula_outcome_not_copied():
    descriptors = _wide_descriptors().assign(split="train")
    with pytest.raises(ValueError, match="潜在泄漏字段"):
        features.aggregate_formulation_features(_formulations(), descriptors)
    result = features.aggregate_formulation_features(_formulations(), _wide_descriptors())
    assert "measured_tensile_strength_mpa" not in result.columns
    with pytest.raises(ValueError, match="潜在泄漏字段"):
        features.aggregate_formulation_features(
            _formulations(),
            _wide_descriptors().assign(measured_strength_mpa=42.0),
        )


def test_pareto_dominance_directions_duplicates_and_missing_value_gate():
    frame = pd.DataFrame(
        {
            "formulation_id": ["a", "b", "c", "d", "e"],
            "descriptor_join_status": ["ready", "ready", "ready", "ready", "blocked"],
            "reactivity": [3.0, 2.0, 4.0, np.nan, 100.0],
            "uncertainty": [1.0, 2.0, 3.0, 0.1, 0.0],
        }
    )
    result = features.prepare_pareto_input(
        frame, {"reactivity": "max", "uncertainty": "min"}
    ).set_index("formulation_id")
    assert result.loc["a", "pareto_is_nondominated"]
    assert not result.loc["b", "pareto_is_nondominated"]
    assert result.loc["c", "pareto_is_nondominated"]
    assert result.loc["d", "pareto_exclusion_reason"] == "objective_missing_or_nonfinite"
    assert result.loc["e", "pareto_exclusion_reason"] == "component_gate_failed"
    assert result["pareto_score"].isna().all()


def test_equal_pareto_points_are_both_retained_and_direction_is_explicit():
    frame = pd.DataFrame({"x": [1.0, 1.0], "status": ["ready", "ready"]})
    result = features.prepare_pareto_input(
        frame, {"x": "max"}, status_column="status"
    )
    assert result["pareto_is_nondominated"].tolist() == [True, True]
    with pytest.raises(ValueError, match="min/max"):
        features.prepare_pareto_input(frame, {"x": "higher"}, status_column="status")


def test_long_table_rejects_ambiguous_metadata_status_and_nonnumeric_values():
    base = pd.DataFrame(
        [
            {"component_id": "x", "feature_name": "homo_ev", "feature_value": -5.0, "calculation_status": "completed"},
            {"component_id": "x", "feature_name": "dipole", "feature_value": 2.0, "calculation_status": "completed"},
        ]
    )
    with pytest.raises(ValueError, match="未声明的元数据"):
        features.normalize_component_descriptors(base.assign(method="GFN2-xTB"))
    conflicting = base.copy()
    conflicting.loc[1, "calculation_status"] = "running"
    with pytest.raises(ValueError, match="多个 calculation_status"):
        features.normalize_component_descriptors(conflicting)
    invalid = base.astype({"feature_value": "object"})
    invalid.loc[1, "feature_value"] = "not-a-number"
    with pytest.raises(ValueError, match="不是数值"):
        features.normalize_component_descriptors(invalid)


def test_explicit_feature_validation_and_not_ready_status():
    with pytest.raises(ValueError, match="缺少指定特征"):
        features.aggregate_formulation_features(
            _formulations(), _wide_descriptors(), feature_columns=["missing"]
        )
    nonnumeric = _wide_descriptors().assign(method="GFN2-xTB")
    with pytest.raises(ValueError, match="必须为数值"):
        features.aggregate_formulation_features(
            _formulations(), nonnumeric, feature_columns=["method"]
        )
    pending = _wide_descriptors()
    pending.loc[pending["component_id"].eq("di-1"), "calculation_status"] = "running"
    result = features.aggregate_formulation_features(_formulations(), pending)
    assert result.loc[0, "descriptor_join_status"] == "not_ready"


def test_pareto_api_closes_invalid_shapes_and_missing_schema():
    frame = pd.DataFrame({"x": [1.0], "status": ["ready"]})
    with pytest.raises(ValueError, match="目标不能为空"):
        features.pareto_nondominated_mask(frame, {})
    with pytest.raises(ValueError, match="缺少目标"):
        features.pareto_nondominated_mask(frame, {"missing": "max"})
    with pytest.raises(ValueError, match="行数不一致"):
        features.pareto_nondominated_mask(frame, {"x": "max"}, eligibility_mask=[])
    with pytest.raises(ValueError, match="缺少状态字段"):
        features.prepare_pareto_input(frame, {"x": "max"})
