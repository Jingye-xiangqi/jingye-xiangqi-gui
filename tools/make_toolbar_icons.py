#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 ``icons`` 目录里那套图标导入成工具栏用的图标（``assets/toolbar/*.png``）。

``icons`` 里是 18 个自己画的图标（深蓝灰圆角底 + 白色图形）::

    new/open/save  first/prev/play/next/last  undo/redo  edit/copy/paste/copy_pgn
    flip  engine_red/engine_black  analyze

本脚本负责：

1. 改名成界面里用的键：``copy`` → ``copy_fen``（复制局面）、
   ``copy_pgn`` → ``copy_game``（复制棋谱）；
2. 补一个 ``stop.png``（自动播放时"播放"键变成方块）：拿 ``play.png`` 的底，
   把中间的三角抹掉，再画一个同风格的白色圆角方块；
3. 每个图标生成三个状态：``_hover``（悬停，提亮）、``_press``（按下，压暗）、
   ``_dis``（禁用，去色 + 变淡）；
4. 拼一张 ``assets/toolbar_preview.png``，一眼看完所有图标。

以后重画图标只要覆盖 ``icons/*.png`` 再跑一次本脚本即可::

    python tools/make_toolbar_icons.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageEnhance
except ImportError:  # pragma: no cover
    print("需要 Pillow 与 numpy：pip install pillow numpy")
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "icons"
OUT = ROOT / "assets" / "toolbar"
PREVIEW = ROOT / "assets" / "toolbar_preview.png"

#: icons 里的文件名 → 界面里用的图标键
KEYS = {
    "new": "new",
    "open": "open",
    "save": "save",
    "first": "first",
    "prev": "prev",
    "play": "play",
    "next": "next",
    "last": "last",
    "undo": "undo",
    "redo": "redo",
    "edit": "edit",
    "copy": "copy_fen",          # 复制局面
    "paste": "paste",            # 粘贴局面 / 棋谱
    "copy_pgn": "copy_game",     # 复制棋谱
    "flip": "flip",
    "engine_red": "engine_red",
    "engine_black": "engine_black",
    "analyze": "analyze",
}
#: 生成出来的额外图标（播放中要用"停止"）
EXTRA = ("stop",)
#: 预览图的排列顺序
ORDER = ("new", "open", "save", "engine_red", "engine_black", "analyze",
         "first", "prev", "play", "stop", "next", "last", "undo", "redo",
         "edit", "copy_fen", "paste", "copy_game", "flip")
#: 悬停 / 按下 / 禁用 的明暗系数（米黄底：悬停略压暗一点最看得出来）
HOVER_FACTOR = 0.92
PRESS_FACTOR = 0.84


def chip_and_glyph(image: Image.Image):
    """把图标拆成"圆角底"和"中间的图形"两部分。

    做法：底色取**外圈**（离中心远、基本是底）的中位色，再把与底色差别明显的像素
    当成图形。这样不管图形是白色（深底那套）还是棕色（米黄底那套）都认得出来。
    """
    array = np.asarray(image.convert("RGBA"), dtype=np.float32)
    height, width = array.shape[:2]
    alpha = array[:, :, 3]
    ys, xs = np.mgrid[0:height, 0:width]
    radius = np.sqrt((xs - width / 2.0) ** 2 + (ys - height / 2.0) ** 2)
    inside = alpha > 40
    ring = inside & (radius > min(width, height) * 0.36)
    if ring.sum() < 10:
        ring = inside
    chip_color = np.median(array[ring][:, :3], axis=0)
    difference = np.abs(array[:, :, :3] - chip_color).max(axis=2)
    glyph = inside & (difference > 40)
    return array, chip_color, glyph


def erase_glyph(image: Image.Image) -> Image.Image:
    """用左右两侧的底色把中间的图形填掉（底色是渐变也不怕）。"""
    array, _chip, mask = chip_and_glyph(image)
    array = array.copy()
    height, width = mask.shape
    for y in range(height):
        xs = np.where(mask[y])[0]
        if not len(xs):
            continue
        left, right = int(xs.min()), int(xs.max())
        left_index = left - 1
        while left_index >= 0 and mask[y, left_index]:
            left_index -= 1
        right_index = right + 1
        while right_index < width and mask[y, right_index]:
            right_index += 1
        left_color = (array[y, left_index] if left_index >= 0
                      else array[y, min(right + 1, width - 1)])
        right_color = (array[y, right_index] if right_index < width else left_color)
        span = max(1, right - left)
        for x in range(left, right + 1):
            ratio = (x - left) / span
            array[y, x] = left_color * (1 - ratio) + right_color * ratio
    return (Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), "RGBA"),
            _chip, mask)


def make_stop(play_path: Path, out_path: Path) -> None:
    """播放键的"停止"状态：同样的底 + 同色的圆角方块（图形颜色取自原图标）。"""
    original = Image.open(play_path).convert("RGBA")
    base, _chip, mask = erase_glyph(original)
    array = np.asarray(original, dtype=np.float32)
    glyph_color = (np.median(array[mask][:, :3], axis=0) if mask.sum() > 10
                   else np.array([255.0, 255.0, 255.0]))
    width, height = base.size
    size = int(round(width * 0.42))
    left = (width - size) // 2
    top = (height - size) // 2
    ImageDraw.Draw(base).rounded_rectangle(
        (left, top, left + size - 1, top + size - 1),
        radius=max(2, size // 5),
        fill=(int(glyph_color[0]), int(glyph_color[1]), int(glyph_color[2]), 255))
    base.save(out_path)


def with_brightness(image: Image.Image, factor: float) -> Image.Image:
    """只调 RGB、不动透明通道（否则透明区域会糊上一层）。"""
    bright = ImageEnhance.Brightness(image.convert("RGB")).enhance(factor).convert("RGBA")
    bright.putalpha(image.convert("RGBA").split()[3])
    return bright


def disabled(image: Image.Image) -> Image.Image:
    """禁用：去色 + 变淡。"""
    grey = image.convert("LA").convert("RGBA")
    grey.putalpha(image.convert("RGBA").split()[3].point(lambda value: int(value * 0.45)))
    return grey


def build_preview(paths) -> None:
    icons = [Image.open(path).convert("RGBA") for path in paths]
    cell, pad = 60, 8
    rows, cols = 4, len(icons)
    sheet = Image.new("RGB", (cols * cell + pad, rows * cell + pad), (246, 244, 240))
    for column, icon in enumerate(icons):
        for row, variant in enumerate(("normal", "hover", "press", "dis")):
            image = (icon if variant == "normal" else
                     (with_brightness(icon, HOVER_FACTOR) if variant == "hover" else
                      (with_brightness(icon, PRESS_FACTOR) if variant == "press"
                       else disabled(icon))))
            sheet.paste(image, (pad // 2 + column * cell, pad // 2 + row * cell), image)
    sheet.save(PREVIEW)


def main() -> int:
    parser = argparse.ArgumentParser(description="导入 icons/ 里的工具栏图标")
    parser.add_argument("--no-preview", action="store_true", help="不生成预览图")
    args = parser.parse_args()

    missing = [name for name in KEYS if not (SOURCE / f"{name}.png").is_file()]
    if missing:
        print(f"icons 目录里缺少：{missing}")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)

    written = 0
    for source_name, key in KEYS.items():
        image = Image.open(SOURCE / f"{source_name}.png").convert("RGBA")
        image.save(OUT / f"{key}.png")
        written += 1
        image.save(OUT / f"{key}_hover.png")
        with_brightness(image, HOVER_FACTOR).save(OUT / f"{key}_hover.png")
        with_brightness(image, PRESS_FACTOR).save(OUT / f"{key}_press.png")
        disabled(image).save(OUT / f"{key}_dis.png")
        written += 3

    make_stop(SOURCE / "play.png", OUT / "stop.png")
    stop = Image.open(OUT / "stop.png").convert("RGBA")
    with_brightness(stop, HOVER_FACTOR).save(OUT / "stop_hover.png")
    with_brightness(stop, PRESS_FACTOR).save(OUT / "stop_press.png")
    disabled(stop).save(OUT / "stop_dis.png")
    written += 4

    print(f"已导入 {len(KEYS)} 个图标（+ 停止）共 {written} 个文件 → {OUT}")
    if not args.no_preview:
        build_preview([OUT / f"{key}.png" for key in ORDER])
        print(f"预览图 → {PREVIEW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
