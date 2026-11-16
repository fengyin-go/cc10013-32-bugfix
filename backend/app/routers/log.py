"""采集日志接口：归档落文件、清理只改可见范围、按端点授权与审计数据集读取。

路由顺序刻意把静态路径（/export、/archives、/pending-audits）放在 /{entry_id} 前面，
否则 FastAPI 会把 "export" 当成 entry_id 解析成 422。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.audit_auth import AuditError, Operator, resolve_operator
from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.log import LogService

router = APIRouter(prefix="/api/log", tags=["采集日志"])

service = LogService()

LIST_FIELDS = ["记录编号", "关联端点", "请求时间", "响应状态", "耗时毫秒", "返回行数", "错误信息", "重试次数"]
STATUSES = ["草稿", "记录中", "已归档", "已清理"]


def current_operator(
    x_operator_id: str | None = Header(default=None, alias="X-Operator-Id"),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> Operator:
    """从请求头解析操作身份；账号注册表自带角色，不信任前端自报的角色。"""
    return resolve_operator(x_operator_id, x_role)


def raise_audit_error(exc: AuditError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


def project_list_item(row: dict[str, Any]) -> dict[str, Any]:
    """列表投影：不把完整审计文件塞进列表，只给同源摘要与缺失标记。"""
    item = dict(row)
    archive = item.pop("archive", None)
    if archive:
        item["archive_available"] = True
        item["archive_summary"] = archive.get("summary")
    else:
        item["archive_available"] = False
        item["archive_summary"] = None
    return item


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号检索"),
    status: str | None = Query(default=None, description="草稿、记录中、已归档、已清理"),
    include_hidden: bool = Query(default=False, description="是否包含已清理（隐藏）记录"),
    page: int = 1,
    size: int = 20,
    operator: Operator = Depends(current_operator),
) -> PageResult[dict]:
    """按记录编号与状态过滤采集日志；只返回账号被授权端点范围内的数据。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    rows, total = service.list_entries(
        operator,
        keyword=keyword,
        status=status,
        page=page,
        size=size,
        include_hidden=include_hidden,
    )
    return PageResult(
        items=[project_list_item(row) for row in rows], total=total, page=page, size=size
    )


@router.get("/export")
def export_entries(operator: Operator = Depends(current_operator)) -> dict[str, Any]:
    """导出采集日志清单：只读，不再改动任何记录的归档状态。"""
    items, total = service.list_entries(operator, page=1, size=10000)
    return {
        "module": "log",
        "operator": operator.operator_id,
        "total": total,
        "items": [project_list_item(row) for row in items],
    }


@router.get("/stats")
def log_stats(operator: Operator = Depends(current_operator)) -> dict[str, Any]:
    """采集日志看板：归档量、缺失文件量、已清理量都按授权范围统计。"""
    rows, _ = service.list_entries(operator, page=1, size=10000, include_hidden=True)
    return {
        "total": len(rows),
        "archived": sum(1 for row in rows if row.get("status") == "已归档"),
        "missing_file": sum(
            1 for row in rows if row.get("status") == "已归档" and not row.get("archive")
        ),
        "hidden": sum(1 for row in rows if not row.get("visible", True)),
    }


@router.get("/pending-audits")
def list_pending_audits(operator: Operator = Depends(current_operator)) -> dict[str, Any]:
    """未完成审计待处理区：缺失审计文件的记录索引，补建后自动移出。"""
    try:
        items = service.list_pending_audits(operator)
    except AuditError as exc:
        raise_audit_error(exc)
    return {"items": items, "total": len(items)}


@router.get("/archives")
def list_archives(operator: Operator = Depends(current_operator)) -> dict[str, Any]:
    """归档页：读取与完整文件同源的摘要，不存在文件的记录不会混进来。"""
    return {"items": service.list_archives(operator)}


@router.get("/archives/{entry_id}/file")
def get_archive_file(entry_id: int, operator: Operator = Depends(current_operator)) -> dict[str, Any]:
    """打开完整审计文件；文件缺失或损坏返回 409，并保证待处理区索引已登记。"""
    try:
        return service.get_archive_file(entry_id, operator)
    except AuditError as exc:
        raise_audit_error(exc)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int, operator: Operator = Depends(current_operator)) -> dict[str, Any]:
    """读取单条日志记录明细；越权或不存在时给出可读的错误说明。"""
    try:
        return service.get_entry(entry_id, operator)
    except AuditError as exc:
        raise_audit_error(exc)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条日志记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="日志记录已登记", entry=project_list_item(entry))


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(
    entry_id: int,
    payload: EntryPayload,
    operator: Operator = Depends(current_operator),
) -> ActionResult:
    """归档记录 / 补建审计文件 / 清理日志；越权与保留期冲突返回 4xx 并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    try:
        entry, message = service.run_action(entry_id, action, operator)
    except AuditError as exc:
        raise_audit_error(exc)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=project_list_item(entry))
