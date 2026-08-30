"""汇总定向扩库发布包，防止文件和数量口径失控。"""

from __future__ import annotations
import argparse
import hashlib
import json
import tempfile
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "结果" / "定向筛选"
OUT = D / "扩充数据总账.csv"
MAN = D / "扩充数据总账发布清单.json"
SPECS = [
    (
        "drum_tensile",
        "toughness",
        "DRUM机械回收拉伸端点.csv",
        "DRUM机械回收发布清单.json",
        "CC0-1.0",
        "component_topology_mapped_partial",
    ),
    (
        "drum_cycle",
        "cyclic_recovery",
        "DRUM机械回收循环端点.csv",
        "DRUM机械回收循环发布清单.json",
        "CC0-1.0",
        "component_topology_mapped_partial",
    ),
    (
        "drum_tga",
        "thermal_stability",
        "DRUM机械回收TGA端点.csv",
        "DRUM机械回收TGA发布清单.json",
        "CC0-1.0",
        "component_topology_mapped_partial",
    ),
    (
        "low_ceiling_cycle",
        "cyclic_recovery",
        "TPUU循环端点.csv",
        "TPUU循环端点发布清单.json",
        "CC0-1.0",
        "formulation_code_only",
    ),
    (
        "low_ceiling_tpuu_tga",
        "thermal_stability",
        "低天花板TPUU热稳定端点.csv",
        "低天花板TPUU热稳定发布清单.json",
        "CC0-1.0",
        "formulation_code_only",
    ),
    (
        "zenodo_porous",
        "toughness",
        "Zenodo多孔TPU拉伸端点.csv",
        "Zenodo多孔TPU发布清单.json",
        "CC-BY-4.0",
        "commercial_identity_unresolved",
    ),
    (
        "figshare_healing",
        "toughness_and_healing",
        "Figshare强韧自愈端点.csv",
        "Figshare强韧自愈发布清单.json",
        "CC-BY-4.0",
        "material_code_only",
    ),
    (
        "standard_tensile",
        "toughness",
        "标准化热塑性弹性体拉伸端点.csv",
        "标准化热塑性弹性体拉伸发布清单.json",
        "CC-BY-4.0",
        "commercial_grade_only",
    ),
    (
        "standard_tga",
        "thermal_stability",
        "标准化热塑性弹性体TGA端点.csv",
        "标准化热塑性弹性体TGA发布清单.json",
        "CC-BY-4.0",
        "commercial_grade_only",
    ),
    (
        "standard_relaxation_proxy",
        "stress_relaxation_recovery_proxy",
        "标准热塑性弹性体松弛端点.csv",
        "标准热塑性弹性体松弛发布清单.json",
        "CC-BY-4.0",
        "commercial_grade_only",
    ),
    (
        "phcu_dual",
        "toughness_and_thermal",
        "PHCU双目标端点.csv",
        "PHCU双目标发布清单.json",
        "CC-BY-4.0",
        "monomer_set_composition_mapped",
    ),
    (
        "date_seed_tga",
        "thermal_stability",
        "TGA热稳定端点.csv",
        "TGA端点发布清单.json",
        "CC-BY-4.0",
        "composition_series_mapped",
    ),
    (
        "qub_self_healing_tensile",
        "toughness_and_self_healing",
        "QUB生物基自修复TPU拉伸端点.csv",
        "QUB生物基自修复TPU发布清单.json",
        "CC-BY-4.0",
        "monomer_set_hard_segment_mapped",
    ),
    (
        "qub_self_healing_cycle_proxy",
        "cyclic_hysteresis_proxy",
        "QUB生物基自修复TPU循环端点.csv",
        "QUB生物基自修复TPU发布清单.json",
        "CC-BY-4.0",
        "monomer_set_hard_segment_mapped",
    ),
    (
        "qub_self_healing_tga",
        "thermal_stability",
        "QUB生物基自修复TPUTGA端点.csv",
        "QUB生物基自修复TPU发布清单.json",
        "CC-BY-4.0",
        "monomer_set_hard_segment_mapped",
    ),
    (
        "dib_shape_memory_tensile",
        "toughness_transfer",
        "DataInBrief形状记忆PU拉伸端点.csv",
        "DataInBrief形状记忆PU发布清单.json",
        "CC-BY-4.0",
        "monomer_set_molar_composition_mapped",
    ),
    (
        "dib_shape_memory_cycle_proxy",
        "cyclic_stress_retention_hysteresis_transfer",
        "DataInBrief形状记忆PU循环端点.csv",
        "DataInBrief形状记忆PU发布清单.json",
        "CC-BY-4.0",
        "monomer_set_molar_composition_mapped",
    ),
    (
        "dib_shape_memory_thermal",
        "thermal_stability_transfer",
        "DataInBrief形状记忆PU热稳定端点.csv",
        "DataInBrief形状记忆PU发布清单.json",
        "CC-BY-4.0",
        "monomer_set_molar_composition_mapped",
    ),
    (
        "commercial_tpu_impact_fatigue",
        "compression_energy_and_cyclic_fatigue",
        "商业TPU温度疲劳端点.csv",
        "商业TPU温度疲劳发布清单.json",
        "CC-BY-4.0",
        "commercial_grade_only",
    ),
    (
        "commercial_tpu_energy_recovery_pairs",
        "ambient_energy_recovery",
        "商业TPU恢复配对端点.csv",
        "商业TPU温度疲劳发布清单.json",
        "CC-BY-4.0",
        "commercial_grade_only",
    ),
    (
        "elastollan_pcl_shape_memory_summary",
        "direct_shape_fixity_and_recovery",
        "ElastollanPCL形状记忆端点.csv",
        "ElastollanPCL形状记忆发布清单.json",
        "paper-license-unverified-facts-only",
        "commercial_grade_blend_fraction_mapped",
    ),
    (
        "tecoflex_nic_multiperformance",
        "partial_toughness_and_thermal_stability",
        "Tecoflex药物复合TPU多性能端点.csv",
        "Tecoflex药物复合TPU发布清单.json",
        "CC-BY-4.0",
        "commercial_grade_additive_fraction_mapped",
    ),
    (
        "iir_oh_100cycle_endpoints",
        "cyclic_hysteresis_and_peak_stress_retention",
        "IIR-OH聚氨酯循环端点.csv",
        "IIR-OH聚氨酯循环耐久发布清单.json",
        "CC-BY-4.0",
        "polymer_family_diisocyanate_code_mapped",
    ),
    (
        "iir_oh_hydrolytic_retention",
        "hydrolytic_mechanical_retention",
        "IIR-OH聚氨酯水解保持端点.csv",
        "IIR-OH聚氨酯循环耐久发布清单.json",
        "CC-BY-4.0",
        "polymer_family_diisocyanate_code_mapped",
    ),
    (
        "tpu95a_load_extension_auxiliary",
        "load_extension_auxiliary_not_toughness",
        "TPU95A载荷伸长端点.csv",
        "TPU95A力学代理发布清单.json",
        "CC-BY-4.0",
        "commercial_grade_only",
    ),
    (
        "tpu95a_relaxation_proxy",
        "stress_relaxation_recovery_proxy",
        "TPU95A应力松弛端点.csv",
        "TPU95A力学代理发布清单.json",
        "CC-BY-4.0",
        "commercial_grade_only",
    ),
    (
        "pcf20_foam_tension_fracture",
        "toughness_transfer",
        "PCF20泡沫拉伸断裂端点.csv",
        "PCF20泡沫断裂发布清单.json",
        "CC-BY-4.0",
        "commercial_foam_grade_density_mapped",
    ),
    (
        "tpu1301_tensile_application",
        "toughness_application",
        "TPU1301拉伸端点.csv",
        "TPU1301机械代理发布清单.json",
        "CC-BY-4.0",
        "commercial_grade_only",
    ),
    (
        "tpu1301_relaxation_proxy",
        "stress_relaxation_recovery_proxy",
        "TPU1301应力松弛端点.csv",
        "TPU1301机械代理发布清单.json",
        "CC-BY-4.0",
        "commercial_grade_only",
    ),
    (
        "biobased_vitrimer_tensile_transfer",
        "break_strength_elongation_transfer",
        "生物基玻璃体拉伸端点.csv",
        "生物基玻璃体发布清单.json",
        "CC-BY-4.0",
        "formulation_code_synthesis_family_mapped",
    ),
    (
        "biobased_vitrimer_relaxation_transfer",
        "stress_relaxation_recovery_proxy",
        "生物基玻璃体松弛端点.csv",
        "生物基玻璃体发布清单.json",
        "CC-BY-4.0",
        "formulation_code_synthesis_family_mapped",
    ),
    (
        "biobased_vitrimer_tga_transfer",
        "thermal_stability_transfer",
        "生物基玻璃体TGA端点.csv",
        "生物基玻璃体发布清单.json",
        "CC-BY-4.0",
        "formulation_code_synthesis_family_mapped",
    ),
]


def sha(p):
    d = hashlib.sha256()
    d.update(p.read_bytes())
    return d.hexdigest()


def build_release():
    rows = []
    scores = {
        "component_topology_mapped_partial": 0.85,
        "composition_series_mapped": 0.60,
        "monomer_set_composition_mapped": 0.72,
        "commercial_grade_only": 0.35,
        "formulation_code_only": 0.30,
        "material_code_only": 0.25,
        "commercial_identity_unresolved": 0.15,
        "monomer_set_hard_segment_mapped": 0.88,
        "monomer_set_molar_composition_mapped": 0.92,
        "commercial_grade_blend_fraction_mapped": 0.75,
        "commercial_grade_additive_fraction_mapped": 0.78,
        "polymer_family_diisocyanate_code_mapped": 0.70,
        "commercial_foam_grade_density_mapped": 0.60,
        "formulation_code_synthesis_family_mapped": 0.20,
    }
    actions = {
        "component_topology_mapped_partial": "补PMCL区域异构分布与逐配方完整投料",
        "composition_series_mapped": "补逐配方投料和唯一聚合物结构",
        "monomer_set_composition_mapped": "补逐配方投料、嵌段长度和端基",
        "commercial_grade_only": "补商业牌号化学组成或TDS",
        "formulation_code_only": "从正文或SI恢复组分和比例",
        "material_code_only": "恢复材料代码对应配方",
        "commercial_identity_unresolved": "确认商业TPU基体身份",
        "monomer_set_hard_segment_mapped": "补Pripol 2033与HEDS逐配方精确摩尔投料和唯一结构",
        "monomer_set_molar_composition_mapped": "补物理试样跨工作簿身份；保持交联PU迁移层边界",
        "commercial_grade_blend_fraction_mapped": "补原始曲线、重复数、不确定性及同配方TGA",
        "commercial_grade_additive_fraction_mapped": "补完整断裂曲线和同配方循环恢复数据",
        "polymer_family_diisocyanate_code_mapped": "闭合数字配方代码语义与水解协议并补同配方TGA",
        "commercial_foam_grade_density_mapped": "仅作泡沫断裂迁移；补完整化学牌号而不并入TPU核心",
        "formulation_code_synthesis_family_mapped": "仅作动态网络迁移；补论文Table 1缩写映射且不并入TPU核心",
    }
    layers = {
        "drum_tensile": "core_tpuu_experimental",
        "drum_cycle": "core_tpuu_experimental",
        "drum_tga": "core_tpuu_experimental",
        "low_ceiling_cycle": "core_tpuu_experimental",
        "low_ceiling_tpuu_tga": "core_tpuu_experimental",
        "zenodo_porous": "auxiliary_experimental",
        "figshare_healing": "core_tpu_experimental",
        "standard_tensile": "commercial_elastomer_auxiliary",
        "standard_tga": "commercial_elastomer_auxiliary",
        "standard_relaxation_proxy": "commercial_elastomer_auxiliary",
        "phcu_dual": "polyurethane_adjacent_experimental",
        "date_seed_tga": "pu_pir_transfer",
        "qub_self_healing_tensile": "core_tpu_experimental",
        "qub_self_healing_cycle_proxy": "core_tpu_experimental",
        "qub_self_healing_tga": "core_tpu_experimental",
        "dib_shape_memory_tensile": "polyurethane_transfer",
        "dib_shape_memory_cycle_proxy": "polyurethane_transfer",
        "dib_shape_memory_thermal": "polyurethane_transfer",
        "commercial_tpu_impact_fatigue": "core_tpu_application_experimental",
        "commercial_tpu_energy_recovery_pairs": "core_tpu_application_experimental",
        "elastollan_pcl_shape_memory_summary": "core_tpu_blend_published_summary",
        "tecoflex_nic_multiperformance": "core_tpu_composite_experimental",
        "iir_oh_100cycle_endpoints": "polyurethane_adjacent_experimental",
        "iir_oh_hydrolytic_retention": "polyurethane_adjacent_experimental",
        "tpu95a_load_extension_auxiliary": "core_tpu_application_experimental",
        "tpu95a_relaxation_proxy": "core_tpu_application_experimental",
        "pcf20_foam_tension_fracture": "polyurethane_foam_transfer",
        "tpu1301_tensile_application": "core_tpu_application_experimental",
        "tpu1301_relaxation_proxy": "core_tpu_application_experimental",
        "biobased_vitrimer_tensile_transfer": "dynamic_network_vitrimer_transfer",
        "biobased_vitrimer_relaxation_transfer": "dynamic_network_vitrimer_transfer",
        "biobased_vitrimer_tga_transfer": "dynamic_network_vitrimer_transfer",
    }
    for package, target, data, manifest, license_, mapping in SPECS:
        dp, mp = D / data, D / manifest
        f = pd.read_csv(dp)
        material_col = next(
            column
            for column in (
                "formulation_id",
                "material_code",
                "material_grade",
                "material_id",
            )
            if column in f
        )
        target_bonus = 0.10 if "and" in target else 0.05
        priority_score = scores[mapping] + target_bonus + min(0.10, f[material_col].nunique(dropna=True) / 200)
        rows.append(
            {
                "package_id": package,
                "target_family": target,
                "data_file": data,
                "manifest_file": manifest,
                "row_count": len(f),
                "material_count": f[material_col].nunique(dropna=True),
                "mapping_tier": mapping,
                "model_admission_layer": layers[package],
                "source_independence_status": (
                    "historical_mirror_rematerialized_zero_new_source"
                    if package.startswith("tpu95a_")
                    else "governed_materialization"
                ),
                "mapping_completeness_score": scores[mapping],
                "next_mapping_action": actions[mapping],
                "expansion_priority_score": round(priority_score, 4),
                "license": license_,
                "data_sha256": sha(dp),
                "manifest_sha256": sha(mp),
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["expansion_priority_score", "material_count", "package_id"],
        ascending=[False, False, True],
    ).reset_index(drop=True)


def write(f):
    f.to_csv(OUT, index=False, encoding="utf-8-sig", lineterminator="\n")
    MAN.write_text(
        json.dumps(
            {
                "package_count": len(f),
                "total_rows": int(f.row_count.sum()),
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
            raise SystemExit("扩充数据总账不一致")
    print("扩充数据总账检查通过")


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
                {"packages": len(f), "rows": int(f.row_count.sum())}, ensure_ascii=False
            )
        )


if __name__ == "__main__":
    main()
