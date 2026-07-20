"""PUR-GEN 片段库的结构、权利状态与可复现性回归测试。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "代码" / "审计" / "第七批PURGEN虚拟片段.py"


def _load_module():
    audit_dir = str(SCRIPT.parent)
    if audit_dir not in sys.path:
        sys.path.insert(0, audit_dir)
    spec = importlib.util.spec_from_file_location("batch7_purgen_audit", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def audited():
    module = _load_module()
    if not module.ARCHIVE.is_file():
        pytest.skip("PUR-GEN官方归档未在当前检出中分发")
    fragments, candidates = module.build_fragment_rows()
    return module, fragments, candidates


def test_archive_identity_and_fragment_counts_are_frozen(audited) -> None:
    module, fragments, _ = audited
    assert module.ARCHIVE.stat().st_size == 752_485
    assert hashlib.sha256(module.ARCHIVE.read_bytes()).hexdigest() == (
        "965cf1d04b9b5358bf71beaddbb14ab43346acae8b80eb3baefe7e60cd452e24"
    )
    summary = module.summarize_fragments(fragments)
    assert summary["content"] == {
        "fragment_count": 414,
        "unique_canonical_smiles": 414,
        "unique_inchikey": 414,
        "unit_counts": {"2": 160, "3": 198, "4": 56},
        "descriptor_row_count": 360,
        "missing_descriptor_row_count": 54,
        "descriptor_structure_match_counts": {
            "achiral_match_mol2_has_inferred_stereochemistry": 136,
            "descriptor_row_missing": 54,
            "exact": 224,
        },
    }


def test_candidates_are_gold_v_reference_not_property_truth(audited) -> None:
    _, fragments, candidates = audited
    assert len(candidates) == len(fragments) == 414
    assert len({row["candidate_id"] for row in candidates}) == 414
    assert len({row["canonical_smiles"] for row in candidates}) == 414
    assert len({row["inchikey"] for row in candidates}) == 414
    assert Counter(row["gold_admission_status"] for row in candidates) == {
        "admitted_reference": 414
    }
    assert all(row["gold_layer"] == "Gold-V" for row in candidates)
    assert all(row["data_origin"] == "reaction_rule_generated" for row in candidates)
    assert all(row["direct_property_supervision_weight_ceiling"] == 0.0 for row in candidates)
    assert all(row["license_spdx"] == "" for row in candidates)


def test_materialized_audit_summary_matches_recomputed_content(audited) -> None:
    module, fragments, _ = audited
    materialized = json.loads(module.SUMMARY_PATH.read_text(encoding="utf-8"))
    assert materialized["content"] == module.summarize_fragments(fragments)["content"]
    assert materialized["admission"]["recommended_layer"] == (
        "Gold-V admitted scientific reference"
    )


def test_atomic_write_keeps_existing_file_if_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    target = tmp_path / "output.txt"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic replace failure"):
        module._atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob("*.tmp"))
