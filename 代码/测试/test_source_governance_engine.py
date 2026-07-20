from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from contract import (
    validate_citation_rows,
    validate_locator_rows,
    validate_supersession_chains,
)
from record_identity import stable_record_uid
from source_governance import (
    CITATION_ASSIGNMENT_COLUMNS,
    CITATION_COLUMNS,
    RIGHTS_ACTION_CANDIDATE_COLUMNS,
    SOURCE_COLUMNS,
    SOURCE_LOCATOR_COLUMNS,
    SOURCE_SCOPE_COLUMNS,
    SOURCE_SCOPE_RELATION_COLUMNS,
    SourceGovernanceBuild,
    SourceGovernanceError,
    build_source_governance,
    load_source_scope_config,
    normalize_relative_path,
    resolve_asset_scope,
    validate_source_scope_config,
    write_source_governance_outputs,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "配置" / "v0.2来源范围.yaml"
CONTRACT_PATH = ROOT / "配置/结构定义" / "v0.2来源治理合同.yaml"
LEDGER_PATH = ROOT / "文档" / "数据来源与参考文献.md"


def _config() -> dict:
    return load_source_scope_config(CONFIG_PATH)


def _asset(relative_path: str, *, suffix: str = "") -> dict[str, object]:
    normalized = normalize_relative_path(relative_path)
    source_file_id = stable_record_uid(
        "source_file", {"fixture_path": normalized, "suffix": suffix}
    )
    return {
        "relative_path": relative_path,
        "source_file_id": source_file_id,
        "content_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    }


def test_nested_duplicate_yaml_keys_fail_closed(tmp_path: Path):
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text(
        "schema_version: v0.2\n"
        "config_version: test\n"
        "sources:\n"
        "  - source_key: first\n"
        "    title: one\n"
        "    title: two\n",
        encoding="utf-8",
    )

    with pytest.raises(SourceGovernanceError, match="duplicate YAML key") as error:
        load_source_scope_config(duplicate)

    assert error.value.code == "duplicate_yaml_key"


def test_real_configuration_covers_required_scopes_and_all_ledger_citations():
    config = _config()
    scope_keys = {scope["source_scope_key"] for scope in config["scopes"]}
    required = {
        "scope_smipoly_dataset",
        "scope_openpoly_dataset",
        "scope_pi1m_dataset",
        "scope_adept_release",
        "scope_dq_release",
        "scope_matimpute_release",
        "scope_polygraphmt_raw",
        "scope_viscosity_release",
        "scope_pugar_modulus_si_002",
        "scope_pugar_tg_si_002",
        "scope_pugar_tg_si_003",
        "scope_pugar_viscosity_feature_spaces",
        "scope_eom_source_main",
        "scope_eom_source_supplementary",
        "scope_nature2025_source_data",
        "scope_wpu_dcr_source_data",
        "scope_polyomics_general",
        "scope_polyomics_purt",
        "scope_pu18_deposit",
        "scope_pue643_esi",
        "scope_acs_tg_extension",
        "scope_cial_si",
        "scope_wiley_bpue_1500",
        "scope_dimpu_source_data",
        "scope_pun_source_data",
        "scope_internal_plan",
        "scope_project_readme",
        "scope_sciencedb_withdrawn",
        "scope_zenodo15370425",
        "scope_zenodo14983287",
        "scope_zenodo6390478",
        "scope_mendeley_tby33jd48k_v1",
        "scope_mendeley_byjbmymyhh_v5",
        "scope_figshare23635998_v1",
        "scope_nature2026_source_data",
        "scope_mendeley_7zcd9bmmg5_v1",
        "scope_acs_figshare_28906446_v1",
        "scope_acs_figshare_29074233_v1",
        "scope_acs_figshare_31333274_v1",
        "scope_acs_figshare_31429142_v1",
        "scope_acs_figshare_31614502_v1",
        "scope_acs_figshare_31989433_v1",
        "scope_acs_figshare_32256977_v1",
        "scope_acs_figshare_32567339_v1",
    }

    assert required <= scope_keys
    assert [item["ledger_number"] for item in config["citations"]] == list(
        range(1, len(config["citations"]) + 1)
    )
    citation_keys = [item["citation_key"] for item in config["citations"]]
    assert len(citation_keys) == len(set(citation_keys)) == len(config["citations"])
    roles = {
        role for item in config["citations"] for role in item["citation_roles"]
    }
    assert {
        "dataset",
        "original_measurement",
        "method",
        "software",
        "license_source",
    } <= roles


def test_machine_citations_match_the_formal_markdown_reference_ledger_exactly():
    config = _config()
    ledger_entries = {
        int(number): reference.replace("*", "")
        for number, reference in re.findall(
            r"^\[(\d+)\] (.+)$",
            LEDGER_PATH.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    }

    assert set(ledger_entries) == set(range(1, len(config["citations"]) + 1))
    for citation in config["citations"]:
        number = citation["ledger_number"]
        assert citation["reference_text"] == ledger_entries[number]
        assert citation["title"].casefold() in ledger_entries[number].casefold()
        for author in citation["authors"]:
            assert author.rstrip(".").casefold() in ledger_entries[number].casefold()


def test_contract_backed_output_columns_and_exact_uuid5_algorithm():
    config = _config()
    contract = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    build = build_source_governance(
        config,
        [_asset("基础数据/openpoly.csv", suffix="contract")],
    )

    contract_tables = contract["tables"]
    for table_name in (
        "source",
        "source_scope",
        "source_scope_relation",
        "source_locator",
        "citation",
        "citation_assignment",
    ):
        assert list(build.columns[table_name]) == list(
            contract_tables[table_name]["fields"]
        )

    for table_name in ("source", "source_scope", "source_locator", "citation"):
        assert all(UUID(str(row["record_uid"])).version == 5 for row in build.tables[table_name])

    source = build.tables["source"][0]
    source_identity = {
        "source_kind": source["source_kind"],
        "canonical_identifier": source["canonical_identifier"],
        "version_label": source["version_label"],
    }
    assert source["source_id"] == stable_record_uid(
        "source_id", source_identity, algorithm_version="uuid5-v1"
    )
    assert source["record_uid"] == stable_record_uid(
        "source", source_identity, algorithm_version="uuid5-v1"
    )

    for scope in build.tables["source_scope"]:
        scope_identity = {"source_scope_key": scope["source_scope_key"]}
        assert scope["source_scope_id"] == stable_record_uid(
            "source_scope_id", scope_identity, algorithm_version="uuid5-v1"
        )
        assert scope["record_uid"] == stable_record_uid(
            "source_scope", scope_identity, algorithm_version="uuid5-v1"
        )


def test_locators_and_citations_pass_contract_semantic_validators():
    build = build_source_governance(
        _config(),
        [_asset("基础数据/openpoly.csv", suffix="semantic")],
    )

    validate_locator_rows(build.tables["source_locator"])
    validate_citation_rows(build.tables["citation"])
    assert all(
        row["locator_hash_algorithm_version"] == "tpu-locator-json/1"
        for row in build.tables["source_locator"]
    )
    for row in build.tables["citation"]:
        authors = json.loads(str(row["authors_json"]))
        assert authors
        assert all(str(author).strip().casefold() != "et al." for author in authors)
        assert row["title"].casefold() in str(row["reference_text"]).casefold()
        assert "*" not in str(row["reference_text"])
        if row["bibtex_text"] is not None:
            assert str(row["bibtex_text"]).lstrip().startswith("@")


def test_scope_parent_chain_is_same_source_and_cross_source_navigation_is_relational():
    config = _config()
    build = build_source_governance(config, [])
    scopes = {row["source_scope_id"]: row for row in build.tables["source_scope"]}
    for scope in scopes.values():
        parent_id = scope["parent_scope_id"]
        if parent_id is not None:
            assert scopes[parent_id]["source_id"] == scope["source_id"]

    configured = {
        row["source_scope_key"]: row for row in config["scopes"]
    }
    cross_source_edges = {
        (key, str(row["parent_scope_key"]))
        for key, row in configured.items()
        if row.get("parent_scope_key") is not None
        and configured[str(row["parent_scope_key"])]["source_key"]
        != row["source_key"]
    }
    scope_key_by_id = {
        row["source_scope_id"]: row["source_scope_key"]
        for row in build.tables["source_scope"]
    }
    emitted_edges = {
        (
            scope_key_by_id[row["subject_scope_id"]],
            scope_key_by_id[row["object_scope_id"]],
        )
        for row in build.tables["source_scope_relation"]
        if row["relation_type"] == "subset_of"
        and str(row["evidence_summary"]).startswith("配置层级迁移")
    }
    assert emitted_edges == cross_source_edges


def test_publication_data_repository_file_and_partition_are_distinct_scopes():
    config = _config()
    scopes = {item["source_scope_key"]: item for item in config["scopes"]}

    assert scopes["scope_polygraphmt_publication"]["scope_kind"] == "publication"
    assert scopes["scope_polygraphmt_release"]["scope_kind"] == "repository_release"
    assert scopes["scope_polygraphmt_raw"]["scope_kind"] == "logical_partition"
    assert scopes["scope_sciencedb_withdrawn"]["scope_kind"] == "dataset_version"
    identifiers = {
        scopes[key]["canonical_identifier"]
        for key in (
            "scope_polygraphmt_publication",
            "scope_polygraphmt_release",
            "scope_polygraphmt_raw",
            "scope_sciencedb_withdrawn",
        )
    }
    assert len(identifiers) == 4


def test_adept_polygraphmt_are_one_study_family_with_explicit_overlap_evidence():
    config = _config()
    sources = {item["source_key"]: item for item in config["sources"]}
    citation = next(
        item for item in config["citations"] if item["ledger_number"] == 8
    )
    family_keys = {
        sources["source_adept_repo"]["source_family_key"],
        sources["source_polygraphmt_repo"]["source_family_key"],
        citation["source_family_key"],
    }
    family_types = {
        sources["source_adept_repo"]["source_family_type"],
        sources["source_polygraphmt_repo"]["source_family_type"],
        citation["source_family_type"],
    }
    assert family_keys == {"family_adept_polygraphmt_study"}
    assert family_types == {"parent_dataset"}

    relation = next(
        item
        for item in config["relations"]
        if item["relation_key"] == "rel_adept_polygraphmt_same_study"
    )
    assert relation["relation_type"] in {"companion_to", "same_study_as"}
    assert relation["subject_scope_key"] == "scope_polygraphmt_release"
    assert relation["object_scope_key"] == "scope_adept_release"
    evidence = relation["evidence_summary"]
    assert "12,271" in evidence and "13,272" in evidence
    assert "exact SMILES" in evidence
    assert "同一文件" in evidence and "独立观测" in evidence


def test_parent_cycle_and_relation_cycle_fail_closed():
    parent_cycle = deepcopy(_config())
    scopes = {item["source_scope_key"]: item for item in parent_cycle["scopes"]}
    scopes["scope_project_readme"]["parent_scope_key"] = "scope_internal_plan"
    scopes["scope_internal_plan"]["parent_scope_key"] = "scope_project_readme"

    with pytest.raises(SourceGovernanceError) as parent_error:
        validate_source_scope_config(parent_cycle)
    assert parent_error.value.code == "scope_graph_cycle"

    relation_cycle = deepcopy(_config())
    relation_cycle["relations"].extend(
        [
            {
                "relation_key": "test_cycle_a",
                "subject_scope_key": "scope_wiley_bpue_1500",
                "object_scope_key": "scope_sciencedb_withdrawn",
                "relation_type": "companion_to",
                "evidence_summary": "negative test edge A",
                "review_status": "not_reviewed",
            },
            {
                "relation_key": "test_cycle_b",
                "subject_scope_key": "scope_sciencedb_withdrawn",
                "object_scope_key": "scope_wiley_bpue_1500",
                "relation_type": "companion_to",
                "evidence_summary": "negative test edge B",
                "review_status": "not_reviewed",
            },
        ]
    )
    with pytest.raises(SourceGovernanceError) as relation_error:
        validate_source_scope_config(relation_cycle)
    assert relation_error.value.code == "scope_graph_cycle"


def test_unknown_and_same_priority_ambiguous_paths_are_blocking():
    config = _config()
    with pytest.raises(SourceGovernanceError) as unknown:
        resolve_asset_scope("外部数据/从未登记的新文件.csv", config)
    assert unknown.value.code == "unknown_asset_scope"

    ambiguous = deepcopy(config)
    ambiguous["path_mappings"].append(
        {
            "mapping_id": "test_ambiguous_openpoly",
            "priority": 900,
            "match_type": "exact",
            "pattern": "基础数据/openpoly.csv",
            "source_scope_key": "scope_pi1m_dataset",
        }
    )
    original = next(
        item
        for item in ambiguous["path_mappings"]
        if item["pattern"] == "基础数据/openpoly.csv"
    )
    original["priority"] = 900

    with pytest.raises(SourceGovernanceError) as conflict:
        resolve_asset_scope("基础数据/openpoly.csv", ambiguous)
    assert conflict.value.code == "ambiguous_asset_scope"


def test_file_scope_is_stably_derived_from_parent_and_canonical_posix_path():
    config = _config()
    windows_asset = _asset("基础数据\\openpoly.csv")
    posix_asset = dict(windows_asset, relative_path="基础数据/openpoly.csv")

    first = build_source_governance(config, [windows_asset])
    second = build_source_governance(config, [posix_asset])
    first_file_scope = next(
        item for item in first.tables["source_scope"] if item["scope_kind"] == "file"
    )
    second_file_scope = next(
        item for item in second.tables["source_scope"] if item["scope_kind"] == "file"
    )

    assert first_file_scope == second_file_scope
    assert first_file_scope["canonical_identifier"].endswith(
        "基础数据/openpoly.csv"
    )
    assert "\\" not in first_file_scope["canonical_identifier"]
    configured = {
        item["source_scope_key"]: item for item in first.tables["source_scope"]
    }
    assert first_file_scope["parent_scope_id"] == configured[
        "openpoly_local_export"
    ]["source_scope_id"]


def test_every_current_discovered_asset_resolves_and_reaches_configured_root():
    config = _config()
    discovery_root = ROOT / config["discovery_root"]
    paths = sorted(
        (
            path.relative_to(discovery_root).as_posix()
            for path in discovery_root.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(discovery_root).parts
        ),
        key=lambda value: (value.casefold(), value),
    )
    assets = [_asset(path, suffix=str(index)) for index, path in enumerate(paths)]

    build = build_source_governance(config, assets)
    file_scopes = [
        item for item in build.tables["source_scope"] if item["scope_kind"] == "file"
    ]

    assert paths
    assert len(file_scopes) == len(paths)
    assert build.audit["asset_count"] == len(paths)
    assert build.audit["unknown_asset_scope_count"] == 0
    assert build.audit["ambiguous_asset_scope_count"] == 0
    assert build.audit["unreachable_file_scope_count"] == 0
    assert build.audit["source_chain_conflict_count"] == 0
    assert build.audit["automatic_allow_count"] == 0
    assert build.audit["source_locator_count"] == len(paths)
    scopes_by_key = {
        item["source_scope_key"]: item for item in config["scopes"]
    }
    expected_cross_source_relations = sum(
        item.get("parent_scope_key") is not None
        and scopes_by_key[item["parent_scope_key"]]["source_key"]
        != item["source_key"]
        for item in config["scopes"]
    )
    assert (
        build.audit["derived_cross_source_relation_count"]
        == expected_cross_source_relations
    )


def test_rights_candidates_never_infer_allow_from_access_or_publication_license():
    config = _config()
    build = build_source_governance(
        config,
        [
            _asset("外部数据/TPU_HBond_2021_Source_Main.xlsx", suffix="ccby"),
            _asset("外部数据/PUE_StressStrain_2026_ESI.pdf", suffix="withdrawn"),
        ],
    )
    candidates = build.tables["rights_action_candidate"]

    assert candidates
    assert {item["candidate_status"] for item in candidates} <= {
        "pending",
        "manual_review",
        "block",
    }
    assert {item["mapped_decision"] for item in candidates} <= {
        "manual_review",
        "deny",
    }
    assert not any(item["mapped_decision"].startswith("allow") for item in candidates)
    assert build.audit["automatic_allow_count"] == 0
    eom = [
        item
        for item in candidates
        if "TPU_HBond_2021_Source_Main.xlsx" in item["target_scope_key"]
    ]
    assert eom
    by_operation = {item["operation"]: item["candidate_status"] for item in eom}
    assert by_operation["retrieve"] == "pending"
    assert by_operation["analyze"] == "manual_review"
    assert by_operation["train"] == "manual_review"
    assert by_operation["redistribute"] == "block"
    assert by_operation["publish"] == "block"
    assert by_operation["deploy"] == "block"


def test_ledger_citations_have_stable_keys_and_role_assignments():
    config = _config()
    first = build_source_governance(config, [])
    second = build_source_governance(config, [])

    citations = first.tables["citation"]
    assignments = first.tables["citation_assignment"]
    expected_count = len(config["citations"])
    assert len(citations) == expected_count
    assert citations == second.tables["citation"]
    assert assignments == second.tables["citation_assignment"]
    assert len({item["citation_key"] for item in citations}) == expected_count
    assert all(item["citation_key"].startswith("ledger-") for item in citations)
    assert {
        "dataset",
        "original_measurement",
        "method",
        "software",
        "license_source",
    } <= {item["citation_role"] for item in assignments}
    pending_incomplete_author_lists = {
        item["citation_key"]
        for item in citations
        if item["review_status"] == "pending"
    }
    assert pending_incomplete_author_lists == {
        "ledger-020-polyomics-2025",
        "ledger-022-jiang-2021-she",
        "ledger-023-li-2026-mechanophore",
        "ledger-029-huang-2025-dimpu",
        "ledger-030-kong-2026-pun",
        "ledger-032-opoly26",
        "ledger-049-liu-2026-thermal-conductivity",
    }


def test_citation_revision_supersedes_an_older_citation_of_the_same_source():
    config = deepcopy(_config())
    older = config["citations"][0]
    newer = config["citations"][1]
    config["sources"].append(
        {
            "source_key": newer["source_key"],
            "source_kind": newer["source_kind"],
            "canonical_identifier": newer["doi"],
            "version_label": "published",
            "title": newer["title"],
            "publisher_or_repository": "见来源台账",
            "source_family_key": newer["source_family_key"],
            "source_family_type": newer["source_family_type"],
            "availability_status": "metadata_only",
        }
    )
    old_doi = str(newer["doi"])
    newer.update(
        source_key=older["source_key"],
        source_kind=older["source_kind"],
        source_family_key=older["source_family_key"],
        source_family_type=older["source_family_type"],
        doi=older["doi"],
        supersedes_citation_key=older["citation_key"],
        reference_text=str(newer["reference_text"]).replace(
            old_doi,
            str(older["doi"]),
        ),
    )

    build = build_source_governance(config, [])
    rows = {
        row["citation_key"]: row for row in build.tables["citation"]
    }
    assert rows[newer["citation_key"]]["source_id"] == rows[older["citation_key"]][
        "source_id"
    ]
    assert rows[newer["citation_key"]]["supersedes_citation_id"] == rows[
        older["citation_key"]
    ]["citation_id"]
    validate_supersession_chains({"citation": build.tables["citation"]})


def test_output_columns_bom_stable_sort_and_byte_determinism(tmp_path: Path):
    config = _config()
    assets = [
        _asset("基础数据/smipoly_monomers.csv", suffix="z"),
        _asset("基础数据/openpoly.csv", suffix="a"),
    ]
    first = build_source_governance(config, list(reversed(assets)))
    second = build_source_governance(config, assets)
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    paths_a = write_source_governance_outputs(first, out_a)
    paths_b = write_source_governance_outputs(second, out_b)

    assert tuple(first.columns["source"]) == SOURCE_COLUMNS
    assert tuple(first.columns["source_scope"]) == SOURCE_SCOPE_COLUMNS
    assert tuple(first.columns["source_scope_relation"]) == SOURCE_SCOPE_RELATION_COLUMNS
    assert tuple(first.columns["source_locator"]) == SOURCE_LOCATOR_COLUMNS
    assert tuple(first.columns["citation"]) == CITATION_COLUMNS
    assert tuple(first.columns["citation_assignment"]) == CITATION_ASSIGNMENT_COLUMNS
    assert (
        tuple(first.columns["rights_action_candidate"])
        == RIGHTS_ACTION_CANDIDATE_COLUMNS
    )
    assert paths_a.keys() == paths_b.keys()
    for table_name in paths_a:
        payload_a = paths_a[table_name].read_bytes()
        payload_b = paths_b[table_name].read_bytes()
        assert payload_a.startswith(b"\xef\xbb\xbf")
        assert payload_a == payload_b, table_name
    assert first.logical_hash == second.logical_hash


def test_absolute_path_escape_and_missing_source_file_identity_fail_closed():
    config = _config()
    with pytest.raises(SourceGovernanceError) as path_error:
        build_source_governance(config, [_asset("C:/absolute.csv")])
    assert path_error.value.code == "path_escape"

    with pytest.raises(SourceGovernanceError) as identity_error:
        build_source_governance(
            config, [{"relative_path": "基础数据/openpoly.csv"}]
        )
    assert identity_error.value.code == "asset_identity_missing"


def test_configuration_and_build_fail_closed_negative_matrix(tmp_path: Path):
    base = _config()

    def invalid(mutator, expected_code: str):
        candidate = deepcopy(base)
        mutator(candidate)
        with pytest.raises(SourceGovernanceError) as error:
            validate_source_scope_config(candidate)
        assert error.value.code == expected_code

    invalid(lambda c: c.__setitem__("schema_version", "v9"), "config_version_invalid")
    invalid(lambda c: c.__setitem__("id_algorithm_version", "uuid5-v2"), "id_algorithm_version_invalid")
    invalid(lambda c: c.__setitem__("discovery_root", "other"), "discovery_root_invalid")
    invalid(lambda c: c.__setitem__("observed_at", "2026-07-19"), "timestamp_invalid")
    invalid(lambda c: c["sources"][0].__setitem__("source_key", "Bad Key"), "config_key_invalid")
    invalid(lambda c: c.__setitem__("root_scope_key", "missing"), "scope_root_missing")
    invalid(lambda c: c["scopes"][1].__setitem__("source_key", "missing"), "scope_source_unknown")
    invalid(lambda c: c["scopes"][1].__setitem__("parent_scope_key", "missing"), "scope_parent_unknown")
    invalid(lambda c: c["scopes"][0].__setitem__("parent_scope_key", "scope_project_readme"), "scope_root_invalid")
    invalid(lambda c: c["relations"][0].__setitem__("subject_scope_key", "missing"), "scope_relation_unknown")
    invalid(lambda c: c["relations"][0].__setitem__("object_scope_key", c["relations"][0]["subject_scope_key"]), "scope_relation_self")
    invalid(lambda c: c.__setitem__("path_mappings", []), "path_mapping_missing")
    invalid(lambda c: c["path_mappings"][0].__setitem__("priority", -1), "path_mapping_invalid")
    invalid(lambda c: c["path_mappings"][0].__setitem__("match_type", "glob"), "path_mapping_invalid")
    invalid(lambda c: c["path_mappings"][0].__setitem__("source_scope_key", "missing"), "path_mapping_scope_unknown")
    invalid(
        lambda c: next(
            item for item in c["path_mappings"] if item["mapping_id"] == "root_readme"
        ).__setitem__("pattern", "README.md/"),
        "path_mapping_invalid",
    )
    invalid(lambda c: c["citations"][-1].__setitem__("ledger_number", 99), "citation_ledger_incomplete")
    invalid(lambda c: c["citations"][0].__setitem__("citation_key", "bad"), "citation_key_invalid")
    invalid(lambda c: c["citations"][0].__setitem__("target_scope_key", "missing"), "citation_target_unknown")
    invalid(lambda c: c["citations"][0].__setitem__("citation_roles", []), "citation_roles_invalid")
    invalid(lambda c: c["citations"][0].__setitem__("issued_year", 100), "citation_year_invalid")
    invalid(lambda c: c["citations"][0].__setitem__("authors", [1]), "citation_authors_invalid")
    invalid(lambda c: c["citations"][0].__setitem__("authors", []), "citation_authors_missing")
    invalid(lambda c: c["citations"][0].__setitem__("authors", ["et al."]), "citation_authors_invalid")
    invalid(lambda c: c["citations"][0].__setitem__("reference_text", "not a formal reference"), "citation_reference_text_invalid")
    invalid(lambda c: c["citations"][0].__setitem__("bibtex_text", "[1] Markdown reference"), "citation_bibtex_invalid")
    invalid(lambda c: c["citations"][0].__setitem__("supersedes_citation_key", "missing"), "citation_supersedes_unknown")
    invalid(lambda c: c["citations"][0].__setitem__("supersedes_citation_key", c["citations"][0]["citation_key"]), "citation_supersedes_self")
    invalid(lambda c: c["citations"][0].__setitem__("supersedes_citation_key", c["citations"][1]["citation_key"]), "citation_supersedes_source_mismatch")
    invalid(lambda c: c.__setitem__("rights_actions", []), "rights_actions_missing")
    invalid(lambda c: c["rights_actions"].append(deepcopy(c["rights_actions"][0])), "rights_action_duplicate")
    invalid(lambda c: c["rights_actions"][0].__setitem__("mapped_decision", "deny"), "rights_action_mapping_invalid")
    invalid(lambda c: c["rights_actions"][-1].__setitem__("mapped_decision", "manual_review"), "rights_action_mapping_invalid")

    with pytest.raises(SourceGovernanceError) as root_error:
        validate_source_scope_config([])
    assert root_error.value.code == "config_root_invalid"
    for invalid_path in (None, "a/../b", "a//b"):
        with pytest.raises(SourceGovernanceError):
            normalize_relative_path(invalid_path)

    malformed = tmp_path / "malformed.yaml"
    malformed.write_text("- not-a-mapping\n", encoding="utf-8")
    with pytest.raises(SourceGovernanceError) as malformed_error:
        load_source_scope_config(malformed)
    assert malformed_error.value.code == "config_root_invalid"
    with pytest.raises(SourceGovernanceError) as missing_error:
        load_source_scope_config(tmp_path / "missing.yaml")
    assert missing_error.value.code == "config_load_failed"

    bad_regex = deepcopy(base)
    bad_regex["path_mappings"][0].update(
        match_type="regex", pattern="^unclosed([ $"
    )
    with pytest.raises(SourceGovernanceError) as regex_error:
        validate_source_scope_config(bad_regex)
    assert regex_error.value.code in {"path_mapping_invalid", "path_mapping_not_canonical"}

    conflicted_asset = _asset("基础数据/openpoly.csv")
    conflicted_asset["source_file_uid"] = stable_record_uid("source_file", {"other": 1})
    with pytest.raises(SourceGovernanceError) as conflict:
        build_source_governance(base, [conflicted_asset])
    assert conflict.value.code == "asset_identity_conflict"
    invalid_identity = _asset("基础数据/openpoly.csv")
    invalid_identity["source_file_id"] = "not-a-uuid"
    with pytest.raises(SourceGovernanceError) as invalid_identity_error:
        build_source_governance(base, [invalid_identity])
    assert invalid_identity_error.value.code == "asset_identity_invalid"
    mismatched_scope = _asset("基础数据/openpoly.csv")
    mismatched_scope["source_scope_key"] = "scope_pi1m_dataset"
    with pytest.raises(SourceGovernanceError) as scope_mismatch:
        build_source_governance(base, [mismatched_scope])
    assert scope_mismatch.value.code == "asset_scope_mismatch"
    duplicate_identity_left = _asset("基础数据/openpoly.csv")
    duplicate_identity_right = _asset("基础数据/smipoly_monomers.csv")
    duplicate_identity_right["source_file_id"] = duplicate_identity_left[
        "source_file_id"
    ]
    with pytest.raises(SourceGovernanceError) as duplicate_identity:
        build_source_governance(
            base,
            [duplicate_identity_left, duplicate_identity_right],
        )
    assert duplicate_identity.value.code == "asset_identity_duplicate"
    with pytest.raises(SourceGovernanceError) as bad_asset:
        build_source_governance(base, ["not-a-mapping"])
    assert bad_asset.value.code == "asset_invalid"
    duplicate = _asset("基础数据/openpoly.csv")
    with pytest.raises(SourceGovernanceError) as duplicate_error:
        build_source_governance(base, [duplicate, duplicate])
    assert duplicate_error.value.code == "asset_path_duplicate"


def test_optional_csl_fields_and_output_guards(tmp_path: Path):
    config = deepcopy(_config())
    config["citations"][0]["authors"] = ["Ding, F."]
    config["citations"][0]["canonical_url"] = "https://doi.org/10.1007/s10118-022-2838-6"
    build = build_source_governance(config, [])
    first = next(row for row in build.tables["citation"] if row["citation_key"].startswith("ledger-001"))
    assert "Ding, F." in first["authors_json"]
    assert "https://doi.org" in first["csl_json"]

    with pytest.raises(SourceGovernanceError) as unsafe:
        write_source_governance_outputs(build, tmp_path / "数据/暂存")
    assert unsafe.value.code == "unsafe_output_root"

    missing_table = dict(build.tables)
    missing_table.pop("citation")
    broken = SourceGovernanceBuild(
        tables=missing_table,
        columns=build.columns,
        logical_hash=build.logical_hash,
        audit=build.audit,
    )
    with pytest.raises(SourceGovernanceError) as missing:
        write_source_governance_outputs(broken, tmp_path / "missing")
    assert missing.value.code == "output_table_missing"

    bad_columns = {name: [dict(row) for row in rows] for name, rows in build.tables.items()}
    bad_columns["source"][0]["unexpected"] = True
    broken_columns = SourceGovernanceBuild(
        tables=bad_columns,
        columns=build.columns,
        logical_hash=build.logical_hash,
        audit=build.audit,
    )
    with pytest.raises(SourceGovernanceError) as columns:
        write_source_governance_outputs(broken_columns, tmp_path / "columns")
    assert columns.value.code == "output_columns_invalid"
