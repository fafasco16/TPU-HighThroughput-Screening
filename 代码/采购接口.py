"""统一的构件采购目录查询适配层。

目录命中只用于安排人工核验优先级，不会自动把构件释放到实验。
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

import pandas as pd
import yaml
from rdkit import Chem


EVIDENCE_FIELDS = (
    "component_id",
    "inchi_key",
    "interface_name",
    "query_status",
    "evidence_level",
    "query_value",
    "result_count",
    "result_summary",
    "result_url",
    "queried_utc",
    "cache_key",
    "limitations",
)


@dataclass(frozen=True)
class ComponentQuery:
    component_id: str
    canonical_smiles: str
    inchi_key: str

    @classmethod
    def from_values(cls, component_id: str, smiles: str) -> "ComponentQuery":
        mol = Chem.MolFromSmiles(str(smiles))
        if mol is None:
            raise ValueError(f"无法解析构件SMILES: {component_id}")
        return cls(str(component_id), Chem.MolToSmiles(mol), Chem.MolToInchiKey(mol))


@dataclass(frozen=True)
class InterfaceEvidence:
    component_id: str
    inchi_key: str
    interface_name: str
    query_status: str
    evidence_level: str
    query_value: str
    result_count: int
    result_summary: str
    result_url: str
    queried_utc: str
    cache_key: str
    limitations: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Adapter(Protocol):
    name: str

    def query(self, component: ComponentQuery) -> InterfaceEvidence: ...


def utc_now() -> str:
    return pd.Timestamp.now(tz="UTC").isoformat()


def stable_cache_key(interface_name: str, inchi_key: str, variant: str = "") -> str:
    raw = f"{interface_name}|{inchi_key}|{variant}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_config(path: str | Path) -> dict[str, Any]:
    config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    required = {
        "pubchem_vendor",
        "molbloom",
        "smallworld",
        "emolecules_link",
        "aggregation",
    }
    if not isinstance(config, dict) or required.difference(config):
        raise ValueError(f"采购接口配置缺少分区: {sorted(required.difference(config or {}))}")
    return config


class EvidenceCache:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.rows: dict[str, dict[str, Any]] = {}
        if self.path.is_file():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("采购接口缓存必须是JSON对象")
            self.rows = {str(key): dict(value) for key, value in payload.items()}

    def get(self, key: str) -> InterfaceEvidence | None:
        row = self.rows.get(key)
        return InterfaceEvidence(**row) if row is not None else None

    def put(self, evidence: InterfaceEvidence) -> None:
        self.rows[evidence.cache_key] = evidence.to_dict()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(
            json.dumps(self.rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


def _read_json(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "TPU-procurement-adapters/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def parse_pubchem_vendor_sources(payload: Mapping[str, Any]) -> tuple[int, list[str], list[str]]:
    categories = payload.get("SourceCategories", {}).get("Categories", [])
    category = next(
        (
            item
            for item in categories
            if str(item.get("Category", "")).strip().casefold() == "chemical vendors"
        ),
        None,
    )
    if category is None:
        return 0, [], []
    sources = category.get("Sources", [])
    names = sorted(
        {
            str(source.get("SourceName") or source.get("Name") or "").strip()
            for source in sources
            if str(source.get("SourceName") or source.get("Name") or "").strip()
        }
    )
    urls = sorted(
        {
            str(source.get("SourceURL") or source.get("URL") or "").strip()
            for source in sources
            if str(source.get("SourceURL") or source.get("URL") or "").strip()
        }
    )
    return len(sources), names, urls


class PubChemVendorAdapter:
    name = "pubchem_vendor"

    def __init__(self, *, timeout_seconds: float = 30.0):
        self.timeout = float(timeout_seconds)

    def query(self, component: ComponentQuery) -> InterfaceEvidence:
        key = stable_cache_key(self.name, component.inchi_key)
        cid_url = (
            "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchikey/"
            f"{component.inchi_key}/cids/JSON/"
        )
        try:
            payload = _read_json(cid_url, self.timeout)
            cids = payload.get("IdentifierList", {}).get("CID", [])
            if not cids:
                raise LookupError("PubChem CID not found")
            cid = int(cids[0])
            vendor_url = (
                "https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/categories/compound/"
                f"{cid}/JSON/?response_type=view"
            )
            vendor_payload = _read_json(vendor_url, self.timeout)
            count, names, urls = parse_pubchem_vendor_sources(vendor_payload)
            return InterfaceEvidence(
                component.component_id,
                component.inchi_key,
                self.name,
                "completed",
                "vendor_directory",
                str(cid),
                count,
                ";".join(names),
                f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}",
                utc_now(),
                key,
                "PubChem目录可能重复或过期，不代表实时库存；vendor_urls=" + ";".join(urls),
            )
        except urllib.error.HTTPError as error:
            status = "not_found" if error.code == 404 else f"http_error_{error.code}"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, LookupError) as error:
            status = "not_found" if isinstance(error, LookupError) else f"query_error_{type(error).__name__}"
        return InterfaceEvidence(
            component.component_id,
            component.inchi_key,
            self.name,
            status,
            "vendor_directory",
            "",
            0,
            "",
            cid_url,
            utc_now(),
            key,
            "查询失败或无CID；不能据此断言不可购买",
        )


class MolBloomAdapter:
    name = "molbloom"

    def __init__(self, *, catalog: str, canonicalize: bool = True, check_common: bool = True):
        self.catalog = str(catalog)
        self.canonicalize = bool(canonicalize)
        self.check_common = bool(check_common)

    def query(self, component: ComponentQuery) -> InterfaceEvidence:
        key = stable_cache_key(self.name, component.inchi_key, self.catalog)
        try:
            program = (
                "import json,sys,molbloom;"
                "print(json.dumps(bool(molbloom.buy(sys.argv[1],catalog=sys.argv[2],"
                "canonicalize=sys.argv[3]=='1',check_common=sys.argv[4]=='1'))))"
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    program,
                    component.canonical_smiles,
                    self.catalog,
                    "1" if self.canonicalize else "0",
                    "1" if self.check_common else "0",
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(f"isolated_process_exit_{completed.returncode}")
            hit = bool(json.loads(completed.stdout.strip()))
            status = "completed"
            summary = f"catalog={self.catalog};hit={str(hit).lower()}"
            count = int(hit)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            status = f"query_error_{type(error).__name__}"
            summary = ""
            count = 0
        false_positive_note = (
            "随包mini目录假阳性率约7%；"
            if self.catalog == "zinc-instock-mini"
            else "完整zinc-instock过滤器假阳性率约0.03%；"
        )
        return InterfaceEvidence(
            component.component_id,
            component.inchi_key,
            self.name,
            status,
            "catalog_bloom_prefilter",
            self.catalog,
            count,
            summary,
            "https://zinc20.docking.org/",
            utc_now(),
            key,
            false_positive_note + "ZINC20目录为历史快照，不代表实时库存",
        )


class EMoleculesLinkAdapter:
    name = "emolecules_link"

    def query(self, component: ComponentQuery) -> InterfaceEvidence:
        key = stable_cache_key(self.name, component.inchi_key, "exact")
        url = (
            "https://www.emolecules.com/search/?q="
            + urllib.parse.quote(component.canonical_smiles, safe="")
            + "&t=1"
        )
        return InterfaceEvidence(
            component.component_id,
            component.inchi_key,
            self.name,
            "link_generated",
            "manual_exact_structure_search",
            component.canonical_smiles,
            0,
            "exact_structure_search_url",
            url,
            utc_now(),
            key,
            "eMolecules没有公开无密钥库存API；必须打开链接人工核验",
        )


class SmallWorldAdapter:
    name = "smallworld"

    def __init__(self, *, distance: int = 0, database: str = "zinc"):
        self.distance = int(distance)
        self.database = str(database)

    def query(self, component: ComponentQuery) -> InterfaceEvidence:
        key = stable_cache_key(self.name, component.inchi_key, f"{self.database}:{self.distance}")
        try:
            from smallworld_api import SmallWorld

            client = SmallWorld(update_dbs=True)
            database = client.ZINC_dataset if self.database == "zinc" else client.REAL_dataset
            results = client.search(component.canonical_smiles, db=database, dist=self.distance)
            relevant = (
                results.loc[pd.to_numeric(results["dist"], errors="coerce").eq(0)]
                if self.distance == 0 and "dist" in results.columns
                else results
            )
            count = int(len(relevant))
            summary = "no_hits" if relevant.empty else ";".join(map(str, relevant.head(5).index.tolist()))
            status = "completed"
            result_url = "https://sw.docking.org/search.html"
        except Exception as error:  # public service has several transport-specific errors
            count = 0
            summary = ""
            status = f"query_error_{type(error).__name__}"
            result_url = "https://sw.docking.org/search.html"
        return InterfaceEvidence(
            component.component_id,
            component.inchi_key,
            self.name,
            status,
            "public_purchasable_space_search",
            f"database={self.database};distance={self.distance}",
            count,
            summary,
            result_url,
            utc_now(),
            key,
            "非官方公共服务，不用于大批量或实时库存；相似命中不是同一物质",
        )


def query_with_cache(
    adapter: Adapter,
    component: ComponentQuery,
    cache: EvidenceCache,
    *,
    refresh: bool = False,
) -> tuple[InterfaceEvidence, bool]:
    probe_key = stable_cache_key(
        adapter.name,
        component.inchi_key,
        getattr(adapter, "catalog", "")
        or f"{getattr(adapter, 'database', '')}:{getattr(adapter, 'distance', '')}".strip(":"),
    )
    if not refresh:
        cached = cache.get(probe_key)
        if cached is not None:
            return cached, True
    result = adapter.query(component)
    cache.put(result)
    return result, False


def merge_evidence(
    components: pd.DataFrame,
    evidence: pd.DataFrame,
    *,
    experiment_release_status: str,
) -> pd.DataFrame:
    required = {"candidate_id", "canonical_smiles", "inchi_key"}
    if required.difference(components.columns):
        raise ValueError(f"构件摘要缺少字段: {sorted(required.difference(components.columns))}")
    if evidence.empty:
        raise ValueError("没有接口证据")
    frame = components.copy()
    signal_rows: list[dict[str, Any]] = []
    for component_id, rows in evidence.groupby("component_id", sort=False):
        by_name = {row.interface_name: row for row in rows.itertuples(index=False)}
        pubchem = by_name.get("pubchem_vendor")
        molbloom = by_name.get("molbloom")
        smallworld = by_name.get("smallworld")
        emolecules = by_name.get("emolecules_link")
        pubchem_hit = bool(pubchem and pubchem.query_status == "completed" and pubchem.result_count > 0)
        molbloom_hit = bool(molbloom and molbloom.query_status == "completed" and molbloom.result_count > 0)
        smallworld_hit = bool(smallworld and smallworld.query_status == "completed" and smallworld.result_count > 0)
        count = int(pubchem_hit) + int(molbloom_hit) + int(smallworld_hit)
        signal_rows.append(
            {
                "candidate_id": component_id,
                "pubchem_vendor_hit": pubchem_hit,
                "pubchem_vendor_record_count": int(pubchem.result_count) if pubchem else 0,
                "pubchem_cid": pubchem.query_value if pubchem else "",
                "molbloom_hit": molbloom_hit,
                "molbloom_catalog": molbloom.query_value if molbloom else "",
                "smallworld_hit": smallworld_hit,
                "smallworld_result_count": int(smallworld.result_count) if smallworld else 0,
                "emolecules_exact_url": emolecules.result_url if emolecules else "",
                "catalog_signal_count": count,
                "unified_catalog_status": (
                    "no_catalog_signal"
                    if count == 0
                    else "single_catalog_signal"
                    if count == 1
                    else "multiple_catalog_signals"
                ),
                "direct_commercial_evidence_status": "required",
                "experiment_release_status": experiment_release_status,
            }
        )
    signals = pd.DataFrame(signal_rows)
    conflicting = [column for column in signals.columns if column != "candidate_id" and column in frame.columns]
    if conflicting:
        frame = frame.drop(columns=conflicting)
    output = frame.merge(signals, on="candidate_id", how="left", validate="one_to_one")
    if output["unified_catalog_status"].isna().any():
        raise ValueError("存在没有接口证据的构件")
    return output


def package_versions() -> dict[str, str]:
    names = ("molbloom", "smallworld-api", "pandas", "rdkit")
    result = {}
    for name in names:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = "not_installed"
    return result


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
