from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "生成短名单新颖性初筛.py"
SPEC = importlib.util.spec_from_file_location("novelty_prescreen", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_novelty_records_cover_six_unique_candidates() -> None:
    records = pd.DataFrame(MODULE.NOVELTY_RECORDS)
    assert len(records) == 6
    assert records["formulation_id"].is_unique
    assert records["primary_evidence_url"].str.startswith("http").all()


def test_no_record_grants_novelty_claim_permission() -> None:
    records = pd.DataFrame(MODULE.NOVELTY_RECORDS)
    assert not records["novelty_screen_status"].astype(str).str.contains(
        "confirmed_novel"
    ).any()
