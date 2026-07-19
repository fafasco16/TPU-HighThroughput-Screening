"""Machine-readable quality-rule catalog validation for TPU database v0.2."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Set
from pathlib import Path
from typing import Any, Final

import yaml


ALLOWED_SEVERITIES: Final[frozenset[str]] = frozenset(
    {"blocking", "error", "warning", "info"}
)


class RuleCatalogValidationError(ValueError):
    """Raised when a quality-rule catalog cannot be audited deterministically."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise RuleCatalogValidationError(f"duplicate YAML key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_rule_catalog(path: str | Path) -> dict[str, Any]:
    """Load a UTF-8 rule catalog while preserving duplicate-key failures."""

    with Path(path).open(encoding="utf-8") as stream:
        document = yaml.load(stream, Loader=_UniqueKeyLoader)
    if not isinstance(document, dict):
        raise RuleCatalogValidationError("rule catalog YAML root must be a mapping")
    validate_rule_catalog(document)
    return document


def _require_nonempty_string(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise RuleCatalogValidationError(f"{label} must be a non-empty trimmed string")
    return value


def _require_string_list(value: object, *, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(value) != len(set(value))
    ):
        raise RuleCatalogValidationError(
            f"{label} must be a non-empty list of unique strings"
        )
    return value


def validate_rule_catalog(
    catalog: Mapping[str, Any],
    collected_test_ids: Set[str] | None = None,
) -> None:
    """Validate identifiers, severities, implementations and test mappings."""

    if not isinstance(catalog, Mapping):
        raise RuleCatalogValidationError("rule catalog must be a mapping")
    _require_nonempty_string(catalog.get("schema_version"), label="schema_version")
    _require_nonempty_string(catalog.get("catalog_version"), label="catalog_version")
    rules = catalog.get("rules")
    if not isinstance(rules, Mapping) or not rules:
        raise RuleCatalogValidationError("rules must be a non-empty mapping")

    available_tests = set(collected_test_ids) if collected_test_ids is not None else None
    for rule_id, rule in rules.items():
        rule_id = _require_nonempty_string(rule_id, label="rule_id")
        if rule_id == "UNSPECIFIED" or not rule_id.startswith("V02-"):
            raise RuleCatalogValidationError(
                f"rule_id {rule_id!r} must start with 'V02-' and cannot be UNSPECIFIED"
            )
        if not isinstance(rule, Mapping):
            raise RuleCatalogValidationError(f"rule {rule_id!r} must be a mapping")
        version = rule.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise RuleCatalogValidationError(
                f"rule {rule_id!r} version must be a positive integer"
            )
        _require_nonempty_string(rule.get("scope"), label=f"rule {rule_id} scope")
        severity = _require_nonempty_string(
            rule.get("default_severity"),
            label=f"rule {rule_id} default_severity",
        )
        if severity not in ALLOWED_SEVERITIES:
            raise RuleCatalogValidationError(
                f"rule {rule_id!r} has unknown severity {severity!r}"
            )
        implementation_ref = _require_nonempty_string(
            rule.get("implementation_ref"),
            label=f"rule {rule_id} implementation_ref",
        )
        if "." not in implementation_ref:
            raise RuleCatalogValidationError(
                f"rule {rule_id!r} implementation_ref must be module.symbol"
            )
        test_ids = _require_string_list(
            rule.get("test_ids"), label=f"rule {rule_id} test_ids"
        )
        profiles = rule.get("blocking_profiles", [])
        if severity == "blocking":
            _require_string_list(
                profiles, label=f"rule {rule_id} blocking_profiles"
            )
        elif not isinstance(profiles, list) or any(
            not isinstance(item, str) or not item.strip() for item in profiles
        ):
            raise RuleCatalogValidationError(
                f"rule {rule_id!r} blocking_profiles must be a list of strings"
            )
        if available_tests is not None:
            missing = sorted(set(test_ids) - available_tests)
            if missing:
                raise RuleCatalogValidationError(
                    f"rule {rule_id!r} references missing test id(s): {', '.join(missing)}"
                )


def rule_catalog_hash(catalog: Mapping[str, Any]) -> str:
    """Return the SHA-256 of the validated canonical JSON catalog."""

    validate_rule_catalog(catalog)
    payload = json.dumps(
        catalog,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
