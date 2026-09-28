# -*- coding: utf-8 -*-
"""一次性脚本：把"图标改用 icons/ 那套"写进使用说明与更新日志。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OLD_MODE_LINE = "      红棋子＋齿轮＝引擎执红    黑棋子＋齿轮＝引擎执黑    放大镜＝分析模式"
NEW_MODE_LINE = ("      红点＋箭头＝引擎执红    黑点＋箭头＝引擎执黑    金色放大镜＝分析模式")

OLD_TOOL_LINE = ("     · 图标由 tools/make_toolbar_icons.py 生成（assets/toolbar），")
NEW_TOOL_LINES = [
    "     · 图标源文件放在 icons 文件夹（自己画的 18 个 PNG：深蓝灰圆角底 + 白色图形），",
    "       跑一次 tools/make_toolbar_icons.py 就会导入成 assets/toolbar 里的四态图标",
    "       （常态 / 悬停 / 按下 / 禁用），重画图标只要覆盖 icons 里的文件再跑一次；",
]


def main() -> int:
    manual = ROOT / "使用说明.txt"
    text = manual.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    if OLD_MODE_LINE in text:
        text = text.replace(OLD_MODE_LINE, NEW_MODE_LINE, 1)
        manual.write_text(text, encoding="utf-8")
        print("使用说明.txt：模式图标说明已更新")
    else:
        print("使用说明.txt：没找到旧的模式图标那行（可能已经改过）")

    changelog = ROOT / "更新日志.txt"
    log = changelog.read_text(encoding="utf-8").replace("\r\n", "\n")
    if OLD_TOOL_LINE in log:
        log = log.replace(OLD_TOOL_LINE, "\n".join(NEW_TOOL_LINES), 1)
        changelog.write_text(log, encoding="utf-8")
        print("更新日志.txt：图标来源说明已更新")
    else:
        print("更新日志.txt：没找到旧说明（可能已经改过）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
