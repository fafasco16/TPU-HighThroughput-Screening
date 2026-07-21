from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import tarfile
from collections import Counter

import pytest

from 生成数据总账 import GOLD_C_VALUE_COLUMNS as INVENTORY_GOLD_C_COLUMNS
from 审计 import 第十二批PUFoam as pufoam_module
from 审计.第十二批PUFoam import (
    ARCHIVE_BYTES,
    ARCHIVE_PATH,
    ARCHIVE_SHA256,
    EXPECTED_DERIVED_ROWS,
    EXPECTED_NATIVE_ROWS,
    EXPECTED_POLICY_TIER_COUNTS,
    EXPECTED_TOTAL_ROWS,
    GOLD_C_VALUE_COLUMNS,
    SIMULATION_KEY,
    SOURCE_ID,
    AuditBlocked,
    _sha256,
    _validate_archive_member,
    audit,
    build_gold_c_rows,
)


def _tarinfo(name: str, *, kind: bytes = tarfile.REGTYPE, linkname: str = "") -> tarfile.TarInfo:
    member = tarfile.TarInfo(name)
    member.type = kind
    member.linkname = linkname
    return member


def test_冻结身份_成员计数_安全链接与单一算例() -> None:
    payload = audit()
    archive = payload["archive"]
    case = payload["case"]

    assert ARCHIVE_PATH.stat().st_size == ARCHIVE_BYTES == 8_252_834
    assert _sha256(ARCHIVE_PATH) == ARCHIVE_SHA256
    assert archive["member_count"] == 3_822
    assert archive["regular_file_count"] == 3_452
    assert archive["directory_count"] == 331
    assert archive["symlink_count"] == 39
    assert archive["special_member_count"] == 0
    assert len(archive["safe_relative_symlinks"]) == 39
    assert all(
        link["resolved_target"] == "PUFoam"
        or link["resolved_target"].startswith("PUFoam/")
        for link in archive["safe_relative_symlinks"]
    )

    assert case["case_count"] == 1
    assert case["geometry"] == "2D mixing-cup"
    assert case["result_time_directory_count"] == 51
    assert case["result_times_s"] == list(range(51))
    assert case["scalar_result_times_s"] == list(range(1, 51))
    assert case["vol_scalar_field_count_per_time"] == 52
    assert case["uniform_field_file_count"] == 462
    assert case["nonuniform_field_file_count"] == 2_138


def test_路径门禁拒绝绝对路径_穿越与越界链接() -> None:
    with pytest.raises(AuditBlocked):
        _validate_archive_member(_tarinfo("/etc/passwd"))
    with pytest.raises(AuditBlocked):
        _validate_archive_member(_tarinfo("PUFoam/../escape.txt"))
    with pytest.raises(AuditBlocked):
        _validate_archive_member(
            _tarinfo(
                "PUFoam/lnInclude/escape.H",
                kind=tarfile.SYMTYPE,
                linkname="../../escape.H",
            )
        )
    with pytest.raises(AuditBlocked):
        _validate_archive_member(
            _tarinfo(
                "PUFoam/lnInclude/absolute.H",
                kind=tarfile.SYMTYPE,
                linkname="/etc/passwd",
            )
        )

    accepted = _validate_archive_member(
        _tarinfo(
            "PUFoam/PBE/lnInclude/MomEqns.H",
            kind=tarfile.SYMTYPE,
            linkname="../MomEqns.H",
        )
    )
    assert accepted == {
        "member": "PUFoam/PBE/lnInclude/MomEqns.H",
        "linkname": "../MomEqns.H",
        "resolved_target": "PUFoam/PBE/MomEqns.H",
    }


def test_gold_c_固定计数_字段契约_唯一身份与单一模拟分组() -> None:
    rows = build_gold_c_rows()
    payload = audit()["materialization"]

    assert tuple(INVENTORY_GOLD_C_COLUMNS) == GOLD_C_VALUE_COLUMNS
    assert len(rows) == EXPECTED_TOTAL_ROWS == 9_014
    assert payload["native_volume_average_count"] == EXPECTED_NATIVE_ROWS == 250
    assert payload["derived_spatial_statistic_count"] == EXPECTED_DERIVED_ROWS == 8_764
    assert payload["total_gold_c_count"] == EXPECTED_TOTAL_ROWS
    assert Counter(row["record_role"] for row in rows) == {
        "source_native_volume_average": 250,
        "derived_spatial_summary": 8_764,
    }
    assert all(tuple(row) == GOLD_C_VALUE_COLUMNS for row in rows)
    assert len({row["observation_id"] for row in rows}) == len(rows)
    assert len({row["source_record_id"] for row in rows}) == len(rows)
    assert {row["simulation_key"] for row in rows} == {SIMULATION_KEY}
    assert {row["global_structure_family_key"] for row in rows} == {
        "family_pufoam_generic_nco_oh_water_npentane"
    }
    assert {row["source_id"] for row in rows} == {SOURCE_ID}
    assert payload["simulation_key_count"] == 1
    assert payload["time_points_are_independent_systems"] is False

    time_tokens = {
        part.split("=", 1)[1]
        for row in rows
        for part in row["source_record_id"].split("|")
        if part.startswith("time_s=")
    }
    assert time_tokens == {str(value) for value in range(1, 51)}


def test_空间统计策略去重_单位_有限数值与空训练权重() -> None:
    rows = build_gold_c_rows()
    native = [row for row in rows if row["record_role"] == "source_native_volume_average"]
    derived = [row for row in rows if row["record_role"] == "derived_spatial_summary"]

    assert Counter(
        row["property_name"].removeprefix("pufoam_").rsplit("_", 2)[-1]
        for row in native
    ) == {"average": 250}
    assert audit()["materialization"]["aggregation_counts_derived"] == {
        "max": 2_138,
        "mean": 2_350,
        "min": 2_138,
        "population_std": 2_138,
    }

    duplicate_mean_properties = {
        "pufoam_alpha_gas_mean",
        "pufoam_mzero_mean",
        "pufoam_mone_mean",
        "pufoam_rho_foam_mean",
        "pufoam_rho_mean",
    }
    assert not duplicate_mean_properties.intersection(
        {row["property_name"] for row in derived}
    )
    assert {row["unit"] for row in derived} == {
        "dimensionless",
        "Pa*s",
        "K",
        "m^2/s",
        "kg/m^3",
        "s^2/m^2",
        "Pa",
    }
    materialization = audit()["materialization"]
    assert materialization["policy_tier_counts"] == EXPECTED_POLICY_TIER_COUNTS
    assert materialization["gold_admission_status_counts"] == {
        "admitted_reference": 4_293,
        "conditional_reference": 4_721,
    }
    assert materialization["potential_weight_ceiling_counts"] == {
        "0.10": 4_721,
        "0.20": 4_143,
        "0.30": 150,
    }
    assert materialization["unit_status_counts"] == {
        "resolved": 150,
        "resolved_from_openfoam_dimensions": 4_143,
        "source_declared_dimensions_semantics_unresolved": 4_621,
        "unresolved": 100,
    }
    assert materialization["source_validation_status_counts"] == {
        "derived_model_output_field_semantics_unresolved": 4_621,
        "derived_model_output_model_level_validation_only": 4_143,
        "model_level_validation_reported_field_semantics_unresolved": 100,
        "model_level_validation_reported_not_field_specific": 150,
    }

    native_unresolved = [
        row for row in rows if row["unit_status"] == "unresolved"
    ]
    assert len(native_unresolved) == 100
    assert {row["unit"] for row in native_unresolved} == {
        "source_native_unit_unresolved"
    }
    semantics_unresolved = [
        row
        for row in rows
        if row["unit_status"]
        == "source_declared_dimensions_semantics_unresolved"
    ]
    assert len(semantics_unresolved) == 4_621
    assert all(math.isfinite(float(row["value"])) for row in rows)
    assert all(row["canonical_structure"] == "" for row in rows)
    assert all(row["structure_identity_status"] == "process_system_identity_only" for row in rows)
    assert all(row["current_weight_materialized"] == "false" for row in rows)
    assert all(row["training_weight"] == "" for row in rows)
    assert all(
        row["property_admission_status"] == row["gold_admission_status"]
        for row in rows
    )
    assert {row["potential_weight_ceiling"] for row in rows} == {
        "0.10",
        "0.20",
        "0.30",
    }
    assert all("time_s=" in row["source_locator"] for row in rows)
    assert all("field=" in row["source_locator"] for row in rows)
    assert all("aggregation=" in row["source_locator"] for row in rows)


def test_字段级准入分层_代表字段精确命中() -> None:
    rows_by_id = {row["source_record_id"]: row for row in build_gold_c_rows()}

    def row_for(field: str, aggregation: str) -> dict[str, str]:
        source_record_id = (
            f"pufoam_2d_cup|time_s=1|field={field}|aggregation={aggregation}"
        )
        return rows_by_id[source_record_id]

    native_resolved = row_for("alpha.gas", "volume_average")
    assert {
        "unit": native_resolved["unit"],
        "unit_status": native_resolved["unit_status"],
        "fidelity_level": native_resolved["fidelity_level"],
        "gold_admission_status": native_resolved["gold_admission_status"],
        "source_validation_status": native_resolved["source_validation_status"],
        "potential_weight_ceiling": native_resolved["potential_weight_ceiling"],
    } == {
        "unit": "dimensionless",
        "unit_status": "resolved",
        "fidelity_level": "reactive_CFD_source_native_model_level_validated",
        "gold_admission_status": "admitted_reference",
        "source_validation_status": (
            "model_level_validation_reported_not_field_specific"
        ),
        "potential_weight_ceiling": "0.30",
    }

    native_unresolved = row_for("mZero", "volume_average")
    assert {
        "unit": native_unresolved["unit"],
        "unit_status": native_unresolved["unit_status"],
        "fidelity_level": native_unresolved["fidelity_level"],
        "gold_admission_status": native_unresolved["gold_admission_status"],
        "source_validation_status": native_unresolved["source_validation_status"],
        "potential_weight_ceiling": native_unresolved["potential_weight_ceiling"],
    } == {
        "unit": "source_native_unit_unresolved",
        "unit_status": "unresolved",
        "fidelity_level": "reactive_CFD_source_native_semantics_unresolved",
        "gold_admission_status": "conditional_reference",
        "source_validation_status": (
            "model_level_validation_reported_field_semantics_unresolved"
        ),
        "potential_weight_ceiling": "0.10",
    }

    derived_resolved = row_for("T", "mean")
    assert {
        "unit": derived_resolved["unit"],
        "unit_status": derived_resolved["unit_status"],
        "fidelity_level": derived_resolved["fidelity_level"],
        "gold_admission_status": derived_resolved["gold_admission_status"],
        "source_validation_status": derived_resolved["source_validation_status"],
        "potential_weight_ceiling": derived_resolved["potential_weight_ceiling"],
    } == {
        "unit": "K",
        "unit_status": "resolved_from_openfoam_dimensions",
        "fidelity_level": "reactive_CFD_derived_model_output",
        "gold_admission_status": "admitted_reference",
        "source_validation_status": (
            "derived_model_output_model_level_validation_only"
        ),
        "potential_weight_ceiling": "0.20",
    }

    for field in ("M0", "node0", "weight0", "Psi1", "thermalConductivity"):
        derived_unclosed = row_for(field, "mean")
        assert {
            "unit_status": derived_unclosed["unit_status"],
            "fidelity_level": derived_unclosed["fidelity_level"],
            "gold_admission_status": derived_unclosed["gold_admission_status"],
            "source_validation_status": derived_unclosed[
                "source_validation_status"
            ],
            "potential_weight_ceiling": derived_unclosed[
                "potential_weight_ceiling"
            ],
        } == {
            "unit_status": "source_declared_dimensions_semantics_unresolved",
            "fidelity_level": "reactive_CFD_derived_semantics_unresolved",
            "gold_admission_status": "conditional_reference",
            "source_validation_status": (
                "derived_model_output_field_semantics_unresolved"
            ),
            "potential_weight_ceiling": "0.10",
        }
    assert row_for("Psi1", "mean")["unit"] == "s^2/m^2"


def test_main_产物完整且可重复复算(tmp_path, monkeypatch) -> None:
    output_tsv = tmp_path / "PUFoam_Gold-C.tsv"
    output_audit = tmp_path / "PUFoam_审计.json"
    output_checksums = tmp_path / "PUFoam_校验和.tsv"
    output_readme = tmp_path / "README.md"
    monkeypatch.setattr(pufoam_module, "OUTPUT_TSV", output_tsv)
    monkeypatch.setattr(pufoam_module, "OUTPUT_AUDIT", output_audit)
    monkeypatch.setattr(pufoam_module, "OUTPUT_CHECKSUMS", output_checksums)
    monkeypatch.setattr(pufoam_module, "OUTPUT_README", output_readme)

    pufoam_module.main()
    outputs = (output_tsv, output_audit, output_checksums, output_readme)
    first_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in outputs}

    with output_tsv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert tuple(reader.fieldnames or ()) == GOLD_C_VALUE_COLUMNS
        rows = list(reader)
    assert len(rows) == EXPECTED_TOTAL_ROWS
    assert json.loads(output_audit.read_text(encoding="utf-8")) == audit()

    checksums = list(
        csv.DictReader(
            io.StringIO(output_checksums.read_text(encoding="utf-8")),
            delimiter="\t",
        )
    )
    assert {row["filename"] for row in checksums} == {
        "PUFoam.tar.gz",
        "官方元数据.json",
        "论文Crossref元数据.json",
    }
    assert next(row for row in checksums if row["filename"] == "PUFoam.tar.gz")["sha256"] == ARCHIVE_SHA256
    readme = output_readme.read_text(encoding="utf-8")
    assert "10.17632/62ggzx623g.1" in readme
    assert "10.1016/j.cpc.2017.03.010" in readme
    assert "9014" in readme or "9,014" in readme

    pufoam_module.main()
    second_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in outputs}
    assert first_hashes == second_hashes
