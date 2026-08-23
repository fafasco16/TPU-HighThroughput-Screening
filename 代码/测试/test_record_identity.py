import math
from uuid import UUID

import pytest

from record_identity import (
    ROOT_NAMESPACE,
    canonical_identity_json,
    content_sha256,
    identity_key_sha256,
    stable_record_uid,
    stable_revision_id,
)


def test_uuid5_identity_is_canonical_and_snapshot_independent():
    key_a = {"source_scope_id": "数据集-A", "source_record_id": "样品-01"}
    key_b = {"source_record_id": "样品-01", "source_scope_id": "数据集-A"}

    uid_a = stable_record_uid("formulation", key_a)
    uid_b = stable_record_uid("formulation", key_b)

    assert uid_a == uid_b
    assert UUID(uid_a).version == 5
    assert UUID(uid_a).variant == "specified in RFC 4122"
    assert ROOT_NAMESPACE == UUID("1b3452dd-f305-5c9e-b55a-4f782ea67d10")


def test_unicode_nfc_is_applied_recursively_to_keys_and_values():
    composed = {"材料-é": "聚氨酯-é", "nested": [{"名称-é": "é"}]}
    decomposed = {"材料-e\u0301": "聚氨酯-e\u0301", "nested": [{"名称-e\u0301": "e\u0301"}]}

    assert canonical_identity_json(composed) == canonical_identity_json(decomposed)
    assert identity_key_sha256(composed) == identity_key_sha256(decomposed)
    assert stable_record_uid("material", composed) == stable_record_uid("material", decomposed)


def test_volatile_snapshot_path_and_display_metadata_are_not_identity_inputs():
    first_record = {
        "source_scope_id": "scope-1",
        "source_record_id": "row-7",
        "snapshot_id": "snapshot-old",
        "path": "下载/旧文件.xlsx",
        "display_name": "旧名称",
    }
    second_record = {
        "source_scope_id": "scope-1",
        "source_record_id": "row-7",
        "snapshot_id": "snapshot-new",
        "path": "归档/新文件.xlsx",
        "display_name": "新名称",
    }
    identity_fields = ("source_scope_id", "source_record_id")

    first_key = {field: first_record[field] for field in identity_fields}
    second_key = {field: second_record[field] for field in identity_fields}

    assert stable_record_uid("formulation", first_key) == stable_record_uid("formulation", second_key)


def test_entity_type_and_algorithm_version_define_separate_uid_namespaces():
    key = {"source_scope_id": "scope-1", "source_record_id": "row-7"}
    baseline = stable_record_uid("formulation", key)

    assert stable_record_uid("material", key) != baseline
    assert stable_record_uid("formulation", key, algorithm_version="uuid5-v2") != baseline


def test_canonical_identity_preserves_list_order_and_json_scalar_types():
    assert stable_record_uid("x", ["a", "b"]) != stable_record_uid("x", ["b", "a"])

    scalar_uids = {
        stable_record_uid("x", value)
        for value in (True, 1, 1.0, "1", None)
    }
    assert len(scalar_uids) == 5


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_identity_and_content_reject_non_finite_float(value):
    assert not math.isfinite(value)
    with pytest.raises(ValueError, match="finite"):
        canonical_identity_json({"value": value})
    with pytest.raises(ValueError, match="finite"):
        content_sha256({"value": value})


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({1: "not-a-string-key"}, "mapping key"),
        ({"nested": {"ok", "not-json"}}, "set"),
        ({"payload": b"bytes"}, "bytes"),
        ({"tuple": ("not", "a", "list")}, "unsupported"),
    ],
)
def test_identity_rejects_non_contract_key_shapes(value, message):
    with pytest.raises((TypeError, ValueError), match=message):
        canonical_identity_json(value)


def test_nfc_key_collision_is_rejected_instead_of_silently_overwriting():
    with pytest.raises(ValueError, match="collision"):
        canonical_identity_json({"é": 1, "e\u0301": 2})


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda: stable_record_uid("", {"id": "1"}), "entity_type"),
        (lambda: stable_record_uid(" formulation ", {"id": "1"}), "entity_type"),
        (lambda: stable_record_uid(1, {"id": "1"}), "entity_type"),
        (lambda: stable_record_uid("formulation", {"id": "1"}, algorithm_version=""), "algorithm_version"),
        (lambda: stable_revision_id("not-a-uuid", "v0.2", {}), "record_uid"),
        (lambda: stable_revision_id(123, "v0.2", {}), "record_uid"),
        (lambda: stable_revision_id(str(ROOT_NAMESPACE), "", {}), "schema_version"),
    ],
)
def test_identity_tokens_and_record_uuid_are_validated(call, message):
    with pytest.raises((TypeError, ValueError), match=message):
        call()


def test_revision_changes_without_reidentifying_entity():
    key = {"source_scope_id": "s", "source_record_id": "1"}
    uid = stable_record_uid("formulation", key)
    first = stable_revision_id(uid, "v0.2", {"nco_oh_ratio": 1.0})
    first_reordered = stable_revision_id(uid, "v0.2", {"nco_oh_ratio": 1.0})
    second = stable_revision_id(uid, "v0.2", {"nco_oh_ratio": 1.01})

    assert first == first_reordered
    assert first != second
    assert UUID(first).version == 5
    assert stable_record_uid("formulation", key) == uid


def test_revision_is_scoped_by_record_and_schema_version():
    content = {"nco_oh_ratio": 1.0}
    first_uid = stable_record_uid("formulation", {"id": "1"})
    second_uid = stable_record_uid("formulation", {"id": "2"})

    assert stable_revision_id(first_uid, "v0.2", content) != stable_revision_id(second_uid, "v0.2", content)
    assert stable_revision_id(first_uid, "v0.2", content) != stable_revision_id(first_uid, "v0.3", content)


def test_identity_and_content_hashes_are_full_lowercase_sha256():
    identity_digest = identity_key_sha256({"id": "样品-1"})
    content_digest = content_sha256({"value": 1.0})

    assert len(identity_digest) == len(content_digest) == 64
    assert identity_digest == identity_digest.lower()
    assert content_digest == content_digest.lower()
    assert all(character in "0123456789abcdef" for character in identity_digest + content_digest)


def test_uuid5_v1_known_vector_freezes_the_published_algorithm():
    key = {"source_scope_id": "数据集-A", "source_record_id": "样品-01"}
    uid = stable_record_uid("formulation", key)

    assert canonical_identity_json(key) == '{"source_record_id":"样品-01","source_scope_id":"数据集-A"}'
    assert identity_key_sha256(key) == "0218a35caa7cc78f8e52e2741d28a071cbd5e92b6aa4147d3e43720e9bb25a50"
    assert uid == "dfba2842-4972-58b0-aba4-602c64c697dd"
    assert content_sha256({"nco_oh_ratio": 1.0}) == "8d878bd57d14e738b8182a92a09dfbbaaa970b875b4439f86d43260f8015a3a2"
    assert stable_revision_id(uid, "v0.2", {"nco_oh_ratio": 1.0}) == "a49e5d16-8c9b-5b2a-be36-73c61a7ccb05"
