from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE = (
    PROJECT_ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第十批实验_ScienceDB643"
)
RAW = BASE / "原始" / "PUE643_YM-TS-EB.csv"
DERIVED = BASE / "派生" / "PUE643_标准化643.csv"
MANIFEST = BASE / "来源清单.json"
AUDIT_OUTPUT = BASE / "审计结果.json"

EXPECTED_COLUMNS = [
    "SSID", "ZS_CHS", "ZS_R", "ZS_log_Tr1K", "ZS_log_Tr2K", "PMStep", "Form_Method",
    "ZS_log_CSArea", "ZS_log_StrainRate", "ZS_log_PO_MW", "ZS_log_FCVm", "ZS_FCCED",
    "ZS_log_Fchi", "ZS_SS_TPSA_norm", "ZS_SS_MolLogP_norm", "ZS_HS_BertzCT",
    "ZS_SS_VSA_EState8", "ZS_SS_PEOE_VSA8", "ZS_log_HS_NumNHCO_norm",
    "ZS_FC_NumHAcceptors_norm", "ZS_FC_RingCount_norm", "logYM", "logTS", "logEB",
]


def file_hash(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audit() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected_file = manifest["file"]
    if not RAW.is_file():
        raise FileNotFoundError(RAW)
    integrity = {
        "bytes": RAW.stat().st_size,
        "md5": file_hash(RAW, "md5"),
        "sha256": file_hash(RAW, "sha256"),
    }
    for key in ("bytes", "md5", "sha256"):
        if integrity[key] != expected_file[key]:
            raise ValueError(f"ScienceDB source integrity mismatch: {key}")

    with RAW.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != EXPECTED_COLUMNS:
            raise ValueError("unexpected ScienceDB PUE643 header")
        raw_rows = list(reader)

    duplicate_header_rows = [row for row in raw_rows if [row[name] for name in EXPECTED_COLUMNS] == EXPECTED_COLUMNS]
    rows = [row for row in raw_rows if [row[name] for name in EXPECTED_COLUMNS] != EXPECTED_COLUMNS]
    if len(rows) != 643:
        raise ValueError(f"expected 643 samples, got {len(rows)}")
    ssids = [row["SSID"] for row in rows]
    if len(set(ssids)) != len(ssids):
        raise ValueError("SSID is not unique")
    for row in rows:
        for name in EXPECTED_COLUMNS[1:]:
            value = float(row[name])
            if not math.isfinite(value):
                raise ValueError(f"non-finite {name} at SSID={row['SSID']}")

    output_fields = [
        "source_dataset_doi", "source_article_doi", "source_family", "record_fidelity",
        "label_origin", "is_experimental", "input_feature_state",
    ] + EXPECTED_COLUMNS
    tmp = DERIVED.with_suffix(DERIVED.suffix + ".tmp")
    DERIVED.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_fields)
        writer.writeheader()
        for row in rows:
            output = {
                "source_dataset_doi": "10.57760/sciencedb.14957",
                "source_article_doi": "10.1007/s10118-022-2838-6",
                "source_family": "PUE-643",
                "record_fidelity": "published_experimental_transformed_table",
                "label_origin": "experimental_transformed",
                "is_experimental": "true",
                "input_feature_state": "published_standardized_and_or_log_transformed",
            }
            output.update(row)
            writer.writerow(output)
    os.replace(tmp, DERIVED)

    result = {
        "status": "pass",
        "source_integrity": integrity,
        "raw_rows_after_header": len(raw_rows),
        "duplicate_header_rows_removed": len(duplicate_header_rows),
        "sample_rows": len(rows),
        "columns": len(EXPECTED_COLUMNS),
        "unique_ssid": len(set(ssids)),
        "first_ssid": ssids[0],
        "last_ssid": ssids[-1],
        "targets": ["logYM", "logTS", "logEB"],
        "label_origin": "experimental_transformed",
        "source_family_policy": "canonical completion of PUE-643 family; do not count as independent from the article/ESI",
        "derived_file": {
            "relative_path": str(DERIVED.relative_to(BASE)),
            "rows": len(rows),
            "sha256": file_hash(DERIVED, "sha256"),
        },
    }
    AUDIT_OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, indent=2))
