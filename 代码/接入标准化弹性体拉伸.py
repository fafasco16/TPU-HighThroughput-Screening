"""提取标准化弹性体来源中两种热塑性弹性体的拉伸端点。"""

from __future__ import annotations
import argparse
import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path
import pandas as pd
from 接入DRUM机械回收 import _derive_endpoints

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "Zenodo_标准化弹性体表征"
    / "Uniaxial tension.zip"
)
OUT = ROOT / "结果" / "定向筛选" / "标准化热塑性弹性体拉伸端点.csv"
MAN = ROOT / "结果" / "定向筛选" / "标准化热塑性弹性体拉伸发布清单.json"


def sha(p):
    d = hashlib.sha256()
    d.update(p.read_bytes())
    return d.hexdigest()


def build_release():
    rows = []
    with zipfile.ZipFile(SOURCE) as z:
        for name in z.namelist():
            material = next(
                (
                    m
                    for m in ("Cheetah", "Filaflex 60A")
                    if name.startswith(f"Uniaxial tension/{m} - ")
                ),
                None,
            )
            if not material:
                continue
            frame = pd.read_csv(io.BytesIO(z.read(name))).rename(
                columns={"Strain (%)": "strain", "Stress (MPa)": "stress"}
            )
            ep = _derive_endpoints(frame[["stress", "strain"]].dropna())
            replicate = int(Path(name).stem.rsplit(" - ", 1)[1])
            rows.append(
                {
                    "source_id": "source_zenodo_14983287_v1",
                    "formulation_id": material,
                    "sample_id": f"{material}_tensile_{replicate}",
                    "replicate_index": replicate,
                    **ep,
                    "material_join_key": f"zenodo14983287|{material}",
                    "chemistry_mapping_status": "commercial_grade_identity_only",
                    "usage_mode": "auxiliary_train",
                    "source_locator": f"{SOURCE.relative_to(ROOT).as_posix()}#{name}",
                    "license": "source_license_pending_manifest_confirmation",
                    "citation_keys": "zenodo-14983287",
                }
            )
    return (
        pd.DataFrame(rows)
        .sort_values(["formulation_id", "replicate_index"])
        .reset_index(drop=True)
    )


def write(f):
    f.to_csv(OUT, index=False, encoding="utf-8-sig", lineterminator="\n")
    MAN.write_text(
        json.dumps(
            {
                "counts": {
                    "curve_rows": len(f),
                    "material_count": f.formulation_id.nunique(),
                },
                "source_sha256": sha(SOURCE),
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
            raise SystemExit("标准化弹性体拉伸输出不一致")
    print("标准化弹性体拉伸检查通过")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--检查", action="store_true")
    a = p.parse_args()
    f = build_release()
    if a.检查:
        check(f)
    else:
        write(f)
        print(json.dumps({"curves": len(f)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
