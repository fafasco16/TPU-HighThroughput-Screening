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
    }
    actions = {
        "component_topology_mapped_partial": "补PMCL区域异构分布与逐配方完整投料",
        "composition_series_mapped": "补逐配方投料和唯一聚合物结构",
        "monomer_set_composition_mapped": "补逐配方投料、嵌段长度和端基",
        "commercial_grade_only": "补商业牌号化学组成或TDS",
        "formulation_code_only": "从正文或SI恢复组分和比例",
        "material_code_only": "恢复材料代码对应配方",
        "commercial_identity_unresolved": "确认商业TPU基体身份",
    }
    for package, target, data, manifest, license_, mapping in SPECS:
        dp, mp = D / data, D / manifest
        f = pd.read_csv(dp)
        material_col = "formulation_id" if "formulation_id" in f else "material_code"
        rows.append(
            {
                "package_id": package,
                "target_family": target,
                "data_file": data,
                "manifest_file": manifest,
                "row_count": len(f),
                "material_count": f[material_col].nunique(dropna=True),
                "mapping_tier": mapping,
                "mapping_completeness_score": scores[mapping],
                "next_mapping_action": actions[mapping],
                "license": license_,
                "data_sha256": sha(dp),
                "manifest_sha256": sha(mp),
            }
        )
    return pd.DataFrame(rows)


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
