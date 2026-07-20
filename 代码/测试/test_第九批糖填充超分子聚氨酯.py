"""糖填充超分子聚氨酯 OPJ 数据的身份、解析和 Gold-E 回归门禁。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "代码" / "审计" / "第九批糖填充超分子聚氨酯.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("batch9_sugar_filled_spu", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def audited():
    module = _load_module()
    required = [
        module.SOURCE_DIR / "Mendeley_元数据_v1.json",
        module.SOURCE_DIR / "Mendeley_文件清单_v1.json",
        module.OFFICIAL_DIR,
        module.EXPORT_DIR,
    ]
    if not all(path.exists() for path in required):
        pytest.skip("第九批糖填充 SPU 原件或只读导出未在当前检出中分发")
    return module, module.audit()


def test_official_identity_license_and_hashes_are_frozen(audited) -> None:
    module, bundle = audited
    summary = bundle["summary"]
    assert summary["dataset"] == {
        "repository": "Mendeley Data",
        "dataset_id": "z4zy523b8c",
        "doi": "10.17632/z4zy523b8c.1",
        "article_doi": "10.1016/j.ijimpeng.2022.104239",
        "title": "Experimental characterisation and modelling of sugar-filled supramolecular polyurethane",
        "dataset_url": "https://data.mendeley.com/datasets/z4zy523b8c/1",
        "metadata_url": "https://data.mendeley.com/public-api/datasets/z4zy523b8c?version=1",
        "files_url": "https://data.mendeley.com/public-api/datasets/z4zy523b8c/files?folder_id=root&version=1",
        "license_spdx": "CC-BY-NC-3.0",
        "license_name": "Attribution-NonCommercial 3.0 Unported",
        "commercial_use_allowed": False,
    }

    rows = bundle["file_rows"]
    assert len(rows) == 28
    assert sum(int(row["bytes"]) for row in rows) == 6_600_228
    assert len({row["filename"] for row in rows}) == 28
    assert all(row["filename"].lower().endswith(".opj") for row in rows)
    assert all(row["local_integrity"] == "verified" for row in rows)
    for row in rows:
        path = module.OFFICIAL_DIR / row["filename"]
        assert path.is_file() and not path.is_symlink()
        assert path.stat().st_size == int(row["bytes"])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]


def test_readonly_export_parse_coverage_is_exact(audited) -> None:
    _, bundle = audited
    integrity = bundle["summary"]["integrity"]
    assert integrity == {
        "official_opj_file_count": 28,
        "official_total_bytes": 6_600_228,
        "official_sha256_verified_count": 28,
        "readonly_export_count": 28,
        "opj_parse_success_count": 28,
        "exported_sheet_count": 144,
        "spreadsheet_container_count": 12,
        "excel_container_count": 37,
        "origin_version_counts": {"9.4": 28},
    }
    parse_rows = bundle["parse_rows"]
    assert len(parse_rows) == 28
    assert all(int(row["parse_error"]) == 0 for row in parse_rows)
    assert sum(int(row["exported_sheet_count"]) for row in parse_rows) == 144
    assert sum(int(row["plotted_graph_curve_count"]) for row in parse_rows) == 331
    assert all(len(str(row["readonly_export_sha256"])) == 64 for row in parse_rows)
    assert {row["parser_commit"] for row in parse_rows} == {
        "f5457c4e2ae9d3b0783dcb3a408ecee3cf7f1c4e"
    }


def test_nine_material_conditions_not_twenty_eight_files(audited) -> None:
    _, bundle = audited
    rows = {row["material_id"]: row for row in bundle["material_rows"]}
    assert set(rows) == {"SPU", "G25", "G50", "G70", "G75", "C50", "C65", "I30", "I50"}
    assert len(rows) == 9
    assert all(row["independent_material_condition"] == "true" for row in rows.values())
    assert all(row["exact_polymer_smiles"] == "" for row in rows.values())
    assert all(
        row["split_group"] == "spu_sugar_composite_z4zy523b8c_v1"
        for row in rows.values()
    )

    expected = {
        "SPU": ("none", "", "", 0.00, "", "", ""),
        "G25": ("granulated sugar", 530, 670, 0.25, 3.2, 0.055, 0.40),
        "G50": ("granulated sugar", 530, 670, 0.50, 8.5, 0.048, 0.68),
        "G70": ("granulated sugar", 530, 670, 0.70, 14.9, 0.043, 0.93),
        "G75": ("granulated sugar", 530, 670, 0.75, 16.8, 0.042, 0.99),
        "C50": ("caster sugar", 270, 340, 0.50, 8.5, 0.054, 0.61),
        "C65": ("caster sugar", 270, 340, 0.65, 13.0, 0.051, 0.78),
        "I30": ("icing sugar", 20, 25, 0.30, 4.0, 0.065, 0.31),
        "I50": ("icing sugar", 20, 25, 0.50, 8.5, 0.060, 0.54),
    }
    for material_id, values in expected.items():
        row = rows[material_id]
        actual = (
            row["filler_material"],
            row["filler_particle_size_min_um"],
            row["filler_particle_size_max_um"],
            row["filler_volume_fraction"],
            row["guth_reinforcement_factor"],
            row["damage_activation_strain_epsilon_a"],
            row["damage_residual_strength_k"],
        )
        assert actual == values


def test_curve_recovery_deduplication_and_mapping_are_frozen(audited) -> None:
    _, bundle = audited
    rows = bundle["curve_rows"]
    counts = bundle["summary"]["scientific_counts"]
    assert len(rows) == counts["total_graph_curve_references"] == 331
    assert Counter(row["resolution_status"] for row in rows) == {
        "resolved": 303,
        "insufficient_finite_pairs": 28,
    }
    assert counts["resolved_graph_curve_references"] == 303
    assert counts["resolved_curve_points_total"] == 115_013
    assert counts["unique_resolved_curve_payloads"] == 207
    assert counts["duplicate_curve_references"] == 96
    assert counts["exact_sheet_payload_duplicate_groups"] == [
        ["Fig11a.opj", "Fig11b.opj", "Fig11c.opj"]
    ]
    assert counts["curve_mapping_counts"] == {
        "explicit_internal_label": 13,
        "multi_material_scope_unresolved": 147,
        "publication_book_context": 7,
        "publication_context_internal_label_conflict": 1,
        "publication_single_material_context": 163,
    }
    assert counts["curve_admission_counts"] == {
        "admitted_reference": 87,
        "conditional_reference": 244,
    }
    assert len({row["curve_sha256"] for row in rows if row["curve_sha256"]}) == 207
    assert all(
        row["curve_points_are_independent_material_samples"] == "false" for row in rows
    )


def test_computational_and_experimental_curves_keep_distinct_gold_layers(
    audited,
) -> None:
    _, bundle = audited
    rows = bundle["curve_rows"]
    summary = bundle["summary"]
    assert summary["gold_recommendation"] == {
        "layer": "Gold-E+Gold-C",
        "status": "admitted_multifidelity_reference",
        "reason": "公开原始 OPJ、逐文件 SHA-256、论文方法与图层曲线引用均可核验；实验与连续体模型分层保留。",
        "experimental_curve_weight_ceiling": 0.75,
        "published_model_curve_weight_ceiling": 0.25,
        "unresolved_or_duplicate_weight_ceiling": 0.0,
        "structure_only_model_use": "blocked_no_exact_polymer_smiles",
        "recommended_use": "家族/配方/填料/工况条件化的压缩曲线、多保真校准、外部验证；按整个 SPU 糖填充家族分组切分。",
    }
    assert Counter(row["data_origin"] for row in rows) == {
        "experimental_derived_scalar": 37,
        "experimental_processed_curve": 62,
        "mixed_published_experiment_model": 77,
        "published_continuum_model_curve": 149,
        "published_reinforcement_model_curve": 6,
    }
    assert Counter(row["gold_layer"] for row in rows) == {
        "Gold-E": 99,
        "Gold-C": 155,
        "Gold-E+Gold-C": 77,
    }
    assert summary["scientific_counts"]["curve_gold_layer_counts"] == {
        "Gold-C": 155,
        "Gold-E": 99,
        "Gold-E+Gold-C": 77,
    }
    assert Counter(row["future_weight_ceiling"] for row in rows) == {
        "0.00": 217,
        "0.10": 27,
        "0.25": 47,
        "0.50": 16,
        "0.75": 24,
    }
    for row in rows:
        ceiling = float(row["future_weight_ceiling"])
        assert row["split_group"] == "spu_sugar_composite_z4zy523b8c_v1"
        if row["data_origin"].startswith("published_"):
            assert row["gold_layer"] == "Gold-C"
            assert ceiling <= 0.25
            if ceiling > 0:
                assert row["gold_admission_status"] == "admitted_reference"
        if row["data_origin"].startswith("experimental_"):
            assert row["gold_layer"] == "Gold-E"
            assert ceiling <= 0.75
        if row["data_origin"] == "mixed_published_experiment_model":
            assert row["gold_layer"] == "Gold-E+Gold-C"
        if row["duplicate_of_curve_id"] or row["resolution_status"] != "resolved":
            assert ceiling == 0.0
            assert row["gold_admission_status"] == "conditional_reference"
        assert row["gold_admission_status"] in {
            "admitted_reference",
            "conditional_reference",
        }


def test_rendering_and_atomic_main_are_idempotent(
    audited, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, bundle = audited
    first = module.render_outputs(bundle)
    second = module.render_outputs(module.audit())
    assert first == second
    assert set(first) == set(module.OUTPUT_NAMES)

    monkeypatch.setattr(module, "OUTPUT_DIR", tmp_path)
    module.main()
    first_hashes = {
        name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
        for name in module.OUTPUT_NAMES
    }
    module.main()
    second_hashes = {
        name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
        for name in module.OUTPUT_NAMES
    }
    assert first_hashes == second_hashes
    assert not list(tmp_path.glob("*.tmp"))
    materialized = json.loads(
        (tmp_path / "内容审计摘要.json").read_text(encoding="utf-8")
    )
    assert materialized == bundle["summary"]
