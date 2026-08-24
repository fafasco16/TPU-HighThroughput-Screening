from pathlib import Path

import pandas as pd
import pytest

import 生成现实MD计量计划 as mdplan


ROOT = Path(__file__).resolve().parents[2]


def _components() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "component_id": "di-1",
                "role": "diisocyanate",
                "canonical_smiles": "O=C=NCCCCCCN=C=O",
            },
            {
                "component_id": "ce-1",
                "role": "chain_extender",
                "canonical_smiles": "OCCCCO",
            },
            {
                "component_id": "m-1",
                "role": "macrodiol",
                "canonical_smiles": "",
            },
        ]
    )


def _macro_models() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "component_id": "m-1",
                "nominal_mn_g_mol": 1000.0,
                "representative_smiles": "OCCCCOCCCCO",
                "approximation_status": "single_oligomer_proxy_for_product_distribution",
            }
        ]
    )


def _formulations() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "formulation_id": "f-1",
                "diisocyanate_id": "di-1",
                "macrodiol_id": "m-1",
                "chain_extender_id": "ce-1",
                "macrodiol_nominal_mn_g_mol": 1000.0,
                "hard_segment_mass_fraction_target": 0.35,
                "nco_oh_ratio_target": 1.02,
                "performance_claim_status": "no_performance_claim",
            },
            {
                "formulation_id": "f-2",
                "diisocyanate_id": "di-1",
                "macrodiol_id": "m-1",
                "chain_extender_id": "ce-1",
                "macrodiol_nominal_mn_g_mol": 1000.0,
                "hard_segment_mass_fraction_target": 0.45,
                "nco_oh_ratio_target": 1.0,
                "performance_claim_status": "no_performance_claim",
            },
        ]
    )


def test_integer_plan_closes_nco_oh_and_hard_segment_with_explicit_proxy_scope():
    result = mdplan.build_md_stoichiometry_plan(
        _formulations(), _components(), _macro_models()
    )
    assert len(result) == 2
    assert (result["diisocyanate_count"] == result["macrodiol_count"] + result["chain_extender_count"]).all()
    assert result["realized_nco_oh_ratio"].eq(1.0).all()
    assert result.set_index("formulation_id").loc[
        "f-1", "nco_excess_fraction_batch_context"
    ] == pytest.approx(0.02)
    assert result.set_index("formulation_id").loc[
        "f-1", "nco_excess_representation"
    ] == "separate_batch_and_multichain_context_not_embedded_in_single_chain"
    assert result["hard_segment_fraction_abs_error"].le(0.015).all()
    assert result["macrodiol_count"].ge(1).all()
    assert result["chain_extender_count"].ge(1).all()
    assert result["model_scope"].eq("single_sequence_oligomer_proxy").all()
    assert result["md_execution_status"].eq(
        "blocked_pending_forcefield_COA_and_chain_distribution_validation"
    ).all()


def test_plan_is_deterministic_under_input_shuffle():
    first = mdplan.build_md_stoichiometry_plan(
        _formulations(), _components(), _macro_models()
    ).sort_values("formulation_id").reset_index(drop=True)
    second = mdplan.build_md_stoichiometry_plan(
        _formulations().sample(frac=1.0, random_state=3),
        _components().sample(frac=1.0, random_state=4),
        _macro_models(),
    ).sort_values("formulation_id").reset_index(drop=True)
    pd.testing.assert_frame_equal(first, second)


def test_invalid_ratio_identity_and_performance_claim_fail_closed():
    ratio = _formulations()
    ratio.loc[0, "nco_oh_ratio_target"] = 1.10
    with pytest.raises(ValueError, match="NCO/OH=1.00或1.02"):
        mdplan.build_md_stoichiometry_plan(ratio, _components(), _macro_models())
    components = _components()
    components.loc[0, "role"] = "chain_extender"
    with pytest.raises(ValueError, match="角色"):
        mdplan.build_md_stoichiometry_plan(
            _formulations(), components, _macro_models()
        )
    claimed = _formulations()
    claimed.loc[0, "performance_claim_status"] = "high_performance"
    with pytest.raises(ValueError, match="性能宣称"):
        mdplan.build_md_stoichiometry_plan(
            claimed, _components(), _macro_models()
        )


def test_writer_and_current_12_candidate_release(tmp_path: Path):
    f = tmp_path / "formulations.csv"
    c = tmp_path / "components.csv"
    m = tmp_path / "macro.csv"
    _formulations().to_csv(f, index=False)
    _components().to_csv(c, index=False)
    _macro_models().to_csv(m, index=False)
    manifest = mdplan.write_release(
        f, c, m, tmp_path / "out", release_id="test-md-plan"
    )
    assert manifest["counts"] == {"formulations": 2, "within_tolerance": 2}
    assert (tmp_path / "out" / "低聚链计量计划.csv").is_file()
    current = mdplan.build_md_stoichiometry_plan(
        pd.read_csv(ROOT / "结果" / "现实筛选" / "高层DFT候选12.csv"),
        pd.read_csv(ROOT / "数据" / "现实库" / "构件.csv"),
        pd.read_csv(ROOT / "数据" / "现实库" / "PTMG代表模型.csv"),
    )
    assert len(current) == 12
    assert current["hard_segment_fraction_abs_error"].le(0.015).all()
