import subprocess
import sys
from pathlib import Path
import 生成扩充数据总账 as source

ROOT = Path(__file__).resolve().parents[2]


def test_release():
    f = source.build_release()
    assert len(f) == 37
    assert f.package_id.is_unique
    assert f.row_count.gt(0).all()
    assert f.data_sha256.str.len().eq(64).all()
    assert f.mapping_completeness_score.between(0, 1).all()
    assert f.next_mapping_action.str.len().gt(0).all()
    assert f.expansion_priority_score.is_monotonic_decreasing
    relaxation = f.loc[f.package_id.eq("standard_relaxation_proxy")]
    assert len(relaxation) == 1
    assert relaxation.iloc[0].model_admission_layer == (
        "commercial_elastomer_auxiliary"
    )
    assert relaxation.iloc[0].row_count == 2
    low_ceiling_tga = f.loc[f.package_id.eq("low_ceiling_tpuu_tga")]
    assert len(low_ceiling_tga) == 1
    assert low_ceiling_tga.iloc[0].row_count == 4
    assert low_ceiling_tga.iloc[0].model_admission_layer == (
        "core_tpuu_experimental"
    )
    assert {
        "qub_self_healing_tensile",
        "qub_self_healing_cycle_proxy",
        "qub_self_healing_tga",
    } <= set(f.package_id)
    assert f.loc[f.package_id.str.startswith("qub_"), "license"].eq("CC-BY-4.0").all()
    assert {
        "dib_shape_memory_tensile",
        "dib_shape_memory_cycle_proxy",
        "dib_shape_memory_thermal",
    } <= set(f.package_id)
    assert f.loc[
        f.package_id.str.startswith("dib_"), "model_admission_layer"
    ].eq("polyurethane_transfer").all()
    assert {
        "commercial_tpu_impact_fatigue",
        "commercial_tpu_energy_recovery_pairs",
    } <= set(f.package_id)
    assert f.loc[
        f.package_id.str.startswith("commercial_tpu_"), "model_admission_layer"
    ].eq("core_tpu_application_experimental").all()
    shape_memory = f.loc[f.package_id.eq("elastollan_pcl_shape_memory_summary")]
    assert len(shape_memory) == 1
    assert shape_memory.iloc[0].model_admission_layer == (
        "core_tpu_blend_published_summary"
    )
    assert shape_memory.iloc[0].license == (
        "paper-license-unverified-facts-only"
    )
    tecoflex = f.loc[f.package_id.eq("tecoflex_nic_multiperformance")]
    assert len(tecoflex) == 1
    assert tecoflex.iloc[0].model_admission_layer == (
        "core_tpu_composite_experimental"
    )
    assert tecoflex.iloc[0].target_family == (
        "partial_toughness_and_thermal_stability"
    )
    assert {
        "iir_oh_100cycle_endpoints",
        "iir_oh_hydrolytic_retention",
    } <= set(f.package_id)
    assert f.loc[
        f.package_id.str.startswith("iir_oh_"), "model_admission_layer"
    ].eq("polyurethane_adjacent_experimental").all()
    assert {
        "tpu95a_load_extension_auxiliary",
        "tpu95a_relaxation_proxy",
    } <= set(f.package_id)
    mirrors = f.loc[f.package_id.str.startswith("tpu95a_")]
    assert mirrors["source_independence_status"].eq(
        "historical_mirror_rematerialized_zero_new_source"
    ).all()
    foam = f.loc[f.package_id.eq("pcf20_foam_tension_fracture")]
    assert len(foam) == 1
    assert foam.iloc[0].model_admission_layer == "polyurethane_foam_transfer"
    assert foam.iloc[0].target_family == "toughness_transfer"
    tpu1301 = f.loc[f.package_id.str.startswith("tpu1301_")]
    assert set(tpu1301.package_id) == {
        "tpu1301_tensile_application",
        "tpu1301_relaxation_proxy",
    }
    assert tpu1301["row_count"].sum() == 20
    assert tpu1301["model_admission_layer"].eq(
        "core_tpu_application_experimental"
    ).all()
    vitrimer = f.loc[f.package_id.str.startswith("biobased_vitrimer_")]
    assert len(vitrimer) == 3
    assert vitrimer["row_count"].sum() == 40
    assert vitrimer["model_admission_layer"].eq(
        "dynamic_network_vitrimer_transfer"
    ).all()
    assert vitrimer["mapping_completeness_score"].eq(0.20).all()
    single_fiber = f.loc[
        f.package_id.eq("pcu85_single_fiber_cyclic_transfer")
    ]
    assert len(single_fiber) == 1
    assert single_fiber.iloc[0].row_count == 152
    assert single_fiber.iloc[0].model_admission_layer == (
        "single_fiber_polyurethane_auxiliary"
    )
    cast_pu = f.loc[f.package_id.str.startswith("cast_pu_relaxation_")]
    assert set(cast_pu.package_id) == {
        "cast_pu_relaxation_curve_evidence",
        "cast_pu_relaxation_condition_aggregate",
    }
    assert cast_pu["row_count"].sum() == 79
    assert cast_pu["mapping_completeness_score"].eq(0.20).all()
    copper = f.loc[
        f.package_id.eq("pu_copper_pyrolysis_tga_transfer")
    ]
    assert len(copper) == 1
    assert copper.iloc[0].row_count == 6
    assert copper.iloc[0].model_admission_layer == (
        "pu_pyrolysis_thermal_transfer"
    )
    fdm = f.loc[
        f.package_id.eq("fdm_tpu_lattice_substrate_mechanics")
    ]
    assert len(fdm) == 1
    assert fdm.iloc[0].row_count == 57
    assert fdm.iloc[0].model_admission_layer == (
        "FDM_TPU_application_transfer"
    )


def test_command():
    s = ROOT / "代码" / "生成扩充数据总账.py"
    subprocess.run([sys.executable, str(s)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(s), "--检查"], cwd=ROOT, check=True)
