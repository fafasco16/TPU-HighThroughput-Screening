"""发布 TPU 虚拟配方预审视图和第一层 DFT 复核队列。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
import yaml

import 候选预审 as precheck


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "配置" / "候选预审.yaml"
DEFAULT_CANDIDATE_DIR = ROOT / "候选"
MANIFEST_NAME = "候选预审发布清单.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_config(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("候选预审配置必须是映射")
    return value


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    compression = {"method": "gzip", "mtime": 0} if path.suffix == ".gz" else None
    frame.to_csv(path, index=False, compression=compression, float_format="%.12g")


def build(config_path: Path, candidate_dir: Path) -> dict[str, object]:
    config = load_config(config_path)
    candidate_manifest_path = candidate_dir / "候选发布清单.json"
    candidate_manifest = json.loads(candidate_manifest_path.read_text(encoding="utf-8"))
    if candidate_manifest["candidate_release_id"] != config["source_candidate_release_id"]:
        raise ValueError("候选发布 ID 与预审配置不一致")
    inputs = {
        "candidate_manifest": candidate_manifest_path,
        "components": candidate_dir / "候选构件库.csv",
        "combinations": candidate_dir / "候选组合库.csv",
        "formulations": candidate_dir / "可合成配方候选.csv.gz",
        "config": config_path,
    }
    components = pd.read_csv(inputs["components"])
    combinations = pd.read_csv(inputs["combinations"])
    formulations = pd.read_csv(inputs["formulations"])
    if not combinations["combination_id"].is_unique:
        raise ValueError("候选组合 combination_id 不唯一")
    if set(formulations["combination_id"]) != set(combinations["combination_id"]):
        raise ValueError("配方与候选组合的 combination_id 集合不闭合")
    annotated = precheck.annotate_formulations(
        formulations, components, config["manual_review"]
    )
    queue = precheck.select_dft_queue(annotated, config["dft_queue"])
    outputs = {
        "precheck": candidate_dir / "候选预审.csv.gz",
        "dft_md_queue": candidate_dir / "DFT_MD复核队列.csv",
    }
    _write_csv(annotated, outputs["precheck"])
    _write_csv(queue, outputs["dft_md_queue"])
    manifest = {
        "precheck_release_id": config["release_id"],
        "source_candidate_release_id": config["source_candidate_release_id"],
        "purpose": "manual_precheck_and_tier1_dft_queue",
        "counts": {
            "precheck_rows": len(annotated),
            "dft_queue_rows": len(queue),
        },
        "config": config,
        "inputs": {
            key: {"path": _relative(path), "sha256": sha256(path), "bytes": path.stat().st_size}
            for key, path in inputs.items()
        },
        "outputs": {
            key: {"path": _relative(path), "sha256": sha256(path), "bytes": path.stat().st_size}
            for key, path in outputs.items()
        },
        "interpretation_limits": [
            "结构警示只触发SDS/EHS人工复核，不是危险分类或安全结论。",
            "采购状态和文献新颖性未联网逐条核验，全部保持not_checked。",
            "DFT队列是结构多样性复核队列，不是性能排名。",
            "宏二醇只有代理结构，真实身份与Mn/Mw/PDI闭合前不启动块体MD。",
        ],
    }
    manifest_path = candidate_dir / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def verify(candidate_dir: Path) -> None:
    manifest_path = candidate_dir / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for section in ("inputs", "outputs"):
        for entry in manifest[section].values():
            path = ROOT / entry["path"]
            if not path.is_file() or path.stat().st_size != entry["bytes"] or sha256(path) != entry["sha256"]:
                raise ValueError(f"候选预审发布核验失败: {path}")
    precheck_frame = pd.read_csv(ROOT / manifest["outputs"]["precheck"]["path"])
    queue = pd.read_csv(ROOT / manifest["outputs"]["dft_md_queue"]["path"])
    expected = manifest["counts"]
    if len(precheck_frame) != expected["precheck_rows"] or not precheck_frame["formulation_id"].is_unique:
        raise ValueError("候选预审行数或 formulation_id 唯一性异常")
    if len(queue) != expected["dft_queue_rows"] or not queue["formulation_id"].is_unique:
        raise ValueError("DFT 队列行数或 formulation_id 唯一性异常")
    if not precheck_frame["procurement_status"].eq("not_checked").all():
        raise ValueError("采购状态出现未经核验的结论")
    if not precheck_frame["literature_novelty_status"].eq("not_checked").all():
        raise ValueError("文献新颖性出现未经核验的结论")
    if not queue["md_stage"].eq("on_hold_pending_real_macrodiol_identity_Mn_Mw_PDI").all():
        raise ValueError("MD 暂停门被意外打开")
    if not queue["performance_claim_status"].eq("no_performance_claim").all():
        raise ValueError("DFT 队列出现未经支持的性能结论")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--配置", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--候选目录", type=Path, default=DEFAULT_CANDIDATE_DIR)
    parser.add_argument("--检查", action="store_true")
    args = parser.parse_args()
    candidate_dir = args.候选目录.resolve()
    if args.检查:
        verify(candidate_dir)
        print(f"候选预审发布核验通过: {candidate_dir}")
        return
    manifest = build(args.配置.resolve(), candidate_dir)
    print(json.dumps(manifest["counts"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
