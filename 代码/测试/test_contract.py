from copy import deepcopy
from pathlib import Path

import pytest
import yaml

import contract as contract_module
from contract import ContractValidationError, load_contract_bundle, validate_contract_bundle


FIXTURES = Path(__file__).parent / "夹具"


def _documents():
    with (FIXTURES / "v0.2最小合同.yaml").open(encoding="utf-8") as stream:
        schema = yaml.safe_load(stream)
    with (FIXTURES / "v0.2最小枚举.yaml").open(encoding="utf-8") as stream:
        enums = yaml.safe_load(stream)
    with (FIXTURES / "v0.2最小质量规则.yaml").open(encoding="utf-8") as stream:
        rules = yaml.safe_load(stream)
    return schema, enums, rules


def test_contract_bundle_accepts_complete_minimum():
    bundle = load_contract_bundle(
        FIXTURES / "v0.2最小合同.yaml",
        FIXTURES / "v0.2最小枚举.yaml",
        FIXTURES / "v0.2最小质量规则.yaml",
    )
    assert bundle.schema_version == "v0.2"
    assert set(bundle.document_hashes) == {"schema", "enums", "rules"}
    assert all(len(value) == 64 for value in bundle.document_hashes.values())


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda schema: schema["tables"]["snapshot_record"]["foreign_keys"][0][
                "references"
            ].update(table="missing"),
            "unknown table",
        ),
        (
            lambda schema: schema["tables"]["record_registry"]["unique_constraints"][
                0
            ].update(fields=["missing"]),
            "unknown field",
        ),
        (
            lambda schema: schema["tables"]["record_registry"]["checks"][0].pop(
                "expression"
            ),
            "requires expression",
        ),
        (
            lambda schema: schema["conditional_required"][0].update(
                require=["missing"]
            ),
            "unknown required field",
        ),
    ],
)
def test_contract_bundle_rejects_structural_errors(mutation, message):
    schema, enums, rules = _documents()
    broken = deepcopy(schema)
    mutation(broken)
    with pytest.raises(ContractValidationError, match=message):
        validate_contract_bundle(broken, enums, rules)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda schema: schema["tables"]["source_scope"]["unique_constraints"][
                0
            ].update(name=""),
            "non-empty name",
        ),
        (
            lambda schema: schema["tables"]["record_registry"].setdefault(
                "unique_constraints", []
            ).append({"name": "ck_identity_sha256", "fields": ["record_uid"]}),
            "not unique within table",
        ),
        (
            lambda schema: schema["tables"]["source_scope"]["unique_constraints"][
                0
            ].update(fields=[]),
            "non-empty list",
        ),
        (
            lambda schema: schema["tables"]["source_scope"]["unique_constraints"][
                0
            ].update(fields=["scope_kind", "scope_kind"]),
            "must not contain duplicates",
        ),
        (
            lambda schema: schema["tables"]["snapshot_record"]["foreign_keys"][0][
                "references"
            ].update(fields=["record_uid", "entity_type"]),
            "field counts must be equal",
        ),
        (
            lambda schema: schema["tables"]["snapshot_record"]["foreign_keys"][0][
                "references"
            ].update(fields=["missing"]),
            "unknown field",
        ),
        (
            lambda schema: schema["tables"]["snapshot_record"]["foreign_keys"][
                0
            ].update(on_delete="delete_everything"),
            "on_delete must be one of",
        ),
        (
            lambda schema: schema["tables"]["record_registry"]["checks"][0].update(
                expression="   "
            ),
            "requires expression",
        ),
    ],
)
def test_contract_bundle_rejects_all_relational_constraint_errors(mutation, message):
    schema, enums, rules = _documents()
    broken = deepcopy(schema)
    mutation(broken)
    with pytest.raises(ContractValidationError, match=message):
        validate_contract_bundle(broken, enums, rules)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda schema: schema["tables"]["source_scope"]["fields"]["scope_kind"].pop("arrow_type"),
            "requires arrow_type",
        ),
        (
            lambda schema: schema["tables"]["source_scope"]["fields"]["scope_kind"].update(duckdb_type="DOUBLE"),
            "storage types are incompatible",
        ),
        (
            lambda schema: schema["tables"]["source_scope"]["fields"].update(
                {
                    "target_table": {"type": "string", "required": True, "arrow_type": "string", "duckdb_type": "VARCHAR"},
                    "target_id": {"type": "string", "required": True, "arrow_type": "string", "duckdb_type": "VARCHAR"},
                }
            ),
            "unconstrained polymorphic",
        ),
        (
            lambda schema: schema["tables"]["snapshot_record"]["foreign_keys"][0]["references"].update(fields=["entity_type"]),
            "not a primary or unique key",
        ),
        (
            lambda schema: schema["tables"]["snapshot_record"]["fields"]["record_uid"].update(
                type="integer", arrow_type="int64", duckdb_type="BIGINT"
            ),
            "is incompatible with target",
        ),
    ],
)
def test_contract_bundle_rejects_storage_and_polymorphic_errors(mutation, message):
    schema, enums, rules = _documents()
    broken = deepcopy(schema)
    mutation(broken)
    with pytest.raises(ContractValidationError, match=message):
        validate_contract_bundle(broken, enums, rules)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda schema: schema["conditional_required"][0].update(table="missing"),
            "unknown table",
        ),
        (
            lambda schema: schema["conditional_required"][0]["when"].update(
                field="missing"
            ),
            "unknown when field",
        ),
        (
            lambda schema: schema["conditional_required"][0]["when"].pop("equals"),
            "requires equals",
        ),
        (
            lambda schema: schema["conditional_required"].append(
                deepcopy(schema["conditional_required"][0])
            ),
            "not unique within table",
        ),
    ],
)
def test_contract_bundle_rejects_conditional_rule_errors(mutation, message):
    schema, enums, rules = _documents()
    broken = deepcopy(schema)
    mutation(broken)
    with pytest.raises(ContractValidationError, match=message):
        validate_contract_bundle(broken, enums, rules)


def test_contract_error_exposes_machine_context():
    schema, enums, rules = _documents()
    schema["tables"]["snapshot_record"]["foreign_keys"][0]["references"][
        "table"
    ] = "missing"
    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)
    assert captured.value.as_dict() == {
        "code": "foreign_key_unknown_table",
        "message": "foreign key references unknown table 'missing'",
        "table": "snapshot_record",
        "constraint": "fk_snapshot_record_registry",
    }


def test_contract_bundle_rejects_schema_version_mismatch():
    schema, enums, rules = _documents()
    rules["schema_version"] = "v0.3"
    with pytest.raises(ContractValidationError, match="one schema_version"):
        validate_contract_bundle(schema, enums, rules)


def test_contract_loader_rejects_non_mapping_yaml(tmp_path):
    schema, enums, rules = _documents()
    bad_schema = tmp_path / "bad.yaml"
    bad_schema.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    enum_path = tmp_path / "enums.yaml"
    enum_path.write_text(yaml.safe_dump(enums, allow_unicode=True), encoding="utf-8")
    rule_path = tmp_path / "rules.yaml"
    rule_path.write_text(yaml.safe_dump(rules, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ContractValidationError, match="root must be a mapping"):
        load_contract_bundle(bad_schema, enum_path, rule_path)


def test_contract_document_hashes_are_semantic_and_deterministic(tmp_path):
    schema, enums, rules = _documents()
    schema_path = tmp_path / "schema.yaml"
    enum_path = tmp_path / "enums.yaml"
    rule_path = tmp_path / "rules.yaml"
    schema_path.write_text(
        yaml.safe_dump(schema, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    enum_path.write_text(
        yaml.safe_dump(enums, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    rule_path.write_text(
        yaml.safe_dump(rules, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    first = load_contract_bundle(schema_path, enum_path, rule_path)
    schema_path.write_text(
        yaml.safe_dump(schema, allow_unicode=True, sort_keys=True), encoding="utf-8"
    )
    second = load_contract_bundle(schema_path, enum_path, rule_path)
    assert second.document_hashes == first.document_hashes


@pytest.mark.parametrize(
    "schema_source",
    [
        "schema_version: [\n",
        "tables:\n  broken: {\n",
    ],
)
def test_contract_loader_wraps_yaml_parse_errors(tmp_path, schema_source):
    _, enums, rules = _documents()
    schema_path = tmp_path / "malformed-schema.yaml"
    enum_path = tmp_path / "enums.yaml"
    rule_path = tmp_path / "rules.yaml"
    schema_path.write_text(schema_source, encoding="utf-8")
    enum_path.write_text(
        yaml.safe_dump(enums, allow_unicode=True), encoding="utf-8"
    )
    rule_path.write_text(
        yaml.safe_dump(rules, allow_unicode=True), encoding="utf-8"
    )

    with pytest.raises(ContractValidationError) as captured:
        load_contract_bundle(schema_path, enum_path, rule_path)

    assert captured.value.code == "document_load_failed"
    assert captured.value.as_dict()["message"].startswith("cannot load YAML document")


def test_contract_loader_wraps_missing_file_error(tmp_path):
    with pytest.raises(ContractValidationError) as captured:
        load_contract_bundle(
            tmp_path / "does-not-exist.yaml",
            FIXTURES / "v0.2最小枚举.yaml",
            FIXTURES / "v0.2最小质量规则.yaml",
        )

    assert captured.value.code == "document_load_failed"
    assert "does-not-exist.yaml" in captured.value.message


def test_contract_loader_rejects_unhashable_yaml_mapping_key(tmp_path):
    schema_path = tmp_path / "unhashable-key.yaml"
    schema_path.write_text("? [schema, version]\n: v0.2\n", encoding="utf-8")

    with pytest.raises(ContractValidationError) as captured:
        load_contract_bundle(
            schema_path,
            FIXTURES / "v0.2最小枚举.yaml",
            FIXTURES / "v0.2最小质量规则.yaml",
        )

    assert captured.value.code == "document_load_failed"
    assert "unhashable YAML key" in captured.value.message


@pytest.mark.parametrize(
    ("table_key", "invalid_value", "expected_code"),
    [
        ("unique_constraints", {}, "constraint_collection_invalid"),
        ("foreign_keys", "not-a-list", "constraint_collection_invalid"),
        ("checks", ["not-a-mapping"], "constraint_definition_invalid"),
    ],
)
def test_contract_bundle_rejects_malformed_constraint_collections(
    table_key, invalid_value, expected_code
):
    schema, enums, rules = _documents()
    schema["tables"]["snapshot_record"][table_key] = invalid_value

    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)

    assert captured.value.code == expected_code
    assert captured.value.table == "snapshot_record"


def test_contract_bundle_rejects_non_string_constraint_field():
    schema, enums, rules = _documents()
    schema["tables"]["source_scope"]["unique_constraints"][0]["fields"] = [
        "scope_kind",
        "",
    ]

    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)

    assert captured.value.code == "constraint_fields_invalid"
    assert "non-empty strings" in captured.value.message


@pytest.mark.parametrize(
    ("tables", "expected_code"),
    [
        ({"broken": []}, "table_definition_invalid"),
        ({"broken": {"fields": []}}, "table_fields_invalid"),
    ],
)
def test_table_constraint_helper_fails_fast_on_invalid_internal_shape(
    tables, expected_code
):
    with pytest.raises(ContractValidationError) as captured:
        contract_module._validate_table_constraints(tables)

    assert captured.value.code == expected_code
    assert captured.value.table == "broken"


def test_contract_bundle_requires_duckdb_storage_type():
    schema, enums, rules = _documents()
    schema["tables"]["source_scope"]["fields"]["scope_kind"].pop("duckdb_type")

    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)

    assert captured.value.code == "storage_type_missing"
    assert captured.value.constraint == "scope_kind"
    assert "requires duckdb_type" in captured.value.message


@pytest.mark.parametrize(
    ("references", "expected_code"),
    [
        (None, "foreign_key_references_invalid"),
        ({"table": "", "fields": ["record_uid"]}, "foreign_key_target_invalid"),
    ],
)
def test_contract_bundle_rejects_malformed_foreign_key_target(
    references, expected_code
):
    schema, enums, rules = _documents()
    schema["tables"]["snapshot_record"]["foreign_keys"][0][
        "references"
    ] = references

    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)

    assert captured.value.code == expected_code
    assert captured.value.constraint == "fk_snapshot_record_registry"


@pytest.mark.parametrize(
    ("conditional_required", "expected_code"),
    [
        ({}, "conditional_collection_invalid"),
        (["not-a-mapping"], "conditional_definition_invalid"),
        (
            [
                {
                    "name": "",
                    "table": "snapshot_record",
                    "when": {"field": "registration_status", "equals": "registered"},
                    "require": ["record_uid"],
                }
            ],
            "conditional_name_invalid",
        ),
        (
            [
                {
                    "name": "invalid_table",
                    "table": "",
                    "when": {"field": "registration_status", "equals": "registered"},
                    "require": ["record_uid"],
                }
            ],
            "conditional_table_invalid",
        ),
        (
            [
                {
                    "name": "invalid_when",
                    "table": "snapshot_record",
                    "when": [],
                    "require": ["record_uid"],
                }
            ],
            "conditional_when_invalid",
        ),
    ],
)
def test_contract_bundle_rejects_malformed_conditional_rule_shapes(
    conditional_required, expected_code
):
    schema, enums, rules = _documents()
    schema["conditional_required"] = conditional_required

    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)

    assert captured.value.code == expected_code


@pytest.mark.parametrize(
    ("rule_mutation", "expected_code"),
    [
        ({"catalog_version": "", "rules": {"V02-X": {}}}, "rule_catalog_version_invalid"),
        ({"catalog_version": "v1", "rules": {}}, "rule_catalog_invalid"),
        ({"catalog_version": "v1", "rules": {None: {}}}, "rule_id_invalid"),
        ({"catalog_version": "v1", "rules": {"V02-X": []}}, "rule_definition_invalid"),
    ],
)
def test_contract_bundle_rejects_malformed_rule_catalog(
    rule_mutation, expected_code
):
    schema, enums, rules = _documents()
    rules.update(rule_mutation)

    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)

    assert captured.value.code == expected_code


@pytest.mark.parametrize(
    ("invalid_position", "expected_code"),
    [
        ("schema", "schema_definition_invalid"),
        ("enums", "enum_catalog_invalid"),
        ("rules", "rule_catalog_invalid"),
    ],
)
def test_contract_bundle_rejects_non_mapping_document_roots(
    invalid_position, expected_code
):
    schema, enums, rules = _documents()
    documents = {"schema": schema, "enums": enums, "rules": rules}
    documents[invalid_position] = []

    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(
            documents["schema"], documents["enums"], documents["rules"]
        )

    assert captured.value.code == expected_code


def test_contract_bundle_wraps_base_schema_validation_error():
    schema, enums, rules = _documents()
    schema["tables"]["source_scope"]["fields"]["scope_kind"]["type"] = "vector"

    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)

    assert captured.value.code == "schema_definition_invalid"
    assert "unsupported type 'vector'" in captured.value.message
    assert isinstance(captured.value.__cause__, contract_module.SchemaValidationError)


@pytest.mark.parametrize(
    ("document_name", "duplicate_yaml"),
    [
        (
            "schema",
            """schema_version: v0.2
schema_version: v0.2
""",
        ),
        (
            "enums",
            """schema_version: v0.2
enums:
  registration_status:
    - discovered
  registration_status:
    - registered
""",
        ),
        (
            "rules",
            """schema_version: v0.2
catalog_version: duplicate-test
rules:
  V02-CONTRACT-001:
    version: 1
    version: 2
""",
        ),
    ],
)
def test_contract_loader_rejects_duplicate_yaml_keys_at_any_depth(
    tmp_path, document_name, duplicate_yaml
):
    paths = {
        "schema": FIXTURES / "v0.2最小合同.yaml",
        "enums": FIXTURES / "v0.2最小枚举.yaml",
        "rules": FIXTURES / "v0.2最小质量规则.yaml",
    }
    duplicate_path = tmp_path / f"duplicate-{document_name}.yaml"
    duplicate_path.write_text(duplicate_yaml, encoding="utf-8")
    paths[document_name] = duplicate_path

    with pytest.raises(ContractValidationError, match="duplicate YAML key") as captured:
        load_contract_bundle(paths["schema"], paths["enums"], paths["rules"])

    assert captured.value.code == "document_duplicate_key"


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", " uuid5-v1", "uuid5-v1 ", "uuid5-v2", "tpu-record-uuid5-v1", 1],
)
def test_contract_bundle_requires_trimmed_id_algorithm_version(value):
    schema, enums, rules = _documents()
    schema["id_algorithm_version"] = value

    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)

    assert captured.value.code == "id_algorithm_version_invalid"


@pytest.mark.parametrize("description", [None, "", "   ", " description", "description "])
def test_contract_bundle_requires_trimmed_table_description(description):
    schema, enums, rules = _documents()
    schema["tables"]["source_scope"]["description"] = description

    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)

    assert captured.value.code == "table_description_invalid"
    assert captured.value.table == "source_scope"


@pytest.mark.parametrize("description", [None, "", "   ", " description", "description "])
def test_contract_bundle_requires_trimmed_field_description(description):
    schema, enums, rules = _documents()
    schema["tables"]["source_scope"]["fields"]["scope_kind"][
        "description"
    ] = description

    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)

    assert captured.value.code == "field_description_invalid"
    assert captured.value.table == "source_scope"
    assert captured.value.constraint == "scope_kind"


def test_contract_bundle_requires_explicit_required_flag_for_every_field():
    schema, enums, rules = _documents()
    schema["tables"]["source_scope"]["fields"]["scope_kind"].pop("required")

    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)

    assert captured.value.code == "field_required_invalid"
    assert captured.value.constraint == "scope_kind"


@pytest.mark.parametrize(
    ("target", "replacement", "expected_code"),
    [
        ("table", "SourceScope", "table_name_invalid"),
        ("field", "scopeKind", "field_name_invalid"),
        ("constraint", "uq_SourceScope", "constraint_name_invalid"),
        ("conditional", "cr Registered", "conditional_name_invalid"),
    ],
)
def test_contract_bundle_requires_lower_snake_case_names(
    target, replacement, expected_code
):
    schema, enums, rules = _documents()
    if target == "table":
        schema["tables"][replacement] = schema["tables"].pop("source_scope")
    elif target == "field":
        fields = schema["tables"]["source_scope"]["fields"]
        fields[replacement] = fields.pop("scope_kind")
    elif target == "constraint":
        schema["tables"]["source_scope"]["unique_constraints"][0][
            "name"
        ] = replacement
    else:
        schema["conditional_required"][0]["name"] = replacement

    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)

    assert captured.value.code == expected_code


def test_contract_bundle_requires_primary_key_fields_to_be_required():
    schema, enums, rules = _documents()
    schema["tables"]["source_scope"]["fields"]["source_scope_id"][
        "required"
    ] = False

    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)

    assert captured.value.code == "primary_key_required_invalid"
    assert captured.value.constraint == "source_scope_id"


@pytest.mark.parametrize("cardinality", [None, "", "one-to-one", "many_to_many"])
def test_contract_bundle_requires_explicit_supported_fk_cardinality(cardinality):
    schema, enums, rules = _documents()
    foreign_key = schema["tables"]["snapshot_record"]["foreign_keys"][0]
    if cardinality is None:
        foreign_key.pop("cardinality")
    else:
        foreign_key["cardinality"] = cardinality

    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)

    assert captured.value.code == "foreign_key_cardinality_invalid"


def test_contract_bundle_rejects_one_to_one_fk_without_local_key():
    schema, enums, rules = _documents()
    schema["tables"]["snapshot_record"]["foreign_keys"][0][
        "cardinality"
    ] = "one_to_one"

    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)

    assert captured.value.code == "foreign_key_one_to_one_not_unique"


def test_contract_bundle_accepts_one_to_one_fk_backed_by_unique_constraint():
    schema, enums, rules = _documents()
    table = schema["tables"]["snapshot_record"]
    table["unique_constraints"] = [
        {"name": "uq_snapshot_record_record_uid", "fields": ["record_uid"]}
    ]
    table["foreign_keys"][0]["cardinality"] = "one_to_one"

    validate_contract_bundle(schema, enums, rules)


def test_contract_bundle_rejects_set_null_on_required_fk_fields():
    schema, enums, rules = _documents()
    schema["tables"]["snapshot_record"]["foreign_keys"][0][
        "on_delete"
    ] = "set_null"

    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)

    assert captured.value.code == "foreign_key_set_null_required"


def test_contract_bundle_rejects_conditional_equals_outside_field_enum():
    schema, enums, rules = _documents()
    schema["conditional_required"][0]["when"]["equals"] = "not_registered"

    with pytest.raises(ContractValidationError) as captured:
        validate_contract_bundle(schema, enums, rules)

    assert captured.value.code == "conditional_enum_value_invalid"


def test_contract_bundle_allows_conditional_equals_for_non_enum_field():
    schema, enums, rules = _documents()
    schema["conditional_required"][0]["when"] = {
        "field": "record_uid",
        "equals": "any-stable-uid",
    }

    validate_contract_bundle(schema, enums, rules)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda rule: rule.pop("version"), "version must be a positive integer"),
        (
            lambda rule: rule.update(implementation_ref="validate_contract_bundle"),
            "implementation_ref must be module.symbol",
        ),
        (lambda rule: rule.update(default_severity="fatal"), "unknown severity"),
        (lambda rule: rule.update(test_ids=[]), "test_ids"),
    ],
)
def test_contract_bundle_reuses_full_quality_rule_validation(mutation, message):
    schema, enums, rules = _documents()
    mutation(rules["rules"]["V02-CONTRACT-001"])

    with pytest.raises(ContractValidationError, match=message) as captured:
        validate_contract_bundle(schema, enums, rules)

    assert captured.value.code == "rule_catalog_invalid"
    assert isinstance(
        captured.value.__cause__, contract_module.RuleCatalogValidationError
    )
