"""物化6个PHCU配方的拉伸与TGA双目标端点。"""

from __future__ import annotations
import argparse
import hashlib
import json
import tempfile
from pathlib import Path
import pandas as pd
from 接入DRUM机械回收 import _derive_endpoints
from 提取TGA热稳定端点 import extract_tga_endpoints

ROOT = Path(__file__).resolve().parents[1]
BASE = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第八批实验_非异氰酸酯PHCU热塑性聚氨酯"
    / "只读导出"
)
TENSILE = BASE / "拉伸.opj.1.dat"
TGA = BASE / "TGA.opj.1.dat"
OUT = ROOT / "结果" / "定向筛选" / "PHCU双目标端点.csv"
MAN = ROOT / "结果" / "定向筛选" / "PHCU双目标发布清单.json"
FORMULATIONS = ["PHCU10", "PHCU20", "PHCU30", "PHCU40", "PHCU50", "PHCU70"]


def sha(p):
    d = hashlib.sha256()
    d.update(p.read_bytes())
    return d.hexdigest()


def read(p):
    return (
        pd.read_csv(p, sep=";", engine="python")
        .iloc[:, :12]
        .apply(pd.to_numeric, errors="coerce")
    )


def build_release():
    a, b = read(TENSILE), read(TGA)
    rows = []
    for i, f in enumerate(FORMULATIONS):
        tensile = pd.DataFrame(
            {"strain": a.iloc[:, 2 * i], "stress": a.iloc[:, 2 * i + 1]}
        ).dropna()
        thermal = pd.DataFrame(
            {"temperature": b.iloc[:, 2 * i], "mass": b.iloc[:, 2 * i + 1]}
        ).dropna()
        rows.append(
            {
                "source_id": "source_mendeley_bvv43yk29c_v1",
                "formulation_id": f,
                "hu_mol_percent": int(f.replace("PHCU", "")),
                **_derive_endpoints(tensile[["stress", "strain"]]),
                **extract_tga_endpoints(thermal),
                "polymer_family": "nonisocyanate_PHCU",
                "chemistry_mapping_status": "composition_series_mapped_exact_recipe_missing",
                "usage_mode": "auxiliary_train",
                "source_locator": f"{TENSILE.relative_to(ROOT).as_posix()}+{TGA.relative_to(ROOT).as_posix()}#columns={2 * i + 1}-{2 * i + 2}",
                "license": "CC-BY-4.0",
                "citation_keys": "reference-142;reference-143;reference-144",
            }
        )
    return pd.DataFrame(rows)


def write(f):
    f.to_csv(OUT, index=False, encoding="utf-8-sig", lineterminator="\n")
    MAN.write_text(
        json.dumps(
            {
                "counts": {
                    "formulations": len(f),
                    "tensile_endpoints": len(f),
                    "tga_endpoints": len(f),
                },
                "inputs": {"tensile": sha(TENSILE), "tga": sha(TGA)},
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
            raise SystemExit("PHCU双目标输出不一致")
    print("PHCU双目标检查通过")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--检查", action="store_true")
    x = p.parse_args()
    f = build_release()
    if x.检查:
        check(f)
    else:
        write(f)
        print(json.dumps({"formulations": len(f)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
