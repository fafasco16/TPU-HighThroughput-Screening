from datetime import date, datetime
from pathlib import Path

import pytest
import yaml

from schema import (
    SchemaValidationError,
    load_enums,
    load_schema,
    validate_record,
    validate_schema_definition,
)


ROOT = Path(__file__).resolve().parents[2]


def _schema():
    return {
        "schema_version": "v0.1",
        "tables": {
            "source": {
                "primary_key": ["source_id"],
                "fields": {
                    "source_id": {"type": "string", "required": True},
                    "status": {
                        "type": "string",
                        "required": True,
                        "enum": "source_status",
                    },
                    "size_bytes": {"type": "integer", "required": False},
                    "open": {"type": "boolean", "required": False},
                },
            }
        },
    }


def _enums():
    return {"schema_version": "v0.1", "enums": {"source_status": ["available", "withdrawn"]}}


def test_project_schema_and_enums_load_from_chinese_paths():
    schema = load_schema(ROOT / "配置/结构定义" / "v0.1字段字典.yaml")
    enums = load_enums(ROOT / "配置/结构定义" / "v0.1枚举.yaml")
    validate_schema_definition(schema, enums)
    assert schema["schema_version"] == "v0.1"
    assert "source_file" in schema["tables"]
    assert "license_spdx" in schema["tables"]["source"]["fields"]
    formulation_fields = schema["tables"]["formulation"]["fields"]
    assert {"lineage_family", "lineage_record_id", "split_group"} <= set(
        formulation_fields
    )
    assert "evidence_grade" in enums["enums"]


def test_validate_record_accepts_valid_values_and_optional_null():
    validate_record(
        "source",
        {"source_id": "ds_x", "status": "available", "size_bytes": None, "open": True},
        _schema(),
        _enums(),
    )


@pytest.mark.parametrize(
    ("record", "message"),
    [
        ({"status": "available"}, "required"),
        ({"source_id": "ds_x", "status": "bad"}, "enum"),
        ({"source_id": "ds_x", "status": "available", "size_bytes": 1.5}, "integer"),
        ({"source_id": "ds_x", "status": "available", "open": 1}, "boolean"),
        ({"source_id": "ds_x", "status": "available", "unexpected": 1}, "unknown field"),
    ],
)
def test_validate_record_rejects_missing_enum_type_and_unknown_fields(record, message):
    with pytest.raises(SchemaValidationError, match=message):
        validate_record("source", record, _schema(), _enums())


def test_validate_record_can_allow_extra_fields_and_rejects_unknown_table():
    validate_record(
        "source",
        {"source_id": "ds_x", "status": "available", "extra": "kept upstream"},
        _schema(),
        _enums(),
        allow_extra=True,
    )
    with pytest.raises(SchemaValidationError, match="unknown table"):
        validate_record("missing", {}, _schema(), _enums())


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.pop("schema_version"), "schema_version"),
        (lambda value: value.update(tables=[]), "tables"),
        (lambda value: value["tables"]["source"].update(fields=[]), "fields"),
        (
            lambda value: value["tables"]["source"]["fields"]["source_id"].update(type="blob"),
            "unsupported type",
        ),
        (
            lambda value: value["tables"]["source"].update(primary_key=["missing"]),
            "primary_key",
        ),
        (
            lambda value: value["tables"]["source"]["fields"]["status"].update(enum="missing"),
            "unknown enum",
        ),
    ],
)
def test_validate_schema_definition_rejects_malformed_catalogs(mutation, message):
    value = _schema()
    mutation(value)
    with pytest.raises(SchemaValidationError, match=message):
        validate_schema_definition(value, _enums())


def test_loaders_reject_non_mapping_yaml_and_invalid_enum_catalog(tmp_path):
    bad_schema = tmp_path / "字段.yaml"
    bad_schema.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    with pytest.raises(SchemaValidationError, match="mapping"):
        load_schema(bad_schema)

    bad_enums = tmp_path / "枚举.yaml"
    bad_enums.write_text(
        yaml.safe_dump({"schema_version": "v0.1", "enums": {"bad": []}}),
        encoding="utf-8",
    )
    with pytest.raises(SchemaValidationError, match="non-empty"):
        load_enums(bad_enums)


@pytest.mark.parametrize(
    ("catalog", "message"),
    [
        ({"enums": {"ok": ["x"]}}, "schema_version"),
        ({"schema_version": "v0.1", "enums": []}, "mapping"),
        ({"schema_version": "v0.1", "enums": {"": ["x"]}}, "names"),
        ({"schema_version": "v0.1", "enums": {"bad": [["x"]]}}, "scalar"),
        ({"schema_version": "v0.1", "enums": {"bad": ["x", "x"]}}, "unique"),
    ],
)
def test_enum_catalog_rejects_structural_errors(tmp_path, catalog, message):
    path = tmp_path / "枚举.yaml"
    path.write_text(yaml.safe_dump(catalog, allow_unicode=True), encoding="utf-8")
    with pytest.raises(SchemaValidationError, match=message):
        load_enums(path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["tables"].update({"": value["tables"].pop("source")}), "table names"),
        (lambda value: value["tables"].update(source=[]), "must be a mapping"),
        (
            lambda value: value["tables"]["source"]["fields"].update(
                {"": value["tables"]["source"]["fields"].pop("open")}
            ),
            "invalid field name",
        ),
        (lambda value: value["tables"]["source"]["fields"].update(open=[]), "must be a mapping"),
        (
            lambda value: value["tables"]["source"]["fields"]["open"].update(required="yes"),
            "required must be boolean",
        ),
        (
            lambda value: value["tables"]["source"]["fields"]["status"].update(enum=""),
            "enum must be a string",
        ),
        (lambda value: value["tables"]["source"].update(primary_key=[]), "primary_key"),
    ],
)
def test_schema_definition_additional_structural_errors(mutation, message):
    value = _schema()
    mutation(value)
    with pytest.raises(SchemaValidationError, match=message):
        validate_schema_definition(value, _enums())


def test_schema_definition_rejects_non_mapping_schema_and_enum_catalog():
    with pytest.raises(SchemaValidationError, match="schema root"):
        validate_schema_definition([], _enums())
    with pytest.raises(SchemaValidationError, match="enum catalog"):
        validate_schema_definition(_schema(), [])


def test_record_type_validation_covers_number_date_datetime_and_json():
    schema = {
        "schema_version": "v0.1",
        "tables": {
            "types": {
                "primary_key": ["id"],
                "fields": {
                    "id": {"type": "string", "required": True},
                    "ratio": {"type": "number", "required": True},
                    "day": {"type": "date", "required": True},
                    "time": {"type": "datetime", "required": True},
                    "payload": {"type": "json", "required": True},
                },
            }
        },
    }
    valid_records = [
        {"id": "x", "ratio": 1, "day": date(2026, 7, 18), "time": datetime(2026, 7, 18), "payload": {"中文": 1}},
        {"id": "x", "ratio": 1.5, "day": "2026-07-18", "time": "2026-07-18T12:00:00Z", "payload": [1, 2]},
    ]
    for record in valid_records:
        validate_record("types", record, schema)

    invalid_values = [
        ("ratio", float("nan")),
        ("ratio", True),
        ("day", datetime(2026, 7, 18)),
        ("day", "18-07-2026"),
        ("day", 1),
        ("time", "not-a-time"),
        ("time", date(2026, 7, 18)),
        ("payload", {"bad": float("nan")}),
        ("payload", object()),
    ]
    baseline = valid_records[1]
    for field, bad_value in invalid_values:
        record = {**baseline, field: bad_value}
        with pytest.raises(SchemaValidationError, match=field):
            validate_record("types", record, schema)


def test_validate_record_rejects_non_mapping_and_requires_enums():
    with pytest.raises(SchemaValidationError, match="record"):
        validate_record("source", [], _schema(), _enums())
    with pytest.raises(SchemaValidationError, match="enum catalog required"):
        validate_record(
            "source",
            {"source_id": "ds_x", "status": "available"},
            _schema(),
        )
