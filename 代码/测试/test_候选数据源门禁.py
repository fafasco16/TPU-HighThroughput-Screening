from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "代码" / "审计" / "候选数据源门禁.py"
DOWNLOADER = ROOT / "代码" / "获取" / "获取候选数据.py"
REGISTRY = ROOT / "配置" / "候选数据源.yaml"
SOURCE_SCOPE = ROOT / "配置" / "v0.2来源范围.yaml"


def _load_gate():
    spec = importlib.util.spec_from_file_location("fifth_batch_candidate_gate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_downloader():
    spec = importlib.util.spec_from_file_location(
        "fifth_batch_candidate_downloader", DOWNLOADER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_candidate_registry_is_candidate_only_and_training_stays_closed() -> None:
    payload = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "v0.2"
    assert payload["registry_version"].startswith("candidate-registry-")
    assert payload["registry_state"] == "candidate_only"
    assert payload["training_split_created"] is False
    assert payload["training_weight_materialized"] is False
    assert payload["model_ready_record_count"] == 0
    assert payload["candidates"]


def test_gate_uses_full_governance_config_not_only_49_source_ledger() -> None:
    gate = _load_gate()
    source_payload = yaml.safe_load(SOURCE_SCOPE.read_text(encoding="utf-8"))
    index = gate.build_existing_identifier_index(source_payload)

    # 4TU 与两个 Zenodo 来源已在全量来源治理配置中，但不一定都出现在当前
    # 49 个独立贡献口径；候选门禁必须仍能判重。
    for identifier in (
        "doi:10.4121/13603775.v1",
        "doi:10.5281/zenodo.1098206",
        "doi:10.5281/zenodo.4156000",
    ):
        assert gate.normalize_identifier(identifier) in index


def test_gate_recomputes_scores_grades_dedup_and_download_eligibility() -> None:
    gate = _load_gate()
    report = gate.evaluate_registry(REGISTRY, SOURCE_SCOPE)

    assert report["valid"] is True
    assert report["candidate_count"] >= 1
    assert report["error_count"] == 0
    assert report["model_ready_record_count"] == 0
    assert report["training_split_created"] is False
    assert report["training_weight_materialized"] is False

    rows = report["candidates"]
    assert [row["candidate_id"] for row in rows] == sorted(
        row["candidate_id"] for row in rows
    )
    for row in rows:
        assert row["score_total"] == sum(row["scores"].values())
        expected_grade = "A" if row["score_total"] >= 28 else "B" if row["score_total"] >= 21 else "C"
        assert row["grade"] == expected_grade
        if row["download_eligible"]:
            assert row["grade"] == "A"
            assert row["dedup_state"] == "new"
            assert row["vetoes"] == []
            assert row["rights_status"] in {
                "open_redistributable",
                "open_noncommercial",
            }


def test_gate_fails_closed_for_an_unmarked_existing_source(tmp_path: Path) -> None:
    gate = _load_gate()
    payload = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    candidate = json.loads(json.dumps(payload["candidates"][0]))
    candidate["candidate_id"] = "fixture_unmarked_existing_4tu"
    candidate["canonical_identifier"] = "doi:10.4121/13603775.v1"
    candidate["dedup"] = {
        "state": "new",
        "matched_source_keys": [],
        "matched_scope_keys": [],
        "independent_source_contribution": True,
    }
    payload["candidates"] = [candidate]
    fixture = tmp_path / "候选.yaml"
    fixture.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    report = gate.evaluate_registry(fixture, SOURCE_SCOPE)
    assert report["valid"] is False
    assert report["error_count"] >= 1
    assert any(
        "existing_governance_source" in error for error in report["errors"]
    )


def test_gate_cli_output_is_byte_deterministic() -> None:
    command = [sys.executable, str(SCRIPT), "--json"]
    first = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    second = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert first.stdout == second.stdout
    report = json.loads(first.stdout)
    assert report["valid"] is True
    assert report["candidate_count"] == len(report["candidates"])
    assert first.stderr == second.stderr == ""


def test_downloader_rejects_non_https_unknown_hosts_and_fragments() -> None:
    downloader = _load_downloader()

    assert downloader.validate_url(
        "https://zenodo.org/api/records/3631551"
    ) == "zenodo.org"
    for bad_url in (
        "http://zenodo.org/api/records/3631551",
        "https://example.com/data.xlsx",
        "https://user:secret@zenodo.org/data.xlsx",
        "https://zenodo.org/data.xlsx#fragment",
    ):
        with pytest.raises(downloader.AcquisitionBlocked):
            downloader.validate_url(bad_url)


def test_downloader_only_selects_gate_approved_files_with_frozen_sha256() -> None:
    downloader = _load_downloader()
    selection = downloader.load_download_selection(REGISTRY, SOURCE_SCOPE)

    # 四个已下载A级候选均已晋升来源治理，不应重复下载。
    assert selection == {}
    assert downloader.CANDIDATE_DIRECTORIES[
        "mendeley_2026_iir_oh_low_permeability_pu"
    ] == "第十八批实验_IIR-OH聚氨酯"
    payload = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    promoted = {
        row["candidate_id"]: row
        for row in payload["candidates"]
        if row["candidate_id"]
        in {
            "fisher_2020_pu_shape_memory_raw",
            "zenodo_3631551_lignin_tpu_blends",
            "mendeley_2026_iir_oh_low_permeability_pu",
            "mendeley_2024_aged_vegetable_puf_simulation",
        }
    }
    assert set(promoted) == {
        "fisher_2020_pu_shape_memory_raw",
        "zenodo_3631551_lignin_tpu_blends",
        "mendeley_2026_iir_oh_low_permeability_pu",
        "mendeley_2024_aged_vegetable_puf_simulation",
    }
    assert sum(len(item["files"]) for item in promoted.values()) == 10
    assert all(
        item["dedup"]["state"] == "existing_governance_source"
        and item["dedup"]["independent_source_contribution"] is False
        for item in promoted.values()
    )
    for item in promoted.values():
        for file_spec in item["files"]:
            digest = file_spec["checksum"].removeprefix("sha256:")
            assert int(file_spec["size_bytes"]) > 0
            assert len(digest) == 64
            assert all(character in "0123456789abcdef" for character in digest)
            assert downloader.validate_url(file_spec["download_url"]) in downloader.ALLOWED_HOSTS
