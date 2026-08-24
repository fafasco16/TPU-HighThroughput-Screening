from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import 生成预反应复合物任务 as complexes


ROOT = Path(__file__).resolve().parents[2]


def test_pair_builder_deduplicates_two_pair_types_and_preserves_source_formulations():
    subset = pd.DataFrame(
        [
            {
                "formulation_id": "f-1",
                "base_system_id": "b-1",
                "diisocyanate_id": "di-1",
                "macrodiol_id": "m-1",
                "chain_extender_id": "ce-1",
            },
            {
                "formulation_id": "f-2",
                "base_system_id": "b-1",
                "diisocyanate_id": "di-1",
                "macrodiol_id": "m-1",
                "chain_extender_id": "ce-1",
            },
            {
                "formulation_id": "f-3",
                "base_system_id": "b-2",
                "diisocyanate_id": "di-1",
                "macrodiol_id": "m-2",
                "chain_extender_id": "ce-1",
            },
        ]
    )
    pairs = complexes.build_unique_pairs(subset)
    assert len(pairs) == 3
    assert pairs["pair_id"].is_unique
    assert pairs["pair_type"].value_counts().to_dict() == {
        "diisocyanate_macrodiol": 2,
        "diisocyanate_chain_extender": 1,
    }
    ce = pairs.loc[pairs["pair_type"].eq("diisocyanate_chain_extender")].iloc[0]
    assert ce["formulation_count"] == 3
    assert ce["formulation_ids"] == "f-1;f-2;f-3"


def test_current_release_materializes_22_pairs_and_88_hashed_geometry_tasks(tmp_path):
    tasks, pairs, release = complexes.build_release(
        subset_path=ROOT / "结果" / "现实筛选" / "高层DFT候选12.csv",
        monomer_manifest_path=ROOT
        / "计算"
        / "现实预反应复合物"
        / "单体结构清单.csv",
        monomer_root=ROOT / "计算" / "现实预反应复合物",
        components_path=ROOT / "数据" / "现实库" / "构件.csv",
        ptmg_models_path=ROOT / "数据" / "现实库" / "PTMG代表模型.csv",
        discrete_results_path=ROOT
        / "计算"
        / "现实xTB系综"
        / "聚合"
        / "逐构象描述符.csv",
        ptmg_results_path=ROOT
        / "计算"
        / "现实PTMG_xTB"
        / "聚合"
        / "逐构象描述符.csv",
        output_root=tmp_path,
        release_id="test-prereaction-complexes",
    )
    assert len(pairs) == 22
    assert pairs["pair_type"].value_counts().to_dict() == {
        "diisocyanate_macrodiol": 12,
        "diisocyanate_chain_extender": 10,
    }
    assert len(tasks) == 88
    assert tasks["task_slug"].is_unique
    assert tasks.groupby("pair_id").size().eq(4).all()
    assert np.allclose(tasks["initial_reactive_distance_a"], 2.7, atol=1e-12)
    assert tasks["attack_angle_deg"].eq(105.0).all()
    ready = tasks["geometry_status"].eq("ready")
    assert tasks.loc[ready, "initial_min_interfragment_distance_a"].gt(0.7).all()
    assert tasks.loc[~ready, "initial_min_interfragment_distance_a"].le(0.7).all()
    assert tasks.loc[ready, "execution_permission"].eq("allowed").all()
    assert tasks.loc[~ready, "execution_permission"].eq("blocked").all()
    assert tasks["monomer_energy_sum_hartree"].notna().all()
    assert release["counts"]["pairs"] == 22
    assert release["counts"]["tasks"] == 88
    assert (
        release["counts"]["ready_tasks"] + release["counts"]["blocked_tasks"]
        == 88
    )
    for row in tasks.itertuples(index=False):
        xyz = tmp_path / row.input_xyz_file
        control = tmp_path / row.xcontrol_file
        assert xyz.is_file() and control.is_file()
        assert complexes.sha256(xyz) == row.input_xyz_sha256
        assert complexes.sha256(control) == row.xcontrol_sha256
        text = control.read_text(encoding="utf-8")
        assert f"distance: {row.nco_carbon_atom_index_1based},{row.oh_oxygen_atom_index_1based},2.700000" in text


def test_real_release_is_byte_deterministic_across_two_builds(tmp_path):
    kwargs = {
        "subset_path": ROOT / "结果" / "现实筛选" / "高层DFT候选12.csv",
        "monomer_manifest_path": ROOT
        / "计算"
        / "现实预反应复合物"
        / "单体结构清单.csv",
        "monomer_root": ROOT / "计算" / "现实预反应复合物",
        "components_path": ROOT / "数据" / "现实库" / "构件.csv",
        "ptmg_models_path": ROOT / "数据" / "现实库" / "PTMG代表模型.csv",
        "discrete_results_path": ROOT
        / "计算"
        / "现实xTB系综"
        / "聚合"
        / "逐构象描述符.csv",
        "ptmg_results_path": ROOT
        / "计算"
        / "现实PTMG_xTB"
        / "聚合"
        / "逐构象描述符.csv",
        "release_id": "test-prereaction-complexes",
    }
    first, _, _ = complexes.build_release(output_root=tmp_path / "a", **kwargs)
    second, _, _ = complexes.build_release(output_root=tmp_path / "b", **kwargs)
    pd.testing.assert_frame_equal(first, second)
    for relative in first["input_xyz_file"].tolist() + first["xcontrol_file"].tolist():
        assert (tmp_path / "a" / relative).read_bytes() == (
            tmp_path / "b" / relative
        ).read_bytes()


def test_missing_component_hash_and_invalid_protocol_values_fail_closed(tmp_path):
    with pytest.raises(ValueError, match="距离"):
        complexes.validate_protocol(distance_a=1.0, attack_angle_deg=105.0)
    with pytest.raises(ValueError, match="角度"):
        complexes.validate_protocol(distance_a=2.7, attack_angle_deg=180.0)
    manifest = pd.read_csv(
        ROOT / "计算" / "现实预反应复合物" / "单体结构清单.csv"
    )
    manifest.loc[0, "published_xyz_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256"):
        complexes.load_monomer_geometries(
            manifest, ROOT / "计算" / "现实预反应复合物"
        )
