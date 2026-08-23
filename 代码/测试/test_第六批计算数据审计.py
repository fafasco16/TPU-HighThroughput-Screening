from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "数据/原始" / "外部数据" / "新增开放数据"
SCRIPT = ROOT / "代码" / "审计" / "第六批计算数据审计.py"
SOURCE_NAMES = (
    "MDPI_HDI_PEG双力场TPU",
    "MDPI_MDI聚醚双组分PU分子动力学",
    "Frontiers_PU_ReaxFF热解",
    "Figshare_商用PUR形状记忆本构FEA",
)


def _load_auditor():
    spec = importlib.util.spec_from_file_location("sixth_batch_data_auditor", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _require_payloads() -> None:
    required = (
        RAW / SOURCE_NAMES[0] / "molecules-31-01259-s001.zip",
        RAW / SOURCE_NAMES[1] / "PMC全文.xml",
        RAW / SOURCE_NAMES[2] / "Data Sheet 1.docx",
        RAW / SOURCE_NAMES[3] / "Simulation Data_PECCII 2026.docx",
    )
    if not all(path.is_file() for path in required):
        pytest.skip("第六批原始计算来源未在当前检出中分发")


def _tsv(payload: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(payload.decode("utf-8")), delimiter="\t"))


def test_frozen_files_have_exact_size_and_sha256() -> None:
    _require_payloads()
    module = _load_auditor()
    for source, specifications in module.FROZEN_FILES.items():
        for filename, (expected_size, expected_sha256, _role) in specifications.items():
            path = RAW / source / filename
            payload = path.read_bytes()
            assert len(payload) == expected_size
            assert hashlib.sha256(payload).hexdigest() == expected_sha256

    supplement = RAW / SOURCE_NAMES[0] / "molecules-31-01259-s001.zip"
    with zipfile.ZipFile(supplement) as archive:
        assert archive.namelist() == ["molecules-4226108-supplementary.pdf"]
        pdf = archive.read(archive.namelist()[0])
    assert len(pdf) == 2_854_059
    assert hashlib.sha256(pdf).hexdigest() == (
        "1b4354451a429f3e18b065cfccb6d2d9461fbd70a0d33c971ab11fd6a00a076e"
    )


def test_hdi_peg_separates_experimental_gold_e_from_md_gold_c() -> None:
    _require_payloads()
    bundle = _load_auditor().audit_hdi_peg()
    summary = bundle.summary

    assert summary["canonical_identifier"] == "doi:10.3390/molecules31081259"
    assert summary["formulation_or_series_identity_count"] == 5
    assert summary["experimental_formulation_count"] == 3
    assert summary["computational_system_count"] == 5
    assert summary["system_force_field_branch_count"] == 10
    assert summary["reported_independent_seed_run_count"] == 100
    assert summary["observation_record_count"] == len(bundle.observations) == 26
    assert summary["numeric_value_count"] == 52

    md = [row for row in bundle.observations if row["target_origin"] == "md"]
    experimental = [
        row for row in bundle.observations if row["target_origin"] == "experimental"
    ]
    assert len(md) == 20
    assert len(experimental) == 6
    assert all(row["origin_kind"] == "md" and row["gold_layer"] == "Gold-C" for row in md)
    assert all(
        row["origin_kind"] == "experimental" and row["gold_layer"] == "Gold-E"
        for row in experimental
    )
    assert all(row["reduction_level"] == "aggregate" for row in bundle.observations)
    assert all(row["independent_sample_increment"] == "0" for row in bundle.observations)
    assert all(row["training_weight"] == "" for row in bundle.observations)

    mapped = {"PEG-H400", "PEG-H1000", "PEG-H1500"}
    for row in md:
        ceiling = float(row["future_weight_ceiling"])
        if row["property_name"] == "glass_transition_temperature":
            assert ceiling <= (0.40 if row["system_id"] in mapped else 0.20)
        else:
            assert row["property_name"] == "elastic_modulus"
            assert ceiling <= 0.20
    assert all(float(row["future_weight_ceiling"]) == 0.65 for row in experimental)


def test_mdi_polyether_keeps_all_values_but_hard_zeros_bad_or_duplicate_targets() -> None:
    _require_payloads()
    bundle = _load_auditor().audit_mdi_polyether()
    summary = bundle.summary

    assert summary["canonical_identifier"] == "doi:10.3390/ma16031006"
    assert summary["formulation_count"] == 3
    assert summary["computational_system_count"] == 4
    assert summary["mixture_formulation_system_count"] == 3
    assert summary["reference_component_system_count"] == 1
    assert summary["ratio_temperature_condition_count"] == 12
    assert summary["reference_component_temperature_condition_count"] == 4
    assert summary["reported_independent_seed_run_count"] is None
    assert summary["observation_record_count"] == len(bundle.observations) == 120
    assert summary["candidate_observation_record_count"] == 79
    assert summary["numeric_value_count"] == 136
    assert summary["fit_quality_numeric_count"] == 16
    assert all(row["target_origin"] == "md" for row in bundle.observations)
    assert all(row["gold_layer"] == "Gold-C" for row in bundle.observations)
    system_ids = {row["system_id"] for row in bundle.systems}
    assert system_ids == {"PB1", "PB2", "PB3", "polyol_reference"}
    assert {row["system_id"] for row in bundle.observations} <= system_ids

    candidates = [row for row in bundle.observations if row["target_candidate"] == "true"]
    assert len(candidates) == 79
    assert max(float(row["future_weight_ceiling"]) for row in candidates) <= 0.20
    energy = [
        row
        for row in bundle.observations
        if row["decision"] == "reference_only_intermediate_energy"
    ]
    duplicates = [
        row
        for row in bundle.observations
        if row["decision"] == "derived_duplicate_of_lame_mu"
    ]
    bad_fit = [
        row
        for row in bundle.observations
        if row["decision"] == "hard_zero_low_fit_quality"
    ]
    assert len(energy) == 28
    assert len(duplicates) == 12
    assert len(bad_fit) == 1
    assert bad_fit[0]["record_id"] == "mdi_t5_333_B_(0.6)"
    assert bad_fit[0]["quality_evidence"] == "R2=0.1338"
    assert all(row["future_weight_ceiling"] == "0.00" for row in energy + duplicates + bad_fit)


def test_reaxff_is_one_derived_target_not_six_materials() -> None:
    _require_payloads()
    bundle = _load_auditor().audit_reaxff()
    summary = bundle.summary

    assert summary["canonical_identifier"] == "doi:10.3389/fchem.2025.1691308"
    assert summary["computational_system_count"] == 1
    assert summary["temperature_condition_count"] == 6
    assert summary["observation_record_count"] == len(bundle.observations) == 2
    assert summary["candidate_observation_record_count"] == 1
    assert {row["target_origin"] for row in bundle.observations} == {"md"}
    assert {row["gold_layer"] for row in bundle.observations} == {"Gold-C"}
    activation = next(
        row
        for row in bundle.observations
        if row["property_name"] == "pyrolysis_activation_energy"
    )
    quality = next(
        row
        for row in bundle.observations
        if row["property_name"] == "linear_correlation_coefficient"
    )
    assert (activation["value"], activation["unit"]) == ("136.35", "kJ/mol")
    assert activation["future_weight_ceiling"] == "0.20"
    assert activation["target_candidate"] == "true"
    assert quality["value"] == "0.99"
    assert quality["target_candidate"] == "false"
    assert quality["future_weight_ceiling"] == "0.00"


def test_figshare_fea_is_gold_c_simulation_input_not_a_performance_target() -> None:
    _require_payloads()
    bundle = _load_auditor().audit_fea()
    summary = bundle.summary

    assert summary["canonical_identifier"] == "doi:10.6084/m9.figshare.31111210.v3"
    assert summary["material_identity_count"] == 4
    assert summary["finite_element_run_count"] == 0
    assert summary["prony_term_record_count"] == 40
    assert summary["input_parameter_numeric_token_count"] == len(bundle.inputs) == 120
    assert summary["complete_input_parameter_count"] == 112
    assert summary["incomplete_cte_parameter_count"] == 8
    assert summary["observation_record_count"] == len(bundle.observations) == 0
    assert summary["numeric_target_value_count"] == 0
    assert all(row["target_origin"] == "simulation_input" for row in bundle.inputs)
    assert all(row["gold_layer"] == "Gold-C" for row in bundle.inputs)
    assert all(row["target_candidate"] == "false" for row in bundle.inputs)
    assert all(row["future_weight_ceiling"] == "0.00" for row in bundle.inputs)
    assert all(row["training_weight"] == "" for row in bundle.inputs)
    assert sum(row["parameter_group"] == "prony_shear" for row in bundle.inputs) == 80
    assert sum(row["completeness"] != "complete" for row in bundle.inputs) == 8


def test_rendered_outputs_are_deterministic_and_match_materialized_audit_files() -> None:
    _require_payloads()
    module = _load_auditor()
    expected_names = set(module.OUTPUT_NAMES)
    for auditor in module.AUDITORS:
        bundle = auditor()
        first = module.render_outputs(bundle)
        second = module.render_outputs(auditor())
        assert first == second
        assert set(first) == expected_names
        assert json.loads(first["内容审计摘要.json"].decode("utf-8"))[
            "training_weight_materialized"
        ] is False
        observation_rows = _tsv(first["计算观测清单.tsv"])
        if observation_rows:
            assert all(row["target_origin"] and row["gold_layer"] for row in observation_rows)
        observation_header = first["计算观测清单.tsv"].splitlines()[0].decode("utf-8")
        assert "target_origin" in observation_header.split("\t")
        assert "gold_layer" in observation_header.split("\t")
        for filename, payload in first.items():
            assert (RAW / bundle.source_directory / filename).read_bytes() == payload


def test_archive_guard_rejects_path_traversal(tmp_path: Path) -> None:
    module = _load_auditor()
    malicious = tmp_path / "malicious.zip"
    with zipfile.ZipFile(malicious, "w") as archive:
        archive.writestr("../escape.txt", b"blocked")
    with pytest.raises(module.AuditBlocked, match="ZIP成员路径不安全"):
        module._archive_summary(malicious)
