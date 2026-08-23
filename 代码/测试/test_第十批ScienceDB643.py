import csv
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE = (
    ROOT
    / "数据"
    / "原始"
    / "外部数据"
    / "新增开放数据"
    / "第十批实验_ScienceDB643"
)


class TestScienceDB643AuditArtifacts(unittest.TestCase):
    def test_audit_counts_and_hash(self):
        audit = json.loads((BASE / "审计结果.json").read_text(encoding="utf-8"))
        self.assertEqual(audit["status"], "pass")
        self.assertEqual(audit["sample_rows"], 643)
        self.assertEqual(audit["columns"], 24)
        self.assertEqual(audit["unique_ssid"], 643)
        self.assertEqual(audit["targets"], ["logYM", "logTS", "logEB"])
        self.assertEqual(len(audit["source_integrity"]["sha256"]), 64)

    def test_derived_rows_keep_transformed_label_provenance(self):
        path = BASE / "派生" / "PUE643_标准化643.csv"
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 643)
        self.assertEqual(len({row["SSID"] for row in rows}), 643)
        self.assertEqual({row["is_experimental"] for row in rows}, {"true"})
        self.assertEqual({row["label_origin"] for row in rows}, {"experimental_transformed"})
        self.assertEqual({row["source_family"] for row in rows}, {"PUE-643"})

    def test_manifest_locks_doi_and_license(self):
        manifest = json.loads((BASE / "来源清单.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source"]["dataset_doi"], "10.57760/sciencedb.14957")
        self.assertEqual(manifest["source"]["license"], "CC BY 4.0")


if __name__ == "__main__":
    unittest.main()
