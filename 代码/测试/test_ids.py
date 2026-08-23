import pytest

from ids import stable_id


def test_stable_id_is_deterministic_and_namespaced():
    result = stable_id("chemical", "CCO")
    assert result == stable_id("chemical", "CCO")
    assert result.startswith("chemical_")
    assert len(result) == len("chemical_") + 16


def test_stable_id_canonicalizes_mapping_key_order_and_supports_unicode():
    left = stable_id("source", {"路径": "数据/样品.csv", "year": 2026})
    right = stable_id("source", {"year": 2026, "路径": "数据/样品.csv"})
    assert left == right


def test_stable_id_keeps_part_boundaries_and_types_distinct():
    assert stable_id("x", "a", "b") != stable_id("x", "ab")
    assert stable_id("x", 1) != stable_id("x", "1")


@pytest.mark.parametrize("namespace", ["", " ", "source/file", r"source\file", "source file"])
def test_stable_id_rejects_unsafe_namespaces(namespace):
    with pytest.raises(ValueError, match="namespace"):
        stable_id(namespace, "value")


def test_stable_id_requires_a_string_namespace():
    with pytest.raises(TypeError, match="namespace"):
        stable_id(123, "value")
