"""Deterministic identifiers for TPU database entities."""

from __future__ import annotations

import hashlib
import json


def _validate_namespace(namespace: str) -> str:
    if not isinstance(namespace, str):
        raise TypeError("namespace must be a string")
    if (
        not namespace
        or namespace != namespace.strip()
        or any(character.isspace() for character in namespace)
        or "/" in namespace
        or "\\" in namespace
    ):
        raise ValueError("namespace must be non-empty and contain no whitespace or path separators")
    return namespace


def stable_id(namespace: str, *parts: object) -> str:
    """Return a stable, namespaced ID for JSON-serializable *parts*.

    Mapping keys are sorted and Unicode is encoded directly before hashing.  The
    part tuple is serialized as a JSON array, so part boundaries and JSON types
    remain significant.
    """

    namespace = _validate_namespace(namespace)
    payload = json.dumps(
        parts,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{namespace}_{digest}"
