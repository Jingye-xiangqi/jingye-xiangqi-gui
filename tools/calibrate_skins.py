#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量校准皮肤：让"棋子"精确压在棋盘交叉点上（棋盘图照旧用皮肤自己的）。

做的事（对 skins 目录下每一套皮肤）：

1. 读 ``skin.json`` 里的网格参数，用 ``xqtrainer.ui.skin`` 的检测算法
   在 ``board.png`` 上找真实的 9 条竖线 / 10 条横线（``[x,y,w,h]`` 和
   ``[左,上,右,下]`` 两种写法都会试），并核对"每条线是否都落在等间距网格上"：

   * 测得出 → 把**校准后**的精确网格写回 ``skin.json``（对象写法，不会再产生歧义），
     界面继续用这张棋盘图，格线和棋子严丝合缝；
   * 测不出（图里没有格线 / 线条太弱 / 明显不是等间距）→ **不动它的参数**：
     ``board_grid_usable: false`` 的仍然是程序自画棋盘，其余仍然用 skin.json 里
     原来的网格（改坏了不如不改）。

用法::

    python tools/calibrate_skins.py                     # 只体检，报告每套的结果
    python tools/calibrate_skins.py --apply             # 体检并把结果写回 skin.json
    python tools/calibrate_skins.py --only "清幽茶道"    # 只处理某几套（可写多个）
    python tools/calibrate_skins.py --apply --keep-uncertain
                                         # 不敢确定的也照旧贴棋盘图（只写校准网格）

报告同时写到 ``tools/skin_calibration_report.txt``（UTF-8）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from xqtrainer.ui import skin as skin_module  # noqa: E402

REPORT = ROOT / "tools" / "skin_calibration_report.txt"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量校准皮肤网格（让棋子压在交叉点上）")
    parser.add_argument("--skins", default=str(ROOT / "skins"), help="皮肤根目录")
    parser.add_argument("--apply", action="store_true", help="把结果写回 skin.json")
    parser.add_argument("--only", nargs="*", default=None, help="只处理这些皮肤（目录名）")
    parser.add_argument("--tolerance", type=float, default=skin_module.GRID_TOLERANCE,
                        help=f"允许的最大线条偏差（像素，默认 {skin_module.GRID_TOLERANCE}）")
    parser.add_argument("--prominence", type=float, default=skin_module.GRID_PROMINENCE,
                        help=f"线条显著度下限（默认 {skin_module.GRID_PROMINENCE}）")
    return parser


def skin_folders(root: Path, only=None) -> list:
    names = sorted([item.name for item in root.iterdir() if item.is_dir()],
                   key=str.lower)
    if only:
        wanted = {name.strip() for name in only}
        names = [name for name in names if name in wanted]
    return [root / name for name in names]


def rounded(values) -> list:
    return [round(float(value), 1) for value in values]


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.skins)
    folders = skin_folders(root, args.only)
    if not folders:
        print(f"没找到皮肤：{root}")
        return 1

    lines = []
    calibrated, unchanged, program_board, skipped = [], [], [], []
    for index, folder in enumerate(folders, 1):
        skin = skin_module.Skin(folder)
        board = skin.path_of("board")
        if not skin.ok:
            skipped.append(skin.name)
            lines.append(f"{index:02d} {skin.name} | 跳过（棋子不全：{skin.missing[:4]}）")
            continue
        if board is None:
            skipped.append(skin.name)
            lines.append(f"{index:02d} {skin.name} | 没有 board.png（本来就用程序画棋盘）")
            continue
        skin.resolve_grid()                      # 走的就是界面运行时那条路
        info = skin.grid_check or {}
        data = dict(skin.meta) if isinstance(skin.meta, dict) else {}
        data.pop("board_grid_force", None)
        data.pop("board_grid_note", None)
        if info.get("ok"):
            rect = info["rect"]
            data["grid"] = {"x": round(rect[0], 1), "y": round(rect[1], 1),
                            "w": round(rect[2] - rect[0], 1),
                            "h": round(rect[3] - rect[1], 1)}
            data["board_grid_usable"] = True
            data["board_grid_note"] = (
                f"自动校准：按 board.png 上的真实线条重新定位 8×9 网格"
                f"（最大偏差 {info['residual']:.2f}px、线条显著度 {info['prominence']:.1f}）")
            state = "贴棋盘图（已校准）"
            calibrated.append(skin.name)
            detail = (f"网格=[{rect[0]:.1f}, {rect[1]:.1f}, {rect[2] - rect[0]:.1f}, "
                      f"{rect[3] - rect[1]:.1f}] 偏差={info['residual']:.2f}px "
                      f"显著度={info['prominence']:.2f}")
        else:
            usable = bool(data.get("board_grid_usable", True)) and "grid" in data
            if not usable:
                data["board_grid_usable"] = False
                data.pop("grid", None)
                state = "程序自画棋盘"
                program_board.append(skin.name)
            else:
                state = "沿用原参数"
                unchanged.append(skin.name)
            note = skin.grid_note or "board.png 里没有可用的 8×9 网格"
            data["board_grid_note"] = f"未校准（{note}）"
            detail = f"（{note}）"
        if args.apply:
            (folder / skin_module.SKIN_JSON).write_text(
                json.dumps(data, ensure_ascii=False, indent=2).replace("\n", "\r\n"),
                encoding="utf-8", newline="")
        lines.append(f"{index:02d} {skin.name} | {state} | {detail}")

    header = [
        "静夜象棋界面 · 皮肤网格自动校准报告",
        f"皮肤目录：{root}",
        f"共 {len(folders)} 套：重新校准 {len(calibrated)} 套，沿用原参数 {len(unchanged)} 套，"
        f"程序自画棋盘 {len(program_board)} 套，跳过 {len(skipped)} 套",
        f"判定标准：内部线条偏差 ≤ {args.tolerance}px、最外圈 ≤ "
        f"{skin_module.GRID_TOLERANCE_EDGE}px 且显著度 ≥ {args.prominence}",
        "",
    ]
    REPORT.write_text("\n".join(header + lines) + "\n", encoding="utf-8")

    print(f"skins={len(folders)} calibrated={len(calibrated)} "
          f"unchanged={len(unchanged)} draw-board={len(program_board)} "
          f"skipped={len(skipped)}")
    print(f"report={REPORT}")
    print("报告文件里有每套的明细（皮肤名 + 网格 + 偏差）")
    if not args.apply:
        print("（这是体检模式，没有改动任何文件；要写回请加 --apply）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
