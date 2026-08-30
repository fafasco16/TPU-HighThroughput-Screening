from __future__ import annotations

import json
import sys
import types
import urllib.error
from pathlib import Path

import pandas as pd
import pytest

import 查询构件采购 as runner
import 采购接口 as api


ROOT = Path(__file__).resolve().parents[2]


def _component(component_id: str = "bdo", smiles: str = "OCCCCO") -> api.ComponentQuery:
    return api.ComponentQuery.from_values(component_id, smiles)


def _evidence(component_id: str, interface: str, count: int = 1) -> api.InterfaceEvidence:
    component = _component(component_id)
    return api.InterfaceEvidence(
        component_id,
        component.inchi_key,
        interface,
        "completed" if interface != "emolecules_link" else "link_generated",
        "test",
        "value",
        count,
        "summary",
        "https://example.org",
        "2026-08-24T00:00:00+00:00",
        api.stable_cache_key(interface, component.inchi_key),
        "limitations",
    )


def test_component_query_and_config(tmp_path: Path):
    component = _component()
    assert component.component_id == "bdo"
    assert component.inchi_key == "WERYXYBDKMZEQL-UHFFFAOYSA-N"
    with pytest.raises(ValueError, match="无法解析"):
        api.ComponentQuery.from_values("bad", "not smiles")
    bad = tmp_path / "bad.yaml"
    bad.write_text("pubchem_vendor: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="缺少分区"):
        api.load_config(bad)
    assert api.load_config(ROOT / "配置" / "采购接口.yaml")["schema_version"] == "1.0"


def test_cache_roundtrip(tmp_path: Path):
    path = tmp_path / "cache.json"
    cache = api.EvidenceCache(path)
    row = _evidence("bdo", "pubchem_vendor")
    assert cache.get(row.cache_key) is None
    cache.put(row)
    cache.save()
    loaded = api.EvidenceCache(path)
    assert loaded.get(row.cache_key) == row
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON对象"):
        api.EvidenceCache(bad)


def test_pubchem_source_parser():
    payload = {
        "SourceCategories": {
            "Categories": [
                {
                    "Category": "Chemical Vendors",
                    "Sources": [
                        {"SourceName": "Vendor A", "SourceURL": "https://a.example"},
                        {"SourceName": "Vendor A", "SourceURL": "https://a.example"},
                        {"Name": "Vendor B", "URL": "https://b.example"},
                    ],
                }
            ]
        }
    }
    assert api.parse_pubchem_vendor_sources(payload) == (
        3,
        ["Vendor A", "Vendor B"],
        ["https://a.example", "https://b.example"],
    )
    assert api.parse_pubchem_vendor_sources({}) == (0, [], [])


def test_pubchem_adapter_success_and_errors(monkeypatch: pytest.MonkeyPatch):
    payloads = iter(
        [
            {"IdentifierList": {"CID": [8064]}},
            {
                "SourceCategories": {
                    "Categories": [
                        {"Category": "Chemical Vendors", "Sources": [{"SourceName": "Supplier"}]}
                    ]
                }
            },
        ]
    )
    monkeypatch.setattr(api, "_read_json", lambda url, timeout: next(payloads))
    result = api.PubChemVendorAdapter().query(_component())
    assert set(api.EVIDENCE_FIELDS) == set(result.to_dict())
    assert result.query_status == "completed" and result.result_count == 1

    monkeypatch.setattr(api, "_read_json", lambda url, timeout: {"IdentifierList": {"CID": []}})
    assert api.PubChemVendorAdapter().query(_component()).query_status == "not_found"

    def http_error(url, timeout):
        raise urllib.error.HTTPError(url, 503, "bad", None, None)

    monkeypatch.setattr(api, "_read_json", http_error)
    assert api.PubChemVendorAdapter().query(_component()).query_status == "http_error_503"


def test_molbloom_and_emolecules_adapters(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        api.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(returncode=0, stdout="true\n", stderr=""),
    )
    result = api.MolBloomAdapter(catalog="zinc-instock").query(_component())
    assert result.query_status == "completed" and result.result_count == 1
    link = api.EMoleculesLinkAdapter().query(_component())
    assert link.query_status == "link_generated"
    assert "t=1" in link.result_url and "q=OCCCCO" in link.result_url

    monkeypatch.setattr(
        api.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(returncode=1, stdout="", stderr="bad"),
    )
    assert api.MolBloomAdapter(catalog="bad").query(_component()).query_status.startswith("query_error_")


def test_smallworld_adapter_success_and_failure(monkeypatch: pytest.MonkeyPatch):
    class FakeSmallWorld:
        ZINC_dataset = "zinc"
        REAL_dataset = "real"

        def __init__(self, update_dbs=True):
            pass

        def search(self, smiles, db, dist):
            return pd.DataFrame([{"smiles": smiles}])

    monkeypatch.setitem(sys.modules, "smallworld_api", types.SimpleNamespace(SmallWorld=FakeSmallWorld))
    result = api.SmallWorldAdapter(distance=0).query(_component())
    assert result.query_status == "completed" and result.result_count == 1

    class BrokenSmallWorld(FakeSmallWorld):
        def search(self, smiles, db, dist):
            raise RuntimeError("offline")

    monkeypatch.setitem(sys.modules, "smallworld_api", types.SimpleNamespace(SmallWorld=BrokenSmallWorld))
    assert api.SmallWorldAdapter().query(_component()).query_status == "query_error_RuntimeError"


def test_query_cache_skips_second_remote_call(tmp_path: Path):
    class CountingAdapter:
        name = "counting"

        def __init__(self):
            self.calls = 0

        def query(self, component):
            self.calls += 1
            return _evidence(component.component_id, self.name)

    adapter = CountingAdapter()
    cache = api.EvidenceCache(tmp_path / "cache.json")
    first, hit1 = api.query_with_cache(adapter, _component(), cache)
    second, hit2 = api.query_with_cache(adapter, _component(), cache)
    assert first == second and not hit1 and hit2 and adapter.calls == 1


def test_merge_evidence_is_fail_closed():
    component = _component()
    components = pd.DataFrame(
        [{"candidate_id": component.component_id, "canonical_smiles": component.canonical_smiles, "inchi_key": component.inchi_key}]
    )
    rows = pd.DataFrame(
        [
            _evidence("bdo", "pubchem_vendor", 3).to_dict(),
            _evidence("bdo", "molbloom", 1).to_dict(),
            _evidence("bdo", "emolecules_link", 0).to_dict(),
        ]
    )
    merged = api.merge_evidence(
        components,
        rows,
        experiment_release_status="blocked_pending_quote_sds_and_local_approval",
    )
    assert merged.loc[0, "unified_catalog_status"] == "multiple_catalog_signals"
    assert merged.loc[0, "experiment_release_status"].startswith("blocked")
    with pytest.raises(ValueError, match="缺少字段"):
        api.merge_evidence(pd.DataFrame({"x": [1]}), rows, experiment_release_status="blocked")
    with pytest.raises(ValueError, match="没有接口证据"):
        api.merge_evidence(components, rows.iloc[0:0], experiment_release_status="blocked")


def test_load_components_and_existing_pubchem(tmp_path: Path):
    source = tmp_path / "components.csv"
    pd.DataFrame([{"candidate_id": "a", "canonical_smiles": "OCCCCO"}]).to_csv(source, index=False)
    assert runner.load_components(source).loc[0, "inchi_key"] == "WERYXYBDKMZEQL-UHFFFAOYSA-N"
    reality_source = tmp_path / "reality.csv"
    pd.DataFrame(
        [
            {"component_id": "a", "canonical_smiles": "OCCCCO"},
            {"component_id": "polymer", "canonical_smiles": ""},
        ]
    ).to_csv(reality_source, index=False)
    assert runner.load_components(reality_source)["candidate_id"].tolist() == ["a"]
    bad = tmp_path / "bad.csv"
    pd.DataFrame({"x": [1]}).to_csv(bad, index=False)
    with pytest.raises(ValueError, match="缺少字段"):
        runner.load_components(bad)
    duplicate = tmp_path / "dup.csv"
    pd.DataFrame(
        [{"candidate_id": "a", "canonical_smiles": "OCCCCO"}, {"candidate_id": "a", "canonical_smiles": "CCO"}]
    ).to_csv(duplicate, index=False)
    with pytest.raises(ValueError, match="唯一"):
        runner.load_components(duplicate)

    pubchem = tmp_path / "pubchem.csv"
    pd.DataFrame(
        [
            {
                "candidate_id": "a",
                "inchi_key": "KEY",
                "query_status": "completed",
                "pubchem_cid": 1,
                "pubchem_vendor_record_count": 2,
                "pubchem_vendor_names": "Vendor",
                "queried_utc": "now",
            }
        ]
    ).to_csv(pubchem, index=False)
    assert runner.existing_pubchem_evidence(pubchem)[0]["result_count"] == 2
    assert runner.existing_pubchem_evidence(tmp_path / "missing.csv") == []


def test_run_interfaces_and_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    source = tmp_path / "components.csv"
    pd.DataFrame(
        [
            {"candidate_id": "a", "canonical_smiles": "OCCCCO"},
            {"candidate_id": "b", "canonical_smiles": "CC(O)C(C)O"},
        ]
    ).to_csv(source, index=False)
    config = tmp_path / "config.yaml"
    config.write_text(
        """schema_version: '1.0'
cache_path: cache.json
pubchem_vendor: {enabled: false}
molbloom: {enabled: false}
smallworld: {integrated: true, enabled_by_default: false, max_candidates: 1, distance: 0, database: zinc}
emolecules_link: {enabled: true}
aggregation:
  experiment_release_status: blocked_pending_quote_sds_and_local_approval
""",
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence.csv"
    summary = tmp_path / "summary.csv"
    manifest = tmp_path / "manifest.json"
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    result = runner.run_interfaces(source, config, evidence, summary, manifest)
    assert result["status"] == "completed"
    assert result["interfaces"] == ["emolecules_link"]
    assert evidence.is_file() and summary.is_file() and manifest.is_file()
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert saved["outputs"]["summary"]["rows"] == 2

    assert runner.main(
        [
            "--输入", str(source),
            "--配置", str(config),
            "--证据输出", str(tmp_path / "evidence2.csv"),
            "--摘要输出", str(tmp_path / "summary2.csv"),
            "--清单输出", str(tmp_path / "manifest2.json"),
        ]
    ) == 0
    assert '"status": "completed"' in capsys.readouterr().out


def test_versions_and_hash(tmp_path: Path):
    path = tmp_path / "x.txt"
    path.write_text("abc", encoding="utf-8")
    assert api.sha256_file(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    versions = api.package_versions()
    assert set(versions) == {"molbloom", "smallworld-api", "pandas", "rdkit"}
