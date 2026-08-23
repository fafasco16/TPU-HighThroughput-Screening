"""Loading and fail-fast validation for the v0.1 schema catalogs."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

import yaml


SUPPORTED_TYPES: Final[frozenset[str]] = frozenset(
    {"string", "integer", "number", "boolean", "date", "datetime", "json"}
)


class SchemaValidationError(ValueError):
    """Raised when a schema catalog or a record violates the schema contract."""


def _read_yaml_mapping(path: str | Path, *, label: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, dict):
        raise SchemaValidationError(f"{label} YAML root must be a mapping")
    return document


def _validate_enum_catalog(enums: Mapping[str, Any]) -> None:
    if not isinstance(enums.get("schema_version"), str) or not enums["schema_version"]:
        raise SchemaValidationError("enum catalog requires schema_version")
    values_by_enum = enums.get("enums")
    if not isinstance(values_by_enum, Mapping):
        raise SchemaValidationError("enum catalog 'enums' must be a mapping")
    for name, values in values_by_enum.items():
        if not isinstance(name, str) or not name:
            raise SchemaValidationError("enum names must be non-empty strings")
        if not isinstance(values, list) or not values:
            raise SchemaValidationError(f"enum {name!r} must be a non-empty list")
        if any(isinstance(value, (dict, list)) for value in values):
            raise SchemaValidationError(f"enum {name!r} values must be scalar")
        if len({json.dumps(value, sort_keys=True) for value in values}) != len(values):
            raise SchemaValidationError(f"enum {name!r} values must be unique")


def load_enums(path: str | Path) -> dict[str, Any]:
    """Load and validate a UTF-8 enum catalog."""

    enums = _read_yaml_mapping(path, label="enum catalog")
    _validate_enum_catalog(enums)
    return enums


def validate_schema_definition(
    schema: Mapping[str, Any],
    enums: Mapping[str, Any] | None = None,
) -> None:
    """Validate table, field, primary-key and enum-reference definitions."""

    if not isinstance(schema, Mapping):
        raise SchemaValidationError("schema root must be a mapping")
    if not isinstance(schema.get("schema_version"), str) or not schema["schema_version"]:
        raise SchemaValidationError("schema requires schema_version")
    tables = schema.get("tables")
    if not isinstance(tables, Mapping) or not tables:
        raise SchemaValidationError("schema tables must be a non-empty mapping")

    enum_values: Mapping[str, Any] | None = None
    if enums is not None:
        if not isinstance(enums, Mapping):
            raise SchemaValidationError("enum catalog must be a mapping")
        _validate_enum_catalog(enums)
        enum_values = enums["enums"]

    for table_name, table in tables.items():
        if not isinstance(table_name, str) or not table_name:
            raise SchemaValidationError("table names must be non-empty strings")
        if not isinstance(table, Mapping):
            raise SchemaValidationError(f"table {table_name!r} must be a mapping")
        fields = table.get("fields")
        if not isinstance(fields, Mapping) or not fields:
            raise SchemaValidationError(f"table {table_name!r} fields must be a non-empty mapping")
        for field_name, field in fields.items():
            if not isinstance(field_name, str) or not field_name:
                raise SchemaValidationError(f"table {table_name!r} has an invalid field name")
            if not isinstance(field, Mapping):
                raise SchemaValidationError(
                    f"field {table_name}.{field_name} must be a mapping"
                )
            field_type = field.get("type")
            if field_type not in SUPPORTED_TYPES:
                raise SchemaValidationError(
                    f"field {table_name}.{field_name} has unsupported type {field_type!r}"
                )
            if "required" in field and not isinstance(field["required"], bool):
                raise SchemaValidationError(
                    f"field {table_name}.{field_name} required must be boolean"
                )
            enum_name = field.get("enum")
            if enum_name is not None:
                if not isinstance(enum_name, str) or not enum_name:
                    raise SchemaValidationError(
                        f"field {table_name}.{field_name} enum must be a string"
                    )
                if enum_values is not None and enum_name not in enum_values:
                    raise SchemaValidationError(
                        f"field {table_name}.{field_name} references unknown enum {enum_name!r}"
                    )

        primary_key = table.get("primary_key")
        if not isinstance(primary_key, list) or not primary_key:
            raise SchemaValidationError(
                f"table {table_name!r} primary_key must be a non-empty list"
            )
        if len(set(primary_key)) != len(primary_key) or any(
            not isinstance(field_name, str) or field_name not in fields
            for field_name in primary_key
        ):
            raise SchemaValidationError(
                f"table {table_name!r} primary_key must reference unique defined fields"
            )


def load_schema(path: str | Path) -> dict[str, Any]:
    """Load and validate a UTF-8 field dictionary."""

    schema = _read_yaml_mapping(path, label="schema")
    validate_schema_definition(schema)
    return schema


def _matches_type(value: object, type_name: str) -> bool:
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "date":
        if isinstance(value, datetime):
            return False
        if isinstance(value, date):
            return True
        if isinstance(value, str):
            try:
                date.fromisoformat(value)
            except ValueError:
                return False
            return True
        return False
    if type_name == "datetime":
        if isinstance(value, datetime):
            return True
        if isinstance(value, str):
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return False
            return True
        return False
    if type_name == "json":
        try:
            json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            return False
        return True
    raise AssertionError(f"unsupported validated type: {type_name}")


def validate_record(
    table_name: str,
    record: Mapping[str, Any],
    schema: Mapping[str, Any],
    enums: Mapping[str, Any] | None = None,
    *,
    allow_extra: bool = False,
) -> None:
    """Validate one record against a named table, raising on the first error."""

    validate_schema_definition(schema, enums)
    tables = schema["tables"]
    if table_name not in tables:
        raise SchemaValidationError(f"unknown table {table_name!r}")
    if not isinstance(record, Mapping):
        raise SchemaValidationError("record must be a mapping")

    table = tables[table_name]
    fields = table["fields"]
    unknown_fields = sorted(set(record) - set(fields))
    if unknown_fields and not allow_extra:
        raise SchemaValidationError(
            f"unknown field(s) for {table_name}: {', '.join(unknown_fields)}"
        )

    primary_key = set(table["primary_key"])
    enum_values = enums["enums"] if enums is not None else {}
    for field_name, field in fields.items():
        required = field.get("required", False) or field_name in primary_key
        if field_name not in record or record[field_name] is None:
            if required:
                raise SchemaValidationError(
                    f"required field {table_name}.{field_name} is missing or null"
                )
            continue
        value = record[field_name]
        field_type = field["type"]
        if not _matches_type(value, field_type):
            raise SchemaValidationError(
                f"field {table_name}.{field_name} must have type {field_type}"
            )
        enum_name = field.get("enum")
        if enum_name is not None:
            if enums is None:
                raise SchemaValidationError(
                    f"enum catalog required for field {table_name}.{field_name}"
                )
            if value not in enum_values[enum_name]:
                raise SchemaValidationError(
                    f"field {table_name}.{field_name} is not in enum {enum_name!r}"
                )
