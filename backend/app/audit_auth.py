"""采集日志的操作身份与按端点授权。

账号只在演示环境里用请求头传入（X-Operator-Id / X-Role），真实环境应换成网关签发的
令牌；授权判定的口径保持不变：审计日志按端点归属授权，只读账号只能查看受控共享范围，
任何写动作（归档、修复、清理、导出归档文件）都必须具备归档权限。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 三个复测账号：只读、归档、管理员。各自能看到的关联端点即「受控共享范围」。
ACCOUNTS: dict[str, dict[str, Any]] = {
    "readonly-01": {
        "role": "readonly",
        "name": "只读审计员",
        "endpoints": ["EP-LOG-WEB", "EP-LOG-ORDER"],
    },
    "archivist-01": {
        "role": "archivist",
        "name": "归档专员",
        "endpoints": ["EP-LOG-WEB", "EP-LOG-ORDER", "EP-LOG-PAY"],
    },
    "admin-01": {
        "role": "admin",
        "name": "保留期管理员",
        "endpoints": ["*"],
    },
}

ROLE_LABELS = {"readonly": "只读账号", "archivist": "归档账号", "admin": "管理员账号"}
WRITE_ROLES = {"archivist", "admin"}


@dataclass(frozen=True)
class Operator:
    operator_id: str
    role: str
    name: str
    endpoints: tuple[str, ...]

    @property
    def can_write(self) -> bool:
        return self.role in WRITE_ROLES

    def can_view_endpoint(self, endpoint: Any) -> bool:
        if "*" in self.endpoints:
            return True
        return str(endpoint or "") in self.endpoints


def resolve_operator(operator_id: str | None, role: str | None) -> Operator:
    """从请求头解析操作人。

    优先以账号注册表为准（账号自带角色，防止只读账号自行提权）；
    未注册账号按显式角色兜底，但只给最小的只读范围。
    """
    account = ACCOUNTS.get(str(operator_id or "").strip())
    if account is not None:
        return Operator(
            operator_id=str(operator_id).strip(),
            role=account["role"],
            name=account["name"],
            endpoints=tuple(account["endpoints"]),
        )

    safe_role = str(role or "readonly").strip()
    if safe_role not in ROLE_LABELS:
        safe_role = "readonly"
    # 未登记身份一律不给共享范围，避免匿名请求读到受控日志。
    return Operator(
        operator_id=str(operator_id or "anonymous").strip(),
        role=safe_role,
        name=ROLE_LABELS[safe_role],
        endpoints=(),
    )


class AuditError(Exception):
    """带 HTTP 状态码的业务异常，路由层统一翻译、统一留痕。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
