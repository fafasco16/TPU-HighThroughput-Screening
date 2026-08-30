import subprocess
import sys
from pathlib import Path
import 生成多目标材料索引 as source

ROOT = Path(__file__).resolve().parents[2]


def test_release():
    f = source.build_release()
    assert {"Cheetah", "Filaflex 60A"} <= set(f.material_key)
    assert (
        f.query("material_key in ['Cheetah','Filaflex 60A']")
        .objective_coverage_count.eq(2)
        .all()
    )
    assert (f.objective_coverage_count == 3).sum() > 0
    assert f.loc[f.objective_coverage_count.eq(3), "missing_objectives"].eq("").all()
    assert f.loc[f.objective_coverage_count.eq(2), "objective_gap_count"].eq(1).all()
    assert f.loc[f.objective_gap_count.gt(0), "gap_next_action"].ne("none").all()
    assert f.loc[f.material_key.eq("PMCL-2k-18HS"), "gap_evidence_status"].eq("source_tensile_sheet_empty").all()
    qub = f.loc[f.source_family.eq("QUB_生物基三重自修复TPU")]
    assert set(qub.material_key) == {"P35", "P40", "P45", "P40-HDO"}
    assert qub.loc[qub.material_key.eq("P40"), "objective_coverage_count"].eq(3).all()
    assert qub.loc[qub.material_key.isin(["P35", "P45"]), "objective_coverage_count"].eq(2).all()
    assert qub.loc[qub.material_key.eq("P40-HDO"), "objective_coverage_count"].eq(1).all()
    assert qub.loc[qub.material_key.eq("P40"), "cyclic_evidence_level"].eq(
        "hysteresis_proxy_not_direct_recovery"
    ).all()
    dib = f.loc[f.source_family.eq("DataInBrief_交联形状记忆PU")]
    assert len(dib) == 12
    assert dib["objective_coverage_count"].eq(3).all()
    assert dib["model_admission_layer"].eq("polyurethane_transfer").all()
    assert dib["cyclic_evidence_level"].eq(
        "stress_retention_hysteresis_proxy_not_shape_recovery"
    ).all()
    assert dib["chemistry_mapping_status"].eq(
        "monomer_set_molar_composition_mapped"
    ).all()
    commercial = f.loc[f.source_family.eq("Mendeley_商业TPU温度疲劳")]
    assert set(commercial.material_key) == {
        "Elastollan 1154D",
        "Elastollan 1164D",
        "Elastollan 1174D",
        "Elastollan 1195A",
        "Texin 245",
    }
    assert commercial["objective_coverage_count"].eq(2).all()
    assert commercial["model_admission_layer"].eq(
        "core_tpu_application_experimental"
    ).all()
    assert commercial["toughness_evidence_level"].eq(
        "compression_energy_absorption_application_proxy"
    ).all()
    assert commercial["cyclic_evidence_level"].eq(
        "direct_impact_fatigue_and_same_specimen_energy_recovery"
    ).all()
    blends = f.loc[
        f.source_family.eq("JMERD_Elastollan1154D_PCL_shape_memory")
    ]
    assert len(blends) == 3
    assert set(blends.material_key) == {
        "Elastollan1154D_30wt_PCL_70wt",
        "Elastollan1154D_45wt_PCL_55wt",
        "Elastollan1154D_60wt_PCL_40wt",
    }
    assert blends["objective_coverage_count"].eq(1).all()
    assert blends["model_admission_layer"].eq(
        "core_tpu_blend_published_summary"
    ).all()
    assert blends["cyclic_evidence_level"].eq(
        "direct_shape_fixity_and_recovery_published_summary"
    ).all()
    tecoflex = f.loc[f.source_family.eq("Zenodo_Tecoflex_EG60D_NIC")]
    assert set(tecoflex.material_key) == {
        "Tecoflex_EG60D",
        "Tecoflex_EG60D_NIC2",
        "Tecoflex_EG60D_NIC5",
        "Tecoflex_EG60D_NIC10",
    }
    assert tecoflex["objective_coverage_count"].eq(2).all()
    assert tecoflex["model_admission_layer"].eq(
        "core_tpu_composite_experimental"
    ).all()
    assert tecoflex["toughness_evidence_level"].eq(
        "partial_tensile_curve_area_lower_bound"
    ).all()
    assert tecoflex["thermal_evidence_level"].eq("direct_TGA_curve").all()
    iir = f.loc[f.source_family.eq("Mendeley_IIROH_PU_durability")]
    assert set(iir.material_key) == {"HDI-4", "HMDI-4"}
    assert iir["objective_coverage_count"].eq(2).all()
    assert iir["model_admission_layer"].eq(
        "polyurethane_adjacent_experimental"
    ).all()
    assert iir["cyclic_evidence_level"].eq(
        "direct_100_cycle_hysteresis_and_hydrolytic_retention"
    ).all()
    assert iir["toughness_evidence_level"].eq(
        "hydrolytic_pair_before_curve_area"
    ).all()
    tpu95a = f.loc[f.source_family.eq("Mendeley_eSUN_eTPU95A")]
    assert len(tpu95a) == 1
    assert tpu95a.iloc[0].material_key == "eSUN eTPU-95A"
    assert tpu95a.iloc[0].objective_coverage_count == 1
    assert tpu95a.iloc[0].cyclic_evidence_level == (
        "stress_relaxation_transfer_proxy_historical_mirror"
    )
    assert tpu95a.iloc[0].model_admission_layer == (
        "core_tpu_application_experimental"
    )
    foam = f.loc[f.source_family.eq("MaterialsCloud_Sawbones_PCF20")]
    assert len(foam) == 1
    assert foam.iloc[0].material_key == "Sawbones PCF20"
    assert foam.iloc[0].objective_coverage_count == 1
    assert foam.iloc[0].model_admission_layer == "polyurethane_foam_transfer"
    assert foam.iloc[0].toughness_evidence_level == (
        "direct_tensile_area_and_SENB_nominal_K_foam_transfer"
    )


def test_command():
    s = ROOT / "代码" / "生成多目标材料索引.py"
    subprocess.run([sys.executable, str(s)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(s), "--检查"], cwd=ROOT, check=True)
