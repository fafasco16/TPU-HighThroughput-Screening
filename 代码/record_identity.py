"""Canonical cross-snapshot identities for TPU database records.

Callers must construct ``identity_key`` from the frozen identity fields for an
entity.  Snapshot IDs, file paths, display names, and other volatile metadata
therefore remain outside the key instead of being silently ignored here.
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Mapping
from uuid import UUID, uuid5


ROOT_NAMESPACE = UUID("1b3452dd-f305-5c9e-b55a-4f782ea67d10")


def _normalize(value: object) -> object:
    """Return the closed, JSON-compatible canonical identity representation."""

    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("float values in identity content must be finite")
        return value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        normalized_items: list[tuple[str, object]] = []
        normalized_keys: set[str] = set()
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("mapping key must be a string")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in normalized_keys:
                raise ValueError(f"mapping key normalization collision: {normalized_key!r}")
            normalized_keys.add(normalized_key)
            normalized_items.append((normalized_key, _normalize(item)))
        return {key: item for key, item in sorted(normalized_items, key=lambda pair: pair[0])}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        raise TypeError("set values are unsupported in identity content")
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError("bytes values are unsupported in identity content")
    raise TypeError(f"unsupported identity value type: {type(value).__name__}")


def _token(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = unicodedata.normalize("NFC", value)
    if not normalized or normalized != normalized.strip():
        raise ValueError(f"{field_name} must be a non-empty trimmed string")
    return normalized


def canonical_identity_json(value: object) -> str:
    """Serialize an identity/content value to canonical Unicode JSON."""

    normalized = _normalize(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def identity_key_sha256(identity_key: object) -> str:
    """Return the full SHA-256 digest of a canonical identity key."""

    return hashlib.sha256(canonical_identity_json(identity_key).encode("utf-8")).hexdigest()


def stable_record_uid(
    entity_type: str,
    identity_key: object,
    *,
    algorithm_version: str = "uuid5-v1",
) -> str:
    """Return a stable UUIDv5 scoped by algorithm version and entity type."""

    canonical_entity_type = _token(entity_type, "entity_type")
    canonical_algorithm_version = _token(algorithm_version, "algorithm_version")
    namespace = uuid5(
        ROOT_NAMESPACE,
        f"{canonical_algorithm_version}:{canonical_entity_type}",
    )
    return str(uuid5(namespace, canonical_identity_json(identity_key)))


def content_sha256(content: object) -> str:
    """Return the full SHA-256 digest of canonical revision content."""

    return hashlib.sha256(canonical_identity_json(content).encode("utf-8")).hexdigest()


def stable_revision_id(record_uid: str, schema_version: str, content: object) -> str:
    """Return a content-addressed UUIDv5 revision without reidentifying a record."""

    if not isinstance(record_uid, str):
        raise TypeError("record_uid must be a UUID string")
    try:
        record_uuid = UUID(record_uid)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("record_uid must be a valid UUID string") from error
    canonical_schema_version = _token(schema_version, "schema_version")
    return str(
        uuid5(
            record_uuid,
            f"{canonical_schema_version}:{content_sha256(content)}",
        )
    )
