import pandas as pd
import pytest
from rdkit import Chem

import 生成现实MD三维种子 as seeds


def _graphs() -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {
                "formulation_id": "f-1",
                "canonical_smiles": "O=C=NCCCCCCNC(=O)OCCCCO",
                "atom_count": 0,
                "chemical_graph_status": "completed",
                "three_dimensional_status": "not_generated",
                "forcefield_status": "not_parameterized",
                "performance_claim_status": "no_performance_claim",
            },
            {
                "formulation_id": "f-2",
                "canonical_smiles": "O=C=NCCNC(=O)OCCO",
                "atom_count": 0,
                "chemical_graph_status": "completed",
                "three_dimensional_status": "not_generated",
                "forcefield_status": "not_parameterized",
                "performance_claim_status": "no_performance_claim",
            },
        ]
    )
    frame["atom_count"] = frame["canonical_smiles"].map(
        lambda value: Chem.AddHs(Chem.MolFromSmiles(value)).GetNumAtoms()
    )
    return frame


def test_seed_generation_is_deterministic_hashed_and_not_md_ready(tmp_path):
    first = seeds.generate_seed_table(_graphs(), tmp_path / "a")
    second = seeds.generate_seed_table(
        _graphs().sample(frac=1.0, random_state=2), tmp_path / "b"
    )
    first = first.sort_values("formulation_id").reset_index(drop=True)
    second = second.sort_values("formulation_id").reset_index(drop=True)
    comparable = [
        column
        for column in first.columns
        if column not in {"embedding_seconds", "mmff_seconds"}
    ]
    pd.testing.assert_frame_equal(first[comparable], second[comparable])
    assert first["geometry_status"].isin(
        ["mmff_converged_seed", "mmff_max_iterations_seed"]
    ).all()
    assert first["md_execution_status"].eq(
        "blocked_seed_only_forcefield_and_bulk_protocol_missing"
    ).all()
    for row in first.itertuples(index=False):
        assert seeds.sha256(tmp_path / "a" / row.xyz_file) == row.xyz_sha256
        assert (tmp_path / "a" / row.xyz_file).read_bytes() == (
            tmp_path / "b" / row.xyz_file
        ).read_bytes()


def test_invalid_graph_status_atom_count_and_claim_fail_closed(tmp_path):
    invalid = _graphs()
    invalid.loc[0, "chemical_graph_status"] = "failed"
    with pytest.raises(ValueError, match="化学图状态"):
        seeds.generate_seed_table(invalid, tmp_path / "a")
    mismatch = _graphs()
    mismatch.loc[0, "atom_count"] = 999
    with pytest.raises(ValueError, match="原子数"):
        seeds.generate_seed_table(mismatch, tmp_path / "b")
    claimed = _graphs()
    claimed.loc[0, "performance_claim_status"] = "high_performance"
    with pytest.raises(ValueError, match="性能宣称"):
        seeds.generate_seed_table(claimed, tmp_path / "c")


def test_writer_creates_manifest_and_seed_table(tmp_path):
    source = tmp_path / "graphs.csv.gz"
    _graphs().to_csv(
        source,
        index=False,
        compression={"method": "gzip", "mtime": 0},
    )
    manifest = seeds.write_release(
        source, tmp_path / "out", release_id="test-md-seeds"
    )
    assert manifest["counts"]["graphs"] == 2
    assert manifest["counts"]["embedded"] == 2
    assert (tmp_path / "out" / "三维种子清单.csv").is_file()
    assert (tmp_path / "out" / "三维种子发布清单.json").is_file()
