"""Fail-closed license and redistribution gates."""

from __future__ import annotations

from typing import Final


# This is deliberately an allow-list, not a list of licenses known to SPDX.
# Licenses with non-commercial/no-derivatives clauses and software copyleft
# licenses require separate compliance handling and therefore remain blocked.
PUBLISHABLE_LICENSES: Final[frozenset[str]] = frozenset(
    {
        "CC0-1.0",
        "CC-BY-1.0",
        "CC-BY-2.0",
        "CC-BY-2.5",
        "CC-BY-3.0",
        "CC-BY-4.0",
        "CC-BY-SA-1.0",
        "CC-BY-SA-2.0",
        "CC-BY-SA-2.5",
        "CC-BY-SA-3.0",
        "CC-BY-SA-4.0",
        "PDDL-1.0",
        "ODC-By-1.0",
        "ODbL-1.0",
        "MIT",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "Apache-2.0",
    }
)

_SPDX_BY_CASEFOLD: Final[dict[str, str]] = {
    license_id.casefold(): license_id for license_id in PUBLISHABLE_LICENSES
}
_TRUE_STRINGS: Final[frozenset[str]] = frozenset({"true", "yes", "y", "1", "是"})
_FALSE_STRINGS: Final[frozenset[str]] = frozenset({"false", "no", "n", "0", "否"})
_NULL_STRINGS: Final[frozenset[str]] = frozenset(
    {"", "unknown", "none", "null", "n/a", "na", "noassertion", "未知"}
)


def normalize_spdx(license_spdx: str | None) -> str:
    """Trim an SPDX identifier and canonicalize allow-listed IDs by case."""

    if license_spdx is None:
        return ""
    if not isinstance(license_spdx, str):
        raise TypeError("license_spdx must be a string or None")
    value = license_spdx.strip()
    return _SPDX_BY_CASEFOLD.get(value.casefold(), value)


def parse_optional_bool(value: object, *, field_name: str = "value") -> bool | None:
    """Parse explicit booleans without treating arbitrary strings as truthy.

    Ambiguous inputs raise ``ValueError`` so configuration ingestion can stop
    rather than silently granting a redistribution permission.
    """

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        if value in (0, 1):
            return bool(value)
        raise ValueError(f"{field_name} must be an explicit boolean")
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in _TRUE_STRINGS:
            return True
        if normalized in _FALSE_STRINGS:
            return False
        if normalized in _NULL_STRINGS:
            return None
    raise ValueError(f"{field_name} must be an explicit boolean")


def may_publish(
    license_spdx: str | None,
    derivatives_allowed: object,
    redistribution_allowed: object,
) -> bool:
    """Return whether a record passes the public-release license gate.

    Invalid or missing permissions fail closed.  This predicate does not raise
    for malformed permission flags because callers commonly use it as a filter;
    direct configuration validation should use :func:`parse_optional_bool`.
    """

    try:
        derivatives = parse_optional_bool(
            derivatives_allowed,
            field_name="derivatives_allowed",
        )
        redistribution = parse_optional_bool(
            redistribution_allowed,
            field_name="redistribution_allowed",
        )
        license_id = normalize_spdx(license_spdx)
    except (TypeError, ValueError):
        return False
    return bool(
        license_id in PUBLISHABLE_LICENSES
        and derivatives is True
        and redistribution is True
    )
