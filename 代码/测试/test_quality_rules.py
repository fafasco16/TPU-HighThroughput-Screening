from copy import deepcopy
from pathlib import Path

import pytest

from contract import _repository_test_index
from quality_rules import (
    RuleCatalogValidationError,
    load_rule_catalog,
    rule_catalog_hash,
    validate_rule_catalog,
)


ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "配置/结构定义/v0.2质量规则.yaml"
COLLECTED_TEST_IDS = _repository_test_index()


def test_rule_catalog_is_unique_and_every_blocking_rule_has_tests():
    catalog = load_rule_catalog(RULES)
    validate_rule_catalog(catalog, collected_test_ids=COLLECTED_TEST_IDS)
    assert len(catalog["rules"]) == 24
    assert all(
        rule["test_ids"]
        for rule in catalog["rules"].values()
        if rule["default_severity"] == "blocking"
    )
    assert all(rule_id != "UNSPECIFIED" for rule_id in catalog["rules"])


def test_rule_catalog_hash_is_mapping_order_independent():
    catalog = load_rule_catalog(RULES)
    reordered = {
        "rules": dict(reversed(list(catalog["rules"].items()))),
        "catalog_version": catalog["catalog_version"],
        "schema_version": catalog["schema_version"],
    }
    assert rule_catalog_hash(catalog) == rule_catalog_hash(reordered)
    assert len(rule_catalog_hash(catalog)) == 64


def test_duplicate_yaml_rule_ids_are_rejected(tmp_path: Path):
    path = tmp_path / "duplicate.yaml"
    path.write_text(
        """schema_version: v0.2
catalog_version: duplicate-test
rules:
  V02-X-001: {version: 1}
  V02-X-001: {version: 2}
""",
        encoding="utf-8",
    )
    with pytest.raises(RuleCatalogValidationError, match="duplicate YAML key"):
        load_rule_catalog(path)


def test_rule_catalog_yaml_root_must_be_a_mapping(tmp_path: Path):
    path = tmp_path / "list-root.yaml"
    path.write_text("- not-a-mapping\n", encoding="utf-8")

    with pytest.raises(
        RuleCatalogValidationError,
        match="rule catalog YAML root must be a mapping",
    ):
        load_rule_catalog(path)


@pytest.mark.parametrize("schema_version", ["", "   ", " v0.2", "v0.2 "])
def test_rule_catalog_rejects_empty_or_untrimmed_strings(schema_version):
    catalog = deepcopy(load_rule_catalog(RULES))
    catalog["schema_version"] = schema_version

    with pytest.raises(
        RuleCatalogValidationError,
        match="schema_version must be a non-empty trimmed string",
    ):
        validate_rule_catalog(catalog)


def test_rule_catalog_validator_requires_a_mapping():
    with pytest.raises(
        RuleCatalogValidationError,
        match="rule catalog must be a mapping",
    ):
        validate_rule_catalog([])


@pytest.mark.parametrize("rules", [None, {}, []])
def test_rule_catalog_requires_nonempty_rules_mapping(rules):
    catalog = deepcopy(load_rule_catalog(RULES))
    catalog["rules"] = rules

    with pytest.raises(
        RuleCatalogValidationError,
        match="rules must be a non-empty mapping",
    ):
        validate_rule_catalog(catalog)


def test_each_rule_definition_must_be_a_mapping():
    catalog = deepcopy(load_rule_catalog(RULES))
    catalog["rules"]["V02-ID-001"] = "not-a-mapping"

    with pytest.raises(
        RuleCatalogValidationError,
        match="rule 'V02-ID-001' must be a mapping",
    ):
        validate_rule_catalog(catalog)


@pytest.mark.parametrize("version", [None, 0, -1, 1.0, "1", True, False])
def test_rule_version_must_be_a_positive_non_boolean_integer(version):
    catalog = deepcopy(load_rule_catalog(RULES))
    catalog["rules"]["V02-ID-001"]["version"] = version

    with pytest.raises(
        RuleCatalogValidationError,
        match="rule 'V02-ID-001' version must be a positive integer",
    ):
        validate_rule_catalog(catalog)


@pytest.mark.parametrize(
    "profiles",
    [None, "contract_freeze", {}, [""], ["   "], [1]],
)
def test_nonblocking_rule_profiles_must_be_a_list_of_nonempty_strings(profiles):
    catalog = deepcopy(load_rule_catalog(RULES))
    rule = catalog["rules"]["V02-ID-001"]
    rule["default_severity"] = "warning"
    rule["blocking_profiles"] = profiles

    with pytest.raises(
        RuleCatalogValidationError,
        match="rule 'V02-ID-001' blocking_profiles must be a list of strings",
    ):
        validate_rule_catalog(catalog)


def test_nonblocking_rule_accepts_a_valid_profile_list():
    catalog = deepcopy(load_rule_catalog(RULES))
    rule = catalog["rules"]["V02-ID-001"]
    rule["default_severity"] = "warning"
    rule["blocking_profiles"] = ["advisory_review"]

    validate_rule_catalog(catalog)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda catalog: catalog["rules"].update({"UNSPECIFIED": catalog["rules"].pop("V02-ID-001")}), "UNSPECIFIED"),
        (lambda catalog: catalog["rules"]["V02-ID-001"].update(default_severity="fatal"), "unknown severity"),
        (lambda catalog: catalog["rules"]["V02-ID-001"].update(blocking_profiles=[]), "blocking_profiles"),
        (lambda catalog: catalog["rules"]["V02-ID-001"].update(implementation_ref="stable_record_uid"), "module.symbol"),
        (lambda catalog: catalog["rules"]["V02-ID-001"].update(test_ids=[]), "test_ids"),
    ],
)
def test_rule_catalog_rejects_malformed_rules(mutation, message):
    catalog = deepcopy(load_rule_catalog(RULES))
    mutation(catalog)
    with pytest.raises(RuleCatalogValidationError, match=message):
        validate_rule_catalog(catalog, collected_test_ids=COLLECTED_TEST_IDS)


def test_rule_catalog_rejects_missing_test_mapping():
    catalog = load_rule_catalog(RULES)
    with pytest.raises(RuleCatalogValidationError, match="missing test id"):
        validate_rule_catalog(catalog, collected_test_ids={"different_test"})
