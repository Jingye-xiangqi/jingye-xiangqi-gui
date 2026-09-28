# -*- coding: utf-8 -*-
"""原生界面测试的入口：在独立子进程里运行 tests/ui_suite.py。

Tkinter 在同一进程里反复创建/销毁窗口时，解释器退出阶段会打印 Tcl 清理告警，
把界面测试放到独立进程可以完全隔离，输出干净、也避免影响其它测试。
"""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE = Path(__file__).resolve().parent / "ui_suite.py"

try:
    import tkinter as tk  # noqa: F401

    _probe = tk.Tk()
    _probe.destroy()
    TK_OK = True
    TK_ERROR = ""
except Exception as exc:  # noqa: BLE001
    TK_OK = False
    TK_ERROR = str(exc)


@unittest.skipUnless(TK_OK, f"没有可用的图形环境：{TK_ERROR}")
class TestNativeUI(unittest.TestCase):
    """把界面测试作为一个整体跑起来（内部有 18 个用例）。"""

    def test_ui_suite(self):
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(SUITE)],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            self.fail("原生界面测试未通过：\n" + output[-4000:])
        self.assertIn("OK", output, output[-2000:])


if __name__ == "__main__":
    unittest.main()
