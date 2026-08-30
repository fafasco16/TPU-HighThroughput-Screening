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
    "std_tensile": D / "标准化热塑性弹性体拉伸端点.csv",
    "std_tga": D / "标准化热塑性弹性体TGA端点.csv",
    "phcu": D / "PHCU双目标端点.csv",
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
            "Zenodo_标准化弹性体表征",
            "zenodo-14983287",
            "commercial_grade_identity_only",
            "std_tensile",
            None,
            "std_tga",
        ),
    ]
    frames = {k: pd.read_csv(v) for k, v in INPUTS.items()}
    for source, cites, mapping, tkey, ckey, hkey in specs:
        materials = set(frames[tkey].formulation_id) | set(frames[hkey].formulation_id)
        if ckey:
            materials |= set(frames[ckey].formulation_id)
        for m in sorted(materials):
            tc = int((frames[tkey].formulation_id == m).sum())
            cc = int((frames[ckey].formulation_id == m).sum()) if ckey else 0
            hc = int((frames[hkey].formulation_id == m).sum())
            coverage = sum(x > 0 for x in (tc, cc, hc))
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
                "citation_keys": row.citation_keys,
            }
        )
    return (
        pd.DataFrame(rows)
        .sort_values(
            ["objective_coverage_count", "source_family", "material_key"],
            ascending=[False, True, True],
        )
        .reset_index(drop=True)
    )


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
