import math
import struct
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from logical_hash import (
    ALGORITHM_VERSION,
    LogicalHashError,
    canonical_typed_value,
    row_logical_hash,
    snapshot_logical_hash,
    table_logical_hash,
)


def test_algorithm_version_is_frozen():
    assert ALGORITHM_VERSION == "tpu-logical-hash/1"


def test_table_logical_hash_is_row_order_independent_with_declared_key():
    frame = pd.DataFrame([{"id": "b", "value": 2.0}, {"id": "a", "value": 1.0}])
    reversed_frame = frame.iloc[::-1].reset_index(drop=True)
    schema = [("id", "string"), ("value", "float64")]

    assert table_logical_hash(frame, schema=schema, sort_key=["id"]) == table_logical_hash(
        reversed_frame,
        schema=schema,
        sort_key=["id"],
    )


def test_row_hash_is_mapping_order_and_unicode_normalization_independent():
    schema = [("id", "string"), ("metadata", "json")]
    composed = {"id": "caf\u00e9", "metadata": {"\u540d\u79f0": "\u805a\u6c28\u916f", "tag": "\u00e9"}}
    decomposed = {"metadata": {"tag": "e\u0301", "\u540d\u79f0": "\u805a\u6c28\u916f"}, "id": "cafe\u0301"}

    assert row_logical_hash(composed, schema) == row_logical_hash(decomposed, schema)


@pytest.mark.parametrize(
    ("first_schema", "second_schema", "first_row", "second_row"),
    [
        (
            [("id", "string"), ("value", "float64")],
            [("value", "float64"), ("id", "string")],
            {"id": "a", "value": 1.0},
            {"id": "a", "value": 1.0},
        ),
        (
            [("id", "string"), ("value", "float64")],
            [("id", "string"), ("value", "float64")],
            {"id": "a", "value": 1.0},
            {"id": "a", "value": 1.01},
        ),
        (
            [("id", "string"), ("value", "integer")],
            [("id", "string"), ("value", "float64")],
            {"id": "a", "value": 1},
            {"id": "a", "value": 1.0},
        ),
    ],
)
def test_schema_column_order_value_and_type_change_row_or_table_hash(
    first_schema, second_schema, first_row, second_row
):
    first_frame = pd.DataFrame([first_row])
    second_frame = pd.DataFrame([second_row])

    assert table_logical_hash(
        first_frame, schema=first_schema, sort_key=["id"]
    ) != table_logical_hash(second_frame, schema=second_schema, sort_key=["id"])


def test_null_nan_and_missing_reason_are_distinct():
    null_value = canonical_typed_value(None, "float64")
    nan_value = canonical_typed_value(float("nan"), "float64")
    missing_value = canonical_typed_value(
        {"missing_reason": "not_reported"}, "missing"
    )

    assert len({null_value, nan_value, missing_value}) == 3
    assert canonical_typed_value(None, "missing") != missing_value


def test_all_nan_payloads_use_one_canonical_quiet_nan_bit_pattern():
    positive_signalling_nan = struct.unpack(">d", bytes.fromhex("7ff0000000000001"))[0]
    negative_quiet_nan = struct.unpack(">d", bytes.fromhex("fff8000000000042"))[0]
    expected = b"F" + (8).to_bytes(8, "big") + bytes.fromhex("7ff8000000000000")

    assert math.isnan(positive_signalling_nan)
    assert canonical_typed_value(positive_signalling_nan, "float64") == expected
    assert canonical_typed_value(negative_quiet_nan, "float64") == expected


def test_negative_and_positive_float_zero_remain_distinct():
    assert canonical_typed_value(-0.0, "float64") != canonical_typed_value(
        0.0, "float64"
    )


def test_datetime_is_normalized_to_the_same_utc_microsecond_instant():
    local = datetime(
        2026, 7, 19, 8, 9, 10, 123456, tzinfo=timezone(timedelta(hours=8))
    )
    utc = datetime(2026, 7, 19, 0, 9, 10, 123456, tzinfo=timezone.utc)

    assert canonical_typed_value(local, "datetime") == canonical_typed_value(
        utc, "datetime"
    )
    assert canonical_typed_value("2026-07-19T08:09:10.123456+08:00", "datetime") == (
        canonical_typed_value("2026-07-19T00:09:10.123456Z", "datetime")
    )


def test_textual_payloads_are_nfc_normalized_and_length_framed():
    composed = canonical_typed_value("\u00e9", "string")
    decomposed = canonical_typed_value("e\u0301", "string")
    missing_composed = canonical_typed_value({"missing_reason": "non_r\u00e9port\u00e9"}, "missing")
    missing_decomposed = canonical_typed_value(
        {"missing_reason": "non_re\u0301porte\u0301"}, "missing"
    )

    assert composed == decomposed
    assert composed == b"S" + (2).to_bytes(8, "big") + "\u00e9".encode("utf-8")
    assert missing_composed == missing_decomposed


def test_every_supported_non_null_type_has_a_distinct_tag_and_framed_payload():
    values = {
        "string": "1",
        "integer": 1,
        "float64": 1.0,
        "boolean": True,
        "date": date(2026, 7, 19),
        "datetime": datetime(2026, 7, 19, tzinfo=timezone.utc),
        "json": {"value": 1},
        "binary": b"1",
        "missing": {"missing_reason": "not_reported"},
    }
    tags = {
        logical_type: canonical_typed_value(value, logical_type)[:1]
        for logical_type, value in values.items()
    }

    assert tags == {
        "string": b"S",
        "integer": b"I",
        "float64": b"F",
        "boolean": b"B",
        "date": b"D",
        "datetime": b"T",
        "json": b"J",
        "binary": b"X",
        "missing": b"M",
    }
    for logical_type in values.keys() - {"boolean"}:
        encoded = canonical_typed_value(values[logical_type], logical_type)
        declared_length = int.from_bytes(encoded[1:9], "big")
        assert declared_length == len(encoded[9:])


@pytest.mark.parametrize(
    ("value", "type_name", "message"),
    [
        ("1", "unknown", "unsupported logical type"),
        (1, "string", "string value required"),
        (True, "integer", "integer value required"),
        ("1.0", "float64", "float64 value required"),
        (1, "boolean", "boolean value required"),
        (datetime(2026, 7, 19), "date", "date value required"),
        (datetime(2026, 7, 19), "datetime", "timezone-aware datetime required"),
        ("not-a-date", "date", "date value required"),
        (bytearray(b"a"), "binary", "bytes value required"),
        ({"missing_reason": "x", "extra": 1}, "missing", "one missing_reason"),
        ({"missing_reason": ""}, "missing", "non-empty missing_reason"),
        ({1: "non-string-key"}, "json", "mapping key must be a string"),
        ({"bad": float("nan")}, "json", "must be finite"),
    ],
)
def test_typed_values_reject_invalid_or_ambiguous_inputs(value, type_name, message):
    with pytest.raises(LogicalHashError, match=message):
        canonical_typed_value(value, type_name)


def test_invalid_datetime_text_is_reported_as_a_logical_hash_error():
    with pytest.raises(LogicalHashError, match="timezone-aware datetime required"):
        canonical_typed_value("not-a-datetime", "datetime")


def test_non_nan_infinite_float_is_rejected():
    with pytest.raises(LogicalHashError, match="finite or NaN"):
        canonical_typed_value(float("inf"), "float64")


def test_table_rejects_missing_columns_invalid_schema_and_sort_keys():
    frame = pd.DataFrame([{"id": "a"}])

    with pytest.raises(LogicalHashError, match="missing schema columns: value"):
        table_logical_hash(
            frame,
            schema=[("id", "string"), ("value", "float64")],
            sort_key=["id"],
        )
    with pytest.raises(LogicalHashError, match="duplicate schema column"):
        table_logical_hash(
            frame,
            schema=[("id", "string"), ("id", "string")],
            sort_key=["id"],
        )
    with pytest.raises(LogicalHashError, match="missing sort-key columns"):
        table_logical_hash(frame, schema=[("id", "string")], sort_key=["missing"])
    with pytest.raises(LogicalHashError, match="sort_key must not be empty"):
        table_logical_hash(frame, schema=[("id", "string")], sort_key=[])


@pytest.mark.parametrize(
    ("frame", "schema", "sort_key", "message"),
    [
        (pd.DataFrame({"id": ["a"]}), "not-a-schema", ["id"], "schema must be a sequence"),
        (pd.DataFrame({"id": ["a"]}), [("id",)], ["id"], "schema must contain"),
        (pd.DataFrame({"id": ["a"]}), [("", "string")], ["id"], "column name"),
        (pd.DataFrame({"id": ["a"]}), [("id", "uuid")], ["id"], "unsupported logical type"),
        (pd.DataFrame({"id": ["a"]}), [], ["id"], "schema must not be empty"),
        ("not-a-frame", [("id", "string")], ["id"], "pandas DataFrame"),
        (pd.DataFrame({"id": ["a"]}), [("id", "string")], "id", "sort_key must be a sequence"),
        (pd.DataFrame({"id": ["a"]}), [("id", "string")], [""], "non-empty string"),
        (pd.DataFrame({"id": ["a"]}), [("id", "string")], ["id", "id"], "duplicate columns"),
        (
            pd.DataFrame({"id": ["a"], "extra": [1]}),
            [("id", "string")],
            ["extra"],
            "outside schema",
        ),
    ],
)
def test_table_contract_rejects_malformed_arguments(frame, schema, sort_key, message):
    with pytest.raises(LogicalHashError, match=message):
        table_logical_hash(frame, schema=schema, sort_key=sort_key)


def test_row_requires_a_mapping():
    with pytest.raises(LogicalHashError, match="row must be a mapping"):
        row_logical_hash(["a"], [("id", "string")])


def test_row_missing_field_is_not_silently_hashed_as_null():
    with pytest.raises(LogicalHashError, match="row is missing schema columns: value"):
        row_logical_hash(
            {"id": "a"},
            [("id", "string"), ("value", "float64")],
        )


def test_table_reports_unorderable_key_values():
    frame = pd.DataFrame({"key": [{"a": 1}, [1]]})
    with pytest.raises(LogicalHashError, match="not orderable"):
        table_logical_hash(frame, schema=[("key", "json")], sort_key=["key"])


def test_snapshot_hash_is_mapping_order_independent_and_sorted_by_table_name():
    tables_a = {"z_table": (2, "a" * 64), "a_table": (1, "b" * 64)}
    tables_b = {"a_table": (1, "b" * 64), "z_table": (2, "a" * 64)}

    assert snapshot_logical_hash(tables_a) == snapshot_logical_hash(tables_b)


@pytest.mark.parametrize(
    "changed",
    [
        {"a_table": (2, "b" * 64)},
        {"a_table": (1, "c" * 64)},
        {"renamed_table": (1, "b" * 64)},
    ],
)
def test_snapshot_hash_changes_with_table_name_count_or_digest(changed):
    assert snapshot_logical_hash({"a_table": (1, "b" * 64)}) != snapshot_logical_hash(
        changed
    )


@pytest.mark.parametrize(
    ("tables", "message"),
    [
        ({"": (1, "a" * 64)}, "table name"),
        ({"table": (-1, "a" * 64)}, "non-negative integer"),
        ({"table": (True, "a" * 64)}, "non-negative integer"),
        ({"table": (1, "not-a-sha256")}, "64-character lowercase SHA-256"),
    ],
)
def test_snapshot_hash_rejects_invalid_manifest_entries(tables, message):
    with pytest.raises(LogicalHashError, match=message):
        snapshot_logical_hash(tables)


def test_snapshot_hash_rejects_non_mapping_bad_descriptor_and_nfc_collision():
    with pytest.raises(LogicalHashError, match="tables must be a mapping"):
        snapshot_logical_hash([])
    with pytest.raises(LogicalHashError, match="descriptor"):
        snapshot_logical_hash({"table": (1,)})
    with pytest.raises(LogicalHashError, match="Unicode NFC normalization"):
        snapshot_logical_hash(
            {"caf\u00e9": (1, "a" * 64), "cafe\u0301": (1, "b" * 64)}
        )
