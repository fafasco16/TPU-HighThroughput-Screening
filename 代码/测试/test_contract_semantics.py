from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
import duckdb

import contract
from contract import ContractValidationError, load_contract_bundle, validate_contract_bundle
from record_identity import (
    canonical_identity_json,
    content_sha256,
    identity_key_sha256,
    stable_record_uid,
    stable_revision_id,
)


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "结构定义" / "v0.2来源治理合同.yaml"
ENUM_PATH = ROOT / "结构定义" / "v0.2枚举.yaml"
RULE_PATH = ROOT / "结构定义" / "v0.2质量规则.yaml"
FIXTURES = Path(__file__).parent / "夹具"
NOW = "2026-07-20T00:00:00+00:00"


def _documents():
    with (FIXTURES / "v0.2最小合同.yaml").open(encoding="utf-8") as stream:
        schema = yaml.safe_load(stream)
    with (FIXTURES / "v0.2最小枚举.yaml").open(encoding="utf-8") as stream:
        enums = yaml.safe_load(stream)
    with (FIXTURES / "v0.2最小质量规则.yaml").open(encoding="utf-8") as stream:
        rules = yaml.safe_load(stream)
    return schema, enums, rules


def _bundle():
    return load_contract_bundle(SCHEMA_PATH, ENUM_PATH, RULE_PATH)


def _fk(table: dict, name: str) -> dict:
    return next(item for item in table["foreign_keys"] if item["name"] == name)


def _uq(table: dict, name: str) -> list[str]:
    return next(
        item["fields"] for item in table["unique_constraints"] if item["name"] == name
    )


def _registry_row(entity_type: str = "source") -> dict[str, object]:
    identity = {"key": "alpha"}
    canonical = canonical_identity_json(identity)
    return {
        "record_uid": stable_record_uid(entity_type, identity),
        "entity_type": entity_type,
        "canonical_identity_key_json": canonical,
        "identity_key_sha256": identity_key_sha256(identity),
        "id_algorithm_version": "uuid5-v1",
    }


def test_revision_policy_is_closed_and_every_check_is_bound_by_duckdb():
    schema, enums, rules = _documents()
    schema["tables"]["source_scope"]["revision_policy"] = "mutable"
    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)
    assert captured.value.code == "revision_policy_invalid"

    schema, enums, rules = _documents()
    schema["tables"]["record_registry"]["checks"][0]["expression"] = (
        "missing_bound_column = 1"
    )
    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)
    assert captured.value.code == "check_duckdb_invalid"
    assert captured.value.table == "record_registry"
    assert captured.value.constraint == "ck_identity_sha256"


def test_quality_rule_implementations_and_tests_resolve_in_repository():
    schema, enums, rules = _documents()
    rules["rules"]["V02-CONTRACT-001"]["implementation_ref"] = (
        "contract.symbol_that_does_not_exist"
    )
    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)
    assert captured.value.code == "rule_implementation_missing"

    schema, enums, rules = _documents()
    rules["rules"]["V02-CONTRACT-001"]["implementation_ref"] = "os.system"
    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)
    assert captured.value.code == "rule_implementation_outside_repository"

    schema, enums, rules = _documents()
    rules["rules"]["V02-CONTRACT-001"]["test_ids"] = [
        "test_name_that_does_not_exist"
    ]
    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)
    assert captured.value.code == "rule_test_missing"


def test_duckdb_checks_reject_bad_path_blob_locator_and_rights_matrix():
    tables = _bundle().schema["tables"]
    connection = duckdb.connect(":memory:")
    try:
        cases = {
            "source_file": (
                ["relative_path"],
                ["C:/escape/../bad.csv"],
            ),
            "content_blob": (
                ["content_blob_uid", "content_sha256", "size_bytes"],
                ["sha256:" + "b" * 64, "a" * 64, 1],
            ),
            "source_locator": (
                ["locator_text", "locator_json", "locator_hash"],
                ["file:x", "[]", hashlib.sha256(b"[]").hexdigest()],
            ),
            "rights_action_decision": (
                ["decision", "reason_code"],
                ["allow", "EVIDENCE_MISSING"],
            ),
        }
        for table_name, (insert_fields, values) in cases.items():
            table = tables[table_name]
            columns = [
                f'"{name}" {field["duckdb_type"]}'
                for name, field in table["fields"].items()
            ]
            checks = [
                f'CONSTRAINT "{item["name"]}" CHECK ({item["expression"]})'
                for item in table["checks"]
            ]
            connection.execute(
                f'CREATE TEMP TABLE "negative_{table_name}" ('
                + ", ".join(columns + checks)
                + ")"
            )
            placeholders = ", ".join("?" for _ in values)
            field_sql = ", ".join(f'"{name}"' for name in insert_fields)
            with pytest.raises(duckdb.Error):
                connection.execute(
                    f'INSERT INTO "negative_{table_name}" ({field_sql}) VALUES ({placeholders})',
                    values,
                )
    finally:
        connection.close()


def test_real_contract_freezes_identity_registry_and_generic_target_types():
    bundle = _bundle()
    tables = bundle.schema["tables"]

    assert bundle.schema["id_algorithm_version"] == "uuid5-v1"
    assert _uq(tables["record_registry"], "uq_record_registry_uid_entity") == [
        "record_uid",
        "entity_type",
    ]
    registry_checks = " ".join(item["expression"] for item in tables["record_registry"]["checks"])
    assert "uuid5-v1" in registry_checks
    assert "-5" in registry_checks

    for table_name in (
        "citation_assignment",
        "rights_evidence_package",
        "rights_action_decision",
        "exclusion_record",
    ):
        table = tables[table_name]
        expected = next(
            item
            for item in table["foreign_keys"]
            if item["references"]["table"] == "record_registry"
            and item["references"]["fields"] == ["record_uid", "entity_type"]
        )
        assert expected["fields"] == ["target_uid", "expected_entity_type"]


def test_source_file_chain_content_blob_and_single_classification_truth_are_relational():
    tables = _bundle().schema["tables"]
    source_file = tables["source_file"]
    locator = tables["source_locator"]

    assert "content_blob" in tables
    assert _uq(source_file, "uq_source_file_id_scope") == [
        "source_file_id",
        "source_scope_id",
    ]
    assert _uq(locator, "uq_source_locator_id_file") == [
        "source_locator_id",
        "source_file_id",
    ]
    assert _fk(source_file, "fk_source_file_content_blob")["fields"] == [
        "content_blob_uid",
        "content_sha256",
        "size_bytes",
    ]

    for retired in (
        "artifact_role",
        "data_stage",
        "material_scope_hint",
        "registration_status",
        "availability_status",
        "parse_status",
        "scientific_admission_status",
        "model_readiness_status",
        "release_status",
    ):
        assert retired not in source_file["fields"]
    assert {"artifact_role", "data_stage", "material_scope_hint"} <= set(
        tables["asset_decision"]["fields"]
    )

    assert _fk(tables["record_source"], "fk_record_source_file_scope")["fields"] == [
        "source_file_id",
        "source_scope_id",
    ]
    assert _fk(tables["record_source"], "fk_record_source_locator_file")["fields"] == [
        "source_locator_id",
        "source_file_id",
    ]
    assert _fk(tables["count_assertion"], "fk_count_assertion_file_scope_hash")[
        "fields"
    ] == ["source_file_id", "source_scope_id", "source_file_sha256"]
    assert _fk(tables["asset_decision"], "fk_asset_decision_file_scope")["fields"] == [
        "source_file_id",
        "source_scope_id",
    ]

    path_check = next(
        item["expression"]
        for item in source_file["checks"]
        if item["name"] == "ck_source_file_relative_path"
    )
    for forbidden_fragment in ("../", "/../", "//", "^[A-Za-z]:"):
        assert forbidden_fragment in path_check


def test_physical_rows_are_immutable_and_lifecycle_is_snapshot_bound():
    tables = _bundle().schema["tables"]
    for table_name in ("record_registry", "source", "source_scope", "source_file"):
        assert tables[table_name]["revision_policy"] == "immutable_append_only"

    assert "record_status_event" in tables
    assert "snapshot_record_status" in tables
    status = tables["snapshot_record_status"]
    assert status["primary_key"] == ["snapshot_id", "record_uid"]
    assert _fk(status, "fk_snapshot_record_status_selection")["references"] == {
        "table": "snapshot_record",
        "fields": ["snapshot_id", "record_uid"],
    }
    for axis in (
        "registration_status",
        "availability_status",
        "parse_status",
        "scientific_admission_status",
        "model_readiness_status",
        "release_status",
    ):
        assert status["fields"][axis]["enum"] == axis
        assert f"{axis}_event_id" in status["fields"]


def test_revisions_point_backward_with_same_owner_and_never_require_old_row_rewrite():
    table = _bundle().schema["tables"]["record_revision"]
    assert "supersedes_revision_id" in table["fields"]
    assert "superseded_by_revision_id" not in table["fields"]
    assert "revision_status" not in table["fields"]
    assert "effective_snapshot_id" not in table["fields"]
    assert _fk(table, "fk_record_revision_supersedes_owner")["fields"] == [
        "supersedes_revision_id",
        "record_uid",
    ]
    assert _fk(table, "fk_record_revision_supersedes_owner")["references"] == {
        "table": "record_revision",
        "fields": ["record_revision_id", "record_uid"],
    }


def test_equivalence_canonical_is_a_selected_snapshot_member():
    tables = _bundle().schema["tables"]
    group = tables["equivalence_group"]
    membership = tables["equivalence_membership"]
    assert "snapshot_id" in membership["fields"]
    assert _fk(membership, "fk_equivalence_membership_snapshot_record")[
        "references"
    ] == {"table": "snapshot_record", "fields": ["snapshot_id", "record_uid"]}
    assert _fk(group, "fk_equivalence_group_canonical_snapshot_record")["fields"] == [
        "snapshot_id",
        "canonical_record_uid",
    ]
    assert _fk(group, "fk_equivalence_group_canonical_snapshot_record")["references"] == {
        "table": "snapshot_record",
        "fields": ["snapshot_id", "record_uid"],
    }


def test_transformations_have_explicit_revision_inputs_outputs_and_lineage_requirements():
    tables = _bundle().schema["tables"]
    assert {"transformation_input", "transformation_output"} <= set(tables)
    assert _fk(tables["transformation_input"], "fk_transformation_input_revision_owner")[
        "references"
    ] == {
        "table": "record_revision",
        "fields": ["record_revision_id", "record_uid"],
    }
    assert _fk(
        tables["transformation_output"], "fk_transformation_output_revision_owner"
    )["references"] == {
        "table": "record_revision",
        "fields": ["record_revision_id", "record_uid"],
    }
    lineage_checks = " ".join(
        item["expression"] for item in tables["record_lineage"]["checks"]
    )
    assert "computed_from" in lineage_checks
    assert "aggregated_from" in lineage_checks
    assert "digitized_from" in lineage_checks


def test_source_family_is_formal_and_parent_scope_is_bound_to_same_source():
    tables = _bundle().schema["tables"]
    assert {"source_family", "source_family_membership"} <= set(tables)
    assert "source_family_key" not in tables["source"]["fields"]
    assert "source_family_type" not in tables["source_scope"]["fields"]
    parent_fk = _fk(tables["source_scope"], "fk_source_scope_parent_same_source")
    assert parent_fk["fields"] == ["parent_scope_id", "source_id"]
    assert parent_fk["references"] == {
        "table": "source_scope",
        "fields": ["source_scope_id", "source_id"],
    }
    membership = tables["source_family_membership"]
    assert "evidence_summary" in membership["fields"]
    assert "evidence_locator_id" in membership["fields"]


def test_record_identity_qc_recomputes_json_digest_algorithm_and_uuid5():
    row = _registry_row()
    contract.validate_record_identity_rows([row])

    for field, value in (
        ("record_uid", "00000000-0000-4000-8000-000000000000"),
        ("identity_key_sha256", "0" * 64),
        ("id_algorithm_version", "uuid5-v2"),
        ("canonical_identity_key_json", '{"key": "alpha"}'),
    ):
        broken = dict(row)
        broken[field] = value
        with pytest.raises(ContractValidationError):
            contract.validate_record_identity_rows([broken])


def test_locator_qc_recomputes_canonical_json_hash_and_type_shape():
    payload = {"relative_path": "01_原始数据/data.csv"}
    canonical = canonical_identity_json(payload)
    row = {
        "source_locator_id": "locator-1",
        "locator_type": "file",
        "locator_json": canonical,
        "locator_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "locator_hash_algorithm_version": "tpu-locator-json/1",
    }
    contract.validate_locator_rows([row])

    broken = dict(row)
    broken["locator_json"] = '{"relative_path": "01_原始数据/data.csv"}'
    with pytest.raises(ContractValidationError):
        contract.validate_locator_rows([broken])


def test_citation_qc_requires_full_reference_and_nonempty_article_authors():
    citation_contract = _bundle().schema["tables"]["citation"]
    assert citation_contract["fields"]["reference_text"]["required"] is True
    assert any(
        item["name"] == "ck_citation_reference_text"
        for item in citation_contract["checks"]
    )
    row = {
        "citation_id": "citation-1",
        "citation_type": "article",
        "title": "A TPU dataset",
        "authors_json": canonical_identity_json([{"literal": "A. Author"}]),
        "csl_json": canonical_identity_json(
            {
                "id": "citation-1",
                "type": "article-journal",
                "title": "A TPU dataset",
                "author": [{"literal": "A. Author"}],
            }
        ),
        "reference_text": "A. Author. A TPU dataset. Journal 1 (2026) 1–10. https://doi.org/10.1/example.",
        "bibtex_text": "@article{citation-1, title={A TPU dataset}}",
    }
    contract.validate_citation_rows([row])

    for field, value in (
        ("reference_text", ""),
        ("authors_json", "[]"),
        ("bibtex_text", "[A TPU dataset](https://example.org)"),
    ):
        broken = dict(row)
        broken[field] = value
        with pytest.raises(ContractValidationError):
            contract.validate_citation_rows([broken])

    page_payload = canonical_identity_json({"page_number": "one"})
    broken = {
        **row,
        "locator_type": "page",
        "locator_json": page_payload,
        "locator_hash": hashlib.sha256(page_payload.encode("utf-8")).hexdigest(),
    }
    with pytest.raises(ContractValidationError):
        contract.validate_locator_rows([broken])


def test_revision_qc_recomputes_content_hash_revision_uuid_and_chain():
    record_uid = _registry_row()["record_uid"]
    first_content = {"value": 1}
    second_content = {"value": 2}
    first_id = stable_revision_id(record_uid, "v0.2", first_content)
    second_id = stable_revision_id(record_uid, "v0.2", second_content)
    rows = [
        {
            "record_revision_id": first_id,
            "record_uid": record_uid,
            "schema_version": "v0.2",
            "content_hash": content_sha256(first_content),
            "content_json": canonical_identity_json(first_content),
            "revision_number": 1,
            "supersedes_revision_id": None,
        },
        {
            "record_revision_id": second_id,
            "record_uid": record_uid,
            "schema_version": "v0.2",
            "content_hash": content_sha256(second_content),
            "content_json": canonical_identity_json(second_content),
            "revision_number": 2,
            "supersedes_revision_id": first_id,
        },
    ]
    contract.validate_revision_chains(rows)

    broken = deepcopy(rows)
    broken[1]["content_hash"] = "0" * 64
    with pytest.raises(ContractValidationError):
        contract.validate_revision_chains(broken)

    broken = deepcopy(rows)
    broken[1]["supersedes_revision_id"] = second_id
    with pytest.raises(ContractValidationError):
        contract.validate_revision_chains(broken)


def test_snapshot_qc_checks_real_contract_hashes_revision_schema_and_approval():
    record_uid = _registry_row()["record_uid"]
    content = {"value": 1}
    revision_id = stable_revision_id(record_uid, "v0.2", content)
    hashes = {"schema": "a" * 64, "enums": "b" * 64, "rules": "c" * 64}
    tables = {
        "dataset_snapshot": [
            {
                "snapshot_id": "snapshot-1",
                "snapshot_status": "frozen",
                "schema_version": "v0.2",
                "schema_hash": hashes["schema"],
                "enum_hash": hashes["enums"],
                "rule_catalog_hash": hashes["rules"],
                "logical_hash": "d" * 64,
                "logical_hash_algorithm_version": "tpu-logical-hash/1",
                "created_at": "2026-07-19T00:00:00+00:00",
                "created_by": "builder",
                "frozen_at": "2026-07-20T00:00:00+00:00",
                "approved_by": "reviewer",
            }
        ],
        "record_revision": [
            {
                "record_revision_id": revision_id,
                "record_uid": record_uid,
                "schema_version": "v0.2",
            }
        ],
        "snapshot_record": [
            {
                "snapshot_id": "snapshot-1",
                "record_uid": record_uid,
                "record_revision_id": revision_id,
            }
        ],
    }
    contract.validate_snapshot_integrity(
        tables, document_hashes=hashes, schema_version="v0.2"
    )

    broken = deepcopy(tables)
    broken["dataset_snapshot"][0]["schema_hash"] = "0" * 64
    with pytest.raises(ContractValidationError):
        contract.validate_snapshot_integrity(
            broken, document_hashes=hashes, schema_version="v0.2"
        )

    broken = deepcopy(tables)
    broken["dataset_snapshot"][0]["approved_by"] = "builder"
    with pytest.raises(ContractValidationError):
        contract.validate_snapshot_integrity(
            broken, document_hashes=hashes, schema_version="v0.2"
        )

    broken = deepcopy(tables)
    broken["dataset_snapshot"][0]["schema_version"] = "v0.1"
    with pytest.raises(ContractValidationError):
        contract.validate_snapshot_integrity(
            broken, document_hashes=hashes, schema_version="v0.2"
        )


def test_status_qc_rejects_axis_value_mismatch_and_broken_event_selection():
    record_uid = _registry_row()["record_uid"]
    axes = {
        "registration_status": "registered",
        "availability_status": "available",
        "parse_status": "parsed",
        "scientific_admission_status": "admitted",
        "model_readiness_status": "eligible",
        "release_status": "approved",
    }
    events = []
    assignment = {
        "snapshot_id": "s1",
        "record_uid": record_uid,
        "entity_type": "source",
    }
    for index, (axis, value) in enumerate(axes.items(), 1):
        event_id = f"event-{index}"
        events.append(
            {
                "status_event_id": event_id,
                "record_uid": record_uid,
                "entity_type": "source",
                "status_axis": axis,
                "status_value": value,
                "event_sequence": 1,
                "previous_status_event_id": None,
                "effective_at": NOW,
                "asserted_at": NOW,
            }
        )
        assignment[axis] = value
        assignment[f"{axis}_event_id"] = event_id
    contract.validate_status_history(
        {
            "dataset_snapshot": [{"snapshot_id": "s1", "created_at": NOW, "frozen_at": NOW}],
            "record_status_event": events,
            "snapshot_record_status": [assignment],
        },
        _bundle().enums,
    )

    broken = deepcopy(events)
    broken[0]["status_value"] = "available"
    with pytest.raises(ContractValidationError):
        contract.validate_status_history(
            {
                "dataset_snapshot": [{"snapshot_id": "s1", "created_at": NOW, "frozen_at": NOW}],
                "record_status_event": broken,
                "snapshot_record_status": [assignment],
            },
            _bundle().enums,
        )
    broken = deepcopy(events)
    broken[0]["effective_at"] = "2026-07-21T00:00:00+00:00"
    with pytest.raises(ContractValidationError):
        contract.validate_status_history(
            {
                "dataset_snapshot": [{"snapshot_id": "s1", "created_at": NOW, "frozen_at": NOW}],
                "record_status_event": broken,
                "snapshot_record_status": [assignment],
            },
            _bundle().enums,
        )


def test_lineage_qc_requires_successful_transform_and_reachability_to_original_file():
    tables = {
        "record_registry": [
            {"record_uid": "raw", "entity_type": "experimental_record"},
            {"record_uid": "derived", "entity_type": "computational_record"},
        ],
        "record_source": [{"record_uid": "raw", "source_file_id": "file-1"}],
        "record_lineage": [
            {
                "child_record_uid": "derived",
                "parent_record_uid": "raw",
                "relation_type": "computed_from",
                "transformation_id": "tx-1",
                "evidence_locator_id": None,
            }
        ],
        "transformation_run": [{"transformation_id": "tx-1", "status": "succeeded"}],
        "transformation_input": [
            {"transformation_id": "tx-1", "record_uid": "raw"}
        ],
        "transformation_output": [
            {"transformation_id": "tx-1", "record_uid": "derived"}
        ],
    }
    contract.validate_lineage_integrity(tables)

    broken = deepcopy(tables)
    broken["transformation_run"][0]["status"] = "failed"
    with pytest.raises(ContractValidationError):
        contract.validate_lineage_integrity(broken)

    broken = deepcopy(tables)
    broken["record_lineage"][0]["parent_record_uid"] = "derived"
    with pytest.raises(ContractValidationError):
        contract.validate_lineage_integrity(broken)


def test_rights_closure_qc_recomputes_rows_ancestry_archive_state_and_hash():
    evidence_rows = [
        {
            "rights_decision_id": "decision-1",
            "contributing_record_uid": "source-record",
            "rights_fact_id": "fact-1",
        }
    ]
    closure_hash = hashlib.sha256(
        (
            "tpu-rights-closure/1"
            + json.dumps(
            [["source-record", "fact-1"]],
            ensure_ascii=False,
            separators=(",", ":"),
            )
        ).encode("utf-8")
    ).hexdigest()
    tables = {
        "rights_action_decision": [
            {
                "rights_decision_id": "decision-1",
                "target_uid": "derived-record",
                "decision": "allow",
                "reason_code": "EXPLICIT_PERMISSION",
                "evidence_fact_count": 1,
                "contributing_record_count": 1,
                "evidence_closure_hash": closure_hash,
                "evidence_closure_algorithm_version": "tpu-rights-closure/1",
            }
        ],
        "rights_decision_evidence": evidence_rows,
        "rights_fact": [
            {"rights_fact_id": "fact-1", "evidence_package_id": "package-1"}
        ],
        "rights_evidence_package": [
            {
                "evidence_package_id": "package-1",
                "capture_status": "archived_verified",
                "body_archive_path": "evidence/package-1.html",
                "body_sha256": "e" * 64,
                "response_headers_json": "{}",
                "verified_by": "reviewer",
                "verified_at": NOW,
            }
        ],
        "record_lineage": [
            {
                "child_record_uid": "derived-record",
                "parent_record_uid": "source-record",
            }
        ],
    }
    contract.validate_rights_decision_closure(tables)

    for table_name, field, value in (
        ("rights_action_decision", "evidence_fact_count", 2),
        ("rights_action_decision", "reason_code", "EVIDENCE_MISSING"),
        ("rights_evidence_package", "capture_status", "archived_unverified"),
        ("rights_evidence_package", "body_sha256", None),
        ("rights_decision_evidence", "contributing_record_uid", "unreachable"),
    ):
        broken = deepcopy(tables)
        broken[table_name][0][field] = value
        with pytest.raises(ContractValidationError):
            contract.validate_rights_decision_closure(broken)


def test_rights_archive_qc_rejects_unverified_body_or_unsafe_archive_path():
    row = {
        "evidence_package_id": "package-1",
        "capture_status": "archived_verified",
        "body_archive_path": "文档/来源证据/package-1.html",
        "body_sha256": "e" * 64,
        "response_headers_json": canonical_identity_json({"content-type": "text/html"}),
        "verified_by": "reviewer",
        "verified_at": NOW,
        "session_fingerprint_sha256": None,
        "session_fingerprint_scope": None,
    }
    contract.validate_rights_evidence_archives([row])
    for field, value in (
        ("body_sha256", None),
        ("verified_by", None),
        ("body_archive_path", "../outside.html"),
        ("response_headers_json", "[]"),
    ):
        broken = dict(row)
        broken[field] = value
        with pytest.raises(ContractValidationError):
            contract.validate_rights_evidence_archives([broken])


def test_equivalence_qc_rejects_missing_or_multiple_canonical_membership():
    tables = {
        "equivalence_group": [
            {
                "group_id": "g1",
                "snapshot_id": "s1",
                "canonical_record_uid": "r1",
            }
        ],
        "equivalence_membership": [
            {"group_id": "g1", "snapshot_id": "s1", "record_uid": "r1"},
            {"group_id": "g1", "snapshot_id": "s1", "record_uid": "r2"},
        ],
        "snapshot_record": [
            {"snapshot_id": "s1", "record_uid": "r1"},
            {"snapshot_id": "s1", "record_uid": "r2"},
        ],
    }
    contract.validate_equivalence_integrity(tables)
    broken = deepcopy(tables)
    broken["equivalence_membership"] = broken["equivalence_membership"][1:]
    with pytest.raises(ContractValidationError):
        contract.validate_equivalence_integrity(broken)
    broken = deepcopy(tables)
    broken["equivalence_group"].append(
        {"group_id": "g1", "snapshot_id": "s1", "canonical_record_uid": "r2"}
    )
    with pytest.raises(ContractValidationError):
        contract.validate_equivalence_integrity(broken)


def test_source_family_and_supersession_qc_reject_cycles_and_cross_object_links():
    family_tables = {
        "source_scope": [
            {"source_scope_id": "root", "source_id": "source-1", "parent_scope_id": None},
            {"source_scope_id": "child", "source_id": "source-1", "parent_scope_id": "root"},
        ],
        "source_family": [{"source_family_id": "family-1"}],
        "source_family_membership": [
            {
                "source_family_id": "family-1",
                "source_id": "source-1",
                "source_scope_id": "child",
                "evidence_locator_id": None,
                "evidence_summary": "same experiment",
            }
        ],
    }
    contract.validate_source_family_integrity(family_tables)
    broken = deepcopy(family_tables)
    broken["source_scope"][0]["parent_scope_id"] = "child"
    with pytest.raises(ContractValidationError):
        contract.validate_source_family_integrity(broken)
    broken = deepcopy(family_tables)
    broken["source_family_membership"][0]["evidence_summary"] = ""
    with pytest.raises(ContractValidationError):
        contract.validate_source_family_integrity(broken)

    revisions = [
        {"record_revision_id": "a", "record_uid": "r", "supersedes_revision_id": None},
        {"record_revision_id": "b", "record_uid": "r", "supersedes_revision_id": "a"},
    ]
    contract.validate_supersession_chains({"record_revision": revisions})
    revisions[0]["supersedes_revision_id"] = "b"
    with pytest.raises(ContractValidationError):
        contract.validate_supersession_chains({"record_revision": revisions})

    citations = [
        {"citation_id": "old", "source_id": "source-1", "supersedes_citation_id": None},
        {"citation_id": "new", "source_id": "source-2", "supersedes_citation_id": "old"},
    ]
    with pytest.raises(ContractValidationError):
        contract.validate_supersession_chains({"citation": citations})

    scope_tables = {
        "source_scope": [
            {"source_scope_id": "s1", "source_id": "source-1"},
            {"source_scope_id": "s2", "source_id": "source-1"},
        ],
        "source_scope_relation": [
            {"subject_scope_id": "s1", "object_scope_id": "s2", "relation_type": "supersedes"},
            {"subject_scope_id": "s2", "object_scope_id": "s1", "relation_type": "supersedes"},
        ],
    }
    with pytest.raises(ContractValidationError):
        contract.validate_supersession_chains(scope_tables)


def test_frozen_count_qc_requires_same_file_scope_hash_and_successful_recount():
    tables = {
        "source_file": [
            {
                "source_file_id": "f1",
                "source_scope_id": "scope-1",
                "content_sha256": "a" * 64,
            }
        ],
        "source_locator": [
            {"source_locator_id": "loc-1", "source_file_id": "f1"}
        ],
        "transformation_run": [
            {"transformation_id": "tx-1", "status": "succeeded"}
        ],
        "count_assertion": [
            {
                "count_assertion_id": "c1",
                "assertion_status": "frozen_fact",
                "count_evidence_type": "ingested_file_recount",
                "source_scope_id": "scope-1",
                "source_file_id": "f1",
                "source_locator_id": "loc-1",
                "source_file_sha256": "a" * 64,
                "recount_transformation_id": "tx-1",
            }
        ],
    }
    contract.validate_frozen_count_assertions(tables)
    broken = deepcopy(tables)
    broken["count_assertion"][0]["source_scope_id"] = "scope-2"
    with pytest.raises(ContractValidationError):
        contract.validate_frozen_count_assertions(broken)


def test_rule_catalog_includes_executable_semantic_and_build_gates():
    rules = _bundle().rules["rules"]
    expected = {
        "V02-ID-REGISTRY-001": "contract.validate_record_identity_rows",
        "V02-REVISION-001": "contract.validate_revision_chains",
        "V02-SNAPSHOT-001": "contract.validate_snapshot_integrity",
        "V02-STATUS-001": "contract.validate_status_history",
        "V02-LINEAGE-001": "contract.validate_lineage_integrity",
        "V02-RIGHTS-001": "contract.validate_rights_decision_closure",
        "V02-RIGHTS-ARCHIVE-001": "contract.validate_rights_evidence_archives",
        "V02-EQUIVALENCE-001": "contract.validate_equivalence_integrity",
        "V02-FAMILY-001": "contract.validate_source_family_integrity",
        "V02-SUPERSESSION-001": "contract.validate_supersession_chains",
        "V02-COUNT-001": "contract.validate_frozen_count_assertions",
        "V02-LOCATOR-001": "contract.validate_locator_rows",
        "V02-CITATION-001": "contract.validate_citation_rows",
        "V02-ASSET-AUDIT-001": "build_verification.audit_asset_build",
        "V02-BUILD-REPEAT-001": "build_verification.compare_asset_builds",
        "V02-FROZEN-BASELINE-001": "build_verification.verify_v01_baseline",
    }
    for rule_id, implementation_ref in expected.items():
        assert rules[rule_id]["implementation_ref"] == implementation_ref
        assert rules[rule_id]["default_severity"] == "blocking"
        assert rules[rule_id]["test_ids"]
