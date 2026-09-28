# -*- coding: utf-8 -*-
"""局面的图片导出与识别（可选功能：装了 Pillow 才能用）。

* :func:`render_position` 把局面画成一张图片（导出 / 复制到剪贴板）
* :func:`recognize_position` 从图片里认出棋盘与棋子，返回 FEN（粘贴 / 拖拽图片导入）

识别思路：先用投影找到 9 条竖线与 10 条横线（也就是棋盘的 9×10 个交点），再按
颜色区分"空格 / 红子 / 黑子"，最后把每个子的笔迹与 7 种字形的模板比对（先按笔迹
包围盒归一化，所以字号、字体略有差别也能对上）。识别结果会交给用户确认后再应用。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

try:                                        # 可选依赖：没有 Pillow 时功能自动关闭
    from PIL import Image, ImageChops, ImageDraw, ImageFont

    PIL_OK = True
    PIL_ERROR = ""
except Exception as exc:  # noqa: BLE001
    Image = None                            # type: ignore[assignment]
    PIL_OK = False
    PIL_ERROR = str(exc)

from ..board import FILES
from .board_view import GLYPHS_SIMPLE, THEMES

RED_INK = "#b3271f"
BLACK_INK = "#25303a"
READ_RED = "r"
READ_BLACK = "b"

#: 认字用的字体（按顺序找第一个存在的）
FONT_CANDIDATES = (
    r"C:\Windows\Fonts\simkai.ttf", r"C:\Windows\Fonts\kaiu.ttf",
    r"C:\Windows\Fonts\simsun.ttc", r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
)

CHINESE_DIGITS = "一二三四五六七八九"


def available() -> bool:
    """图片功能是否可用（需要 Pillow）。"""
    return PIL_OK


def unavailable_reason() -> str:
    return PIL_ERROR or "没有安装 Pillow"


def _font_path() -> Optional[str]:
    import os

    for candidate in FONT_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def _load_font(size: int):
    path = _font_path()
    if path is None:
        return ImageFont.load_default()
    try:
        return ImageFont.truetype(path, size)
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


def _rgb(value: str) -> Tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _expand_row(row: str) -> List[str]:
    """把 FEN 的一行（带数字段）展开成 9 个格子。"""
    cells: List[str] = []
    for char in str(row):
        if char.isdigit():
            cells.extend(["."] * int(char))
        else:
            cells.append(char)
    if len(cells) < 9:
        cells.extend(["."] * (9 - len(cells)))
    return cells[:9]


def _pack_row(cells: List[str]) -> str:
    """9 个格子 → FEN 的一行（空格子合并成数字）。"""
    text: List[str] = []
    empty = 0
    for cell in cells:
        if cell == ".":
            empty += 1
            continue
        if empty:
            text.append(str(empty))
            empty = 0
        text.append(cell)
    if empty:
        text.append(str(empty))
    return "".join(text) or "9"


def render_position(board_rows: List[str], side: str = "w", *, cell: int = 72,
                    theme: str = "wood", show_numbers: bool = True,
                    traditional: bool = False, title: str = "") -> "Image.Image":
    """把局面（10 行棋盘字符串）画成图片。"""
    if not PIL_OK:
        raise RuntimeError(f"图片功能不可用：{unavailable_reason()}")
    palette = THEMES.get(theme, THEMES["wood"])
    light, dark = _rgb(palette["light"]), _rgb(palette["dark"])
    line, river = _rgb(palette["line"]), _rgb(palette["river"])
    face, edge = _rgb(palette["face"]), _rgb(palette["piece_edge"])
    shadow = _rgb(palette["shadow"])

    margin = int(cell * 0.62)
    width = margin * 2 + cell * 8
    height = margin * 2 + cell * 9
    header = int(cell * 0.5) if title else 0
    image = Image.new("RGB", (width, height + header), light)
    draw = ImageDraw.Draw(image)
    offset_y = header

    # 木纹底纹
    stripes = max(8, height // max(6, int(cell * 0.42)))
    for index in range(stripes):
        y = offset_y + margin + index * (cell * 9 / stripes)
        blend = tuple(int(light[i] * 0.5 + dark[i] * 0.5) for i in range(3))
        draw.line([(margin * 0.4, y), (width - margin * 0.4, y)], fill=blend, width=1)

    x0, y0 = margin, margin + offset_y
    x1, y1 = margin + cell * 8, margin + cell * 9
    line_width = max(1, int(cell * 0.028))
    # 横线
    for rank in range(10):
        y = y0 + cell * (9 - rank)
        draw.line([(x0, y), (x1, y)], fill=line, width=line_width)
    # 竖线（中间在河界断开）
    for file in range(9):
        x = x0 + cell * file
        if file in (0, 8):
            draw.line([(x, y0), (x, y1)], fill=line, width=line_width)
        else:
            draw.line([(x, y0), (x, y0 + cell * 4)], fill=line, width=line_width)
            draw.line([(x, y0 + cell * 5), (x, y1)], fill=line, width=line_width)
    # 九宫斜线
    for fa, ra, fb, rb in ((3, 0, 5, 2), (5, 0, 3, 2), (3, 7, 5, 9), (5, 7, 3, 9)):
        draw.line([(x0 + fa * cell, y0 + cell * (9 - ra)), (x0 + fb * cell,
                  y0 + cell * (9 - rb))], fill=line, width=line_width)
    # 外框
    draw.rectangle([x0, y0, x1, y1], outline=line, width=max(2, int(cell * 0.05)))
    # 兵炮位标记
    marks = [(1, 2), (7, 2), (1, 7), (7, 7), (0, 3), (2, 3), (4, 3), (6, 3),
             (8, 3), (0, 6), (2, 6), (4, 6), (6, 6), (8, 6)]
    for file, rank in marks:
        cx, cy = x0 + file * cell, y0 + cell * (9 - rank)
        gap, length = cell * 0.1, cell * 0.17
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            if file == 0 and dx < 0:
                continue
            if file == 8 and dx > 0:
                continue
            px, py = cx + dx * gap, cy + dy * gap
            draw.line([(px + dx * length, py), (px, py), (px, py + dy * length)],
                      fill=line, width=max(1, int(cell * 0.026)))
    # 楚河汉界
    river_font = _load_font(max(9, int(cell * 0.36)))
    for text, file in (("楚 河", 2), ("汉 界", 6)):
        draw.text((x0 + file * cell, y0 + cell * 4.5), text, font=river_font, fill=river,
                  anchor="mm")
    # 坐标数字
    if show_numbers:
        coord_font = _load_font(max(8, int(cell * 0.22)))
        for file in range(9):
            draw.text((x0 + file * cell, y1 + cell * 0.44),
                      CHINESE_DIGITS[8 - file], font=coord_font, fill=river, anchor="mm")
            draw.text((x0 + file * cell, y0 - cell * 0.44), str(file + 1),
                      font=coord_font, fill=river, anchor="mm")
    # 棋子
    glyph_font = _load_font(max(10, int(cell * 0.56)))
    radius = cell * 0.42
    for index, row in enumerate(board_rows):
        rank = 9 - index
        for file, char in enumerate(_expand_row(row)):
            if char == "." or char not in GLYPHS_SIMPLE:
                continue
            cx, cy = x0 + file * cell, y0 + cell * (9 - rank)
            draw.ellipse([cx - radius, cy - radius + radius * 0.12, cx + radius,
                          cy + radius + radius * 0.12], fill=shadow)
            draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=face,
                         outline=edge, width=max(2, int(cell * 0.045)))
            draw.ellipse([cx - radius * 0.82, cy - radius * 0.82, cx + radius * 0.82,
                          cy + radius * 0.82], outline=edge, width=1)
            glyph = GLYPHS_SIMPLE[char]
            ink = _rgb(RED_INK if char.isupper() else BLACK_INK)
            draw.text((cx, cy), glyph, font=glyph_font, fill=ink, anchor="mm")
    if title:
        title_font = _load_font(max(12, int(cell * 0.3)))
        draw.text((width / 2, header / 2), title, font=title_font, fill=river, anchor="mm")
    return image


# ------------------------------------------------------------------ 图片识别
def _ink_mask(image, color: str):
    """把"棋子字迹"像素标出来（0/255 的 L 图）。

    * 红子：偏红（R 明显大于 G/B）——木纹/褐色棋盘线不会被误判；
    * 黑子：偏暗且不偏红（蓝分量不低于红分量）——褐色的棋盘线（R>B）会被排除。
    """
    if image.mode != "RGB":
        image = image.convert("RGB")
    data = image.getdata()
    out = bytearray(len(data))
    if color == READ_RED:
        for index, (red, green, blue) in enumerate(data):
            if red > 100 and red - green > 55 and red - blue > 55:
                out[index] = 255
    else:
        for index, (red, green, blue) in enumerate(data):
            luma = 0.299 * red + 0.587 * green + 0.114 * blue
            if luma >= 110:
                continue
            # 黑子墨色：偏蓝的深色；纯黑（R=G=B 且很暗）也算。
            # 褐色棋盘线（R 明显大于 B）与浅灰线会被排除，不会被误当成棋子。
            if blue - red >= 8 or (abs(red - green) < 12 and abs(green - blue) < 12
                                   and luma < 85):
                out[index] = 255
    return Image.frombytes("L", image.size, bytes(out))


def _normalized(mask, size: int = 32):
    """按笔迹包围盒裁剪并缩放成固定大小，做到与字号无关。"""
    bbox = mask.getbbox()
    if bbox is None:
        return None
    cropped = mask.crop(bbox)
    width, height = cropped.size
    scale = max(width, height)
    canvas = Image.new("L", (scale, scale), 0)
    canvas.paste(cropped, ((scale - width) // 2, (scale - height) // 2))
    return canvas.resize((size, size), Image.LANCZOS).point(lambda v: 255 if v > 90 else 0)


def _clip_circle(mask, ratio: float = 0.43):
    """只保留中心的圆形区域：棋子最外圈的投影/描边（例如夜色主题的阴影）
    很容易和"黑墨"混在一起，剪掉后比对字形更准。"""
    width, height = mask.size
    circle = Image.new("L", mask.size, 0)
    draw = ImageDraw.Draw(circle)
    radius = min(width, height) * ratio
    draw.ellipse([width / 2 - radius, height / 2 - radius,
                  width / 2 + radius, height / 2 + radius], fill=255)
    return ImageChops.multiply(mask, circle)


def _similarity(mask_a, mask_b) -> float:
    """两个二值图的交并比。"""
    if mask_a is None or mask_b is None:
        return 0.0
    a = mask_a.histogram()
    b = mask_b.histogram()
    both = Image.frombytes("L", mask_a.size,
                           bytes([255 if (x > 0 and y > 0) else 0
                                  for x, y in zip(mask_a.getdata(), mask_b.getdata())]))
    inter = both.histogram()[255]
    union = a[255] + b[255] - inter
    return inter / union if union else 0.0


def _templates(traditional: bool = False) -> Dict[str, Dict[str, object]]:
    """生成 14 个棋子字形的模板（按颜色分组，键＝棋子字母）。"""
    glyphs = {"K": "帅", "A": "仕", "B": "相", "N": "马", "R": "车", "C": "炮", "P": "兵",
              "k": "将", "a": "士", "b": "象", "n": "马", "r": "车", "c": "炮", "p": "卒"}
    if traditional:
        glyphs.update({"K": "帥", "N": "傌", "R": "俥", "k": "將", "n": "馬", "r": "車",
                       "c": "砲"})
    size = 96
    font = _load_font(int(size * 0.72))
    table: Dict[str, Dict[str, object]] = {"r": {}, "b": {}}
    for piece, glyph in glyphs.items():
        canvas = Image.new("RGB", (size, size), _rgb("#ffffff"))
        draw = ImageDraw.Draw(canvas)
        ink = _rgb(RED_INK if piece.isupper() else BLACK_INK)
        draw.text((size / 2, size / 2), glyph, font=font, fill=ink, anchor="mm")
        color = READ_RED if piece.isupper() else READ_BLACK
        table[color][piece] = _normalized(_ink_mask(canvas, color))
    return table


_TEMPLATE_CACHE: Dict[str, Dict[str, Dict[str, object]]] = {}


def templates(traditional: bool = False) -> Dict[str, Dict[str, object]]:
    key = "trad" if traditional else "simple"
    if key not in _TEMPLATE_CACHE:
        _TEMPLATE_CACHE[key] = _templates(traditional)
    return _TEMPLATE_CACHE[key]


def _long_run(values: List[bool]) -> int:
    """最长的连续 True 长度。"""
    best = current = 0
    for value in values:
        current = current + 1 if value else 0
        if current > best:
            best = current
    return best


def _line_peaks(profile: List[int], ratio: float = 0.5) -> List[Tuple[int, int]]:
    """从投影里挑出"很可能是棋盘线"的峰：权重高、且相邻像素合并成一个峰。"""
    if not profile:
        return []
    strongest = max(profile)
    if strongest <= 0:
        return []
    threshold = strongest * ratio
    peaks: List[Tuple[int, int]] = []
    index = 0
    length = len(profile)
    while index < length:
        if profile[index] >= threshold:
            end = index
            weight = 0
            moment = 0
            while end < length and profile[end] >= threshold and end < index + 4:
                weight += profile[end]
                moment += profile[end] * end
                end += 1
            peaks.append((int(round(moment / weight)) if weight else index, weight))
            index = max(end, index + 1)
        else:
            index += 1
    return peaks


def _fit_lattice(peaks: List[Tuple[int, int]], count: int,
                 limit: int) -> Optional[Tuple[float, float]]:
    """把检测到的线拟合成"count 条等距线"，返回 (起点, 间距)。"""
    if len(peaks) < 2:
        return None
    weights = {position: weight for position, weight in peaks}
    points = sorted(weights)
    low_gap = limit / (count * 2.0)              # 间距的先验范围
    high_gap = limit / (count * 0.55)
    candidates = set()
    for position, first in enumerate(points):
        for second in points[position + 1:]:
            distance = second - first
            for divisor in range(1, count):
                value = distance / divisor
                if low_gap <= value <= high_gap:
                    candidates.add(round(value, 3))
    tolerance = 3.0
    ranked: List[Tuple[float, float, List[Tuple[float, int]]]] = []
    for gap in candidates:
        for anchor in points:
            hits = [(point, round((point - anchor) / gap)) for point in points
                    if abs(((point - anchor) / gap) - round((point - anchor) / gap))
                    * gap <= tolerance]
            if len(hits) < 2:
                continue
            indexes = [index for _point, index in hits]
            span = max(indexes) - min(indexes) + 1
            # 好的拟合：命中的线又多又重，而且跨度接近 count 条线
            penalty = 1.0 + 3.0 * abs((span - 1) - (count - 1))
            score = sum(weights[point] for point, _index in hits) / penalty
            ranked.append((score, gap, hits))
    if not ranked:
        return None
    ranked.sort(key=lambda item: -item[0])
    result: List[Tuple[float, float, float]] = []
    seen_gaps: List[float] = []
    for score, gap, hits in ranked:
        if any(abs(gap - other) < max(2.0, gap * 0.04) for other in seen_gaps):
            continue                     # 同一个间距只保留最好的那一次
        seen_gaps.append(gap)
        mean_index = sum(index for _point, index in hits) / len(hits)
        mean_point = sum(point for point, _index in hits) / len(hits)
        denominator = sum((index - mean_index) ** 2 for _point, index in hits)
        if denominator > 0:
            slope = sum((index - mean_index) * (point - mean_point)
                        for point, index in hits) / denominator
            if gap * 0.5 <= slope <= gap * 1.5:
                gap = slope
        result.append((score, mean_point - gap * mean_index, gap))
        if len(result) >= 3:
            break
    return result


def _grid_positions(peaks: List[Tuple[int, int]], count: int,
                    limit: int) -> List[List[float]]:
    """给定投影峰，给出几组可能的棋盘线位置（等距，缺的向外补），按可信度排序。"""
    fitted = _fit_lattice(peaks, count, limit)
    if not fitted:
        return []
    options: List[List[float]] = []
    for _score, start, gap in fitted:
        indexes = [round((point - start) / gap) for point, _weight in peaks
                   if abs(((point - start) / gap) - round((point - start) / gap))
                   * gap <= 4]
        if len(indexes) < 2:
            continue
        low, high = min(indexes), max(indexes)
        lines = [start + index * gap for index in range(low, high + 1)]
        while len(lines) < count:
            before, after = lines[0] - gap, lines[-1] + gap
            if after < limit:
                lines.append(after)
            elif before >= 0:
                lines.insert(0, before)
            else:
                break
        if len(lines) > count:
            offset = max(0, (len(lines) - count) // 2)
            lines = lines[offset:offset + count]
        if len(lines) == count and lines[0] >= 0 and lines[-1] <= limit:
            options.append(lines)
    return options


def _periodic_lines(profile: List[int], count: int, limit: int) -> List[List[float]]:
    """用**自相关**求格距、再用相位搜索对齐，返回几组候选线位置。

    棋盘线一定是等距的：先对投影做自相关求周期（即使有几条线被棋子挡住、或者多出
    一些杂峰，周期依然能算准），再在图像内滑动找到最能解释投影的那个相位。
    """
    n = len(profile)
    if n < 40 or count < 2:
        return []
    peak = max(profile)
    if peak <= 0:
        return []
    floor = peak * 0.12
    data = [value - floor for value in profile]
    low = max(8, n // (count * 2))
    high = max(low + 1, n // max(3, count - 3))
    best_lag = 0
    best_score: Optional[float] = None
    for lag in range(low, high + 1):
        pairs = n - lag
        if pairs <= 0:
            continue
        total = 0.0
        for index in range(pairs):
            total += data[index] * data[index + lag]
        score = total / pairs
        if best_score is None or score > best_score:
            best_score, best_lag = score, lag
    if not best_lag:
        return []
    window = 2
    smoothed = [max(profile[max(0, index - window):index + window + 1])
                for index in range(n)]
    results: List[Tuple[float, float, float]] = []
    for delta in (-1.0, -0.5, 0.0, 0.5, 1.0):
        period = best_lag + delta
        if period <= 4:
            continue
        for phase_index in range(int(round(period))):
            phase = float(phase_index)
            total = 0.0
            fits = True
            for index in range(count):
                centre = int(round(phase + index * period))
                if centre < -2 or centre > n + 2:
                    fits = False
                    break
                total += smoothed[min(n - 1, max(0, centre))]
            if fits:
                results.append((total, phase, period))
    if not results:
        return []
    results.sort(key=lambda item: -item[0])
    options: List[List[float]] = []
    for _total, phase, period in results:
        lines = [phase + index * period for index in range(count)]
        if lines[0] < -2 or lines[-1] > n + 2:
            continue
        if any(abs(lines[0] - other[0]) < 2.5 and abs(lines[-1] - other[-1]) < 2.5
               for other in options):
            continue
        options.append(lines)
        if len(options) >= 3:
            break
    return options


def _grid_candidates(image) -> List[Tuple[float, float, float, float]]:
    """给出几组可能的棋盘网格 ``(x0, y0, cell_x, cell_y)``，按可信度排序。"""
    gray = image.convert("L")
    width, height = gray.size
    scale = 1.0
    if max(width, height) > 720:                  # 先缩小，检测快很多（坐标再换算回去）
        scale = 720 / max(width, height)
        gray = gray.resize((max(1, int(width * scale)), max(1, int(height * scale))),
                           Image.LANCZOS)
    w, h = gray.size
    # 先估计棋盘底色（亮度中位数），再把"明显不同于底色"的像素当成线条/字迹。
    # 这样深色主题（浅色线条）和浅色主题（深色线条）都能检测。
    histogram = gray.histogram()
    running = 0
    background = 0
    for value, count in enumerate(histogram):
        running += count
        if running >= w * h / 2:
            background = value
            break
    data = gray.tobytes()

    def is_line(x: int, y: int) -> bool:
        return abs(data[y * w + x] - background) > 60

    # 棋盘线是贯穿整幅图的横/竖线 → 投影（该行/列上有多少"非底色"像素）会明显突出。
    # 只取权重高的峰，再用"等距"这个強约束把被棋子挡住的外框也算出来。
    row_profile = [sum(1 for x in range(w) if is_line(x, y)) for y in range(h)]
    column_profile = [sum(1 for y in range(h) if is_line(x, y)) for x in range(w)]
    rows_options = _periodic_lines(row_profile, 10, h)
    columns_options = _periodic_lines(column_profile, 9, w)
    candidates: List[Tuple[float, float, float, float]] = []
    for horizontal in rows_options:
        for vertical in columns_options[:2]:
            x0, x1 = vertical[0], vertical[-1]
            y0, y1 = horizontal[0], horizontal[-1]
            cell_x = (x1 - x0) / 8.0
            cell_y = (y1 - y0) / 9.0
            if cell_x < 8 or cell_y < 8:
                continue
            if not 0.5 <= cell_x / cell_y <= 2.0:  # 棋盘格应该接近正方形
                continue
            box = (x0 / scale, y0 / scale, cell_x / scale, cell_y / scale)
            if box not in candidates:
                candidates.append(box)
    return candidates[:4]


def _recognize_with_grid(image, grid, table, traditional: bool = False) -> Dict[str, object]:
    """按给定的网格把 90 个交叉点读一遍（返回棋子行、可疑位置与置信度）。"""
    x0, y0, cell_x, cell_y = grid
    # 棋盘底色（取整张图的亮度中位数）：棋子牌面比它亮很多，可用来判断"这格有没有子"
    gray = image.convert("L").resize((64, 64))
    histogram = gray.histogram()
    total_pixels = sum(histogram) or 1
    running = 0
    background = 0
    for value, count in enumerate(histogram):
        running += count
        if running >= total_pixels / 2:
            background = value
            break
    rows: List[str] = []
    unknown: List[str] = []
    quality = 0.0
    for index in range(10):
        rank = 9 - index
        line: List[str] = []
        for file in range(9):
            cx = x0 + file * cell_x
            cy = y0 + (9 - rank) * cell_y
            radius = min(cell_x, cell_y) * 0.40
            box = (int(cx - radius), int(cy - radius), int(cx + radius), int(cy + radius))
            box = (max(0, box[0]), max(0, box[1]),
                   min(image.size[0], box[2]), min(image.size[1], box[3]))
            if box[2] - box[0] < 6 or box[3] - box[1] < 6:
                line.append(".")
                continue
            cell_image = image.crop(box)
            red = _ink_mask(cell_image, READ_RED)
            black = _ink_mask(cell_image, READ_BLACK)
            total = float(cell_image.size[0] * cell_image.size[1])
            red_ratio = red.histogram()[255] / total
            black_ratio = black.histogram()[255] / total
            gray_crop = cell_image.convert("L").histogram()
            crop_total = sum(gray_crop) or 1
            running = 0
            crop_median = 0
            for value, count in enumerate(gray_crop):
                running += count
                if running >= crop_total / 2:
                    crop_median = value
                    break
            bright_disc = crop_median - background > 35
            # 深色棋盘（例如"夜色"主题）整块底色都落在"黑墨"范围内，
            # 这时用四角的底色来判断，不能只看黑墨比例。
            corner = max(3, int(min(cell_image.size) * 0.2))
            corners = [cell_image.crop((0, 0, corner, corner)),
                       cell_image.crop((cell_image.size[0] - corner, 0,
                                        cell_image.size[0], corner)),
                       cell_image.crop((0, cell_image.size[1] - corner, corner,
                                        cell_image.size[1])),
                       cell_image.crop((cell_image.size[0] - corner,
                                        cell_image.size[1] - corner,
                                        cell_image.size[0], cell_image.size[1]))]
            corner_ink = sum(mask.histogram()[255] for mask in
                             (_ink_mask(patch, READ_BLACK) for patch in corners))
            corner_total = float(sum(patch.size[0] * patch.size[1] for patch in corners))
            dark_board = corner_total and corner_ink / corner_total > 0.5
            if not bright_disc and red_ratio < 0.012 and (
                    black_ratio < 0.012 or dark_board):
                line.append(".")
                continue
            color = READ_RED if red_ratio >= black_ratio else READ_BLACK
            mask = _normalized(_clip_circle(red if color == READ_RED else black))
            best, score = "", 0.0
            for piece, template in table[color].items():
                value = _similarity(mask, template)
                if value > score:
                    best, score = piece, value
            if not best or score < 0.34:
                unknown.append(FILES[file] + str(rank))
                quality -= 2.0
                line.append(".")
                continue
            # 认得很准（>=0.45）加分，模棱两可的少加一点，用来在候选网格里挑最好的
            quality += score if score >= 0.45 else score - 0.2
            line.append(best)
        rows.append("".join(line))
    board = "/".join(_pack_row([char for char in row]) for row in rows)
    message = f"识别完成：{len(unknown)} 个位置不确定" if unknown else "识别完成"
    return {"ok": True, "board": board, "boardRows": rows, "unknown": unknown,
            "message": message, "quality": round(quality, 2),
            "grid": (x0, y0, cell_x, cell_y)}


def recognize_position(image, *, traditional: bool = False) -> Dict[str, object]:
    """从图片里认出局面（自动在多个候选网格里挑认得最准的那个）。

    返回 ``{"ok": bool, "board": str, "boardRows": [...], "unknown": [...],
    "quality": float, "message": str}``；``board`` 只有棋子摆放部分，
    先行方由调用方决定（图片里看不出轮到谁走）。
    """
    if not PIL_OK:
        return {"ok": False, "message": f"图片识别需要 Pillow：{unavailable_reason()}"}
    if image.mode != "RGB":
        image = image.convert("RGB")
    candidates = _grid_candidates(image)
    if not candidates:
        return {"ok": False,
                "message": "没找到棋盘线条，请裁剪到只剩棋盘（或换一张更清晰的图片）"}
    table = templates(traditional)
    best: Optional[Dict[str, object]] = None
    for grid in candidates:
        result = _recognize_with_grid(image, grid, table, traditional)
        if best is None or float(result["quality"]) > float(best["quality"]):
            best = result
    assert best is not None
    return best


def _detect_grid(image) -> Optional[Tuple[float, float, float, float]]:
    """第一个候选网格（调试 / 测试用）。"""
    candidates = _grid_candidates(image)
    return candidates[0] if candidates else None
