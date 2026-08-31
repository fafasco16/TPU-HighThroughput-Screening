import json
import subprocess
import sys
from pathlib import Path

import 接入Zenodo导电自修复PU as source


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "结果" / "定向筛选"


def test_tensile_and_recycling_curves_are_mapped_without_core_tpu_claim():
    mechanical, _ = source.build_release()
    assert len(mechanical) == 8
    screening = mechanical.loc[
        mechanical["record_role"].eq("formulation_screening_tensile")
    ]
    recycling = mechanical.loc[
        mechanical["record_role"].eq("recycling_state_tensile")
    ]
    assert len(screening) == 5
    assert len(recycling) == 3
    assert screening["formulation_id"].nunique() == 5
    assert screening["curve_max_vs_table_elongation_relative_difference"].lt(
        0.05
    ).all()
    assert mechanical["tensile_curve_area_MJ_m3"].gt(0).all()
    assert mechanical["thermoplastic_TPU_core"].eq(False).all()
    assert mechanical["blend_fraction_basis"].eq(
        "solution_mass_fraction_not_dry_solid_fraction"
    ).all()
    assert set(recycling["material_state"]) == {
        "original_composite",
        "chemically_recycled_composite",
        "mechanically_reused_composite",
    }
    assert recycling[
        "recycling_state_curve_area_retention_fraction"
    ].between(0.9, 1.1).all()
    optimized = screening.loc[
        screening["formulation_id"].eq("PEDOT:PSS/PU-18/Gly-2.2")
    ].iloc[0]
    assert optimized.pedot_pss_solution_wt_pct == 80.3
    assert optimized.pu_solution_wt_pct == 17.5
    assert optimized.glycerol_wt_pct == 2.2


def test_only_explicit_recovery_summaries_are_published():
    _, recovery = source.build_release()
    assert len(recovery) == 3
    assert set(recovery["formulation_id"]) == {
        "PEDOT:PSS/PU-13",
        "PEDOT:PSS/PU-18/Gly-2.2",
    }
    cut_stick = recovery.loc[
        recovery["metric_name"].eq("cut_stick_elongation_recovery")
    ]
    cyclic = recovery.loc[recovery["metric_name"].eq("cyclic_energy_recovery")]
    assert len(cut_stick) == 2
    assert len(cyclic) == 1
    assert set(cut_stick["metric_mean_pct"]) == {75.0, 98.0}
    assert cyclic.iloc[0].metric_mean_pct == 75.0
    assert cyclic.iloc[0].cycle_count == 500
    assert cyclic.iloc[0].stabilization_cycle == 100
    assert cyclic.iloc[0].energy_dissipation_initial_kJ_m3 == 145.0
    assert cyclic.iloc[0].energy_dissipation_stable_kJ_m3 == 110.0
    assert recovery["raw_mechanical_cycle_curve_available_in_local_zip"].eq(
        False
    ).all()


def test_release_and_check_command():
    script = ROOT / "代码" / "接入Zenodo导电自修复PU.py"
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(script), "--检查"], cwd=ROOT, check=True)
    manifest = json.loads(
        (OUTPUT / "导电自修复PU发布清单.json").read_text(encoding="utf-8")
    )
    assert manifest["counts"]["archive_member_count"] == 68
    assert manifest["counts"]["substantive_file_count"] == 27
    assert manifest["counts"]["formulation_screening_curve_count"] == 5
    assert manifest["counts"]["recycling_state_curve_count"] == 3
    assert manifest["counts"]["published_recovery_summary_count"] == 3
    assert manifest["counts"]["published_compact_row_count"] == 11
    assert manifest["policy"]["electrical_cycles_claimed_as_mechanical_cycles"] is False
    assert manifest["policy"]["crosslinked_PU_claimed_as_thermoplastic_TPU_core"] is False
