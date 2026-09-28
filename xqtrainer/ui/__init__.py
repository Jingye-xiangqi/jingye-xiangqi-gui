# -*- coding: utf-8 -*-
"""原生桌面界面（Tkinter 实现，不依赖浏览器）。

* :mod:`xqtrainer.ui.board_view` —— 棋盘画布控件（棋盘线、棋子、提示、箭头、动画）
* :mod:`xqtrainer.ui.app`        —— 主窗口（工具栏、棋谱/分析/引擎/局面四个面板）
"""

from .app import XiangqiWindow, run_desktop  # noqa: F401

__all__ = ["XiangqiWindow", "run_desktop"]
