"""Deterministic source-scope, citation, and rights-candidate governance.

This module deliberately stops before scientific-record ingestion.  It maps
already-discovered file assets to explicit source scopes, derives file scopes
and file locators, materializes the ledger citation registry, and emits only
fail-closed rights *candidates*.  Accessibility or a publication licence is
never promoted to permission for a data file, model, or derived release.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn
from uuid import UUID

import yaml

from record_identity import (
    canonical_identity_json,
    content_sha256,
    stable_record_uid,
)


SOURCE_COLUMNS = (
    "source_id",
    "record_uid",
    "source_kind",
    "canonical_identifier",
    "version_label",
    "title",
    "publisher_or_repository",
    "discovered_at",
    "registered_at",
    "notes",
)

SOURCE_SCOPE_COLUMNS = (
    "source_scope_id",
    "source_scope_key",
    "record_uid",
    "source_id",
    "parent_scope_id",
    "scope_kind",
    "canonical_identifier",
    "version_label",
    "independence_basis",
    "rights_scope_note",
    "created_at",
)

SOURCE_SCOPE_RELATION_COLUMNS = (
    "relation_id",
    "subject_scope_id",
    "object_scope_id",
    "relation_type",
    "evidence_locator_id",
    "evidence_summary",
    "review_status",
    "asserted_by",
    "asserted_at",
)

SOURCE_LOCATOR_COLUMNS = (
    "source_locator_id",
    "record_uid",
    "source_file_id",
    "locator_type",
    "locator_text",
    "locator_json",
    "locator_hash",
    "locator_hash_algorithm_version",
    "created_at",
)

CITATION_COLUMNS = (
    "citation_id",
    "record_uid",
    "source_id",
    "citation_key",
    "citation_type",
    "title",
    "authors_json",
    "issued_year",
    "doi",
    "canonical_url",
    "csl_json",
    "reference_text",
    "bibtex_text",
    "supersedes_citation_id",
    "accessed_at",
    "review_status",
)

CITATION_ASSIGNMENT_COLUMNS = (
    "citation_assignment_id",
    "citation_id",
    "target_uid",
    "expected_entity_type",
    "citation_role",
    "assignment_note",
    "assigned_by",
    "assigned_at",
    "review_status",
)

RIGHTS_ACTION_CANDIDATE_COLUMNS = (
    "candidate_id",
    "target_uid",
    "target_scope_key",
    "source_scope_id",
    "operation",
    "actor",
    "purpose",
    "rights_object_class",
    "context_profile_key",
    "candidate_status",
    "mapped_decision",
    "reason_code",
    "evidence_state",
    "rule_id",
    "rule_version",
    "reason_detail",
)

TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "source": SOURCE_COLUMNS,
    "source_scope": SOURCE_SCOPE_COLUMNS,
    "source_scope_relation": SOURCE_SCOPE_RELATION_COLUMNS,
    "source_locator": SOURCE_LOCATOR_COLUMNS,
    "citation": CITATION_COLUMNS,
    "citation_assignment": CITATION_ASSIGNMENT_COLUMNS,
    "rights_action_candidate": RIGHTS_ACTION_CANDIDATE_COLUMNS,
}

OUTPUT_FILENAMES = {
    "source": "v0.2来源.csv",
    "source_scope": "v0.2来源范围.csv",
    "source_scope_relation": "v0.2来源范围关系.csv",
    "source_locator": "v0.2来源定位.csv",
    "citation": "v0.2引用.csv",
    "citation_assignment": "v0.2引用分配.csv",
    "rights_action_candidate": "v0.2权利动作候选.csv",
}

_LOWER_KEY = re.compile(r"^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_SOURCE_KINDS = {
    "publication",
    "dataset",
    "repository",
    "software",
    "author_communication",
    "legal_terms",
    "other",
}
_SCOPE_KINDS = {
    "collection",
    "publication",
    "dataset_deposit",
    "dataset_version",
    "repository_release",
    "logical_partition",
    "source_record",
}
_RELATION_TYPES = {
    "subset_of",
    "supersedes",
    "mirror_of",
    "derived_from",
    "companion_to",
    "supplement_to",
    "same_study_as",
    "withdrawn_version_of",
}
_SOURCE_FAMILY_TYPES = {
    "independent_experiment",
    "companion_materials",
    "repository_mirror",
    "parent_dataset",
    "derived_dataset",
    "author_collection",
    "unresolved",
}
_AVAILABILITY = {
    "available",
    "metadata_only",
    "request_required",
    "unreachable",
    "withdrawn",
}
_CITATION_TYPES = {
    "article",
    "dataset",
    "software",
    "repository",
    "license",
    "book",
    "standard",
    "web_page",
    "personal_communication",
}
_AUTHOR_REQUIRED_CITATION_TYPES = {
    "article",
    "dataset",
    "software",
    "repository",
    "book",
    "standard",
}
_CITATION_ROLES = {
    "dataset",
    "original_measurement",
    "method",
    "software",
    "derived",
    "license_source",
    "rights_evidence",
}
_REVIEW_STATUS = {
    "not_reviewed",
    "pending",
    "in_review",
    "verified",
    "rejected",
    "superseded",
    "expired",
}
_RIGHTS_OPERATIONS = {
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
_RIGHTS_ACTORS = {
    "project_member",
    "institution_member",
    "approved_collaborator",
    "external_processor",
    "public",
    "commercial_partner",
}
_RIGHTS_PURPOSES = {
    "evidence_audit",
    "noncommercial_research",
    "teaching",
    "public_benchmark",
    "commercial_research",
    "product_service",
}
_RIGHTS_OBJECTS = {"metadata", "raw", "normalized", "derived", "aggregate", "model"}
_CANDIDATE_STATUSES = {"pending", "manual_review", "block"}
_MAPPED_DECISIONS = {"manual_review", "deny"}
_RIGHTS_REASON_CODES = {
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
}
_EVIDENCE_STATES = {
    "unreviewed",
    "evidence_missing",
    "captured_unverified",
    "scope_unresolved",
    "conflict_detected",
    "verified",
    "stale",
    "withdrawn",
}
_FORBIDDEN_OUTPUT_NAMES = {
    "02_暂存数据",
    "03_规范数据",
    "04_派生数据",
    "05_数据库快照",
    "06_审核导出",
}


class SourceGovernanceError(ValueError):
    """Structured fail-closed validation or build failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        context: str | None = None,
    ) -> None:
        self.message = message
        self.code = code
        self.context = context
        suffix = f" [{context}]" if context else ""
        super().__init__(f"{code}: {message}{suffix}")


class _UniqueKeyLoader(yaml.SafeLoader):
    """YAML loader rejecting duplicate keys at every mapping depth."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise SourceGovernanceError(
                "unhashable YAML mapping key",
                code="invalid_yaml_key",
            ) from error
        if duplicate:
            raise SourceGovernanceError(
                f"duplicate YAML key: {key!r}",
                code="duplicate_yaml_key",
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class SourceGovernanceBuild:
    """Deterministic provisional source-governance tables."""

    tables: dict[str, list[dict[str, object]]]
    columns: dict[str, tuple[str, ...]]
    logical_hash: str
    audit: dict[str, object]


def _fail(message: str, *, code: str, context: str | None = None) -> NoReturn:
    raise SourceGovernanceError(message, code=code, context=context)


def _nonempty(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        _fail(
            f"{label} must be a non-empty trimmed string",
            code="config_field_invalid",
            context=label,
        )
    return unicodedata.normalize("NFC", value)


def _enum(value: object, allowed: set[str], *, label: str) -> str:
    result = _nonempty(value, label=label)
    if result not in allowed:
        _fail(
            f"{label} has unsupported value {result!r}",
            code="config_enum_invalid",
            context=label,
        )
    return result


def _mapping_list(config: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = config.get(key)
    if not isinstance(value, list):
        _fail(f"{key} must be a list", code="config_collection_invalid", context=key)
    result: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            _fail(
                f"{key}[{index}] must be a mapping",
                code="config_entry_invalid",
                context=f"{key}[{index}]",
            )
        result.append(item)
    return result


def _unique_index(
    entries: Sequence[Mapping[str, Any]], key: str, *, collection: str
) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for position, entry in enumerate(entries):
        value = _nonempty(entry.get(key), label=f"{collection}[{position}].{key}")
        if value in index:
            _fail(
                f"duplicate {key}: {value!r}",
                code="config_identity_duplicate",
                context=collection,
            )
        index[value] = entry
    return index


def normalize_relative_path(value: object) -> str:
    """Return NFC POSIX project-relative path or fail on any escape."""

    if not isinstance(value, str):
        _fail("relative path must be a string", code="path_invalid")
    path = unicodedata.normalize("NFC", value).replace("\\", "/")
    if (
        not path
        or path.startswith("/")
        or path.startswith("//")
        or _WINDOWS_DRIVE.match(path)
    ):
        _fail(f"absolute or empty path is forbidden: {value!r}", code="path_escape")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        _fail(f"path contains empty or traversal component: {value!r}", code="path_escape")
    return "/".join(parts)


def _normalized_pattern(entry: Mapping[str, Any], index: int) -> str:
    match_type = entry.get("match_type")
    raw = _nonempty(entry.get("pattern"), label=f"path_mappings[{index}].pattern")
    if match_type == "regex":
        canonical = unicodedata.normalize("NFC", raw)
        if canonical != raw or not raw.startswith("^") or not raw.endswith("$"):
            _fail(
                "regex path mapping must be NFC and explicitly anchored",
                code="path_mapping_not_canonical",
                context=f"path_mappings[{index}]",
            )
        try:
            re.compile(raw)
        except re.error as error:
            raise SourceGovernanceError(
                f"invalid path mapping regex: {error}",
                code="path_mapping_invalid",
                context=f"path_mappings[{index}]",
            ) from error
        return raw
    trailing = raw.replace("\\", "/").endswith("/")
    normalized = normalize_relative_path(raw.replace("\\", "/").rstrip("/"))
    if match_type == "prefix":
        return normalized + "/"
    if trailing:
        _fail(
            "exact path mapping must not end with slash",
            code="path_mapping_invalid",
            context=f"path_mappings[{index}]",
        )
    return normalized


def _source_declarations(config: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    explicit = _mapping_list(config, "sources")
    citations = _mapping_list(config, "citations")
    derived: list[Mapping[str, Any]] = []
    for citation in citations:
        derived.append(
            {
                "source_key": citation.get("source_key"),
                "source_kind": citation.get("source_kind"),
                "canonical_identifier": citation.get("doi")
                or citation.get("canonical_url"),
                "version_label": citation.get("source_version_label", "published"),
                "title": citation.get("title"),
                "publisher_or_repository": citation.get(
                    "publisher_or_repository", "见来源台账"
                ),
                "source_family_key": citation.get("source_family_key"),
                "source_family_type": citation.get("source_family_type", "unresolved"),
                "availability_status": citation.get(
                    "availability_status", "metadata_only"
                ),
                "notes": f"台账引用[{citation.get('ledger_number')}]对应来源",
            }
        )
    result: list[Mapping[str, Any]] = []
    by_key: dict[str, Mapping[str, Any]] = {}
    identity_fields = (
        "source_kind",
        "canonical_identifier",
        "version_label",
        "source_family_key",
        "source_family_type",
    )
    for declaration in [*explicit, *derived]:
        key_value = declaration.get("source_key")
        key = key_value if isinstance(key_value, str) else ""
        previous = by_key.get(key)
        if previous is None:
            by_key[key] = declaration
            result.append(declaration)
            continue
        if any(
            declaration.get(field) != previous.get(field)
            for field in identity_fields
        ):
            _fail(
                "reused source_key has conflicting versioned source identity",
                code="source_declaration_conflict",
                context=key,
            )
        # Multiple citation revisions may describe the same versioned source.
        # The first declaration owns display metadata; identity fields must agree.
    return result


def _assert_acyclic(
    nodes: set[str], edges: Iterable[tuple[str, str]], *, code: str
) -> None:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for child, parent in edges:
        adjacency[child].add(parent)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, trail: list[str]) -> None:
        if node in visiting:
            start = trail.index(node) if node in trail else 0
            cycle = " -> ".join([*trail[start:], node])
            _fail(f"source scope graph contains cycle: {cycle}", code=code)
        if node in visited:
            return
        visiting.add(node)
        for target in sorted(adjacency.get(node, ())):
            visit(target, [*trail, node])
        visiting.remove(node)
        visited.add(node)

    for node in sorted(nodes):
        visit(node, [])


def load_source_scope_config(path: str | Path) -> dict[str, Any]:
    """Load and fully validate the UTF-8 source-scope YAML configuration."""

    source = Path(path)
    try:
        with source.open("r", encoding="utf-8") as stream:
            document = yaml.load(stream, Loader=_UniqueKeyLoader)
    except SourceGovernanceError:
        raise
    except (OSError, yaml.YAMLError) as error:
        raise SourceGovernanceError(
            f"cannot load source-scope configuration {source}: {error}",
            code="config_load_failed",
        ) from error
    if not isinstance(document, dict):
        _fail("configuration root must be a mapping", code="config_root_invalid")
    validate_source_scope_config(document)
    return document


def validate_source_scope_config(config: Mapping[str, Any]) -> None:
    """Validate identities, graph closure, citation ledger, and path rules."""

    if not isinstance(config, Mapping):
        _fail("configuration must be a mapping", code="config_root_invalid")
    if _nonempty(config.get("schema_version"), label="schema_version") != "v0.2":
        _fail("schema_version must be v0.2", code="config_version_invalid")
    _nonempty(config.get("config_version"), label="config_version")
    if (
        _nonempty(config.get("id_algorithm_version"), label="id_algorithm_version")
        != "uuid5-v1"
    ):
        _fail(
            "id_algorithm_version must be the published uuid5-v1 contract",
            code="id_algorithm_version_invalid",
        )
    if normalize_relative_path(config.get("discovery_root")) != "01_原始数据":
        _fail(
            "discovery_root must be the project-relative 01_原始数据",
            code="discovery_root_invalid",
        )
    observed_at = _nonempty(config.get("observed_at"), label="observed_at")
    if _UTC_TIMESTAMP.fullmatch(observed_at) is None:
        _fail("observed_at must be second-precision UTC", code="timestamp_invalid")
    _nonempty(config.get("asserted_by"), label="asserted_by")
    root_scope_key = _nonempty(config.get("root_scope_key"), label="root_scope_key")

    source_entries = _source_declarations(config)
    source_index = _unique_index(source_entries, "source_key", collection="sources")
    source_natural_keys: dict[tuple[str, str, str], str] = {}
    for key, source in source_index.items():
        if _LOWER_KEY.fullmatch(key) is None:
            _fail(f"invalid source key {key!r}", code="config_key_invalid")
        source_kind = _enum(
            source.get("source_kind"),
            _SOURCE_KINDS,
            label=f"source {key}.source_kind",
        )
        canonical_identifier = _nonempty(
            source.get("canonical_identifier"),
            label=f"source {key}.canonical_identifier",
        )
        version_label = _nonempty(
            source.get("version_label"), label=f"source {key}.version_label"
        )
        natural_key = (source_kind, canonical_identifier, version_label)
        if natural_key in source_natural_keys:
            _fail(
                "two source declarations share the contract natural key",
                code="source_natural_key_duplicate",
                context=f"{source_natural_keys[natural_key]} / {key}",
            )
        source_natural_keys[natural_key] = key
        _nonempty(source.get("title"), label=f"source {key}.title")
        _nonempty(
            source.get("publisher_or_repository"),
            label=f"source {key}.publisher_or_repository",
        )
        _nonempty(
            source.get("source_family_key"), label=f"source {key}.source_family_key"
        )
        _enum(
            source.get("source_family_type"),
            _SOURCE_FAMILY_TYPES,
            label=f"source {key}.source_family_type",
        )
        _enum(
            source.get("availability_status"),
            _AVAILABILITY,
            label=f"source {key}.availability_status",
        )

    scopes = _mapping_list(config, "scopes")
    scope_index = _unique_index(scopes, "source_scope_key", collection="scopes")
    if root_scope_key not in scope_index:
        _fail("root_scope_key is not defined", code="scope_root_missing")
    parent_edges: list[tuple[str, str]] = []
    scope_natural_keys: dict[tuple[str, str, str, str], str] = {}
    for key, scope in scope_index.items():
        if _LOWER_KEY.fullmatch(key) is None:
            _fail(f"invalid source scope key {key!r}", code="config_key_invalid")
        source_key = _nonempty(scope.get("source_key"), label=f"scope {key}.source_key")
        if source_key not in source_index:
            _fail(
                f"scope references unknown source {source_key!r}",
                code="scope_source_unknown",
                context=key,
            )
        scope_kind = _enum(
            scope.get("scope_kind"), _SCOPE_KINDS, label=f"scope {key}.scope_kind"
        )
        canonical_identifier = _nonempty(
            scope.get("canonical_identifier"),
            label=f"scope {key}.canonical_identifier",
        )
        version_label = _nonempty(
            scope.get("version_label"), label=f"scope {key}.version_label"
        )
        scope_natural_key = (
            source_key,
            scope_kind,
            canonical_identifier,
            version_label,
        )
        if scope_natural_key in scope_natural_keys:
            _fail(
                "two source scopes share the contract natural key",
                code="scope_natural_key_duplicate",
                context=f"{scope_natural_keys[scope_natural_key]} / {key}",
            )
        scope_natural_keys[scope_natural_key] = key
        parent = scope.get("parent_scope_key")
        if key == root_scope_key:
            if parent is not None:
                parent_edges.append((key, _nonempty(parent, label=f"scope {key}.parent")))
        else:
            parent_key = _nonempty(parent, label=f"scope {key}.parent_scope_key")
            if parent_key not in scope_index:
                _fail(
                    f"scope parent {parent_key!r} is undefined",
                    code="scope_parent_unknown",
                    context=key,
                )
            parent_edges.append((key, parent_key))
        evidence_state = scope.get("rights_evidence_state", "evidence_missing")
        _enum(evidence_state, _EVIDENCE_STATES, label=f"scope {key}.rights_evidence_state")
        override = scope.get("rights_candidate_status")
        if override is not None:
            _enum(override, _CANDIDATE_STATUSES, label=f"scope {key}.rights_candidate_status")
            _enum(
                scope.get("rights_reason_code"),
                _RIGHTS_REASON_CODES,
                label=f"scope {key}.rights_reason_code",
            )

    if scope_index[root_scope_key].get("parent_scope_key") is not None:
        _fail("configured root scope must not have a parent", code="scope_root_invalid")
    _assert_acyclic(set(scope_index), parent_edges, code="scope_graph_cycle")
    parents = {child: parent for child, parent in parent_edges}
    for scope_key in sorted(scope_index):
        cursor = scope_key
        while cursor != root_scope_key:
            if cursor not in parents:
                _fail(
                    f"scope {scope_key!r} cannot reach configured root",
                    code="scope_unreachable",
                    context=scope_key,
                )
            cursor = parents[cursor]

    relations = _mapping_list(config, "relations")
    _unique_index(relations, "relation_key", collection="relations")
    relation_edges: list[tuple[str, str]] = []
    for index, relation in enumerate(relations):
        subject = _nonempty(
            relation.get("subject_scope_key"),
            label=f"relations[{index}].subject_scope_key",
        )
        object_ = _nonempty(
            relation.get("object_scope_key"),
            label=f"relations[{index}].object_scope_key",
        )
        if subject not in scope_index or object_ not in scope_index:
            _fail(
                "scope relation references unknown scope",
                code="scope_relation_unknown",
                context=f"relations[{index}]",
            )
        if subject == object_:
            _fail("self scope relation is forbidden", code="scope_relation_self")
        _enum(
            relation.get("relation_type"),
            _RELATION_TYPES,
            label=f"relations[{index}].relation_type",
        )
        _nonempty(
            relation.get("evidence_summary"),
            label=f"relations[{index}].evidence_summary",
        )
        _enum(
            relation.get("review_status"),
            _REVIEW_STATUS,
            label=f"relations[{index}].review_status",
        )
        relation_edges.append((subject, object_))
    _assert_acyclic(
        set(scope_index),
        [*parent_edges, *relation_edges],
        code="scope_graph_cycle",
    )

    mappings = _mapping_list(config, "path_mappings")
    _unique_index(mappings, "mapping_id", collection="path_mappings")
    if not mappings:
        _fail("path_mappings must not be empty", code="path_mapping_missing")
    for index, mapping in enumerate(mappings):
        priority = mapping.get("priority")
        if not isinstance(priority, int) or isinstance(priority, bool) or priority < 0:
            _fail(
                "path mapping priority must be a non-negative integer",
                code="path_mapping_invalid",
                context=f"path_mappings[{index}]",
            )
        match_type = mapping.get("match_type")
        if match_type not in {"exact", "prefix", "regex"}:
            _fail(
                "path mapping match_type must be exact, prefix, or regex",
                code="path_mapping_invalid",
                context=f"path_mappings[{index}]",
            )
        normalized = _normalized_pattern(mapping, index)
        if normalized != mapping.get("pattern"):
            _fail(
                "path mapping pattern must already be NFC POSIX canonical",
                code="path_mapping_not_canonical",
                context=f"path_mappings[{index}]",
            )
        target = _nonempty(
            mapping.get("source_scope_key"),
            label=f"path_mappings[{index}].source_scope_key",
        )
        if target not in scope_index:
            _fail(
                f"path mapping references unknown scope {target!r}",
                code="path_mapping_scope_unknown",
                context=f"path_mappings[{index}]",
            )

    citations = _mapping_list(config, "citations")
    numbers = [item.get("ledger_number") for item in citations]
    if numbers != list(range(1, 53)):
        _fail(
            "citations must preserve ledger numbers 1 through 52 in order",
            code="citation_ledger_incomplete",
        )
    citation_index = _unique_index(citations, "citation_key", collection="citations")
    citation_supersession_edges: list[tuple[str, str]] = []
    for index, citation in enumerate(citations):
        key = _nonempty(citation.get("citation_key"), label=f"citations[{index}].citation_key")
        if not key.startswith("ledger-"):
            _fail("citation_key must start with ledger-", code="citation_key_invalid", context=key)
        _enum(
            citation.get("citation_type"),
            _CITATION_TYPES,
            label=f"citation {key}.citation_type",
        )
        _nonempty(citation.get("title"), label=f"citation {key}.title")
        target = _nonempty(
            citation.get("target_scope_key"), label=f"citation {key}.target_scope_key"
        )
        if target not in scope_index:
            _fail(
                f"citation target scope {target!r} is undefined",
                code="citation_target_unknown",
                context=key,
            )
        roles = citation.get("citation_roles")
        if (
            not isinstance(roles, list)
            or not roles
            or len(roles) != len(set(roles))
            or any(role not in _CITATION_ROLES for role in roles)
        ):
            _fail(
                "citation_roles must be a non-empty unique role list",
                code="citation_roles_invalid",
                context=key,
            )
        year = citation.get("issued_year")
        if year is not None and (
            not isinstance(year, int) or isinstance(year, bool) or not 1600 <= year <= 3000
        ):
            _fail("citation year is invalid", code="citation_year_invalid", context=key)
        authors = citation.get("authors", [])
        if (
            not isinstance(authors, list)
            or any(
                not isinstance(item, str)
                or not item.strip()
                or item != item.strip()
                or item.casefold() == "et al."
                for item in authors
            )
        ):
            _fail("citation authors must be a string list", code="citation_authors_invalid", context=key)
        if citation.get("citation_type") in _AUTHOR_REQUIRED_CITATION_TYPES and not authors:
            _fail(
                "scholarly citation requires verified authors",
                code="citation_authors_missing",
                context=key,
            )
        reference_text = _nonempty(
            citation.get("reference_text"),
            label=f"citation {key}.reference_text",
        )
        if reference_text != reference_text.strip() or "*" in reference_text:
            _fail(
                "reference_text must be trimmed plain reference-list text",
                code="citation_reference_text_invalid",
                context=key,
            )
        title = str(citation["title"])
        if title.casefold() not in reference_text.casefold():
            _fail(
                "reference_text must contain the formal title",
                code="citation_reference_text_invalid",
                context=key,
            )
        doi = citation.get("doi")
        if isinstance(doi, str) and doi.strip() and doi.casefold() not in reference_text.casefold():
            _fail(
                "reference_text must contain the configured DOI",
                code="citation_reference_text_invalid",
                context=key,
            )
        bibtex = citation.get("bibtex_text")
        if bibtex is not None:
            if (
                not isinstance(bibtex, str)
                or not bibtex.strip()
                or not bibtex.lstrip().startswith("@")
                or "```" in bibtex
                or "**" in bibtex
                or re.search(r"\[[^\]]+\]\([^\)]+\)", bibtex) is not None
            ):
                _fail(
                    "bibtex_text must contain BibTeX without Markdown wrappers",
                    code="citation_bibtex_invalid",
                    context=key,
                )
        supersedes_key = citation.get("supersedes_citation_key")
        if supersedes_key is not None:
            supersedes_key = _nonempty(
                supersedes_key,
                label=f"citation {key}.supersedes_citation_key",
            )
            if supersedes_key not in citation_index:
                _fail(
                    "citation supersedes an unknown citation key",
                    code="citation_supersedes_unknown",
                    context=key,
                )
            if supersedes_key == key:
                _fail(
                    "citation cannot supersede itself",
                    code="citation_supersedes_self",
                    context=key,
                )
            if citation_index[supersedes_key].get("source_key") != citation.get("source_key"):
                _fail(
                    "citation supersession must remain within one versioned source",
                    code="citation_supersedes_source_mismatch",
                    context=key,
                )
            citation_supersession_edges.append((key, supersedes_key))

    _assert_acyclic(
        set(citation_index),
        citation_supersession_edges,
        code="citation_supersession_cycle",
    )

    actions = _mapping_list(config, "rights_actions")
    if not actions:
        _fail("rights_actions must not be empty", code="rights_actions_missing")
    identities: set[tuple[str, str, str, str, str]] = set()
    for index, action in enumerate(actions):
        operation = _enum(
            action.get("operation"), _RIGHTS_OPERATIONS, label=f"rights_actions[{index}].operation"
        )
        actor = _enum(action.get("actor"), _RIGHTS_ACTORS, label=f"rights_actions[{index}].actor")
        purpose = _enum(
            action.get("purpose"), _RIGHTS_PURPOSES, label=f"rights_actions[{index}].purpose"
        )
        object_class = _enum(
            action.get("rights_object_class"),
            _RIGHTS_OBJECTS,
            label=f"rights_actions[{index}].rights_object_class",
        )
        context_key = _nonempty(
            action.get("context_profile_key"),
            label=f"rights_actions[{index}].context_profile_key",
        )
        identity = (operation, actor, purpose, object_class, context_key)
        if identity in identities:
            _fail("duplicate rights action axes", code="rights_action_duplicate")
        identities.add(identity)
        status = _enum(
            action.get("candidate_status"),
            _CANDIDATE_STATUSES,
            label=f"rights_actions[{index}].candidate_status",
        )
        decision = _enum(
            action.get("mapped_decision"),
            _MAPPED_DECISIONS,
            label=f"rights_actions[{index}].mapped_decision",
        )
        if status == "block" and decision != "deny":
            _fail("block candidate must map to deny", code="rights_action_mapping_invalid")
        if status != "block" and decision != "manual_review":
            _fail(
                "pending/manual_review candidates must map to manual_review",
                code="rights_action_mapping_invalid",
            )
        _enum(
            action.get("reason_code"),
            _RIGHTS_REASON_CODES,
            label=f"rights_actions[{index}].reason_code",
        )
        _nonempty(action.get("reason_detail"), label=f"rights_actions[{index}].reason_detail")


def _mapping_matches(path: str, mapping: Mapping[str, Any]) -> bool:
    canonical_path = path.casefold()
    pattern = str(mapping["pattern"]).casefold()
    if mapping["match_type"] == "exact":
        return canonical_path == pattern
    if mapping["match_type"] == "regex":
        return re.fullmatch(str(mapping["pattern"]), path, flags=re.IGNORECASE) is not None
    return canonical_path.startswith(pattern)


def _resolve_asset_scope_validated(relative_path: object, config: Mapping[str, Any]) -> str:
    canonical = normalize_relative_path(relative_path)
    matches = [
        item
        for item in config["path_mappings"]
        if _mapping_matches(canonical, item)
    ]
    if not matches:
        _fail(
            f"no source scope mapping for {canonical!r}",
            code="unknown_asset_scope",
            context=canonical,
        )
    highest = max(int(item["priority"]) for item in matches)
    winners = [item for item in matches if int(item["priority"]) == highest]
    if len(winners) != 1:
        ids = ", ".join(sorted(str(item["mapping_id"]) for item in winners))
        _fail(
            f"same-priority source scope mappings conflict: {ids}",
            code="ambiguous_asset_scope",
            context=canonical,
        )
    return str(winners[0]["source_scope_key"])


def resolve_asset_scope(relative_path: object, config: Mapping[str, Any]) -> str:
    """Resolve one asset path to exactly one configured non-file parent scope."""

    validate_source_scope_config(config)
    return _resolve_asset_scope_validated(relative_path, config)


def _ids(algorithm_version: str, entity: str, identity: object) -> tuple[str, str]:
    primary = stable_record_uid(
        f"{entity}_id", identity, algorithm_version=algorithm_version
    )
    record_uid = stable_record_uid(
        entity, identity, algorithm_version=algorithm_version
    )
    return primary, record_uid


def _build_sources(
    config: Mapping[str, Any], source_entries: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    algorithm = str(config["id_algorithm_version"])
    observed_at = str(config["observed_at"])
    rows: list[dict[str, object]] = []
    by_key: dict[str, dict[str, object]] = {}
    for source in source_entries:
        source_key = str(source["source_key"])
        identity = {
            "source_kind": source["source_kind"],
            "canonical_identifier": source["canonical_identifier"],
            "version_label": source["version_label"],
        }
        source_id, record_uid = _ids(algorithm, "source", identity)
        row: dict[str, object] = {
            "source_id": source_id,
            "record_uid": record_uid,
            "source_kind": source["source_kind"],
            "canonical_identifier": source["canonical_identifier"],
            "version_label": source["version_label"],
            "title": source["title"],
            "publisher_or_repository": source["publisher_or_repository"],
            "discovered_at": observed_at,
            "registered_at": observed_at,
            "notes": source.get("notes"),
        }
        rows.append(row)
        by_key[source_key] = row
    rows.sort(key=lambda row: str(row["source_id"]))
    return rows, by_key


def _build_configured_scopes(
    config: Mapping[str, Any],
    sources: Mapping[str, Mapping[str, object]],
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    algorithm = str(config["id_algorithm_version"])
    observed_at = str(config["observed_at"])
    configured = {str(item["source_scope_key"]): item for item in config["scopes"]}
    rows_by_key: dict[str, dict[str, object]] = {}
    for scope_key in sorted(configured):
        scope = configured[scope_key]
        source = sources[str(scope["source_key"])]
        identity = {"source_scope_key": scope_key}
        source_scope_id, record_uid = _ids(algorithm, "source_scope", identity)
        rows_by_key[scope_key] = {
            "source_scope_id": source_scope_id,
            "source_scope_key": scope_key,
            "record_uid": record_uid,
            "source_id": source["source_id"],
            "parent_scope_id": None,
            "scope_kind": scope["scope_kind"],
            "canonical_identifier": scope["canonical_identifier"],
            "version_label": scope["version_label"],
            "independence_basis": scope.get("independence_basis"),
            "rights_scope_note": scope.get(
                "rights_scope_note",
                "仅登记范围；不得从论文许可或可访问性自动推断数据文件动作权限。",
            ),
            "created_at": observed_at,
        }
    for scope_key, scope in configured.items():
        parent_key = scope.get("parent_scope_key")
        if (
            parent_key is not None
            and configured[str(parent_key)]["source_key"] == scope["source_key"]
        ):
            rows_by_key[scope_key]["parent_scope_id"] = rows_by_key[str(parent_key)][
                "source_scope_id"
            ]
    rows = sorted(rows_by_key.values(), key=lambda row: str(row["source_scope_key"]))
    return rows, rows_by_key


def _source_file_id(asset: Mapping[str, object], path: str) -> str:
    values = [asset.get("source_file_id"), asset.get("source_file_uid")]
    present = [value for value in values if isinstance(value, str) and value.strip()]
    if not present:
        _fail(
            "asset requires source_file_id or source_file_uid",
            code="asset_identity_missing",
            context=path,
        )
    if len(set(present)) > 1:
        _fail(
            "source_file_id and source_file_uid disagree",
            code="asset_identity_conflict",
            context=path,
        )
    source_file_id = present[0]
    try:
        parsed = UUID(source_file_id)
    except (AttributeError, TypeError, ValueError) as error:
        raise SourceGovernanceError(
            "source_file identity must be a UUID",
            code="asset_identity_invalid",
            context=path,
        ) from error
    if parsed.version != 5:
        _fail(
            "source_file identity must use UUIDv5",
            code="asset_identity_invalid",
            context=path,
        )
    return source_file_id


def _derive_file_scope(
    config: Mapping[str, Any],
    parent_key: str,
    parent: Mapping[str, object],
    path: str,
) -> dict[str, object]:
    algorithm = str(config["id_algorithm_version"])
    scope_key = f"file::{parent_key}::{path}"
    identity = {"source_scope_key": scope_key}
    source_scope_id, record_uid = _ids(algorithm, "source_scope", identity)
    return {
        "source_scope_id": source_scope_id,
        "source_scope_key": scope_key,
        "record_uid": record_uid,
        "source_id": parent["source_id"],
        "parent_scope_id": parent["source_scope_id"],
        "scope_kind": "file",
        "canonical_identifier": f"file:{parent_key}:{path}",
        "version_label": parent["version_label"],
        "independence_basis": None,
        "rights_scope_note": (
            "由父范围和规范POSIX相对路径自动派生；父论文或仓库权利不得自动扩展到本文件。"
        ),
        "created_at": config["observed_at"],
    }


def _derive_locator(
    config: Mapping[str, Any], asset: Mapping[str, object], path: str
) -> dict[str, object]:
    source_file_id = _source_file_id(asset, path)
    locator_payload = {"relative_path": path}
    locator_json = canonical_identity_json(locator_payload)
    locator_hash = hashlib.sha256(locator_json.encode("utf-8")).hexdigest()
    identity = {"source_file_id": source_file_id, "locator_hash": locator_hash}
    source_locator_id, record_uid = _ids(
        str(config["id_algorithm_version"]), "source_locator", identity
    )
    return {
        "source_locator_id": source_locator_id,
        "record_uid": record_uid,
        "source_file_id": source_file_id,
        "locator_type": "file",
        "locator_text": f"file:{path}",
        "locator_json": locator_json,
        "locator_hash": locator_hash,
        "locator_hash_algorithm_version": "tpu-locator-json/1",
        "created_at": config["observed_at"],
    }


def _build_relations(
    config: Mapping[str, Any], scopes: Mapping[str, Mapping[str, object]]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    algorithm = str(config["id_algorithm_version"])
    relation_specs: list[dict[str, object]] = [dict(relation) for relation in config["relations"]]
    configured = {
        str(item["source_scope_key"]): item for item in config["scopes"]
    }
    for scope_key, scope in configured.items():
        parent_key = scope.get("parent_scope_key")
        if (
            parent_key is None
            or configured[str(parent_key)]["source_key"] == scope["source_key"]
        ):
            continue
        relation_specs.append(
            {
                "subject_scope_key": scope_key,
                "object_scope_key": str(parent_key),
                "relation_type": "subset_of",
                "evidence_summary": (
                    "配置层级迁移：跨版本化来源的导航父级不得写入parent_scope_id，"
                    "改以显式subset_of关系保留来源链。"
                ),
                "review_status": "not_reviewed",
            }
        )
    seen_edges: set[tuple[str, str, str]] = set()
    for relation in relation_specs:
        subject_scope_id = str(
            scopes[str(relation["subject_scope_key"])]["source_scope_id"]
        )
        object_scope_id = str(
            scopes[str(relation["object_scope_key"])]["source_scope_id"]
        )
        edge = (subject_scope_id, object_scope_id, str(relation["relation_type"]))
        if edge in seen_edges:
            _fail(
                "duplicate materialized source scope relation",
                code="scope_relation_duplicate",
                context=str(edge),
            )
        seen_edges.add(edge)
        identity = {
            "subject_scope_id": subject_scope_id,
            "object_scope_id": object_scope_id,
            "relation_type": relation["relation_type"],
        }
        relation_id = stable_record_uid(
            "source_scope_relation_id", identity, algorithm_version=algorithm
        )
        rows.append(
            {
                "relation_id": relation_id,
                "subject_scope_id": subject_scope_id,
                "object_scope_id": object_scope_id,
                "relation_type": relation["relation_type"],
                "evidence_locator_id": None,
                "evidence_summary": relation["evidence_summary"],
                "review_status": relation["review_status"],
                "asserted_by": config["asserted_by"],
                "asserted_at": config["observed_at"],
            }
        )
    rows.sort(key=lambda row: str(row["relation_id"]))
    return rows


def _build_citations(
    config: Mapping[str, Any],
    sources: Mapping[str, Mapping[str, object]],
    scopes: Mapping[str, Mapping[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    algorithm = str(config["id_algorithm_version"])
    citation_rows: list[dict[str, object]] = []
    assignment_rows: list[dict[str, object]] = []
    citation_ids = {
        str(citation["citation_key"]): _ids(
            algorithm,
            "citation",
            {"citation_key": str(citation["citation_key"])},
        )
        for citation in config["citations"]
    }
    for citation in config["citations"]:
        citation_key = str(citation["citation_key"])
        citation_id, record_uid = citation_ids[citation_key]
        csl: dict[str, object] = {
            "id": citation_key,
            "type": citation["citation_type"],
            "title": citation["title"],
        }
        if citation.get("authors"):
            csl["author"] = [{"literal": author} for author in citation["authors"]]
        if citation.get("issued_year") is not None:
            csl["issued"] = {"date-parts": [[citation["issued_year"]]]}
        if citation.get("doi"):
            csl["DOI"] = citation["doi"]
        if citation.get("canonical_url"):
            csl["URL"] = citation["canonical_url"]
        citation_rows.append(
            {
                "citation_id": citation_id,
                "record_uid": record_uid,
                "source_id": sources[str(citation["source_key"])]["source_id"],
                "citation_key": citation_key,
                "citation_type": citation["citation_type"],
                "title": citation["title"],
                "authors_json": canonical_identity_json(citation.get("authors", [])),
                "issued_year": citation.get("issued_year"),
                "doi": citation.get("doi"),
                "canonical_url": citation.get("canonical_url"),
                "csl_json": canonical_identity_json(csl),
                "reference_text": citation["reference_text"],
                "bibtex_text": citation.get("bibtex_text"),
                "supersedes_citation_id": (
                    citation_ids[str(citation["supersedes_citation_key"])][0]
                    if citation.get("supersedes_citation_key") is not None
                    else None
                ),
                "accessed_at": citation.get("accessed_at"),
                "review_status": citation.get("review_status", "verified"),
            }
        )
        target_scope = scopes[str(citation["target_scope_key"])]
        for role in citation["citation_roles"]:
            assignment_identity = {
                "citation_key": citation_key,
                "target_scope_key": citation["target_scope_key"],
                "citation_role": role,
            }
            assignment_id = stable_record_uid(
                "citation_assignment_id",
                assignment_identity,
                algorithm_version=algorithm,
            )
            assignment_rows.append(
                {
                    "citation_assignment_id": assignment_id,
                    "citation_id": citation_id,
                    "target_uid": target_scope["record_uid"],
                    "expected_entity_type": "source_scope",
                    "citation_role": role,
                    "assignment_note": f"台账[{citation['ledger_number']}]引用角色",
                    "assigned_by": config["asserted_by"],
                    "assigned_at": config["observed_at"],
                    "review_status": citation.get("review_status", "verified"),
                }
            )
    citation_rows.sort(key=lambda row: str(row["citation_key"]))
    assignment_rows.sort(
        key=lambda row: (
            str(row["citation_id"]),
            str(row["target_uid"]),
            str(row["citation_role"]),
        )
    )
    return citation_rows, assignment_rows


def _max_candidate_status(left: str, right: str) -> str:
    order = {"pending": 0, "manual_review": 1, "block": 2}
    return left if order[left] >= order[right] else right


def _build_rights_candidates(
    config: Mapping[str, Any],
    scope_rows: Sequence[Mapping[str, object]],
    configured_scopes: Mapping[str, Mapping[str, Any]],
    configured_sources: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, object]]:
    algorithm = str(config["id_algorithm_version"])
    rows: list[dict[str, object]] = []
    for scope in scope_rows:
        scope_key = str(scope["source_scope_key"])
        if scope_key.startswith("file::"):
            parent_key = scope_key.split("::", 2)[1]
            policy_scope = configured_scopes[parent_key]
        else:
            policy_scope = configured_scopes[scope_key]
        source_config = configured_sources[str(policy_scope["source_key"])]
        availability_status = str(
            policy_scope.get(
                "availability_status",
                source_config.get("availability_status", "metadata_only"),
            )
        )
        override_status = policy_scope.get("rights_candidate_status")
        override_reason = policy_scope.get("rights_reason_code")
        evidence_state = str(
            policy_scope.get("rights_evidence_state", "evidence_missing")
        )
        for action in config["rights_actions"]:
            status = str(action["candidate_status"])
            reason_code = str(action["reason_code"])
            reason_detail = str(action["reason_detail"])
            if availability_status == "withdrawn":
                status = "block"
                reason_code = "ACCESS_WITHDRAWN"
                reason_detail = "来源已撤回；在重新取得作者授权与范围证据前阻断该动作。"
                evidence_state = "withdrawn"
            elif override_status is not None:
                status = _max_candidate_status(status, str(override_status))
                if status == override_status and override_reason is not None:
                    reason_code = str(override_reason)
            mapped_decision = "deny" if status == "block" else "manual_review"
            identity = {
                "target_scope_key": scope_key,
                "operation": action["operation"],
                "actor": action["actor"],
                "purpose": action["purpose"],
                "rights_object_class": action["rights_object_class"],
                "context_profile_key": action["context_profile_key"],
            }
            rows.append(
                {
                    "candidate_id": stable_record_uid(
                        "rights_action_candidate_id",
                        identity,
                        algorithm_version=algorithm,
                    ),
                    "target_uid": scope["record_uid"],
                    "target_scope_key": scope_key,
                    "source_scope_id": scope["source_scope_id"],
                    "operation": action["operation"],
                    "actor": action["actor"],
                    "purpose": action["purpose"],
                    "rights_object_class": action["rights_object_class"],
                    "context_profile_key": action["context_profile_key"],
                    "candidate_status": status,
                    "mapped_decision": mapped_decision,
                    "reason_code": reason_code,
                    "evidence_state": evidence_state,
                    "rule_id": "V02-RIGHTS-CANDIDATE-001",
                    "rule_version": "1",
                    "reason_detail": reason_detail,
                }
            )
    rows.sort(
        key=lambda row: (
            str(row["target_scope_key"]).casefold(),
            str(row["target_scope_key"]),
            str(row["operation"]),
            str(row["actor"]),
            str(row["purpose"]),
            str(row["rights_object_class"]),
            str(row["context_profile_key"]),
        )
    )
    return rows


def _validate_rows(tables: Mapping[str, Sequence[Mapping[str, object]]]) -> None:
    for table_name, columns in TABLE_COLUMNS.items():
        rows = tables.get(table_name)
        if rows is None:
            _fail(f"missing output table {table_name}", code="output_table_missing")
        expected = set(columns)
        for index, row in enumerate(rows):
            if set(row) != expected:
                missing = sorted(expected - set(row))
                extra = sorted(set(row) - expected)
                _fail(
                    f"output columns differ; missing={missing}, extra={extra}",
                    code="output_columns_invalid",
                    context=f"{table_name}[{index}]",
                )


def build_source_governance(
    config: Mapping[str, Any], assets: Iterable[Mapping[str, object]]
) -> SourceGovernanceBuild:
    """Build deterministic provisional governance rows from registered assets."""

    validate_source_scope_config(config)
    source_entries = _source_declarations(config)
    source_rows, sources_by_key = _build_sources(config, source_entries)
    configured_scope_rows, scopes_by_key = _build_configured_scopes(
        config, sources_by_key
    )

    normalized_assets: list[tuple[str, Mapping[str, object], str]] = []
    seen_paths: set[str] = set()
    seen_source_file_ids: set[str] = set()
    for asset in assets:
        if not isinstance(asset, Mapping):
            _fail("asset must be a mapping", code="asset_invalid")
        path = normalize_relative_path(asset.get("relative_path"))
        folded = path.casefold()
        if folded in seen_paths:
            _fail(
                f"duplicate canonical asset path {path!r}",
                code="asset_path_duplicate",
                context=path,
            )
        seen_paths.add(folded)
        parent_key = _resolve_asset_scope_validated(path, config)
        declared_scope_key = asset.get("source_scope_key")
        if declared_scope_key is not None and declared_scope_key != parent_key:
            _fail(
                "asset-declared source scope disagrees with the source governance resolver",
                code="asset_scope_mismatch",
                context=path,
            )
        source_file_id = _source_file_id(asset, path)
        if source_file_id in seen_source_file_ids:
            _fail(
                "one source_file identity is attached to multiple asset paths",
                code="asset_identity_duplicate",
                context=path,
            )
        seen_source_file_ids.add(source_file_id)
        normalized_assets.append((path, asset, parent_key))
    normalized_assets.sort(key=lambda item: (item[0].casefold(), item[0]))

    file_scope_rows: list[dict[str, object]] = []
    locator_rows: list[dict[str, object]] = []
    all_scope_rows_by_key = dict(scopes_by_key)
    for path, asset, parent_key in normalized_assets:
        file_scope = _derive_file_scope(
            config, parent_key, scopes_by_key[parent_key], path
        )
        key = str(file_scope["source_scope_key"])
        if key in all_scope_rows_by_key:
            _fail("derived file scope collision", code="file_scope_collision", context=path)
        all_scope_rows_by_key[key] = file_scope
        file_scope_rows.append(file_scope)
        locator_rows.append(_derive_locator(config, asset, path))

    scope_rows = [*configured_scope_rows, *file_scope_rows]
    scope_rows.sort(key=lambda row: (str(row["source_scope_key"]).casefold(), str(row["source_scope_key"])))
    locator_rows.sort(key=lambda row: (str(row["source_file_id"]), str(row["locator_hash"])))
    relation_rows = _build_relations(config, scopes_by_key)
    citation_rows, assignment_rows = _build_citations(
        config, sources_by_key, scopes_by_key
    )
    configured_scope_configs = {
        str(item["source_scope_key"]): item for item in config["scopes"]
    }
    rights_rows = _build_rights_candidates(
        config,
        scope_rows,
        configured_scope_configs,
        {str(item["source_key"]): item for item in source_entries},
    )

    tables: dict[str, list[dict[str, object]]] = {
        "source": source_rows,
        "source_scope": scope_rows,
        "source_scope_relation": relation_rows,
        "source_locator": locator_rows,
        "citation": citation_rows,
        "citation_assignment": assignment_rows,
        "rights_action_candidate": rights_rows,
    }
    _validate_rows(tables)
    logical_payload = {
        table_name: {
            "columns": list(TABLE_COLUMNS[table_name]),
            "rows": rows,
        }
        for table_name, rows in sorted(tables.items())
    }
    logical_hash = content_sha256(logical_payload)
    automatic_allow_count = sum(
        1
        for row in rights_rows
        if str(row["mapped_decision"]).startswith("allow")
    )
    audit = {
        "status": "provisional",
        "schema_version": config["schema_version"],
        "source_config_version": config["config_version"],
        "id_algorithm_version": config["id_algorithm_version"],
        "asset_count": len(normalized_assets),
        "configured_source_count": len(source_rows),
        "configured_scope_count": len(configured_scope_rows),
        "derived_file_scope_count": len(file_scope_rows),
        "source_scope_relation_count": len(relation_rows),
        "configured_relation_count": len(config["relations"]),
        "derived_cross_source_relation_count": len(relation_rows)
        - len(config["relations"]),
        "source_locator_count": len(locator_rows),
        "citation_count": len(citation_rows),
        "citation_assignment_count": len(assignment_rows),
        "citation_pending_review_count": sum(
            row["review_status"] == "pending" for row in citation_rows
        ),
        "rights_action_candidate_count": len(rights_rows),
        "automatic_allow_count": automatic_allow_count,
        "source_chain_conflict_count": 0,
        "unknown_asset_scope_count": 0,
        "ambiguous_asset_scope_count": 0,
        "unreachable_file_scope_count": 0,
        "logical_hash": logical_hash,
    }
    return SourceGovernanceBuild(
        tables=tables,
        columns=dict(TABLE_COLUMNS),
        logical_hash=logical_hash,
        audit=audit,
    )


def _csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return canonical_identity_json(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def write_source_governance_outputs(
    build: SourceGovernanceBuild, output_root: str | Path
) -> dict[str, Path]:
    """Write fixed-column UTF-8-BOM CSVs with stable LF line endings."""

    root = Path(output_root)
    if root.name in _FORBIDDEN_OUTPUT_NAMES:
        _fail(
            "source governance candidates cannot write into a formal v0.1/v0.2 data layer",
            code="unsafe_output_root",
            context=str(root),
        )
    root.mkdir(parents=True, exist_ok=True)
    _validate_rows(build.tables)
    outputs: dict[str, Path] = {}
    for table_name in TABLE_COLUMNS:
        path = root / OUTPUT_FILENAMES[table_name]
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=list(TABLE_COLUMNS[table_name]),
                extrasaction="raise",
                lineterminator="\n",
            )
            writer.writeheader()
            for row in build.tables[table_name]:
                writer.writerow({key: _csv_value(row[key]) for key in TABLE_COLUMNS[table_name]})
        outputs[table_name] = path
    return outputs
