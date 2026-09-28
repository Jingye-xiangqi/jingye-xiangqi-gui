# -*- coding: utf-8 -*-
"""一次性脚本：把 1.3 版这三项改动写进更新日志与使用说明。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHANGELOG = [
    "",
    "============================================================",
    "1.3 版（2026-09-26）：版本号 / 默认外观 / 连线设置独立窗口",
    "============================================================",
    "",
    "  1. 窗口标题从「静夜象棋界面1.1」改成「静夜象棋界面1.3」。",
    "",
    "  2. 新用户的默认外观：棋子皮肤＝秋分棋者，桌面背景＝03-意式。",
    "     （皮肤现在按「名字」记住，比如 0-秋分棋者 —— 打包成 exe 后解包目录",
    "       每次都变，记路径下次会失效；老用户原来的设置不受影响。）",
    "",
    "  3. 连线相关设置从「界面设置」里搬出来，工具栏「连线」右边新增",
    "     「连线设置」按钮，点开是独立窗口，里面集中了：",
    "       扫描间隔 / 点击按下 / 两步间隔 / 后台模式 / 智能确认 /",
    "       走子前后各确认一次 / 规则校验 / 连续 N 帧确认 / 或持续 N 秒 /",
    "       识别模型（.onnx，可浏览或自动查找 + 显示当前识别方式）/",
    "       棋盘区域（只扫描窗口的一部分：左/上/宽/高 百分比，默认整窗）/",
    "       开始连线 / 断开。",
    "",
]

MANUAL_LINE = "    棋盘区域 —— 连线设置窗口里改（工具栏「连线设置」）"


def main() -> int:
    changelog = ROOT / "更新日志.txt"
    text = changelog.read_text(encoding="utf-8").replace("\r\n", "\n")
    if "1.3 版（2026-09-26）" in text:
        print("更新日志：已有 1.3 条目")
    else:
        lines = text.split("\n")
        for position, line in enumerate(lines):
            if line.startswith("二、"):
                lines.insert(position, "\n".join(CHANGELOG).strip("\n") + "\n")
                break
        else:
            lines.append("\n".join(CHANGELOG))
        changelog.write_text("\n".join(lines), encoding="utf-8")
        print("更新日志：已插入 1.3 条目")

    manual = ROOT / "使用说明.txt"
    body = manual.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    if "它自己带一个下拉框" in body or "连线设置" not in body:
        pass
    old = "    界面 —— 背景、开关、主题、棋盘大小、棋子皮肤（见第七节）。"
    new = ("    界面 —— 背景、开关、主题、棋盘大小、棋子皮肤（见第七节）。\n"
           "      （连线相关的设置不在这里，见下面的「连线设置」。）\n"
           "    连线设置 —— 扫描间隔 / 点击节奏 / 识别模型 / 棋盘区域 / 确认方式，"
           "单独一个窗口。")
    if "连线设置 —— 扫描间隔" in body:
        print("使用说明：已有连线设置说明")
    elif old in body:
        body = body.replace(old, new, 1)
        manual.write_text(body, encoding="utf-8")
        print("使用说明：已补「连线设置」说明")
    else:
        print("使用说明：没找到锚点，跳过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
