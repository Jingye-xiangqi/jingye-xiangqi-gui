# -*- coding: utf-8 -*-
"""界面背景（桌面底图）：读 ``backgrounds`` 文件夹里的图片（打包后从 exe 里读）。

文件夹里每个图片文件算一种背景，**显示名就是文件名去掉扩展名**
（例如 ``02-秋天.png`` → 「02-秋天」）。用户也可以把喜欢的图片丢进
exe 旁边的 ``backgrounds`` 文件夹，重启后就会出现在下拉框里。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple

#: 认这些扩展名
BACKGROUND_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")
#: 下拉框里"跟随皮肤 / 木纹"那一项的显示文字（存进 settings.json 的是空字符串）
DEFAULT_LABEL = "默认（皮肤 / 木纹）"


def background_dirs() -> List[Path]:
    """按优先级返回要扫描的目录：打包解包目录 → exe 旁边 → 源码目录。"""
    dirs: List[Path] = []
    override = os.environ.get("XQ_BACKGROUNDS_DIR")
    if override:
        dirs.append(Path(override))
    try:
        from ..session import APP_ROOT, bundle_dir, exe_dir

        dirs.extend([Path(bundle_dir()) / "backgrounds", Path(exe_dir()) / "backgrounds",
                     Path(APP_ROOT) / "backgrounds"])
    except Exception:  # noqa: BLE001
        dirs.append(Path(__file__).resolve().parents[2] / "backgrounds")
    seen, unique = set(), []
    for item in dirs:
        key = str(item).lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def list_backgrounds() -> List[Tuple[str, Path]]:
    """所有可用背景：``[(显示名, 路径)]``，按文件名排序（同名以靠前的目录为准）。"""
    found: List[Tuple[str, Path]] = []
    seen = set()
    for base in background_dirs():
        try:
            entries = sorted(base.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            continue
        for item in entries:
            if not item.is_file() or item.suffix.lower() not in BACKGROUND_EXTENSIONS:
                continue
            name = item.stem
            if name in seen:
                continue
            seen.add(name)
            found.append((name, item))
    return found


def find_background(name) -> Optional[Path]:
    """按显示名找背景图；空字符串返回 None（＝用皮肤背景或自带木纹）。"""
    text = str(name or "").strip()
    if not text:
        return None
    for item_name, path in list_backgrounds():
        if item_name == text:
            return path
    candidate = Path(text)
    if candidate.is_file():
        return candidate
    for base in background_dirs():
        for suffix in BACKGROUND_EXTENSIONS:
            candidate = base / f"{text}{suffix}"
            if candidate.is_file():
                return candidate
    return None


def choices() -> List[str]:
    """下拉框里的选项：默认 + 所有背景名。"""
    return [DEFAULT_LABEL] + [name for name, _path in list_backgrounds()]
