"""按来源与材料键汇总已物化的多目标性能覆盖。"""

from __future__ import annotations
import argparse
import hashlib
import json
import tempfile
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "结果" / "定向筛选"
OUT = D / "多目标材料索引.csv"
MAN = D / "多目标材料索引发布清单.json"
INPUTS = {
    "drum_tensile": D / "DRUM机械回收拉伸端点.csv",
    "drum_cycle": D / "DRUM机械回收循环端点.csv",
    "drum_tga": D / "DRUM机械回收TGA端点.csv",
    "low_tpuu_cycle": D / "TPUU循环端点.csv",
    "low_tpuu_tga": D / "低天花板TPUU热稳定端点.csv",
    "directed_labels": D / "三目标实验标签.csv.gz",
    "std_tensile": D / "标准化热塑性弹性体拉伸端点.csv",
    "std_relaxation": D / "标准热塑性弹性体松弛端点.csv",
    "std_tga": D / "标准化热塑性弹性体TGA端点.csv",
    "phcu": D / "PHCU双目标端点.csv",
    "qub_tensile": D / "QUB生物基自修复TPU拉伸端点.csv",
    "qub_cycle": D / "QUB生物基自修复TPU循环端点.csv",
    "qub_tga": D / "QUB生物基自修复TPUTGA端点.csv",
    "dib_tensile": D / "DataInBrief形状记忆PU拉伸端点.csv",
    "dib_cycle": D / "DataInBrief形状记忆PU循环端点.csv",
    "dib_tga": D / "DataInBrief形状记忆PU热稳定端点.csv",
    "commercial_fatigue": D / "商业TPU温度疲劳端点.csv",
    "commercial_recovery": D / "商业TPU恢复配对端点.csv",
    "elastollan_pcl_shape_memory": D / "ElastollanPCL形状记忆端点.csv",
    "tecoflex_nic": D / "Tecoflex药物复合TPU多性能端点.csv",
    "iir_cyclic": D / "IIR-OH聚氨酯循环端点.csv",
    "iir_aging": D / "IIR-OH聚氨酯水解保持端点.csv",
    "tpu95a_tensile": D / "TPU95A载荷伸长端点.csv",
    "tpu95a_relaxation": D / "TPU95A应力松弛端点.csv",
    "pcf20_foam": D / "PCF20泡沫拉伸断裂端点.csv",
    "tpu1301_tensile": D / "TPU1301拉伸端点.csv",
    "tpu1301_relaxation": D / "TPU1301应力松弛端点.csv",
}


def sha(p):
    d = hashlib.sha256()
    d.update(p.read_bytes())
    return d.hexdigest()


def build_release():
    rows = []
    specs = [
        (
            "DRUM_TPUU_机械回收",
            "reference-53;reference-54",
            "family_mn_hard_segment_mapped",
            "drum_tensile",
            "drum_cycle",
            "drum_tga",
        ),
        (
            "DRUM_TPUU_低天花板",
            "reference-55;reference-56",
            "formulation_id_only",
            "low_tpuu_tensile",
            "low_tpuu_cycle",
            "low_tpuu_tga",
        ),
        (
            "Zenodo_标准化弹性体表征",
            "zenodo-14983287",
            "commercial_grade_identity_only",
            "std_tensile",
            "std_relaxation",
            "std_tga",
        ),
        (
            "QUB_生物基三重自修复TPU",
            "reference-182;reference-183",
            "monomer_set_hard_segment_mapped",
            "qub_tensile",
            "qub_cycle",
            "qub_tga",
        ),
        (
            "DataInBrief_交联形状记忆PU",
            "reference-184;reference-185",
            "monomer_set_molar_composition_mapped",
            "dib_tensile",
            "dib_cycle",
            "dib_tga",
        ),
        (
            "Zenodo_TPU1301热黏弹黏塑本构",
            "reference-38;reference-39",
            "commercial_grade_identity_only",
            "tpu1301_tensile",
            "tpu1301_relaxation",
            None,
        ),
    ]
    frames = {k: pd.read_csv(v, low_memory=False) for k, v in INPUTS.items()}
    labels = frames["directed_labels"]
    frames["low_tpuu_tensile"] = labels.loc[
        labels["source_id"].eq("source_drum_zf53w893")
        & labels["property_name"].eq("tensile_toughness")
    ].copy()
    model_layers = {
        "DRUM_TPUU_机械回收": "core_tpuu_experimental",
        "DRUM_TPUU_低天花板": "core_tpuu_experimental",
        "Zenodo_标准化弹性体表征": "commercial_elastomer_auxiliary",
        "QUB_生物基三重自修复TPU": "core_tpu_experimental",
        "DataInBrief_交联形状记忆PU": "polyurethane_transfer",
        "Zenodo_TPU1301热黏弹黏塑本构": (
            "core_tpu_application_experimental"
        ),
    }
    for source, cites, mapping, tkey, ckey, hkey in specs:
        materials = set()
        for key in (tkey, ckey, hkey):
            if key:
                materials |= set(frames[key].formulation_id)
        for m in sorted(materials):
            tc = int((frames[tkey].formulation_id == m).sum()) if tkey else 0
            cc = int((frames[ckey].formulation_id == m).sum()) if ckey else 0
            hc = int((frames[hkey].formulation_id == m).sum()) if hkey else 0
            coverage = sum(x > 0 for x in (tc, cc, hc))
            cyclic_evidence = "not_available"
            if cc > 0:
                cyclic_evidence = (
                    "hysteresis_proxy_not_direct_recovery"
                    if source == "QUB_生物基三重自修复TPU"
                    else "stress_retention_hysteresis_proxy_not_shape_recovery"
                    if source == "DataInBrief_交联形状记忆PU"
                    else "stress_relaxation_proxy_not_direct_cycles"
                    if source
                    in {
                        "Zenodo_标准化弹性体表征",
                        "Zenodo_TPU1301热黏弹黏塑本构",
                    }
                    else "direct_cycle_endpoint"
                )
            rows.append(
                {
                    "source_family": source,
                    "material_key": m,
                    "chemistry_mapping_status": mapping,
                    "toughness_record_count": tc,
                    "cyclic_record_count": cc,
                    "thermal_record_count": hc,
                    "has_toughness": tc > 0,
                    "has_cyclic_recovery": cc > 0,
                    "has_thermal_stability": hc > 0,
                    "objective_coverage_count": coverage,
                    "multiobjective_status": "three_objectives"
                    if coverage == 3
                    else "two_objectives"
                    if coverage == 2
                    else "single_objective",
                    "model_admission_layer": model_layers[source],
                    "toughness_evidence_level": (
                        "not_available"
                        if tc == 0
                        else "direct_tensile_curve_area_transfer"
                        if source == "DataInBrief_交联形状记忆PU"
                        else "direct_tensile_curve_area_auxiliary"
                        if source == "Zenodo_标准化弹性体表征"
                        else "direct_tensile_curve_area_application"
                        if source == "Zenodo_TPU1301热黏弹黏塑本构"
                        else "direct_tensile_curve_area"
                    ),
                    "thermal_evidence_level": (
                        "not_available"
                        if hc == 0
                        else "direct_TGA_curve_transfer"
                        if source == "DataInBrief_交联形状记忆PU"
                        else "direct_TGA_curve"
                    ),
                    "cyclic_evidence_level": cyclic_evidence,
                    "citation_keys": cites,
                }
            )
    for row in frames["phcu"].itertuples(index=False):
        rows.append(
            {
                "source_family": "Mendeley_PHCU_nonisocyanate",
                "material_key": row.formulation_id,
                "chemistry_mapping_status": row.chemistry_mapping_status,
                "toughness_record_count": 1,
                "cyclic_record_count": 0,
                "thermal_record_count": 1,
                "has_toughness": True,
                "has_cyclic_recovery": False,
                "has_thermal_stability": True,
                "objective_coverage_count": 2,
                "multiobjective_status": "two_objectives",
                "model_admission_layer": "polyurethane_adjacent_experimental",
                "toughness_evidence_level": "published_tensile_summary",
                "thermal_evidence_level": "published_TGA_summary",
                "cyclic_evidence_level": "not_available",
                "citation_keys": row.citation_keys,
            }
        )
    commercial_histories = frames["commercial_fatigue"]
    commercial_recovery = frames["commercial_recovery"]
    for material in sorted(commercial_histories["material_grade"].unique()):
        history_count = int(
            commercial_histories["material_grade"].eq(material).sum()
        )
        recovery_count = int(
            commercial_recovery["material_grade"].eq(material).sum()
        )
        rows.append(
            {
                "source_family": "Mendeley_商业TPU温度疲劳",
                "material_key": material,
                "chemistry_mapping_status": "commercial_grade_identity_only",
                "toughness_record_count": history_count,
                "cyclic_record_count": history_count + recovery_count,
                "thermal_record_count": 0,
                "has_toughness": True,
                "has_cyclic_recovery": True,
                "has_thermal_stability": False,
                "objective_coverage_count": 2,
                "multiobjective_status": "two_objectives_application",
                "model_admission_layer": "core_tpu_application_experimental",
                "toughness_evidence_level": (
                    "compression_energy_absorption_application_proxy"
                ),
                "thermal_evidence_level": "not_available",
                "cyclic_evidence_level": (
                    "direct_impact_fatigue_and_same_specimen_energy_recovery"
                ),
                "citation_keys": "reference-186",
            }
        )
    for row in frames["elastollan_pcl_shape_memory"].itertuples(index=False):
        rows.append(
            {
                "source_family": "JMERD_Elastollan1154D_PCL_shape_memory",
                "material_key": row.material_id,
                "chemistry_mapping_status": row.chemistry_mapping_status,
                "toughness_record_count": 0,
                "cyclic_record_count": 1,
                "thermal_record_count": 0,
                "has_toughness": False,
                "has_cyclic_recovery": True,
                "has_thermal_stability": False,
                "objective_coverage_count": 1,
                "multiobjective_status": "single_objective_direct_recovery",
                "model_admission_layer": "core_tpu_blend_published_summary",
                "toughness_evidence_level": "not_available",
                "thermal_evidence_level": "not_available",
                "cyclic_evidence_level": (
                    "direct_shape_fixity_and_recovery_published_summary"
                ),
                "citation_keys": row.citation_keys,
            }
        )
    for row in frames["tecoflex_nic"].itertuples(index=False):
        rows.append(
            {
                "source_family": "Zenodo_Tecoflex_EG60D_NIC",
                "material_key": row.material_id,
                "chemistry_mapping_status": row.chemistry_mapping_status,
                "toughness_record_count": 1,
                "cyclic_record_count": 0,
                "thermal_record_count": 1,
                "has_toughness": True,
                "has_cyclic_recovery": False,
                "has_thermal_stability": True,
                "objective_coverage_count": 2,
                "multiobjective_status": "two_objectives_partial_toughness",
                "model_admission_layer": "core_tpu_composite_experimental",
                "toughness_evidence_level": row.toughness_evidence_level,
                "thermal_evidence_level": "direct_TGA_curve",
                "cyclic_evidence_level": "not_available",
                "citation_keys": row.citation_keys,
            }
        )
    iir_cyclic = frames["iir_cyclic"]
    iir_aging = frames["iir_aging"]
    for material in sorted(iir_cyclic["formulation_id"].unique()):
        cyclic_count = int(iir_cyclic["formulation_id"].eq(material).sum())
        aging_count = int(iir_aging["formulation_id"].eq(material).sum())
        mapping = iir_cyclic.loc[
            iir_cyclic["formulation_id"].eq(material),
            "chemistry_mapping_status",
        ].iloc[0]
        rows.append(
            {
                "source_family": "Mendeley_IIROH_PU_durability",
                "material_key": material,
                "chemistry_mapping_status": mapping,
                "toughness_record_count": aging_count,
                "cyclic_record_count": cyclic_count + aging_count,
                "thermal_record_count": 0,
                "has_toughness": True,
                "has_cyclic_recovery": True,
                "has_thermal_stability": False,
                "objective_coverage_count": 2,
                "multiobjective_status": "two_objectives_durability_transfer",
                "model_admission_layer": "polyurethane_adjacent_experimental",
                "toughness_evidence_level": (
                    "hydrolytic_pair_before_curve_area"
                ),
                "thermal_evidence_level": "not_available",
                "cyclic_evidence_level": (
                    "direct_100_cycle_hysteresis_and_hydrolytic_retention"
                ),
                "citation_keys": "reference-190;reference-191",
            }
        )
    tpu95a_relaxation = frames["tpu95a_relaxation"]
    tpu95a_tensile = frames["tpu95a_tensile"]
    rows.append(
        {
            "source_family": "Mendeley_eSUN_eTPU95A",
            "material_key": "eSUN eTPU-95A",
            "chemistry_mapping_status": "commercial_grade_identity_only",
            "toughness_record_count": 0,
            "cyclic_record_count": len(tpu95a_relaxation),
            "thermal_record_count": 0,
            "has_toughness": False,
            "has_cyclic_recovery": True,
            "has_thermal_stability": False,
            "objective_coverage_count": 1,
            "multiobjective_status": "single_objective_relaxation_proxy",
            "model_admission_layer": "core_tpu_application_experimental",
            "toughness_evidence_level": "load_extension_auxiliary_not_toughness",
            "thermal_evidence_level": "not_available",
            "cyclic_evidence_level": (
                "stress_relaxation_transfer_proxy_historical_mirror"
            ),
            "source_independence_status": (
                "historical_mirror_rematerialized_zero_new_source"
            ),
            "auxiliary_tensile_run_count": len(tpu95a_tensile),
            "citation_keys": "reference-192",
        }
    )
    pcf20 = frames["pcf20_foam"]
    rows.append(
        {
            "source_family": "MaterialsCloud_Sawbones_PCF20",
            "material_key": "Sawbones PCF20",
            "chemistry_mapping_status": "commercial_grade_density_only",
            "toughness_record_count": len(pcf20),
            "cyclic_record_count": 0,
            "thermal_record_count": 0,
            "has_toughness": True,
            "has_cyclic_recovery": False,
            "has_thermal_stability": False,
            "objective_coverage_count": 1,
            "multiobjective_status": "single_objective_foam_toughness_transfer",
            "model_admission_layer": "polyurethane_foam_transfer",
            "toughness_evidence_level": (
                "direct_tensile_area_and_SENB_nominal_K_foam_transfer"
            ),
            "thermal_evidence_level": "not_available",
            "cyclic_evidence_level": "not_available",
            "citation_keys": "reference-193;reference-194",
        }
    )
    frame = (
        pd.DataFrame(rows)
        .sort_values(
            ["objective_coverage_count", "source_family", "material_key"],
            ascending=[False, True, True],
        )
        .reset_index(drop=True)
    )
    target_columns = {
        "toughness": "has_toughness",
        "cyclic_recovery": "has_cyclic_recovery",
        "thermal_stability": "has_thermal_stability",
    }
    frame["missing_objectives"] = frame.apply(
        lambda row: ";".join(
            target for target, column in target_columns.items() if not row[column]
        ),
        axis=1,
    )
    frame["objective_gap_count"] = 3 - frame["objective_coverage_count"]
    frame["completion_priority"] = frame["objective_gap_count"].map(
        {0: "complete_three_objectives", 1: "high_complete_third_objective", 2: "medium_single_objective"}
    )
    frame["gap_evidence_status"] = "not_applicable_complete"
    frame["gap_next_action"] = "none"
    phcu = frame["source_family"].eq("Mendeley_PHCU_nonisocyanate")
    frame.loc[phcu, "gap_evidence_status"] = "no_cyclic_modality_in_local_source"
    frame.loc[phcu, "gap_next_action"] = "targeted_external_search_for_same_PHCU_family_or_new_experiment"
    standard = frame["source_family"].eq("Zenodo_标准化弹性体表征")
    frame.loc[standard, "multiobjective_status"] = (
        "three_objectives_relaxation_proxy"
    )
    frame.loc[standard, "completion_priority"] = (
        "complete_proxy_not_direct_cycle"
    )
    frame.loc[standard, "gap_evidence_status"] = "stress_relaxation_proxy_available_no_direct_cycles"
    frame.loc[standard, "gap_next_action"] = "retain_relaxation_as_auxiliary_and_search_direct_cycles"
    recycled = frame["material_key"].isin(["P4MCL-1.6k-31HS", "P4PrCL-1.6k-31HS", "PCL-1.6k-31HS", "PMCL-1k-46HS"])
    frame.loc[recycled, "gap_evidence_status"] = "no_matching_tga_in_local_source"
    frame.loc[recycled, "gap_next_action"] = "search_repolymerized_material_tga_or_measurement"
    empty_tensile = frame["material_key"].eq("PMCL-2k-18HS")
    frame.loc[empty_tensile, "gap_evidence_status"] = "source_tensile_sheet_empty"
    frame.loc[empty_tensile, "gap_next_action"] = "do_not_impute_search_independent_tensile_source"
    thermal_only = frame["material_key"].eq("PMCL-1k-44HS")
    frame.loc[thermal_only, "gap_evidence_status"] = "no_exact_matching_mechanical_formulation"
    frame.loc[thermal_only, "gap_next_action"] = "do_not_merge_with_PMCL_1k_46HS_search_exact_mechanical_match"
    qub_missing_cycle = frame["source_family"].eq("QUB_生物基三重自修复TPU") & frame[
        "material_key"
    ].isin(["P35", "P45"])
    frame.loc[qub_missing_cycle, "gap_evidence_status"] = "no_direct_cycle_for_exact_formulation"
    frame.loc[qub_missing_cycle, "gap_next_action"] = "search_or_measure_exact_formulation_cycles"
    qub_hdo = frame["source_family"].eq("QUB_生物基三重自修复TPU") & frame[
        "material_key"
    ].eq("P40-HDO")
    frame.loc[qub_hdo, "gap_evidence_status"] = "no_tga_or_cycle_for_HDO_control"
    frame.loc[qub_hdo, "gap_next_action"] = "search_article_SI_or_measure_HDO_control_thermal_and_cycles"
    dib = frame["source_family"].eq("DataInBrief_交联形状记忆PU")
    frame.loc[dib, "multiobjective_status"] = "three_objectives_transfer"
    frame.loc[dib, "completion_priority"] = "complete_transfer_not_core_tpu"
    frame.loc[dib, "gap_evidence_status"] = "three_targets_transfer_only_no_direct_shape_recovery"
    frame.loc[dib, "gap_next_action"] = "retain_low_transfer_weight_and_search_direct_TPU_recovery"
    commercial = frame["source_family"].eq("Mendeley_商业TPU温度疲劳")
    frame.loc[commercial, "gap_evidence_status"] = "no_thermal_degradation_for_exact_commercial_grade"
    frame.loc[commercial, "gap_next_action"] = "search_exact_grade_TGA_or_measurement"
    shape_memory_blends = frame["source_family"].eq(
        "JMERD_Elastollan1154D_PCL_shape_memory"
    )
    frame.loc[shape_memory_blends, "gap_evidence_status"] = (
        "direct_shape_memory_only_no_curve_toughness_or_TGA"
    )
    frame.loc[shape_memory_blends, "gap_next_action"] = (
        "search_same_blend_raw_tensile_and_TGA_or_measurement"
    )
    tecoflex = frame["source_family"].eq("Zenodo_Tecoflex_EG60D_NIC")
    frame.loc[tecoflex, "gap_evidence_status"] = (
        "direct_TGA_and_partial_tensile_no_cyclic_recovery"
    )
    frame.loc[tecoflex, "gap_next_action"] = (
        "search_exact_formulation_cyclic_or_recovery_measurement"
    )
    iir = frame["source_family"].eq("Mendeley_IIROH_PU_durability")
    frame.loc[iir, "gap_evidence_status"] = (
        "cyclic_and_hydrolytic_durability_no_TGA"
    )
    frame.loc[iir, "gap_next_action"] = (
        "search_exact_formulation_TGA_and_close_numeric_code_semantics"
    )
    tpu95a = frame["source_family"].eq("Mendeley_eSUN_eTPU95A")
    frame.loc[tpu95a, "gap_evidence_status"] = (
        "relaxation_proxy_only_no_absolute_tensile_stress_or_TGA"
    )
    frame.loc[tpu95a, "gap_next_action"] = (
        "resolve_cross_section_or_find_stress_curve_and_exact_grade_TGA"
    )
    foam = frame["source_family"].eq("MaterialsCloud_Sawbones_PCF20")
    frame.loc[foam, "gap_evidence_status"] = (
        "rigid_PU_foam_toughness_transfer_only_not_TPU"
    )
    frame.loc[foam, "gap_next_action"] = (
        "retain_as_transfer_and_do_not_seek_TPU_core_completion"
    )
    tpu1301 = frame["source_family"].eq(
        "Zenodo_TPU1301热黏弹黏塑本构"
    )
    frame.loc[tpu1301, "gap_evidence_status"] = (
        "direct_tensile_and_relaxation_proxy_no_TGA"
    )
    frame.loc[tpu1301, "gap_next_action"] = (
        "retain_application_proxy_and_search_exact_grade_TGA"
    )
    return frame


def write(f):
    f.to_csv(OUT, index=False, encoding="utf-8-sig", lineterminator="\n")
    MAN.write_text(
        json.dumps(
            {
                "counts": {
                    "material_rows": len(f),
                    "two_objective_or_more": int(
                        (f.objective_coverage_count >= 2).sum()
                    ),
                    "three_objective": int((f.objective_coverage_count == 3).sum()),
                },
                "inputs": {k: sha(v) for k, v in INPUTS.items()},
                "output_sha256": sha(OUT),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def check(f):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / OUT.name
        f.to_csv(p, index=False, encoding="utf-8-sig", lineterminator="\n")
        if sha(p) != sha(OUT):
            raise SystemExit("多目标材料索引不一致")
    print("多目标材料索引检查通过")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--检查", action="store_true")
    a = p.parse_args()
    f = build_release()
    if a.检查:
        check(f)
    else:
        write(f)
        print(
            json.dumps(
                {
                    "materials": len(f),
                    "triple": int((f.objective_coverage_count == 3).sum()),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
