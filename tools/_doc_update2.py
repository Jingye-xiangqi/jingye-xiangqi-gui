# -*- coding: utf-8 -*-
"""一次性脚本：把"图标工具栏 + 背景下拉框"写进使用说明与更新日志。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TOOLBAR_LINES = [
    "【顶部工具栏】两行，都靠左排（按钮多，窗口窄也挤不掉）：",
    "",
    "  第一行最左边是**带文字**的设置类按钮：",
    "    界面 / 引擎 / 开局库 / 连线 / 断开 / 恢复出厂",
    "      · 界面 —— 背景、开关、主题、棋盘大小、棋子皮肤（见第七节）。",
    "      · 引擎 —— 引擎路径与引擎自身的 UCI 选项（见第五节）。",
    "      · 开局库 —— 本地开局库与云库设置（见第六节）。",
    "      · 连线 / 断开 —— 连到别的象棋平台 / 取消连线（见第八节）。",
    "      · 恢复出厂 —— 删掉 settings.json，所有设置回到默认值（会先弹确认框）。",
    "",
    "  接着是一排**图标按钮**：按钮上只有图案、没有文字，鼠标停上去会显示名字与快捷键。",
    "",
    "    第一行（文件 + 模式）：",
    "      白纸加号＝新建棋局    文件夹＝打开棋谱    软盘＝保存棋谱",
    "      红棋子＋齿轮＝引擎执红    黑棋子＋齿轮＝引擎执黑    放大镜＝分析模式",
    "",
    "    第二行（播放 + 局面）：",
    "      竖线＋三角＝首步    单三角＝上一步    三角＝播放/暂停    单三角＝下一步",
    "      三角＋竖线＝末尾    弯箭头＝悔棋    弯箭头＝重做",
    "      铅笔＝编辑局面    两个方块＝复制局面(FEN)    剪贴板＝粘贴局面/棋谱",
    "      文稿＋小方块＝复制棋谱    上下箭头＝翻转棋盘",
    "",
    "    ★ 图标按颜色分组，好记：",
    "      · 蓝色＝文件（新建 / 打开 / 保存）",
    "      · 红、黑、金色＝三种模式（引擎执红 / 引擎执黑 / 分析模式）；",
    "        **选中的模式整块变红**，再点一次取消，都不选＝纯手动打谱",
    "        （引擎不自动走子、也不自动分析，双方都自己走）。",
    "      · 绿色＝播放（首步 / 上一步 / 播放 / 下一步 / 末尾 / 悔棋 / 重做）",
    "      · 紫色＝局面（编辑 / 复制局面 / 粘贴局面·棋谱 / 复制棋谱 / 翻转棋盘）",
    "      · 播放键在自动播放时会变成\"方块\"＝再点一下暂停。",
    "      · 忘了哪个按钮是哪个？把鼠标停在图标上，会弹出文字提示（含快捷键）。",
    "",
    "  工具栏最右边：",
    "    背景 —— 下拉框：选桌面底图。自带 8 种：木纹 / 秋天 / 意式 / 海洋 / 冰雪 /",
    "            黄昏 / 森林 / 夏至；把喜欢的图片（png/jpg）丢进 exe 旁边的",
    "            backgrounds 文件夹，重启后也会出现在这里。",
    "            默认那一项＝\"默认（皮肤 / 木纹）\"：跟着棋子皮肤的背景走，",
    "            没有皮肤就用自带木纹。",
    "    音效 —— 勾选框：走子 / 吃子 / 将军的音效开关（打勾＝开）。",
    "    连线：未连接 —— 连线状态；纯手动打谱 —— 当前模式。",
    "",
    "",
]
TOOLBAR_NEW = "\n".join(TOOLBAR_LINES)

SETTINGS_LINES = [
    "    背景 —— 桌面底图：默认/木纹/秋天/意式/海洋/冰雪/黄昏/森林/夏至",
    "            （工具栏最右边的「背景」下拉框里也能直接换）。",
]
SETTINGS_ADDITION = "\n".join(SETTINGS_LINES) + "\n"

CHANGELOG_LINES = [
    "",
    "============================================================",
    "本次更新（2026-09-26 晚）：图标工具栏 + 8 种桌面背景",
    "============================================================",
    "",
    "  1. 工具栏按钮换成图标（参考鲨鱼象棋那种有立体感的圆角按钮，按钮上不再有文字）：",
    "       文件：新建棋局 / 打开棋谱 / 保存棋谱",
    "       模式：引擎执红 / 引擎执黑 / 分析模式（选中整块变红）",
    "       播放：首步 / 上一步 / 播放（播放中变方块）/ 下一步 / 末尾 / 悔棋 / 重做",
    "       局面：编辑局面 / 复制局面 / 粘贴局面·棋谱 / 复制棋谱 / 翻转棋盘",
    "     · 图标由 tools/make_toolbar_icons.py 生成（assets/toolbar），",
    "       每个图标有常态/悬停/按下/禁用四种状态；",
    "     · 鼠标停在图标上有文字提示（名字 + 快捷键），忘了也不怕；",
    "     · 万一没有 Pillow / 图标文件，会自动退回原来的文字按钮，功能不受影响。",
    "",
    "  2. 「界面 / 引擎 / 开局库 / 连线 / 断开 / 恢复出厂」这六个按钮保留文字，",
    "     固定放在第一行最左边。",
    "",
    "  3. 新增「背景」下拉框（工具栏最右边，界面设置里也有）：",
    "     木纹 / 秋天 / 意式 / 海洋 / 冰雪 / 黄昏 / 森林 / 夏至 8 种桌面底图，",
    "     选完立刻生效、记进 settings.json；默认＝跟随棋子皮肤 / 自带木纹。",
    "     图片放在 backgrounds 文件夹（已打包进 exe，也可以自己在 exe 旁边放",
    "     同名文件夹来增加或覆盖）。",
    "",
]
CHANGELOG_ADDITION = "\n".join(CHANGELOG_LINES)


def replace_region(text: str, start_marker: str, end_marker: str, new: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[:start] + new + text[end:]


def main() -> int:
    manual = ROOT / "使用说明.txt"
    text = manual.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    if "只有图案、没有文字" in text:
        print("使用说明.txt：已经更新过工具栏那一段，跳过")
    else:
        text = replace_region(text, "【顶部工具栏】", "【左面板】", TOOLBAR_NEW)
        print("使用说明.txt：工具栏说明已改写")
    if "背景 —— 桌面底图" not in text:
        marker = "    棋盘主题"
        if marker in text:
            text = text.replace(marker, SETTINGS_ADDITION + marker, 1)
            print("使用说明.txt：界面设置里补了「背景」一行")
    manual.write_text(text, encoding="utf-8")

    changelog = ROOT / "更新日志.txt"
    log = changelog.read_text(encoding="utf-8").replace("\r\n", "\n")
    if "图标工具栏 + 8 种桌面背景" in log:
        print("更新日志.txt：已有本次条目")
    else:
        lines = log.split("\n")
        for position, line in enumerate(lines):
            if line.startswith("二、"):
                lines.insert(position, CHANGELOG_ADDITION + "\n")
                break
        else:
            lines.append(CHANGELOG_ADDITION)
        changelog.write_text("\n".join(lines), encoding="utf-8")
        print("更新日志.txt：已插入本次更新条目")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
