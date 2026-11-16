"""采集日志业务规则：归档落文件、清理只改可见范围、按端点授权与待处理区索引。

关键不变量：
1. 完整审计文件写出并通过校验和复核之后，才允许把状态切换成「已归档」；写文件失败时
   状态保持原样，并在未完成审计待处理区登记一条索引。
2. 清理动作只翻转 visible，不删除任何字段、不动完整文件与摘要链，列表原始顺序不变。
3. 保留期未到时清理请求与保留期保护冲突，保留期保护优先，清理被拒绝且原因留痕。
4. 所有写动作都要求归档权限；只读账号的越权尝试会被拒绝并写进事件链，原因可查。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable

from app.archive_file import build_archive, now_text, verify_archive
from app.audit_auth import AuditError, Operator
from app.store import store

MODULE = "log"
REQUIRED_FIELDS = ["记录编号", "关联端点", "请求时间"]
STATUS_ORDER = ["草稿", "记录中", "已归档", "已清理"]

ACTION_ARCHIVE = "归档记录"
ACTION_CLEANUP = "清理日志"
ACTION_REPAIR = "补建审计文件"

# 归档前置状态：记录生命周期只能向前，不允许从已清理回退到归档。
ARCHIVE_ALLOWED_FROM = ("草稿", "记录中")


class LogService:
    def __init__(self, clock: Callable[[], date] | None = None) -> None:
        # 注入时钟方便复测保留期边界；默认取系统当天。
        self._clock = clock or date.today

    # ------------------------------------------------------------------ 查询

    def list_entries(
        self,
        operator: Operator,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        include_hidden: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        # 始终按仓库原始顺序输出，清理动作不会重排记录。
        rows = [row for row in store.rows(MODULE) if operator.can_view_endpoint(row.get("关联端点"))]
        if not include_hidden:
            rows = [row for row in rows if row.get("visible", True)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int, operator: Operator) -> dict[str, Any]:
        entry = self._require_entry(entry_id)
        self._require_endpoint_scope(entry, operator)
        return entry

    def list_archives(self, operator: Operator) -> list[dict[str, Any]]:
        """归档页：只返回拥有完整文件的记录，摘要直接取文件同源摘要。"""
        result = []
        for row in store.rows(MODULE):
            archive = row.get("archive")
            if not archive or not operator.can_view_endpoint(row.get("关联端点")):
                continue
            result.append({
                "id": row.get("id"),
                "记录编号": row.get("记录编号"),
                "关联端点": row.get("关联端点"),
                "status": row.get("status"),
                "visible": row.get("visible", True),
                "summary": archive["summary"],
            })
        return result

    def get_archive_file(self, entry_id: int, operator: Operator) -> dict[str, Any]:
        entry = self._require_entry(entry_id)
        self._require_endpoint_scope(entry, operator)
        archive = entry.get("archive")
        if not archive:
            # 缺失文件一定能在待处理区索引里查到，前台据此引导去补建。
            self._index_pending(entry, "审计文件缺失，归档状态与文件不一致")
            raise AuditError(
                409,
                "AUDIT_FILE_MISSING",
                f"日志记录 {entry.get('记录编号')} 的审计文件尚未生成，已登记到未完成审计待处理区",
            )
        if not verify_archive(archive):
            self._index_pending(entry, "审计文件校验和不通过，摘要链可能已损坏")
            raise AuditError(
                409,
                "AUDIT_FILE_CORRUPTED",
                f"日志记录 {entry.get('记录编号')} 的审计文件校验未通过，禁止打开损坏文件",
            )
        return archive

    # ------------------------------------------------------ 未完成审计待处理区

    def list_pending_audits(self, operator: Operator) -> list[dict[str, Any]]:
        """对账并返回待处理区索引：已归档但缺文件（或文件损坏）的记录全部在册。"""
        self.reconcile_pending_index()
        return [
            dict(row)
            for row in store.rows(store.PENDING_AUDIT_TABLE)
            if operator.can_view_endpoint(row.get("关联端点"))
        ]

    def reconcile_pending_index(self) -> None:
        """全量对账：补齐缺失索引、移除已补建完成的索引，顺序跟随日志原始顺序。"""
        table = store.rows(store.PENDING_AUDIT_TABLE)
        indexed = {int(row["log_id"]): row for row in table}
        current_missing: set[int] = set()
        for row in store.rows(MODULE):
            archive = row.get("archive")
            if row.get("status") != "已归档":
                continue
            if not archive or not verify_archive(archive):
                current_missing.add(int(row["id"]))
                reason = "审计文件缺失" if not archive else "审计文件校验和不通过"
                if int(row["id"]) not in indexed:
                    self._index_pending(row, reason)
        # 文件已补建成功的记录移出待处理区。
        table[:] = [row for row in table if int(row["log_id"]) in current_missing]

    def _index_pending(self, entry: dict[str, Any], reason: str) -> dict[str, Any]:
        existing = store.find_pending(int(entry["id"]))
        if existing is not None:
            return existing
        record = {
            "log_id": int(entry["id"]),
            "记录编号": entry.get("记录编号"),
            "关联端点": entry.get("关联端点"),
            "请求时间": entry.get("请求时间"),
            "reason": reason,
            "detected_at": now_text(),
            "status": "待补建",
        }
        store.rows(store.PENDING_AUDIT_TABLE).append(record)
        return record

    def _clear_pending(self, entry_id: int) -> None:
        table = store.rows(store.PENDING_AUDIT_TABLE)
        table[:] = [row for row in table if int(row["log_id"]) != entry_id]

    # ------------------------------------------------------------------ 写入

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for optional in ("响应状态", "耗时毫秒", "返回行数", "错误信息", "重试次数", "retain_until"):
            if optional in values:
                entry[optional] = values.get(optional)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["visible"] = True
        entry["archive"] = None
        entry["events"] = [{"at": now_text(), "action": "登记日志", "operator": "system"}]
        rows.append(entry)
        return entry, []

    def run_action(
        self, entry_id: int, action: str, operator: Operator
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"日志记录 {entry_id} 不存在或已归档"

        if action == ACTION_REPAIR:
            return self._repair(entry, operator)
        if action == ACTION_ARCHIVE:
            return self._archive(entry, operator)
        if action == ACTION_CLEANUP:
            return self._cleanup(entry, operator)
        return None, f"动作「{action}」不属于采集日志可执行范围"

    def _archive(
        self, entry: dict[str, Any], operator: Operator
    ) -> tuple[dict[str, Any] | None, str]:
        self._require_write(operator, entry, ACTION_ARCHIVE)
        if entry.get("status") not in ARCHIVE_ALLOWED_FROM:
            return None, (
                f"当前状态「{entry.get('status')}」不允许归档，"
                f"仅 {'、'.join(ARCHIVE_ALLOWED_FROM)} 状态可执行归档"
            )
        try:
            # 先在内存里构造完整文件并复核，复核通过才挂到记录上、再切状态，
            # 任何一步抛错都不会留下「已归档但无文件」的半成品。
            archive = build_archive(entry)
            if not verify_archive(archive):
                raise RuntimeError("审计文件校验和复核未通过")
        except Exception as exc:  # 归档失败：状态不动，落入待处理区
            self._index_pending(entry, f"归档失败：{exc}")
            self._append_event(entry, ACTION_ARCHIVE, operator, f"失败：{exc}")
            return None, f"审计文件写出失败，归档状态未切换，已登记待处理区：{exc}"

        entry["archive"] = archive
        entry["status"] = "已归档"
        entry["pending"] = False
        entry["abnormal"] = False
        self._clear_pending(int(entry["id"]))
        self._append_event(entry, ACTION_ARCHIVE, operator, "完整文件写出后切换归档状态")
        return entry, "审计文件已生成并校验通过，归档状态已切换"

    def _repair(
        self, entry: dict[str, Any], operator: Operator
    ) -> tuple[dict[str, Any] | None, str]:
        self._require_write(operator, entry, ACTION_REPAIR)
        archive = entry.get("archive")
        if archive is not None and verify_archive(archive):
            return None, "审计文件完整且校验通过，无需补建"
        reason = "审计文件缺失" if not archive else "审计文件校验和不通过"
        rebuilt = build_archive(entry, rebuilt=True, rebuilt_from=reason)
        if not verify_archive(rebuilt):
            self._index_pending(entry, "补建后校验仍不通过")
            return None, "补建审计文件校验未通过，已保留在待处理区"
        entry["archive"] = rebuilt
        # 状态本来就是已归档（历史故障现场），补建只修文件，不改生命周期状态。
        entry["pending"] = False
        self._clear_pending(int(entry["id"]))
        self._append_event(entry, ACTION_REPAIR, operator, f"补建完成，原因：{reason}")
        return entry, f"审计文件已补建（{reason}），待处理区索引已移除"

    def _cleanup(
        self, entry: dict[str, Any], operator: Operator
    ) -> tuple[dict[str, Any] | None, str]:
        self._require_write(operator, entry, ACTION_CLEANUP)

        # 完整文件与摘要链是清理的前提，不完整就拒绝，防止清理把问题证据抹掉。
        archive = entry.get("archive")
        if not archive or not verify_archive(archive):
            self._index_pending(
                entry, "审计文件缺失" if not archive else "审计文件校验和不通过"
            )
            return None, "审计文件不完整或摘要链校验未通过，拒绝清理，请先补建审计文件"

        # 保留期冲突判定：保留期保护优先于清理请求。
        retain_until = self._parse_retain_until(entry)
        today = self._clock()
        if retain_until is not None and today <= retain_until:
            self._append_event(
                entry,
                ACTION_CLEANUP,
                operator,
                f"拒绝：保留期至 {retain_until.isoformat()} 未到，保留期保护优先于清理请求",
            )
            raise AuditError(
                409,
                "RETENTION_CONFLICT",
                (
                    f"保留期至 {retain_until.isoformat()} 未到（当前日期 {today.isoformat()}），"
                    "清理请求与保留期保护冲突，保留期保护优先，清理已拒绝"
                ),
            )

        # 只改变可见范围：字段、完整文件、摘要、原始顺序全部保留。
        entry["visible"] = False
        entry["status"] = "已清理"
        entry["pending"] = False
        self._append_event(
            entry, ACTION_CLEANUP, operator, "仅切换可见范围，完整文件与摘要链保留"
        )
        return entry, "日志已清理（仅对默认列表隐藏，完整文件与摘要链保留）"

    # ------------------------------------------------------------------ 辅助

    def _require_entry(self, entry_id: int) -> dict[str, Any]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            raise AuditError(404, "ENTRY_NOT_FOUND", f"日志记录 {entry_id} 不存在")
        return entry

    def _require_endpoint_scope(self, entry: dict[str, Any], operator: Operator) -> None:
        endpoint = entry.get("关联端点")
        if not operator.can_view_endpoint(endpoint):
            raise AuditError(
                403,
                "ENDPOINT_FORBIDDEN",
                f"账号 {operator.operator_id} 未被授权查看端点 {endpoint} 的审计日志",
            )

    def _require_write(
        self, operator: Operator, entry: dict[str, Any], action: str
    ) -> None:
        # 先判端点授权，再判角色：只读账号越权时原因要能精确查到。
        self._require_endpoint_scope(entry, operator)
        if not operator.can_write:
            reason = f"拒绝：{operator.name}仅有只读权限，不能执行{action}"
            self._append_event(entry, action, operator, reason)
            raise AuditError(403, "READONLY_FORBIDDEN", reason)

    def _append_event(
        self, entry: dict[str, Any], action: str, operator: Operator, result: str
    ) -> None:
        entry.setdefault("events", []).append(
            {
                "at": now_text(),
                "action": action,
                "operator": operator.operator_id,
                "role": operator.role,
                "result": result,
            }
        )

    @staticmethod
    def _parse_retain_until(entry: dict[str, Any]) -> date | None:
        raw = str(entry.get("retain_until") or "").strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None
