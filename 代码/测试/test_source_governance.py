from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb

from contract import load_contract_bundle


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "结构定义" / "v0.2来源治理合同.yaml"
ENUM_PATH = ROOT / "结构定义" / "v0.2枚举.yaml"
RULE_PATH = ROOT / "结构定义" / "v0.2质量规则.yaml"
HAS_CJK = re.compile(r"[\u3400-\u9fff]")

REQUIRED_TABLES = {
    "content_blob",
    "source",
    "source_family",
    "source_family_membership",
    "source_scope",
    "source_scope_relation",
    "source_file",
    "source_locator",
    "asset_decision",
    "count_assertion",
    "citation",
    "citation_assignment",
    "rights_evidence_package",
    "rights_fact",
    "context_profile",
    "rights_action_decision",
    "rights_decision_evidence",
    "record_registry",
    "record_revision",
    "record_status_event",
    "record_source",
    "snapshot_record_status",
    "transformation_run",
    "transformation_input",
    "transformation_output",
    "record_lineage",
    "exclusion_record",
    "dataset_snapshot",
    "snapshot_record",
    "equivalence_group",
    "equivalence_membership",
}

LIFECYCLE_ENUMS = {
    "registration_status": ["discovered", "registered", "excluded_with_evidence"],
    "availability_status": [
        "available",
        "metadata_only",
        "request_required",
        "unreachable",
        "withdrawn",
    ],
    "parse_status": [
        "not_attempted",
        "parsed",
        "partially_parsed",
        "failed",
        "not_applicable",
    ],
    "scientific_admission_status": [
        "pending",
        "admitted",
        "admitted_with_waiver",
        "rejected",
    ],
    "model_readiness_status": [
        "not_assessed",
        "eligible",
        "held_out_only",
        "ineligible",
        "blocked",
    ],
    "release_status": [
        "not_assessed",
        "approved",
        "denied",
        "expired",
        "superseded",
    ],
}


def _bundle():
    return load_contract_bundle(SCHEMA_PATH, ENUM_PATH, RULE_PATH)


def _foreign_key(table: dict, name: str) -> dict:
    return next(item for item in table["foreign_keys"] if item["name"] == name)


def _unique_fields(table: dict, name: str) -> list[str]:
    return next(
        item["fields"] for item in table["unique_constraints"] if item["name"] == name
    )


def test_real_source_governance_contract_loads_and_has_complete_table_set():
    bundle = _bundle()

    assert bundle.schema_version == "v0.2"
    assert set(bundle.schema["tables"]) == REQUIRED_TABLES
    assert len(bundle.schema["tables"]) == 31
    assert all(len(value) == 64 for value in bundle.document_hashes.values())


def test_every_field_and_constraint_is_explicit_and_documented():
    tables = _bundle().schema["tables"]

    for table_name, table in tables.items():
        assert table["description"].strip() == table["description"], table_name
        assert table["description"], table_name
        assert HAS_CJK.search(table["description"]), table_name
        assert table["revision_policy"], table_name
        assert isinstance(table["primary_key"], list) and table["primary_key"], table_name
        assert isinstance(table["unique_constraints"], list), table_name
        assert isinstance(table["foreign_keys"], list), table_name
        assert isinstance(table["checks"], list) and table["checks"], table_name

        for field_name, field in table["fields"].items():
            assert field["description"].strip() == field["description"], (
                table_name,
                field_name,
            )
            assert field["description"], (table_name, field_name)
            assert HAS_CJK.search(field["description"]), (table_name, field_name)
            assert type(field["required"]) is bool, (table_name, field_name)
            assert field["arrow_type"], (table_name, field_name)
            assert field["duckdb_type"], (table_name, field_name)

        for field_name in table["primary_key"]:
            assert table["fields"][field_name]["required"] is True, (
                table_name,
                field_name,
            )

        constraint_names = [
            item["name"]
            for collection in ("unique_constraints", "foreign_keys", "checks")
            for item in table[collection]
        ]
        assert len(constraint_names) == len(set(constraint_names)), table_name
        for foreign_key in table["foreign_keys"]:
            assert foreign_key["cardinality"] in {"many_to_one", "one_to_one"}
            assert foreign_key["on_delete"] in {
                "restrict",
                "cascade",
                "set_null",
                "no_action",
            }


def test_all_check_expressions_parse_and_bind_in_duckdb():
    tables = _bundle().schema["tables"]
    connection = duckdb.connect(":memory:")
    try:
        for table_name, table in tables.items():
            columns = [
                f'"{field_name}" {field["duckdb_type"]}'
                for field_name, field in table["fields"].items()
            ]
            checks = [
                f'CONSTRAINT "{item["name"]}" CHECK ({item["expression"]})'
                for item in table["checks"]
            ]
            ddl = (
                f'CREATE TEMP TABLE "check_{table_name}" ('
                + ", ".join(columns + checks)
                + ")"
            )
            connection.execute(ddl)
    finally:
        connection.close()


def test_lifecycle_enums_and_asset_lifecycle_fields_are_exact():
    bundle = _bundle()
    enums = bundle.enums["enums"]
    tables = bundle.schema["tables"]
    source_file_fields = tables["source_file"]["fields"]
    snapshot_status_fields = tables["snapshot_record_status"]["fields"]

    for enum_name, expected_values in LIFECYCLE_ENUMS.items():
        assert enums[enum_name] == expected_values
        assert snapshot_status_fields[enum_name]["enum"] == enum_name
        assert snapshot_status_fields[enum_name]["required"] is True
        assert snapshot_status_fields[f"{enum_name}_event_id"]["required"] is True
        for physical_table in ("record_registry", "source", "source_scope", "source_file"):
            assert enum_name not in tables[physical_table]["fields"]

    # The six observation axes are frozen for the later observation contract,
    # but asset classification must not make any of them file-level truth.
    assert enums["origin_kind"] == [
        "experimental",
        "dft",
        "md",
        "coarse_grained_md",
        "group_contribution",
        "ml_prediction",
    ]
    assert enums["reduction_level"] == [
        "raw_point",
        "measurement",
        "replicate",
        "aggregate",
        "derived",
    ]
    assert enums["acquisition_method"] == [
        "direct_table",
        "structured_workbook",
        "repository_file",
        "pdf_table",
        "docx_table",
        "figure_digitization",
        "author_communication",
        "project_recalculation",
    ]
    assert enums["evidence_quality"] == ["Q1", "Q2", "Q3", "Q4", "Q5"]
    assert enums["rights_evidence_state"] == [
        "unreviewed",
        "evidence_missing",
        "captured_unverified",
        "scope_unresolved",
        "conflict_detected",
        "verified",
        "stale",
        "withdrawn",
    ]
    asset_fields = tables["asset_decision"]["fields"]
    for observation_axis in (
        "origin_kind",
        "reduction_level",
        "acquisition_method",
        "evidence_quality",
        "scientific_use_class",
        "rights_evidence_state",
    ):
        assert observation_axis not in source_file_fields
        assert observation_axis not in asset_fields
    assert "material_scope" not in source_file_fields
    assert "material_scope_hint" not in source_file_fields
    assert "material_scope" not in asset_fields
    assert asset_fields["material_scope_hint"]["enum"] == "material_scope"
    assert enums["artifact_role"] == [
        "primary_data",
        "supplementary_information",
        "code",
        "simulation_input",
        "simulation_output",
        "model_artifact",
        "model_output",
        "computed_property_output",
        "derived_duplicate",
        "mirror_duplicate",
        "subset_view",
        "documentation",
        "restricted_reference",
        "excluded_non_domain",
    ]
    assert enums["data_stage"] == [
        "raw",
        "normalized",
        "derived",
        "aggregate",
        "model_output",
        "metadata_only",
        "reference_only",
    ]
    assert {"virtual_candidate", "monomer_rule"} <= set(enums["material_scope"])


def test_source_scope_file_and_locator_foreign_keys_are_frozen():
    tables = _bundle().schema["tables"]

    assert _foreign_key(tables["source_scope"], "fk_source_scope_source")[
        "references"
    ] == {"table": "source", "fields": ["source_id"]}
    assert _foreign_key(tables["source_scope"], "fk_source_scope_parent_same_source")[
        "references"
    ] == {"table": "source_scope", "fields": ["source_scope_id", "source_id"]}
    assert _unique_fields(tables["source_scope"], "uq_source_scope_key") == [
        "source_scope_key"
    ]
    assert _foreign_key(tables["source_file"], "fk_source_file_source_scope")[
        "references"
    ] == {"table": "source_scope", "fields": ["source_scope_id"]}
    assert _foreign_key(tables["source_locator"], "fk_source_locator_source_file")[
        "references"
    ] == {"table": "source_file", "fields": ["source_file_id"]}
    relation = tables["source_scope_relation"]
    assert _foreign_key(relation, "fk_source_scope_relation_subject")["references"][
        "table"
    ] == "source_scope"
    assert _foreign_key(relation, "fk_source_scope_relation_object")["references"][
        "table"
    ] == "source_scope"
    assert _unique_fields(tables["source_locator"], "uq_source_locator_file_hash") == [
        "source_file_id",
        "locator_hash",
    ]


def test_record_revision_snapshot_selection_and_lineage_are_not_conflated():
    tables = _bundle().schema["tables"]
    revision = tables["record_revision"]
    snapshot_record = tables["snapshot_record"]

    assert _unique_fields(revision, "uq_record_revision_content") == [
        "record_uid",
        "schema_version",
        "content_hash",
    ]
    assert snapshot_record["primary_key"] == ["snapshot_id", "record_uid"]
    assert _foreign_key(snapshot_record, "fk_snapshot_record_snapshot")[
        "references"
    ] == {"table": "dataset_snapshot", "fields": ["snapshot_id"]}
    assert _foreign_key(snapshot_record, "fk_snapshot_record_revision_owner")[
        "references"
    ] == {
        "table": "record_revision",
        "fields": ["record_revision_id", "record_uid"],
    }
    lineage = tables["record_lineage"]
    assert _foreign_key(lineage, "fk_record_lineage_child")["references"][
        "table"
    ] == "record_registry"
    assert _foreign_key(lineage, "fk_record_lineage_parent")["references"][
        "table"
    ] == "record_registry"


def test_equivalence_groups_and_memberships_are_snapshot_bound_and_typed():
    bundle = _bundle()
    tables = bundle.schema["tables"]
    enum_values = bundle.enums["enums"]["equivalence_group_type"]

    assert {
        "exact_file_hash",
        "parent_dataset",
        "publication",
        "source_record",
        "formulation",
        "curve",
        "lineage_family",
        "computational_system",
    } <= set(enum_values)
    assert _foreign_key(tables["equivalence_group"], "fk_equivalence_group_snapshot")[
        "references"
    ]["table"] == "dataset_snapshot"
    assert _foreign_key(
        tables["equivalence_group"], "fk_equivalence_group_canonical_snapshot_record"
    )["references"] == {
        "table": "snapshot_record",
        "fields": ["snapshot_id", "record_uid"],
    }
    membership = tables["equivalence_membership"]
    assert _unique_fields(membership, "uq_equivalence_membership_record") == [
        "group_id",
        "snapshot_id",
        "record_uid",
    ]
    assert _foreign_key(membership, "fk_equivalence_membership_snapshot_record")[
        "references"
    ] == {"table": "snapshot_record", "fields": ["snapshot_id", "record_uid"]}


def test_rights_facts_decisions_and_full_lineage_evidence_are_relational():
    tables = _bundle().schema["tables"]
    rights_fact = tables["rights_fact"]
    decision = tables["rights_action_decision"]
    decision_evidence = tables["rights_decision_evidence"]

    assert _foreign_key(rights_fact, "fk_rights_fact_evidence_package")[
        "references"
    ] == {"table": "rights_evidence_package", "fields": ["evidence_package_id"]}
    assert _foreign_key(rights_fact, "fk_rights_fact_source_scope")["references"] == {
        "table": "source_scope",
        "fields": ["source_scope_id"],
    }
    assert _foreign_key(decision, "fk_rights_action_decision_snapshot")[
        "references"
    ]["table"] == "dataset_snapshot"
    assert _foreign_key(decision, "fk_rights_action_decision_target")["references"] == {
        "table": "record_registry",
        "fields": ["record_uid", "entity_type"],
    }
    assert _foreign_key(decision, "fk_rights_action_decision_context")[
        "references"
    ]["table"] == "context_profile"
    assert _unique_fields(decision, "uq_rights_action_decision_axes") == [
        "snapshot_id",
        "target_uid",
        "operation",
        "actor",
        "purpose",
        "rights_object_class",
        "context_profile_id",
    ]
    assert _foreign_key(
        decision_evidence, "fk_rights_decision_evidence_decision"
    )["references"]["table"] == "rights_action_decision"
    assert _foreign_key(decision_evidence, "fk_rights_decision_evidence_fact")[
        "references"
    ]["table"] == "rights_fact"
    assert _foreign_key(
        decision_evidence, "fk_rights_decision_evidence_contributor"
    )["references"]["table"] == "record_registry"
    decision_checks = " ".join(item["expression"] for item in decision["checks"])
    assert "evidence_fact_count >= 1" in decision_checks
    assert "contributing_record_count >= 1" in decision_checks


def test_rights_enums_cover_evidence_facts_five_axes_and_fail_closed_reasons():
    enums = _bundle().enums["enums"]

    assert {
        "repository_metadata",
        "publisher_page",
        "license_text",
        "terms_of_use",
        "author_communication",
        "access_restriction",
        "withdrawal_notice",
    } <= set(enums["rights_evidence_type"])
    assert {
        "official_repository",
        "publisher",
        "licensor",
        "author_permission",
        "crossref",
        "third_party_summary",
    } == set(enums["rights_authority_type"])
    assert {
        "declared_license",
        "commercial_use",
        "derivative",
        "redistribution",
        "tdm",
        "ai_training",
        "attribution",
        "access_restriction",
        "withdrawn",
    } == set(enums["rights_fact_predicate"])
    assert set(enums["rights_operation"]) == {
        "retrieve",
        "store",
        "parse",
        "transform",
        "analyze",
        "train",
        "evaluate",
        "redistribute",
        "publish",
        "deploy",
    }
    assert set(enums["rights_decision"]) == {
        "allow",
        "allow_with_obligations",
        "deny",
        "manual_review",
    }
    assert {
        "EVIDENCE_MISSING",
        "SCOPE_UNRESOLVED",
        "LICENSE_CONFLICT",
        "TDM_AI_UNSPECIFIED",
        "NC_PURPOSE_MISMATCH",
        "ND_DERIVATIVE_SHARE",
        "ACCESS_WITHDRAWN",
        "AUTHOR_PERMISSION_REQUIRED",
        "LINEAGE_INCOMPLETE",
        "OBLIGATION_UNSATISFIED",
        "EXPLICIT_PROHIBITION",
    } <= set(enums["rights_reason_code"])


def test_count_evidence_cannot_promote_unlanded_claims_to_frozen_facts():
    schema = _bundle().schema
    table = schema["tables"]["count_assertion"]
    check_text = " ".join(item["expression"] for item in table["checks"])
    ingested_rule = next(
        item
        for item in schema["conditional_required"]
        if item["name"] == "cr_count_ingested_file"
    )

    assert "frozen_fact" in check_text
    assert "ingested_file_recount" in check_text
    assert ingested_rule["when"] == {
        "field": "count_evidence_type",
        "equals": "ingested_file_recount",
    }
    assert set(ingested_rule["require"]) == {
        "source_file_id",
        "snapshot_id",
        "filtering_rule",
        "deduplication_rule",
        "parser_version",
        "source_file_sha256",
        "recount_transformation_id",
    }


def test_governance_targets_use_registry_and_expected_entity_type_only():
    tables = _bundle().schema["tables"]

    for table_name, table in tables.items():
        fields = table["fields"]
        assert not {"target_table", "target_id"} <= set(fields), table_name
        if "target_uid" not in fields:
            continue
        assert fields["target_uid"]["required"] is True, table_name
        assert fields["expected_entity_type"]["required"] is True, table_name
        target_fks = [
            item
            for item in table["foreign_keys"]
            if item["fields"] == ["target_uid", "expected_entity_type"]
        ]
        assert len(target_fks) == 1, table_name
        assert target_fks[0]["references"] == {
            "table": "record_registry",
            "fields": ["record_uid", "entity_type"],
        }


def test_contract_contains_no_retired_governance_keys_or_global_publish_flag():
    schema = _bundle().schema
    forbidden_keys = {
        "target_table",
        "target_id",
        "rights_tier",
        "rights_state",
        "scientific_readiness",
        "license_status",
        "may_publish",
    }

    def mapping_keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from mapping_keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from mapping_keys(child)

    assert forbidden_keys.isdisjoint(set(mapping_keys(schema)))


def test_contract_semantic_hashes_are_deterministic_across_two_loads():
    first = _bundle()
    second = _bundle()

    assert first.document_hashes == second.document_hashes
    assert json.dumps(first.schema, ensure_ascii=False, sort_keys=True) == json.dumps(
        second.schema, ensure_ascii=False, sort_keys=True
    )
    assert first.document_hashes["schema"] == (
        "63bb1bcea8e5791e86368279956c1b0c30cc09da918a745da92fc1b31836433d"
    )
    assert first.document_hashes["enums"] == (
        "02b27cb5ad2a1f7e59b1a1c263d00780299b534f6cbf0e6670333a050a67085c"
    )
    assert first.document_hashes["rules"] == (
        "3e3859b683a14b8ff5d548f45b4cc067e08e906339ca2808c71d513c347db3d1"
    )
