#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成程序图标 assets/app.ico（仅在重新打包时需要，运行程序本身不需要 Pillow）。

用法::

    pip install pillow
    python tools/make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:  # pragma: no cover
    print("需要先安装 Pillow：pip install pillow")
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SIZE = 512
GLYPH = "象"
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simkai.ttf",   # 楷体
    r"C:\Windows\Fonts\simsun.ttc",   # 宋体
    r"C:\Windows\Fonts\msyh.ttc",     # 微软雅黑
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
]


def load_font(size: int):
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def radial_disc(size: int) -> Image.Image:
    """画一个木纹棋子圆牌（带高光与描边）。"""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = int(size * 0.02)
    box = (margin, margin, size - margin, size - margin)

    # 阴影
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse(
        (margin + size * 0.02, margin + size * 0.05, size - margin + size * 0.02, size - margin + size * 0.05),
        fill=(60, 38, 10, 120))
    shadow = shadow.filter(ImageFilter.GaussianBlur(size * 0.02))
    image = Image.alpha_composite(image, shadow)
    draw = ImageDraw.Draw(image)

    # 牌面渐变
    face = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    face_draw = ImageDraw.Draw(face)
    steps = 64
    for index in range(steps, 0, -1):
        ratio = index / steps
        radius = int((size / 2 - margin) * ratio)
        if radius <= margin * 2:
            continue
        color = (
            int(250 - 30 * (1 - ratio)),
            int(238 - 45 * (1 - ratio)),
            int(210 - 70 * (1 - ratio)),
            255,
        )
        offset = int(size * 0.06 * (1 - ratio))
        face_draw.ellipse((size / 2 - radius + margin, size / 2 - radius + margin + offset,
                           size / 2 + radius - margin, size / 2 + radius - margin + offset),
                          fill=color)
    image = Image.alpha_composite(image, face)
    draw = ImageDraw.Draw(image)

    # 外圈与内圈
    draw.ellipse(box, outline=(150, 108, 50, 255), width=int(size * 0.028))
    inner = (margin + size * 0.07, margin + size * 0.07, size - margin - size * 0.07,
             size - margin - size * 0.07)
    draw.ellipse(inner, outline=(178, 134, 70, 180), width=max(2, int(size * 0.010)))

    # 汉字
    font = load_font(int(size * 0.52))
    text_box = draw.textbbox((0, 0), GLYPH, font=font)
    text_w = text_box[2] - text_box[0]
    text_h = text_box[3] - text_box[1]
    position = ((size - text_w) / 2 - text_box[0], (size - text_h) / 2 - text_box[1] - size * 0.01)
    draw.text(position, GLYPH, font=font, fill=(178, 40, 32, 255))
    return image


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    icon = radial_disc(SIZE)
    ico_path = ASSETS / "app.ico"
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icon.save(ico_path, sizes=sizes)
    icon.resize((256, 256), Image.LANCZOS).save(ASSETS / "app.png")
    print(f"已生成 {ico_path}（尺寸 {', '.join(str(s[0]) for s in sizes)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
