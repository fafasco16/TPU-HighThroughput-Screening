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
    / "第十批计算_OMG"
)


class TestOMGAuditArtifacts(unittest.TestCase):
    def test_audit_summary_and_provenance(self):
        audit = json.loads((BASE / "审计结果.json").read_text(encoding="utf-8"))
        self.assertEqual(audit["status"], "pass")
        self.assertEqual(audit["raw_row_counts"]["OMG_polymers.csv"], 12886131)
        self.assertEqual(audit["pu_subset"]["rows"], 100584)
        self.assertEqual(audit["reaction_id_join"]["property_rows"], 47676)
        self.assertEqual(audit["reaction_id_join"]["joined_rows"], 47676)
        self.assertEqual(audit["reaction_id_join"]["coverage"], 1.0)
        self.assertEqual(audit["reaction_id_join"]["pu_computed_rows"], 2086)
        self.assertFalse(audit["label_policy"]["is_experimental"])
        self.assertFalse(audit["label_policy"]["is_12m_ml_prediction"])

    def test_computed_table_is_explicitly_nonexperimental(self):
        path = BASE / "派生" / "OMG_PU_计算属性_2086.csv"
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
        self.assertEqual(len(rows), 2086)
        self.assertEqual({row["record_fidelity"] for row in rows}, {"direct_computational_reference"})
        self.assertEqual({row["is_experimental"] for row in rows}, {"false"})
        self.assertEqual({row["label_origin"] for row in rows}, {"computed_not_experimental_not_model_prediction"})

    def test_field_dictionary_has_25_units_and_methods(self):
        fields = json.loads((BASE / "计算字段字典.json").read_text(encoding="utf-8"))["fields"]
        self.assertEqual(len(fields), 25)
        self.assertTrue(all(item["unit"] and item["method"] for item in fields))


if __name__ == "__main__":
    unittest.main()
