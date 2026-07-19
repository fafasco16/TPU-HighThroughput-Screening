"""Deterministic, fail-closed discovery and classification of TPU data assets.

The registry deliberately stays at file-asset level.  It records a role,
processing stage, non-authoritative material hint, and six independent
governance lifecycle states; observation-level scientific semantics are not
inferred from filenames or extensions.
"""

from __future__ import annotations

import csv
import hashlib
import os
import re
import stat
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from record_identity import stable_record_uid, stable_revision_id


ARTIFACT_ROLES = (
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
)

DATA_STAGES = (
    "raw",
    "normalized",
    "derived",
    "aggregate",
    "model_output",
    "metadata_only",
    "reference_only",
)

LIFECYCLE_VALUES: Mapping[str, frozenset[str]] = {
    "registration_status": frozenset(
        {"discovered", "registered", "excluded_with_evidence"}
    ),
    "availability_status": frozenset(
        {"available", "metadata_only", "request_required", "unreachable", "withdrawn"}
    ),
    "parse_status": frozenset(
        {"not_attempted", "parsed", "partially_parsed", "failed", "not_applicable"}
    ),
    "scientific_admission_status": frozenset(
        {"pending", "admitted", "admitted_with_waiver", "rejected"}
    ),
    "model_readiness_status": frozenset(
        {"not_assessed", "eligible", "held_out_only", "ineligible", "blocked"}
    ),
    "release_status": frozenset(
        {"not_assessed", "approved", "denied", "expired", "superseded"}
    ),
}
LIFECYCLE_FIELDS = tuple(LIFECYCLE_VALUES)

REGISTRY_COLUMNS = (
    "asset_occurrence_uid",
    "source_file_uid",
    "content_blob_uid",
    "record_revision_id",
    "relative_path",
    "source_file_natural_key",
    "original_name",
    "extension",
    "size_bytes",
    "content_sha256",
    "media_type",
    "read_status",
    "source_scope_key",
    "artifact_role",
    "data_stage",
    "material_scope_hint",
    "registration_status",
    "availability_status",
    "parse_status",
    "scientific_admission_status",
    "model_readiness_status",
    "release_status",
    "matched_rule_id",
    "rules_version",
    "rule_priority",
    "decision_method",
    "decision_basis",
    "review_required",
)

DUPLICATE_COLUMNS = (
    "duplicate_group_uid",
    "content_sha256",
    "member_count",
    "canonical_asset_occurrence_uid",
    "canonical_relative_path",
    "member_asset_occurrence_uid",
    "member_relative_path",
    "is_canonical",
)

_RULE_FIELDS = frozenset(
    {
        "rule_id",
        "priority",
        "path_regex",
        "source_scope_key",
        "artifact_role",
        "data_stage",
        "material_scope_hint",
        *LIFECYCLE_FIELDS,
        "decision_basis",
        "review_required",
    }
)
_ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "id_algorithm_version",
        "rules_version",
        "discovery_scope_key",
        "scan",
        "duplicate_policy",
        "rules",
    }
)
_SCAN_FIELDS = frozenset(
    {
        "root_hint",
        "follow_symlinks",
        "prune_directory_names",
        "path_normalization",
        "stable_sort",
        "hash_algorithm",
        "chunk_size_bytes",
        "symlink_policy",
        "read_failure_policy",
        "unmatched_policy",
        "conflict_policy",
    }
)
_DUPLICATE_POLICY_FIELDS = frozenset({"group_key", "canonical_order"})
_DEFAULT_CHUNK_SIZE = 1024 * 1024

# ``mimetypes.guess_type`` reads platform and user registry state.  The same
# asset can therefore receive a different media type (and consequently a
# different ``record_revision_id``) on Windows, Linux, or a machine with a
# customized MIME database.  Keep the registry contract self-contained and
# version-controlled instead.  Values for extensions already present in the
# v0.2 raw inventory intentionally preserve the original Windows snapshot.
# Explicit compound suffixes are matched before their simple suffixes.
_FROZEN_MEDIA_TYPES: Mapping[str, str] = MappingProxyType({
    ".tar.bz2": "application/x-bzip2",
    ".tar.gz": "application/gzip",
    ".tar.xz": "application/x-xz",
    ".csv.gz": "application/gzip",
    ".json.gz": "application/gzip",
    ".ndjson.gz": "application/gzip",
    ".csv": "application/vnd.ms-excel",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".elastic": "application/octet-stream",
    ".in": "application/octet-stream",
    ".ipynb": "application/octet-stream",
    ".jpeg": "image/jpeg",
    ".lock": "application/octet-stream",
    ".md": "text/markdown",
    ".mod": "application/octet-stream",
    ".mol": "application/octet-stream",
    ".npy": "application/octet-stream",
    ".pdf": "application/pdf",
    ".pkl": "application/octet-stream",
    ".png": "image/png",
    ".py": "text/x-python",
    ".sh": "application/x-sh",
    ".tif": "image/tiff",
    ".toml": "application/octet-stream",
    ".txt": "text/plain",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".yml": "application/octet-stream",
    ".zip": "application/x-zip-compressed",
})
_FROZEN_MEDIA_SUFFIXES = tuple(
    sorted(_FROZEN_MEDIA_TYPES, key=lambda suffix: (-len(suffix), suffix))
)
_FALLBACK_MEDIA_TYPE = "application/octet-stream"


class AssetRegistryError(RuntimeError):
    """A structured blocker that prevents a partial or ambiguous registry."""

    def __init__(
        self,
        code: str,
        message: str,
        context: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.context = dict(context or {})

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "blocked",
            "error_code": self.code,
            "message": self.message,
            "context": self.context,
        }


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys at every mapping level."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    loader.flatten_mapping(node)
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise AssetRegistryError(
                "invalid_yaml_key",
                "YAML mapping keys must be hashable scalars",
                {"line": key_node.start_mark.line + 1},
            ) from error
        if duplicate:
            raise AssetRegistryError(
                "duplicate_yaml_key",
                f"duplicate YAML key: {key!r}",
                {"key": str(key), "line": key_node.start_mark.line + 1},
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _load_unique_yaml(path: Path) -> Mapping[str, object]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            document = yaml.load(stream, Loader=_UniqueKeyLoader)
    except AssetRegistryError:
        raise
    except yaml.YAMLError as error:
        raise AssetRegistryError(
            "invalid_yaml",
            "asset registration rules are not valid YAML",
            {"path": str(path), "detail": str(error)},
        ) from error
    except OSError as error:
        raise AssetRegistryError(
            "rules_read_failure",
            "asset registration rules could not be read",
            {"path": str(path), "detail": str(error)},
        ) from error
    if not isinstance(document, Mapping):
        raise AssetRegistryError(
            "invalid_configuration",
            "asset registration rules root must be a mapping",
            {"path": str(path)},
        )
    return document


def _token(value: object, field_name: str, *, error_code: str) -> str:
    if not isinstance(value, str):
        raise AssetRegistryError(error_code, f"{field_name} must be a string")
    normalized = unicodedata.normalize("NFC", value)
    if not normalized or normalized != normalized.strip():
        raise AssetRegistryError(
            error_code,
            f"{field_name} must be a non-empty trimmed string",
        )
    return normalized


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise AssetRegistryError(
            "invalid_configuration",
            f"{field_name} must be a string-keyed mapping",
        )
    return value


def _unknown_fields(
    mapping: Mapping[str, object],
    allowed: frozenset[str],
    field_name: str,
    *,
    code: str,
) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise AssetRegistryError(
            code,
            f"{field_name} contains unsupported fields",
            {"fields": unknown},
        )


@dataclass(frozen=True)
class AssetRule:
    rule_id: str
    priority: int
    path_regex: str
    source_scope_key: str
    artifact_role: str
    data_stage: str
    material_scope_hint: str
    registration_status: str
    availability_status: str
    parse_status: str
    scientific_admission_status: str
    model_readiness_status: str
    release_status: str
    decision_basis: str
    review_required: bool
    _compiled: re.Pattern[str] = field(repr=False, compare=False)

    def matches(self, relative_path: str) -> bool:
        return self._compiled.search(relative_path) is not None


@dataclass(frozen=True)
class AssetRegistryConfig:
    schema_version: str
    id_algorithm_version: str
    rules_version: str
    discovery_scope_key: str
    root_hint: str
    follow_symlinks: bool
    prune_directory_names: tuple[str, ...]
    chunk_size_bytes: int
    rules: tuple[AssetRule, ...]


@dataclass(frozen=True)
class DiscoveredAsset:
    physical_path: Path = field(repr=False, compare=False)
    relative_path: str


@dataclass(frozen=True)
class AssetRecord:
    asset_occurrence_uid: str
    source_file_uid: str
    content_blob_uid: str
    record_revision_id: str
    relative_path: str
    source_file_natural_key: str
    original_name: str
    extension: str
    size_bytes: int
    content_sha256: str
    media_type: str
    read_status: str
    source_scope_key: str
    artifact_role: str
    data_stage: str
    material_scope_hint: str
    registration_status: str
    availability_status: str
    parse_status: str
    scientific_admission_status: str
    model_readiness_status: str
    release_status: str
    matched_rule_id: str
    rules_version: str
    rule_priority: int
    decision_method: str
    decision_basis: str
    review_required: bool

    def as_csv_row(self) -> dict[str, object]:
        row = {column: getattr(self, column) for column in REGISTRY_COLUMNS}
        row["review_required"] = "true" if self.review_required else "false"
        return row


@dataclass(frozen=True)
class ExactDuplicateGroup:
    duplicate_group_uid: str
    content_sha256: str
    member_count: int
    canonical_asset_occurrence_uid: str
    canonical_relative_path: str
    members: tuple[str, ...]
    member_occurrence_uids: tuple[str, ...]


@dataclass(frozen=True)
class AssetRegistryResult:
    records: tuple[AssetRecord, ...]
    duplicate_groups: tuple[ExactDuplicateGroup, ...]
    audit: Mapping[str, object]


def _parse_rule(raw: object, index: int) -> AssetRule:
    if not isinstance(raw, Mapping) or not all(isinstance(key, str) for key in raw):
        raise AssetRegistryError(
            "invalid_rule",
            "each asset rule must be a string-keyed mapping",
            {"rule_index": index},
        )
    if "origin_kind" in raw:
        raise AssetRegistryError(
            "forbidden_observation_field",
            "origin_kind is observation-level semantics and is forbidden in asset rules",
            {"rule_index": index},
        )
    _unknown_fields(raw, _RULE_FIELDS, "asset rule", code="invalid_rule")
    missing = sorted(_RULE_FIELDS - set(raw))
    if missing:
        raise AssetRegistryError(
            "invalid_rule",
            "asset rule is missing required fields",
            {"rule_index": index, "fields": missing},
        )

    rule_id = _token(raw["rule_id"], "rule_id", error_code="invalid_rule")
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]*", rule_id) is None:
        raise AssetRegistryError(
            "invalid_rule",
            "rule_id must use lower ASCII letters, digits, dot, underscore, or hyphen",
            {"rule_id": rule_id},
        )
    priority = raw["priority"]
    if isinstance(priority, bool) or not isinstance(priority, int) or priority < 0:
        raise AssetRegistryError(
            "invalid_rule",
            "priority must be a non-negative integer",
            {"rule_id": rule_id},
        )
    path_regex = _token(raw["path_regex"], "path_regex", error_code="invalid_rule")
    try:
        compiled = re.compile(path_regex)
    except re.error as error:
        raise AssetRegistryError(
            "invalid_regex",
            "asset rule path_regex is invalid",
            {"rule_id": rule_id, "path_regex": path_regex, "detail": str(error)},
        ) from error

    artifact_role = _token(
        raw["artifact_role"], "artifact_role", error_code="invalid_rule"
    )
    if artifact_role not in ARTIFACT_ROLES:
        raise AssetRegistryError(
            "invalid_rule",
            "artifact_role is outside the frozen artifact roles",
            {"rule_id": rule_id, "artifact_role": artifact_role},
        )
    data_stage = _token(raw["data_stage"], "data_stage", error_code="invalid_rule")
    if data_stage not in DATA_STAGES:
        raise AssetRegistryError(
            "invalid_rule",
            "data_stage is outside the frozen values",
            {"rule_id": rule_id, "data_stage": data_stage},
        )

    lifecycle: dict[str, str] = {}
    for field_name, allowed_values in LIFECYCLE_VALUES.items():
        value = _token(raw[field_name], field_name, error_code="invalid_rule")
        if value not in allowed_values:
            raise AssetRegistryError(
                "invalid_rule",
                f"{field_name} is outside the frozen values",
                {"rule_id": rule_id, field_name: value},
            )
        lifecycle[field_name] = value

    review_required = raw["review_required"]
    if not isinstance(review_required, bool):
        raise AssetRegistryError(
            "invalid_rule",
            "review_required must be a boolean",
            {"rule_id": rule_id},
        )
    return AssetRule(
        rule_id=rule_id,
        priority=priority,
        path_regex=path_regex,
        source_scope_key=_token(
            raw["source_scope_key"], "source_scope_key", error_code="invalid_rule"
        ),
        artifact_role=artifact_role,
        data_stage=data_stage,
        material_scope_hint=_token(
            raw["material_scope_hint"],
            "material_scope_hint",
            error_code="invalid_rule",
        ),
        registration_status=lifecycle["registration_status"],
        availability_status=lifecycle["availability_status"],
        parse_status=lifecycle["parse_status"],
        scientific_admission_status=lifecycle["scientific_admission_status"],
        model_readiness_status=lifecycle["model_readiness_status"],
        release_status=lifecycle["release_status"],
        decision_basis=_token(
            raw["decision_basis"], "decision_basis", error_code="invalid_rule"
        ),
        review_required=review_required,
        _compiled=compiled,
    )


def load_asset_rules(path: str | os.PathLike[str]) -> AssetRegistryConfig:
    """Load and validate the versioned asset classification rule document."""

    rules_path = Path(path)
    document = _load_unique_yaml(rules_path)
    _unknown_fields(document, _ROOT_FIELDS, "rules root", code="invalid_configuration")
    missing_root = sorted(_ROOT_FIELDS - set(document))
    if missing_root:
        raise AssetRegistryError(
            "invalid_configuration",
            "rules root is missing required fields",
            {"fields": missing_root},
        )

    schema_version = _token(
        document["schema_version"], "schema_version", error_code="invalid_configuration"
    )
    if schema_version != "v0.2":
        raise AssetRegistryError(
            "invalid_configuration",
            "asset registry schema_version must be v0.2",
            {"schema_version": schema_version},
        )
    id_algorithm_version = _token(
        document["id_algorithm_version"],
        "id_algorithm_version",
        error_code="invalid_configuration",
    )
    if id_algorithm_version != "uuid5-v1":
        raise AssetRegistryError(
            "invalid_configuration",
            "asset identities require id_algorithm_version uuid5-v1",
            {"id_algorithm_version": id_algorithm_version},
        )
    rules_version = _token(
        document["rules_version"], "rules_version", error_code="invalid_configuration"
    )
    discovery_scope_key = _token(
        document["discovery_scope_key"],
        "discovery_scope_key",
        error_code="invalid_configuration",
    )

    scan = _mapping(document["scan"], "scan")
    _unknown_fields(scan, _SCAN_FIELDS, "scan", code="invalid_configuration")
    missing_scan = sorted(_SCAN_FIELDS - set(scan))
    if missing_scan:
        raise AssetRegistryError(
            "invalid_configuration",
            "scan is missing required fields",
            {"fields": missing_scan},
        )
    follow_symlinks = scan["follow_symlinks"]
    if not isinstance(follow_symlinks, bool):
        raise AssetRegistryError(
            "invalid_configuration", "scan.follow_symlinks must be a boolean"
        )
    prune = scan["prune_directory_names"]
    if not isinstance(prune, list) or not prune:
        raise AssetRegistryError(
            "invalid_configuration",
            "scan.prune_directory_names must be a non-empty list",
        )
    prune_names = tuple(
        _token(value, "prune_directory_names item", error_code="invalid_configuration")
        for value in prune
    )
    if ".git" not in {name.casefold() for name in prune_names}:
        raise AssetRegistryError(
            "invalid_configuration",
            "scan.prune_directory_names must explicitly include .git",
        )
    expected_scan_values = {
        "path_normalization": "unicode_nfc_posix",
        "stable_sort": "casefold_then_nfc",
        "hash_algorithm": "sha256_stream",
        "read_failure_policy": "block",
        "unmatched_policy": "block",
        "conflict_policy": "block",
    }
    for field_name, expected in expected_scan_values.items():
        if scan[field_name] != expected:
            raise AssetRegistryError(
                "invalid_configuration",
                f"scan.{field_name} must be {expected}",
                {field_name: scan[field_name]},
            )
    expected_symlink_policy = "follow_within_root" if follow_symlinks else "block"
    if scan["symlink_policy"] != expected_symlink_policy:
        raise AssetRegistryError(
            "invalid_configuration",
            f"scan.symlink_policy must be {expected_symlink_policy}",
        )
    chunk_size = scan["chunk_size_bytes"]
    if (
        isinstance(chunk_size, bool)
        or not isinstance(chunk_size, int)
        or chunk_size <= 0
        or chunk_size > 64 * 1024 * 1024
    ):
        raise AssetRegistryError(
            "invalid_configuration",
            "scan.chunk_size_bytes must be an integer from 1 through 67108864",
        )

    duplicate_policy = _mapping(document["duplicate_policy"], "duplicate_policy")
    _unknown_fields(
        duplicate_policy,
        _DUPLICATE_POLICY_FIELDS,
        "duplicate_policy",
        code="invalid_configuration",
    )
    if duplicate_policy != {
        "group_key": "content_sha256",
        "canonical_order": "priority_desc_then_casefold_path",
    }:
        raise AssetRegistryError(
            "invalid_configuration",
            "duplicate_policy must use exact SHA-256 and deterministic canonical order",
        )

    raw_rules = document["rules"]
    if not isinstance(raw_rules, list) or not raw_rules:
        raise AssetRegistryError(
            "invalid_configuration", "rules must be a non-empty list"
        )
    rules = tuple(_parse_rule(raw, index) for index, raw in enumerate(raw_rules))
    duplicate_ids = sorted(
        rule_id for rule_id, count in Counter(rule.rule_id for rule in rules).items() if count > 1
    )
    if duplicate_ids:
        raise AssetRegistryError(
            "duplicate_rule_id",
            "rule_id values must be unique",
            {"rule_ids": duplicate_ids},
        )
    return AssetRegistryConfig(
        schema_version=schema_version,
        id_algorithm_version=id_algorithm_version,
        rules_version=rules_version,
        discovery_scope_key=discovery_scope_key,
        root_hint=_token(
            scan["root_hint"], "scan.root_hint", error_code="invalid_configuration"
        ),
        follow_symlinks=follow_symlinks,
        prune_directory_names=prune_names,
        chunk_size_bytes=chunk_size,
        rules=rules,
    )


def canonical_relative_path(value: str | os.PathLike[str]) -> str:
    """Return a strict Unicode-NFC, slash-separated relative path."""

    try:
        raw = os.fspath(value)
    except TypeError as error:
        raise AssetRegistryError("path_escape", "relative path must be path-like") from error
    if not isinstance(raw, str):
        raise AssetRegistryError("path_escape", "relative path must decode to text")
    normalized = unicodedata.normalize("NFC", raw.replace("\\", "/"))
    if (
        not normalized
        or "\x00" in normalized
        or normalized.startswith("/")
        or normalized.startswith("//")
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        raise AssetRegistryError(
            "path_escape", "path must be a non-empty relative path", {"path": raw}
        )
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise AssetRegistryError(
            "path_escape",
            "path contains an empty, current-directory, or parent-directory segment",
            {"path": raw},
        )
    return "/".join(parts)


def _path_sort_key(relative_path: str) -> tuple[str, str]:
    return relative_path.casefold(), relative_path


def _resolved_within(root: Path, candidate: Path) -> bool:
    try:
        return os.path.commonpath((str(root), str(candidate))) == str(root)
    except ValueError:
        return False


def _entry_is_link_or_reparse(entry: os.DirEntry[str]) -> bool:
    if entry.is_symlink():
        return True
    attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _path_is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError as error:
        raise AssetRegistryError(
            "read_failure",
            "scan root metadata could not be read",
            {"path": str(path), "detail": str(error)},
        ) from error
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def discover_asset_paths(
    root: str | os.PathLike[str],
    config: AssetRegistryConfig,
) -> tuple[DiscoveredAsset, ...]:
    """Recursively discover regular files while pruning every ``.git`` tree."""

    root_path = Path(root).absolute()
    if not root_path.exists() or not root_path.is_dir():
        raise AssetRegistryError(
            "invalid_scan_root", "scan root must be an existing directory", {"path": str(root)}
        )
    if _path_is_link_or_reparse(root_path) and not config.follow_symlinks:
        raise AssetRegistryError(
            "symlink_blocked",
            "scan root is a symbolic link or junction",
            {"path": str(root_path)},
        )
    try:
        resolved_root = root_path.resolve(strict=True)
    except OSError as error:
        raise AssetRegistryError(
            "read_failure",
            "scan root could not be resolved",
            {"path": str(root_path), "detail": str(error)},
        ) from error

    discovered: list[DiscoveredAsset] = []
    seen_paths: dict[str, str] = {}
    visited_directories: set[tuple[object, ...]] = set()
    prune_names = {name.casefold() for name in config.prune_directory_names}

    def directory_key(path: Path) -> tuple[object, ...]:
        metadata = path.stat()
        inode = getattr(metadata, "st_ino", 0)
        if inode:
            return (getattr(metadata, "st_dev", 0), inode)
        return (str(path.resolve(strict=True)).casefold(),)

    def visit(directory: Path) -> None:
        try:
            key = directory_key(directory)
            if key in visited_directories:
                return
            visited_directories.add(key)
            entries = list(os.scandir(directory))
        except OSError as error:
            raise AssetRegistryError(
                "read_failure",
                "directory could not be enumerated",
                {"path": str(directory), "detail": str(error)},
            ) from error

        for entry in entries:
            try:
                is_directory_without_follow = entry.is_dir(follow_symlinks=False)
                if is_directory_without_follow and entry.name.casefold() in prune_names:
                    continue
                is_link = _entry_is_link_or_reparse(entry)
                entry_path = Path(entry.path)
                relative_path = canonical_relative_path(entry_path.relative_to(root_path))
                if is_link and not config.follow_symlinks:
                    raise AssetRegistryError(
                        "symlink_blocked",
                        "symbolic links and junctions are blocked by discovery policy",
                        {"relative_path": relative_path},
                    )
                target = entry_path.resolve(strict=True)
                if not _resolved_within(resolved_root, target):
                    raise AssetRegistryError(
                        "path_escape",
                        "discovered path resolves outside the scan root",
                        {"relative_path": relative_path},
                    )
                if entry.is_dir(follow_symlinks=config.follow_symlinks):
                    visit(entry_path)
                elif entry.is_file(follow_symlinks=config.follow_symlinks):
                    previous = seen_paths.get(relative_path)
                    if previous is not None and previous != str(entry_path):
                        raise AssetRegistryError(
                            "path_normalization_collision",
                            "two physical paths normalize to one registry relative path",
                            {
                                "relative_path": relative_path,
                                "physical_paths": sorted([previous, str(entry_path)]),
                            },
                        )
                    seen_paths[relative_path] = str(entry_path)
                    discovered.append(DiscoveredAsset(entry_path, relative_path))
                else:
                    raise AssetRegistryError(
                        "unsupported_file_type",
                        "discovery encountered a non-regular filesystem entry",
                        {"relative_path": relative_path},
                    )
            except AssetRegistryError:
                raise
            except OSError as error:
                raise AssetRegistryError(
                    "read_failure",
                    "filesystem entry metadata could not be read",
                    {"path": entry.path, "detail": str(error)},
                ) from error

    visit(root_path)
    discovered.sort(key=lambda asset: _path_sort_key(asset.relative_path))
    return tuple(discovered)


def classify_path(relative_path: str, config: AssetRegistryConfig) -> AssetRule:
    """Select exactly one highest-priority rule, failing closed otherwise."""

    canonical = canonical_relative_path(relative_path)
    matches = [rule for rule in config.rules if rule.matches(canonical)]
    if not matches:
        raise AssetRegistryError(
            "unmatched_path",
            "no asset registration rule matched the path",
            {"relative_path": canonical},
        )
    highest_priority = max(rule.priority for rule in matches)
    winners = sorted(
        (rule for rule in matches if rule.priority == highest_priority),
        key=lambda rule: rule.rule_id,
    )
    if len(winners) != 1:
        raise AssetRegistryError(
            "ambiguous_rule",
            "multiple asset rules share the highest matching priority",
            {
                "relative_path": canonical,
                "priority": highest_priority,
                "rule_ids": [rule.rule_id for rule in winners],
            },
        )
    return winners[0]


def _open_binary(path: Path):
    return path.open("rb")


def _hash_file_with_size(path: Path, chunk_size: int) -> tuple[str, int]:
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    digest = hashlib.sha256()
    total = 0
    try:
        before = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise AssetRegistryError(
                "read_failure", "hash target is not a regular file", {"path": str(path)}
            )
        with _open_binary(path) as stream:
            opened = os.fstat(stream.fileno())
            if getattr(before, "st_ino", 0) and (
                before.st_dev,
                before.st_ino,
            ) != (opened.st_dev, opened.st_ino):
                raise AssetRegistryError(
                    "file_changed_during_read",
                    "file identity changed between discovery and open",
                    {"path": str(path)},
                )
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
                total += len(chunk)
        after = path.stat(follow_symlinks=False)
    except AssetRegistryError:
        raise
    except OSError as error:
        raise AssetRegistryError(
            "read_failure",
            "file bytes could not be read",
            {"path": str(path), "detail": str(error)},
        ) from error
    if total != before.st_size or (
        before.st_size,
        getattr(before, "st_mtime_ns", None),
        getattr(before, "st_ino", None),
    ) != (
        after.st_size,
        getattr(after, "st_mtime_ns", None),
        getattr(after, "st_ino", None),
    ):
        raise AssetRegistryError(
            "file_changed_during_read",
            "file changed while its SHA-256 was being computed",
            {"path": str(path)},
        )
    return digest.hexdigest(), total


def hash_file_sha256(
    path: str | os.PathLike[str],
    *,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
) -> str:
    """Compute a SHA-256 by bounded streaming reads."""

    return _hash_file_with_size(Path(path), chunk_size)[0]


def _media_type(path: str) -> str:
    """Return a platform-independent media type from the frozen suffix map.

    Matching is Unicode-normalized, case-insensitive, and longest-suffix-first
    so an explicit compound suffix cannot be shadowed by its final component.
    Extensionless files, dotfiles, and every unknown simple or compound suffix
    receive the same conservative binary fallback.
    """

    normalized_path = unicodedata.normalize("NFC", path).replace("\\", "/")
    filename = normalized_path.rsplit("/", 1)[-1].casefold()
    for suffix in _FROZEN_MEDIA_SUFFIXES:
        if len(filename) > len(suffix) and filename.endswith(suffix):
            return _FROZEN_MEDIA_TYPES[suffix]
    return _FALLBACK_MEDIA_TYPE


def _record_from_asset(
    asset: DiscoveredAsset,
    rule: AssetRule,
    config: AssetRegistryConfig,
) -> AssetRecord:
    content_hash, size_bytes = _hash_file_with_size(
        asset.physical_path, config.chunk_size_bytes
    )
    relative_path = asset.relative_path
    natural_key = relative_path
    asset_occurrence_uid = stable_record_uid(
        "asset_occurrence",
        {
            "discovery_scope_key": config.discovery_scope_key,
            "relative_path": relative_path,
        },
        algorithm_version=config.id_algorithm_version,
    )
    source_file_uid = stable_record_uid(
        "source_file",
        {
            "source_scope_key": rule.source_scope_key,
            "source_file_natural_key": natural_key,
        },
        algorithm_version=config.id_algorithm_version,
    )
    content_blob_uid = stable_record_uid(
        "content_blob",
        {"sha256": content_hash},
        algorithm_version=config.id_algorithm_version,
    )
    original_name = unicodedata.normalize("NFC", asset.physical_path.name)
    extension = unicodedata.normalize("NFC", asset.physical_path.suffix.casefold())
    media_type = _media_type(relative_path)
    revision_content = {
        "content_sha256": content_hash,
        "size_bytes": size_bytes,
        "original_name": original_name,
        "extension": extension,
        "media_type": media_type,
        "source_scope_key": rule.source_scope_key,
        "artifact_role": rule.artifact_role,
        "data_stage": rule.data_stage,
        "material_scope_hint": rule.material_scope_hint,
        **{field_name: getattr(rule, field_name) for field_name in LIFECYCLE_FIELDS},
        "matched_rule_id": rule.rule_id,
        "rules_version": config.rules_version,
        "rule_priority": rule.priority,
        "decision_method": "machine_rule",
        "decision_basis": rule.decision_basis,
        "review_required": rule.review_required,
        "id_algorithm_version": config.id_algorithm_version,
    }
    record_revision_id = stable_revision_id(
        source_file_uid,
        config.schema_version,
        revision_content,
    )
    return AssetRecord(
        asset_occurrence_uid=asset_occurrence_uid,
        source_file_uid=source_file_uid,
        content_blob_uid=content_blob_uid,
        record_revision_id=record_revision_id,
        relative_path=relative_path,
        source_file_natural_key=natural_key,
        original_name=original_name,
        extension=extension,
        size_bytes=size_bytes,
        content_sha256=content_hash,
        media_type=media_type,
        read_status="readable",
        source_scope_key=rule.source_scope_key,
        artifact_role=rule.artifact_role,
        data_stage=rule.data_stage,
        material_scope_hint=rule.material_scope_hint,
        registration_status=rule.registration_status,
        availability_status=rule.availability_status,
        parse_status=rule.parse_status,
        scientific_admission_status=rule.scientific_admission_status,
        model_readiness_status=rule.model_readiness_status,
        release_status=rule.release_status,
        matched_rule_id=rule.rule_id,
        rules_version=config.rules_version,
        rule_priority=rule.priority,
        decision_method="machine_rule",
        decision_basis=rule.decision_basis,
        review_required=rule.review_required,
    )


def exact_duplicate_groups(
    records: Iterable[AssetRecord],
    *,
    id_algorithm_version: str = "uuid5-v1",
) -> tuple[ExactDuplicateGroup, ...]:
    """Group exact byte duplicates while retaining every file occurrence."""

    by_hash: defaultdict[str, list[AssetRecord]] = defaultdict(list)
    for record in records:
        by_hash[record.content_sha256].append(record)
    groups: list[ExactDuplicateGroup] = []
    for content_hash, members in by_hash.items():
        if len(members) < 2:
            continue
        ordered = sorted(
            members,
            key=lambda record: (
                -record.rule_priority,
                record.relative_path.casefold(),
                record.relative_path,
            ),
        )
        canonical = ordered[0]
        members_by_path = sorted(
            members, key=lambda record: _path_sort_key(record.relative_path)
        )
        groups.append(
            ExactDuplicateGroup(
                duplicate_group_uid=stable_record_uid(
                    "exact_duplicate_group",
                    {"content_sha256": content_hash},
                    algorithm_version=id_algorithm_version,
                ),
                content_sha256=content_hash,
                member_count=len(members_by_path),
                canonical_asset_occurrence_uid=canonical.asset_occurrence_uid,
                canonical_relative_path=canonical.relative_path,
                members=tuple(member.relative_path for member in members_by_path),
                member_occurrence_uids=tuple(
                    member.asset_occurrence_uid for member in members_by_path
                ),
            )
        )
    groups.sort(key=lambda group: (group.content_sha256, group.duplicate_group_uid))
    return tuple(groups)


def build_asset_registry(
    root: str | os.PathLike[str],
    config: AssetRegistryConfig | str | os.PathLike[str],
) -> AssetRegistryResult:
    """Discover, classify, identify, hash, and deduplicate one asset root."""

    loaded = load_asset_rules(config) if not isinstance(config, AssetRegistryConfig) else config
    assets = discover_asset_paths(root, loaded)
    records = tuple(
        _record_from_asset(asset, classify_path(asset.relative_path, loaded), loaded)
        for asset in assets
    )
    groups = exact_duplicate_groups(
        records, id_algorithm_version=loaded.id_algorithm_version
    )
    role_counts = dict(sorted(Counter(record.artifact_role for record in records).items()))
    rule_counts = dict(sorted(Counter(record.matched_rule_id for record in records).items()))
    duplicate_occurrences = sum(group.member_count for group in groups)
    excluded_count = role_counts.get("excluded_non_domain", 0)
    missing_status_count = sum(
        1
        for record in records
        if any(not getattr(record, field_name) for field_name in LIFECYCLE_FIELDS)
    )
    audit: dict[str, object] = {
        "status": "provisional_pass",
        "audit_scope": "asset_registry_only",
        "schema_version": loaded.schema_version,
        "id_algorithm_version": loaded.id_algorithm_version,
        "rules_version": loaded.rules_version,
        "discovery_scope_key": loaded.discovery_scope_key,
        "rule_count": len(loaded.rules),
        "input_count": len(assets),
        "registered_count": len(records) - excluded_count,
        "excluded_count": excluded_count,
        "unclassified_count": 0,
        "ambiguous_count": 0,
        "read_failure_count": 0,
        "unknown_scope_count": None,
        "missing_status_count": missing_status_count,
        "table_logical_hashes": {},
        "snapshot_logical_hash": None,
        "integration_pending": [
            "source_scope_existence_validation",
            "final_table_and_snapshot_logical_hashes",
        ],
        "discovered": len(assets),
        "classified": len(records),
        "unmatched": 0,
        "ambiguous": 0,
        "read_failures": 0,
        "role_counts": role_counts,
        "rule_counts": rule_counts,
        "duplicate_group_count": len(groups),
        "duplicate_occurrence_count": duplicate_occurrences,
        "duplicate_redundant_occurrence_count": duplicate_occurrences - len(groups),
    }
    return AssetRegistryResult(records=records, duplicate_groups=groups, audit=audit)


def _write_csv(
    path: str | os.PathLike[str],
    columns: Sequence[str],
    rows: Iterable[Mapping[str, object]],
) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=list(columns),
                extrasaction="raise",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)
    except (OSError, ValueError) as error:
        raise AssetRegistryError(
            "output_write_failure",
            "deterministic registry CSV could not be written",
            {"path": str(output), "detail": str(error)},
        ) from error


def write_registry_csv(
    path: str | os.PathLike[str], records: Iterable[AssetRecord]
) -> None:
    """Write the fixed-schema asset registry as deterministic UTF-8 BOM CSV."""

    ordered = sorted(records, key=lambda record: _path_sort_key(record.relative_path))
    _write_csv(path, REGISTRY_COLUMNS, (record.as_csv_row() for record in ordered))


def write_duplicate_groups_csv(
    path: str | os.PathLike[str], groups: Iterable[ExactDuplicateGroup]
) -> None:
    """Write one row per exact-duplicate occurrence, including the canonical member."""

    rows: list[dict[str, object]] = []
    for group in sorted(
        groups, key=lambda item: (item.content_sha256, item.duplicate_group_uid)
    ):
        for member_path, member_uid in zip(
            group.members, group.member_occurrence_uids, strict=True
        ):
            rows.append(
                {
                    "duplicate_group_uid": group.duplicate_group_uid,
                    "content_sha256": group.content_sha256,
                    "member_count": group.member_count,
                    "canonical_asset_occurrence_uid": group.canonical_asset_occurrence_uid,
                    "canonical_relative_path": group.canonical_relative_path,
                    "member_asset_occurrence_uid": member_uid,
                    "member_relative_path": member_path,
                    "is_canonical": (
                        "true"
                        if member_uid == group.canonical_asset_occurrence_uid
                        else "false"
                    ),
                }
            )
    _write_csv(path, DUPLICATE_COLUMNS, rows)


# Narrow aliases keep the public API discoverable for the later CLI integration.
discover_assets = discover_asset_paths
scan_assets = build_asset_registry

__all__ = [
    "ARTIFACT_ROLES",
    "DATA_STAGES",
    "DUPLICATE_COLUMNS",
    "LIFECYCLE_FIELDS",
    "LIFECYCLE_VALUES",
    "REGISTRY_COLUMNS",
    "AssetRecord",
    "AssetRegistryConfig",
    "AssetRegistryError",
    "AssetRegistryResult",
    "AssetRule",
    "DiscoveredAsset",
    "ExactDuplicateGroup",
    "build_asset_registry",
    "canonical_relative_path",
    "classify_path",
    "discover_asset_paths",
    "discover_assets",
    "exact_duplicate_groups",
    "hash_file_sha256",
    "load_asset_rules",
    "scan_assets",
    "write_duplicate_groups_csv",
    "write_registry_csv",
]
