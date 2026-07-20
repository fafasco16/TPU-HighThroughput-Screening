"""糖填充超分子聚氨酯第九批来源、资产、画像与引用的集成回归门禁。"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import yaml

from asset_registry import classify_path, load_asset_rules


ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "数据" / "原始"
SOURCE_DIR = (
    RAW_ROOT
    / "外部数据"
    / "新增开放数据"
    / "第九批实验_糖填充超分子聚氨酯"
)
SOURCE_CONFIG = ROOT / "配置" / "v0.2来源范围.yaml"
ASSET_CONFIG = ROOT / "配置" / "v0.2资产登记规则.yaml"
PROFILE_CONFIG = ROOT / "配置" / "v0.2可训练样本总账来源画像.yaml"
REFERENCE_DOC = ROOT / "文档" / "数据来源与参考文献.md"
GOLD_DOC = ROOT / "文档" / "Gold数据集定义.md"

DATASET_REFERENCE = (
    "Chen, H.; Hart, L. R.; Hayes, W.; Siviour, C. R. Experimental "
    "Characterisation and Modelling of Sugar-Filled Supramolecular "
    "Polyurethane [Data set], version 1; Mendeley Data, 2022. "
    "https://doi.org/10.17632/z4zy523b8c.1. CC BY-NC 3.0."
)
ARTICLE_REFERENCE = (
    "Chen, H.; Hart, L. R.; Hayes, W.; Siviour, C. R. Experimental "
    "Characterisation and Modelling of the Strain Rate Dependent Mechanical "
    "Response of a Filled Thermo-Reversible Supramolecular Polyurethane. "
    "International Journal of Impact Engineering 2022, 166, 104239. "
    "https://doi.org/10.1016/j.ijimpeng.2022.104239."
)
POLYOMICS_REFERENCE = (
    "Hayashi, Y. PolyOmics [Data set]; Hugging Face, 2026. "
    "https://doi.org/10.57967/hf/7475. CC BY 4.0."
)


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def test_source_scope_mapping_relations_and_formal_references_are_closed() -> None:
    config = _yaml(SOURCE_CONFIG)
    sources = {row["source_key"]: row for row in config["sources"]}
    scopes = {row["source_scope_key"]: row for row in config["scopes"]}
    mappings = {row["mapping_id"]: row for row in config["path_mappings"]}
    relations = {row["relation_key"]: row for row in config["relations"]}
    citations = {row["ledger_number"]: row for row in config["citations"]}

    dataset = sources["source_mendeley_z4zy523b8c_v1"]
    article = sources["ledger_source_150"]
    assert dataset["canonical_identifier"] == "10.17632/z4zy523b8c.1"
    assert dataset["version_label"] == "v1"
    assert article["canonical_identifier"] == "10.1016/j.ijimpeng.2022.104239"

    dataset_scope = scopes["scope_mendeley_z4zy523b8c_v1"]
    assert dataset_scope["source_key"] == dataset["source_key"]
    assert dataset_scope["canonical_identifier"] == "doi:10.17632/z4zy523b8c.1"
    assert dataset_scope["rights_evidence_state"] == "verified"
    assert dataset_scope["rights_candidate_status"] == "manual_review"
    assert scopes["scope_chen_2022_sugar_filled_spu_publication"]["source_key"] == (
        article["source_key"]
    )
    assert mappings["ninth_batch_sugar_filled_spu"] == {
        "mapping_id": "ninth_batch_sugar_filled_spu",
        "priority": 1800,
        "match_type": "prefix",
        "pattern": "外部数据/新增开放数据/第九批实验_糖填充超分子聚氨酯/",
        "source_scope_key": "scope_mendeley_z4zy523b8c_v1",
    }
    relation = relations["rel_mendeley_z4zy523b8c_sugar_filled_spu_data"]
    assert relation["subject_scope_key"] == "scope_mendeley_z4zy523b8c_v1"
    assert relation["object_scope_key"] == (
        "scope_chen_2022_sugar_filled_spu_publication"
    )
    assert relation["relation_type"] == "supplement_to"

    assert citations[149]["reference_text"] == DATASET_REFERENCE
    assert citations[150]["reference_text"] == ARTICLE_REFERENCE
    assert citations[151]["reference_text"] == POLYOMICS_REFERENCE
    assert citations[149]["target_scope_key"] == "scope_mendeley_z4zy523b8c_v1"
    assert citations[150]["target_scope_key"] == (
        "scope_chen_2022_sugar_filled_spu_publication"
    )
    assert citations[151]["target_scope_key"] == "polyomics_general_version"

    markdown = REFERENCE_DOC.read_text(encoding="utf-8")
    assert f"[149] {DATASET_REFERENCE}" in markdown
    assert f"[150] {ARTICLE_REFERENCE}" in markdown
    assert f"[151] {POLYOMICS_REFERENCE}" in markdown
    assert "`第九批计算_PolyOmics` | [20], [151]" in markdown


def test_every_local_asset_is_governed_and_noncommercial_release_is_denied() -> None:
    config = load_asset_rules(ASSET_CONFIG)
    files = sorted(path for path in SOURCE_DIR.rglob("*") if path.is_file())
    assert len(files) == 70

    selected = []
    for path in files:
        relative = path.relative_to(RAW_ROOT).as_posix()
        selected.append(classify_path(relative, config))
    assert {row.source_scope_key for row in selected} == {
        "scope_mendeley_z4zy523b8c_v1"
    }
    assert Counter(row.rule_id for row in selected) == {
        "ninth_batch_sugar_filled_spu_official_opj": 28,
        "ninth_batch_sugar_filled_spu_readonly_exports": 28,
        "ninth_batch_sugar_filled_spu_source_evidence": 8,
        "ninth_batch_sugar_filled_spu_audit_outputs": 6,
    }
    assert all(row.release_status == "denied" for row in selected)
    assert all(row.scientific_admission_status == "admitted" for row in selected)
    assert {
        row.artifact_role for row in selected
    } == {"primary_data", "subset_view", "documentation"}


def test_profile_uses_material_not_file_counts_and_manual_review_for_by_nc() -> None:
    config = _yaml(PROFILE_CONFIG)
    profiles = {row["source_directory"]: row for row in config["profiles"]}
    profile = profiles["第九批实验_糖填充超分子聚氨酯"]
    assert profile["origin_kind"] == "混合"
    assert profile["reference_admission_status"] == "admitted_reference"
    assert profile["weight_ceiling"] == 0.75
    assert profile["license_status"] == "manual_review"
    assert profile["counts"] == {
        "source_record_count": 331,
        "material_count": 9,
        "formulation_count": 9,
        "batch_count": None,
        "specimen_count": None,
        "run_count": None,
        "curve_count_observed": 331,
        "curve_count_candidate": 331,
        "scalar_count_observed": 0,
        "scalar_count_candidate": 0,
        "point_count_observed": 115_013,
        "point_count_candidate": 115_013,
        "numeric_value_count": 230_026,
        "evidence_group_count": 0,
        "computational_system_count": None,
        "source_identity_count_contribution": 1,
    }
    notes = profile["notes"]
    assert "99条Gold-E" in notes and "155条Gold-C" in notes
    assert "77条Gold-E+Gold-C" in notes
    assert "纯SMILES结构监督权重为0" in notes
    assert "CC BY-NC 3.0" in notes and "manual_review" in notes
    assert "28个文件和144个导出工作表不是材料" in notes


def test_record_level_gold_layers_admission_and_weight_caps_match_audit() -> None:
    summary = json.loads(
        (SOURCE_DIR / "内容审计摘要.json").read_text(encoding="utf-8")
    )
    rows = _tsv(SOURCE_DIR / "曲线审计清单.tsv")
    materials = _tsv(SOURCE_DIR / "材料条件清单.tsv")

    assert len(materials) == 9
    assert all(row["exact_polymer_smiles"] == "" for row in materials)
    assert len(rows) == 331
    assert summary["scientific_counts"]["resolved_graph_curve_references"] == 303
    assert summary["scientific_counts"]["unique_resolved_curve_payloads"] == 207
    assert summary["scientific_counts"]["resolved_curve_points_total"] == 115_013
    assert summary["scientific_counts"]["curve_admission_counts"] == {
        "admitted_reference": 87,
        "conditional_reference": 244,
    }
    assert Counter(row["gold_layer"] for row in rows) == {
        "Gold-E": 99,
        "Gold-C": 155,
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
        if row["gold_layer"] == "Gold-C":
            assert ceiling <= 0.25
        elif row["gold_layer"] == "Gold-E":
            assert ceiling <= 0.75
        else:
            assert row["gold_layer"] == "Gold-E+Gold-C"
            assert ceiling <= 0.10
        assert row["gold_admission_status"] in {
            "admitted_reference",
            "conditional_reference",
        }

    gold_definition = GOLD_DOC.read_text(encoding="utf-8")
    assert "99条 `Gold-E`" in gold_definition
    assert "155条 `Gold-C`" in gold_definition
    assert "CC BY-NC 3.0" in gold_definition
    assert "纯SMILES结构任务权重为0" in gold_definition
