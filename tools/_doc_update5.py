# -*- coding: utf-8 -*-
"""一次性脚本：把上一步插入的"布局紧凑化"那条挪到第 3 条后面，编号顺序理顺。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOCK = "\n".join([
    "  4. 布局紧凑化：把「背景 / 音效 / 连线状态 / 模式」并进两行工具栏里，",
    "     去掉了工具栏下方那条左边全空的横带 —— 内容区（开局库 / 棋盘 / 分析面板）",
    "     整体上提，棋盘也更大（1400x900 下多出 23 像素高度）。",
    "",
])


def main() -> int:
    path = ROOT / "更新日志.txt"
    text = path.read_text(encoding="utf-8")
    if BLOCK not in text:
        print("找不到要挪动的段落")
        return 1
    text = text.replace(BLOCK + "\n", "", 1)          # 先摘出来
    anchor = "     同名文件夹来增加或覆盖）。\n"
    if anchor not in text:
        print("找不到第 3 条的结尾")
        return 1
    text = text.replace(anchor, anchor + "\n" + BLOCK, 1)
    path.write_text(text, encoding="utf-8")
    print("已把「布局紧凑化」挪到第 3 条后面")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
