"""审计文件与摘要链的构造、校验原语。

本模块不依赖数据仓库，种子数据与业务服务都可以安全导入，避免循环依赖。
约定：先构造完整文件并算出校验和，再由调用方切换归档状态；任何一步失败都不允许
出现「状态已是已归档、文件却不存在」的半成品。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

# 完整审计文件必须保留的原始字段；清理动作只能改可见范围，这些字段一个都不能丢。
AUDIT_FIELDS = [
    "记录编号",
    "关联端点",
    "请求时间",
    "响应状态",
    "耗时毫秒",
    "返回行数",
    "错误信息",
    "重试次数",
]

# 摘要面向列表页与归档页：保留排障与授权判断所需的最小字段集合。
SUMMARY_FIELDS = ["记录编号", "关联端点", "请求时间", "响应状态", "重试次数"]


def canonical_json(payload: Any) -> str:
    """统一序列化口径，保证校验和可复算。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def checksum_of(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def build_archive(
    entry: dict[str, Any],
    *,
    rebuilt: bool = False,
    rebuilt_from: str | None = None,
    built_at: str | None = None,
) -> dict[str, Any]:
    """根据日志记录构造完整审计文件与同源摘要。

    返回 ``{"file": 完整文件, "summary": 摘要}``；摘要直接取自文件内容，
    保证归档页摘要与文件内容天然一致。
    """
    built_at = built_at or now_text()
    content = {field: entry.get(field) for field in AUDIT_FIELDS}
    file_id = f"AUDIT-{int(entry.get('id', 0)):04d}"
    file_name = f"{entry.get('记录编号', file_id)}.audit.json"

    file_body: dict[str, Any] = {
        "file_id": file_id,
        "file_name": file_name,
        "built_at": built_at,
        "rebuilt": rebuilt,
        "rebuilt_from": rebuilt_from,
        "content": content,
    }
    # 文件与摘要互相引用对方的校验和，先算文件本体、再算摘要，最后回填：
    # 文件校验和的输入固定排除两个校验和字段（见 verify_archive 的同一口径）。
    file_body["checksum"] = checksum_of(file_body)

    summary = {
        "file_id": file_id,
        "file_name": file_name,
        "built_at": built_at,
        "rebuilt": rebuilt,
        "rebuilt_from": rebuilt_from,
        "file_checksum": file_body["checksum"],
        "fields": {field: content.get(field) for field in SUMMARY_FIELDS},
    }
    # 摘要自身再做一层校验和，形成「文件 -> 摘要」的完整摘要链。
    summary["summary_checksum"] = checksum_of(
        {key: value for key, value in summary.items() if key != "summary_checksum"}
    )
    file_body["summary_checksum"] = summary["summary_checksum"]
    return {"file": file_body, "summary": summary}


def verify_archive(archive: dict[str, Any] | None) -> bool:
    """复算文件与摘要两级校验和，判断完整文件与摘要链是否被破坏。"""
    if not archive:
        return False
    file_body = archive.get("file")
    summary = archive.get("summary")
    if not isinstance(file_body, dict) or not isinstance(summary, dict):
        return False

    stored_file_checksum = file_body.get("checksum")
    # 文件校验和的输入不含两个校验和字段，与 build_archive 的构造口径保持一致。
    file_without_checksum = {
        key: value
        for key, value in file_body.items()
        if key not in ("checksum", "summary_checksum")
    }
    if checksum_of(file_without_checksum) != stored_file_checksum:
        return False

    stored_summary_checksum = summary.get("summary_checksum")
    summary_without_checksum = {
        key: value for key, value in summary.items() if key != "summary_checksum"
    }
    if checksum_of(summary_without_checksum) != stored_summary_checksum:
        return False

    if summary.get("file_checksum") != stored_file_checksum:
        return False
    if file_body.get("summary_checksum") != stored_summary_checksum:
        return False

    # 摘要必须与文件内容同源：编号、文件名与摘要字段逐项比对，
    # 任何一处对不上都说明摘要链已被破坏（归档页摘要与文件内容对不上）。
    if summary.get("file_id") != file_body.get("file_id"):
        return False
    if summary.get("file_name") != file_body.get("file_name"):
        return False
    content = file_body.get("content")
    if not isinstance(content, dict):
        return False
    for field in SUMMARY_FIELDS:
        if summary.get("fields", {}).get(field) != content.get(field):
            return False
    return True
