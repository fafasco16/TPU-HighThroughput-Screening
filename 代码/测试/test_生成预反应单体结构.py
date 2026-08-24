from pathlib import Path

import pandas as pd
import pytest

import 生成预反应单体结构 as monomers


def _write_xyz(path: Path, x: float) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"1\n0.0 fixture\nH {x} 0 0\n", encoding="utf-8")
    return monomers.sha256(path)


def test_lowest_successful_discrete_and_single_ptmg_inputs_are_materialized(tmp_path):
    discrete_root = tmp_path / "discrete"
    ptmg_root = tmp_path / "ptmg"
    h1 = _write_xyz(discrete_root / "输入构象" / "a1.xyz", 0.0)
    h2 = _write_xyz(discrete_root / "输入构象" / "a2.xyz", 1.0)
    hp = _write_xyz(ptmg_root / "输入构象" / "p.xyz", 2.0)
    discrete_results = pd.DataFrame(
        [
            {"candidate_id": "di-1", "component_role": "diisocyanate", "xtb_task_slug": "a1", "run_status": "success", "total_energy_hartree": -10.0},
            {"candidate_id": "di-1", "component_role": "diisocyanate", "xtb_task_slug": "a2", "run_status": "success", "total_energy_hartree": -11.0},
        ]
    )
    discrete_tasks = pd.DataFrame(
        [
            {"xtb_task_slug": "a1", "conformer_xyz_file": "输入构象/a1.xyz", "conformer_xyz_sha256": h1},
            {"xtb_task_slug": "a2", "conformer_xyz_file": "输入构象/a2.xyz", "conformer_xyz_sha256": h2},
        ]
    )
    ptmg_tasks = pd.DataFrame(
        [
            {
                "candidate_id": "macro-1",
                "component_role": "macrodiol_proxy",
                "xtb_task_slug": "p",
                "conformer_xyz_file": "输入构象/p.xyz",
                "conformer_xyz_sha256": hp,
                "nominal_mn_g_mol": 1000.0,
                "representation_scope": "single_oligomer_proxy_for_product_distribution",
            }
        ]
    )
    manifest = monomers.build_monomer_manifest(
        discrete_results, discrete_tasks, ptmg_tasks
    )
    assert manifest.set_index("candidate_id").loc["di-1", "xtb_task_slug"] == "a2"
    assert manifest.set_index("candidate_id").loc["di-1", "descriptor_fidelity"] == "crest_ensemble_lowest_xtb_energy"
    assert manifest.set_index("candidate_id").loc["macro-1", "descriptor_fidelity"] == "single_conformer_proxy"
    published = monomers.materialize_monomers(
        manifest,
        discrete_root,
        ptmg_root,
        tmp_path / "out",
    )
    assert len(published) == 2
    assert (tmp_path / "out" / "单体结构" / "di-1.xyz").read_bytes() == (
        discrete_root / "输入构象" / "a2.xyz"
    ).read_bytes()
    assert published["published_xyz_sha256"].eq(published["source_xyz_sha256"]).all()


def test_failed_duplicate_missing_and_hash_mismatch_inputs_close_release(tmp_path):
    results = pd.DataFrame(
        [
            {"candidate_id": "x", "component_role": "diisocyanate", "xtb_task_slug": "a", "run_status": "failed", "total_energy_hartree": -1.0}
        ]
    )
    tasks = pd.DataFrame(
        [{"xtb_task_slug": "a", "conformer_xyz_file": "输入构象/a.xyz", "conformer_xyz_sha256": "0" * 64}]
    )
    with pytest.raises(ValueError, match="没有成功的离散构件"):
        monomers.build_monomer_manifest(results, tasks, pd.DataFrame())
    duplicate = pd.concat([tasks, tasks], ignore_index=True)
    with pytest.raises(ValueError, match="xtb_task_slug不唯一"):
        monomers.build_monomer_manifest(
            results.assign(run_status="success"), duplicate, pd.DataFrame()
        )
    manifest = monomers.build_monomer_manifest(
        results.assign(run_status="success"), tasks, pd.DataFrame()
    )
    with pytest.raises(ValueError, match="不存在"):
        monomers.materialize_monomers(
            manifest, tmp_path / "d", tmp_path / "p", tmp_path / "out"
        )
    source = tmp_path / "d" / "输入构象" / "a.xyz"
    _write_xyz(source, 0.0)
    with pytest.raises(ValueError, match="SHA-256不一致"):
        monomers.materialize_monomers(
            manifest, tmp_path / "d", tmp_path / "p", tmp_path / "out2"
        )
