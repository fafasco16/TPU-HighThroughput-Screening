import pandas as pd

from qc import (
    check_finite_values,
    check_foreign_key,
    check_lineage_split,
    check_primary_key,
    check_provenance,
    check_public_release,
    has_errors,
    issues_frame,
)


def test_duplicate_primary_keys_are_errors():
    frame = pd.DataFrame({"id": ["a", "a"]})
    issues = check_primary_key(frame, "chemical", ["id"])
    assert len(issues) == 2
    assert has_errors(issues)


def test_null_and_missing_primary_keys_are_reported():
    null_issues = check_primary_key(pd.DataFrame({"id": [None]}), "chemical", ["id"])
    missing_issues = check_primary_key(pd.DataFrame({"other": [1]}), "chemical", ["id"])
    assert null_issues[0].rule_id == "integrity.null_primary_key"
    assert missing_issues[0].rule_id == "schema.required_column"


def test_orphan_foreign_key_is_reported():
    child = pd.DataFrame({"source_id": ["known", "missing"]})
    parent = pd.DataFrame({"source_id": ["known"]})
    issues = check_foreign_key(child, parent, "chemical", "source_id", "source_id")
    assert [issue.record_id for issue in issues] == ["missing"]


def test_missing_foreign_key_column_is_reported():
    issues = check_foreign_key(
        pd.DataFrame({"other": [1]}),
        pd.DataFrame({"source_id": ["known"]}),
        "chemical",
        "source_id",
        "source_id",
    )
    assert issues[0].rule_id == "schema.required_column"


def test_missing_provenance_is_reported():
    frame = pd.DataFrame(
        {
            "source_id": ["s1"],
            "source_file_id": [""],
            "source_locator": ["row:2"],
        }
    )
    assert len(check_provenance(frame, "chemical")) == 1
    assert check_provenance(pd.DataFrame({"source_id": ["s1"]}), "chemical")


def test_non_finite_and_missing_numeric_columns_are_reported():
    frame = pd.DataFrame({"value": [1.0, float("inf"), "bad", None]})
    issues = check_finite_values(frame, "property", ["value", "missing"])
    assert [issue.rule_id for issue in issues].count("value.non_finite") == 2
    assert any(issue.rule_id == "schema.required_column" for issue in issues)


def test_same_lineage_may_not_cross_split():
    frame = pd.DataFrame(
        {
            "lineage_record_id": ["record-1", "record-1", "record-2"],
            "split_group": ["train", "test", "test"],
        }
    )
    issues = check_lineage_split(frame, "property_value")
    assert [issue.record_id for issue in issues] == ["record-1"]
    assert check_lineage_split(pd.DataFrame({"other": [1]}), "property_value")


def test_public_release_blocks_restricted_records():
    frame = pd.DataFrame(
        {
            "may_publish": [True, False, True],
            "material_scope": ["linear_tpu", "linear_tpu", "reference_only"],
        }
    )
    issues = check_public_release(frame, "public")
    assert len(issues) == 2
    assert len(issues_frame(issues)) == 2


def test_public_release_requires_gate_columns_and_empty_issue_frame_is_stable():
    issues = check_public_release(pd.DataFrame({"may_publish": [True]}), "public")
    assert issues[0].rule_id == "schema.required_column"
    assert list(issues_frame([]).columns) == [
        "rule_id", "severity", "table_name", "record_id", "message"
    ]
    assert not has_errors([])
