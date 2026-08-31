import subprocess
import sys
from pathlib import Path
import 生成扩充数据总账 as source

ROOT = Path(__file__).resolve().parents[2]


def test_release():
    f = source.build_release()
    assert len(f) == 62
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
    microsphere = f.loc[
        f.package_id.eq("pu_microsphere_loading_hysteresis_transfer")
    ]
    assert len(microsphere) == 1
    assert microsphere.iloc[0].row_count == 6
    assert microsphere.iloc[0].model_admission_layer == (
        "PU_microsphere_composite_transfer"
    )
    sls = f.loc[
        f.package_id.eq("sls_tpu1301_gold_process_tensile")
    ]
    assert len(sls) == 1
    assert sls.iloc[0].row_count == 140
    assert sls.iloc[0].model_admission_layer == (
        "core_tpu_application_experimental"
    )
    kinetics = f.loc[
        f.package_id.eq("solvent_free_pu_reaction_kinetics")
    ]
    assert len(kinetics) == 1
    assert kinetics.iloc[0].row_count == 21
    assert kinetics.iloc[0].model_admission_layer == (
        "synthesis_kinetics_experimental"
    )
    assert kinetics.iloc[0].mapping_completeness_score == 0.90
    microporous = f.loc[f.package_id.str.startswith("microporous_pu_")]
    assert set(microporous.package_id) == {
        "microporous_pu_dma_transfer",
        "microporous_pu_shpb_transfer",
    }
    assert microporous["row_count"].sum() == 12
    assert microporous["model_admission_layer"].eq(
        "microporous_PU_dynamic_transfer"
    ).all()
    sls_silver = f.loc[
        f.package_id.eq("sls_tpu1301_silver_process_tensile")
    ]
    assert len(sls_silver) == 1
    assert sls_silver.iloc[0].row_count == 210
    assert sls_silver.iloc[0].model_admission_layer == (
        "SLS_TPU_process_silver"
    )
    md = f.loc[f.package_id.eq("mdpi_pu_md_descriptors")]
    assert len(md) == 1
    assert md.iloc[0].row_count == 79
    assert md.iloc[0].model_admission_layer == (
        "md_computed_descriptor_reference"
    )
    dynamic_foam = f.loc[
        f.package_id.eq("dynamic_pu_foam_compression_energy")
    ]
    assert len(dynamic_foam) == 1
    assert dynamic_foam.iloc[0].row_count == 12
    assert dynamic_foam.iloc[0].material_count == 2
    assert dynamic_foam.iloc[0].model_admission_layer == (
        "dynamic_PU_foam_transfer"
    )
    healing = f.loc[f.package_id.str.startswith("self_healing_4tu_")]
    assert set(healing.package_id) == {
        "self_healing_4tu_cut_recovery_pairs",
        "self_healing_4tu_tga",
    }
    assert healing["row_count"].sum() == 12
    assert healing["license"].eq("CC-BY-4.0").all()
    assert set(healing.model_admission_layer) == {
        "core_TPU_healing_experimental",
        "core_TPU_thermal_experimental",
    }
    conductive = f.loc[
        f.package_id.str.startswith("conductive_self_healing_pu_")
    ]
    assert set(conductive.package_id) == {
        "conductive_self_healing_pu_tensile_recycling",
        "conductive_self_healing_pu_recovery_summary",
    }
    assert conductive["row_count"].sum() == 11
    assert conductive["model_admission_layer"].eq(
        "conductive_crosslinked_PU_composite_transfer"
    ).all()
    footwear = f.loc[f.package_id.str.startswith("tpu_footwear_")]
    assert set(footwear.package_id) == {
        "tpu_footwear_tga",
        "tpu_footwear_wear_summary",
    }
    assert footwear["row_count"].sum() == 15
    assert footwear["model_admission_layer"].eq(
        "commercial_footwear_elastomer_application"
    ).all()
    mendeley_tpu = f.loc[
        f.package_id.str.startswith("mendeley_tpu_")
    ]
    assert set(mendeley_tpu.package_id) == {
        "mendeley_tpu_partial_tensile_experiment",
        "mendeley_tpu_simulation_calibration_reference",
    }
    assert mendeley_tpu["row_count"].sum() == 16
    simulation = mendeley_tpu.loc[
        mendeley_tpu.package_id.eq(
            "mendeley_tpu_simulation_calibration_reference"
        )
    ]
    assert simulation.iloc[0].model_admission_layer == (
        "simulation_calibration_reference"
    )
    lignin = f.loc[f.package_id.str.startswith("lignin_tpu_")]
    assert set(lignin.package_id) == {
        "lignin_tpu_precursor_fiber_mechanical",
        "lignin_tpu_tga_transfer",
    }
    assert lignin["row_count"].sum() == 14
    assert lignin["model_admission_layer"].eq(
        "lignin_TPU_carbon_fiber_precursor_transfer"
    ).all()
    doe = f.loc[f.package_id.eq("tpu_print_compression_doe")]
    assert len(doe) == 1
    assert doe.iloc[0].row_count == 344
    assert doe.iloc[0].model_admission_layer == (
        "core_TPU_application_experimental"
    )
    recycled = f.loc[f.package_id.str.startswith("recycled_pu_foam_")]
    assert len(recycled) == 6
    assert recycled.row_count.sum() == 13931
    assert recycled.model_admission_layer.eq("polyurethane_foam_transfer").all()


def test_command():
    s = ROOT / "代码" / "生成扩充数据总账.py"
    subprocess.run([sys.executable, str(s)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(s), "--检查"], cwd=ROOT, check=True)
