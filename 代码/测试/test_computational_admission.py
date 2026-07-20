import csv
from pathlib import Path

import pytest

from computational_admission import (
    ComputationalAdmissionError,
    ComputationalAdmissionProfile,
    ExactStructureOverlapProfile,
    profile_adept_candidates,
    profile_dq_matimpute,
    profile_exact_structure_overlaps,
    profile_polygraphmt,
    profile_polyomics,
    profile_structure_candidates,
    render_computational_admission_markdown,
)


def _write_csv(path: Path, columns: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(rows)


_POLYOMICS_META_COLUMNS = [
    "monomer_ID",
    "copoly_ratio_list",
    "copoly_type",
    "temp",
    "press",
    "tacticity",
    "qm_method",
    "forcefield",
    "class_PURT",
    "check_eq",
    "check_tc",
    "do_TC",
    "remarks",
    "smiles_1",
    "smiles_2",
    "smiles_3",
    "smiles_4",
]


def _polyomics_columns(*qoi: str) -> list[str]:
    return ["UUID", "smiles_list", *qoi, *_POLYOMICS_META_COLUMNS]


def _polyomics_row(
    uid: str,
    smiles: str,
    *qoi_values: str,
    purt: bool = False,
) -> list[str]:
    return [
        uid,
        smiles,
        *qoi_values,
        f"monomer-{smiles}",
        "1",
        "",
        "300",
        "1",
        "atactic",
        "B3LYP",
        "GAFF",
        "True" if purt else "False",
        "True",
        "True",
        "True",
        "",
        smiles,
        "",
        "",
        "",
    ]


def test_polygraphmt_profiles_fidelity_uniques_duplicates_and_conflicts(tmp_path):
    _write_csv(
        tmp_path / "A_DFT.csv",
        ["SMILES", "A"],
        [["[*]CC[*]", "1"], ["[*]CC[*]", "2"], ["[*]CO[*]", "3"]],
    )
    _write_csv(
        tmp_path / "B_MD.csv",
        ["SMILES", "B"],
        [["[*]CC[*]", "4"], ["[*]CN[*]", "5"], ["[*]CN[*]", "5"]],
    )
    _write_csv(tmp_path / "C_GC.csv", ["SMILES", "C"], [["[*]CS[*]", "6"]])

    profile = profile_polygraphmt(tmp_path)

    assert profile.source_key == "polygraphmt"
    assert profile.file_count == 3
    assert profile.source_record_candidate_count == 7
    assert profile.unique_system_candidate_count == 4
    assert profile.computational_activity_candidate_count is None
    assert profile.computational_observation_candidate_count == 7
    assert profile.admitted_observation_count == 0
    assert profile.fidelity_counts == {"dft": 3, "gc": 1, "md": 3}
    assert profile.diagnostics["duplicate_key_group_count"] == 2
    assert profile.diagnostics["duplicate_extra_row_count"] == 2
    assert profile.diagnostics["conflicting_target_group_count"] == 1


@pytest.mark.parametrize("sentinel", ["nan", "NA", "n/a", "None", "null"])
def test_polygraphmt_quarantines_missing_identity_sentinels(tmp_path, sentinel):
    _write_csv(
        tmp_path / "A_MD.csv",
        ["SMILES", "A"],
        [["C", "1"], [sentinel, "2"]],
    )
    profile = profile_polygraphmt(tmp_path)
    assert profile.source_record_candidate_count == 2
    assert profile.computational_observation_candidate_count == 1
    assert profile.unique_system_candidate_count == 1
    assert profile.fidelity_counts == {"md": 1}
    assert profile.diagnostics["invalid_identity_row_count"] == 1


@pytest.mark.parametrize(
    ("bad_value", "diagnostic"),
    [("not-a-number", "invalid_numeric_row_count"), ("nan", "nonfinite_numeric_row_count"), ("inf", "nonfinite_numeric_row_count")],
)
def test_polygraphmt_quarantines_invalid_property_numbers(tmp_path, bad_value, diagnostic):
    _write_csv(
        tmp_path / "A_DFT.csv",
        ["SMILES", "A"],
        [["C", "1"], ["N", bad_value]],
    )
    profile = profile_polygraphmt(tmp_path)
    assert profile.source_record_candidate_count == 2
    assert profile.computational_observation_candidate_count == 1
    assert profile.diagnostics[diagnostic] == 1


def test_structured_error_and_profile_invariants():
    assert ComputationalAdmissionError("x", "bad").as_dict() == {
        "code": "x",
        "message": "bad",
    }
    assert ComputationalAdmissionError("x", "bad", path="p").as_dict()["path"] == "p"

    base = dict(
        source_key="x",
        evidence_class="candidate",
        file_count=1,
        source_record_candidate_count=1,
        unique_system_candidate_count=1,
        computational_activity_candidate_count=0,
        computational_observation_candidate_count=0,
    )
    with pytest.raises(ComputationalAdmissionError) as blank:
        ComputationalAdmissionProfile(**{**base, "source_key": ""})
    assert blank.value.code == "invalid_profile"
    with pytest.raises(ComputationalAdmissionError) as negative:
        ComputationalAdmissionProfile(**{**base, "file_count": -1})
    assert negative.value.code == "invalid_profile"
    with pytest.raises(ComputationalAdmissionError) as fractional:
        ComputationalAdmissionProfile(**{**base, "file_count": 1.5})
    assert fractional.value.code == "invalid_profile"
    with pytest.raises(ComputationalAdmissionError) as fidelity:
        ComputationalAdmissionProfile(**base, fidelity_counts={"md": True})
    assert fidelity.value.code == "invalid_profile"
    with pytest.raises(ComputationalAdmissionError) as admitted:
        ComputationalAdmissionProfile(**base, admitted_observation_count=1)
    assert admitted.value.code == "premature_admission"


@pytest.mark.parametrize(
    ("filename", "columns", "rows", "code"),
    [
        ("bad.csv", ["SMILES", "A"], [["x", "1"]], "unknown_fidelity"),
        ("A_DFT.csv", ["wrong", "A"], [["x", "1"]], "invalid_columns"),
        ("A_DFT.csv", ["SMILES", "A"], [["", "1"]], "missing_identity"),
        ("A_DFT.csv", ["SMILES", "A"], [["x", ""]], "missing_value"),
    ],
)
def test_polygraphmt_fails_closed_for_unusable_tables(
    tmp_path, filename, columns, rows, code
):
    _write_csv(tmp_path / filename, columns, rows)
    with pytest.raises(ComputationalAdmissionError) as failure:
        profile_polygraphmt(tmp_path)
    assert failure.value.code == code


def test_polygraphmt_rejects_empty_directory_and_malformed_rows(tmp_path):
    with pytest.raises(ComputationalAdmissionError) as empty:
        profile_polygraphmt(tmp_path)
    assert empty.value.code == "no_candidate_files"

    path = tmp_path / "A_DFT.csv"
    path.write_text("SMILES,A\nx,1,extra\n", encoding="utf-8")
    with pytest.raises(ComputationalAdmissionError) as width:
        profile_polygraphmt(tmp_path)
    assert width.value.code == "invalid_row_width"

    path.write_bytes(b"SMILES,A\nx,\xff\n")
    with pytest.raises(ComputationalAdmissionError) as decode:
        profile_polygraphmt(tmp_path)
    assert decode.value.code == "file_decode_failed"

    path.write_bytes(b"SMILES,A\n" + (b"x,1\n" * 3000) + b"x,\xff\n")
    with pytest.raises(ComputationalAdmissionError) as late_decode:
        profile_polygraphmt(tmp_path)
    assert late_decode.value.code == "file_decode_failed"


def test_polygraphmt_smiles_identity_is_case_sensitive(tmp_path):
    _write_csv(
        tmp_path / "A_DFT.csv",
        ["SMILES", "A"],
        [["*C1CCCCC1*", "1"], ["*c1ccccc1*", "2"]],
    )
    profile = profile_polygraphmt(tmp_path)
    assert profile.unique_system_candidate_count == 2
    assert profile.diagnostics["duplicate_key_group_count"] == 0


def test_candidate_profiler_wraps_missing_file(tmp_path):
    with pytest.raises(ComputationalAdmissionError) as missing:
        profile_structure_candidates(
            tmp_path / "missing.csv",
            source_key="x",
            identity_column="SMILES",
            evidence_class="virtual_candidate",
        )
    assert missing.value.code == "file_unreadable"


def test_candidate_profiler_rejects_duplicate_headers(tmp_path):
    path = tmp_path / "duplicate.csv"
    path.write_text("SMILES,SMILES\nC,C\n", encoding="utf-8")
    with pytest.raises(ComputationalAdmissionError) as caught:
        profile_structure_candidates(
            path,
            source_key="x",
            identity_column="SMILES",
            evidence_class="virtual_candidate",
        )
    assert caught.value.code == "duplicate_columns"


def test_polyomics_excludes_purt_subset_from_parent_counts(tmp_path):
    general = tmp_path / "general.csv"
    purt = tmp_path / "purt.csv"
    columns = _polyomics_columns("density", "tg")
    _write_csv(
        general,
        columns,
        [
            _polyomics_row("u1", "A", "1.0", ""),
            _polyomics_row("u2", "B", "1.1", "300", purt=True),
            _polyomics_row("u3", "B", "", "301"),
        ],
    )
    _write_csv(purt, columns, [_polyomics_row("u2", "B", "1.1", "300", purt=True)])

    profile = profile_polyomics(general, purt, qoi_columns=("density", "tg"))

    assert profile.file_count == 2
    assert profile.source_record_candidate_count == 3
    assert profile.unique_system_candidate_count == 2
    assert profile.computational_activity_candidate_count is None
    assert profile.diagnostics["unique_source_record_uuid_count"] == 3
    assert profile.computational_observation_candidate_count == 4
    assert profile.diagnostics["purt_subset_rows"] == 1
    assert profile.diagnostics["purt_uuid_not_in_general"] == 0
    assert profile.diagnostics["purt_raw_content_mismatch_count"] == 0
    assert profile.admitted_observation_count == 0


def test_polyomics_rejects_missing_columns_duplicate_uuid_and_non_subset(tmp_path):
    general = tmp_path / "general.csv"
    purt = tmp_path / "purt.csv"
    _write_csv(general, ["UUID", "smiles_list", "density"], [["u1", "A", "1"]])
    _write_csv(purt, ["UUID", "smiles_list", "density"], [["u2", "B", "1"]])
    with pytest.raises(ComputationalAdmissionError) as subset:
        profile_polyomics(general, purt, qoi_columns=("density",))
    assert subset.value.code == "subset_not_contained"

    _write_csv(
        general,
        ["UUID", "smiles_list", "density"],
        [["u1", "A", "1"], ["u1", "A", "1"]],
    )
    _write_csv(purt, ["UUID", "smiles_list", "density"], [["u1", "A", "1"]])
    with pytest.raises(ComputationalAdmissionError) as duplicate:
        profile_polyomics(general, purt, qoi_columns=("density",))
    assert duplicate.value.code == "duplicate_source_record_id"

    _write_csv(general, ["UUID", "smiles_list"], [["u1", "A"]])
    with pytest.raises(ComputationalAdmissionError) as missing:
        profile_polyomics(general, purt, qoi_columns=("density",))
    assert missing.value.code == "invalid_columns"

    _write_csv(general, ["UUID", "smiles_list", "density"], [["", "A", "1"]])
    with pytest.raises(ComputationalAdmissionError) as blank:
        profile_polyomics(general, purt, qoi_columns=("density",))
    assert blank.value.code == "missing_identity"

    with pytest.raises(ComputationalAdmissionError) as empty_qoi:
        profile_polyomics(general, purt, qoi_columns=())
    assert empty_qoi.value.code == "invalid_qoi_catalog"
    with pytest.raises(ComputationalAdmissionError) as duplicate_qoi:
        profile_polyomics(general, purt, qoi_columns=("density", "density"))
    assert duplicate_qoi.value.code == "invalid_qoi_catalog"


def test_polyomics_requires_subset_schema_and_row_content_identity(tmp_path):
    general = tmp_path / "general.csv"
    purt = tmp_path / "purt.csv"
    columns = _polyomics_columns("density")
    _write_csv(general, columns, [_polyomics_row("u1", "A", "1", purt=True)])
    _write_csv(purt, ["UUID", "smiles_list"], [["u1", "A"]])
    with pytest.raises(ComputationalAdmissionError) as schema:
        profile_polyomics(general, purt, qoi_columns=("density",))
    assert schema.value.code == "subset_schema_mismatch"

    _write_csv(purt, columns, [_polyomics_row("u1", "A", "2", purt=True)])
    with pytest.raises(ComputationalAdmissionError) as content:
        profile_polyomics(general, purt, qoi_columns=("density",))
    assert content.value.code == "subset_content_mismatch"

    _write_csv(
        general,
        columns,
        [_polyomics_row("u1", "A", "20.730759206436996", purt=True)],
    )
    _write_csv(
        purt,
        columns,
        [_polyomics_row("u1", "A", "20.730759206437", purt=True)],
    )
    profile = profile_polyomics(general, purt, qoi_columns=("density",))
    assert profile.diagnostics["purt_raw_content_mismatch_count"] == 1
    assert profile.diagnostics["purt_numeric_format_equivalent_count"] == 1
    assert profile.diagnostics["purt_material_content_mismatch_count"] == 0


@pytest.mark.parametrize("bad_value", ["not-a-number", "nan", "inf", "-inf"])
def test_polyomics_rejects_non_numeric_or_nonfinite_qoi(tmp_path, bad_value):
    general = tmp_path / "general.csv"
    purt = tmp_path / "purt.csv"
    _write_csv(
        general,
        _polyomics_columns("density"),
        [_polyomics_row("u1", "A", bad_value, purt=True)],
    )
    _write_csv(
        purt,
        _polyomics_columns("density"),
        [_polyomics_row("u1", "A", bad_value, purt=True)],
    )
    with pytest.raises(ComputationalAdmissionError) as caught:
        profile_polyomics(general, purt, qoi_columns=("density",))
    assert caught.value.code == "invalid_qoi_value"


def test_structure_and_adept_candidates_never_become_admitted_observations(tmp_path):
    structures = tmp_path / "pi1m.csv"
    _write_csv(
        structures,
        ["SMILES", "SA Score"],
        [["A", "1"], ["A", "2"], ["B", "3"]],
    )
    profile = profile_structure_candidates(
        structures,
        source_key="pi1m",
        identity_column="SMILES",
        evidence_class="virtual_candidate",
    )
    assert profile.source_record_candidate_count == 3
    assert profile.unique_system_candidate_count == 2
    assert profile.computational_observation_candidate_count == 0
    assert profile.admitted_observation_count == 0

    adept = tmp_path / "SMILES.csv"
    _write_csv(adept, ["PID", "SMILES"], [["p1", "A"], ["p2", "A"], ["p3", "B"]])
    adept_profile = profile_adept_candidates(adept, simulation_input_file_count=11)
    assert adept_profile.source_record_candidate_count == 3
    assert adept_profile.unique_system_candidate_count == 2
    assert adept_profile.computational_activity_candidate_count == 0
    assert adept_profile.computational_observation_candidate_count == 0
    assert adept_profile.diagnostics["simulation_input_file_count"] == 11


def test_structure_candidates_distinguish_record_identity_from_exact_system(tmp_path):
    structures = tmp_path / "smipoly.csv"
    _write_csv(
        structures,
        ["comID", "SMILES"],
        [["m1", "C"], ["m2", "C"], ["m3", "c"]],
    )
    profile = profile_structure_candidates(
        structures,
        source_key="smipoly",
        identity_column="comID",
        system_column="SMILES",
        require_unique_identity=True,
        evidence_class="monomer_rules",
    )
    assert profile.source_record_candidate_count == 3
    assert profile.unique_system_candidate_count == 2
    assert profile.diagnostics["unique_record_identity_count"] == 3
    assert profile.diagnostics["duplicate_system_group_count"] == 1
    assert profile.diagnostics["duplicate_system_rows"] == 1

    _write_csv(structures, ["comID", "SMILES"], [["m1", "C"], ["m1", "N"]])
    with pytest.raises(ComputationalAdmissionError) as duplicate:
        profile_structure_candidates(
            structures,
            source_key="smipoly",
            identity_column="comID",
            system_column="SMILES",
            require_unique_identity=True,
            evidence_class="monomer_rules",
        )
    assert duplicate.value.code == "duplicate_record_identity"


def test_structure_candidates_reject_missing_or_blank_identity(tmp_path):
    path = tmp_path / "x.csv"
    _write_csv(path, ["wrong"], [["A"]])
    with pytest.raises(ComputationalAdmissionError) as missing:
        profile_structure_candidates(
            path,
            source_key="x",
            identity_column="SMILES",
            evidence_class="virtual_candidate",
        )
    assert missing.value.code == "invalid_columns"

    _write_csv(path, ["SMILES"], [[""]])
    with pytest.raises(ComputationalAdmissionError) as blank:
        profile_structure_candidates(
            path,
            source_key="x",
            identity_column="SMILES",
            evidence_class="virtual_candidate",
        )
    assert blank.value.code == "missing_identity"


def test_adept_rejects_invalid_count_blank_identity_and_duplicate_pid(tmp_path):
    path = tmp_path / "SMILES.csv"
    _write_csv(path, ["PID", "SMILES"], [["p1", "A"]])
    with pytest.raises(ComputationalAdmissionError) as count:
        profile_adept_candidates(path, simulation_input_file_count=-1)
    assert count.value.code == "invalid_input_count"

    with pytest.raises(ComputationalAdmissionError) as fractional:
        profile_adept_candidates(path, simulation_input_file_count=1.5)
    assert fractional.value.code == "invalid_input_count"

    _write_csv(path, ["PID", "SMILES"], [["", "A"]])
    with pytest.raises(ComputationalAdmissionError) as blank:
        profile_adept_candidates(path, simulation_input_file_count=0)
    assert blank.value.code == "missing_identity"

    _write_csv(path, ["PID", "SMILES"], [["p1", "A"], ["p1", "B"]])
    with pytest.raises(ComputationalAdmissionError) as duplicate:
        profile_adept_candidates(path, simulation_input_file_count=0)
    assert duplicate.value.code == "duplicate_source_record_id"


_MISSING_RATIO_TOKENS = (
    "0.1",
    "0.2",
    "0.30000000000000004",
    "0.4",
    "0.5",
    "0.6000000000000001",
    "0.7000000000000001",
    "0.8",
    "0.9",
)
_FILL_METHODS = (
    "fill_with_dt",
    "fill_with_et",
    "fill_with_gbr",
    "fill_with_lgb",
    "fill_with_rf",
    "fill_with_ridge",
    "fill_with_xgb",
)


def _pue_content_fixture(tmp_path: Path) -> dict[str, object]:
    import numpy as np
    from openpyxl import Workbook

    columns = ["SSID", "logEB", "logYM", "logTS"]
    parent_rows = [
        [f"id-{index}", str(index + 0.1), str(index + 0.2), str(index + 0.3)]
        for index in range(10)
    ]
    dq = tmp_path / "dq_parent.csv"
    mat = tmp_path / "mat_parent.csv"
    _write_csv(dq, columns, parent_rows)
    mat.write_bytes(dq.read_bytes())

    projection_ts = tmp_path / "projection_ts.csv"
    projection_ym = tmp_path / "projection_ym.csv"
    _write_csv(projection_ts, ["logTS"], [[row[3]] for row in parent_rows])
    _write_csv(projection_ym, ["logYM"], [[row[2]] for row in parent_rows])

    variants = tmp_path / "variants"
    for target_index, target in enumerate(columns[1:], start=1):
        for token in _MISSING_RATIO_TOKENS:
            rows = [list(row) for row in parent_rows]
            for row in rows[: round(len(rows) * float(token))]:
                row[target_index] = ""
            _write_csv(variants / f"{target}_{token}.csv", columns, rows)

    experiment = tmp_path / "experiment"
    filled_directory = experiment / "dataset" / "filled_results"
    experiment.mkdir(parents=True, exist_ok=True)
    centers = np.arange(3.0, 166.0, 6.0, dtype=np.float64)
    rdf = np.vstack([np.ones((3, 28), dtype=np.float64), centers])
    ratio_npy = experiment / "ratio.npy"
    type_npy = experiment / "type.npy"
    np.save(ratio_npy, rdf)
    np.save(type_npy, rdf)

    filled = filled_directory / "PUE.csv"
    _write_csv(
        filled,
        ["dataset", "column", "miss_ratio", "method", "RMSE", "Wasserstein", "time"],
        [
            ["PUE", column, token, method, "1", "2", "3"]
            for column in columns[1:]
            for token in _MISSING_RATIO_TOKENS
            for method in _FILL_METHODS
        ],
    )
    et_header = [
        "Scenario",
        "miss_pct [0, 1]",
        "Evaluated: et",
        "mean",
        "hyperimpute",
        "missforest",
        "gain",
        "sinkhorn",
    ]
    et_rows = [
        [scenario, str(ratio), *("1 +/- 0.1" for _ in range(6))]
        for scenario in ("MAR", "MCAR", "MNAR")
        for ratio in (0.1, 0.2, 0.3, 0.4, 0.5)
    ]
    distance = experiment / "distance.csv"
    rmse = experiment / "rmse.csv"
    _write_csv(distance, et_header, et_rows)
    _write_csv(rmse, et_header, et_rows)

    workbook_path = experiment / "rmse.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(["RMSE", "Method", "Range", "Col"])
    workbook_methods = ("Gain", "HyperImpute", "MatImpute", "Mean", "MissForest", "Sinkhorn")
    workbook_ranges = ("≤1σ", "1~2σ", ">2σ")
    workbook_columns = (
        "Form_Method",
        "ZS_CHS",
        "ZS_HS_BertzCT",
        "ZS_SS_PEOE_VSA8",
        "ZS_SS_TPSA_norm",
        "ZS_SS_VSA_EState8",
        "ZS_log_FCVm",
        "ZS_log_Fchi",
        "ZS_log_StrainRate",
        "ZS_log_Tr2K",
    )
    index = 0
    for method in workbook_methods:
        for value_range in workbook_ranges:
            for column in workbook_columns:
                sheet.append([None if index < 18 else 1.0, method, value_range, column])
                index += 1
    workbook.save(workbook_path)
    workbook.close()

    pue_model_outputs = {
        "rdf_ratio": ratio_npy,
        "rdf_type": type_npy,
        "filled_metrics": filled,
        "distance_metrics": distance,
        "rmse_metrics": rmse,
        "rmse_workbook": workbook_path,
    }
    repository_outputs: list[Path] = list(pue_model_outputs.values())

    for index, column_count in enumerate((27, 27, 25, 25)):
        centers = np.arange(column_count, dtype=np.float64)
        array = np.vstack([np.ones((3, column_count), dtype=np.float64), centers])
        path = experiment / f"other-{index}.npy"
        np.save(path, array)
        repository_outputs.append(path)

    def write_benchmark(path: Path, metric_count: int) -> None:
        header = ["Scenario", "miss_pct [0, 1]", *[f"metric-{i}" for i in range(metric_count)]]
        rows = [
            [scenario, str(ratio), *("1 +/- 0.1" for _ in range(metric_count))]
            for scenario in ("MAR", "MCAR", "MNAR")
            for ratio in (0.1, 0.2, 0.3, 0.4, 0.5)
        ]
        _write_csv(path, header, rows)

    for index in range(2):
        path = experiment / f"ten-{index}.csv"
        write_benchmark(path, 8)
        repository_outputs.append(path)
    for index in range(12):
        path = experiment / f"eight-{index}.csv"
        write_benchmark(path, 6)
        repository_outputs.append(path)
    for index in range(4):
        path = experiment / f"seven-{index}.csv"
        write_benchmark(path, 5)
        repository_outputs.append(path)

    def write_workbook(path: Path, column_count: int, missing_count: int) -> None:
        book = Workbook()
        tab = book.active
        tab.title = "Sheet1"
        tab.append(["RMSE", "Method", "Range", "Col"])
        row_index = 0
        for method in workbook_methods:
            for value_range in workbook_ranges:
                for column_index in range(column_count):
                    tab.append(
                        [
                            None if row_index < missing_count else 1.0,
                            method,
                            value_range,
                            f"column-{column_index}",
                        ]
                    )
                    row_index += 1
        book.save(path)
        book.close()

    for index, column_count in enumerate((6, 6, 6, 6, 5, 5, 5)):
        path = experiment / f"other-{index}.xlsx"
        write_workbook(path, column_count, 78 if index == 0 else 0)
        repository_outputs.append(path)

    def write_png(path: Path, width: int = 100, height: int = 50) -> None:
        import zlib

        def chunk(kind: bytes, payload: bytes) -> bytes:
            return (
                len(payload).to_bytes(4, "big")
                + kind
                + payload
                + (zlib.crc32(kind + payload) & 0xFFFFFFFF).to_bytes(4, "big")
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + chunk(
                b"IHDR",
                width.to_bytes(4, "big")
                + height.to_bytes(4, "big")
                + bytes((8, 0, 0, 0, 0)),
            )
            + chunk(b"IDAT", zlib.compress((b"\x00" + bytes(width)) * height))
            + chunk(b"IEND", b"")
        )

    root_png = experiment / "root.png"
    write_png(root_png)
    repository_outputs.append(root_png)

    for index, target_count in enumerate((*([13] * 9), 12)):
        path = filled_directory / f"dataset-{index}.csv"
        _write_csv(
            path,
            ["dataset", "column", "miss_ratio", "method", "RMSE", "Wasserstein", "time"],
            [
                [f"dataset-{index}", f"column-{column}", token, method, "1", "2", "3"]
                for column in range(target_count)
                for token in _MISSING_RATIO_TOKENS
                for method in _FILL_METHODS
            ],
        )
        repository_outputs.append(path)
    for index in range(15):
        path = filled_directory / f"plot-{index}.png"
        write_png(path)
        repository_outputs.append(path)

    return {
        "dq_parent_path": dq,
        "matimpute_parent_path": mat,
        "dq_projection_paths": {"logTS": projection_ts, "logYM": projection_ym},
        "missing_variants_directory": variants,
        "model_output_paths": pue_model_outputs,
        "repository_model_output_paths": repository_outputs,
        "expected_parent_row_count": 10,
        "expected_parent_column_count": 4,
    }


def test_dq_matimpute_content_verifies_parent_derived_family_and_outputs(tmp_path):
    profile = profile_dq_matimpute(**_pue_content_fixture(tmp_path))

    assert profile.source_record_candidate_count == 10
    assert profile.unique_system_candidate_count is None
    assert profile.file_count == 37
    assert profile.computational_observation_candidate_count == 0
    assert profile.diagnostics["canonical_parent_dataset_count"] == 1
    assert profile.diagnostics["derived_container_file_count"] == 29
    assert profile.diagnostics["dq_projection_file_count"] == 2
    assert profile.diagnostics["missing_variant_file_count"] == 27
    assert profile.diagnostics["missing_variant_intentional_blank_cell_count"] == 135
    assert profile.diagnostics["model_output_file_count"] == 6
    assert profile.diagnostics["filled_metric_row_count"] == 189
    assert profile.diagnostics["model_output_material_observation_count"] == 0
    assert profile.diagnostics["repository_model_output_file_count"] == 61
    assert profile.diagnostics["repository_benchmark_metric_cell_count"] == 1800
    assert profile.diagnostics["repository_filled_metric_row_count"] == 8316
    assert profile.diagnostics["repository_model_output_material_observation_count"] == 0


def test_dq_matimpute_rejects_parent_drift_and_invalid_expected_counts(tmp_path):
    kwargs = _pue_content_fixture(tmp_path)
    Path(kwargs["matimpute_parent_path"]).write_text("changed", encoding="utf-8")
    with pytest.raises(ComputationalAdmissionError) as mismatch:
        profile_dq_matimpute(**kwargs)
    assert mismatch.value.code == "parent_dataset_mismatch"

    kwargs = _pue_content_fixture(tmp_path / "second")
    kwargs["expected_parent_row_count"] = 0
    with pytest.raises(ComputationalAdmissionError) as count:
        profile_dq_matimpute(**kwargs)
    assert count.value.code == "invalid_input_count"


def test_dq_matimpute_rejects_projection_variant_and_model_tampering(tmp_path):
    kwargs = _pue_content_fixture(tmp_path / "projection")
    projection = kwargs["dq_projection_paths"]["logTS"]
    _write_csv(projection, ["logTS"], [["999"], *[[str(index + 0.3)] for index in range(1, 10)]])
    with pytest.raises(ComputationalAdmissionError) as projected:
        profile_dq_matimpute(**kwargs)
    assert projected.value.code == "pue_projection_content_mismatch"

    kwargs = _pue_content_fixture(tmp_path / "variant")
    next(Path(kwargs["missing_variants_directory"]).glob("*.csv")).unlink()
    with pytest.raises(ComputationalAdmissionError) as variant:
        profile_dq_matimpute(**kwargs)
    assert variant.value.code == "pue_missing_variant_set_mismatch"

    kwargs = _pue_content_fixture(tmp_path / "model")
    import numpy as np

    np.save(kwargs["model_output_paths"]["rdf_ratio"], np.zeros((3, 28)))
    with pytest.raises(ComputationalAdmissionError) as model:
        profile_dq_matimpute(**kwargs)
    assert model.value.code == "pue_model_output_invalid"

    kwargs = _pue_content_fixture(tmp_path / "repository")
    kwargs["repository_model_output_paths"] = kwargs[
        "repository_model_output_paths"
    ][:-1]
    with pytest.raises(ComputationalAdmissionError) as repository:
        profile_dq_matimpute(**kwargs)
    assert repository.value.code == "repository_model_output_set_mismatch"


def test_markdown_report_is_deterministic_explicitly_nontraining_and_cited(tmp_path):
    _write_csv(tmp_path / "A_DFT.csv", ["SMILES", "A"], [["A", "1"]])
    profile = profile_polygraphmt(tmp_path)

    first = render_computational_admission_markdown([profile])
    second = render_computational_admission_markdown([profile])

    assert first == second
    assert "不构成训练集" in first
    assert "CSV 行数不等于独立实验样本数" in first
    assert "已准入计算观测 | 0" in first
    assert "[8]" in first
    assert "10.1039/D6DD00206D" in first


def test_markdown_report_accepts_explicit_master_ledger_link(tmp_path):
    _write_csv(tmp_path / "A_DFT.csv", ["SMILES", "A"], [["A", "1"]])
    profile = profile_polygraphmt(tmp_path)
    report = render_computational_admission_markdown(
        [profile], ledger_link="../../文档/数据来源与参考文献.md"
    )
    assert "(../../文档/数据来源与参考文献.md)" in report


def test_markdown_report_rejects_blank_master_ledger_link(tmp_path):
    _write_csv(tmp_path / "A_DFT.csv", ["SMILES", "A"], [["A", "1"]])
    profile = profile_polygraphmt(tmp_path)
    with pytest.raises(ComputationalAdmissionError) as caught:
        render_computational_admission_markdown([profile], ledger_link=" ")
    assert caught.value.code == "invalid_ledger_link"


def _overlap_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    pi1m = tmp_path / "pi1m.csv"
    adept = tmp_path / "adept.csv"
    polyomics = tmp_path / "polyomics.csv"
    polygraph = tmp_path / "polygraph"
    _write_csv(pi1m, ["SMILES"], [["A"], ["B"], ["C"]])
    _write_csv(
        adept,
        ["PID", "SMILES"],
        [["p1", "A"], ["p2", "B"], ["p3", "B"]],
    )
    _write_csv(polyomics, ["smiles_list"], [["B"], ["C"], ["D"]])
    _write_csv(
        polygraph / "A_DFT.csv",
        ["SMILES", "A"],
        [["A", "1"], ["B", "2"], ["--", "3"]],
    )
    return pi1m, adept, polyomics, polygraph


def test_exact_structure_overlap_profiles_case_sensitive_lower_bounds(tmp_path):
    paths = _overlap_fixture(tmp_path)
    profile = profile_exact_structure_overlaps(*paths)

    assert profile.source_exact_structure_counts == {
        "pi1m": 3,
        "adept": 2,
        "polyomics": 3,
        "polygraphmt": 2,
    }
    assert profile.pair_overlap_counts == {
        "pi1m__adept": 2,
        "pi1m__polyomics": 2,
        "pi1m__polygraphmt": 2,
        "adept__polyomics": 1,
        "adept__polygraphmt": 2,
        "polyomics__polygraphmt": 1,
    }
    assert profile.diagnostics["polygraphmt_invalid_identity_record_count"] == 1
    assert profile.diagnostics["adept_multi_pid_structure_count"] == 1
    assert profile.diagnostics["adept_extra_pid_link_count"] == 1

    computational = profile_polygraphmt(paths[3])
    report = render_computational_admission_markdown(
        [computational], overlap_profile=profile
    )
    assert "跨库精确结构重叠" in report
    assert "PI1M ↔ ADEPT | 2" in report
    assert "禁止按文件行随机拆分" in report


def test_exact_structure_overlap_is_case_sensitive(tmp_path):
    paths = _overlap_fixture(tmp_path)
    _write_csv(paths[0], ["SMILES"], [["a"], ["B"], ["C"]])
    profile = profile_exact_structure_overlaps(*paths)
    assert profile.pair_overlap_counts["pi1m__adept"] == 1
    assert profile.pair_overlap_counts["pi1m__polygraphmt"] == 1


@pytest.mark.parametrize("sentinel", ["nan", "NA", "n/a", "none", "null", "-", "--"])
def test_exact_structure_overlap_rejects_identity_sentinels_outside_polygraphmt(
    tmp_path, sentinel
):
    paths = _overlap_fixture(tmp_path)
    _write_csv(paths[0], ["SMILES"], [[sentinel]])
    with pytest.raises(ComputationalAdmissionError) as caught:
        profile_exact_structure_overlaps(*paths)
    assert caught.value.code == "missing_identity"


def test_exact_structure_overlap_rejects_polygraphmt_outside_adept(tmp_path):
    paths = _overlap_fixture(tmp_path)
    _write_csv(paths[3] / "A_DFT.csv", ["SMILES", "A"], [["X", "1"]])
    with pytest.raises(ComputationalAdmissionError) as caught:
        profile_exact_structure_overlaps(*paths)
    assert caught.value.code == "polygraphmt_not_adept_subset"


def test_exact_structure_overlap_rejects_duplicate_adept_pid(tmp_path):
    paths = _overlap_fixture(tmp_path)
    _write_csv(paths[1], ["PID", "SMILES"], [["p1", "A"], ["p1", "B"]])
    with pytest.raises(ComputationalAdmissionError) as caught:
        profile_exact_structure_overlaps(*paths)
    assert caught.value.code == "duplicate_source_record_id"


def test_exact_structure_overlap_profile_invariants():
    sources = {"pi1m": 1, "adept": 1, "polyomics": 1, "polygraphmt": 1}
    pairs = {
        "pi1m__adept": 0,
        "pi1m__polyomics": 0,
        "pi1m__polygraphmt": 0,
        "adept__polyomics": 0,
        "adept__polygraphmt": 0,
        "polyomics__polygraphmt": 0,
    }
    profile = ExactStructureOverlapProfile(sources, pairs, {"basis": "exact"})
    assert profile.source_exact_structure_counts["adept"] == 1

    with pytest.raises(ComputationalAdmissionError) as missing_source:
        ExactStructureOverlapProfile({"pi1m": 1}, pairs, {})
    assert missing_source.value.code == "invalid_overlap_profile"

    with pytest.raises(ComputationalAdmissionError) as impossible_overlap:
        ExactStructureOverlapProfile(sources, {**pairs, "pi1m__adept": 2}, {})
    assert impossible_overlap.value.code == "invalid_overlap_profile"

    with pytest.raises(ComputationalAdmissionError) as boolean_count:
        ExactStructureOverlapProfile({**sources, "pi1m": True}, pairs, {})
    assert boolean_count.value.code == "invalid_overlap_profile"
