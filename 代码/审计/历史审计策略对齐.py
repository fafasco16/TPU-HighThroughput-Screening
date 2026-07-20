"""将三份早期内容审计中的非约束权重建议对齐到统一多保真策略。

本脚本不重算或改写任何科学测量，只更新三个既有 ``内容审计摘要.json`` 中的
准入/权重与拆分语义。首次运行前必须匹配已记录的原摘要 SHA-256；后续运行必须
匹配本脚本写入的策略对齐元数据。输出采用固定白名单、重解析路径拒绝、fsync 与
同目录原子替换，连续复跑应保持字节一致。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable


SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parents[2]
DATA_ROOT = PROJECT_ROOT / "01_原始数据" / "外部数据" / "新增开放数据"
POLICY_VERSION = "multi-fidelity-admission-weight-v0.2.9"
ALIGNMENT_BASELINE_DATE = "2026-07-20"

TARGETS = {
    "dft_deblocking": {
        "path": DATA_ROOT
        / "Zenodo_TPU回收封端剂DFT与机器学习"
        / "内容审计摘要.json",
        "pre_alignment_sha256": "2afeea72e73893a22047f6dd1e871d76e2e240efdd4c8accfee6c2e94b467cdf",
        "accepted_previous_alignment_sha256": {
            "multi-fidelity-admission-weight-v0.2.6":
                "720f186ff9b633ab6c329dbee7a9d3882bd9a7c9765a554ae4f8b1fbc835b1e7",
            "multi-fidelity-admission-weight-v0.2.7":
                "c5b0457ec25e539876f4777de12ad1504f6130b897c5a9c1f9301480634b4300",
            "multi-fidelity-admission-weight-v0.2.8":
                "c91e4d569affb8f7e8d99f8a299ea21e7f15880fd4bec7b1c20eba6573a527de",
        },
    },
    "plant_foam_aging": {
        "path": DATA_ROOT
        / "Mendeley_植物基PU泡沫温湿老化压缩"
        / "内容审计摘要.json",
        "pre_alignment_sha256": "cc9f386ebde5f00fdbe40fb1e70212dceeb95fd5b60964e25eca17ea1086fa25",
        "accepted_previous_alignment_sha256": {
            "multi-fidelity-admission-weight-v0.2.6":
                "c776662dd70be10c1197744c21e87efe927904eafa75f77eb051ffa28305df19",
            "multi-fidelity-admission-weight-v0.2.7":
                "3bf89e7da69662f108050334eaa769fac24fce8ef8c7712f092fa7043cd36f8e",
            "multi-fidelity-admission-weight-v0.2.8":
                "a933f13caa7960989cbeed4e22303c4ac9a7c1584af062964b492485edac5a01",
        },
    },
    "printable_composite": {
        "path": DATA_ROOT
        / "Zenodo_可打印自愈可回收PU生物电子"
        / "内容审计摘要.json",
        "pre_alignment_sha256": "10ba39558c0ad49bd42c5a3246de64fbf907bcd45da0884c220028e18d9ef0d8",
        "accepted_previous_alignment_sha256": {
            "multi-fidelity-admission-weight-v0.2.6":
                "01a61308f830250da96a3f763b6124e21eed926e7a23ff3830b5d4dbdc0a44de",
            "multi-fidelity-admission-weight-v0.2.7":
                "deb9c76f37834039ab770981b259e54b76a790e6c29216b6ee63e9dddcd115da",
            "multi-fidelity-admission-weight-v0.2.8":
                "f1748f36f1eb23c170a37d3fb4e834f0867004c0265ea703b377b2b9964b3c95",
        },
    },
}
AUDIT_OUTPUTS = frozenset(Path(item["path"]) for item in TARGETS.values())


class AuditBlocked(RuntimeError):
    """输入摘要、路径边界或旧权重建议不符合已审计预期。"""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def assert_audit_output(path: Path) -> Path:
    if path not in AUDIT_OUTPUTS:
        raise AuditBlocked(f"拒绝写入白名单以外路径：{path}")
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise AuditBlocked(f"拒绝覆盖符号链接或联接点：{path}")
    if not path.is_file():
        raise AuditBlocked(f"策略对齐目标不是既有普通文件：{path}")
    parent = path.parent
    if parent.is_symlink() or (
        hasattr(parent, "is_junction") and parent.is_junction()
    ):
        raise AuditBlocked(f"拒绝通过重解析目录写入：{parent}")
    if os.path.normcase(str(parent.resolve(strict=True))) != os.path.normcase(
        str(parent.absolute())
    ):
        raise AuditBlocked(f"策略对齐输出目录发生重解析：{parent}")
    return path


def atomic_write(path: Path, payload: bytes) -> None:
    resolved = assert_audit_output(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{resolved.name}.", suffix=".audit.tmp", dir=resolved.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, resolved)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def load_payload(key: str) -> tuple[Path, dict[str, Any]]:
    meta = TARGETS[key]
    path = assert_audit_output(Path(meta["path"]))
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    alignment = payload.get("统一准入策略对齐")
    if alignment is None:
        actual = sha256_bytes(raw)
        if actual != meta["pre_alignment_sha256"]:
            raise AuditBlocked(
                f"{key}: 初始摘要SHA-256漂移，expected={meta['pre_alignment_sha256']}, actual={actual}"
            )
    else:
        aligned_policy_version = alignment.get("策略版本")
        if aligned_policy_version != POLICY_VERSION:
            accepted_previous = meta["accepted_previous_alignment_sha256"]
            expected_previous_hash = accepted_previous.get(aligned_policy_version)
            actual = sha256_bytes(raw)
            if expected_previous_hash is None or actual != expected_previous_hash:
                raise AuditBlocked(
                    f"{key}: 旧策略摘要不在精确迁移白名单，"
                    f"policy={aligned_policy_version!r}, actual={actual}"
                )
        if alignment.get("对齐前摘要SHA256") != meta["pre_alignment_sha256"]:
            raise AuditBlocked(f"{key}: 对齐前摘要SHA-256证据不一致")
    return path, payload


def record_alignment(payload: dict[str, Any], key: str) -> None:
    payload["统一准入策略对齐"] = {
        "策略版本": POLICY_VERSION,
        "规范基准日": ALIGNMENT_BASELINE_DATE,
        "对齐前摘要SHA256": TARGETS[key]["pre_alignment_sha256"],
        "权威性": "本对象中的统一策略上限取代早期来源级启发式权重建议；当前未创建训练拆分或逐观测权重。",
        "科学测量与计数是否改写": False,
    }


def align_dft(payload: dict[str, Any]) -> None:
    previous = payload["future_weighting_recommendation_no_training_performed"]
    if "policy_authority" not in previous:
        if previous.get("experimental_Tdeblock_training_label_weight") != 1.0:
            raise AuditBlocked("DFT早期实验标签权重建议发生漂移")
        if previous.get("verified_DFT_descriptor_pretraining_or_auxiliary_task_weight_range") != [
            0.6,
            0.8,
        ]:
            raise AuditBlocked("DFT早期描述符权重区间发生漂移")
    payload["future_weighting_recommendation_no_training_performed"] = {
        "policy_authority": POLICY_VERSION,
        "policy_status": "design_only_no_training_split_or_weight_materialized",
        "experimental_Tdeblock_label_ceiling": 1.0,
        "locked_holdout_fit_weight": 0.0,
        "experimentally_mapped_DFT_QoI_or_calibrated_descriptor_ceiling": 0.50,
        "unmapped_cross_scale_DFT_descriptor_ceiling": 0.25,
        "Gaussian_input_unconverged_or_unparsed_output_ceiling": 0.0,
        "split_group_key": "dataset_doi|compound_id",
        "compound_normalization": "同一compound的计算层级、荷电态、加合物、构象与Gaussian作业数不得增加总权重或跨折。",
        "important": "DFT描述符是协变量/辅助任务，不是合成Tdeblock标签；实验Tdeblock按独立实验保真层计权。",
    }


def align_plant_foam(payload: dict[str, Any]) -> None:
    decision = payload["数据库判定"]
    previous = decision["建议权重"]
    if "统一策略版本" not in previous:
        if previous.get("通用PU力学曲线表征预训练") != "0.4-0.6，且按来源与批次成组":
            raise AuditBlocked("植物基泡沫早期通用迁移权重建议发生漂移")
        if previous.get("PU泡沫温湿老化专门任务") != "0.8-1.0；建议整个数据源留作外部验证或leave-one-batch-out":
            raise AuditBlocked("植物基泡沫早期专门任务权重建议发生漂移")
    decision["建议权重"] = {
        "统一策略版本": POLICY_VERSION,
        "TPU配方到块体性能主任务": 0.0,
        "通用PU力学曲线表征预训练上限": 0.35,
        "PU泡沫温湿老化专门任务上限": 0.35,
        "解释": "0.35取代早期0.4–1.0启发式区间；该来源是单一未知配方远域泡沫，按温湿组与批次拆分或整源外部验证。",
    }


def align_printable_composite(payload: dict[str, Any]) -> None:
    admission = payload["admission"]
    transfer = admission["transfer_mechanical"]
    if transfer.get("suggested_relative_weight") not in (0.35, 0.25):
        raise AuditBlocked("可打印复合PU机械迁移权重建议发生漂移")
    transfer["suggested_relative_weight"] = 0.25
    transfer["policy_authority"] = POLICY_VERSION
    transfer["scope"] = (
        "8条应力-应变曲线；0.25为交联复合远域统一上限，必须保留复合物、交联、加工、回收和测试条件标志"
    )
    admission["split_group_key"] = (
        "dataset_doi|base_pu_synthesis_batch_unknown|composite_formulation"
    )
    admission["split_rule"] = "同一基础PU/复合配方的加工、自愈与原始/回收状态必须同折。"
    rules = admission["weighting_rules"]
    rules[0] = (
        "观测身份按source_doi+base_PU_batch+composite_formulation+processing_state+specimen_id记录；"
        "训练拆分使用更粗的dataset_doi+base_PU_batch+composite_formulation键。"
    )


ALIGNERS: dict[str, Callable[[dict[str, Any]], None]] = {
    "dft_deblocking": align_dft,
    "plant_foam_aging": align_plant_foam,
    "printable_composite": align_printable_composite,
}


def main() -> int:
    results = []
    for key, aligner in ALIGNERS.items():
        path, payload = load_payload(key)
        aligner(payload)
        record_alignment(payload, key)
        encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        atomic_write(path, encoded)
        results.append(
            {
                "source": key,
                "relative_path": path.relative_to(PROJECT_ROOT).as_posix(),
                "bytes": len(encoded),
                "sha256": sha256_bytes(encoded),
            }
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
