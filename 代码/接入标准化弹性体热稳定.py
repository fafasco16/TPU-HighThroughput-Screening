"""从标准化弹性体Zenodo来源提取两种热塑性弹性体TGA端点。"""

from __future__ import annotations
import argparse
import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path
import pandas as pd
from 提取TGA热稳定端点 import extract_tga_endpoints

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "Zenodo_标准化弹性体表征"
    / "Thermal degradation.zip"
)
OUT = ROOT / "结果" / "定向筛选" / "标准化热塑性弹性体TGA端点.csv"
MAN = ROOT / "结果" / "定向筛选" / "标准化热塑性弹性体TGA发布清单.json"


def sha(path):
    d = hashlib.sha256()
    d.update(path.read_bytes())
    return d.hexdigest()


def build_release():
    rows = []
    with zipfile.ZipFile(SOURCE) as z:
        for material in ["Cheetah", "Filaflex 60A"]:
            name = f"Thermal degradation/{material}.csv"
            raw = z.read(name)
            curve = pd.read_csv(io.BytesIO(raw)).rename(
                columns={"Temperature (C)": "temperature", "Mass (%)": "mass"}
            )
            ep = extract_tga_endpoints(curve)
            rows.append(
                {
                    "source_id": "source_zenodo_14983287_v1",
                    "formulation_id": material,
                    "material_class": "commercial_thermoplastic_elastomer",
                    "chemistry_mapping_status": "commercial_grade_identity_only",
                    "usage_mode": "auxiliary_train",
                    **ep,
                    "source_member": name,
                    "member_sha256": hashlib.sha256(raw).hexdigest(),
                    "source_locator": f"{SOURCE.relative_to(ROOT).as_posix()}#{name}",
                    "license": "CC-BY-4.0",
                    "citation_keys": "reference-40;reference-41",
                }
            )
    return pd.DataFrame(rows)


def write(frame):
    frame.to_csv(OUT, index=False, encoding="utf-8-sig", lineterminator="\n")
    payload = {
        "counts": {
            "material_count": len(frame),
            "t5_count": int(frame.T5_degC.notna().sum()),
            "t10_count": int(frame.T10_degC.notna().sum()),
            "t50_count": int(frame.T50_degC.notna().sum()),
        },
        "source_sha256": sha(SOURCE),
        "output_sha256": sha(OUT),
    }
    MAN.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def check(frame):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / OUT.name
        frame.to_csv(p, index=False, encoding="utf-8-sig", lineterminator="\n")
        if sha(p) != sha(OUT):
            raise SystemExit("标准化弹性体TGA输出不一致")
    print("标准化热塑性弹性体TGA检查通过")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--检查", action="store_true")
    a = p.parse_args()
    f = build_release()
    if a.检查:
        check(f)
    else:
        write(f)
        print(json.dumps({"materials": len(f)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
