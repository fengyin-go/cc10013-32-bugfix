#!/usr/bin/env python3
"""端到端复测脚本：只读账号、归档账号、保留期内清理请求三条主线。

直接打真实 HTTP（默认 http://127.0.0.1:8000），不依赖测试框架。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
READONLY = {"X-Operator-Id": "readonly-01"}
ARCHIVIST = {"X-Operator-Id": "archivist-01"}
ADMIN = {"X-Operator-Id": "admin-01"}

failures: list[str] = []


def call(method: str, path: str, headers: dict[str, str] | None = None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name} {detail}")
    if not condition:
        failures.append(name)


# ---- 0. 初始状态：列表把缺文件记录如实标记，而不是伪装成正常已归档 ----
status, payload = call("GET", "/api/log?include_hidden=true", READONLY)
by_id = {item["id"]: item for item in payload["items"]}
check("只读账号只见授权端点(2条)", payload["total"] == 2, f"total={payload['total']}")
check(
    "缺文件记录列表标记 archive_available=false",
    by_id[2]["archive_available"] is False and by_id[2]["status"] == "已归档",
)
# 种子里 id=3 自带完整审计文件（只读账号无 PAY 权限，用归档账号核对）
_, seeded = call("GET", "/api/log?include_hidden=true", ARCHIVIST)
seeded_by_id = {item["id"]: item for item in seeded["items"]}
check("完整归档记录标记 archive_available=true", seeded_by_id[3]["archive_available"] is True)

# 只读账号看不到 PAY 端点
status, payload = call("GET", "/api/log/3", READONLY)
check("只读账号越权单读被拒(403)", status == 403 and payload["detail"]["code"] == "ENDPOINT_FORBIDDEN")

# ---- 1. 只读账号：写动作全部被拒，原因可查 ----
for action in ("归档记录", "清理日志", "补建审计文件"):
    status, payload = call("POST", "/api/log/1/actions", READONLY, {"values": {"action": action}})
    check(f"只读账号{action}被拒(403)", status == 403 and payload["detail"]["code"] == "READONLY_FORBIDDEN")
status, payload = call("GET", "/api/log/1", READONLY)
check("拒绝原因进入事件链可查", any("只读" in e.get("result", "") for e in payload["events"]))
# 伪造角色头不能提权
status, payload = call("POST", "/api/log/1/actions",
                       {"X-Operator-Id": "readonly-01", "X-Role": "admin"},
                       {"values": {"action": "归档记录"}})
check("伪造角色头提权被拒", status == 403 and payload["detail"]["code"] == "READONLY_FORBIDDEN")

# ---- 2. 归档账号：完整文件写出后才切状态 ----
status, payload = call("POST", "/api/log/1/actions", ARCHIVIST, {"values": {"action": "归档记录"}})
check("归档成功", status == 200 and payload["ok"] and payload["entry"]["status"] == "已归档")
status, archive = call("GET", "/api/log/archives/1/file", ARCHIVIST)
content = archive["file"]["content"]
check(
    "完整文件保留请求时间/响应状态/重试次数",
    content["请求时间"] == "2026-11-01 08:12:30" and content["响应状态"] == "200" and content["重试次数"] == "0",
)
check(
    "摘要与文件同源(校验和一致)",
    archive["summary"]["file_checksum"] == archive["file"]["checksum"],
)

# ---- 3. 缺文件 -> 打开 409 -> 待处理区有索引 -> 补建 -> 索引移除 ----
status, payload = call("GET", "/api/log/archives/2/file", READONLY)
check("打开缺失文件返回409", status == 409 and payload["detail"]["code"] == "AUDIT_FILE_MISSING")
status, payload = call("GET", "/api/log/pending-audits", READONLY)
check("待处理区索引存在", payload["total"] >= 1 and any(i["log_id"] == 2 for i in payload["items"]))
status, payload = call("POST", "/api/log/2/actions", ARCHIVIST, {"values": {"action": "补建审计文件"}})
check("归档账号补建成功", status == 200 and payload["ok"])
status, archive = call("GET", "/api/log/archives/2/file", ARCHIVIST)
check("补建文件可打开且带 rebuilt 标记", status == 200 and archive["file"]["rebuilt"] is True)
status, payload = call("GET", "/api/log/pending-audits", ARCHIVIST)
check("补建后索引移除", all(i["log_id"] != 2 for i in payload["items"]))

# 归档页只列完整文件，摘要内容与文件对得上
status, payload = call("GET", "/api/log/archives", ARCHIVIST)
for item in payload["items"]:
    _, full = call("GET", f"/api/log/archives/{item['id']}/file", ARCHIVIST)
    same = item["summary"]["file_checksum"] == full["file"]["checksum"] and all(
        item["summary"]["fields"][k] == full["file"]["content"][k] for k in item["summary"]["fields"]
    )
    check(f"归档页摘要与文件一致(id={item['id']})", same)

# ---- 4. 保留期内清理请求：冲突被拒、原因可查、数据无损 ----
status, payload = call("POST", "/api/log/1/actions", ARCHIVIST, {"values": {"action": "清理日志"}})
check(
    "保留期内清理被拒(409 RETENTION_CONFLICT)",
    status == 409 and payload["detail"]["code"] == "RETENTION_CONFLICT",
    payload["detail"]["message"][:24],
)
status, entry = call("GET", "/api/log/1", ARCHIVIST)
check("被拒后仍可见且文件保留", entry["visible"] is True and entry["archive"] is not None)
check("冲突原因可查", any("保留期" in e.get("result", "") for e in entry["events"]))

# ---- 5. 保留期已过的清理：只隐藏，字段/文件/顺序/摘要链全保留 ----
status, payload = call("POST", "/api/log/3/actions", ADMIN, {"values": {"action": "清理日志"}})
check("过保留期清理成功", status == 200 and payload["ok"])
status, entry = call("GET", "/api/log/3", ADMIN)
check(
    "清理不删请求时间/响应状态/重试次数",
    entry["请求时间"] == "2026-08-20 22:05:11" and entry["响应状态"] == "201" and entry["重试次数"] == "1",
)
check("清理后完整文件与摘要链仍可校验", entry["archive"]["summary"]["file_checksum"]
      == entry["archive"]["file"]["checksum"])
status, default_list = call("GET", "/api/log", ADMIN)
status, full_list = call("GET", "/api/log?include_hidden=true", ADMIN)
check("默认列表隐藏已清理", all(i["id"] != 3 for i in default_list["items"]))
check("include_hidden 保持原始顺序", [i["id"] for i in full_list["items"]] == [1, 2, 3, 4])

# ---- 6. 审计清理不能破坏完整文件：清理后再开文件仍然 200 ----
status, _ = call("GET", "/api/log/archives/3/file", ADMIN)
check("已清理记录的审计文件仍可打开(200)", status == 200)

# ---- 7. 导出接口不再篡改状态，且按授权范围输出 ----
_, before = call("GET", "/api/log/1", ARCHIVIST)
call("GET", "/api/log/export", ARCHIVIST)
_, after = call("GET", "/api/log/1", ARCHIVIST)
check("导出只读不改状态", before["status"] == after["status"])
_, scoped = call("GET", "/api/log/export", READONLY)
check("导出按端点授权过滤", all(i["关联端点"] in {"EP-LOG-WEB", "EP-LOG-ORDER"} for i in scoped["items"]))

print()
if failures:
    print(f"{len(failures)} 项失败：{failures}")
    sys.exit(1)
print("全部复测通过")
