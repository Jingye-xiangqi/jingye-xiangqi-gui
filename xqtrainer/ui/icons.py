# -*- coding: utf-8 -*-
"""工具栏图标：从 ``assets/toolbar`` 读 PNG（打包后从 exe 里读），按尺寸缓存。

图标由 ``tools/make_toolbar_icons.py`` 生成，每个图标有 4 个状态：
``normal``（常态）、``hover``（悬停）、``press``（按下）、``dis``（禁用）。
有 Pillow 时用 LANCZOS 缩放到界面要的尺寸（清晰），没有 Pillow 就退回
Tk 自带的 PhotoImage（只能整数倍缩小）——再不行调用方会用文字按钮兜底。
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from typing import Dict, Optional, Tuple

#: 图标名（和 assets/toolbar 里的文件名一致）
ICON_NAMES = ("new", "open", "save",
              "engine_red", "engine_black", "analyze",
              "first", "prev", "play", "stop", "next", "last", "undo", "redo",
              "edit", "copy_fen", "paste", "copy_game", "flip")
#: 每个图标的状态
VARIANTS = ("normal", "hover", "press", "dis")


def icon_dirs() -> list:
    """按优先级返回图标目录（打包解包目录 → exe 旁边 → 源码目录）。"""
    dirs = []
    try:
        from ..session import bundle_dir, exe_dir

        dirs.extend([Path(bundle_dir()) / "assets" / "toolbar",
                     Path(exe_dir()) / "assets" / "toolbar"])
    except Exception:  # noqa: BLE001
        pass
    dirs.append(Path(__file__).resolve().parents[2] / "assets" / "toolbar")
    seen, unique = set(), []
    for item in dirs:
        key = str(item).lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def icon_file(name: str, variant: str = "normal") -> Optional[Path]:
    suffix = "" if variant == "normal" else f"_{variant}"
    for base in icon_dirs():
        candidate = base / f"{name}{suffix}.png"
        if candidate.is_file():
            return candidate
    return None


def available() -> bool:
    """图标是否可用（有 PNG 就行；缩放优先用 Pillow）。"""
    return icon_file("play") is not None


class IconSet:
    """一套按固定显示尺寸缓存好的图标。"""

    def __init__(self, master: tk.Misc, size: int = 30):
        self.master = master
        self.size = int(size)
        self._cache: Dict[Tuple[str, str], object] = {}

    def get(self, name: str, variant: str = "normal"):
        key = (name, variant)
        if key in self._cache:
            return self._cache[key]
        photo = self._load(name, variant)
        if photo is None and variant != "normal":
            photo = self.get(name, "normal")
        self._cache[key] = photo
        return photo

    def _load(self, name: str, variant: str):
        path = icon_file(name, variant)
        if path is None:
            return None
        try:                                   # 有 Pillow：缩放到精确尺寸，清晰
            from PIL import Image, ImageTk

            with Image.open(path) as image:
                image = image.convert("RGBA")
                if image.size != (self.size, self.size):
                    image = image.resize((self.size, self.size), Image.LANCZOS)
                return ImageTk.PhotoImage(image, master=self.master)
        except Exception:  # noqa: BLE001
            pass
        try:                                   # 退路：Tk 自带 PhotoImage（整数倍缩小）
            photo = tk.PhotoImage(file=str(path), master=self.master)
            if photo.width() != self.size and self.size > 0:
                factor = max(1, int(round(photo.width() / float(self.size))))
                if factor > 1:
                    photo = photo.subsample(factor, factor)
            return photo
        except Exception:  # noqa: BLE001
            return None
