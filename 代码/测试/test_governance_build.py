from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import governance_build as module
from asset_registry import AssetRecord, AssetRegistryResult
from asset_registry import ExactDuplicateGroup
from build_verification import BuildVerificationError
from computational_admission import (
    ComputationalAdmissionProfile,
    ExactStructureOverlapProfile,
)
from source_governance import TABLE_COLUMNS
from source_governance import SourceGovernanceBuild


def _record(
    relative_path: str = "基础数据/example.csv",
    *,
    source_scope_key: str = "scope_ok",
    source_file_uid: str = "3f69ef8c-c09f-5235-9762-b40a70d3174f",
    role: str = "primary_data",
    material_scope: str = "general_polymer",
) -> AssetRecord:
    return AssetRecord(
        asset_occurrence_uid="7fe2ace4-d58c-5ab6-bdf4-0930e8f31bd6",
        source_file_uid=source_file_uid,
        content_blob_uid="519a44f5-84d0-54bf-a9b7-82ec5d0ba251",
        record_revision_id="fa0587d8-6948-56f8-bbfd-29653ec487ae",
        relative_path=relative_path,
        source_file_natural_key=f"file:{relative_path}",
        original_name=Path(relative_path).name,
        extension=Path(relative_path).suffix,
        size_bytes=3,
        content_sha256="a" * 64,
        media_type="text/csv",
        read_status="readable",
        source_scope_key=source_scope_key,
        artifact_role=role,
        data_stage="raw",
        material_scope_hint=material_scope,
        registration_status="registered",
        availability_status="available",
        parse_status="not_attempted",
        scientific_admission_status="pending",
        model_readiness_status="not_assessed",
        release_status="not_assessed",
        matched_rule_id="test_rule",
        rules_version="test-v1",
        rule_priority=100,
        decision_method="explicit_rule",
        decision_basis="test",
        review_required=True,
    )


def _asset_result(record: AssetRecord) -> AssetRegistryResult:
    return AssetRegistryResult(
        records=(record,),
        duplicate_groups=(),
        audit={
            "status": "provisional_pass",
            "audit_scope": "asset_registry_only",
            "schema_version": "v0.2",
            "id_algorithm_version": "uuid5-v1",
            "rules_version": "test-v1",
            "discovery_scope_key": "test",
            "rule_count": 1,
            "input_count": 1,
            "registered_count": 1,
            "excluded_count": 0,
            "unclassified_count": 0,
            "ambiguous_count": 0,
            "read_failure_count": 0,
            "unknown_scope_count": None,
            "missing_status_count": 0,
            "table_logical_hashes": {},
            "snapshot_logical_hash": None,
            "integration_pending": ["test"],
            "duplicate_group_count": 0,
        },
    )


def _source_build(record: AssetRecord) -> SourceGovernanceBuild:
    tables = {
        name: [{column: "" for column in columns}]
        for name, columns in TABLE_COLUMNS.items()
    }
    tables["source_locator"][0]["source_file_id"] = record.source_file_uid
    return SourceGovernanceBuild(
        tables=tables,
        columns=dict(TABLE_COLUMNS),
        logical_hash="b" * 64,
        audit={"status": "provisional"},
    )


def _profile() -> ComputationalAdmissionProfile:
    return ComputationalAdmissionProfile(
        source_key="pi1m",
        evidence_class="virtual_polymer_structure_candidate",
        file_count=1,
        source_record_candidate_count=1,
        unique_system_candidate_count=1,
        computational_activity_candidate_count=0,
        computational_observation_candidate_count=0,
        diagnostics={"duplicate_identity_rows": 0},
    )


def _overlap() -> ExactStructureOverlapProfile:
    return ExactStructureOverlapProfile(
        source_exact_structure_counts={
            "pi1m": 1,
            "adept": 1,
            "polyomics": 1,
            "polygraphmt": 1,
        },
        pair_overlap_counts={
            "pi1m__adept": 0,
            "pi1m__polyomics": 0,
            "pi1m__polygraphmt": 0,
            "adept__polyomics": 0,
            "adept__polygraphmt": 1,
            "polyomics__polygraphmt": 0,
        },
        diagnostics={"identity_basis": "case-sensitive exact structure string"},
    )


def _write_fake_source_outputs(build: SourceGovernanceBuild, output_root: Path):
    root = Path(output_root)
    outputs = {}
    for table, rows in build.tables.items():
        filename, columns = module.TABLE_OUTPUTS[table]
        path = root / filename
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(columns), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        outputs[table] = path
    return outputs


def _configure_fake_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "数据/原始").mkdir(parents=True, exist_ok=True)
    paths = {
        name: tmp_path / f"{name}.txt"
        for name in (
            "asset_rules",
            "source_scope",
            "contract_schema",
            "enums",
            "quality_rules",
            "snapshot",
        )
    }
    for path in paths.values():
        path.write_text("test", encoding="utf-8")
    record = _record()
    asset_result = _asset_result(record)
    source_build = _source_build(record)
    monkeypatch.setattr(
        module, "load_asset_rules", lambda path: SimpleNamespace(root_hint="数据/原始")
    )
    monkeypatch.setattr(module, "load_source_scope_config", lambda path: {})
    monkeypatch.setattr(
        module,
        "load_contract_bundle",
        lambda *documents: SimpleNamespace(document_hashes={"schema": "c" * 64}),
    )
    monkeypatch.setattr(
        module,
        "verify_v01_baseline",
        lambda project, snapshot: {"status": "baseline_verified", "snapshot_id": "test"},
    )
    monkeypatch.setattr(module, "build_asset_registry", lambda root, config: asset_result)
    monkeypatch.setattr(module, "resolve_asset_scope", lambda path, config: "scope_ok")
    monkeypatch.setattr(
        module, "build_source_governance", lambda config, assets: source_build
    )
    monkeypatch.setattr(
        module, "collect_computational_profiles", lambda root, records: (_profile(),)
    )
    monkeypatch.setattr(module, "collect_exact_structure_overlap", lambda root: _overlap())
    monkeypatch.setattr(module, "write_source_governance_outputs", _write_fake_source_outputs)
    kwargs = {
        "asset_rules_path": paths["asset_rules"],
        "source_scope_path": paths["source_scope"],
        "contract_schema_path": paths["contract_schema"],
        "enums_path": paths["enums"],
        "quality_rules_path": paths["quality_rules"],
        "v01_snapshot_path": paths["snapshot"],
    }
    return asset_result, source_build, paths, kwargs


@pytest.mark.parametrize(
    "relative",
    [
        ".",
        "数据/临时/构建缓存",
        "数据/临时/构建缓存/一层/二层",
        "数据/原始/构建",
        "数据/暂存/构建",
        "数据/快照/构建",
        "../外部构建",
    ],
)
def test_output_target_rejects_outside_deep_or_formal_paths(
    tmp_path: Path, relative: str
) -> None:
    with pytest.raises(module.GovernanceBuildError) as caught:
        module.validate_output_target(tmp_path, relative)
    assert caught.value.code == "unsafe_output_root"


def test_output_target_accepts_new_target_and_rejects_any_existing_target(tmp_path: Path) -> None:
    project, target = module.validate_output_target(tmp_path, "数据/临时/构建缓存/构建A")
    assert project == tmp_path.resolve()
    assert target == (tmp_path / "数据/临时/构建缓存/构建A").resolve()

    target.mkdir(parents=True)
    with pytest.raises(module.GovernanceBuildError) as caught:
        module.validate_output_target(project, target)
    assert caught.value.code == "output_root_exists"


def test_output_target_and_project_file_fail_closed_on_missing_or_unsafe_paths(tmp_path: Path) -> None:
    with pytest.raises(module.GovernanceBuildError) as missing:
        module.validate_output_target(tmp_path / "missing", "数据/临时/构建缓存/x")
    assert missing.value.code == "project_root_missing"

    (tmp_path / "数据/临时").mkdir(parents=True)
    (tmp_path / "数据/临时/构建缓存").write_text("not a directory", encoding="utf-8")
    with pytest.raises(module.GovernanceBuildError) as parent:
        module.validate_output_target(tmp_path, "数据/临时/构建缓存/x")
    assert parent.value.code == "unsafe_output_parent"
    with pytest.raises(module.GovernanceBuildError) as document:
        module._project_file(tmp_path, "missing.yaml", label="fixture")
    assert document.value.code == "input_document_missing"
    outside = tmp_path.parent / "outside-governance-fixture.txt"
    outside.write_text("x", encoding="utf-8")
    try:
        with pytest.raises(module.GovernanceBuildError) as unsafe:
            module._project_file(tmp_path, outside, label="fixture")
        assert unsafe.value.code == "unsafe_input_document"
    finally:
        outside.unlink()


def test_structured_error_and_logical_value_domains() -> None:
    error = module.GovernanceBuildError("code", "message", field="x")
    assert error.as_dict() == {"code": "code", "message": "message", "field": "x"}
    rows = [
        {"id": "a", "payload": None},
        {"id": "b", "payload": True},
        {"id": "c", "payload": {"z": 1}},
        {"id": "d", "payload": [1, 2]},
    ]
    count, digest = module.canonical_table_logical_hash(("id", "payload"), rows)
    assert count == 4
    assert len(digest) == 64
    with pytest.raises(module.GovernanceBuildError) as schema:
        module.canonical_table_logical_hash(("id", "id"), [])
    assert schema.value.code == "invalid_hash_schema"


def test_build_lock_is_exclusive_and_removed(tmp_path: Path) -> None:
    lock_path = tmp_path / "build.lock"
    with module._BuildLock(lock_path):
        assert lock_path.is_file()
        with pytest.raises(module.GovernanceBuildError) as collision:
            with module._BuildLock(lock_path):
                pass
        assert collision.value.code == "build_lock_exists"
    assert not lock_path.exists()


def test_table_hash_is_order_independent_and_content_sensitive() -> None:
    left = [{"id": "b", "payload": 2}, {"id": "a", "payload": 1}]
    right = list(reversed(left))
    assert module.canonical_table_logical_hash(("id", "payload"), left) == (
        module.canonical_table_logical_hash(("id", "payload"), right)
    )
    changed = [{"id": "b", "payload": 3}, {"id": "a", "payload": 1}]
    assert module.canonical_table_logical_hash(("id", "payload"), left)[1] != (
        module.canonical_table_logical_hash(("id", "payload"), changed)[1]
    )


def test_table_hash_rejects_schema_drift() -> None:
    with pytest.raises(module.GovernanceBuildError) as caught:
        module.canonical_table_logical_hash(("id",), [{"id": "a", "extra": 1}])
    assert caught.value.code == "invalid_hash_row"


def test_asset_source_join_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "resolve_asset_scope", lambda path, config: "different")
    with pytest.raises(module.GovernanceBuildError) as caught:
        module.validate_asset_source_join((_record(),), {})
    assert caught.value.code == "asset_source_scope_mismatch"
    assert caught.value.context["mismatch_count"] == 1


def test_source_file_join_rejects_missing_or_duplicate_locator() -> None:
    record = _record()
    build = _source_build(record)
    module.validate_source_file_join((record,), build)
    build.tables["source_locator"].append({"source_file_id": record.source_file_uid})
    with pytest.raises(module.GovernanceBuildError) as caught:
        module.validate_source_file_join((record,), build)
    assert caught.value.code == "source_locator_duplicate"
    build.tables["source_locator"] = []
    with pytest.raises(module.GovernanceBuildError) as missing:
        module.validate_source_file_join((record,), build)
    assert missing.value.code == "source_locator_antijoin_failed"


def test_duplicate_rows_preserve_one_canonical_marker() -> None:
    group = ExactDuplicateGroup(
        duplicate_group_uid="g",
        content_sha256="a" * 64,
        member_count=2,
        canonical_asset_occurrence_uid="u1",
        canonical_relative_path="a.csv",
        members=("a.csv", "b.csv"),
        member_occurrence_uids=("u1", "u2"),
    )
    rows = module._duplicate_rows((group,))
    assert len(rows) == 2
    assert sum(row["is_canonical"] for row in rows) == 1


@pytest.mark.parametrize(
    ("raw", "columns", "code"),
    [
        (b"id\n1\n", ("id",), "output_table_missing_bom"),
        (b"\xef\xbb\xbfid\r\n1\r\n", ("id",), "output_table_non_lf"),
        (b"\xef\xbb\xbfwrong\n1\n", ("id",), "output_table_schema_mismatch"),
        (b"\xef\xbb\xbfid\n1,extra\n", ("id",), "output_table_row_malformed"),
        (b"\xef\xbb\xbfid\n\xff\n", ("id",), "output_table_decode_failed"),
    ],
)
def test_csv_reread_rejects_physical_or_schema_drift(
    tmp_path: Path, raw: bytes, columns: tuple[str, ...], code: str
) -> None:
    path = tmp_path / "table.csv"
    path.write_bytes(raw)
    with pytest.raises(module.GovernanceBuildError) as caught:
        module._read_csv_table(path, columns)
    assert caught.value.code == code
    with pytest.raises(module.GovernanceBuildError) as unreadable:
        module._read_csv_table(tmp_path / "missing.csv", columns)
    assert unreadable.value.code == "output_table_unreadable"


def test_table_set_and_missing_artifact_checks_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(module.GovernanceBuildError) as table_set:
        module._table_descriptors_from_rows({})
    assert table_set.value.code == "output_table_set_mismatch"
    descriptors = {table: (0, "a" * 64) for table in module.TABLE_OUTPUTS}
    with pytest.raises(module.GovernanceBuildError) as missing:
        module._output_artifact_manifest(tmp_path, descriptors)
    assert missing.value.code == "output_artifact_missing"


def test_dq_matimpute_counts_only_pue_derived_and_model_outputs() -> None:
    records = (
        _record(
            "代码仓库镜像/MatImpute/experiment/dataset/miss_datasets/PUE/a.csv",
            role="derived_duplicate",
            material_scope="crosslinked_pue",
        ),
        _record(
            "代码仓库镜像/MatImpute/experiment/Et-knn-PUE_rmse.csv",
            role="model_output",
            material_scope="mixed_material_domain",
        ),
        _record(
            "代码仓库镜像/MatImpute/experiment/Et-knn-glass_rmse.csv",
            role="model_output",
            material_scope="mixed_material_domain",
        ),
        _record(
            "外部数据/PUE_unrelated.csv",
            role="model_output",
            material_scope="crosslinked_pue",
        ),
    )
    assert module._dq_matimpute_counts(records) == (1, 1)


def test_atomic_build_is_deterministic_and_auditable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "数据/原始"
    raw.mkdir(parents=True)
    asset_rules = tmp_path / "asset.yaml"
    source_scope = tmp_path / "source.yaml"
    contract_schema = tmp_path / "contract.yaml"
    enums = tmp_path / "enums.yaml"
    quality_rules = tmp_path / "rules.yaml"
    snapshot = tmp_path / "snapshot.json"
    asset_rules.write_text("test", encoding="utf-8")
    source_scope.write_text("test", encoding="utf-8")
    for path in (contract_schema, enums, quality_rules, snapshot):
        path.write_text("test", encoding="utf-8")
    record = _record()
    asset_result = _asset_result(record)
    source_build = _source_build(record)

    monkeypatch.setattr(
        module, "load_asset_rules", lambda path: SimpleNamespace(root_hint="数据/原始")
    )
    monkeypatch.setattr(module, "load_source_scope_config", lambda path: {})
    monkeypatch.setattr(
        module,
        "load_contract_bundle",
        lambda *paths: SimpleNamespace(document_hashes={"schema": "c" * 64}),
    )
    monkeypatch.setattr(
        module,
        "verify_v01_baseline",
        lambda project, snapshot: {"status": "baseline_verified", "snapshot_id": "test"},
    )
    monkeypatch.setattr(module, "build_asset_registry", lambda root, config: asset_result)
    monkeypatch.setattr(module, "resolve_asset_scope", lambda path, config: "scope_ok")
    monkeypatch.setattr(
        module, "build_source_governance", lambda config, assets: source_build
    )
    monkeypatch.setattr(module, "collect_computational_profiles", lambda root, records: (_profile(),))
    monkeypatch.setattr(module, "collect_exact_structure_overlap", lambda root: _overlap())
    monkeypatch.setattr(module, "write_source_governance_outputs", _write_fake_source_outputs)

    first = module.build_governance_database(
        tmp_path,
        "数据/临时/构建缓存/构建A",
        asset_rules_path=asset_rules,
        source_scope_path=source_scope,
        contract_schema_path=contract_schema,
        enums_path=enums,
        quality_rules_path=quality_rules,
        v01_snapshot_path=snapshot,
    )
    second = module.build_governance_database(
        tmp_path,
        "数据/临时/构建缓存/构建B",
        asset_rules_path=asset_rules,
        source_scope_path=source_scope,
        contract_schema_path=contract_schema,
        enums_path=enums,
        quality_rules_path=quality_rules,
        v01_snapshot_path=snapshot,
    )
    assert first.report == second.report
    assert module.audit_governance_build(first.output_root)["input_count"] == 1
    comparison = module.compare_governance_builds(
        first.output_root,
        second.output_root,
    )
    assert comparison["status"] == "identical"


def test_failed_build_does_not_publish_partial_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "数据/原始").mkdir(parents=True)
    asset_rules = tmp_path / "asset.yaml"
    source_scope = tmp_path / "source.yaml"
    contract_schema = tmp_path / "contract.yaml"
    enums = tmp_path / "enums.yaml"
    quality_rules = tmp_path / "rules.yaml"
    snapshot = tmp_path / "snapshot.json"
    asset_rules.write_text("test", encoding="utf-8")
    source_scope.write_text("test", encoding="utf-8")
    for path in (contract_schema, enums, quality_rules, snapshot):
        path.write_text("test", encoding="utf-8")
    monkeypatch.setattr(
        module, "load_asset_rules", lambda path: SimpleNamespace(root_hint="数据/原始")
    )
    monkeypatch.setattr(module, "load_source_scope_config", lambda path: {})
    monkeypatch.setattr(
        module,
        "load_contract_bundle",
        lambda *paths: SimpleNamespace(document_hashes={"schema": "c" * 64}),
    )
    monkeypatch.setattr(
        module,
        "verify_v01_baseline",
        lambda project, snapshot: {"status": "baseline_verified", "snapshot_id": "test"},
    )
    monkeypatch.setattr(
        module,
        "build_asset_registry",
        lambda root, config: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    target = tmp_path / "数据/临时/构建缓存/失败构建"
    with pytest.raises(RuntimeError, match="boom"):
        module.build_governance_database(
            tmp_path,
            target,
            asset_rules_path=asset_rules,
            source_scope_path=source_scope,
            contract_schema_path=contract_schema,
            enums_path=enums,
            quality_rules_path=quality_rules,
            v01_snapshot_path=snapshot,
        )
    assert not target.exists()
    assert not list((tmp_path / "数据/临时/构建缓存").iterdir())


def test_finished_build_audit_rejects_report_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _asset, _source, _paths, kwargs = _configure_fake_build(tmp_path, monkeypatch)
    result = module.build_governance_database(
        tmp_path, "数据/临时/构建缓存/构建A", **kwargs
    )
    report_path = result.output_root / "TPU数据库_v0.2_资产登记审计.json"
    original = json.loads(report_path.read_text(encoding="utf-8"))

    def expect_error(code: str, mutate) -> None:
        payload = json.loads(json.dumps(original))
        mutate(payload)
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        with pytest.raises(module.GovernanceBuildError) as caught:
            module.audit_governance_build(result.output_root)
        assert caught.value.code == code

    expect_error("audit_status_invalid", lambda payload: payload.update(status="failed"))
    expect_error("declared_output_set_mismatch", lambda payload: payload.update(declared_output_files=[]))
    expect_error("audit_blocking_count", lambda payload: payload.update(unknown_scope_count=1))
    expect_error("premature_training_state", lambda payload: payload.update(training_split_created=True))
    expect_error("overlap_profile_invalid", lambda payload: payload.update(exact_structure_overlap=None))
    expect_error(
        "overlap_profile_invalid",
        lambda payload: payload["exact_structure_overlap"]["source_exact_structure_counts"].update(pi1m=True),
    )
    expect_error(
        "overlap_profile_hash_mismatch",
        lambda payload: payload.update(exact_structure_overlap_logical_hash="0" * 64),
    )
    expect_error(
        "table_logical_hash_mismatch",
        lambda payload: payload["table_logical_hashes"].update(asset_registry="0" * 64),
    )
    expect_error("snapshot_logical_hash_mismatch", lambda payload: payload.update(snapshot_logical_hash="0" * 64))
    expect_error("artifact_manifest_mismatch", lambda payload: payload.update(output_artifacts={}))
    expect_error("asset_count_invalid", lambda payload: payload.update(input_count=True))
    expect_error("asset_count_not_reconciled", lambda payload: payload.update(registered_count=0))

    report_path.write_text("[]", encoding="utf-8")
    with pytest.raises(module.GovernanceBuildError) as non_object:
        module.audit_governance_build(result.output_root)
    assert non_object.value.code == "audit_report_invalid"
    report_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(module.GovernanceBuildError) as invalid_json:
        module.audit_governance_build(result.output_root)
    assert invalid_json.value.code == "audit_report_invalid"


def test_finished_build_audit_rejects_file_set_table_and_artifact_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _asset, _source, _paths, kwargs = _configure_fake_build(tmp_path, monkeypatch)
    result = module.build_governance_database(
        tmp_path, "数据/临时/构建缓存/构建A", **kwargs
    )
    extra = result.output_root / "extra.txt"
    extra.write_text("x", encoding="utf-8")
    with pytest.raises(module.GovernanceBuildError) as file_set:
        module.audit_governance_build(result.output_root)
    assert file_set.value.code == "output_file_set_mismatch"
    extra.unlink()

    extra_directory = result.output_root / "未声明目录"
    extra_directory.mkdir()
    with pytest.raises(module.GovernanceBuildError) as directory_set:
        module.audit_governance_build(result.output_root)
    assert directory_set.value.code == "output_file_set_mismatch"
    extra_directory.rmdir()

    asset_csv = result.output_root / "v0.2全量资产登记.csv"
    original_asset = asset_csv.read_bytes()
    asset_csv.write_bytes(original_asset.replace(b"test_rule", b"other_rule"))
    with pytest.raises(module.GovernanceBuildError) as table:
        module.audit_governance_build(result.output_root)
    assert table.value.code == "table_logical_hash_mismatch"
    asset_csv.write_bytes(original_asset)

    report_md = result.output_root / "TPU数据库_v0.2_计算数据准入报告.md"
    report_md.write_text(report_md.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
    with pytest.raises(module.GovernanceBuildError) as artifact:
        module.audit_governance_build(result.output_root)
    assert artifact.value.code == "artifact_manifest_mismatch"

    with pytest.raises(module.GovernanceBuildError) as missing:
        module.audit_governance_build(tmp_path / "missing-build")
    assert missing.value.code == "build_root_missing"


def test_compare_rejects_self_audit_byte_difference_and_logical_difference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _asset, _source, _paths, kwargs = _configure_fake_build(tmp_path, monkeypatch)
    left = module.build_governance_database(tmp_path, "数据/临时/构建缓存/A", **kwargs).output_root
    right = module.build_governance_database(tmp_path, "数据/临时/构建缓存/B", **kwargs).output_root
    report = right / "TPU数据库_v0.2_资产登记审计.json"
    payload = json.loads(report.read_text(encoding="utf-8"))
    report.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(module.GovernanceBuildError) as bytes_differ:
        module.compare_governance_builds(left, right)
    assert bytes_differ.value.code == "governance_build_bytes_differ"
    relaxed = module.compare_governance_builds(
        left, right, require_byte_identical=False
    )
    assert relaxed["status"] == "different"

    monkeypatch.setattr(
        module,
        "audit_governance_build",
        lambda root: {
            "snapshot_logical_hash": "a" * 64 if Path(root) == left else "b" * 64
        },
    )
    monkeypatch.setattr(module, "_sha256_file", lambda path: "c" * 64)
    with pytest.raises(module.GovernanceBuildError) as logical:
        module.compare_governance_builds(left, right)
    assert logical.value.code == "governance_build_logical_hashes_differ"


def test_build_detects_raw_document_and_baseline_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asset_result, _source, _paths, kwargs = _configure_fake_build(tmp_path, monkeypatch)
    changed = _asset_result(replace(asset_result.records[0], content_sha256="d" * 64))
    calls = iter((asset_result, changed))
    monkeypatch.setattr(module, "build_asset_registry", lambda root, config: next(calls))
    with pytest.raises(module.GovernanceBuildError) as raw:
        module.build_governance_database(tmp_path, "数据/临时/构建缓存/raw-drift", **kwargs)
    assert raw.value.code == "raw_input_drift"

    asset_result, _source, _paths, kwargs = _configure_fake_build(tmp_path, monkeypatch)
    real_document_hashes = module._document_hashes
    hashes = iter(({"x": "a" * 64}, {"x": "b" * 64}))
    monkeypatch.setattr(module, "_document_hashes", lambda documents: next(hashes))
    with pytest.raises(module.GovernanceBuildError) as documents:
        module.build_governance_database(tmp_path, "数据/临时/构建缓存/doc-drift", **kwargs)
    assert documents.value.code == "input_document_drift"

    _configure_fake_build(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "_document_hashes", real_document_hashes)
    baseline_calls = iter(
        (
            {"status": "baseline_verified", "snapshot_id": "before"},
            {"status": "baseline_verified", "snapshot_id": "after"},
        )
    )
    monkeypatch.setattr(module, "verify_v01_baseline", lambda project, snapshot: next(baseline_calls))
    with pytest.raises(module.GovernanceBuildError) as baseline:
        module.build_governance_database(tmp_path, "数据/临时/构建缓存/baseline-drift", **kwargs)
    assert baseline.value.code == "v01_baseline_drift"


def test_build_wraps_pre_and_post_baseline_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _asset, _source, _paths, kwargs = _configure_fake_build(tmp_path, monkeypatch)
    monkeypatch.setattr(
        module,
        "verify_v01_baseline",
        lambda *_: (_ for _ in ()).throw(BuildVerificationError("drift", "changed")),
    )
    with pytest.raises(module.GovernanceBuildError) as pre:
        module.build_governance_database(tmp_path, "数据/临时/构建缓存/pre", **kwargs)
    assert pre.value.code == "v01_baseline_pre_failed"

    _configure_fake_build(tmp_path, monkeypatch)
    calls = {"count": 0}

    def post_failure(*_):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"status": "baseline_verified", "snapshot_id": "test"}
        raise BuildVerificationError("drift", "changed")

    monkeypatch.setattr(module, "verify_v01_baseline", post_failure)
    with pytest.raises(module.GovernanceBuildError) as post:
        module.build_governance_database(tmp_path, "数据/临时/构建缓存/post", **kwargs)
    assert post.value.code == "v01_baseline_post_failed"
