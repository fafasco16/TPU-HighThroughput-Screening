"""第九批 RadonPy PI1070 计算数据的定向回归测试。"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "代码" / "审计" / "第九批RadonPy_PI1070.py"
SOURCE = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第九批计算_RadonPy_PI1070"
)
CSV_PATH = SOURCE / "PI1070.csv"


def _require_payload() -> None:
    if not CSV_PATH.is_file():
        pytest.skip("RadonPy PI1070 固定原件未在当前检出中分发")


def _load_module():
    spec = importlib.util.spec_from_file_location("batch9_radonpy_pi1070", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _read_tsv(name: str) -> list[dict[str, str]]:
    with (SOURCE / name).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def test_frozen_commit_files_close_bytes_sha256_and_git_blobs() -> None:
    _require_payload()
    module = _load_module()
    payloads = module.read_frozen_files()
    module.validate_official_metadata(payloads)

    assert module.PINNED_COMMIT == "840dd4a2b5f261fc9370bb6786eff0b71a463d2f"
    assert module.PINNED_TREE == "b6d2a7074c7048a9a91554c9c4d3393de67571a7"
    assert module.FROZEN_FILES["PI1070.csv"].git_blob == (
        "109a773f3da95043cc7861aad3e4e67140865ace"
    )
    for name, specification in module.FROZEN_FILES.items():
        payload = payloads[name]
        assert len(payload) == specification.size
        assert hashlib.sha256(payload).hexdigest() == specification.sha256
        if specification.git_blob:
            assert module.git_blob_sha(payload) == specification.git_blob


def test_full_csv_is_1077_by_157_with_no_id_inflation() -> None:
    _require_payload()
    bundle = _load_module().audit()
    assert len(bundle.rows) == 1_077
    assert len(bundle.field_rows) == 157
    assert len({row["monomer_ID"] for row in bundle.rows}) == 1_077
    assert all(row["smiles"].count("*") == 2 for row in bundle.rows)
    assert Counter(row["polymer_class"] for row in bundle.rows)["11"] == 11
    assert sum(row["polymer_class"] != "11" for row in bundle.rows) == 1_066
    assert bundle.summary["dimensions"]["missing_cell_count"] == 431
    assert bundle.summary["dimensions"]["rows_with_any_missing_value"] == 10


def test_only_the_11_class_11_rows_are_admitted_as_pu_and_all_are_complete() -> None:
    _require_payload()
    module = _load_module()
    bundle = module.audit()
    assert len(bundle.pu_rows) == 11
    assert {row["monomer_ID"] for row in bundle.pu_rows} == module.PU_IDS
    assert all(row["polymer_class"] == "11" for row in bundle.pu_rows)
    assert all(
        all(row[field] != "" for field in module.EXPECTED_HEADERS)
        for row in bundle.pu_rows
    )

    materialized = _read_tsv("PU重复单元清单.tsv")
    assert len(materialized) == 11
    assert {row["monomer_ID"] for row in materialized} == module.PU_IDS
    assert all(row["gold_layer"] == "Gold-C" for row in materialized)
    assert all(row["admission_state"] == "admitted" for row in materialized)
    assert all(row["training_weight"] == "" for row in materialized)
    assert all(
        all(row[field] != "" for field in module.EXPECTED_HEADERS)
        for row in materialized
    )


def test_non_pu_rows_remain_conditional_transfer_references_not_fake_pu() -> None:
    _require_payload()
    rows = _read_tsv("全量行审计.tsv")
    assert len(rows) == 1_077
    admitted = [row for row in rows if row["admission_state"] == "admitted"]
    conditional = [
        row for row in rows if row["admission_state"] == "conditional_reference"
    ]
    assert len(admitted) == 11
    assert len(conditional) == 1_066
    assert all(row["polymer_class"] == "11" for row in admitted)
    assert all(row["polymer_class"] != "11" for row in conditional)
    assert all(row["material_scope"] == "polyurethane_repeat_unit" for row in admitted)
    assert all(row["material_scope"] == "non_PU_general_polymer" for row in conditional)
    assert all(row["direct_tpu_target_candidate"] == "false" for row in rows)
    assert all(row["training_weight"] == "" for row in rows)


def test_pu_observations_preserve_dft_md_units_and_correlated_grouping() -> None:
    _require_payload()
    rows = _read_tsv("PU计算观测清单.tsv")
    assert len(rows) == 440
    assert len({row["observation_id"] for row in rows}) == 440
    assert Counter(row["method_family"] for row in rows) == {
        "DFT": 154,
        "MD": 165,
        "NEMD": 110,
        "DFT+MD derived": 11,
    }
    assert sum(row["target_candidate"] == "true" for row in rows) == 198
    assert all(row["gold_layer"] == "Gold-C" for row in rows)
    assert all(row["admission_state"] == "admitted" for row in rows)
    assert all(row["unit"] for row in rows)
    assert all(row["leakage_group"] == row["split_group"] for row in rows)
    assert all(row["independent_material_increment"] == "0" for row in rows)
    assert all(row["training_weight"] == "" for row in rows)
    assert all(row["temperature_K"] == "300" for row in rows)
    assert all(row["pressure_atm"] == "1" for row in rows)

    dft = [row for row in rows if row["method_family"] == "DFT"]
    md = [row for row in rows if row["method_family"] != "DFT"]
    assert all(row["replicate_count"] == "" for row in dft)
    assert all(int(row["replicate_count"]) > 0 for row in md)


def test_field_coverage_reports_all_157_columns_and_zero_pu_missing() -> None:
    _require_payload()
    rows = _read_tsv("字段审计清单.tsv")
    assert len(rows) == 157
    assert [row["field_name"] for row in rows] == list(_load_module().EXPECTED_HEADERS)
    assert all(row["pu_present_count"] == "11" for row in rows)
    assert all(row["pu_missing_count"] == "0" for row in rows)
    units = {row["field_name"]: row["unit"] for row in rows}
    assert units["density"] == "g/cm^3"
    assert units["thermal_conductivity"] == "W/(m*K)"
    assert units["bulk_modulus"] == "Pa"
    assert units["qm_homo_monomer"] == "eV"


def test_exact_smiles_leakage_groups_are_case_sensitive_and_currently_unique() -> None:
    _require_payload()
    rows = _read_tsv("全量行审计.tsv")
    assert len({row["split_group"] for row in rows}) == 1_077
    assert all(row["exact_smiles_duplicate_count"] == "1" for row in rows)
    assert _read_tsv("重复泄漏组.tsv") == []
    assert all(row["independent_material_increment"] == "1" for row in rows)


def test_outputs_are_byte_reproducible_atomic_and_do_not_materialize_training() -> None:
    _require_payload()
    module = _load_module()
    bundle = module.audit()
    first = module.render_outputs(bundle)
    second = module.render_outputs(module.audit())
    assert first == second
    assert set(first) == set(module.OUTPUT_NAMES)
    assert json.loads(first["内容审计摘要.json"].decode("utf-8"))[
        "training_weight_materialized"
    ] is False
    assert json.loads(first["内容审计摘要.json"].decode("utf-8"))[
        "direct_training_materialization"
    ] is False
    for name, payload in first.items():
        assert (SOURCE / name).read_bytes() == payload

    before = {
        name: hashlib.sha256((SOURCE / name).read_bytes()).hexdigest()
        for name in module.OUTPUT_NAMES
    }
    subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, check=True)
    after = {
        name: hashlib.sha256((SOURCE / name).read_bytes()).hexdigest()
        for name in module.OUTPUT_NAMES
    }
    assert before == after
    script_text = SCRIPT.read_text(encoding="utf-8")
    assert "os.replace" in script_text
    assert ".write_text(" not in script_text
    assert "训练集" in script_text


def test_atomic_replace_failure_keeps_old_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    target = tmp_path / "来源元数据.json"
    target.write_bytes(b"old")
    monkeypatch.setattr(module, "SOURCE_DIR", tmp_path)
    monkeypatch.setattr(module, "OUTPUT_PATHS", frozenset({target}))

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic replace failure"):
        module.atomic_write(target, b"new")
    assert target.read_bytes() == b"old"
    assert list(tmp_path.glob("*.audit.tmp")) == []
