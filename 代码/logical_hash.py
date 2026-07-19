"""Cross-environment logical hashes for typed rows, tables, and snapshots.

The byte format in this module is deliberately independent of pandas/Arrow
physical encodings.  Changing it requires a new ``ALGORITHM_VERSION``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import struct
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from numbers import Real
from typing import Final

import pandas as pd

from record_identity import canonical_identity_json


ALGORITHM_VERSION: Final = "tpu-logical-hash/1"

_SUPPORTED_TYPES: Final = frozenset(
    {
        "string",
        "integer",
        "float64",
        "boolean",
        "date",
        "datetime",
        "json",
        "binary",
        "missing",
    }
)
_LOWER_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class LogicalHashError(ValueError):
    """Raised when a value cannot be represented by the frozen hash format."""


def _frame(payload: bytes) -> bytes:
    """Prefix a payload with its unsigned, 64-bit, big-endian byte length."""

    if not isinstance(payload, bytes):
        raise TypeError("logical hash frames require bytes")
    return len(payload).to_bytes(8, "big") + payload


def _canonical_json_bytes(value: object) -> bytes:
    """Return canonical JSON bytes using the identity module's normalization."""

    try:
        # Round-tripping makes the accepted value domain exactly JSON and keeps
        # this module independent of the identity normalizer's Python objects.
        normalized = json.loads(canonical_identity_json(value))
        return json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise LogicalHashError(str(error)) from error


def _nfc_bytes(value: str) -> bytes:
    return unicodedata.normalize("NFC", value).encode("utf-8")


def _parse_date(value: object) -> date:
    try:
        parsed = date.fromisoformat(value) if isinstance(value, str) else value
    except ValueError as error:
        raise LogicalHashError("date value required") from error
    if not isinstance(parsed, date) or isinstance(parsed, datetime):
        raise LogicalHashError("date value required")
    return parsed


def _parse_datetime(value: object) -> datetime:
    try:
        parsed = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else value
        )
    except ValueError as error:
        raise LogicalHashError("timezone-aware datetime required") from error
    if (
        not isinstance(parsed, datetime)
        or parsed.tzinfo is None
        or parsed.utcoffset() is None
    ):
        raise LogicalHashError("timezone-aware datetime required")
    return parsed


def canonical_typed_value(value: object, type_name: str) -> bytes:
    """Encode one logical value with an unambiguous type tag and framing.

    ``None`` is the universal SQL-null marker.  A measured IEEE NaN is encoded
    as one canonical quiet-NaN bit pattern, while infinities are rejected.
    Missingness with a stated reason uses the separate ``missing`` logical type.
    """

    if type_name not in _SUPPORTED_TYPES:
        raise LogicalHashError(f"unsupported logical type: {type_name}")
    if value is None:
        return b"N"

    if type_name == "string":
        if not isinstance(value, str):
            raise LogicalHashError("string value required")
        return b"S" + _frame(_nfc_bytes(value))

    if type_name == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise LogicalHashError("integer value required")
        return b"I" + _frame(str(value).encode("ascii"))

    if type_name == "float64":
        if not isinstance(value, Real) or isinstance(value, bool):
            raise LogicalHashError("float64 value required")
        try:
            numeric = float(value)
        except (OverflowError, TypeError, ValueError) as error:
            raise LogicalHashError("float64 value required") from error
        if math.isnan(numeric):
            payload = bytes.fromhex("7ff8000000000000")
        elif not math.isfinite(numeric):
            raise LogicalHashError("float64 value must be finite or NaN")
        else:
            # IEEE-754 packing intentionally preserves the sign bit of zero.
            payload = struct.pack(">d", numeric)
        return b"F" + _frame(payload)

    if type_name == "boolean":
        if not isinstance(value, bool):
            raise LogicalHashError("boolean value required")
        return b"B1" if value else b"B0"

    if type_name == "date":
        return b"D" + _frame(_parse_date(value).isoformat().encode("ascii"))

    if type_name == "datetime":
        utc_text = (
            _parse_datetime(value)
            .astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        return b"T" + _frame(utc_text.encode("ascii"))

    if type_name == "binary":
        if not isinstance(value, bytes):
            raise LogicalHashError("bytes value required")
        return b"X" + _frame(base64.b64encode(value))

    if type_name == "missing":
        if (
            not isinstance(value, Mapping)
            or set(value) != {"missing_reason"}
            or not isinstance(value["missing_reason"], str)
        ):
            raise LogicalHashError("missing value requires one missing_reason")
        reason = value["missing_reason"]
        if not reason.strip():
            raise LogicalHashError("missing value requires non-empty missing_reason")
        return b"M" + _frame(_nfc_bytes(reason))

    # The supported-type guard makes this the json branch.
    return b"J" + _frame(_canonical_json_bytes(value))


def _validated_schema(schema: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    if isinstance(schema, (str, bytes)) or not isinstance(schema, Sequence):
        raise LogicalHashError("schema must be a sequence of (name, type) pairs")

    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in schema:
        if (
            not isinstance(entry, Sequence)
            or isinstance(entry, (str, bytes))
            or len(entry) != 2
        ):
            raise LogicalHashError("schema must contain (name, type) pairs")
        name, type_name = entry
        if not isinstance(name, str) or not name:
            raise LogicalHashError("schema column name must be a non-empty string")
        if name in seen:
            raise LogicalHashError(f"duplicate schema column: {name}")
        if not isinstance(type_name, str) or type_name not in _SUPPORTED_TYPES:
            raise LogicalHashError(f"unsupported logical type: {type_name}")
        seen.add(name)
        normalized.append((name, type_name))
    if not normalized:
        raise LogicalHashError("schema must not be empty")
    return tuple(normalized)


def row_logical_hash(
    row: Mapping[str, object], schema: Sequence[tuple[str, str]]
) -> str:
    """Hash a row in declared schema order, ignoring mapping iteration order."""

    if not isinstance(row, Mapping):
        raise LogicalHashError("row must be a mapping")
    validated_schema = _validated_schema(schema)
    missing = [name for name, _ in validated_schema if name not in row]
    if missing:
        raise LogicalHashError(f"row is missing schema columns: {', '.join(missing)}")
    payload = b"".join(
        _frame(_nfc_bytes(name))
        + _frame(canonical_typed_value(row[name], type_name))
        for name, type_name in validated_schema
    )
    return hashlib.sha256(payload).hexdigest()


def table_logical_hash(
    frame: pd.DataFrame,
    *,
    schema: Sequence[tuple[str, str]],
    sort_key: Sequence[str],
) -> str:
    """Hash a table after stable ordering by its explicitly declared key."""

    if not isinstance(frame, pd.DataFrame):
        raise LogicalHashError("frame must be a pandas DataFrame")
    validated_schema = _validated_schema(schema)
    schema_names = [name for name, _ in validated_schema]
    missing = [name for name in schema_names if name not in frame.columns]
    if missing:
        raise LogicalHashError(f"missing schema columns: {', '.join(missing)}")

    if isinstance(sort_key, (str, bytes)) or not isinstance(sort_key, Sequence):
        raise LogicalHashError("sort_key must be a sequence of column names")
    keys = list(sort_key)
    if not keys:
        raise LogicalHashError("sort_key must not be empty")
    if any(not isinstance(key, str) or not key for key in keys):
        raise LogicalHashError("sort_key requires non-empty string column names")
    if len(keys) != len(set(keys)):
        raise LogicalHashError("sort_key contains duplicate columns")
    missing_keys = [key for key in keys if key not in frame.columns]
    if missing_keys:
        raise LogicalHashError(f"missing sort-key columns: {', '.join(missing_keys)}")
    outside_schema = [key for key in keys if key not in schema_names]
    if outside_schema:
        raise LogicalHashError(
            f"sort-key columns outside schema: {', '.join(outside_schema)}"
        )

    try:
        ordered = frame.sort_values(keys, kind="mergesort", na_position="first")
    except (TypeError, ValueError) as error:
        raise LogicalHashError(f"sort_key values are not orderable: {error}") from error

    # The identity JSON domain intentionally excludes tuples, so turn the
    # immutable internal pairs back into their JSON-array representation.
    schema_document = [list(pair) for pair in validated_schema]
    schema_hash = hashlib.sha256(
        canonical_identity_json(schema_document).encode("utf-8")
    ).digest()
    row_hashes = b"".join(
        bytes.fromhex(row_logical_hash(row, validated_schema))
        for row in ordered.to_dict("records")
    )
    return hashlib.sha256(
        ALGORITHM_VERSION.encode("ascii") + schema_hash + row_hashes
    ).hexdigest()


def snapshot_logical_hash(tables: Mapping[str, tuple[int, str]]) -> str:
    """Hash table name, logical row count, and table hash in name order."""

    if not isinstance(tables, Mapping):
        raise LogicalHashError("tables must be a mapping")

    entries: list[tuple[str, int, str]] = []
    normalized_names: set[str] = set()
    for name, descriptor in tables.items():
        if not isinstance(name, str) or not name:
            raise LogicalHashError("table name must be a non-empty string")
        normalized_name = unicodedata.normalize("NFC", name)
        if normalized_name in normalized_names:
            raise LogicalHashError(
                f"duplicate table name after Unicode NFC normalization: {name}"
            )
        normalized_names.add(normalized_name)
        if (
            not isinstance(descriptor, (tuple, list))
            or len(descriptor) != 2
        ):
            raise LogicalHashError("table descriptor must be (row_count, hash)")
        count, digest = descriptor
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise LogicalHashError("table row count must be a non-negative integer")
        if not isinstance(digest, str) or _LOWER_SHA256.fullmatch(digest) is None:
            raise LogicalHashError(
                "table digest must be a 64-character lowercase SHA-256"
            )
        entries.append((normalized_name, count, digest))

    canonical = json.dumps(
        sorted(entries),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256((ALGORITHM_VERSION + canonical).encode("utf-8")).hexdigest()


__all__ = [
    "ALGORITHM_VERSION",
    "LogicalHashError",
    "canonical_typed_value",
    "row_logical_hash",
    "snapshot_logical_hash",
    "table_logical_hash",
]
