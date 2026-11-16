#!/usr/bin/env python3
"""回归测试统一入口：从任意工作目录运行都能正确解析 backend/app 与 tests。

用法：
    .venv/bin/python run_tests.py            # 跑单元/接口回归
    .venv/bin/python run_tests.py e2e        # 起服务后跑端到端复测
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_ROOT))


def run_unit() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(str(BACKEND_ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def run_e2e() -> int:
    import runpy

    runpy.run_path(str(BACKEND_ROOT / "tests" / "e2e_retest.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "unit"
    raise SystemExit(run_e2e() if target == "e2e" else run_unit())
