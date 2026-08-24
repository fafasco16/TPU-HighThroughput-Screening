from pathlib import Path

import 审计GFNFF尺寸门 as audit


def _case(root: Path, name: str, atoms: int, *, success: bool) -> None:
    case = root / name
    case.mkdir(parents=True)
    case.joinpath("input.xyz").write_text(
        f"{atoms}\nfixture\n" + "\n".join(f"H {i} 0 0" for i in range(atoms)) + "\n",
        encoding="utf-8",
    )
    if success:
        case.joinpath("xtb.out").write_text(
            "*** GEOMETRY OPTIMIZATION CONVERGED AFTER 3 ITERATIONS ***\nnormal termination of xtb\n",
            encoding="utf-8",
        )
        case.joinpath("time.log").write_text(
            "WALL=2.0 MAXRSS_KB=100 EXIT=0\n", encoding="utf-8"
        )
        case.joinpath("xtbopt.xyz").write_text(
            case.joinpath("input.xyz").read_text(encoding="utf-8"), encoding="utf-8"
        )
    else:
        case.joinpath("xtb.out").write_text("", encoding="utf-8")
        case.joinpath("time.log").write_text(
            "forrtl: severe (174): SIGSEGV\nWALL=0.2 MAXRSS_KB=200 EXIT=174\n",
            encoding="utf-8",
        )


def test_audit_distinguishes_success_sigsegv_and_unknown_interval(tmp_path):
    _case(tmp_path, "small", 489, success=True)
    _case(tmp_path, "large", 867, success=False)
    table, gate = audit.audit_smoke_root(tmp_path)
    assert table.set_index("case_id").loc["small", "outcome"] == "converged"
    assert table.set_index("case_id").loc["large", "outcome"] == "sigsegv_neighbor_initialization"
    assert gate["max_converged_atom_count"] == 489
    assert gate["min_sigsegv_atom_count"] == 867
    assert gate["production_atom_limit"] == 489
    assert gate["untested_interval"] == "490-866"


def test_writer_records_hashes_and_summary(tmp_path):
    raw = tmp_path / "raw"
    _case(raw, "small", 198, success=True)
    _case(raw, "large", 900, success=False)
    manifest = audit.write_release(
        raw, tmp_path / "out", release_id="test-gfnff-size-gate"
    )
    assert manifest["counts"] == {"cases": 2, "converged": 1, "sigsegv": 1}
    assert (tmp_path / "out" / "GFNFF尺寸烟雾审计.csv").is_file()
    assert (tmp_path / "out" / "GFNFF尺寸门.json").is_file()
