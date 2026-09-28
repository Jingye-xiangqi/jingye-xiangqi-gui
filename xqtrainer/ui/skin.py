# -*- coding: utf-8 -*-
"""皮肤：从文件夹里读棋盘图、棋子图，以及可选的 ``skin.json`` 对齐参数。

文件命名（大小写都认）::

    BA BB BC BK BN BP BR   黑方 士象炮将马卒车
    RA RB RC RK RN RP RR   红方 士象炮帅马兵车
    mask / mask2           选中棋子和落点提示的小框（两张颜色不同）

棋盘图能不能用、怎么对齐，取决于文件夹里的 ``skin.json``：里面记着这套棋盘图
中"8×9 网格"在图里的精确位置（边距比例等）。有它就按它对齐；没有就自动检测网格；
再不行就退回"程序自带的木纹棋盘"（楚河汉界不穿线）。
棋子图都是"圆形居中"的，统一缩放到 ``pieceScale``（默认 0.92）格贴到交叉点中心。

``mask`` / ``mask2`` 缺了就用程序自带的选中框、落点提示。
装了 Pillow 时用 Pillow 缩放（画质好），没有 Pillow 就用 Tk 自带的 PhotoImage。

``skin.json`` 支持的写法（都容错，能用哪个用哪个）::

    {
      "board": "board.png",                    # 可选：棋盘图文件名
      "grid": [x, y, w, h],                    # 8×9 网格在图里的位置
      "pieceScale": 0.92,                      # 可选：棋子大小（格）
      "adjust": {"dx": 0, "dy": 0, "scale": 100}
    }

* ``grid`` 也可以是 ``{"x":…, "y":…, "w":…, "h":…}``、
  ``{"left":…, "top":…, "right":…, "bottom":…}``；
* 数值全部 ≤ 1.5 时按"占图片宽高的比例"理解，否则按像素；
* 也支持 ``margin`` / ``margins``（四边边距）+ ``cell``（一格多少像素），
  或者 ``origin`` / ``topLeft``（网格左上角）+ ``cell``；
* 键名不区分大小写与下划线：``gridRect`` / ``grid_rect`` / ``boardRect`` 都认。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional

PIECE_FILES = {
    "A": "a", "B": "b", "C": "c", "K": "k", "N": "n", "P": "p", "R": "r",
}
EXTENSIONS = (".bmp", ".png", ".gif", ".ppm", ".pgm", ".jpg", ".jpeg")
#: 对齐参数文件名
SKIN_JSON = "skin.json"
#: 默认棋子大小（格）
DEFAULT_PIECE_SCALE = 0.92
#: 默认皮肤（skin 文件夹的名字；打包成 exe 后解包目录每次都变，所以默认值写名字不写路径）
DEFAULT_SKIN_NAME = "0-秋分棋者"
#: 背景图文件名（可以带扩展名，也可以只是一个名字）
BACKGROUND_NAMES = ("background", "bg", "desk", "table")
#: 14 个棋子图的键（ra rb … bp）
PIECE_KEYS = tuple(f"{side}{key}" for side in ("r", "b")
                   for key in PIECE_FILES.values())
#: 网格校验：**内部线条**允许偏离"等间距网格"的最大像素数（超过就不敢拿它当棋盘底）
GRID_TOLERANCE = 2.0
#: 网格校验：最外圈两条线放宽一些 —— 不少棋盘图的外框画得比格线粗、或者故意外扩，
#: 不代表整张棋盘对不准
GRID_TOLERANCE_EDGE = 3.2
#: 网格校验：线条要比背景强多少倍才算"真是一条线"
GRID_PROMINENCE = 2.5


def is_piece_key(key: str) -> bool:
    """``ra`` / ``bp`` 这类棋子图的名字。"""
    name = str(key).strip().lower()
    return (len(name) == 2 and name[0] in ("r", "b")
            and name[1] in PIECE_FILES.values())


# ------------------------------------------------------------------ skin.json
def _norm_key(key) -> str:
    return str(key).strip().lower().replace("_", "").replace("-", "")


def lookup(data, *names):
    """按键名取值（大小写、下划线、连字符都不计较）。"""
    if not isinstance(data, dict):
        return None
    table = {_norm_key(key): value for key, value in data.items()}
    for name in names:
        value = table.get(_norm_key(name))
        if value is not None:
            return value
    return None


def _number(value, size=None):
    """把 JSON 里的数值转成像素：≤1.5 视为"占图片宽/高的比例"。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if abs(number) <= 1.5:
        return number * size if size else None
    return number


def _truthy(value) -> bool:
    """skin.json 里的布尔写法（true/false、"0"/"否" 等）都认。"""
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off", "否")
    return bool(value)


def _pair(node):
    """把一个点（dict 或 [x, y]）转成 (x, y)。"""
    if isinstance(node, dict):
        x, y = lookup(node, "x", "left"), lookup(node, "y", "top")
    elif isinstance(node, (list, tuple)) and len(node) >= 2:
        x, y = node[0], node[1]
    else:
        return None
    return x, y


def _rect_from_node(node, image_size):
    """把 grid 的各种写法转成像素 (x0, y0, x1, y1)；认不出来返回 None。"""
    if node is None:
        return None
    width, height = image_size if image_size else (None, None)
    if isinstance(node, dict):
        left = _number(lookup(node, "left", "x", "x0", "l"), width)
        top = _number(lookup(node, "top", "y", "y0", "t"), height)
        right = _number(lookup(node, "right", "x1", "r"), width)
        bottom = _number(lookup(node, "bottom", "y1", "b"), height)
        box_w = _number(lookup(node, "w", "width", "gridW", "gridWidth"), width)
        box_h = _number(lookup(node, "h", "height", "gridH", "gridHeight"), height)
        if left is not None and top is not None:
            if box_w is not None and box_h is not None:
                return (left, top, left + box_w, top + box_h)
            if right is not None and bottom is not None:
                return (left, top, right, bottom)
        return None
    if isinstance(node, (list, tuple)):
        if len(node) == 2 and all(isinstance(item, (list, tuple)) for item in node):
            first, second = _pair(node[0]), _pair(node[1])
            if first and second:
                x0 = _number(first[0], width) or 0.0
                y0 = _number(first[1], height) or 0.0
                x1 = _number(second[0], width) or 0.0
                y1 = _number(second[1], height) or 0.0
                return (x0, y0, x1, y1)
        if len(node) >= 4:
            values = [_number(item, width if index % 2 == 0 else height)
                      for index, item in enumerate(node[:4])]
            if any(value is None for value in values):
                return None
            x, y, third, fourth = values
            # [x, y, w, h] 还是 [left, top, right, bottom]？看哪种更像"方格"
            as_wh = (third / 8.0, fourth / 9.0)
            as_ltrb = ((third - x) / 8.0, (fourth - y) / 9.0)

            def squareness(cell_x, cell_y):
                if cell_x <= 0 or cell_y <= 0:
                    return 9.9
                return abs(cell_x - cell_y) / max(cell_x, cell_y)

            if squareness(*as_wh) <= squareness(*as_ltrb):
                return (x, y, x + third, y + fourth)
            return (x, y, third, fourth)
    return None


def rect_interpretations(node, image_size):
    """把一个"4 个数的列表"两种可能的写法都算出来：[x,y,w,h] 或 [左,上,右,下]。

    单个写法没法从数字上分辨（``[47, 58, 416, 477]`` 既可能是宽高、也可能是右下角），
    所以核对网格时两种都试，让棋盘图里的真实线条来定哪个对。
    """
    rect = _rect_from_node(node, image_size)
    if rect is None:
        return []
    if not isinstance(node, (list, tuple)) or len(node) < 4 or isinstance(node[0], (list, tuple, dict)):
        return [rect]
    width, height = image_size if image_size else (None, None)
    values = [_number(item, width if index % 2 == 0 else height)
              for index, item in enumerate(node[:4])]
    if any(value is None for value in values):
        return [rect]
    x, y, third, fourth = values
    candidates = [rect]
    for other in ((x, y, x + third, y + fourth), (x, y, third, fourth)):
        if other not in candidates:
            candidates.append(other)
    return candidates


def _margins(node, image_size):
    """四边边距 → (left, top, right, bottom) 像素。"""
    width, height = image_size if image_size else (None, None)
    if isinstance(node, dict):
        left = _number(lookup(node, "left", "l", "x"), width)
        top = _number(lookup(node, "top", "t", "y"), height)
        right = _number(lookup(node, "right", "r"), width)
        bottom = _number(lookup(node, "bottom", "b"), height)
        if left is None:
            left = right
        if top is None:
            top = bottom
        if right is None:
            right = left
        if bottom is None:
            bottom = top
        if None not in (left, top, right, bottom):
            return left, top, right, bottom
        return None
    if isinstance(node, (list, tuple)):
        values = [_number(item, width) for item in node[:4]]
        if len(values) == 1:
            values = values * 4
        if len(values) == 2:
            values = [values[0], values[1], values[0], values[1]]
        if len(values) == 4 and all(value is not None for value in values):
            return tuple(values)                # left, top, right, bottom
        return None
    value = _number(node, width)
    if value is None:
        return None
    return value, value, value, value


def resolve_grid_rect(data, image_size):
    """把 skin.json 里的各种写法统一换算成像素 (x0, y0, x1, y1)。"""
    rect = _rect_from_node(
        lookup(data, "grid", "gridRect", "grid_rect", "boardRect", "board_rect",
               "rect", "area", "boardArea", "gridBox", "frame", "boardFrame"),
        image_size)
    if rect:
        return rect
    cell_x = cell_y = None
    node = lookup(data, "cell", "cellSize", "cell_size", "gridSize", "spacing")
    width = image_size[0] if image_size else None
    height = image_size[1] if image_size else None
    if isinstance(node, dict):
        cell_x = _number(lookup(node, "x", "w", "width"), width)
        cell_y = _number(lookup(node, "y", "h", "height"), height) or cell_x
    elif isinstance(node, (list, tuple)) and len(node) >= 2:
        cell_x = _number(node[0], width)
        cell_y = _number(node[1], height)
    elif node is not None:
        value = _number(node, width)              # 一格是正方形
        cell_x = cell_y = value
    origin = _pair(lookup(data, "origin", "topLeft", "top_left", "leftTop", "start",
                          "p0", "firstPoint"))
    if origin is not None and cell_x and cell_y:
        x0 = _number(origin[0], width)
        y0 = _number(origin[1], height)
        if x0 is not None and y0 is not None:
            return (x0, y0, x0 + cell_x * 8, y0 + cell_y * 9)
    margins = _margins(lookup(data, "margin", "margins", "padding", "inset", "border"),
                       image_size)
    if margins and image_size:
        left, top, right, bottom = margins
        return (left, top, image_size[0] - right, image_size[1] - bottom)
    return None


def parse_grid_ratio(data):
    """用户脚本的写法：``margin_left`` / ``margin_top`` / ``grid_w`` / ``grid_h``（比例）。

    返回 ``(left, top, w, h)``，都是相对图片宽高的 0~1 比例；认不出来返回 None。
    """
    if not isinstance(data, dict):
        return None
    left = lookup(data, "marginLeft", "margin_left", "marginX", "gridLeft", "grid_x")
    top = lookup(data, "marginTop", "margin_top", "marginY", "gridTop", "grid_y")
    width = lookup(data, "gridW", "grid_w", "gridWidth", "grid_width")
    height = lookup(data, "gridH", "grid_h", "gridHeight", "grid_height")
    if None in (left, top, width, height):
        return None
    try:
        values = [float(left), float(top), float(width), float(height)]
    except (TypeError, ValueError):
        return None
    if any(value > 1.5 for value in values):
        return None                       # 这几个键按比例写，>1.5 说明不是这套写法
    left, top, width, height = values
    if width <= 0.05 or height <= 0.05 or width > 1.5 or height > 1.5:
        return None
    return (left, top, width, height)


def parse_board_ratio(data):
    """棋盘图占画布的比例（``board_w_ratio`` / ``board_h_ratio``），默认铺满。"""
    if not isinstance(data, dict):
        return (1.0, 1.0)
    width = lookup(data, "boardWRatio", "board_w_ratio", "boardWidthRatio")
    height = lookup(data, "boardHRatio", "board_h_ratio", "boardHeightRatio")
    try:
        width = float(width)
    except (TypeError, ValueError):
        width = 1.0
    try:
        height = float(height)
    except (TypeError, ValueError):
        height = 1.0
    if width > 1.5:                       # 也可能是百分比写法
        width /= 100.0
    if height > 1.5:
        height /= 100.0
    return (max(0.1, min(1.5, width)), max(0.1, min(1.5, height)))


def parse_piece_scale(data) -> float:
    """棋子大小（相对格子），默认 0.92；也认百分比写法。"""
    for node in (data, data.get("pieces") if isinstance(data, dict) else None,
                 data.get("piece") if isinstance(data, dict) else None):
        if not isinstance(node, dict):
            continue
        value = lookup(node, "pieceScale", "piece_scale", "scale", "size", "pieceSize")
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 2.0:                        # 例如 92 表示 92%
            number /= 100.0
        if 0.3 <= number <= 1.5:
            return number
    return DEFAULT_PIECE_SCALE


def parse_adjust(data) -> Dict[str, float]:
    """skin.json 里的手工微调：``adjust`` = {dx, dy, scale}（dx/dy 单位是格）。"""
    node = lookup(data, "adjust", "offset", "fineTune", "tweak")
    result: Dict[str, float] = {}
    if isinstance(node, dict):
        dx = lookup(node, "dx", "x", "left")
        dy = lookup(node, "dy", "y", "top")
        scale = lookup(node, "scale", "zoom", "size")
    elif isinstance(node, (list, tuple)) and len(node) >= 2:
        dx, dy, scale = node[0], node[1], None
    else:
        dx = lookup(data, "dx")
        dy = lookup(data, "dy")
        scale = lookup(data, "pieceScalePercent")
    for key, value in (("dx", dx), ("dy", dy), ("scale", scale)):
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if key == "scale":
            result[key] = max(40.0, min(250.0, number))
        else:
            result[key] = max(-3.0, min(3.0, number))
    return result


def _rect_is_sane(rect, image_size) -> bool:
    """参数是否靠谱：矩形在图内、格子接近正方形、棋盘别太小。"""
    if rect is None:
        return False
    x0, y0, x1, y1 = rect
    if x1 <= x0 or y1 <= y0:
        return False
    cell_x, cell_y = (x1 - x0) / 8.0, (y1 - y0) / 9.0
    if cell_x <= 1 or cell_y <= 1:
        return False
    if abs(cell_x - cell_y) > max(cell_x, cell_y) * 0.3:
        return False                              # 格子明显不是方的 → 参数不对
    if image_size:
        width, height = image_size
        if x0 < -2 or y0 < -2 or x1 > width + 2 or y1 > height + 2:
            return False                          # 超出了图的范围
        if (x1 - x0) < width * 0.25 or (y1 - y0) < height * 0.25:
            return False                          # 网格太小，多半认错了
    return True


def board_image_size(folder: Path, board_node, data) -> Optional[tuple]:
    """找棋盘图（board.png 等）并返回像素尺寸；没有/读不了返回 None。"""
    name = board_node if isinstance(board_node, str) else None
    if name is None and isinstance(board_node, dict):
        for key in ("file", "path", "name", "image", "src"):
            value = board_node.get(key)
            if isinstance(value, str) and value.strip():
                name = value.strip()
                break
    if name is None:
        for key in ("boardFile", "board_file", "boardImage", "board_image",
                    "image", "file"):
            value = data.get(key) if isinstance(data, dict) else None
            if isinstance(value, str) and value.strip():
                name = value.strip()
                break
    candidates = []
    if name:
        candidates.append(Path(folder) / name)
        candidates.append(Path(folder) / Path(name).name)
    candidates.extend(sorted(Path(folder).glob("board.*")))
    for path in candidates:
        try:
            if not path.is_file():
                continue
            from PIL import Image

            with Image.open(path) as image:
                return image.size
        except Exception:  # noqa: BLE001
            continue
    return None


#: 自动检测结果缓存：{文件路径: (mtime, 结果)}
_GRID_CACHE: Dict[str, tuple] = {}


def image_size(path: Path):
    """图片像素尺寸（读不了返回 None）。"""
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.size
    except Exception:  # noqa: BLE001
        return None


def alpha_bbox(path: Path):
    """去掉透明边之后的内容范围 (x0, y0, x1, y1)；没有透明边就返回整图。"""
    try:
        import numpy as np
        from PIL import Image

        with Image.open(path) as raw:
            image = raw.convert("RGBA")
            size = image.size
            array = np.asarray(image)
        alpha = array[:, :, 3]
        if alpha.min() >= 250:
            return (0, 0, size[0], size[1])
        ys, xs = np.where(alpha > 8)
        if len(xs) == 0:
            return (0, 0, size[0], size[1])
        return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    except Exception:  # noqa: BLE001
        return None


def _box_blur(gray, k: int = 9):
    """大尺度背景（均值滤波），用来突出"细线"。"""
    import numpy as np

    pad = k // 2
    padded = np.pad(gray, pad, mode="edge")
    integral = np.zeros((padded.shape[0] + 1, padded.shape[1] + 1), dtype=np.float64)
    integral[1:, 1:] = padded.cumsum(0).cumsum(1)
    height, width = gray.shape
    rows = np.arange(height)
    cols = np.arange(width)
    return (integral[rows + k][:, cols + k] - integral[rows][:, cols + k]
            - integral[rows + k][:, cols] + integral[rows][:, cols]) / (k * k)


def _evidence_map(gray, k: int = 9):
    """|像素 − 大尺度背景|：格线（比周围暗或亮的细线）在这里是高值。"""
    import numpy as np

    return np.abs(gray - _box_blur(gray, k))


def _comb_score(score, start: float, step: float, lines: int) -> float:
    """把 lines 条线按等间距摆上去，累加落点处的强度（允许 ±1px 取整误差）。"""
    import numpy as np

    index = np.round(start + step * np.arange(lines)).astype(int)
    if index[0] < 1 or index[-1] >= len(score) - 1:
        return -1.0
    return float(np.stack([score[index + offset]
                           for offset in (-1, 0, 1)]).max(axis=0).sum())


def _search_comb(score, lines: int, start_hint: float, step_hint: float,
                 span: float = 10.0):
    """在 (起点 ± span, 间距 ±3%) 里全局搜索最像"棋盘格线"的等间距网格。"""
    import numpy as np

    best = (-1.0, start_hint, step_hint)
    for start in np.arange(start_hint - span, start_hint + span + 0.001, 0.5):
        for factor in np.arange(0.97, 1.0301, 0.002):
            step = step_hint * factor
            value = _comb_score(score, start, step, lines)
            if value > best[0]:
                best = (value, float(start), float(step))
    _, coarse_start, coarse_step = best
    for start in np.arange(coarse_start - 0.6, coarse_start + 0.601, 0.1):
        for factor in np.arange(0.997, 1.0031, 0.0004):
            step = coarse_step * factor
            value = _comb_score(score, start, step, lines)
            if value > best[0]:
                best = (value, float(start), float(step))
    return best


def _auto_fit_axis(score, lines: int, low_ratio: float = 0.05, high_ratio: float = 0.16):
    """没有任何初值时，直接扫"间距 + 起点"找最像棋盘的那把梳子。

    做法：把剖面按 0.5px 上采样，于是"起点取某个 0.5px 位置"的打分就是
    lines 个整数位移切片相加 —— 全部向量化，比频谱估周期稳得多也快得多。
    """
    import numpy as np

    length = len(score)
    if length < lines * 4:
        return None
    values = np.asarray(score, dtype=np.float64)
    fine = np.interp(np.arange(0.0, length - 1.0 + 1e-9, 0.5), np.arange(length), values)
    low = max(8.0, length * low_ratio)
    high = min(length / float(max(3, lines - 2)), length * high_ratio)
    if high <= low or len(fine) < lines * 2:
        return None
    best = None
    for step in np.arange(low, high + 1e-9, 0.25):
        shifts = np.round(step * np.arange(lines) / 0.5).astype(int)
        span = len(fine) - int(shifts[-1]) - 1
        if span < 4:
            continue
        totals = np.zeros(span)
        for shift in shifts:
            totals += fine[int(shift):int(shift) + span]
        position = int(np.argmax(totals))
        if best is None or totals[position] > best[0]:
            best = (float(totals[position]), position * 0.5, float(step))
    if best is None:
        return None
    _, start, step = best
    return _fit_axis(score, lines, start, step, span=2.0)


def _line_center(score, position: float, window: int = 2):
    """线条中心：先在 ±window 取峰，再在峰附近做强度加权重心（抗模糊/双像素线）。"""
    import numpy as np

    length = len(score)
    low = max(0, int(round(position)) - window)
    high = min(length, int(round(position)) + window + 1)
    if high - low < 3:
        return None
    peak = low + int(np.argmax(score[low:high]))
    left, right = max(0, peak - 2), min(length, peak + 3)
    weights = np.maximum(np.array(score[left:right], dtype=np.float64)
                         - float(np.min(score[left:right])), 0.0)
    total = float(weights.sum())
    if total <= 1e-9:
        return float(peak), float(score[peak])
    positions = np.arange(left, right, dtype=np.float64)
    return float((positions * weights).sum() / total), float(score[peak])


def _fit_axis(score, lines: int, start_hint: float, step_hint: float,
              span: float = 10.0):
    """在一条轴上找 lines 条等间距线；返回 {起始, 间距, 残差, 显著度}。"""
    import numpy as np

    if not len(score):
        return None
    _, start, step = _search_comb(score, lines, start_hint, step_hint, span)
    positions, values = [], []
    for index in range(lines):
        found = _line_center(score, start + step * index)
        if found is None:
            return None
        positions.append(found[0])
        values.append(found[1])
    positions = np.array(positions, dtype=np.float64)
    order = np.arange(lines)
    fit_step, fit_start = np.polyfit(order, positions, 1)
    residual = positions - (fit_start + fit_step * order)
    baseline = float(np.median(score))
    values = np.array(values, dtype=np.float64)
    return {
        "start": float(fit_start),
        "step": float(fit_step),
        "residual": residual,
        "max_residual": float(np.abs(residual).max()),
        "prominence": float((values / (baseline + 1e-6)).min()),
        "positions": positions,
    }


def analyse_board_grid(path: Path, hint=None, span: float = 12.0,
                       image_size=None, content_box=None) -> Optional[Dict[str, object]]:
    """在棋盘图里找 8×9 网格，并判断"能不能信赖"。

    返回 ``{rect, residual, prominence, ok, cell}``；读不了图或算不出来返回 None。

    * ``hint``：``(x0, y0, x1, y1)`` 的初值（一般来自 skin.json），给了就在它附近找；
      不给就用频谱估间距。
    * ``ok``：线条本身够明显（prominence）**而且**每条线都落在等间距网格上
      （残差 ≤ :data:`GRID_TOLERANCE`）。只有 ``ok`` 的网格才拿来贴棋盘图，
      否则一律改用"程序自画棋盘"（格线绝对准），棋子仍然用这套皮肤。
    """
    try:
        import numpy as np
        from PIL import Image
    except Exception:  # noqa: BLE001
        return None
    try:
        with Image.open(path) as raw:
            image = raw.convert("RGBA")
            size = image.size
            offset_x = offset_y = 0
            array = np.asarray(image)
            if array[:, :, 3].min() < 250:            # 有透明边 → 先裁掉
                ys, xs = np.where(array[:, :, 3] > 8)
                if len(xs) == 0:
                    return None
                offset_x, offset_y = int(xs.min()), int(ys.min())
                image = image.crop((offset_x, offset_y, int(xs.max()) + 1,
                                    int(ys.max()) + 1))
            gray = np.asarray(image.convert("L"), dtype=np.float32)
    except Exception:  # noqa: BLE001
        return None
    height, width = gray.shape
    evidence = _evidence_map(gray)
    if hint is not None:
        hx0, hy0, hx1, hy1 = [float(value) - offset for value, offset in
                               zip(hint, (offset_x, offset_y, offset_x, offset_y))]
        rows = slice(int(max(0, hy0)), int(min(height, hy1 + 1)))
        cols = slice(int(max(0, hx0)), int(min(width, hx1 + 1)))
        col_hint, row_hint = hx0, hy0
        col_step, row_step = (hx1 - hx0) / 8.0, (hy1 - hy0) / 9.0
        if not (4 < col_step < width * 0.5 and 4 < row_step < height * 0.5):
            return None
        col_score = evidence[rows, :].mean(axis=0)
        row_score = evidence[:, cols].mean(axis=1)
    else:
        rows = slice(0, height)
        cols = slice(0, width)
        col_score = evidence[:, :].mean(axis=0)
        row_score = evidence[:, :].mean(axis=1)
        fit_x = _auto_fit_axis(col_score, 9)
        fit_y = _auto_fit_axis(row_score, 10)
        if fit_x is None or fit_y is None:
            return None
        col_hint, col_step = fit_x["start"], fit_x["step"]
        row_hint, row_step = fit_y["start"], fit_y["step"]
        span = 2.0
    fit_x = _fit_axis(col_score, 9, col_hint, col_step, span)
    fit_y = _fit_axis(row_score, 10, row_hint, row_step, span)
    if fit_x is None or fit_y is None:
        return None
    rect = (fit_x["start"] + offset_x, fit_y["start"] + offset_y,
            fit_x["start"] + fit_x["step"] * 8 + offset_x,
            fit_y["start"] + fit_y["step"] * 9 + offset_y)
    residual = max(fit_x["max_residual"], fit_y["max_residual"])
    inner = max(float(np.abs(fit_x["residual"][1:-1]).max()),
                float(np.abs(fit_y["residual"][1:-1]).max()))
    prominence = min(fit_x["prominence"], fit_y["prominence"])
    ok = (inner <= GRID_TOLERANCE and residual <= GRID_TOLERANCE_EDGE
          and prominence >= GRID_PROMINENCE
          and _rect_is_sane(rect, image_size or size))
    return {"rect": rect, "residual": residual, "inner": inner,
            "prominence": prominence, "ok": ok, "cell": (fit_x["step"], fit_y["step"])}


def _cached_grid(path: Path, hint, span: float):
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    key = (str(path), None if hint is None else tuple(round(float(v), 2) for v in hint),
           float(span))
    cached = _GRID_CACHE.get(key)
    if cached is not None and cached[0] == stat.st_mtime:
        return cached[1]
    result = analyse_board_grid(path, hint, span)
    if len(_GRID_CACHE) > 8:                     # 只留最近几套，别把内存撑大
        _GRID_CACHE.clear()
    _GRID_CACHE[key] = (stat.st_mtime, result)
    return result


def detect_board_grid(path: Path):
    """没有 skin.json 时兜底：自动找棋盘图里 8×9 网格的位置（像素坐标）。

    结果会做可信度检查：线条够明显、每条线都落在等间距网格上、格子接近正方、
    棋盘占图里足够大；不可信就返回 None（调用方改用自带棋盘）。
    """
    info = _cached_grid(Path(path), None, 12.0)
    if not info or not info["ok"]:
        return None
    return info["rect"]


def verify_board_grid(path: Path, rect, span: float = 12.0):
    """核对 skin.json 给的网格：返回精修后的结果（``ok`` 为 False 就不能贴棋盘图）。"""
    return _cached_grid(Path(path), rect, span)


class Skin:
    """一套皮肤（棋盘 + 14 个棋子 + 两个提示框）。"""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.name = self.folder.name
        self.files: Dict[str, Path] = {}
        self.missing: list = []
        self.optional_missing: list = []
        #: skin.json 的内容（没有就是空 dict）
        self.meta: Dict[str, object] = {}
        #: 棋盘图里 8×9 网格的位置（像素，x0/y0/x1/y1）；None＝没有/没解析出来
        self.grid_rect = None
        #: 这个 grid_rect 是哪来的：``skin.json`` / ``auto`` / ""
        self.grid_source = ""
        #: board.png 里有没有完整的 8×9 网格。
        #: 有些皮肤自带的棋盘图是被放大/裁切过的（只有棋盘的一角），
        #: 这种图无论怎么对都必然错位，skin.json 里会写 ``board_grid_usable: false``，
        #: 界面就改用自带棋盘底 + 该皮肤的材质配色，棋子仍然用本皮肤。
        self.board_usable = True
        #: 网格在图里的位置（比例 0~1：(left, top, w, h)），来自 skin.json
        self.grid_ratio = None
        #: skin.json 给的网格（像素）—— 只当"初值"，resolve_grid() 时再核对/精修
        self.grid_hint = None
        #: 候选初值（``[x,y,w,h]`` 与 ``[左,上,右,下]`` 两种解释都留着）
        self.grid_hints = []
        #: 校验结果：{rect, residual, prominence, ok, cell}
        self.grid_check: Dict[str, object] = {}
        #: 为什么没用棋盘图（给日志和皮肤说明用）
        self.grid_note = ""
        #: skin.json 里写了 ``board_grid_force: true`` 时：不做校验，直接用写的网格
        self.grid_force = False
        self._grid_resolved = False
        self._piece_metrics = None
        #: 棋盘图占画布的比例（默认铺满：(1.0, 1.0)）
        self.board_ratio = (1.0, 1.0)
        #: 棋子图相对格子的大小（默认 0.92）
        self.piece_scale = DEFAULT_PIECE_SCALE
        #: 手工微调（来自 skin.json）：dx/dy 单位是格、scale 是百分比
        self.adjust: Dict[str, float] = {}
        self._scan()
        self._load_meta()

    def _scan(self) -> None:
        """按文件名找图片（大小写不敏感，扩展名按优先级挑）。"""
        found: Dict[str, Path] = {}
        for path in sorted(self.folder.iterdir() if self.folder.is_dir() else []):
            if not path.is_file():
                continue
            if path.suffix.lower() not in EXTENSIONS:
                continue
            key = path.stem.lower()
            if key in found:                       # 已经有更高优先级的扩展名
                continue
            found[key] = path
            for alias in BACKGROUND_NAMES:
                if key == alias:                   # background.png 也当作 background
                    found["background"] = path
        self.files = found
        for piece in PIECE_FILES:
            for prefix in ("r", "b"):
                if f"{prefix}{PIECE_FILES[piece]}" not in found:
                    self.missing.append(f"{prefix}{PIECE_FILES[piece]}")
        # mask / mask2 是可选的（没有就用程序自带的选中框、落点提示）
        self.optional_missing = [name for name in ("mask", "mask2")
                                 if name not in found]

    @property
    def ok(self) -> bool:
        """14 个棋子图片齐全才算一套能用的棋子皮肤。"""
        return not self.missing

    def describe(self) -> str:
        if not self.folder.is_dir():
            return "皮肤文件夹不存在"
        text = f"{self.name}：{len(self.files)} 个图片文件"
        if self.missing:
            text += f"；缺少棋子 {'、'.join(self.missing[:6])}"
        return text

    def path_of(self, key: str) -> Optional[Path]:
        return self.files.get(key.lower())

    # ------------------------------------------------------------- skin.json
    def _load_meta(self) -> None:
        """读 ``skin.json``：解析棋盘图的对齐参数（容错，坏文件不影响用皮肤）。"""
        path = self.folder / SKIN_JSON
        if not path.is_file():
            return
        try:
            text = path.read_text(encoding="utf-8-sig")
            data = json.loads(text)
            if isinstance(data, dict):
                self.meta = data
                self._apply_meta(data)
        except Exception:  # noqa: BLE001
            self.meta = {}

    def _apply_meta(self, data: Dict[str, object]) -> None:
        """把 skin.json 里的参数换算成"像素网格矩形 + 棋子比例 + 微调"。"""
        info = data
        # 允许 {"board": {"file": "...", "rect": [...]}} 这种嵌套写法
        board = data.get("board")
        if isinstance(board, dict):
            info = dict(data)
            info.update(board)
        image_size = board_image_size(self.folder, board, data)
        rect = resolve_grid_rect(info, image_size)
        flag = lookup(data, "boardGridUsable", "board_grid_usable",
                      "boardUsable", "board_usable")
        if flag is not None:
            self.board_usable = _truthy(flag)
        force = lookup(data, "boardGridForce", "board_grid_force", "forceBoardGrid",
                       "forceBoardRect")
        if force is not None:
            self.grid_force = _truthy(force)
        if rect is not None and not _rect_is_sane(rect, image_size):
            rect = None                          # 参数明显不对 → 交给自动检测
        if rect is not None:
            self.grid_hint = rect                # 只当"初值"，resolve_grid() 时再核对
            self.grid_hints = rect_interpretations(
                lookup(info, "grid", "gridRect", "grid_rect", "boardRect", "board_rect",
                       "rect", "area", "boardArea", "gridBox", "frame", "boardFrame"),
                image_size) or [rect]
            if not self.board_usable:
                rect = None                      # 图里没有完整网格 → 交给自带棋盘
        if rect is not None:
            self.grid_rect = rect
            self.grid_source = "skin.json"
            if image_size:
                width, height = image_size
                self.grid_ratio = (rect[0] / width, rect[1] / height,
                                   (rect[2] - rect[0]) / width,
                                   (rect[3] - rect[1]) / height)
        else:
            # 用户脚本的写法：给"网格占图片的比例"（具体位置稍后 resolve_grid() 定）
            ratio = parse_grid_ratio(info)
            if ratio is not None:
                self.grid_ratio = ratio
                self.grid_source = "skin.json"
        self.board_ratio = parse_board_ratio(info)
        self.piece_scale = parse_piece_scale(data)
        self.adjust = parse_adjust(data)

    @property
    def background_path(self) -> Optional[Path]:
        """桌面背景图（background.png 等），没有就返回 None。"""
        for name in BACKGROUND_NAMES:
            path = self.path_of(name)
            if path is not None:
                return path
        return None

    def resolve_grid(self) -> None:
        """确定"棋盘图里 8×9 网格"的位置 —— **能测准就校准，测不准就沿用 skin.json**。

        顺序：
          1. ``board_grid_force`` 为真 → 直接用 skin.json 写的网格（用户强制，不校验）；
          2. skin.json 给了网格 → 拿它当初值找真实的 9 竖 10 横：
             * 找得到而且"每条线都落在等间距网格上" → 用**校准后**的精确网格
               （这一步就把"棋子没压在交叉点"的毛病修好了）；
             * 测不准 → **沿用 skin.json 原来的参数**（不动它，也不会突然换画法）；
          3. skin.json 没给网格但给了比例 → 按比例算；
          4. 什么都没有 → 用程序自画棋盘（格线 100% 准）+ 本皮肤材质配色。
        """
        if self._grid_resolved:
            return
        self._grid_resolved = True
        board_path = self.path_of("board")
        if board_path is None or not self.board_usable:
            return                            # 没有棋盘图 / 明确说了图里没有完整网格
        if self.grid_force and self.grid_hint is not None:
            self.grid_rect = self.grid_hint
            self.grid_source = "skin.json(强制)"
            return
        size = image_size(board_path)
        info, source = self._detect_grid(board_path)
        if info and info["ok"]:
            self.grid_rect = info["rect"]     # 校准后的精确网格（亚像素）
            self.grid_check = info
            self.grid_source = "校准" if source == "skin.json" else "auto"
            if size:
                width, height = size
                self.grid_ratio = (self.grid_rect[0] / width, self.grid_rect[1] / height,
                                   (self.grid_rect[2] - self.grid_rect[0]) / width,
                                   (self.grid_rect[3] - self.grid_rect[1]) / height)
            return
        # 测不准 → 保留 skin.json 原来的说法
        if self.grid_hint is not None:
            self.grid_rect = self.grid_hint
            self.grid_source = "skin.json(未校准)"
            if info is not None:
                self.grid_note = (f"这张 board.png 的格线测不准（最大偏差 "
                                  f"{info['residual']:.1f}px、线条显著度 "
                                  f"{info['prominence']:.1f}），沿用 skin.json 的参数")
            return
        if self.grid_ratio is not None:
            rect = self._ratio_grid_rect(board_path)
            if rect is not None and _rect_is_sane(rect, size):
                self.grid_rect = rect
                self.grid_source = "skin.json(比例)"
                return
        # 连参数都没有 → 交给程序自画棋盘（皮肤配色照旧）
        self.grid_rect = None
        self.board_usable = False
        self.grid_source = ""
        if info is not None:
            self.grid_note = (f"棋盘图里的网格和等间距棋盘对不齐"
                              f"（最大偏差 {info['residual']:.1f}px、线条显著度 "
                              f"{info['prominence']:.1f}）")
        else:
            self.grid_note = "棋盘图里没找到可信的 8×9 网格"

    def _detect_grid(self, board_path: Path):
        """在棋盘图里核对/精修网格：``[x,y,w,h]`` 和 ``[l,t,r,b]`` 两种解释都试。

        返回 ``(info, 来源)``；``info`` 可能为 None（读不了图）。
        """
        hints = [item for item in (self.grid_hints or
                                   ([self.grid_hint] if self.grid_hint else []))
                 if item is not None]
        if not hints:
            ratio_rect = self._ratio_grid_rect(board_path)
            if ratio_rect is not None:
                hints = [ratio_rect]
        if not hints:
            return _cached_grid(board_path, None, 12.0), "auto"
        best = None
        for candidate in hints:
            result = verify_board_grid(board_path, candidate)
            if result is None:
                continue
            if result["ok"]:
                return result, "skin.json"
            if best is None or result["prominence"] > best["prominence"]:
                best = result
        return best, "skin.json"

    def _ratio_grid_rect(self, board_path: Path):
        """用 skin.json 里的比例算出"网格大概在哪"，只当检测初值用。"""
        ratio = self.grid_ratio
        if ratio is None:
            return None
        box = alpha_bbox(board_path)
        if box is None:
            return None
        content_w, content_h = box[2] - box[0], box[3] - box[1]
        if content_w < 8 or content_h < 8:
            return None
        grid_w, grid_h = ratio[2] * content_w, ratio[3] * content_h
        return (box[0] + ratio[0] * content_w + (content_w - grid_w) / 2,
                box[1] + ratio[1] * content_h + (content_h - grid_h) / 2,
                box[0] + ratio[0] * content_w + (content_w + grid_w) / 2,
                box[1] + ratio[1] * content_h + (content_h + grid_h) / 2)

    def grid_hint_rect(self):
        """检测/核对网格时的初值：skin.json 的像素矩形，或按比例算出来的大致位置。"""
        if self.grid_hint is not None:
            return self.grid_hint
        board_path = self.path_of("board")
        if board_path is None:
            return None
        return self._ratio_grid_rect(board_path)

    # ------------------------------------------------------------- 棋子居中
    def piece_crop(self, key: str):
        """棋子图该怎么裁，才能让"棋子圆心"正好落在交叉点上。

        返回 ``(中心 x, 中心 y, 边长, 左, 上)``：把图按中心裁成一个正方形、
        再缩放成目标大小，棋子圆心就必然在正中间（不管原图的透明边有多歪）。
        没有透明边（或读不了图）返回 None，调用方按原图处理。
        """
        metrics = self._piece_metrics
        if metrics is None:
            metrics = self._piece_metrics = self._measure_pieces()
        entry = metrics.get(str(key).lower())
        if entry is None:
            return None
        center_x, center_y, side = entry
        return center_x, center_y, side

    def _measure_pieces(self) -> Dict[str, tuple]:
        """量 14 个棋子图的内容框，算出一套皮肤共用的"圆心"和"直径"。

        用 14 个棋子的**中位数**当圆心：个别棋子的字特别长（比如"车"）时不会把
        整套棋子带歪，同时保留同一套皮肤里各棋子的大小关系。
        """
        try:
            import numpy as np
            from PIL import Image
        except Exception:  # noqa: BLE001
            return {}
        measured: Dict[str, tuple] = {}
        centers, sides = [], []
        for key in PIECE_KEYS:
            path = self.path_of(key)
            if path is None:
                continue
            try:
                with Image.open(path) as raw:
                    image = raw.convert("RGBA")
                    size = image.size
                    array = np.asarray(image)
                alpha = array[:, :, 3]
                if int(alpha.max()) < 8:
                    continue
                mask = alpha > 64
                if not mask.any():
                    mask = alpha > 8
                ys, xs = np.where(mask)
                x0, x1 = int(xs.min()), int(xs.max()) + 1
                y0, y1 = int(ys.min()), int(ys.max()) + 1
                side = float(max(x1 - x0, y1 - y0))
                if side < size[0] * 0.25:
                    continue                   # 内容太小 → 多半不是"圆棋子"，别动它
                measured[key] = ((x0 + x1) / 2.0, (y0 + y1) / 2.0, side)
                centers.append(((x0 + x1) / 2.0, (y0 + y1) / 2.0))
                sides.append(side)
            except Exception:  # noqa: BLE001
                continue
        if not centers:
            return {}
        center_x = float(np.median([item[0] for item in centers]))
        center_y = float(np.median([item[1] for item in centers]))
        side = float(np.median(sides))
        result = {}
        for key, (px, py, own) in measured.items():
            result[key] = (center_x, center_y, max(side, own))
        return result


def _strip_index(name: str) -> str:
    """去掉文件夹名开头的序号前缀：``0-秋分棋者`` → ``秋分棋者``。"""
    text = str(name or "").strip()
    for separator in ("-", "_", " ", "."):
        head, sep, tail = text.partition(separator)
        if sep and tail and head.isdigit():
            return tail.strip()
    return text


def resolve_skin_path(value) -> Optional[Path]:
    """把设置里的皮肤值解析成**皮肤文件夹**。

    三种写法都认：

    * 空字符串 → None（＝用程序自带的矢量画法）；
    * 文件夹全路径（用户点「选择文件夹…」选的那种）；
    * **只有名字**（例如默认值 ``0-秋分棋者``，或者只写 ``秋分棋者``）——
      在各个 skins 目录里按"完全相同 → 去掉开头编号相同 → 包含"的顺序找。
      打包成 exe 后解包目录每次都变，写名字才不会失效。
    """
    text = str(value or "").strip().strip('"')
    if not text:
        return None
    if text.startswith("@skin/"):
        text = text[len("@skin/"):]
    candidate = Path(text)
    try:
        if candidate.is_dir():                        # 本来就给了有效路径
            return candidate
    except OSError:
        pass
    name = candidate.name if (os.sep in text or "/" in text) else text
    stripped = _strip_index(name)
    folders = []
    for base in skins_roots():
        try:
            folders.extend(sorted([item for item in base.iterdir() if item.is_dir()],
                                  key=lambda item: item.name.lower()))
        except OSError:
            continue
    for wanted in (name, stripped):
        if not wanted:
            continue
        for folder in folders:
            if folder.name == wanted:
                return folder
    for wanted in (name, stripped):
        if not wanted:
            continue
        for folder in folders:
            if _strip_index(folder.name) == wanted:
                return folder
    for wanted in (name, stripped):
        if wanted:
            for folder in folders:
                if wanted in folder.name:
                    return folder
    return None


def find_skin_folders(root: Optional[Path] = None) -> list:
    """在桌面 / 程序目录附近找可能的皮肤文件夹（含 board 与棋子的）。"""
    candidates = []
    roots = []
    if root is not None:
        roots.append(Path(root))
    home = Path.home()
    roots.extend([home / "Desktop", home / "桌面", Path.cwd()])
    for base in roots:
        try:
            if not base.is_dir():
                continue
            for item in base.iterdir():
                if item.is_dir():
                    skin = Skin(item)
                    if len(skin.files) >= 8:
                        candidates.append(skin)
        except OSError:
            continue
    return candidates


def skins_root() -> Path:
    """皮肤根目录：程序（exe）旁边的 ``skins`` 文件夹。

    测试或特殊场景可以用环境变量 ``XQ_SKINS_DIR`` 指定别的目录。
    """
    override = os.environ.get("XQ_SKINS_DIR")
    if override:
        return Path(override)
    try:
        from ..session import exe_dir

        return Path(exe_dir()) / "skins"
    except Exception:  # noqa: BLE001
        return Path.cwd() / "skins"


def skins_roots() -> list:
    """所有要扫描的皮肤目录（按优先级）。

    * 环境变量 ``XQ_SKINS_DIR``（测试用）；
    * 打包进 exe 的 ``skins``（PyInstaller 解包目录里的那份，78 套内置皮肤）；
    * exe 旁边的 ``skins``（用户自己放的，同名会覆盖内置的那套）。
    """
    override = os.environ.get("XQ_SKINS_DIR")
    if override:
        return [Path(override)]
    roots = []
    try:
        from ..session import bundle_dir, exe_dir

        bundled = Path(bundle_dir()) / "skins"
        beside = Path(exe_dir()) / "skins"
        if bundled != beside:
            roots.append(bundled)          # 内置的排前面，用户自己的覆盖它
        roots.append(beside)
    except Exception:  # noqa: BLE001
        roots.append(Path.cwd() / "skins")
    return roots


def scan_skins(root: Optional[Path] = None) -> list:
    """扫描皮肤根目录，返回 [{name, path, count, missing}]。

    每个子文件夹算**一套棋子皮肤**（放 ba/bb/…/rr 这 14 个棋子图，mask、mask2 可选）。
    只有 14 个棋子齐全的文件夹才会列出来（转换到一半的文件夹先跳过）。
    按文件夹名字排序，名字前面的数字（0-、1-…）就当作顺序。
    """
    roots = [Path(root)] if root is not None else skins_roots()
    found: Dict[str, dict] = {}
    order: list = []
    for base in roots:
        try:
            entries = sorted(base.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            continue
        for item in entries:
            try:
                if not item.is_dir():
                    continue
                skin = Skin(item)
                if skin.missing:                # 14 个棋子必须齐全
                    continue
                if skin.name in found:          # 后面的目录（用户自己的）覆盖前面的
                    found[skin.name].update({"path": str(item),
                                             "count": len(skin.files)})
                else:
                    found[skin.name] = {"name": skin.name, "path": str(item),
                                        "count": len(skin.files), "missing": []}
                    order.append(skin.name)
            except OSError:
                continue
    return [found[name] for name in order]
