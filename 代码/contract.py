"""Load and validate the versioned TPU database contract bundle.

This module extends the v0.1 field-dictionary validation with relational
constraints that must be executable before a v0.2 schema can be frozen.  It
does not build a database or read project data.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

import duckdb
import yaml

from quality_rules import (
    RuleCatalogValidationError,
    validate_rule_catalog as validate_quality_rule_catalog,
)
from schema import SchemaValidationError, validate_schema_definition
from record_identity import (
    canonical_identity_json,
    stable_record_uid,
    stable_revision_id,
)


_ALLOWED_ON_DELETE = frozenset({"restrict", "cascade", "set_null", "no_action"})
_ALLOWED_FK_CARDINALITIES = frozenset({"many_to_one", "one_to_one"})
_ALLOWED_REVISION_POLICIES = frozenset(
    {"append_only", "immutable_append_only", "immutable_after_freeze"}
)
_ID_ALGORITHM_VERSION = "uuid5-v1"
_LOGICAL_HASH_ALGORITHM_VERSION = "tpu-logical-hash/1"
_LOWER_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_IMPLEMENTATION_REF = re.compile(
    r"^[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+$"
)
_LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UUID5 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_STORAGE_TYPES = {
    "string": ({"string", "large_string"}, {"VARCHAR", "TEXT", "JSON"}),
    "integer": ({"int32", "int64"}, {"INTEGER", "BIGINT"}),
    "number": ({"float32", "float64"}, {"REAL", "DOUBLE"}),
    "boolean": ({"bool"}, {"BOOLEAN"}),
    "date": ({"date32", "date64"}, {"DATE"}),
    "datetime": (
        {"timestamp[us, tz=UTC]", "timestamp[ms, tz=UTC]"},
        {"TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ"},
    ),
    "json": ({"string", "large_string"}, {"JSON"}),
}


@dataclass(frozen=True)
class ContractBundle:
    """One validated contract bundle and its canonical document hashes."""

    schema_version: str
    schema: dict[str, Any]
    enums: dict[str, Any]
    rules: dict[str, Any]
    document_hashes: dict[str, str]


class ContractValidationError(ValueError):
    """Raised when a contract document violates the frozen input format.

    ``code``, ``table`` and ``constraint`` are retained separately so callers
    can emit structured JSON without parsing the human-readable message.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "contract_invalid",
        table: str | None = None,
        constraint: str | None = None,
    ) -> None:
        self.message = message
        self.code = code
        self.table = table
        self.constraint = constraint
        context = []
        if table is not None:
            context.append(f"table={table!r}")
        if constraint is not None:
            context.append(f"constraint={constraint!r}")
        suffix = f" [{', '.join(context)}]" if context else ""
        super().__init__(f"{code}: {message}{suffix}")

    def as_dict(self) -> dict[str, str]:
        """Return the stable machine-facing representation of this error."""

        result = {"code": self.code, "message": self.message}
        if self.table is not None:
            result["table"] = self.table
        if self.constraint is not None:
            result["constraint"] = self.constraint
        return result


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys at every mapping depth."""


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
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable YAML key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise ContractValidationError(
                f"duplicate YAML key: {key!r}",
                code="document_duplicate_key",
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _fail(
    message: str,
    *,
    code: str,
    table: str | None = None,
    constraint: str | None = None,
) -> NoReturn:
    raise ContractValidationError(
        message,
        code=code,
        table=table,
        constraint=constraint,
    )


def _load_yaml_mapping(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        with source.open("r", encoding="utf-8") as stream:
            document = yaml.load(stream, Loader=_UniqueKeyLoader)
    except (OSError, yaml.YAMLError) as error:
        raise ContractValidationError(
            f"cannot load YAML document {source}: {error}",
            code="document_load_failed",
        ) from error
    if not isinstance(document, dict):
        raise ContractValidationError(
            f"YAML document {source} root must be a mapping",
            code="document_root_invalid",
        )
    return document


def _constraint_entries(
    table_name: str,
    table: Mapping[str, Any],
    key: str,
) -> list[Mapping[str, Any]]:
    value = table.get(key, [])
    if not isinstance(value, list):
        _fail(
            f"{key} must be a list",
            code="constraint_collection_invalid",
            table=table_name,
        )
    entries: list[Mapping[str, Any]] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, Mapping):
            _fail(
                f"{key}[{index}] must be a mapping",
                code="constraint_definition_invalid",
                table=table_name,
                constraint=f"{key}[{index}]",
            )
        entries.append(entry)
    return entries


def _constraint_name(
    table_name: str,
    entry: Mapping[str, Any],
    kind: str,
    index: int,
    names: set[str],
) -> str:
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        _fail(
            f"{kind} requires a non-empty name",
            code="constraint_name_invalid",
            table=table_name,
            constraint=f"{kind}[{index}]",
        )
    if _LOWER_SNAKE_CASE.fullmatch(name) is None:
        _fail(
            f"{kind} name must use trimmed lower_snake_case",
            code="constraint_name_invalid",
            table=table_name,
            constraint=name,
        )
    if name in names:
        _fail(
            f"constraint name {name!r} is not unique within table",
            code="constraint_name_duplicate",
            table=table_name,
            constraint=name,
        )
    names.add(name)
    return name


def _defined_fields(
    value: object,
    *,
    table_name: str,
    constraint_name: str,
    available: Mapping[str, Any],
    role: str,
    unknown_kind: str = "field",
) -> list[str]:
    if not isinstance(value, list) or not value:
        _fail(
            f"{role} fields must be a non-empty list",
            code="constraint_fields_invalid",
            table=table_name,
            constraint=constraint_name,
        )
    if any(not isinstance(field, str) or not field for field in value):
        _fail(
            f"{role} fields must contain non-empty strings",
            code="constraint_fields_invalid",
            table=table_name,
            constraint=constraint_name,
        )
    fields = list(value)
    if len(set(fields)) != len(fields):
        _fail(
            f"{role} fields must not contain duplicates",
            code="constraint_fields_duplicate",
            table=table_name,
            constraint=constraint_name,
        )
    unknown = [field for field in fields if field not in available]
    if unknown:
        _fail(
            f"{role} references unknown {unknown_kind} {unknown[0]!r}",
            code="constraint_unknown_field",
            table=table_name,
            constraint=constraint_name,
        )
    return fields


def _validate_table_constraints(tables: Mapping[str, Any]) -> None:
    for table_name, table_value in tables.items():
        # Base schema validation has already checked these shapes.  Retaining
        # this guard keeps this helper fail-fast if called independently later.
        if not isinstance(table_value, Mapping):
            _fail(
                "table definition must be a mapping",
                code="table_definition_invalid",
                table=str(table_name),
            )
        fields = table_value.get("fields")
        if not isinstance(fields, Mapping):
            _fail(
                "table fields must be a mapping",
                code="table_fields_invalid",
                table=str(table_name),
            )
        if not isinstance(table_name, str) or _LOWER_SNAKE_CASE.fullmatch(table_name) is None:
            _fail(
                "table name must use trimmed lower_snake_case",
                code="table_name_invalid",
                table=str(table_name),
            )
        description = table_value.get("description")
        if (
            not isinstance(description, str)
            or not description.strip()
            or description != description.strip()
        ):
            _fail(
                "table description must be a non-empty trimmed string",
                code="table_description_invalid",
                table=table_name,
            )
        revision_policy = table_value.get("revision_policy")
        if revision_policy is not None and revision_policy not in _ALLOWED_REVISION_POLICIES:
            allowed = ", ".join(sorted(_ALLOWED_REVISION_POLICIES))
            _fail(
                f"revision_policy must be one of: {allowed}",
                code="revision_policy_invalid",
                table=table_name,
            )
        if {"target_table", "target_id"}.issubset(fields):
            _fail(
                "unconstrained polymorphic target_table + target_id is forbidden",
                code="polymorphic_target_forbidden",
                table=str(table_name),
            )

        for field_name, field in fields.items():
            if (
                not isinstance(field_name, str)
                or _LOWER_SNAKE_CASE.fullmatch(field_name) is None
            ):
                _fail(
                    "field name must use trimmed lower_snake_case",
                    code="field_name_invalid",
                    table=table_name,
                    constraint=str(field_name),
                )
            field_description = field.get("description")
            if (
                not isinstance(field_description, str)
                or not field_description.strip()
                or field_description != field_description.strip()
            ):
                _fail(
                    f"field {field_name!r} description must be a non-empty trimmed string",
                    code="field_description_invalid",
                    table=table_name,
                    constraint=field_name,
                )
            if "required" not in field or not isinstance(field["required"], bool):
                _fail(
                    f"field {field_name!r} requires an explicit boolean required flag",
                    code="field_required_invalid",
                    table=table_name,
                    constraint=field_name,
                )
            logical_type = field["type"]
            arrow_type = field.get("arrow_type")
            duckdb_type = field.get("duckdb_type")
            if (
                not isinstance(arrow_type, str)
                or not arrow_type.strip()
                or arrow_type != arrow_type.strip()
            ):
                _fail(
                    f"field {field_name!r} requires arrow_type",
                    code="storage_type_missing",
                    table=str(table_name),
                    constraint=str(field_name),
                )
            if (
                not isinstance(duckdb_type, str)
                or not duckdb_type.strip()
                or duckdb_type != duckdb_type.strip()
            ):
                _fail(
                    f"field {field_name!r} requires duckdb_type",
                    code="storage_type_missing",
                    table=str(table_name),
                    constraint=str(field_name),
                )
            allowed_arrow, allowed_duckdb = _STORAGE_TYPES[logical_type]
            if arrow_type not in allowed_arrow or duckdb_type.upper() not in allowed_duckdb:
                _fail(
                    f"field {field_name!r} storage types are incompatible with {logical_type!r}",
                    code="storage_type_incompatible",
                    table=str(table_name),
                    constraint=str(field_name),
                )

        for primary_key_field in table_value["primary_key"]:
            if fields[primary_key_field]["required"] is not True:
                _fail(
                    f"primary key field {primary_key_field!r} must set required=true",
                    code="primary_key_required_invalid",
                    table=table_name,
                    constraint=primary_key_field,
                )

        names: set[str] = set()
        unique_entries = _constraint_entries(table_name, table_value, "unique_constraints")
        foreign_entries = _constraint_entries(table_name, table_value, "foreign_keys")
        check_entries = _constraint_entries(table_name, table_value, "checks")

        for index, entry in enumerate(unique_entries):
            name = _constraint_name(table_name, entry, "unique_constraints", index, names)
            _defined_fields(
                entry.get("fields"),
                table_name=table_name,
                constraint_name=name,
                available=fields,
                role="unique constraint",
            )

        for index, entry in enumerate(foreign_entries):
            name = _constraint_name(table_name, entry, "foreign_keys", index, names)
            local_fields = _defined_fields(
                entry.get("fields"),
                table_name=table_name,
                constraint_name=name,
                available=fields,
                role="foreign key",
            )
            cardinality = entry.get("cardinality")
            if cardinality not in _ALLOWED_FK_CARDINALITIES:
                allowed = ", ".join(sorted(_ALLOWED_FK_CARDINALITIES))
                _fail(
                    f"foreign key cardinality must be one of: {allowed}",
                    code="foreign_key_cardinality_invalid",
                    table=table_name,
                    constraint=name,
                )
            if cardinality == "one_to_one":
                local_candidate_keys = [list(table_value["primary_key"])] + [
                    list(item["fields"]) for item in unique_entries
                ]
                if not any(
                    len(candidate) == len(local_fields)
                    and set(candidate) == set(local_fields)
                    for candidate in local_candidate_keys
                ):
                    _fail(
                        "one_to_one foreign key local fields must be a primary or unique key",
                        code="foreign_key_one_to_one_not_unique",
                        table=table_name,
                        constraint=name,
                    )
            references = entry.get("references")
            if not isinstance(references, Mapping):
                _fail(
                    "foreign key requires references mapping",
                    code="foreign_key_references_invalid",
                    table=table_name,
                    constraint=name,
                )
            target_table_name = references.get("table")
            if not isinstance(target_table_name, str) or not target_table_name:
                _fail(
                    "foreign key references requires a non-empty table",
                    code="foreign_key_target_invalid",
                    table=table_name,
                    constraint=name,
                )
            if target_table_name not in tables:
                _fail(
                    f"foreign key references unknown table {target_table_name!r}",
                    code="foreign_key_unknown_table",
                    table=table_name,
                    constraint=name,
                )
            target_table = tables[target_table_name]
            target_fields_definition = target_table.get("fields")
            target_fields = _defined_fields(
                references.get("fields"),
                table_name=table_name,
                constraint_name=name,
                available=target_fields_definition,
                role=f"foreign key target {target_table_name}",
            )
            if len(local_fields) != len(target_fields):
                _fail(
                    "foreign key local and target field counts must be equal",
                    code="foreign_key_arity_mismatch",
                    table=table_name,
                    constraint=name,
                )
            target_keys = [target_table["primary_key"]] + [
                list(item["fields"])
                for item in target_table.get("unique_constraints", [])
                if isinstance(item, Mapping) and isinstance(item.get("fields"), list)
            ]
            if target_fields not in target_keys:
                _fail(
                    "foreign key target fields are not a primary or unique key",
                    code="foreign_key_target_not_unique",
                    table=table_name,
                    constraint=name,
                )
            for local_field, target_field in zip(local_fields, target_fields, strict=True):
                local_definition = fields[local_field]
                target_definition = target_fields_definition[target_field]
                comparable_keys = ("type", "arrow_type", "duckdb_type")
                if any(
                    str(local_definition.get(key)).upper()
                    != str(target_definition.get(key)).upper()
                    for key in comparable_keys
                ):
                    _fail(
                        f"foreign key field {local_field!r} is incompatible with target {target_table_name}.{target_field}",
                        code="foreign_key_type_mismatch",
                        table=table_name,
                        constraint=name,
                    )
            on_delete = entry.get("on_delete")
            if on_delete not in _ALLOWED_ON_DELETE:
                allowed = ", ".join(sorted(_ALLOWED_ON_DELETE))
                _fail(
                    f"foreign key on_delete must be one of: {allowed}",
                    code="foreign_key_on_delete_invalid",
                    table=table_name,
                    constraint=name,
                )
            if on_delete == "set_null" and any(
                fields[field_name]["required"] is True for field_name in local_fields
            ):
                _fail(
                    "set_null is forbidden for required foreign key fields",
                    code="foreign_key_set_null_required",
                    table=table_name,
                    constraint=name,
                )

        for index, entry in enumerate(check_entries):
            name = _constraint_name(table_name, entry, "checks", index, names)
            expression = entry.get("expression")
            if not isinstance(expression, str) or not expression.strip():
                _fail(
                    "CHECK requires expression",
                    code="check_expression_invalid",
                    table=table_name,
                    constraint=name,
                )


def _validate_duckdb_checks(tables: Mapping[str, Any]) -> None:
    """Parse and bind every declared CHECK against its real DuckDB columns."""

    connection = duckdb.connect(":memory:")
    try:
        for table_name, table in tables.items():
            columns = [
                f'"{field_name}" {field["duckdb_type"]}'
                for field_name, field in table["fields"].items()
            ]
            for check in table.get("checks", []):
                check_name = check["name"]
                ddl = (
                    f'CREATE TEMP TABLE "contract_check_{table_name}" ('
                    + ", ".join(
                        columns
                        + [
                            f'CONSTRAINT "{check_name}" CHECK ({check["expression"]})'
                        ]
                    )
                    + ")"
                )
                try:
                    connection.execute(ddl)
                except duckdb.Error as error:
                    _fail(
                        f"CHECK does not parse and bind in DuckDB: {error}",
                        code="check_duckdb_invalid",
                        table=table_name,
                        constraint=check_name,
                    )
                finally:
                    connection.execute(f'DROP TABLE IF EXISTS "contract_check_{table_name}"')
    finally:
        connection.close()


def _validate_conditional_required(
    schema: Mapping[str, Any], enums: Mapping[str, Any]
) -> None:
    rules = schema.get("conditional_required", [])
    if not isinstance(rules, list):
        _fail(
            "conditional_required must be a list",
            code="conditional_collection_invalid",
        )
    tables = schema["tables"]
    seen_names: set[tuple[str, str]] = set()
    for index, rule in enumerate(rules):
        if not isinstance(rule, Mapping):
            _fail(
                f"conditional_required[{index}] must be a mapping",
                code="conditional_definition_invalid",
                constraint=f"conditional_required[{index}]",
            )
        name = rule.get("name")
        if not isinstance(name, str) or not name.strip():
            _fail(
                "conditional rule requires a non-empty name",
                code="conditional_name_invalid",
                constraint=f"conditional_required[{index}]",
            )
        if _LOWER_SNAKE_CASE.fullmatch(name) is None:
            _fail(
                "conditional rule name must use trimmed lower_snake_case",
                code="conditional_name_invalid",
                constraint=name,
            )
        table_name = rule.get("table")
        if not isinstance(table_name, str) or not table_name:
            _fail(
                "conditional rule requires a non-empty table",
                code="conditional_table_invalid",
                constraint=name,
            )
        if table_name not in tables:
            _fail(
                f"conditional rule references unknown table {table_name!r}",
                code="conditional_unknown_table",
                table=table_name,
                constraint=name,
            )
        identity = (table_name, name)
        if identity in seen_names:
            _fail(
                f"conditional rule name {name!r} is not unique within table",
                code="conditional_name_duplicate",
                table=table_name,
                constraint=name,
            )
        seen_names.add(identity)
        fields = tables[table_name]["fields"]
        when = rule.get("when")
        if not isinstance(when, Mapping):
            _fail(
                "conditional rule requires when mapping",
                code="conditional_when_invalid",
                table=table_name,
                constraint=name,
            )
        when_field = when.get("field")
        if not isinstance(when_field, str) or when_field not in fields:
            _fail(
                f"conditional rule references unknown when field {when_field!r}",
                code="conditional_unknown_when_field",
                table=table_name,
                constraint=name,
            )
        if "equals" not in when:
            _fail(
                "conditional rule when requires equals",
                code="conditional_when_invalid",
                table=table_name,
                constraint=name,
            )
        when_definition = fields[when_field]
        enum_name = when_definition.get("enum")
        if enum_name is not None:
            equals_value = when["equals"]
            enum_values = enums["enums"][enum_name]
            if not any(
                type(equals_value) is type(candidate) and equals_value == candidate
                for candidate in enum_values
            ):
                _fail(
                    f"conditional equals value {equals_value!r} is not in enum {enum_name!r}",
                    code="conditional_enum_value_invalid",
                    table=table_name,
                    constraint=name,
                )
        _defined_fields(
            rule.get("require"),
            table_name=table_name,
            constraint_name=name,
            available=fields,
            role="conditional rule",
            unknown_kind="required field",
        )


def _repository_test_index(test_root: Path | None = None) -> set[str]:
    """Return reproducible pytest function/node identifiers without executing tests."""

    root = test_root or (Path(__file__).resolve().parent / "测试")
    by_name: dict[str, list[str]] = defaultdict(list)
    node_ids: set[str] = set()
    if not root.is_dir():
        return node_ids
    for path in sorted(root.glob("test_*.py"), key=lambda item: item.name):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as error:
            _fail(
                f"cannot index pytest file {path}: {error}",
                code="rule_test_index_invalid",
            )
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test_"
            ):
                short_node = f"{path.name}::{node.name}"
                repository_node = f"代码/测试/{short_node}"
                node_ids.update({short_node, repository_node})
                by_name[node.name].append(short_node)
    node_ids.update(name for name, matches in by_name.items() if len(matches) == 1)
    return node_ids


def _resolve_implementation(reference: str) -> object:
    if _IMPLEMENTATION_REF.fullmatch(reference) is None:
        _fail(
            f"quality rule implementation_ref {reference!r} is not module.symbol",
            code="rule_implementation_invalid",
            constraint=reference,
        )
    module_name, symbol_path = reference.split(".", 1)
    try:
        module = importlib.import_module(module_name)
        module_file = getattr(module, "__file__", None)
        code_root = Path(__file__).resolve().parent
        if module_file is None or not Path(module_file).resolve().is_relative_to(code_root):
            _fail(
                f"quality rule implementation_ref {reference!r} is outside repository code",
                code="rule_implementation_outside_repository",
                constraint=reference,
            )
        value: object = module
        for component in symbol_path.split("."):
            value = getattr(value, component)
    except (ImportError, AttributeError) as error:
        _fail(
            f"quality rule implementation_ref {reference!r} cannot be resolved",
            code="rule_implementation_missing",
            constraint=reference,
        )
    if not callable(value):
        _fail(
            f"quality rule implementation_ref {reference!r} is not callable",
            code="rule_implementation_invalid",
            constraint=reference,
        )
    return value


def _validate_rule_catalog(rules: Mapping[str, Any]) -> None:
    catalog_version = rules.get("catalog_version")
    if not isinstance(catalog_version, str) or not catalog_version.strip():
        _fail(
            "rule catalog requires catalog_version",
            code="rule_catalog_version_invalid",
        )
    definitions = rules.get("rules")
    if not isinstance(definitions, Mapping) or not definitions:
        _fail(
            "rule catalog rules must be a non-empty mapping",
            code="rule_catalog_invalid",
        )
    for rule_id, definition in definitions.items():
        if not isinstance(rule_id, str) or not rule_id.strip():
            _fail("rule IDs must be non-empty strings", code="rule_id_invalid")
        if not isinstance(definition, Mapping):
            _fail(
                f"rule {rule_id!r} must be a mapping",
                code="rule_definition_invalid",
                constraint=rule_id,
            )
    try:
        validate_quality_rule_catalog(rules)
    except RuleCatalogValidationError as error:
        raise ContractValidationError(
            str(error), code="rule_catalog_invalid"
        ) from error
    available_tests = _repository_test_index()
    for rule_id, definition in definitions.items():
        implementation_ref = str(definition["implementation_ref"])
        _resolve_implementation(implementation_ref)
        missing_tests = sorted(set(definition["test_ids"]) - available_tests)
        if missing_tests:
            _fail(
                f"quality rule {rule_id!r} references missing pytest id(s): "
                + ", ".join(missing_tests),
                code="rule_test_missing",
                constraint=str(rule_id),
            )


def _table_rows(
    tables: Mapping[str, Sequence[Mapping[str, object]]], table_name: str
) -> list[Mapping[str, object]]:
    rows = tables.get(table_name, [])
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        _fail(
            f"QC table {table_name!r} must be a sequence of mappings",
            code="qc_input_invalid",
            table=table_name,
        )
    result: list[Mapping[str, object]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            _fail(
                f"QC row {index} must be a mapping",
                code="qc_input_invalid",
                table=table_name,
            )
        result.append(row)
    return result


def _canonical_json_value(value: object, *, label: str) -> tuple[object, str]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            _fail(f"{label} is not valid JSON: {error}", code="canonical_json_invalid")
        canonical = canonical_identity_json(parsed)
        if value != canonical:
            _fail(
                f"{label} is not canonical JSON",
                code="canonical_json_not_canonical",
            )
        return parsed, canonical
    try:
        canonical = canonical_identity_json(value)
    except (TypeError, ValueError) as error:
        _fail(f"{label} cannot be canonicalized: {error}", code="canonical_json_invalid")
    return value, canonical


def _parse_timestamp(value: object, *, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            _fail(f"{label} must be an ISO-8601 datetime", code="datetime_invalid")
    else:
        _fail(f"{label} must be an ISO-8601 datetime", code="datetime_invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(f"{label} must include a timezone", code="datetime_invalid")
    return parsed


def _ensure_acyclic_edges(
    edges: Mapping[str, Sequence[str]], *, code: str, label: str
) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            _fail(f"{label} contains a cycle at {node!r}", code=code)
        if node in visited:
            return
        visiting.add(node)
        for parent in edges.get(node, []):
            visit(parent)
        visiting.remove(node)
        visited.add(node)

    for node in edges:
        visit(node)


def validate_record_identity_rows(
    rows: Sequence[Mapping[str, object]],
) -> None:
    """Recompute registry JSON, SHA-256, algorithm version, and UUIDv5."""

    for index, row in enumerate(rows):
        algorithm = row.get("id_algorithm_version")
        if algorithm != _ID_ALGORITHM_VERSION:
            _fail(
                f"record_registry[{index}] must use {_ID_ALGORITHM_VERSION}",
                code="record_identity_algorithm_mismatch",
                table="record_registry",
            )
        entity_type = row.get("entity_type")
        if not isinstance(entity_type, str) or not entity_type.strip():
            _fail(
                f"record_registry[{index}] entity_type is invalid",
                code="record_identity_invalid",
                table="record_registry",
            )
        identity_key, canonical = _canonical_json_value(
            row.get("canonical_identity_key_json"),
            label=f"record_registry[{index}].canonical_identity_key_json",
        )
        expected_sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if row.get("identity_key_sha256") != expected_sha:
            _fail(
                f"record_registry[{index}] identity_key_sha256 does not match canonical JSON",
                code="record_identity_hash_mismatch",
                table="record_registry",
            )
        expected_uid = stable_record_uid(
            entity_type,
            identity_key,
            algorithm_version=_ID_ALGORITHM_VERSION,
        )
        if row.get("record_uid") != expected_uid or _UUID5.fullmatch(expected_uid) is None:
            _fail(
                f"record_registry[{index}] record_uid is not the recomputed UUIDv5",
                code="record_identity_uid_mismatch",
                table="record_registry",
            )


def validate_locator_rows(rows: Sequence[Mapping[str, object]]) -> None:
    """Recompute canonical locator hashes and validate type-specific JSON shape."""

    required_keys = {
        "file": {"relative_path"},
        "repository_path": {"relative_path"},
        "sheet": {"sheet_name"},
        "page": {"page_number"},
        "table": {"table_identifier"},
        "figure": {"figure_identifier"},
        "cell_range": {"sheet_name", "cell_range"},
        "row_column": {"row_number", "column_number"},
        "json_pointer": {"json_pointer"},
        "text_span": {"start_offset", "end_offset"},
    }
    for index, row in enumerate(rows):
        if row.get("locator_hash_algorithm_version") != "tpu-locator-json/1":
            _fail(
                f"source_locator[{index}] locator hash algorithm mismatch",
                code="locator_hash_algorithm_mismatch",
                table="source_locator",
            )
        payload, canonical = _canonical_json_value(
            row.get("locator_json"), label=f"source_locator[{index}].locator_json"
        )
        if not isinstance(payload, Mapping):
            _fail("locator JSON must be an object", code="locator_shape_invalid")
        locator_type = row.get("locator_type")
        if locator_type not in required_keys:
            _fail("locator_type is unsupported", code="locator_shape_invalid")
        if not required_keys[str(locator_type)] <= set(payload):
            _fail("locator JSON lacks type-specific keys", code="locator_shape_invalid")
        expected_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if row.get("locator_hash") != expected_hash:
            _fail("locator hash does not match canonical JSON", code="locator_hash_mismatch")
        if locator_type in {"file", "repository_path"}:
            path = payload.get("relative_path")
            if (
                not isinstance(path, str)
                or not path
                or path != path.strip()
                or path.startswith("/")
                or path.endswith("/")
                or "\\" in path
                or "//" in path
                or re.match(r"^[A-Za-z]:", path)
                or any(part in {"", ".", ".."} for part in path.split("/"))
            ):
                _fail("locator relative_path is unsafe", code="locator_shape_invalid")
        elif locator_type == "page":
            page = payload.get("page_number")
            if not isinstance(page, int) or isinstance(page, bool) or page < 1:
                _fail("page locator requires positive page_number", code="locator_shape_invalid")
        elif locator_type == "row_column":
            if any(
                not isinstance(payload.get(key), int)
                or isinstance(payload.get(key), bool)
                or int(payload[key]) < 1
                for key in ("row_number", "column_number")
            ):
                _fail("row_column locator requires positive integers", code="locator_shape_invalid")
        elif locator_type == "text_span":
            start = payload.get("start_offset")
            end = payload.get("end_offset")
            if (
                not isinstance(start, int)
                or isinstance(start, bool)
                or not isinstance(end, int)
                or isinstance(end, bool)
                or start < 0
                or end <= start
            ):
                _fail("text_span locator offsets are invalid", code="locator_shape_invalid")
        elif locator_type == "json_pointer":
            pointer = payload.get("json_pointer")
            if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
                _fail("json_pointer locator is invalid", code="locator_shape_invalid")
        else:
            for key in required_keys[str(locator_type)]:
                value = payload.get(key)
                if not isinstance(value, str) or not value.strip():
                    _fail("locator string key is empty", code="locator_shape_invalid")


def validate_citation_rows(rows: Sequence[Mapping[str, object]]) -> None:
    """Require citation text plus structured authors/CSL without Markdown-as-BibTeX."""

    author_required_types = {
        "article",
        "dataset",
        "software",
        "repository",
        "book",
        "standard",
    }
    for index, row in enumerate(rows):
        reference_text = row.get("reference_text")
        title = row.get("title")
        if (
            not isinstance(reference_text, str)
            or not reference_text.strip()
            or reference_text != reference_text.strip()
            or not isinstance(title, str)
            or not title.strip()
            or title.casefold() not in reference_text.casefold()
        ):
            _fail(
                f"citation[{index}] lacks a complete title-bearing reference_text",
                code="citation_reference_text_invalid",
                table="citation",
            )
        doi = row.get("doi")
        if isinstance(doi, str) and doi.strip() and doi.casefold() not in reference_text.casefold():
            _fail(
                f"citation[{index}] reference_text omits its DOI",
                code="citation_reference_text_invalid",
                table="citation",
            )
        authors, _ = _canonical_json_value(
            row.get("authors_json"), label=f"citation[{index}].authors_json"
        )
        if not isinstance(authors, list):
            _fail("citation authors_json must be an array", code="citation_authors_invalid")
        if row.get("citation_type") in author_required_types and not authors:
            _fail("scholarly citation requires at least one author", code="citation_authors_missing")
        for author in authors:
            if isinstance(author, str):
                valid = bool(author.strip())
            elif isinstance(author, Mapping):
                valid = any(
                    isinstance(author.get(key), str) and bool(str(author[key]).strip())
                    for key in ("literal", "family", "given")
                )
            else:
                valid = False
            if not valid:
                _fail("citation contains an invalid author", code="citation_authors_invalid")
        csl, _ = _canonical_json_value(
            row.get("csl_json"), label=f"citation[{index}].csl_json"
        )
        if not isinstance(csl, Mapping) or csl.get("title") != title:
            _fail("citation CSL title disagrees", code="citation_csl_invalid")
        bibtex = row.get("bibtex_text")
        if bibtex is not None and (
            not isinstance(bibtex, str) or not bibtex.lstrip().startswith("@")
        ):
            _fail("bibtex_text must contain BibTeX, not Markdown", code="citation_bibtex_invalid")


def validate_supersession_chains(
    tables: Mapping[str, Sequence[Mapping[str, object]]],
) -> None:
    """Validate same-object ownership and acyclicity for all supersession links."""

    specifications = {
        "record_revision": (
            "record_revision_id",
            "supersedes_revision_id",
            ("record_uid",),
        ),
        "citation": ("citation_id", "supersedes_citation_id", ("source_id",)),
        "rights_evidence_package": (
            "evidence_package_id",
            "supersedes_evidence_package_id",
            ("target_uid",),
        ),
        "rights_fact": (
            "rights_fact_id",
            "supersedes_rights_fact_id",
            ("source_scope_id", "predicate", "applicability_key"),
        ),
    }
    for table_name, (id_field, parent_field, owner_fields) in specifications.items():
        rows = _table_rows(tables, table_name)
        if not rows:
            continue
        by_id: dict[str, Mapping[str, object]] = {}
        edges: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            row_id = row.get(id_field)
            if not isinstance(row_id, str) or not row_id:
                _fail(
                    f"{table_name}.{id_field} is invalid",
                    code="supersession_identity_invalid",
                    table=table_name,
                )
            if row_id in by_id:
                _fail(
                    f"duplicate {table_name} identity {row_id!r}",
                    code="supersession_identity_duplicate",
                    table=table_name,
                )
            by_id[row_id] = row
        for row_id, row in by_id.items():
            parent_id = row.get(parent_field)
            if parent_id is None:
                continue
            if not isinstance(parent_id, str) or parent_id not in by_id:
                _fail(
                    f"{table_name} supersedes missing row {parent_id!r}",
                    code="supersession_target_missing",
                    table=table_name,
                )
            parent = by_id[parent_id]
            if any(row.get(field) != parent.get(field) for field in owner_fields):
                _fail(
                    f"{table_name} supersession crosses object ownership",
                    code="supersession_owner_mismatch",
                    table=table_name,
                )
            edges[row_id].append(parent_id)
        _ensure_acyclic_edges(
            edges,
            code="supersession_cycle",
            label=f"{table_name} supersession",
        )
    scopes = {
        str(row.get("source_scope_id")): row
        for row in _table_rows(tables, "source_scope")
    }
    relation_edges: dict[str, list[str]] = defaultdict(list)
    for relation in _table_rows(tables, "source_scope_relation"):
        if relation.get("relation_type") != "supersedes":
            continue
        subject_id = str(relation.get("subject_scope_id"))
        object_id = str(relation.get("object_scope_id"))
        subject = scopes.get(subject_id)
        object_row = scopes.get(object_id)
        if (
            subject is None
            or object_row is None
            or subject.get("source_id") != object_row.get("source_id")
        ):
            _fail(
                "source scope supersession crosses or misses source ownership",
                code="supersession_owner_mismatch",
                table="source_scope_relation",
            )
        relation_edges[subject_id].append(object_id)
    _ensure_acyclic_edges(
        relation_edges,
        code="supersession_cycle",
        label="source scope supersession",
    )


def validate_revision_chains(rows: Sequence[Mapping[str, object]]) -> None:
    """Recompute revision content digests/UUIDs and validate backward chains."""

    normalized_rows = list(rows)
    for index, row in enumerate(normalized_rows):
        content, canonical = _canonical_json_value(
            row.get("content_json"), label=f"record_revision[{index}].content_json"
        )
        expected_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if row.get("content_hash") != expected_hash:
            _fail(
                f"record_revision[{index}] content_hash mismatch",
                code="revision_content_hash_mismatch",
                table="record_revision",
            )
        record_uid = row.get("record_uid")
        schema_version = row.get("schema_version")
        if not isinstance(record_uid, str) or not isinstance(schema_version, str):
            _fail(
                f"record_revision[{index}] owner or schema version is invalid",
                code="revision_identity_invalid",
                table="record_revision",
            )
        expected_id = stable_revision_id(record_uid, schema_version, content)
        if row.get("record_revision_id") != expected_id:
            _fail(
                f"record_revision[{index}] UUIDv5 mismatch",
                code="revision_uid_mismatch",
                table="record_revision",
            )
    validate_supersession_chains({"record_revision": normalized_rows})
    by_id = {str(row["record_revision_id"]): row for row in normalized_rows}
    for row in normalized_rows:
        number = row.get("revision_number")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            _fail("revision_number must be positive", code="revision_number_invalid")
        parent_id = row.get("supersedes_revision_id")
        if number == 1 and parent_id is not None:
            _fail("first revision cannot supersede another row", code="revision_chain_invalid")
        if number > 1:
            if parent_id is None:
                _fail("later revision requires predecessor", code="revision_chain_invalid")
            parent_number = by_id[str(parent_id)].get("revision_number")
            if parent_number != number - 1:
                _fail(
                    "revision numbers must increase exactly by one",
                    code="revision_chain_invalid",
                )


def validate_snapshot_integrity(
    tables: Mapping[str, Sequence[Mapping[str, object]]],
    *,
    document_hashes: Mapping[str, str],
    schema_version: str,
) -> None:
    """Validate frozen snapshot hashes, approvals, times, and selected revisions."""

    required_hashes = {"schema", "enums", "rules"}
    if set(document_hashes) != required_hashes or any(
        not isinstance(value, str) or _LOWER_SHA256.fullmatch(value) is None
        for value in document_hashes.values()
    ):
        _fail("document_hashes must contain three lowercase SHA-256 values", code="snapshot_contract_hash_invalid")
    snapshots = {
        str(row.get("snapshot_id")): row
        for row in _table_rows(tables, "dataset_snapshot")
    }
    for snapshot_id, row in snapshots.items():
        if row.get("schema_version") != schema_version:
            _fail(
                f"snapshot {snapshot_id!r} schema version is incompatible with loaded bundle",
                code="snapshot_schema_version_mismatch",
                table="dataset_snapshot",
            )
        actual = {
            "schema": row.get("schema_hash"),
            "enums": row.get("enum_hash"),
            "rules": row.get("rule_catalog_hash"),
        }
        if actual != dict(document_hashes):
            _fail(
                f"snapshot {snapshot_id!r} contract hashes do not match loaded bundle",
                code="snapshot_contract_hash_mismatch",
                table="dataset_snapshot",
            )
        created = _parse_timestamp(row.get("created_at"), label="snapshot.created_at")
        frozen_value = row.get("frozen_at")
        if frozen_value is not None:
            frozen = _parse_timestamp(frozen_value, label="snapshot.frozen_at")
            if frozen < created:
                _fail("snapshot frozen_at precedes created_at", code="snapshot_time_invalid")
        if row.get("snapshot_status") == "frozen":
            if (
                not isinstance(row.get("logical_hash"), str)
                or _LOWER_SHA256.fullmatch(str(row["logical_hash"])) is None
                or row.get("logical_hash_algorithm_version")
                != _LOGICAL_HASH_ALGORITHM_VERSION
            ):
                _fail("frozen snapshot logical hash is invalid", code="snapshot_logical_hash_invalid")
            if frozen_value is None or not row.get("approved_by"):
                _fail("frozen snapshot lacks approval", code="snapshot_approval_invalid")
            if row.get("approved_by") == row.get("created_by"):
                _fail("snapshot creator cannot approve the same freeze", code="snapshot_approval_not_separated")

    revisions = {
        str(row.get("record_revision_id")): row
        for row in _table_rows(tables, "record_revision")
    }
    for selected in _table_rows(tables, "snapshot_record"):
        snapshot_id = str(selected.get("snapshot_id"))
        revision_id = str(selected.get("record_revision_id"))
        if snapshot_id not in snapshots or revision_id not in revisions:
            _fail("snapshot selection references missing row", code="snapshot_selection_missing")
        revision = revisions[revision_id]
        if revision.get("record_uid") != selected.get("record_uid"):
            _fail("snapshot selected revision has another owner", code="snapshot_revision_owner_mismatch")
        if revision.get("schema_version") != schema_version:
            _fail("snapshot selected revision uses incompatible schema", code="snapshot_revision_schema_mismatch")


def validate_status_history(
    tables: Mapping[str, Sequence[Mapping[str, object]]],
    enums: Mapping[str, object],
) -> None:
    """Validate immutable status events and snapshot-bound six-axis selections."""

    enum_catalog = enums.get("enums") if isinstance(enums.get("enums"), Mapping) else enums
    axes = (
        "registration_status",
        "availability_status",
        "parse_status",
        "scientific_admission_status",
        "model_readiness_status",
        "release_status",
    )
    allowed = {axis: set(enum_catalog[axis]) for axis in axes}
    events = _table_rows(tables, "record_status_event")
    by_id: dict[str, Mapping[str, object]] = {}
    chains: dict[tuple[str, str], list[Mapping[str, object]]] = defaultdict(list)
    for row in events:
        event_id = str(row.get("status_event_id"))
        if event_id in by_id:
            _fail("duplicate status event", code="status_event_duplicate")
        axis = row.get("status_axis")
        value = row.get("status_value")
        if axis not in allowed or value not in allowed[str(axis)]:
            _fail("status event axis/value mismatch", code="status_axis_value_mismatch")
        sequence = row.get("event_sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            _fail("status event sequence is invalid", code="status_sequence_invalid")
        effective_at = _parse_timestamp(
            row.get("effective_at"), label="record_status_event.effective_at"
        )
        asserted_at = _parse_timestamp(
            row.get("asserted_at"), label="record_status_event.asserted_at"
        )
        if asserted_at < effective_at:
            _fail("status event was asserted before it became effective", code="status_time_invalid")
        by_id[event_id] = row
        chains[(str(row.get("record_uid")), str(axis))].append(row)
    for (record_uid, axis), chain in chains.items():
        chain.sort(key=lambda row: int(row["event_sequence"]))
        for expected_sequence, row in enumerate(chain, 1):
            if row["event_sequence"] != expected_sequence:
                _fail("status event sequence is not contiguous", code="status_sequence_invalid")
            previous_id = row.get("previous_status_event_id")
            if expected_sequence == 1 and previous_id is not None:
                _fail("first status event cannot have predecessor", code="status_chain_invalid")
            if expected_sequence > 1:
                previous = chain[expected_sequence - 2]
                if previous_id != previous.get("status_event_id"):
                    _fail("status predecessor does not match sequence", code="status_chain_invalid")
                if previous.get("status_value") == row.get("status_value"):
                    _fail("status event cannot repeat unchanged state", code="status_chain_invalid")
            if str(row.get("record_uid")) != record_uid or str(row.get("status_axis")) != axis:
                _fail("status chain ownership mismatch", code="status_chain_invalid")
    snapshots = {
        str(row.get("snapshot_id")): row
        for row in _table_rows(tables, "dataset_snapshot")
    }
    for assignment in _table_rows(tables, "snapshot_record_status"):
        record_uid = assignment.get("record_uid")
        snapshot = snapshots.get(str(assignment.get("snapshot_id")))
        if snapshot is None:
            _fail("snapshot status assignment lacks snapshot", code="snapshot_status_snapshot_missing")
        cutoff = _parse_timestamp(
            snapshot.get("frozen_at") or snapshot.get("created_at"),
            label="snapshot status cutoff",
        )
        for axis in axes:
            value = assignment.get(axis)
            event_id = assignment.get(f"{axis}_event_id")
            event = by_id.get(str(event_id))
            if (
                value not in allowed[axis]
                or event is None
                or event.get("record_uid") != record_uid
                or event.get("entity_type") != assignment.get("entity_type")
                or event.get("status_axis") != axis
                or event.get("status_value") != value
                or _parse_timestamp(
                    event.get("effective_at"), label="selected status event effective_at"
                )
                > cutoff
            ):
                _fail("snapshot status selection does not match its event", code="snapshot_status_event_mismatch")


def validate_lineage_integrity(
    tables: Mapping[str, Sequence[Mapping[str, object]]],
) -> None:
    """Validate lineage DAG, successful conversions, and raw-file reachability."""

    lineage = _table_rows(tables, "record_lineage")
    edges: dict[str, list[str]] = defaultdict(list)
    transformations = {
        str(row.get("transformation_id")): row
        for row in _table_rows(tables, "transformation_run")
    }
    inputs = {
        (str(row.get("transformation_id")), str(row.get("record_uid")))
        for row in _table_rows(tables, "transformation_input")
    }
    outputs = {
        (str(row.get("transformation_id")), str(row.get("record_uid")))
        for row in _table_rows(tables, "transformation_output")
    }
    transform_required = {
        "extracted_from",
        "normalized_from",
        "derived_from",
        "aggregated_from",
        "computed_from",
    }
    for row in lineage:
        child = str(row.get("child_record_uid"))
        parent = str(row.get("parent_record_uid"))
        relation = row.get("relation_type")
        if child == parent:
            _fail("lineage self edge is forbidden", code="lineage_cycle")
        edges[child].append(parent)
        transformation_id = row.get("transformation_id")
        if relation in transform_required:
            if not isinstance(transformation_id, str):
                _fail("lineage conversion requires transformation", code="lineage_transformation_missing")
        if relation == "digitized_from" and transformation_id is None and row.get(
            "evidence_locator_id"
        ) is None:
            _fail("digitized lineage requires transformation or locator", code="lineage_digitization_evidence_missing")
        if transformation_id is not None:
            transformation = transformations.get(str(transformation_id))
            if transformation is None or transformation.get("status") != "succeeded":
                _fail("lineage transformation did not succeed", code="lineage_transformation_not_succeeded")
            if (str(transformation_id), parent) not in inputs or (
                str(transformation_id), child
            ) not in outputs:
                _fail("lineage edge is absent from transformation inputs/outputs", code="lineage_transformation_junction_missing")
    _ensure_acyclic_edges(edges, code="lineage_cycle", label="record lineage")
    original_records = {
        str(row.get("record_uid"))
        for row in _table_rows(tables, "record_source")
        if row.get("source_file_id") is not None
    }

    def reaches_original(node: str, seen: set[str]) -> bool:
        if node in original_records:
            return True
        if node in seen:
            return False
        return any(reaches_original(parent, seen | {node}) for parent in edges.get(node, []))

    for child in edges:
        if not reaches_original(child, set()):
            _fail(
                f"lineage record {child!r} cannot reach an original source file",
                code="lineage_original_unreachable",
            )


_RIGHTS_REASON_MATRIX = {
    "allow": {"EXPLICIT_PERMISSION"},
    "allow_with_obligations": {"OBLIGATIONS_APPLY"},
    "deny": {
        "NC_PURPOSE_MISMATCH",
        "ND_DERIVATIVE_SHARE",
        "ACCESS_WITHDRAWN",
        "EXPLICIT_PROHIBITION",
    },
    "manual_review": {
        "EVIDENCE_MISSING",
        "SCOPE_UNRESOLVED",
        "LICENSE_CONFLICT",
        "TDM_AI_UNSPECIFIED",
        "AUTHOR_PERMISSION_REQUIRED",
        "LINEAGE_INCOMPLETE",
        "OBLIGATION_UNSATISFIED",
    },
}


def validate_rights_evidence_archives(
    rows: Sequence[Mapping[str, object]],
) -> None:
    """Validate that archive status names correspond to archived, verifiable bytes."""

    for index, row in enumerate(rows):
        status = row.get("capture_status")
        archive_path = row.get("body_archive_path")
        body_sha = row.get("body_sha256")
        headers = row.get("response_headers_json")
        if status in {"archived_verified", "archived_unverified"}:
            if (
                not isinstance(archive_path, str)
                or not archive_path
                or archive_path != archive_path.strip()
                or archive_path.startswith("/")
                or archive_path.endswith("/")
                or "\\" in archive_path
                or "//" in archive_path
                or re.match(r"^[A-Za-z]:", archive_path)
                or any(part in {"", ".", ".."} for part in archive_path.split("/"))
                or not isinstance(body_sha, str)
                or _LOWER_SHA256.fullmatch(body_sha) is None
            ):
                _fail(
                    f"rights_evidence_package[{index}] archive bytes are not verifiable",
                    code="rights_archive_body_invalid",
                )
            parsed_headers, _ = _canonical_json_value(
                headers,
                label=f"rights_evidence_package[{index}].response_headers_json",
            )
            if not isinstance(parsed_headers, Mapping):
                _fail("archive headers must be a JSON object", code="rights_archive_body_invalid")
        if status == "archived_verified" and (
            not row.get("verified_by") or row.get("verified_at") is None
        ):
            _fail("archived_verified package lacks independent verification", code="rights_archive_not_verified")
        if status == "captured_unverified" and archive_path is not None:
            _fail("session capture cannot claim an archived body", code="rights_archive_status_invalid")
        fingerprint = row.get("session_fingerprint_sha256")
        fingerprint_scope = row.get("session_fingerprint_scope")
        if (fingerprint is None) != (fingerprint_scope is None):
            _fail("session fingerprint digest and scope must appear together", code="rights_session_fingerprint_invalid")
        if fingerprint is not None and (
            not isinstance(fingerprint, str) or _LOWER_SHA256.fullmatch(fingerprint) is None
        ):
            _fail("session fingerprint digest is invalid", code="rights_session_fingerprint_invalid")


def validate_rights_decision_closure(
    tables: Mapping[str, Sequence[Mapping[str, object]]],
) -> None:
    """Recompute rights evidence closure and fail closed for allow decisions."""

    evidence_by_decision: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in _table_rows(tables, "rights_decision_evidence"):
        evidence_by_decision[str(row.get("rights_decision_id"))].append(row)
    facts = {
        str(row.get("rights_fact_id")): row
        for row in _table_rows(tables, "rights_fact")
    }
    package_rows = _table_rows(tables, "rights_evidence_package")
    validate_rights_evidence_archives(package_rows)
    packages = {
        str(row.get("evidence_package_id")): row
        for row in package_rows
    }
    parent_edges: dict[str, list[str]] = defaultdict(list)
    for row in _table_rows(tables, "record_lineage"):
        parent_edges[str(row.get("child_record_uid"))].append(
            str(row.get("parent_record_uid"))
        )
    _ensure_acyclic_edges(parent_edges, code="lineage_cycle", label="rights lineage")

    def reachable(target: str) -> set[str]:
        result: set[str] = set()
        stack = list(parent_edges.get(target, []))
        while stack:
            node = stack.pop()
            if node not in result:
                result.add(node)
                stack.extend(parent_edges.get(node, []))
        return result

    def roots(target: str) -> set[str]:
        ancestry = reachable(target)
        return {node for node in ancestry if not parent_edges.get(node)}

    for decision in _table_rows(tables, "rights_action_decision"):
        decision_id = str(decision.get("rights_decision_id"))
        outcome = decision.get("decision")
        reason = decision.get("reason_code")
        if outcome not in _RIGHTS_REASON_MATRIX or reason not in _RIGHTS_REASON_MATRIX[str(outcome)]:
            _fail("rights decision/reason combination is invalid", code="rights_decision_reason_invalid")
        evidence = evidence_by_decision.get(decision_id, [])
        pairs = sorted(
            {
                (str(row.get("contributing_record_uid")), str(row.get("rights_fact_id")))
                for row in evidence
            }
        )
        fact_ids = {pair[1] for pair in pairs}
        contributors = {pair[0] for pair in pairs}
        if len(pairs) != len(evidence):
            _fail("rights decision evidence rows are duplicated", code="rights_evidence_duplicate")
        if decision.get("evidence_fact_count") != len(fact_ids) or decision.get(
            "contributing_record_count"
        ) != len(contributors):
            _fail("rights decision evidence counts are not actual row counts", code="rights_evidence_count_mismatch")
        if decision.get("evidence_closure_algorithm_version") != "tpu-rights-closure/1":
            _fail("rights evidence closure algorithm mismatch", code="rights_evidence_algorithm_mismatch")
        expected_hash = hashlib.sha256(
            (
                "tpu-rights-closure/1"
                + json.dumps(
                    [list(pair) for pair in pairs],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            ).encode("utf-8")
        ).hexdigest()
        if decision.get("evidence_closure_hash") != expected_hash:
            _fail("rights evidence closure hash mismatch", code="rights_evidence_hash_mismatch")
        if outcome in {"allow", "allow_with_obligations"}:
            if not pairs:
                _fail("allow decision requires evidence rows", code="rights_allow_evidence_missing")
            target = str(decision.get("target_uid"))
            ancestry = reachable(target)
            if not contributors <= ancestry | {target} or not roots(target) <= contributors:
                _fail("allow evidence contributor is unreachable or lineage roots are missing", code="rights_lineage_closure_invalid")
            for fact_id in fact_ids:
                fact = facts.get(fact_id)
                package = packages.get(str(fact.get("evidence_package_id"))) if fact else None
                if package is None or package.get("capture_status") != "archived_verified":
                    _fail("allow decision requires archived_verified evidence", code="rights_archive_not_verified")


def validate_equivalence_integrity(
    tables: Mapping[str, Sequence[Mapping[str, object]]],
) -> None:
    """Require exactly one canonical field that names a selected membership."""

    memberships: dict[tuple[str, str], list[str]] = defaultdict(list)
    selected = {
        (str(row.get("snapshot_id")), str(row.get("record_uid")))
        for row in _table_rows(tables, "snapshot_record")
    }
    for row in _table_rows(tables, "equivalence_membership"):
        key = (str(row.get("group_id")), str(row.get("snapshot_id")))
        record_uid = str(row.get("record_uid"))
        if record_uid in memberships[key]:
            _fail("equivalence membership is duplicated", code="equivalence_membership_duplicate")
        memberships[key].append(record_uid)
        if (key[1], record_uid) not in selected:
            _fail("equivalence member is not selected by snapshot", code="equivalence_snapshot_member_missing")
    seen_groups: set[tuple[str, str]] = set()
    for group in _table_rows(tables, "equivalence_group"):
        key = (str(group.get("group_id")), str(group.get("snapshot_id")))
        if key in seen_groups:
            _fail("equivalence group identity is duplicated", code="equivalence_group_duplicate")
        seen_groups.add(key)
        canonical = str(group.get("canonical_record_uid"))
        if memberships.get(key, []).count(canonical) != 1:
            _fail("equivalence canonical is not exactly one membership", code="equivalence_canonical_invalid")
    if set(memberships) - seen_groups:
        _fail("equivalence membership references missing group", code="equivalence_group_missing")


def validate_source_family_integrity(
    tables: Mapping[str, Sequence[Mapping[str, object]]],
) -> None:
    """Validate same-source scope trees and evidence-backed family membership."""

    scopes = {
        str(row.get("source_scope_id")): row
        for row in _table_rows(tables, "source_scope")
    }
    edges: dict[str, list[str]] = defaultdict(list)
    for scope_id, row in scopes.items():
        parent_id = row.get("parent_scope_id")
        if parent_id is None:
            continue
        parent = scopes.get(str(parent_id))
        if parent is None or parent.get("source_id") != row.get("source_id"):
            _fail("source scope parent crosses or misses source", code="source_scope_parent_invalid")
        edges[scope_id].append(str(parent_id))
    _ensure_acyclic_edges(edges, code="source_scope_cycle", label="source scope parent")
    families = {
        str(row.get("source_family_id"))
        for row in _table_rows(tables, "source_family")
    }
    for membership in _table_rows(tables, "source_family_membership"):
        if str(membership.get("source_family_id")) not in families:
            _fail("source family membership references missing family", code="source_family_missing")
        scope_id = membership.get("source_scope_id")
        if scope_id is not None:
            scope = scopes.get(str(scope_id))
            if scope is None or scope.get("source_id") != membership.get("source_id"):
                _fail("source family membership crosses source scope", code="source_family_scope_mismatch")
        summary = membership.get("evidence_summary")
        if membership.get("evidence_locator_id") is None and (
            not isinstance(summary, str) or not summary.strip()
        ):
            _fail("source family membership lacks evidence", code="source_family_evidence_missing")


def validate_frozen_count_assertions(
    tables: Mapping[str, Sequence[Mapping[str, object]]],
) -> None:
    """Validate frozen recount rows against one file/scope/hash chain and run."""

    files = {
        str(row.get("source_file_id")): row
        for row in _table_rows(tables, "source_file")
    }
    locators = {
        str(row.get("source_locator_id")): row
        for row in _table_rows(tables, "source_locator")
    }
    transformations = {
        str(row.get("transformation_id")): row
        for row in _table_rows(tables, "transformation_run")
    }
    for assertion in _table_rows(tables, "count_assertion"):
        if assertion.get("assertion_status") != "frozen_fact":
            continue
        if assertion.get("count_evidence_type") != "ingested_file_recount":
            _fail("frozen count is not an ingested recount", code="count_frozen_evidence_invalid")
        file_row = files.get(str(assertion.get("source_file_id")))
        if file_row is None or (
            file_row.get("source_scope_id") != assertion.get("source_scope_id")
            or file_row.get("content_sha256") != assertion.get("source_file_sha256")
        ):
            _fail("frozen count file/scope/hash chain disagrees", code="count_file_chain_mismatch")
        locator_id = assertion.get("source_locator_id")
        if locator_id is not None:
            locator = locators.get(str(locator_id))
            if locator is None or locator.get("source_file_id") != assertion.get("source_file_id"):
                _fail("frozen count locator belongs to another file", code="count_locator_chain_mismatch")
        transformation = transformations.get(str(assertion.get("recount_transformation_id")))
        if transformation is None or transformation.get("status") != "succeeded":
            _fail("frozen count recount did not succeed", code="count_recount_not_succeeded")


def validate_contract_bundle(
    schema: Mapping[str, Any],
    enums: Mapping[str, Any],
    rules: Mapping[str, Any],
) -> None:
    """Validate all documents in a contract bundle, failing on first error."""

    if not isinstance(schema, Mapping):
        _fail("schema root must be a mapping", code="schema_definition_invalid")
    if not isinstance(enums, Mapping):
        _fail("enum catalog root must be a mapping", code="enum_catalog_invalid")
    if not isinstance(rules, Mapping):
        _fail("rule catalog root must be a mapping", code="rule_catalog_invalid")
    try:
        validate_schema_definition(schema, enums)
    except SchemaValidationError as error:
        raise ContractValidationError(
            str(error), code="schema_definition_invalid"
        ) from error

    id_algorithm_version = schema.get("id_algorithm_version")
    if id_algorithm_version != _ID_ALGORITHM_VERSION:
        _fail(
            f"schema id_algorithm_version must equal {_ID_ALGORITHM_VERSION!r}",
            code="id_algorithm_version_invalid",
        )

    versions = {
        schema.get("schema_version"),
        enums.get("schema_version"),
        rules.get("schema_version"),
    }
    if len(versions) != 1 or None in versions:
        _fail(
            "schema, enums and rules require one schema_version",
            code="schema_version_mismatch",
        )
    _validate_table_constraints(schema["tables"])
    _validate_duckdb_checks(schema["tables"])
    _validate_conditional_required(schema, enums)
    _validate_rule_catalog(rules)


def load_contract_bundle(
    schema_path: str | Path,
    enum_path: str | Path,
    rule_path: str | Path,
) -> ContractBundle:
    """Load, validate and canonically hash a three-document contract bundle."""

    documents = {
        "schema": _load_yaml_mapping(schema_path),
        "enums": _load_yaml_mapping(enum_path),
        "rules": _load_yaml_mapping(rule_path),
    }
    validate_contract_bundle(documents["schema"], documents["enums"], documents["rules"])
    hashes = {
        name: hashlib.sha256(
            json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        for name, document in documents.items()
    }
    return ContractBundle(
        schema_version=documents["schema"]["schema_version"],
        schema=documents["schema"],
        enums=documents["enums"],
        rules=documents["rules"],
        document_hashes=hashes,
    )
