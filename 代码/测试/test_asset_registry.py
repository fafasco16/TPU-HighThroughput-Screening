from __future__ import annotations

import os
import stat
import unicodedata
from pathlib import Path

import pytest
import yaml

import asset_registry as registry_module
from asset_registry import (
    ARTIFACT_ROLES,
    DUPLICATE_COLUMNS,
    LIFECYCLE_FIELDS,
    REGISTRY_COLUMNS,
    AssetRegistryError,
    build_asset_registry,
    canonical_relative_path,
    classify_path,
    discover_asset_paths,
    hash_file_sha256,
    load_asset_rules,
    write_duplicate_groups_csv,
    write_registry_csv,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_RULES = PROJECT_ROOT / "配置" / "v0.2资产登记规则.yaml"
PRODUCTION_ENUMS = PROJECT_ROOT / "配置/结构定义" / "v0.2枚举.yaml"
RAW_ROOT = PROJECT_ROOT / "数据/原始"


def _rule(
    rule_id: str = "all_files",
    pattern: str = r"^.*$",
    *,
    priority: int = 10,
    scope: str = "scope_default",
    role: str = "primary_data",
    stage: str = "raw",
    hint: str = "unknown",
    review_required: bool = True,
) -> dict[str, object]:
    return {
        "rule_id": rule_id,
        "priority": priority,
        "path_regex": pattern,
        "source_scope_key": scope,
        "artifact_role": role,
        "data_stage": stage,
        "material_scope_hint": hint,
        "registration_status": "discovered",
        "availability_status": "available",
        "parse_status": "not_attempted",
        "scientific_admission_status": "pending",
        "model_readiness_status": "not_assessed",
        "release_status": "not_assessed",
        "decision_basis": "test fixture rule",
        "review_required": review_required,
    }


def _document(*rules: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "v0.2",
        "id_algorithm_version": "uuid5-v1",
        "rules_version": "asset-rules-test-v1",
        "discovery_scope_key": "test-discovery-scope",
        "scan": {
            "root_hint": "数据/原始",
            "follow_symlinks": False,
            "prune_directory_names": [".git"],
            "path_normalization": "unicode_nfc_posix",
            "stable_sort": "casefold_then_nfc",
            "hash_algorithm": "sha256_stream",
            "chunk_size_bytes": 7,
            "symlink_policy": "block",
            "read_failure_policy": "block",
            "unmatched_policy": "block",
            "conflict_policy": "block",
        },
        "duplicate_policy": {
            "group_key": "content_sha256",
            "canonical_order": "priority_desc_then_casefold_path",
        },
        "rules": list(rules or (_rule(),)),
    }


def _write_rules(tmp_path: Path, document: dict[str, object]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "rules.yaml"
    path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def _load(tmp_path: Path, *rules: dict[str, object]):
    return load_asset_rules(_write_rules(tmp_path, _document(*rules)))


def test_duplicate_yaml_key_is_rejected_at_any_nesting_depth(tmp_path):
    path = tmp_path / "duplicate.yaml"
    path.write_text(
        """
schema_version: v0.2
rules_version: v1
discovery_scope_key: scope
scan:
  follow_symlinks: false
  follow_symlinks: true
rules: []
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(AssetRegistryError) as error:
        load_asset_rules(path)

    assert error.value.code == "duplicate_yaml_key"
    assert error.value.as_dict()["status"] == "blocked"
    assert error.value.context["key"] == "follow_symlinks"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda doc: doc.pop("discovery_scope_key"), "invalid_configuration"),
        (lambda doc: doc.update(schema_version="v0.1"), "invalid_configuration"),
        (lambda doc: doc.update(id_algorithm_version="uuid5-v2"), "invalid_configuration"),
        (lambda doc: doc.update(rules_version=" "), "invalid_configuration"),
        (lambda doc: doc.update(rules=[]), "invalid_configuration"),
        (lambda doc: doc.update(scan="not-a-mapping"), "invalid_configuration"),
        (lambda doc: doc["scan"].update(follow_symlinks="false"), "invalid_configuration"),
        (lambda doc: doc["scan"].update(prune_directory_names=[]), "invalid_configuration"),
        (lambda doc: doc["scan"].update(prune_directory_names=["cache"]), "invalid_configuration"),
        (lambda doc: doc["scan"].update(path_normalization="platform_default"), "invalid_configuration"),
        (lambda doc: doc["scan"].update(hash_algorithm="md5"), "invalid_configuration"),
        (lambda doc: doc["scan"].update(symlink_policy="ignore"), "invalid_configuration"),
        (lambda doc: doc["scan"].update(chunk_size_bytes=0), "invalid_configuration"),
        (lambda doc: doc["scan"].pop("stable_sort"), "invalid_configuration"),
        (lambda doc: doc.update(duplicate_policy={"group_key": "name", "canonical_order": "first"}), "invalid_configuration"),
        (lambda doc: doc.update(duplicate_policy=[]), "invalid_configuration"),
        (lambda doc: doc.update(rules=["not-a-rule"]), "invalid_rule"),
        (lambda doc: doc["rules"][0].update(priority=True), "invalid_rule"),
        (lambda doc: doc["rules"][0].update(review_required="false"), "invalid_rule"),
        (lambda doc: doc["rules"][0].update(artifact_role="not_a_role"), "invalid_rule"),
        (lambda doc: doc["rules"][0].update(data_stage="training_ready"), "invalid_rule"),
        (lambda doc: doc["rules"][0].update(registration_status="ready"), "invalid_rule"),
        (lambda doc: doc["rules"][0].update(rule_id="Uppercase"), "invalid_rule"),
        (lambda doc: doc["rules"][0].update(source_scope_key=1), "invalid_rule"),
        (lambda doc: doc["rules"][0].pop("decision_basis"), "invalid_rule"),
        (lambda doc: doc["rules"][0].update(origin_kind="experimental"), "forbidden_observation_field"),
        (lambda doc: doc["rules"][0].update(path_regex="["), "invalid_regex"),
    ],
)
def test_rule_document_rejects_invalid_or_observation_level_values(tmp_path, mutation, code):
    document = _document()
    mutation(document)
    path = _write_rules(tmp_path, document)

    with pytest.raises(AssetRegistryError) as error:
        load_asset_rules(path)

    assert error.value.code == code


def test_rule_document_rejects_duplicate_rule_id_and_unknown_rule_key(tmp_path):
    duplicate = _document(_rule("same", r"^a$"), _rule("same", r"^b$"))
    with pytest.raises(AssetRegistryError, match="rule_id") as error:
        load_asset_rules(_write_rules(tmp_path, duplicate))
    assert error.value.code == "duplicate_rule_id"

    unknown = _document()
    unknown["rules"][0]["not_a_contract_field"] = "x"
    with pytest.raises(AssetRegistryError) as error:
        load_asset_rules(_write_rules(tmp_path, unknown))
    assert error.value.code == "invalid_rule"


def test_rule_loader_wraps_malformed_missing_nonmapping_and_unhashable_yaml(tmp_path):
    malformed = tmp_path / "malformed.yaml"
    malformed.write_text("rules: [", encoding="utf-8")
    with pytest.raises(AssetRegistryError) as error:
        load_asset_rules(malformed)
    assert error.value.code == "invalid_yaml"

    with pytest.raises(AssetRegistryError) as error:
        load_asset_rules(tmp_path / "missing.yaml")
    assert error.value.code == "rules_read_failure"

    nonmapping = tmp_path / "list.yaml"
    nonmapping.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(AssetRegistryError) as error:
        load_asset_rules(nonmapping)
    assert error.value.code == "invalid_configuration"

    unhashable = tmp_path / "unhashable.yaml"
    unhashable.write_text("? [a, b]\n: value\n", encoding="utf-8")
    with pytest.raises(AssetRegistryError) as error:
        load_asset_rules(unhashable)
    assert error.value.code == "invalid_yaml_key"


@pytest.mark.parametrize(
    "text",
    ["", ".", "../escape.csv", "a/../escape.csv", "/absolute.csv", "C:/absolute.csv", "//server/share.csv", "a//b.csv", "a\\b.csv\x00"],
)
def test_canonical_relative_path_rejects_empty_escape_absolute_and_malformed_paths(text):
    with pytest.raises(AssetRegistryError) as error:
        canonical_relative_path(text)
    assert error.value.code == "path_escape"


def test_canonical_relative_path_uses_nfc_and_posix_separators():
    decomposed = "目录\\e\u0301.csv"
    result = canonical_relative_path(decomposed)
    assert result == "目录/é.csv"
    assert unicodedata.is_normalized("NFC", result)


def test_canonical_relative_path_rejects_non_pathlike_and_bytes():
    with pytest.raises(AssetRegistryError) as error:
        canonical_relative_path(object())
    assert error.value.code == "path_escape"
    with pytest.raises(AssetRegistryError) as error:
        canonical_relative_path(b"bytes.csv")
    assert error.value.code == "path_escape"


def test_resolved_within_handles_cross_drive_paths():
    assert registry_module._resolved_within(Path("C:/root"), Path("D:/other")) is False


def test_discovery_prunes_git_at_arbitrary_depth_and_sorts_casefold_then_nfc(tmp_path):
    root = tmp_path / "root"
    (root / "nested" / ".git" / "objects").mkdir(parents=True)
    (root / "nested" / ".git" / "objects" / "ignored").write_text("x")
    (root / "nested" / "B.csv").write_text("b")
    (root / "nested" / "a.csv").write_text("a")
    (root / "Z.csv").write_text("z")
    config = _load(tmp_path / "config")

    paths = discover_asset_paths(root, config)

    assert [path.relative_path for path in paths] == ["nested/a.csv", "nested/B.csv", "Z.csv"]
    assert not any(".git" in path.relative_path for path in paths)


def test_root_symlink_and_discovered_symlink_are_structured_blockers(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "file.csv").write_text("x")
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")
    config = _load(tmp_path / "config")

    with pytest.raises(AssetRegistryError) as error:
        discover_asset_paths(link, config)
    assert error.value.code == "symlink_blocked"

    scan_root = tmp_path / "scan"
    scan_root.mkdir()
    (scan_root / "linked.csv").symlink_to(target / "file.csv")
    with pytest.raises(AssetRegistryError) as error:
        discover_asset_paths(scan_root, config)
    assert error.value.code == "symlink_blocked"
    assert error.value.context["relative_path"] == "linked.csv"


def test_windows_reparse_point_detection_covers_junction_attribute():
    class Entry:
        def is_symlink(self):
            return False

        def stat(self, *, follow_symlinks):
            assert follow_symlinks is False
            return type("Stat", (), {"st_file_attributes": stat.FILE_ATTRIBUTE_REPARSE_POINT})()

    assert registry_module._entry_is_link_or_reparse(Entry()) is True


def test_scan_root_and_metadata_failures_are_structured(tmp_path, monkeypatch):
    config = _load(tmp_path / "config")
    with pytest.raises(AssetRegistryError) as error:
        discover_asset_paths(tmp_path / "missing-root", config)
    assert error.value.code == "invalid_scan_root"

    class BrokenPath:
        def is_symlink(self):
            return False

        def stat(self, *, follow_symlinks):
            raise PermissionError("denied")

    with pytest.raises(AssetRegistryError) as error:
        registry_module._path_is_link_or_reparse(BrokenPath())
    assert error.value.code == "read_failure"

    root = tmp_path / "root"
    root.mkdir()
    original_resolve = Path.resolve

    def broken_resolve(self, *, strict=False):
        if self == root.absolute():
            raise PermissionError("resolve denied")
        return original_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", broken_resolve)
    with pytest.raises(AssetRegistryError) as error:
        discover_asset_paths(root, config)
    assert error.value.code == "read_failure"


def test_directory_and_entry_enumeration_failures_are_structured(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    config = _load(tmp_path / "config")

    monkeypatch.setattr(registry_module.os, "scandir", lambda _path: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(AssetRegistryError) as error:
        discover_asset_paths(root, config)
    assert error.value.code == "read_failure"


def test_special_entry_and_entry_metadata_failure_are_structured(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    physical = root / "entry"
    physical.write_text("x")
    config = _load(tmp_path / "config")

    class SpecialEntry:
        name = "entry"
        path = str(physical)

        def is_dir(self, *, follow_symlinks):
            return False

        def is_file(self, *, follow_symlinks):
            return False

        def is_symlink(self):
            return False

        def stat(self, *, follow_symlinks):
            return physical.stat()

    monkeypatch.setattr(registry_module.os, "scandir", lambda _path: [SpecialEntry()])
    with pytest.raises(AssetRegistryError) as error:
        discover_asset_paths(root, config)
    assert error.value.code == "unsupported_file_type"

    class BrokenEntry(SpecialEntry):
        def is_dir(self, *, follow_symlinks):
            raise PermissionError("metadata denied")

    monkeypatch.setattr(registry_module.os, "scandir", lambda _path: [BrokenEntry()])
    with pytest.raises(AssetRegistryError) as error:
        discover_asset_paths(root, config)
    assert error.value.code == "read_failure"


def test_hash_is_streamed_in_configured_chunks_and_read_errors_are_structured(tmp_path, monkeypatch):
    path = tmp_path / "large.bin"
    path.write_bytes(b"0123456789" * 20)
    real_open = registry_module._open_binary
    read_sizes: list[int] = []

    class TrackingReader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def read(self, size=-1):
            read_sizes.append(size)
            assert size == 13
            return self.stream.read(size)

        def fileno(self):
            return self.stream.fileno()

    monkeypatch.setattr(
        registry_module,
        "_open_binary",
        lambda value: TrackingReader(real_open(value)),
    )
    digest = hash_file_sha256(path, chunk_size=13)
    assert len(digest) == 64
    assert len(read_sizes) > 2

    with pytest.raises(AssetRegistryError) as error:
        hash_file_sha256(tmp_path)
    assert error.value.code == "read_failure"
    assert error.value.as_dict()["context"]["path"].endswith(tmp_path.name)


def test_hash_rejects_invalid_chunk_and_detects_open_race_and_midread_change(tmp_path, monkeypatch):
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    with pytest.raises(ValueError):
        hash_file_sha256(first, chunk_size=0)

    monkeypatch.setattr(registry_module, "_open_binary", lambda _path: second.open("rb"))
    with pytest.raises(AssetRegistryError) as error:
        hash_file_sha256(first)
    assert error.value.code == "file_changed_during_read"

    monkeypatch.undo()
    real_open = registry_module._open_binary

    class MutatingReader:
        def __init__(self, stream):
            self.stream = stream
            self.mutated = False

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def read(self, size=-1):
            data = self.stream.read(size)
            if not data and not self.mutated:
                self.mutated = True
                first.write_bytes(b"changed-size")
            return data

    monkeypatch.setattr(
        registry_module, "_open_binary", lambda path: MutatingReader(real_open(path))
    )
    with pytest.raises(AssetRegistryError) as error:
        hash_file_sha256(first, chunk_size=2)
    assert error.value.code == "file_changed_during_read"


def test_hash_wraps_open_permission_error(tmp_path, monkeypatch):
    path = tmp_path / "file.bin"
    path.write_bytes(b"x")
    monkeypatch.setattr(
        registry_module,
        "_open_binary",
        lambda _path: (_ for _ in ()).throw(PermissionError("denied")),
    )
    with pytest.raises(AssetRegistryError) as error:
        hash_file_sha256(path)
    assert error.value.code == "read_failure"


def test_unmatched_and_same_highest_priority_conflict_fail_closed(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "x.csv").write_text("x")

    config = _load(tmp_path / "one", _rule(pattern=r"^only\.txt$"))
    with pytest.raises(AssetRegistryError) as error:
        build_asset_registry(root, config)
    assert error.value.code == "unmatched_path"

    config = _load(
        tmp_path / "two",
        _rule("first", r"^x\.csv$", priority=100),
        _rule("second", r"\.csv$", priority=100),
    )
    with pytest.raises(AssetRegistryError) as error:
        build_asset_registry(root, config)
    assert error.value.code == "ambiguous_rule"
    assert error.value.context["rule_ids"] == ["first", "second"]


def test_only_highest_priority_match_is_selected(tmp_path):
    config = _load(
        tmp_path,
        _rule("fallback", r".*", priority=1, role="documentation"),
        _rule("specific", r"^x\.csv$", priority=5, role="primary_data"),
    )
    selected = classify_path("x.csv", config)
    assert selected.rule_id == "specific"
    assert selected.priority == 5


def test_uid_inputs_exclude_absolute_root_and_output_is_deterministic(tmp_path):
    rules_path = _write_rules(tmp_path, _document())
    config = load_asset_rules(rules_path)
    roots = [tmp_path / "absolute-A", tmp_path / "absolute-B"]
    for root in roots:
        root.mkdir()
        (root / "same.csv").write_bytes(b"same-content")

    left = build_asset_registry(roots[0], config).records[0]
    right = build_asset_registry(roots[1], config).records[0]

    assert left.asset_occurrence_uid == right.asset_occurrence_uid
    assert left.source_file_uid == right.source_file_uid
    assert left.content_blob_uid == right.content_blob_uid
    assert left.record_revision_id == right.record_revision_id
    assert str(roots[0]) not in repr(left)


def test_blob_identity_is_shared_but_source_file_identity_is_scope_specific(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.csv").write_bytes(b"identical")
    (root / "b.csv").write_bytes(b"identical")
    config = _load(
        tmp_path / "config",
        _rule("a", r"^a\.csv$", priority=20, scope="scope-a"),
        _rule("b", r"^b\.csv$", priority=10, scope="scope-b"),
    )

    result = build_asset_registry(root, config)
    a, b = result.records
    assert a.content_blob_uid == b.content_blob_uid
    assert a.source_file_uid != b.source_file_uid
    assert a.asset_occurrence_uid != b.asset_occurrence_uid

    group = result.duplicate_groups[0]
    assert group.member_count == 2
    assert group.canonical_relative_path == "a.csv"
    assert group.members == ("a.csv", "b.csv")


def test_record_revision_changes_with_content_or_classification(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    file = root / "x.csv"
    file.write_text("v1")
    raw = _load(tmp_path / "raw")
    first = build_asset_registry(root, raw).records[0]

    file.write_text("v2")
    second = build_asset_registry(root, raw).records[0]
    assert second.source_file_uid == first.source_file_uid
    assert second.record_revision_id != first.record_revision_id

    changed = _load(
        tmp_path / "changed",
        _rule(role="documentation", stage="metadata_only"),
    )
    third = build_asset_registry(root, changed).records[0]
    assert third.source_file_uid == second.source_file_uid
    assert third.record_revision_id != second.record_revision_id


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("table.csv", "application/vnd.ms-excel"),
        (r"目录\TABLE.CsV", "application/vnd.ms-excel"),
        ("paper.PDF", "application/pdf"),
        ("archive.TAR.GZ", "application/gzip"),
        ("records.CSV.GZ", "application/gzip"),
        ("model.unknown", "application/octet-stream"),
        ("model.ckpt.index", "application/octet-stream"),
        ("README", "application/octet-stream"),
        (".gitignore", "application/octet-stream"),
        (".csv", "application/octet-stream"),
    ],
)
def test_media_type_is_frozen_case_insensitive_and_has_stable_fallback(path, expected):
    assert registry_module._media_type(path) == expected


def test_compound_media_type_uses_longest_frozen_suffix(monkeypatch):
    simple = dict(registry_module._FROZEN_MEDIA_TYPES)
    simple[".gz"] = "application/example-final-suffix"
    monkeypatch.setattr(registry_module, "_FROZEN_MEDIA_TYPES", simple)
    monkeypatch.setattr(
        registry_module,
        "_FROZEN_MEDIA_SUFFIXES",
        tuple(sorted(simple, key=lambda suffix: (-len(suffix), suffix))),
    )

    assert registry_module._media_type("DATA.CSV.GZ") == "application/gzip"
    assert registry_module._media_type("DATA.unknown.GZ") == "application/example-final-suffix"


def test_production_media_type_map_is_runtime_immutable():
    with pytest.raises(TypeError):
        registry_module._FROZEN_MEDIA_TYPES[".csv"] = "text/csv"


def test_registry_and_duplicate_csv_are_fixed_order_bom_lf_and_byte_deterministic(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "b.csv").write_text("same", encoding="utf-8")
    (root / "A.csv").write_text("same", encoding="utf-8")
    result = build_asset_registry(root, _load(tmp_path / "config"))

    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    write_registry_csv(first, reversed(result.records))
    write_registry_csv(second, result.records)
    assert first.read_bytes() == second.read_bytes()
    raw = first.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw
    assert raw.decode("utf-8-sig").splitlines()[0].split(",") == list(REGISTRY_COLUMNS)

    duplicate_path = tmp_path / "duplicates.csv"
    write_duplicate_groups_csv(duplicate_path, result.duplicate_groups)
    duplicate_raw = duplicate_path.read_bytes()
    assert duplicate_raw.startswith(b"\xef\xbb\xbf")
    assert duplicate_raw.decode("utf-8-sig").splitlines()[0].split(",") == list(DUPLICATE_COLUMNS)
    assert len(duplicate_raw.decode("utf-8-sig").splitlines()) == 3


def test_csv_write_failure_is_structured(tmp_path):
    output_directory = tmp_path / "directory.csv"
    output_directory.mkdir()
    with pytest.raises(AssetRegistryError) as error:
        write_registry_csv(output_directory, [])
    assert error.value.code == "output_write_failure"


def test_nfc_path_collision_is_blocking(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    first_name = "é.csv"
    second_name = "e\u0301.csv"
    if first_name == second_name:
        pytest.skip("test names are unexpectedly identical")
    (root / first_name).write_text("one")
    try:
        (root / second_name).write_text("two")
    except OSError:
        pytest.skip("filesystem normalizes Unicode filenames")
    if len(list(root.iterdir())) != 2:
        pytest.skip("filesystem collapses Unicode-normalized filenames")

    with pytest.raises(AssetRegistryError) as error:
        discover_asset_paths(root, _load(tmp_path / "config"))
    assert error.value.code == "path_normalization_collision"


def test_production_rules_use_only_frozen_roles_and_forbid_observation_origin_kind():
    config = load_asset_rules(PRODUCTION_RULES)
    expected_roles = {
        "primary_data",
        "supplementary_information",
        "code",
        "simulation_input",
        "simulation_output",
        "model_artifact",
        "model_output",
        "computed_property_output",
        "derived_duplicate",
        "mirror_duplicate",
        "subset_view",
        "documentation",
        "restricted_reference",
        "excluded_non_domain",
    }
    assert set(ARTIFACT_ROLES) == expected_roles
    enum_document = yaml.safe_load(PRODUCTION_ENUMS.read_text(encoding="utf-8"))
    assert enum_document["enums"]["artifact_role"] == list(ARTIFACT_ROLES)
    assert {rule.artifact_role for rule in config.rules} <= set(ARTIFACT_ROLES)
    assert all(not hasattr(rule, "origin_kind") for rule in config.rules)
    assert set(LIFECYCLE_FIELDS) <= set(REGISTRY_COLUMNS)


@pytest.mark.parametrize(
    ("relative_path", "role", "stage", "hint"),
    [
        ("基础数据/PI1M_v2.csv", "primary_data", "raw", "virtual_candidate"),
        ("基础数据/smipoly_monomers.csv", "primary_data", "raw", "monomer_rule"),
        ("代码仓库镜像/MatImpute/experiment/dataset/miss_datasets/PUE/PUE_10_1.csv", "derived_duplicate", "derived", "crosslinked_pue"),
        ("代码仓库镜像/MatImpute/experiment/dataset/filled_results/PUE.csv", "model_output", "model_output", "crosslinked_pue"),
        ("代码仓库镜像/MatImpute/experiment/Et-knn-PUE_rmse.csv", "model_output", "aggregate", "mixed_material_domain"),
        ("代码仓库镜像/DQ/experiment/processed_data/PUE.csv", "derived_duplicate", "derived", "crosslinked_pue"),
        ("代码仓库镜像/PolyGraphMT/data/raw/HOMO_DFT.csv", "simulation_output", "raw", "general_polymer"),
        ("代码仓库镜像/PolyGraphMT/data/raw/TG_MD.csv", "simulation_output", "raw", "general_polymer"),
        ("代码仓库镜像/PolyGraphMT/data/raw/CP_GC.csv", "computed_property_output", "raw", "general_polymer"),
        ("代码仓库镜像/ADEPT/2.Simulations/lammps_Tg.in", "simulation_input", "raw", "computational_system"),
        ("代码仓库镜像/ADEPT/Files/model_mlp_tg.pkl", "model_artifact", "model_output", "computational_system"),
        ("外部数据/新增开放数据/第九批计算_PolyOmics/general_polymers_with_sp_abbe_dynamic-dielectric.csv", "computed_property_output", "raw", "computational_system"),
        ("外部数据/新增开放数据/第九批计算_PolyOmics/PolyOmics_PURT.csv", "subset_view", "derived", "computational_system"),
        ("仅供参考/受限来源/DiMPU2025/source_data.xlsx", "restricted_reference", "reference_only", "linear_tpu"),
        ("代码仓库镜像/ADEPT/3.Analysis/calc_Tg.py", "code", "metadata_only", "computational_system"),
        ("外部数据/am1c24715_si_001.pdf", "supplementary_information", "reference_only", "mixed_material_domain"),
        ("README.md", "documentation", "metadata_only", "unknown"),
    ],
)
def test_production_rules_freeze_required_asset_layers(relative_path, role, stage, hint):
    selected = classify_path(relative_path, load_asset_rules(PRODUCTION_RULES))
    assert (selected.artifact_role, selected.data_stage, selected.material_scope_hint) == (
        role,
        stage,
        hint,
    )


def test_new_source_audits_metadata_and_raw_observations_are_not_conflated():
    config = load_asset_rules(PRODUCTION_RULES)
    expected = {
        "外部数据/新增开放数据/DRUM_TPUU_机械回收/内容审计摘要.json": (
            "documentation",
            "derived",
            "parsed",
            "ineligible",
        ),
        "外部数据/新增开放数据/DRUM_TPUU_低天花板/曲线审计清单.tsv": (
            "documentation",
            "derived",
            "parsed",
            "ineligible",
        ),
        "外部数据/新增开放数据/Jagiellonian_硬段从头算MD/XYZ解析清单.tsv": (
            "documentation",
            "derived",
            "parsed",
            "ineligible",
        ),
        "外部数据/新增开放数据/Jagiellonian_硬段从头算MD/解包内容/Optimized_geom__MDI_1x2_opt.xyz": (
            "simulation_output",
            "raw",
            "partially_parsed",
            "not_assessed",
        ),
        "外部数据/新增开放数据/Zenodo_TPU_SWCNT热电/官方Zenodo元数据.json": (
            "documentation",
            "metadata_only",
            "parsed",
            "ineligible",
        ),
        "外部数据/新增开放数据/Zenodo_TPU_SWCNT热电/工作簿解析清单.tsv": (
            "documentation",
            "derived",
            "parsed",
            "ineligible",
        ),
        "外部数据/新增开放数据/Zenodo_TPU_SWCNT热电/解包内容/Datasheet__example.pdf": (
            "supplementary_information",
            "raw",
            "not_attempted",
            "ineligible",
        ),
        "外部数据/新增开放数据/Zenodo_TPU_SWCNT热电/解包内容/LightMicroscopy__example.tif": (
            "supplementary_information",
            "raw",
            "not_attempted",
            "ineligible",
        ),
        "外部数据/新增开放数据/Zenodo_TPU_SWCNT热电/解包内容/TE__measurement.xls": (
            "primary_data",
            "raw",
            "partially_parsed",
            "not_assessed",
        ),
        "外部数据/新增开放数据/Mendeley_PU泡沫动态力学_精选表/内容审计摘要.json": (
            "documentation",
            "derived",
            "parsed",
            "ineligible",
        ),
        "外部数据/新增开放数据/Mendeley_PU泡沫动态力学_精选表/HA_StressStrain.xlsx": (
            "primary_data",
            "raw",
            "partially_parsed",
            "not_assessed",
        ),
        "外部数据/新增开放数据/Figshare_自愈离子胶黏PU源数据/工作表解析清单.tsv": (
            "documentation",
            "derived",
            "parsed",
            "ineligible",
        ),
        "外部数据/新增开放数据/Figshare_蓖麻油脂肪族PU化学性能/内容审计摘要.json": (
            "documentation",
            "derived",
            "parsed",
            "ineligible",
        ),
    }

    for relative_path, frozen in expected.items():
        selected = classify_path(relative_path, config)
        assert (
            selected.artifact_role,
            selected.data_stage,
            selected.parse_status,
            selected.model_readiness_status,
        ) == frozen


def test_fourth_batch_experiment_and_pdf_evidence_are_fail_closed():
    config = load_asset_rules(PRODUCTION_RULES)

    mendeley_audit = classify_path(
        "外部数据/新增开放数据/Mendeley_TPU压缩打印DOE/曲线审计清单.tsv",
        config,
    )
    assert (
        mendeley_audit.artifact_role,
        mendeley_audit.data_stage,
        mendeley_audit.parse_status,
        mendeley_audit.model_readiness_status,
    ) == ("documentation", "derived", "parsed", "ineligible")

    mendeley_archive = classify_path(
        "外部数据/新增开放数据/Mendeley_TPU压缩打印DOE/7zcd9bmmg5-1.zip",
        config,
    )
    assert (
        mendeley_archive.artifact_role,
        mendeley_archive.data_stage,
        mendeley_archive.parse_status,
        mendeley_archive.model_readiness_status,
        mendeley_archive.review_required,
    ) == ("primary_data", "raw", "parsed", "blocked", True)

    pdfs = {
        "ACS_Figshare_TPU退火硬段聚集": "ma5c00142_si_001.pdf",
        "ACS_Figshare_双相演化聚氨酯": "tz5c00732_si_001.pdf",
        "ACS_Figshare_PLA立构复合TPU": "ma5c03502_si_001.pdf",
        "ACS_Figshare_呋喃高强聚氨酯": "ma5c03627_si_001.pdf",
        "ACS_Figshare_聚酰亚胺回收链扩剂PU": "ap5c04872_si_001.pdf",
        "ACS_Figshare_二氧化碳共聚酯聚氨酯": "mz6c00123_si_001.pdf",
        "ACS_Figshare_聚碳酸酯大分子二醇TPU": "ap6c00646_si_001.pdf",
        "ACS_Figshare_氢键纳米结构TPU": "ma6c00352_si_001.pdf",
    }
    for directory, filename in pdfs.items():
        metadata = classify_path(
            f"外部数据/新增开放数据/{directory}/官方API元数据.json",
            config,
        )
        audit = classify_path(
            f"外部数据/新增开放数据/{directory}/曲线审计清单.tsv",
            config,
        )
        pdf = classify_path(
            f"外部数据/新增开放数据/{directory}/{filename}",
            config,
        )
        assert (
            metadata.artifact_role,
            metadata.data_stage,
            metadata.model_readiness_status,
            metadata.review_required,
        ) == ("documentation", "metadata_only", "ineligible", True)
        assert (
            audit.artifact_role,
            audit.data_stage,
            audit.model_readiness_status,
            audit.review_required,
        ) == ("documentation", "derived", "ineligible", True)
        assert (
            pdf.artifact_role,
            pdf.data_stage,
            pdf.parse_status,
            pdf.scientific_admission_status,
            pdf.model_readiness_status,
            pdf.review_required,
        ) == (
            "supplementary_information",
            "reference_only",
            "partially_parsed",
            "pending",
            "blocked",
            True,
        )


def test_zero_byte_castor_table_is_machine_blocked_until_verified_redownload():
    selected = classify_path(
        "外部数据/新增开放数据/Figshare_蓖麻油脂肪族PU化学性能/Table_1.xls",
        load_asset_rules(PRODUCTION_RULES),
    )

    assert selected.artifact_role == "primary_data"
    assert selected.availability_status == "unreachable"
    assert selected.parse_status == "failed"
    assert selected.scientific_admission_status == "rejected"
    assert selected.model_readiness_status == "blocked"


def test_model_and_computational_output_rules_are_strictly_separated_by_priority():
    config = load_asset_rules(PRODUCTION_RULES)
    expected = {
        "代码仓库镜像/ADEPT/Files/model_mlp_tg.pkl": (
            "adept_tg_model_artifact",
            1200,
            "model_artifact",
        ),
        "代码仓库镜像/MatImpute/experiment/dataset/filled_results/PUE.csv": (
            "matimpute_filled_pue",
            1100,
            "model_output",
        ),
        "代码仓库镜像/MatImpute/experiment/Et-knn-PUE_rmse.csv": (
            "matimpute_benchmark_outputs",
            850,
            "model_output",
        ),
        "代码仓库镜像/PolyGraphMT/data/raw/CP_GC.csv": (
            "polygraphmt_gc_outputs",
            1000,
            "computed_property_output",
        ),
        "代码仓库镜像/PolyGraphMT/data/raw/HOMO_DFT.csv": (
            "polygraphmt_dft_outputs",
            1000,
            "simulation_output",
        ),
        "代码仓库镜像/PolyGraphMT/data/raw/TG_MD.csv": (
            "polygraphmt_md_outputs",
            1000,
            "simulation_output",
        ),
    }

    for relative_path, frozen in expected.items():
        selected = classify_path(relative_path, config)
        assert (selected.rule_id, selected.priority, selected.artifact_role) == frozen


def test_real_disk_inventory_is_completely_and_unambiguously_classified_read_only(tmp_path):
    config = load_asset_rules(PRODUCTION_RULES)
    result = build_asset_registry(RAW_ROOT, config)

    independent_paths: list[str] = []
    for directory, directory_names, file_names in os.walk(RAW_ROOT, followlinks=False):
        directory_names[:] = [name for name in directory_names if name.casefold() != ".git"]
        for file_name in file_names:
            independent_paths.append(
                canonical_relative_path(str((Path(directory) / file_name).relative_to(RAW_ROOT)))
            )

    assert len(result.records) == len(independent_paths)
    assert {record.relative_path for record in result.records} == set(independent_paths)
    assert result.audit["discovered"] == result.audit["classified"]
    assert result.audit["unmatched"] == 0
    assert result.audit["ambiguous"] == 0
    assert result.audit["read_failures"] == 0
    assert result.audit["input_count"] == len(result.records)
    assert result.audit["id_algorithm_version"] == "uuid5-v1"
    assert result.audit["registered_count"] + result.audit["excluded_count"] == len(
        result.records
    )
    assert result.audit["unclassified_count"] == 0
    assert result.audit["ambiguous_count"] == 0
    assert result.audit["read_failure_count"] == 0
    assert result.audit["missing_status_count"] == 0
    assert result.audit["unknown_scope_count"] is None
    assert result.audit["table_logical_hashes"] == {}
    assert result.audit["snapshot_logical_hash"] is None
    assert result.audit["integration_pending"]
    assert all(record.matched_rule_id for record in result.records)
    assert all(getattr(record, field) for record in result.records for field in LIFECYCLE_FIELDS)

    # The real source tree remains read-only; generated evidence is confined to pytest tmp_path.
    output = tmp_path / "v0.2全量资产登记.csv"
    write_registry_csv(output, result.records)
    assert output.exists()
    assert not (PROJECT_ROOT / "配置/清单" / "v0.2全量资产登记.csv").exists()

    roles = result.audit["role_counts"]
    frozen_v02_baseline_minimums = {
        "code": 117,
        "computed_property_output": 1,
        "derived_duplicate": 1196,
        "documentation": 13,
        "excluded_non_domain": 19,
        "mirror_duplicate": 2,
        "model_artifact": 1,
        "model_output": 61,
        "primary_data": 49,
        "restricted_reference": 2,
        "simulation_input": 112,
        "simulation_output": 20,
        "subset_view": 1,
        "supplementary_information": 13,
    }
    assert sum(roles.values()) == len(result.records)
    assert set(frozen_v02_baseline_minimums) <= set(roles)
    assert all(
        roles[role] >= minimum
        for role, minimum in frozen_v02_baseline_minimums.items()
    )
    assert roles["excluded_non_domain"] == result.audit["excluded_count"]
    assert result.audit["duplicate_group_count"] > 0


def test_fdm_scalar_audit_output_is_governed_as_derived_documentation():
    config = load_asset_rules(PRODUCTION_RULES)
    selected = classify_path(
        "外部数据/新增开放数据/Mendeley_FDM_TPU晶格与基材力学/标量审计清单.tsv",
        config,
    )

    assert selected.rule_id == "third_batch_mendeley_fdm_tpu_lattice_audit"
    assert selected.artifact_role == "documentation"
    assert selected.data_stage == "derived"
    assert selected.parse_status == "parsed"
    assert selected.model_readiness_status == "ineligible"
