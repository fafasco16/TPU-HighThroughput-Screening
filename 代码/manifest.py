"""Deterministic source-file manifest generation."""

from __future__ import annotations

import csv
import hashlib
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Final

import yaml

from ids import stable_id
from licensing import normalize_spdx, parse_optional_bool


MANIFEST_COLUMNS: Final[list[str]] = [
    "source_id",
    "source_file_id",
    "raw_path",
    "original_filename",
    "size_bytes",
    "sha256",
    "doi",
    "url",
    "accessed_at",
    "license_spdx",
    "derivatives_allowed",
    "redistribution_allowed",
    "access_restriction",
    "evidence_grade",
    "material_scope",
    "status",
    "notes",
]

_OPTIONAL_TEXT_COLUMNS: Final[tuple[str, ...]] = (
    "doi",
    "url",
    "accessed_at",
    "access_restriction",
    "evidence_grade",
    "material_scope",
    "status",
    "notes",
)


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Hash *path* using bounded-memory binary reads."""

    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


# A descriptive alias for callers that prefer verb-first naming.
file_sha256 = sha256_file


def _project_relative(path: Path, project_root: Path) -> str:
    try:
        relative = path.relative_to(project_root)
    except ValueError as error:
        raise ValueError(f"path is outside project_root: {path}") from error
    return relative.as_posix()


def _iter_files(scan_root: Path) -> Iterable[Path]:
    if scan_root.is_file():
        if scan_root.name.casefold() != ".git":
            yield scan_root
        return

    for directory, child_directories, filenames in os.walk(scan_root, topdown=True):
        child_directories[:] = sorted(
            (
                name
                for name in child_directories
                if name.casefold() != ".git"
            ),
            key=str.casefold,
        )
        for filename in sorted(filenames, key=str.casefold):
            if filename.casefold() == ".git":
                continue
            candidate = Path(directory, filename)
            if any(part.casefold() == ".git" for part in candidate.parts):
                continue
            yield candidate


def _normalized_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if metadata is None or not isinstance(metadata, Mapping):
        raise ValueError("metadata must be a mapping containing source_id")
    source_id = metadata.get("source_id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("source_id must be a non-empty string")

    normalized: dict[str, Any] = {"source_id": source_id.strip()}
    for column in _OPTIONAL_TEXT_COLUMNS:
        value = metadata.get(column, "")
        normalized[column] = "" if value is None else str(value)
    normalized["license_spdx"] = normalize_spdx(metadata.get("license_spdx"))
    normalized["derivatives_allowed"] = parse_optional_bool(
        metadata.get("derivatives_allowed"),
        field_name="derivatives_allowed",
    )
    normalized["redistribution_allowed"] = parse_optional_bool(
        metadata.get("redistribution_allowed"),
        field_name="redistribution_allowed",
    )
    return normalized


def build_manifest(
    project_root: str | Path,
    scan_root: str | Path,
    *,
    metadata: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build deterministic manifest rows for one registered source.

    ``raw_path`` is always relative to ``project_root`` and uses POSIX `/`
    separators on every platform.  Directories named ``.git`` are pruned at
    every nesting level before files are visited.
    """

    project = Path(project_root).resolve(strict=True)
    if not project.is_dir():
        raise ValueError("project_root must be a directory")
    source = Path(scan_root)
    if not source.is_absolute():
        source = project / source
    source = source.resolve(strict=True)
    _project_relative(source, project)
    source_metadata = _normalized_metadata(metadata)

    rows: list[dict[str, Any]] = []
    for path in _iter_files(source):
        resolved_path = path.resolve(strict=True)
        raw_path = _project_relative(resolved_path, project)
        digest = sha256_file(resolved_path)
        row: dict[str, Any] = {
            "source_id": source_metadata["source_id"],
            "source_file_id": stable_id(
                "source_file",
                source_metadata["source_id"],
                raw_path,
                digest,
            ),
            "raw_path": raw_path,
            "original_filename": path.name,
            "size_bytes": resolved_path.stat().st_size,
            "sha256": digest,
            "doi": source_metadata["doi"],
            "url": source_metadata["url"],
            "accessed_at": source_metadata["accessed_at"],
            "license_spdx": source_metadata["license_spdx"],
            "derivatives_allowed": source_metadata["derivatives_allowed"],
            "redistribution_allowed": source_metadata["redistribution_allowed"],
            "access_restriction": source_metadata["access_restriction"],
            "evidence_grade": source_metadata["evidence_grade"],
            "material_scope": source_metadata["material_scope"],
            "status": source_metadata["status"],
            "notes": source_metadata["notes"],
        }
        rows.append({column: row[column] for column in MANIFEST_COLUMNS})
    return sorted(rows, key=lambda row: (row["raw_path"], row["source_id"]))


def _load_registry(config_path: Path) -> list[Mapping[str, Any]]:
    with config_path.open("r", encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, Mapping):
        raise ValueError("source registry must be a mapping")
    sources = document.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("source registry 'sources' must be a non-empty list")
    if not all(isinstance(source, Mapping) for source in sources):
        raise ValueError("every source registry entry must be a mapping")
    return sources


def build_manifest_from_config(
    project_root: str | Path,
    config_path: str | Path,
) -> list[dict[str, Any]]:
    """Build a combined manifest from a UTF-8 YAML source registry."""

    project = Path(project_root).resolve(strict=True)
    config = Path(config_path)
    if not config.is_absolute():
        config = project / config
    registry = _load_registry(config.resolve(strict=True))

    rows: list[dict[str, Any]] = []
    for entry in registry:
        relative_path = entry.get("path")
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ValueError("every source registry entry requires a non-empty path")
        path = Path(relative_path)
        if path.is_absolute():
            raise ValueError("source registry paths must be project-relative")
        metadata = {key: value for key, value in entry.items() if key != "path"}
        rows.extend(build_manifest(project, path, metadata=metadata))
    return sorted(rows, key=lambda row: (row["raw_path"], row["source_id"]))


def write_manifest_csv(
    rows: Iterable[Mapping[str, Any]],
    output_path: str | Path,
) -> Path:
    """Write rows in the fixed manifest column order using Excel-safe UTF-8."""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=MANIFEST_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            missing = [column for column in MANIFEST_COLUMNS if column not in row]
            if missing:
                raise ValueError(f"manifest row is missing columns: {', '.join(missing)}")
            writer.writerow({column: row[column] for column in MANIFEST_COLUMNS})
    return output
