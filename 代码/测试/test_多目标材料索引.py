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


def test_command():
    s = ROOT / "代码" / "生成多目标材料索引.py"
    subprocess.run([sys.executable, str(s)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(s), "--检查"], cwd=ROOT, check=True)
