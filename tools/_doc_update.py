# -*- coding: utf-8 -*-
"""一次性脚本：把这次"皮肤对齐校准"的说明补进两个 txt 文档（UTF-8，保持原换行）。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch(path: Path, anchor: str, addition: str, marker: str,
          before: bool = False) -> None:
    text = path.read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    text = text.replace("\r\n", "\n")
    addition = addition.replace("\r\n", "\n")
    if marker in text:
        print(f"{path.name}: 已经有这段说明，跳过")
        return
    if anchor not in text:
        print(f"{path.name}: 找不到锚点 {anchor!r}")
        return
    if before:
        text = text.replace(anchor, addition + anchor, 1)
    else:
        text = text.replace(anchor, anchor + addition, 1)
    path.write_text(text, encoding="utf-8", newline=newline)
    print(f"{path.name}: 已更新")


SKIN_DOC_ADDITION = """

============================================================
棋子 / 棋盘的对齐是怎么保证的（2026-09-26 自动校准）
============================================================

一、程序会**核对**棋盘图里的网格，而不是盲信 skin.json
------------------------------------------------------------
界面上要贴一张皮肤的 board.png 当棋盘时，程序先看图里那 9 条竖线、
10 条横线到底在哪（在 skin.json 给的参数附近找，逐线定位 + 等间距拟合），
然后分三种情况处理：

  1. 测得出、而且每条线都在等间距网格上（内部线偏差 ≤ 2px、最外圈 ≤ 3.2px）
     → 用**校准后**的网格贴图：格线和棋子严丝合缝；
  2. 测不出（图里没有格线 / 线条太弱 / 不是等间距）
     → **沿用 skin.json 里写的参数**（改坏了不如不改）；
  3. skin.json 里既没有网格、也没有比例，或者写了 board_grid_usable: false
     → 改用**程序自画棋盘**（格线 100% 准）+ 这张皮肤的材质配色 + 这套棋子图。

自带 78 套皮肤里：57 套是第 1 种（用自己的棋盘图，参数已自动校准），
21 套是第 3 种（棋盘图里没有完整网格，本来就是程序画棋盘）。

二、grid 写成对象最保险（列表有两种解释）
------------------------------------------------------------
   "grid": [47, 58, 416, 477]

这 4 个数字既可能是 [左上角 x, y, 宽, 高]，也可能是 [左, 上, 右, 下]。
程序两种都会试（挑和真实线条对得上的那种），但**推荐直接写清楚**：

   "grid": {"x": 47, "y": 58, "w": 416, "h": 477}       # 左上角 + 宽高
   "grid": {"left": 47, "top": 58, "right": 463, "bottom": 535}

本工具（tools/calibrate_skins.py）自动写入的就是对象写法，不会有歧义。

三、几个可以手写的开关
------------------------------------------------------------
   "board_grid_usable": false   这张棋盘图里没有完整 8×9 网格 → 用程序自画棋盘
   "board_grid_usable": true    正常贴这张棋盘图（默认）
   "board_grid_force": true     强制按 skin.json 的 grid 贴图，程序不再核对
                                （只在你自己量过、很有把握时用）
   "pieceScale": 0.92           棋子直径 = 格宽 × 这个数（默认 0.92）

四、棋子一定是"圆心压在交叉点上"
------------------------------------------------------------
棋子图会被程序先按**内容框**（去掉四周透明边）裁成正方形再缩放，
所以哪怕原图留的透明边不匀、棋子画得偏一点，圆心也一定落在交叉点上。
"""


CHANGELOG_ADDITION = """

============================================================
本次修复（2026-09-26）：皮肤棋子 / 棋盘格线自动校准
============================================================

  问题：78 套皮肤里有个别几套，棋子和棋盘图上的格线对不齐（错开十几像素）。

  原因：skin.json 里 "grid": [x, y, w, h] 这 4 个数字同时能被读成
        "左上角 + 宽高" 或 "左,上,右,下"。有几套的参数被按后一种理解，
        于是整张棋盘图被缩放到错误的大小和位置（越靠边错得越多）。

  修复：
    * 核对棋盘图里的真实格线：逐条定位 9 竖 10 横，做等间距拟合；
      两种写法都试，挑和真实线条对得上的那种；
    * 测得出 → 把校准后的精确网格写回 skin.json（对象写法，无歧义）；
    * 测不出 → 沿用原来的参数，不再乱动（也不再把好皮肤改成程序画棋盘）；
    * 棋子图统一按内容框居中再缩放 → 圆心必然压在交叉点上。
    * 新增 tools/calibrate_skins.py：一条命令重新校准所有皮肤，
      结果写到 tools/skin_calibration_report.txt。

  结果：78 套里 57 套继续用皮肤自带的棋盘图（网格已重新校准、严丝合缝），
        21 套本来就是"棋盘图里没有完整网格"的，仍然由程序自画棋盘
        （格线 100% 准 + 该皮肤材质配色 + 该皮肤棋子）。
"""


def main() -> int:
    patch(ROOT / "skins_skin_json_格式说明.txt",
          "（静夜象棋界面 · 皮肤说明）", SKIN_DOC_ADDITION,
          marker="棋子 / 棋盘的对齐是怎么保证的")
    changelog = ROOT / "更新日志.txt"
    text = changelog.read_text(encoding="utf-8").replace("\r\n", "\n")
    if "皮肤棋子 / 棋盘格线自动校准" in text:
        print(f"{changelog.name}: 已经有这次修复的说明，跳过")
    else:
        lines = text.split("\n")
        for position, line in enumerate(lines):
            if line.startswith("二、"):
                lines.insert(position, CHANGELOG_ADDITION.strip("\n") + "\n")
                break
        else:
            lines.append(CHANGELOG_ADDITION.strip("\n"))
        newline = "\r\n" if "\r\n" in changelog.read_text(encoding="utf-8") else "\n"
        changelog.write_text("\n".join(lines), encoding="utf-8", newline=newline)
        print(f"{changelog.name}: 已插入本次修复说明")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
