from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "汇总RESP片段验证.py"
SPEC = importlib.util.spec_from_file_location("resp_fragment_summary", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize(
    ("name", "family"),
    list(MODULE.FRAGMENT_FAMILIES.items()),
)
def test_fragment_family_mapping(name: str, family: str) -> None:
    assert MODULE.classify_fragment(name) == family


def test_unknown_fragment_fails_closed() -> None:
    with pytest.raises(ValueError, match="未知RESP验证片段"):
        MODULE.classify_fragment("unknown")
