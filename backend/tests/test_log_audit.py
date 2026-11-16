"""采集日志归档、清理与授权的回归测试。

覆盖需求里的全部复测口径：
- 完整文件写出后才切换归档状态；归档失败落入未完成审计待处理区；
- 清理仅改变可见范围，原始顺序、摘要与完整文件保留；
- 保留期未到的清理请求被拒绝，原因可查；
- 只读账号只能看受控共享端点、写动作一律拒绝，伪造角色不能提权；
- 归档页摘要与文件内容同源；待处理区索引缺失可自动补齐。
"""
from __future__ import annotations

import importlib
import sys
import unittest
from datetime import date
from pathlib import Path

# 自举 sys.path：无论从哪个工作目录 discover，都能 import 到 backend/app。
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi.testclient import TestClient


def fresh_client():
    """按依赖顺序重载内存仓库，保证每个用例都从同一份种子数据开始。"""
    import app.archive_file
    import app.audit_auth
    import app.seed
    import app.store
    import app.services.log
    import app.routers.log
    import app.main
    importlib.reload(app.archive_file)
    importlib.reload(app.audit_auth)
    importlib.reload(app.seed)
    importlib.reload(app.store)
    importlib.reload(app.services.log)
    importlib.reload(app.routers.log)
    importlib.reload(app.main)
    return TestClient(app.main.app), app.store.store


READONLY = {"X-Operator-Id": "readonly-01"}
ARCHIVIST = {"X-Operator-Id": "archivist-01"}
ADMIN = {"X-Operator-Id": "admin-01"}
READONLY_SPOOF = {"X-Operator-Id": "readonly-01", "X-Role": "admin"}


class LogArchiveTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store = fresh_client()

    def action(self, entry_id: int, name: str, headers: dict[str, str]):
        return self.client.post(
            f"/api/log/{entry_id}/actions",
            json={"values": {"action": name}},
            headers=headers,
        )

    # ---------------------------------------------------------- 授权范围

    def test_readonly_sees_only_shared_endpoints(self) -> None:
        payload = self.client.get("/api/log?include_hidden=true", headers=READONLY).json()
        endpoints = {item["关联端点"] for item in payload["items"]}
        self.assertEqual(endpoints, {"EP-LOG-WEB", "EP-LOG-ORDER"})
        # EP-LOG-PAY 不在受控共享范围内，单条读取同样拒绝。
        denied = self.client.get("/api/log/3", headers=READONLY)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["detail"]["code"], "ENDPOINT_FORBIDDEN")

    def test_anonymous_has_no_shared_scope(self) -> None:
        self.assertEqual(self.client.get("/api/log").json()["total"], 0)
        denied = self.action(1, "归档记录", {})
        self.assertEqual(denied.status_code, 403)

    def test_readonly_write_actions_denied_and_recorded(self) -> None:
        for name in ("归档记录", "清理日志", "补建审计文件"):
            response = self.action(1, name, READONLY)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["detail"]["code"], "READONLY_FORBIDDEN")
        # 拒绝原因写进事件链，事后可查。
        events = self.client.get("/api/log/1", headers=READONLY).json()["events"]
        self.assertTrue(any("只读" in event.get("result", "") for event in events))

    def test_role_header_cannot_escalate_readonly_account(self) -> None:
        response = self.action(1, "归档记录", READONLY_SPOOF)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "READONLY_FORBIDDEN")

    # ---------------------------------------------------------- 归档落文件

    def test_archive_writes_full_file_before_status_switch(self) -> None:
        response = self.action(1, "归档记录", ARCHIVIST)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        entry = response.json()["entry"]
        self.assertEqual(entry["status"], "已归档")
        self.assertTrue(entry["archive_available"])

        archive = self.client.get("/api/log/archives/1/file", headers=ARCHIVIST).json()
        content = archive["file"]["content"]
        # 请求时间、响应状态、重试次数等原始字段全部进了完整文件。
        self.assertEqual(content["请求时间"], "2026-11-01 08:12:30")
        self.assertEqual(content["响应状态"], "200")
        self.assertEqual(content["重试次数"], "0")
        # 摘要与文件内容同源。
        self.assertEqual(
            archive["summary"]["fields"]["记录编号"], content["记录编号"]
        )
        self.assertEqual(
            archive["summary"]["file_checksum"], archive["file"]["checksum"]
        )

    def test_missing_archive_file_is_indexed_in_pending_area(self) -> None:
        # 种子里 id=2 就是「状态已归档、文件缺失」的历史故障现场。
        missing = self.client.get("/api/log/archives/2/file", headers=READONLY)
        self.assertEqual(missing.status_code, 409)
        self.assertEqual(missing.json()["detail"]["code"], "AUDIT_FILE_MISSING")

        pending = self.client.get("/api/log/pending-audits", headers=READONLY).json()
        self.assertEqual(pending["total"], 1)
        self.assertEqual(pending["items"][0]["log_id"], 2)

    def test_repair_rebuilds_file_and_clears_pending_index(self) -> None:
        response = self.action(2, "补建审计文件", ARCHIVIST)
        self.assertTrue(response.json()["ok"])
        archive = self.client.get("/api/log/archives/2/file", headers=ARCHIVIST).json()
        self.assertTrue(archive["file"]["rebuilt"])
        self.assertEqual(
            self.client.get("/api/log/pending-audits", headers=ARCHIVIST).json()["total"],
            0,
        )

    def test_reconcile_rebuilds_missing_pending_index(self) -> None:
        # 模拟「待处理区索引缺失记录」：清空索引后再对账，记录应被补回。
        self.store.rows(self.store.PENDING_AUDIT_TABLE).clear()
        pending = self.client.get("/api/log/pending-audits", headers=ARCHIVIST).json()
        self.assertEqual(pending["total"], 1)
        self.assertEqual(pending["items"][0]["log_id"], 2)

    def test_archive_page_summary_matches_file_content(self) -> None:
        self.action(2, "补建审计文件", ARCHIVIST)
        archives = {
            item["id"]: item
            for item in self.client.get("/api/log/archives", headers=ARCHIVIST).json()["items"]
        }
        # 缺文件的记录（修复前只有 id=2、3、4 有文件；修复后 2 也在册）不混入归档页。
        self.assertIn(2, archives)
        for entry_id, item in archives.items():
            full = self.client.get(
                f"/api/log/archives/{entry_id}/file", headers=ARCHIVIST
            ).json()
            self.assertEqual(
                item["summary"]["file_checksum"], full["file"]["checksum"]
            )
            for field, value in item["summary"]["fields"].items():
                self.assertEqual(value, full["file"]["content"][field])

    # ---------------------------------------------------------- 清理与保留期

    def test_cleanup_before_retention_is_conflicted_and_reasoned(self) -> None:
        self.action(1, "归档记录", ARCHIVIST)
        response = self.action(1, "清理日志", ARCHIVIST)
        self.assertEqual(response.status_code, 409)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "RETENTION_CONFLICT")
        self.assertIn("保留期", detail["message"])
        # 被拒绝后记录仍可见、文件未动。
        entry = self.client.get("/api/log/1", headers=ARCHIVIST).json()
        self.assertTrue(entry["visible"])
        self.assertIsNotNone(entry["archive"])
        # 冲突原因进事件链。
        self.assertTrue(
            any("保留期" in event.get("result", "") for event in entry["events"])
        )

    def test_cleanup_only_hides_and_keeps_file_summary_order(self) -> None:
        response = self.action(3, "清理日志", ADMIN)  # retain_until 2026-11-10 已过
        self.assertTrue(response.json()["ok"])
        entry = self.client.get("/api/log/3", headers=ADMIN).json()
        self.assertFalse(entry["visible"])
        # 原始字段一个不丢。
        self.assertEqual(entry["请求时间"], "2026-08-20 22:05:11")
        self.assertEqual(entry["响应状态"], "201")
        self.assertEqual(entry["重试次数"], "1")
        # 完整文件与摘要链仍可打开、校验通过。
        archive = self.client.get("/api/log/archives/3/file", headers=ADMIN).json()
        self.assertEqual(archive["file"]["checksum"], archive["summary"]["file_checksum"])
        # 默认列表隐藏，include_hidden 能找回且原始顺序不变。
        default_ids = [
            item["id"]
            for item in self.client.get("/api/log", headers=ADMIN).json()["items"]
        ]
        self.assertNotIn(3, default_ids)
        all_ids = [
            item["id"]
            for item in self.client.get(
                "/api/log?include_hidden=true", headers=ADMIN
            ).json()["items"]
        ]
        self.assertEqual(all_ids, [1, 2, 3, 4])

    def test_cleanup_refused_when_archive_incomplete(self) -> None:
        created = self.client.post(
            "/api/log",
            json={
                "values": {
                    "记录编号": "LOG-NEW-01",
                    "关联端点": "EP-LOG-WEB",
                    "请求时间": "2026-11-16 10:00:00",
                    "retain_until": "2026-01-01",
                }
            },
            headers=ARCHIVIST,
        ).json()["entry"]
        response = self.action(created["id"], "清理日志", ARCHIVIST)
        self.assertFalse(response.json()["ok"])
        self.assertIn("审计文件不完整", response.json()["message"])

    def test_tampered_file_is_detected_indexed_and_repairable(self) -> None:
        row = self.store.find("log", 3)
        row["archive"]["file"]["content"]["响应状态"] = "999"
        denied = self.client.get("/api/log/archives/3/file", headers=ADMIN)
        self.assertEqual(denied.json()["detail"]["code"], "AUDIT_FILE_CORRUPTED")
        pending = self.client.get("/api/log/pending-audits", headers=ADMIN).json()
        self.assertIn(3, [item["log_id"] for item in pending["items"]])
        repaired = self.action(3, "补建审计文件", ADMIN)
        self.assertTrue(repaired.json()["ok"])
        self.assertEqual(
            self.client.get("/api/log/archives/3/file", headers=ADMIN).status_code, 200
        )

    # ---------------------------------------------------------- 其他

    def test_export_is_read_only_and_scoped(self) -> None:
        before = self.client.get("/api/log/1", headers=ARCHIVIST).json()["status"]
        export = self.client.get("/api/log/export", headers=ARCHIVIST)
        self.assertEqual(export.status_code, 200)
        after = self.client.get("/api/log/1", headers=ARCHIVIST).json()["status"]
        self.assertEqual(before, after)
        # 只读账号导出的同样只有受控范围。
        scoped = self.client.get("/api/log/export", headers=READONLY).json()
        self.assertTrue(
            all(item["关联端点"] in {"EP-LOG-WEB", "EP-LOG-ORDER"} for item in scoped["items"])
        )

    def test_retention_boundary_uses_injected_clock(self) -> None:
        from app.services.log import LogService
        from app.audit_auth import resolve_operator
        # 同一天仍在保护期内（today <= retain_until）。
        service = LogService(clock=lambda: date(2027, 2, 1))
        entry = self.store.find("log", 1)
        entry["archive"] = entry.get("archive") or self._archive_for(entry)
        operator = resolve_operator("archivist-01", None)
        with self.assertRaises(Exception):
            service.run_action(1, "清理日志", operator)

    @staticmethod
    def _archive_for(entry):
        from app.archive_file import build_archive
        return build_archive(entry)


if __name__ == "__main__":
    unittest.main()
