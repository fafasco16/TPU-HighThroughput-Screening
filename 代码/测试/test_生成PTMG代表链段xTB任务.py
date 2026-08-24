from pathlib import Path

import pandas as pd
import pytest

import 生成PTMG代表链段xTB任务 as ptmg


ROOT = Path(__file__).resolve().parents[2]
XTB_SHA256 = "debf27a9e0fa4bfb5ca75aafe4b90d8211f08ec2f4a482f375a4987212eaa12a"


def test_real_release_builds_exactly_five_hashed_single_proxy_tasks(tmp_path):
    tasks, sources, manifest = ptmg.build_release(
        pd.read_csv(ROOT / "数据" / "现实库" / "PTMG代表模型.csv"),
        pd.read_csv(ROOT / "计算" / "现实构件" / "量化任务.csv"),
        ROOT / "计算" / "现实构件",
        tmp_path,
        release_id="tpu-reality-ptmg-xtb-proxy-test-v1",
        xtb_version="6.7.1",
        xtb_binary_sha256=XTB_SHA256,
        expected_count=5,
    )
    assert len(tasks) == len(sources) == 5
    assert tasks["candidate_id"].is_unique
    assert tasks["conformer_id"].is_unique
    assert tasks["xtb_task_slug"].is_unique
    assert set(tasks["component_role"]) == {"macrodiol_proxy"}
    assert set(tasks["representation_scope"]) == {
        "single_oligomer_proxy_for_product_distribution"
    }
    assert set(tasks["selection_status"]) == {
        "selected_single_representative_proxy"
    }
    assert set(tasks["xtb_version"].astype(str)) == {"6.7.1"}
    assert set(tasks["xtb_binary_sha256"]) == {XTB_SHA256}
    assert manifest["counts"] == {"components": 5, "conformer_tasks": 5}
    for row in tasks.itertuples(index=False):
        path = tmp_path / row.conformer_xyz_file
        assert path.is_file()
        assert ptmg.sha256(path) == row.conformer_xyz_sha256
        lines = path.read_text(encoding="utf-8").splitlines()
        assert int(lines[0]) == row.atom_count
        assert lines[1].startswith("0.000000000000 ")


def test_preoptimized_geometry_is_preferred_and_blank_comment_is_normalized(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    initial = source_root / "initial.xyz"
    preoptimized = source_root / "preopt.xyz"
    initial.write_text("3\n\nO 0 0 0\nH 0 0 1\nH 0 1 0\n", encoding="utf-8")
    preoptimized.write_text(
        "3\n energy: -1.0\nO 1 0 0\nH 1 0 1\nH 1 1 0\n",
        encoding="utf-8",
    )
    models = pd.DataFrame(
        [
            {
                "component_id": "commercial_ptmg_test",
                "nominal_mn_g_mol": 1000.0,
                "repeat_count": 14,
                "representative_smiles": "O",
                "approximation_status": "single_oligomer_proxy_for_product_distribution",
                "distribution_claim_status": "no_distribution_claim",
            }
        ]
    )
    quantum = pd.DataFrame(
        [
            {
                "task_index": 14,
                "candidate_id": "commercial_ptmg_test",
                "component_role": "macrodiol_representative",
                "task_slug": "reality_commercial_ptmg_test",
                "charge": 0,
                "uhf": 0,
                "geometry_status": "ready_after_gfnff_preoptimization",
                "initial_xyz_file": "initial.xyz",
                "initial_xyz_sha256": ptmg.sha256(initial),
                "preoptimization_status": "completed",
                "preoptimization_method": "GFN-FF",
                "preoptimized_xyz_file": "preopt.xyz",
                "preoptimized_xyz_sha256": ptmg.sha256(preoptimized),
            }
        ]
    )
    tasks, _, _ = ptmg.build_release(
        models,
        quantum,
        source_root,
        tmp_path / "out",
        release_id="test-release",
        xtb_version="6.7.1",
        xtb_binary_sha256=XTB_SHA256,
        expected_count=1,
    )
    row = tasks.iloc[0]
    assert row["source_geometry_method"] == "GFN-FF"
    assert row["source_geometry_sha256"] == ptmg.sha256(preoptimized)
    output = (tmp_path / "out" / row["conformer_xyz_file"]).read_text(
        encoding="utf-8"
    )
    assert "O 1 0 0" in output
    assert "O 0 0 0" not in output


@pytest.mark.parametrize(
    ("version", "binary_hash", "message"),
    [
        ("6.6.0", XTB_SHA256, "xTB版本"),
        ("6.7.1", "ABC", "SHA-256"),
    ],
)
def test_release_identity_is_fail_closed(version, binary_hash, message):
    with pytest.raises(ValueError, match=message):
        ptmg.validate_release_identity(version, binary_hash)


def test_source_hash_mismatch_and_missing_component_close_the_release(tmp_path):
    xyz = tmp_path / "one.xyz"
    xyz.write_text("1\n\nO 0 0 0\n", encoding="utf-8")
    models = pd.DataFrame(
        [
            {
                "component_id": "commercial_ptmg_missing",
                "nominal_mn_g_mol": 650.0,
                "repeat_count": 9,
                "representative_smiles": "O",
                "approximation_status": "single_oligomer_proxy_for_product_distribution",
                "distribution_claim_status": "no_distribution_claim",
            }
        ]
    )
    quantum = pd.DataFrame(
        [
            {
                "task_index": 18,
                "candidate_id": "commercial_ptmg_missing",
                "component_role": "macrodiol_representative",
                "task_slug": "reality_commercial_ptmg_missing",
                "charge": 0,
                "uhf": 0,
                "geometry_status": "ready",
                "initial_xyz_file": "one.xyz",
                "initial_xyz_sha256": "0" * 64,
                "preoptimization_status": "not_required",
            }
        ]
    )
    with pytest.raises(ValueError, match="输入SHA-256不一致"):
        ptmg.build_release(
            models,
            quantum,
            tmp_path,
            tmp_path / "out",
            release_id="test-release",
            xtb_version="6.7.1",
            xtb_binary_sha256=XTB_SHA256,
            expected_count=1,
        )
    with pytest.raises(ValueError, match="构件集合不一致"):
        ptmg.build_release(
            models,
            quantum.iloc[0:0],
            tmp_path,
            tmp_path / "out2",
            release_id="test-release",
            xtb_version="6.7.1",
            xtb_binary_sha256=XTB_SHA256,
            expected_count=1,
        )
