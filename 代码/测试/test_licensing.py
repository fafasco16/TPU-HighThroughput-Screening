import pytest

from licensing import (
    PUBLISHABLE_LICENSES,
    may_publish,
    normalize_spdx,
    parse_optional_bool,
)


@pytest.mark.parametrize("license_spdx", ["", "UNKNOWN", "NOASSERTION", None])
def test_unknown_licenses_are_blocked(license_spdx):
    assert not may_publish(
        license_spdx,
        derivatives_allowed=True,
        redistribution_allowed=True,
    )


@pytest.mark.parametrize(
    "license_spdx",
    ["CC-BY-NC-ND-4.0", "CC-BY-ND-4.0", "GPL-3.0-only", "made-up-license"],
)
def test_non_whitelisted_or_nd_licenses_are_blocked(license_spdx):
    assert not may_publish(
        license_spdx,
        derivatives_allowed=True,
        redistribution_allowed=True,
    )


def test_cc_by_is_publishable_and_case_is_normalized():
    assert "CC-BY-4.0" in PUBLISHABLE_LICENSES
    assert normalize_spdx("  cc-by-4.0 ") == "CC-BY-4.0"
    assert may_publish("cc-by-4.0", derivatives_allowed=True, redistribution_allowed=True)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        (" TRUE ", True),
        ("yes", True),
        ("是", True),
        ("false", False),
        ("no", False),
        ("否", False),
        (None, None),
        ("", None),
        ("unknown", None),
        ("N/A", None),
    ],
)
def test_parse_optional_bool_is_explicit_and_safe(raw, expected):
    assert parse_optional_bool(raw) is expected


@pytest.mark.parametrize("raw", [2, -1, 0.0, 1.0, [], {}, "truthy", object()])
def test_parse_optional_bool_rejects_ambiguous_values(raw):
    with pytest.raises(ValueError, match="boolean"):
        parse_optional_bool(raw, field_name="flag")


def test_may_publish_fails_closed_for_ambiguous_booleans():
    assert not may_publish("MIT", "truthy", True)
    assert not may_publish("MIT", True, "truthy")
    assert not may_publish("MIT", None, True)
    assert not may_publish("MIT", True, False)


def test_normalize_spdx_rejects_non_string_values():
    assert normalize_spdx(None) == ""
    with pytest.raises(TypeError, match="license_spdx"):
        normalize_spdx(42)
