# -*- coding: utf-8 -*-
"""一次性脚本：把"工具栏下方空白带已去掉（紧凑布局）"补进更新日志。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARK = "布局紧凑化"
LINES = [
    "  4. 布局紧凑化：把「背景 / 音效 / 连线状态 / 模式」并进两行工具栏里，",
    "     去掉了工具栏下方那条左边全空的横带 —— 内容区（开局库 / 棋盘 / 分析面板）",
    "     整体上提，棋盘也更大（1400x900 下多出 23 像素高度）。",
    "",
    "",
]


def main() -> int:
    path = ROOT / "更新日志.txt"
    text = path.read_text(encoding="utf-8")
    if MARK in text:
        print("更新日志：已有说明，跳过")
        return 0
    anchor = "  3. 新增「背景」下拉框"
    if anchor not in text:
        print("更新日志：找不到锚点")
        return 1
    position = text.index(anchor)
    text = text[:position] + "\n".join(LINES) + text[position:]
    path.write_text(text, encoding="utf-8")
    print("更新日志：已补上紧凑布局说明")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
