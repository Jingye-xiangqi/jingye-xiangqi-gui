# -*- coding: utf-8 -*-
"""棋盘控件：用 Tkinter Canvas 画出棋盘、棋子、提示与变例箭头。

坐标约定与后端一致：``file`` 0-8 表示 a-i，``rank`` 0-9 表示红方底线到黑方底线。
界面上黑方在上、红方在下（可用"翻转棋盘"切换视角）。
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from ..board import FILES

GLYPHS_SIMPLE = {
    "K": "帅", "A": "仕", "B": "相", "N": "马", "R": "车", "C": "炮", "P": "兵",
    "k": "将", "a": "士", "b": "象", "n": "马", "r": "车", "c": "炮", "p": "卒",
}
GLYPHS_TRAD = {
    "K": "帥", "A": "仕", "B": "相", "N": "傌", "R": "俥", "C": "炮", "P": "兵",
    "k": "將", "a": "士", "b": "象", "n": "馬", "r": "車", "c": "砲", "p": "卒",
}

WOOD_LIGHT = "#f0dcb4"
WOOD_DARK = "#dcbb84"
WOOD_EDGE = "#b98b46"
LINE_COLOR = "#7a5629"
RIVER_COLOR = "#8a6432"
PIECE_FACE = "#fbf3df"
PIECE_EDGE = "#b08a52"
#: 棋盘后面那层"桌面"木纹（浅棕色，像真实棋盘放在木桌上）
BACKDROP_LIGHT = "#dcb383"
BACKDROP_MID = "#c9a06a"
BACKDROP_DARK = "#b3854f"
BACKDROP_HI = "#eed0a4"
RED_INK = "#b3271f"
BLACK_INK = "#25303a"
SELECT_COLOR = "#e0a72c"
LAST_COLOR = "#3f7fd0"
CHECK_COLOR = "#d43a2b"
DOT_COLOR = "#2f9c58"
CAPTURE_COLOR = "#d6463c"
ARROW_COLOR = "#2f7fd0"

#: 棋盘主题（颜色 + 中文名）。界面设置里可以切换，棋子始终是红/黑两色。
THEMES: Dict[str, Dict[str, str]] = {
    "wood": {"name": "木纹", "light": WOOD_LIGHT, "dark": WOOD_DARK,
             "edge": WOOD_EDGE, "line": LINE_COLOR, "river": RIVER_COLOR,
             "face": PIECE_FACE, "piece_edge": PIECE_EDGE, "shadow": "#a98a5f",
             "frame": BACKDROP_MID, "backdrop": "wood",
             "bd_light": BACKDROP_LIGHT, "bd_dark": BACKDROP_DARK, "bd_hi": BACKDROP_HI},
    "walnut": {"name": "胡桃木", "light": "#e6c79b", "dark": "#cba170", "edge": "#8b5c2a",
               "line": "#63401c", "river": "#71491f", "face": "#fdf6e6",
               "piece_edge": "#9c6b34", "shadow": "#8a6234", "frame": "#8b5c2a",
               "backdrop": "wood", "bd_light": "#b98a5c", "bd_dark": "#8a5f34",
               "bd_hi": "#d8ab7c"},
    "green": {"name": "绿呢桌布", "light": "#e6eeda", "dark": "#cfdcbd", "edge": "#7d9668",
              "line": "#4f6b45", "river": "#4f6b45", "face": "#f9f8ef",
              "piece_edge": "#8f9a76", "shadow": "#8d9b78", "frame": "#2c3a2a",
              "backdrop": "wood", "bd_light": "#cfdbc0", "bd_dark": "#a8bf93",
              "bd_hi": "#e6eede"},
    "night": {"name": "夜色", "light": "#4b515a", "dark": "#3e444c",
              "edge": "#262b31", "line": "#b9c1cd", "river": "#c8cfdb",
              "face": "#eceae2", "piece_edge": "#8d939c", "shadow": "#101317",
              "frame": "#2c323a", "backdrop": "wood", "bd_light": "#464e58",
              "bd_dark": "#2f353d", "bd_hi": "#5b6570"},
    "plain": {"name": "素纸", "light": "#ffffff", "dark": "#f1f1f1",
              "edge": "#cccccc", "line": "#5b5b5b", "river": "#7d7d7d",
              "face": "#ffffff", "piece_edge": "#b3b3b3", "shadow": "#bdbdbd",
              "frame": "#e4e4e4", "backdrop": "wood", "bd_light": "#f6f4ef",
              "bd_dark": "#dedad2", "bd_hi": "#ffffff"},
}


class BoardView(tk.Canvas):
    """棋盘画布。``on_click(square)`` 与 ``on_move(from, to)`` 由外部处理。"""

    def __init__(self, master, on_click: Optional[Callable[[str], None]] = None,
                 on_move: Optional[Callable[[str, str], None]] = None,
                 on_right_click: Optional[Callable[..., None]] = None, **kwargs):
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("background", BACKDROP_MID)      # 棋盘后面是浅棕色木纹
        super().__init__(master, **kwargs)
        self.on_click = on_click or (lambda square: None)
        self.on_move = on_move or (lambda frm, to: None)
        self.on_right_click = on_right_click or (lambda square, event: None)

        self.state: Dict[str, object] = {}
        self.flip = False
        self.traditional = False
        self.pv_moves: List[str] = []
        self.show_arrows = True
        self.show_numbers = True
        self.piece_shadow = True                       # 棋子阴影（右下投影）
        self.theme = "wood"
        self.palette: Dict[str, str] = THEMES["wood"]
        self.scale = 1.0
        #: 自定义皮肤（棋盘 + 棋子图片）；None 表示用程序自带的矢量画法
        self.skin_path = ""
        self.skin = None
        #: 桌面背景（backgrounds 文件夹里选的图）；None 表示用皮肤背景 / 自带木纹
        self.background_name = ""
        self.background_path = None
        self._skin_images: Dict[str, object] = {}      # 缓存：缩放后的 PhotoImage
        self._skin_image_size = 0
        self._wood_cache: Dict[object, object] = {}    # 木纹背景 / 棋盘面缓存
        self._shadow_cache: Dict[int, object] = {}     # 棋子阴影图（按尺寸缓存）
        self._shadow_broken = False                    # 没有 Pillow → 不做阴影
        self.edit_mode = False

        self.cell = 50.0
        self.origin = (30.0, 30.0)
        self.piece_font = ("KaiTi", 26, "bold")
        self.coord_font = ("KaiTi", 11)
        self.river_font = ("KaiTi", 20)

        self._drag_from: Optional[str] = None
        self._drag_delta = (0.0, 0.0)
        self._dragging = False
        self._press_xy = (0, 0)
        self._anim: Optional[Dict[str, object]] = None
        self._anim_job: Optional[str] = None

        self.bind("<Configure>", self._on_configure)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_motion)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Button-3>", self._on_right_click)

    # ------------------------------------------------------------ 对外接口
    def set_state(self, state: Dict[str, object], animate: bool = False) -> None:
        self.state = state or {}
        settings = self.state.get("settings") or {}
        self.flip = bool(settings.get("flip", self.flip))
        self.traditional = settings.get("notation") == "trad"
        # 界面显示设置（都在「界面设置」对话框里改）
        self.show_arrows = bool(settings.get("show_arrows", True))
        self.show_numbers = bool(settings.get("show_numbers", True))
        self.piece_shadow = bool(settings.get("piece_shadow", True))
        self.set_theme(str(settings.get("board_theme") or "wood"))
        self.set_scale(settings.get("board_scale", 100))
        self.set_background(settings.get("background") or "")
        self.set_skin(settings.get("board_skin") or "")
        self.redraw(animate=animate)

    def set_pv(self, moves: List[str]) -> None:
        self.pv_moves = list(moves or [])
        self.redraw()

    def set_flip(self, flip: bool) -> None:
        self.flip = bool(flip)
        self.redraw()

    def set_edit_mode(self, enabled: bool) -> None:
        self.edit_mode = bool(enabled)

    def set_theme(self, name: str) -> None:
        """切换棋盘主题（颜色方案）。"""
        key = name if name in THEMES else "wood"
        if key != self.theme or self.palette is not THEMES[key]:
            self.theme = key
            self.palette = THEMES[key]
            self._wood_cache.clear()           # 换主题要重新生成木纹
            try:
                self.configure(background=self.palette["frame"])
            except tk.TclError:
                pass

    def set_show_numbers(self, enabled: bool) -> None:
        """线路数字（棋盘四周的 1-9 / 一-九）显示开关。"""
        self.show_numbers = bool(enabled)

    def set_show_arrows(self, enabled: bool) -> None:
        """棋步提示箭头开关。"""
        self.show_arrows = bool(enabled)

    def set_piece_shadow(self, enabled: bool) -> None:
        """棋子阴影开关（所有棋子/所有皮肤统一用同一种阴影，见 :meth:`_shadow_photo`）。"""
        self.piece_shadow = bool(enabled)

    def set_scale(self, value) -> None:
        """棋盘大小（百分比，60-100：小于 100 时棋盘变小，100＝撑满可用区域）。"""
        try:
            percent = float(value)
        except (TypeError, ValueError):
            percent = 100.0
        self.scale = max(0.6, min(1.0, percent / 100.0))

    def set_background(self, name) -> None:
        """设置桌面背景（``backgrounds`` 文件夹里的名字；空字符串＝用皮肤背景/自带木纹）。"""
        text = str(name or "").strip()
        if text == self.background_name and getattr(self, "_background_ready", False):
            return
        from . import backgrounds as backgrounds_module

        self.background_name = text
        self.background_path = backgrounds_module.find_background(text) if text else None
        self._background_ready = True
        for key in [item for item in self._wood_cache if str(item).startswith("bg-")]:
            self._wood_cache.pop(key, None)
        if text and self.background_path is None:
            self._log_once(f"bg-missing-{text}", f"背景：找不到「{text}」，改用自带木纹")
        elif self.background_path is not None:
            self._log_once(f"bg-{text}", f"背景：使用「{text}」（{self.background_path}）")

    def set_skin(self, folder) -> bool:
        """设置**棋子**皮肤文件夹（空字符串＝回到自带画法）。返回是否成功用上。

        只换棋子：棋盘底始终是程序自己画的木纹（所以皮肤棋盘图的边距不影响），
        棋子图统一缩放到 0.92 格贴到交叉点中心。
        """
        path = str(folder or "").strip()
        if path == self.skin_path and self.skin is not None:
            return True
        from . import skin as skin_module

        self._skin_images = {}
        self._skin_image_size = 0
        self._skin_colors = None            # 格线颜色（换皮肤要重新算）
        self._skin_surface_luma = None      # 棋盘面平均亮度（决定格线用深色还是浅色）
        self.skin_path = path
        if not path:
            self.skin = None
            return False
        # 设置里可以写全路径，也可以只写皮肤名（默认值就是名字，见 skin.resolve_skin_path）
        folder = skin_module.resolve_skin_path(path)
        if folder is None:
            self.skin = None
            self._log_once(f"skin-missing-{path}", f"皮肤：找不到「{path}」，改用自带画法")
            return False
        candidate = skin_module.Skin(Path(folder))
        if candidate.ok:
            candidate.resolve_grid()          # 没有 skin.json 时自动找棋盘网格
            self.skin = candidate
            if not getattr(candidate, "board_usable", True):
                self._log_once(f"skin-board-bad-{path}",
                               f"棋盘：{candidate.name} 不用 board.png 当棋盘底"
                               f"（{candidate.grid_note or '图里没有可信的 8×9 网格'}），"
                               f"改用程序自画棋盘（格线绝对准）+ 该皮肤材质配色，"
                               f"棋子仍然用本皮肤。")
            self._log_once(f"skin-{path}",
                           f"皮肤：{candidate.name}｜棋子 {len(candidate.files)} 张"
                           f"｜棋盘图 {'有' if candidate.path_of('board') else '无'}"
                           f"｜对齐参数 {candidate.grid_source or '无（用自带木纹棋盘）'}")
        else:
            self.skin = None
        return self.skin is not None

    def _skin_photo(self, key: str, size: int):
        """把皮肤里的某张图缩放到 size×size，并缓存成 Tk 可用的图片对象。

        ★ 棋子图会先按"内容框"裁成**以棋子圆心为中心**的正方形再缩放：
          这样不管原图四周的透明边留得多不匀，棋子圆心都正好落在交叉点上。
        """
        if self.skin is None or size <= 0:
            return None
        cache_key = f"{key}@{size}"
        cached = self._skin_images.get(cache_key)
        if cached is not None:
            return cached
        path = self.skin.path_of(key)
        if path is None:
            return None
        from . import skin as skin_module

        crop = self.skin.piece_crop(key) if skin_module.is_piece_key(key) else None
        photo = None
        try:                                        # 有 Pillow 时缩放到精确尺寸
            from PIL import Image, ImageTk

            with Image.open(path) as image:
                image = image.convert("RGBA")
                if crop is not None:                # 裁成"圆心在正中"的正方形
                    center_x, center_y, side = crop
                    half = side / 2.0
                    box = (int(round(center_x - half)), int(round(center_y - half)),
                           int(round(center_x + half)), int(round(center_y + half)))
                    image = image.crop(box)         # 越界部分自动补成透明
                image = image.resize((size, size), Image.LANCZOS)
                photo = ImageTk.PhotoImage(image, master=self)
        except Exception:                           # noqa: BLE001
            try:                                    # 退路：Tk 自带 PhotoImage
                photo = tk.PhotoImage(file=str(path), master=self)
                factor = max(1, round(photo.width() / size))
                if factor > 1:
                    photo = photo.subsample(factor, factor)
            except Exception:  # noqa: BLE001
                photo = None
        if photo is not None:
            self._skin_images[cache_key] = photo
        return photo

    # ------------------------------------------------------------ 几何
    def _on_configure(self, _event=None) -> None:
        self.redraw()

    def _layout(self) -> None:
        raw_width, raw_height = self.winfo_width(), self.winfo_height()
        if raw_width <= 1 or raw_height <= 1:
            return                      # 控件还没摆好（临时未映射），别把布局算坏
        width, height = max(raw_width, 200), max(raw_height, 200)
        # ★ 皮肤自带背景图 + 对齐参数时：棋盘按皮肤设计的位置摆放（不再自己居中）
        if self._apply_skin_layout(width, height):
            self.virtual_size = (width, height)
            self.piece_font = ("KaiTi", max(10, int(self.cell * 0.54)), "bold")
            self.coord_font = ("KaiTi", max(7, int(self.cell * 0.2)))
            self.river_font = ("KaiTi", max(9, int(self.cell * 0.36)))
            return
        # 9.24 = 8 格 + 两侧 0.62 格的外框；10.24 = 9 格 + 两侧 0.62 格
        cell = min((width - 16) / 9.24, (height - 16) / 10.24)
        cell = max(cell * self.scale, 16.0)
        self.cell = cell
        board_w = cell * 8
        board_h = cell * 9
        self.virtual_size = (width, height)
        self.origin = ((width - board_w) / 2, (height - board_h) / 2)
        self.piece_font = ("KaiTi", max(10, int(cell * 0.54)), "bold")
        self.coord_font = ("KaiTi", max(7, int(cell * 0.2)))
        self.river_font = ("KaiTi", max(9, int(cell * 0.36)))

    def _apply_skin_layout(self, width: int, height: int) -> bool:
        """按皮肤的 background.png + skin.json 摆放棋盘（网格位置由皮肤决定）。

        皮肤的背景图会铺满整个棋盘区域（保持比例、超出部分裁掉），
        skin.json 里的 ``margin_left / margin_top / grid_w / grid_h``（都是 0~1 的比例）
        告诉我们"棋盘网格"画在这张图的什么地方 —— 我们就把自己的格线画在那儿。
        这样背景风格随皮肤切换，而棋子和格线永远是对齐的。
        """
        skin = self.skin
        if skin is None or getattr(skin, "grid_ratio", None) is None:
            return False
        board_path = skin.path_of("board")
        if board_path is not None and getattr(skin, "grid_rect", None) is not None:
            # ★ 有棋盘图：把"棋盘图的内容（含外框）"按比例放进画布中间（四周留出桌面），
            #   再让图里的网格正好落在我们的格线上。
            from . import skin as skin_module

            size = skin_module.image_size(board_path)
            content = skin_module.alpha_bbox(board_path)
            if size is None or content is None:
                return False
            gx0, gy0, gx1, gy1 = skin.grid_rect
            grid_w_px = max(1.0, float(gx1 - gx0))
            grid_h_px = max(1.0, float(gy1 - gy0))
            # ★ 棋盘要尽量大：网格 + 一点点画框占满画布，四周只留约 3% 给桌面背景。
            keep = 0.075
            cell = min(width / (8.0 * (1 + keep * 2)),
                       height / (9.0 * (1 + keep * 2)))
            cell *= self.scale
            if cell < 16:
                return False
            self.cell = cell
            # 画面居中：网格中心放在画布中心
            grid_center_x = width / 2
            grid_center_y = height / 2
            self.origin = (grid_center_x - self.cell * 4, grid_center_y - self.cell * 4.5)
            # 只画"网格 + 一点画框"，画框外面的装饰不画（这样棋盘又大又干净）
            size = skin_module.image_size(board_path) or (int(gx1), int(gy1))
            self._skin_art_crop = (max(0, int(gx0 - grid_w_px * keep)),
                                   max(0, int(gy0 - grid_h_px * keep)),
                                   min(size[0], int(gx1 + grid_w_px * keep)),
                                   min(size[1], int(gy1 + grid_h_px * keep)))
            # 横竖用各自的缩放：保证画里的网格和我们的格线**逐像素**重合
            scale_x = (self.cell * 8) / grid_w_px
            scale_y = (self.cell * 9) / grid_h_px
            self._skin_art = (self.origin[0] - (gx0 - self._skin_art_crop[0]) * scale_x,
                              self.origin[1] - (gy0 - self._skin_art_crop[1]) * scale_y,
                              scale_x, scale_y)
            return True
        if board_path is not None:
            return False                     # 有棋盘图但没对齐参数 → 走自带木纹棋盘
        # 背景图铺满整个棋盘区域（拉伸到画布，不裁切、不留黑边），
        # skin.json 里的比例就直接对应画布上的位置。
        left, top, width_ratio, height_ratio = skin.grid_ratio
        grid_w = width_ratio * width
        grid_h = height_ratio * height
        if grid_w < 40 or grid_h < 40:
            return False
        # 用较小的一边当格宽，保证棋盘完整落在设计区里（棋子是圆的，格子必须方）
        cell = min(grid_w / 8.0, grid_h / 9.0)
        self.cell = max(16.0, cell)
        center_x = left * width + grid_w / 2
        center_y = top * height + grid_h / 2
        origin_x = center_x - self.cell * 4
        origin_y = center_y - self.cell * 4.5
        # 留出 0.62 格边距（放坐标、避免边上的棋子被裁掉）；皮肤参数把棋盘顶到
        # 边缘时，往中间挪一点，尺寸不变。
        margin = self.cell * 0.62
        if origin_x < margin:
            origin_x = margin
        if origin_y < margin:
            origin_y = margin
        origin_x = min(origin_x, width - margin - self.cell * 8)
        origin_y = min(origin_y, height - margin - self.cell * 9)
        self.origin = (max(margin * 0.4, origin_x), max(margin * 0.4, origin_y))
        return True

    def _skin_art_rect(self, width: int, height: int):
        """背景图在画布上的绘制区（铺满整个画布）。"""
        if self.skin is None:
            return None
        path = self.skin.background_path
        if path is None:
            return None
        board_w, board_h = getattr(self.skin, "board_ratio", (1.0, 1.0))
        art_w, art_h = width * board_w, height * board_h
        return ((width - art_w) / 2, (height - art_h) / 2, art_w, art_h)

    def _image_size(self, path):
        """图片像素尺寸（带缓存）。"""
        key = f"size@{path}"
        cached = self._skin_images.get(key)
        if cached is not None:
            return cached
        try:
            from PIL import Image

            with Image.open(path) as image:
                size = image.size
        except Exception:  # noqa: BLE001
            return None
        self._skin_images[key] = size
        return size

    def event_xy(self, event) -> Tuple[float, float]:
        """鼠标事件坐标（棋盘控件不再滚动，控件坐标＝画布坐标）。"""
        return float(event.x), float(event.y)

    def _view_rank(self, rank: int) -> int:
        return 9 - rank if self.flip else rank

    def _view_file(self, file: int) -> int:
        return 8 - file if self.flip else file

    def _xy(self, file: int, rank: int) -> Tuple[float, float]:
        vf = self._view_file(file)
        vr = self._view_rank(rank)
        return (self.origin[0] + vf * self.cell, self.origin[1] + (9 - vr) * self.cell)

    def square_at(self, x: float, y: float) -> Optional[str]:
        ox, oy = self.origin
        if self.cell <= 0:
            return None
        vf = round((x - ox) / self.cell)
        vr = 9 - round((y - oy) / self.cell)
        if not (0 <= vf <= 8 and 0 <= vr <= 9):
            return None
        cx, cy = self.origin[0] + vf * self.cell, self.origin[1] + (9 - vr) * self.cell
        if abs(cx - x) > self.cell * 0.62 or abs(cy - y) > self.cell * 0.62:
            return None
        file = 8 - vf if self.flip else vf
        rank = 9 - vr if self.flip else vr
        return FILES[file] + str(rank)

    # ------------------------------------------------------------ 鼠标交互
    def _on_press(self, event) -> None:
        x, y = self.event_xy(event)
        square = self.square_at(x, y)
        self._press_xy = (event.x, event.y)
        self._dragging = False
        if self.edit_mode:
            self._drag_from = None
            if square is not None:
                self.on_click(square)
            return
        if square is None:
            self._drag_from = None
            return
        piece = self.piece_at(square)
        side = self.state.get("side")
        if piece != "." and (piece.isupper() == (side == "w")):
            self._drag_from = square
            self.on_click(square)
        else:
            self._drag_from = None
            self.on_click(square)

    def _on_right_click(self, event) -> None:
        """右键：棋盘编辑时用来删棋子，平时弹出快捷菜单。"""
        square = self.square_at(*self.event_xy(event))
        self.on_right_click(square, event)

    def _on_motion(self, event) -> None:
        if self._drag_from is None:
            return
        if not self._dragging:
            dx = abs(event.x - self._press_xy[0])
            dy = abs(event.y - self._press_xy[1])
            if dx < 6 and dy < 6:
                return
            self._dragging = True
            self._drag_delta = (0.0, 0.0)
        step_x = event.x - self._press_xy[0] - self._drag_delta[0]
        step_y = event.y - self._press_xy[1] - self._drag_delta[1]
        if step_x or step_y:
            self.move("piece:" + self._drag_from, step_x, step_y)
            self._drag_delta = (self._drag_delta[0] + step_x, self._drag_delta[1] + step_y)

    def _on_release(self, event) -> None:
        source = self._drag_from
        dragging = self._dragging
        self._drag_from = None
        self._dragging = False
        self._drag_delta = (0.0, 0.0)
        if source is None:
            return
        if not dragging:
            return
        x, y = self.event_xy(event)
        target = self.square_at(x, y)
        if target and target != source:
            self.on_move(source, target)
        else:
            self.redraw()

    # ------------------------------------------------------------ 数据辅助
    def piece_at(self, square: str) -> str:
        board = self.state.get("board") or []
        file = FILES.index(square[0])
        rank = int(square[1])
        row = board[9 - rank] if 0 <= 9 - rank < len(board) else ""
        index = 0
        for ch in row:
            if ch.isdigit():
                index += int(ch)
            else:
                if index == file:
                    return ch
                index += 1
        return "."

    def _piece_center(self, square: str) -> Tuple[float, float]:
        return self._xy(FILES.index(square[0]), int(square[1]))

    def _glyph(self, piece: str) -> str:
        table = GLYPHS_TRAD if self.traditional else GLYPHS_SIMPLE
        return table.get(piece, piece)

    # ------------------------------------------------------------ 绘制
    def redraw(self, animate: bool = False) -> None:
        if not self.winfo_exists():
            return
        self._layout()
        self.delete("all")
        self._draw_backdrop()
        self._draw_board()
        self._draw_pieces_and_marks(animate=animate)
        self._draw_arrows()

    # ------------------------------------------------------------ 背景（木纹桌面）
    def _wood_photo(self, width: int, height: int, variant: str):
        """生成（并缓存）一张木纹图；没有 Pillow 时返回 None。

        ``variant``：``backdrop``＝棋盘后面那层浅棕木纹桌面，``board``＝棋盘面本身。
        尺寸按 64 像素取整缓存，窗口缩放时最多重新生成几次。
        """
        if width < 32 or height < 32:
            return None
        key = (variant, int(width // 64), int(height // 64), self.theme)
        cached = self._wood_cache.get(key)
        if cached is not None:
            return cached
        try:
            import random

            from PIL import Image, ImageDraw, ImageFilter, ImageTk
        except Exception:  # noqa: BLE001
            self._log_once("wood-fail",
                           "棋盘：没有可用的 Pillow / ImageTk，改用矢量木纹背景")
            return None
        rng = random.Random(20260925)
        # 先在小图上画纹理再放大：又快又自然
        small_w = max(96, width // 3)
        small_h = max(96, height // 3)
        if variant == "board":                      # 棋盘面：用主题的浅色
            base = self.palette["light"]
            dark = self.palette["dark"]
            edge = self.palette["edge"]
            hi = self.palette.get("bd_hi", BACKDROP_HI)
        else:                                       # 桌面：主题对应的木纹底色
            base = self.palette.get("bd_light", BACKDROP_LIGHT)
            dark = self.palette.get("bd_dark", BACKDROP_DARK)
            edge = dark
            hi = self.palette.get("bd_hi", BACKDROP_HI)
        image = Image.new("RGB", (small_w, small_h), base)
        draw = ImageDraw.Draw(image, "RGBA")
        # 大块的深浅木色带
        for _ in range(28):
            y = rng.uniform(-small_h * 0.05, small_h)
            band = rng.uniform(small_h * 0.02, small_h * 0.12)
            shade = rng.choice((dark, edge, hi))
            draw.rectangle([0, y, small_w, y + band],
                           fill=self._rgba(shade, rng.randint(16, 40)))
        # 细木纹
        for _ in range(150):
            y = rng.uniform(0, small_h)
            wobble = rng.uniform(-small_h * 0.01, small_h * 0.01)
            draw.line([(0, y), (small_w * 0.5, y + wobble), (small_w, y)],
                      fill=self._rgba(edge, rng.randint(18, 55)), width=1)
        # 少量木节
        for _ in range(2):
            cx, cy = rng.uniform(0, small_w), rng.uniform(0, small_h)
            for ring in range(3, 0, -1):
                radius = small_h * 0.02 * ring
                draw.ellipse([cx - radius, cy - radius * 0.5,
                              cx + radius, cy + radius * 0.5],
                             outline=self._rgba(edge, 40), width=1)
        image = image.filter(ImageFilter.GaussianBlur(0.7))
        image = image.resize((width, height), Image.BILINEAR)
        try:
            photo = ImageTk.PhotoImage(image)
        except Exception as exc:  # noqa: BLE001
            self._log_once("wood-photo-fail", f"棋盘：木纹图转 PhotoImage 失败：{exc}")
            return None
        self._wood_cache[key] = photo
        self._log_once(f"wood-ok-{variant}",
                       f"棋盘：木纹背景已生成（Pillow，{variant}，{width}x{height}，"
                       f"主题={self.theme}）")
        return photo

    def _log_once(self, key: str, message: str) -> None:
        """把棋盘相关的关键信息写进 link_debug.log（同一件事只写一次）。"""
        done = getattr(self, "_logged", None)
        if done is None:
            done = self._logged = set()
        if str(key) in done:
            return
        done.add(str(key))
        try:
            from ..link import debug_log

            debug_log(message)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _rgba(color: str, alpha: int):
        """``#rrggbb`` + alpha → 供 PIL 画半透明线条用。"""
        color = color.lstrip("#")
        if len(color) != 6:
            color = "c9a06a"
        return (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16), alpha)

    def _draw_backdrop(self) -> None:
        """棋盘后面那层背景：所有主题都画木纹（按主题配色），绝不会是黑底。"""
        width, height = int(self.winfo_width()), int(self.winfo_height())
        if width < 16 or height < 16:
            return
        # ★ 用户在下拉框里选了背景图（backgrounds 文件夹）→ 用它铺满棋盘区域
        if self.background_path is not None:
            photo = self._background_photo(width, height)
            if photo is not None:
                self.create_image(0, 0, image=photo, anchor="nw", tags="backdrop")
                return
        # ★ 选了皮肤：用皮肤自带的 background.png 当桌面背景（铺满整个棋盘区域）
        if self.skin is not None and self.skin.background_path is not None:
            photo = self._skin_backdrop_photo(width, height)
            if photo is not None:
                self.create_image(0, 0, image=photo, anchor="nw", tags="backdrop")
                return
        photo = self._wood_photo(width, height, "backdrop")
        if photo is not None:
            self.create_image(0, 0, image=photo, anchor="nw", tags="backdrop")
            return
        self._draw_wood_fallback(width, height)          # 没有 Pillow 时的矢量木纹

    def _skin_backdrop_photo(self, width: int, height: int):
        """桌面背景：皮肤背景图铺满棋盘区域，并**调暗**（让棋盘面更突出）。"""
        if self.skin is None:
            return None
        path = self.skin.background_path
        if path is None:
            return None
        return self._backdrop_photo_from(path, width, height, 0.86)

    def _background_photo(self, width: int, height: int):
        """用户选的背景图：按"铺满、裁掉多余"的方式缩放，稍微压暗一点让棋盘更突出。"""
        path = self.background_path
        if path is None or width < 32 or height < 32:
            return None
        return self._backdrop_photo_from(path, width, height, 0.94)

    def _backdrop_photo_from(self, path, width: int, height: int, brightness: float):
        """把一张背景图铺满棋盘区域（保持比例、裁掉多余），并整体调亮/调暗。"""
        if path is None or width < 32 or height < 32:
            return None
        key = ("bg-", int(width // 64), int(height // 64), str(path), round(brightness, 2))
        cached = self._wood_cache.get(key)
        if cached is not None:
            return cached
        try:
            from PIL import Image, ImageEnhance, ImageOps, ImageTk

            with Image.open(path) as raw:
                image = raw.convert("RGB")
                fitted = ImageOps.fit(image, (width, height), Image.LANCZOS,
                                      centering=(0.5, 0.5))
                if abs(brightness - 1.0) > 1e-6:
                    fitted = ImageEnhance.Brightness(fitted).enhance(brightness)
                photo = ImageTk.PhotoImage(fitted, master=self)
        except Exception as exc:  # noqa: BLE001
            self._log_once(f"bg-fail-{path}", f"背景：加载「{path.name}」失败：{exc}")
            return None
        self._wood_cache[key] = photo
        return photo

    def _skin_surface_photo(self, width: int, height: int):
        """棋盘面：没有 board.png 时，用背景图里对应的一块做"更亮的棋盘面"。

        * 有 ``board.png`` 的皮肤走另一条路（直接贴棋盘图）；
        * 这里是给"只有 background.png"的皮肤用的：把背景裁一块、提亮、稍微加饱和，
          于是"棋盘面比周围的桌面亮" —— 两者颜色/明暗明显不同，一眼能分出棋盘和桌面。
        """
        if self.skin is None:
            return None
        board_path = self.skin.path_of("board")
        source = board_path if board_path is not None else self.skin.background_path
        if source is None:
            return None
        if width < 32 or height < 32:
            return None
        key = f"skinsurface@{width}x{height}@{source}"
        cached = self._skin_images.get(key)
        if cached is not None:
            return cached
        try:
            from PIL import Image, ImageEnhance, ImageTk

            with Image.open(source) as raw:
                opened = raw.convert("RGBA")
                alpha = opened.split()[3]
                if alpha.getextrema()[0] < 250:
                    mean = opened.resize((1, 1), Image.BOX).getpixel((0, 0))[:3]
                    image = Image.new("RGB", opened.size, mean)
                    image.paste(opened.convert("RGB"), mask=alpha)
                else:
                    image = opened.convert("RGB")
                image_w, image_h = image.size
                if board_path is not None:
                    small = image.resize(
                        (max(6, width // 22), max(6, height // 22)), Image.LANCZOS)
                    patch = small.resize((width, height), Image.BICUBIC)
                    patch = ImageEnhance.Brightness(patch).enhance(1.06)
                    grey = patch.convert("L")
                    values = list(grey.resize((16, 16), Image.BILINEAR).getdata())
                    self._skin_surface_luma = sum(values) / max(1, len(values))
                    photo = ImageTk.PhotoImage(patch, master=self)
                    self._skin_images[key] = photo
                    return photo
                ratio = getattr(self.skin, "grid_ratio", None) or (0.0, 0.0, 1.0, 1.0)
                left = max(0.0, ratio[0] - 0.075)
                top = max(0.0, ratio[1] - 0.075)
                right = min(1.0, ratio[0] + ratio[2] + 0.075)
                bottom = min(1.0, ratio[1] + ratio[3] + 0.075)
                box = (int(left * image_w), int(top * image_h),
                       max(int(right * image_w), int(left * image_w) + 8),
                       max(int(bottom * image_h), int(top * image_h) + 8))
                patch = image.crop(box).resize((width, height), Image.LANCZOS)
                patch = ImageEnhance.Brightness(patch).enhance(1.30)
                patch = ImageEnhance.Color(patch).enhance(1.10)
                grey = patch.convert("L")
                values = list(grey.resize((16, 16), Image.BILINEAR).getdata())
                self._skin_surface_luma = sum(values) / max(1, len(values))
                photo = ImageTk.PhotoImage(patch, master=self)
        except Exception as exc:  # noqa: BLE001
            self._log_once(f"skin-surface-fail-{self.skin.background_path}",
                           f"棋盘：皮肤棋盘面生成失败：{exc}")
            return None
        self._skin_images[key] = photo
        return photo

    def _skin_line_colors(self):
        """皮肤棋盘面上的格线/文字颜色：按棋盘面亮度自动挑深色或浅色。"""
        cached = getattr(self, "_skin_colors", None)
        if cached is not None:
            return cached
        dark = self.palette.get("line", "#5b3a1a")
        light = "#f2e3c2"
        luma = getattr(self, "_skin_surface_luma", None)
        if (luma is None and self.skin is not None
                and (self.skin.path_of("board") is None
                     or not getattr(self.skin, "board_usable", True))):
            self._skin_surface_photo(400, 400)        # 先算一次棋盘面亮度
            luma = getattr(self, "_skin_surface_luma", None)
        if luma is not None and luma < 120:
            colors = (light, light, light)            # 深色棋盘面 → 浅色格线
        else:
            colors = (dark, dark, dark)
        self._skin_colors = colors
        return colors

    def _draw_wood_fallback(self, width: int, height: int) -> None:
        """没有 Pillow 时用画布图元画一层木纹（效果差些，但不会是黑底）。"""
        light = self.palette.get("bd_light", BACKDROP_LIGHT)
        mid = self.palette.get("bd_dark", BACKDROP_MID)
        hi = self.palette.get("bd_hi", BACKDROP_HI)
        self.create_rectangle(0, 0, width, height, fill=light, outline="",
                              tags="backdrop")
        step = max(6, int(height / 40))
        index = 0
        for y in range(0, height + step, step):
            shade = (mid, mid, hi)[index % 3]
            self.create_line(0, y, width, y + (2 if index % 2 else -2), fill=shade,
                             width=max(1, int(height / 320)), stipple="gray25")
            index += 1
        self._log_once("wood-fallback",
                       f"棋盘：使用矢量木纹背景（{width}x{height}，主题={self.theme}）")

    def _draw_board(self) -> None:
        cell = self.cell
        ox, oy = self.origin
        palette = self.palette
        x0, y0 = ox, oy
        x1, y1 = ox + cell * 8, oy + cell * 9
        # ★ 皮肤带棋盘图、又知道这张图里棋盘网格的精确位置（skin.json / 自动检测）
        #   时，就用皮肤自己的棋盘图，并把它的网格对齐到我们的格线；否则用自带木纹棋盘。
        if self.skin is not None and self._draw_skin_board(cell):
            # 皮肤棋盘图自己就画好了格线、兵炮位标记和楚河汉界，不用再叠一层
            return
        # 底色与外框（外框留出 0.62 格，用来放坐标和避免棋子压线）
        panel_x0, panel_y0 = x0 - cell * 0.62, y0 - cell * 0.62
        panel_x1, panel_y1 = x1 + cell * 0.62, y1 + cell * 0.62
        # ★ 皮肤自带背景图时：棋盘面就是那张背景（不再铺自己的木纹面板），
        #   只把格线/河界写在上面，格线颜色按背景深浅自动选。
        # ★ 用不用"皮肤材质当棋盘面"：只要这套皮肤带图（background.png 或 board.png），
        #   而且棋盘图没被采用（网格对不准 / 图里没有网格）→ 就用它的材质，
        #   格线由程序自己画（绝对准）。
        skin_surface = (self.skin is not None
                        and (self.skin.background_path is not None
                             or self.skin.path_of("board") is not None))
        if skin_surface:
            # ★ 棋盘面：比周围桌面亮的一块（皮肤 background 的同色提亮版），
            #   有 board.png 的皮肤则在上面的 _draw_skin_board 里已经贴过图了。
            surface = self._skin_surface_photo(int(round(panel_x1 - panel_x0)),
                                              int(round(panel_y1 - panel_y0)))
            if surface is not None:
                self.create_image((panel_x0 + panel_x1) / 2, (panel_y0 + panel_y1) / 2,
                                  image=surface, tags="boardsurface")
                self.create_rectangle(panel_x0, panel_y0, panel_x1, panel_y1, fill="",
                                      outline="#6b5433", width=max(2, int(cell * 0.05)))
            line_color, river_color, coord_color = self._skin_line_colors()
        else:
            line_color = river_color = coord_color = palette["line"]
        if not skin_surface:
            panel_w = int(round(panel_x1 - panel_x0))
            panel_h = int(round(panel_y1 - panel_y0))
            panel_photo = self._wood_photo(panel_w, panel_h, "board")
            if panel_photo is not None:
                # 棋盘面也用木纹（和参考图一样：整块实木棋盘）
                self.create_image((panel_x0 + panel_x1) / 2, (panel_y0 + panel_y1) / 2,
                                  image=panel_photo, tags="boardpanel")
                self.create_rectangle(panel_x0, panel_y0, panel_x1, panel_y1, fill="",
                                      outline=palette["edge"],
                                      width=max(2, int(cell * 0.05)))
            else:
                self.create_rectangle(panel_x0, panel_y0, panel_x1, panel_y1,
                                      fill=palette["light"], outline=palette["edge"],
                                      width=max(2, int(cell * 0.05)))
                for index in range(14):
                    yy = y0 + (index + 0.5) * (cell * 9 / 14)
                    self.create_line(x0 - cell * 0.6, yy, x1 + cell * 0.6, yy,
                                     fill=palette["dark"], width=1, stipple="gray50")
        # 横线（10 条）
        for rank in range(10):
            _, y = self._xy(0, rank)
            self.create_line(x0, y, x1, y, fill=line_color,
                             width=max(1, int(cell * 0.028)))
        # 竖线：最外两条到底；中间七条**在楚河汉界处断开**（河界里不许有竖线穿过）
        river_top = min(self._xy(0, 4)[1], self._xy(0, 5)[1])
        river_bottom = max(self._xy(0, 4)[1], self._xy(0, 5)[1])
        for file in range(9):
            vf = self._view_file(file)
            x = ox + vf * cell
            if file in (0, 8):
                self.create_line(x, y0, x, y1, fill=line_color,
                                 width=max(1, int(cell * 0.028)))
            else:
                self.create_line(x, y0, x, river_top, fill=line_color,
                                 width=max(1, int(cell * 0.028)))
                self.create_line(x, river_bottom, x, y1, fill=line_color,
                                 width=max(1, int(cell * 0.028)))
        # 九宫斜线
        for file_a, rank_a, file_b, rank_b in ((3, 0, 5, 2), (5, 0, 3, 2),
                                               (3, 7, 5, 9), (5, 7, 3, 9)):
            ax, ay = self._xy(file_a, rank_a)
            bx, by = self._xy(file_b, rank_b)
            self.create_line(ax, ay, bx, by, fill=line_color,
                             width=max(1, int(cell * 0.028)))
        # 外框加粗
        self.create_rectangle(x0, y0, x1, y1, outline=line_color,
                              width=max(2, int(cell * 0.05)))
        # 兵、炮位标记
        marks = [(1, 2), (7, 2), (1, 7), (7, 7),
                 (0, 3), (2, 3), (4, 3), (6, 3), (8, 3),
                 (0, 6), (2, 6), (4, 6), (6, 6), (8, 6)]
        for file, rank in marks:
            self._draw_mark(file, rank)
        # 楚河汉界
        _, y_mid = self._xy(0, 4)
        _, y_mid2 = self._xy(0, 5)
        river_y = (y_mid + y_mid2) / 2
        left_text, right_text = ("楚 河", "汉 界")
        if self.flip:
            left_text, right_text = right_text, left_text
        self.create_text(ox + cell * 2, river_y, text=left_text, font=self.river_font,
                         fill=river_color)
        self.create_text(ox + cell * 6, river_y, text=right_text, font=self.river_font,
                         fill=river_color)
        # 坐标（画在外框与棋盘线之间的留白里，不会被棋子挡住）
        if not self.show_numbers:
            return
        bottom_rank, top_rank = (0, 9) if not self.flip else (9, 0)
        for file in range(9):
            x, _ = self._xy(file, bottom_rank)
            _, y = self._xy(file, bottom_rank)
            bottom_number = 9 - file if not self.flip else file + 1
            self.create_text(x, y1 + cell * 0.44, text=_number_text(bottom_number, True),
                             font=self.coord_font, fill=coord_color)
            x, _ = self._xy(file, top_rank)
            _, y = self._xy(file, top_rank)
            top_number = file + 1 if not self.flip else 9 - file
            self.create_text(x, y0 - cell * 0.44, text=str(top_number),
                             font=self.coord_font, fill=coord_color)

    def _draw_skin_board(self, cell: float) -> bool:
        """贴皮肤的棋盘图：按 skin.json（或自动检测）把它的 8×9 网格对齐到我们的格线。

        没有棋盘图、或者不知道网格位置时返回 False（调用方会退回自带木纹棋盘，
        这样绝不会出现"棋子跟棋盘错位"的情况）。
        """
        if self.skin is None:
            return False
        board_path = self.skin.path_of("board")
        if board_path is None or self.skin.grid_rect is None:
            return False
        try:
            from PIL import Image, ImageTk
        except Exception:  # noqa: BLE001
            return False
        layout = getattr(self, "_skin_art", None)
        crop = getattr(self, "_skin_art_crop", None)
        if layout is None or crop is None:      # 布局还没算过 → 按网格对齐现算一个
            gx0, gy0, gx1, gy1 = self.skin.grid_rect
            scale_x = (cell * 8) / max(1.0, gx1 - gx0)
            scale_y = (cell * 9) / max(1.0, gy1 - gy0)
            margin_x = (gx1 - gx0) * 0.03
            margin_y = (gy1 - gy0) * 0.03
            crop = (max(0, int(gx0 - margin_x)), max(0, int(gy0 - margin_y)),
                    int(gx1 + margin_x), int(gy1 + margin_y))
            layout = (self.origin[0] - (gx0 - crop[0]) * scale_x,
                      self.origin[1] - (gy0 - crop[1]) * scale_y, scale_x, scale_y)
        image_left, image_top, scale_x, scale_y = layout
        adjust = dict(getattr(self.skin, "adjust", None) or {})
        factor = float(adjust.get("scale") or 100.0) / 100.0
        factor = max(0.4, min(2.5, factor))
        try:
            with Image.open(board_path) as raw:
                image = raw.convert("RGBA").crop(crop)
                image_w, image_h = image.size
                new_w = max(8, int(round(image_w * scale_x * factor)))
                new_h = max(8, int(round(image_h * scale_y * factor)))
                key = f"skinboard@{new_w}x{new_h}"
                photo = self._skin_images.get(key)
                if photo is None:
                    scaled = image.resize((new_w, new_h), Image.LANCZOS)
                    photo = ImageTk.PhotoImage(scaled, master=self)
                    self._skin_images[key] = photo
        except Exception as exc:  # noqa: BLE001
            self._log_once(f"skin-board-fail-{board_path}",
                           f"棋盘：皮肤棋盘图贴图失败：{exc}")
            return False
        dx = float(adjust.get("dx") or 0.0) * cell
        dy = float(adjust.get("dy") or 0.0) * cell
        self.create_image(image_left * factor + dx, image_top * factor + dy,
                          image=photo, anchor="nw", tags="skinboard")
        return True

    def _draw_mark(self, file: int, rank: int) -> None:
        cell = self.cell
        x, y = self._xy(file, rank)
        gap = cell * 0.1
        length = cell * 0.17
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            if self._view_file(file) == 0 and dx < 0:
                continue
            if self._view_file(file) == 8 and dx > 0:
                continue
            cx, cy = x + dx * gap, y + dy * gap
            self.create_line(cx + dx * length, cy, cx, cy, cx, cy + dy * length,
                             fill=self.palette["line"], width=max(1, int(cell * 0.026)))

    def _draw_pieces_and_marks(self, animate: bool = False) -> None:
        board = self.state.get("board") or []
        if not board:
            return
        cell = self.cell
        radius = cell * 0.42
        animate_move: Optional[Tuple[str, str]] = None
        if animate and self.state.get("lastMove"):
            last = self.state["lastMove"]
            if last.get("from") and last.get("to"):
                animate_move = (str(last["from"]), str(last["to"]))

        # 上一手高亮
        if self.state.get("lastMove"):
            last = self.state["lastMove"]
            for square in (str(last.get("from")), str(last.get("to"))):
                if not square or square == "None":
                    continue
                x, y = self._piece_center(square)
                self.create_rectangle(x - radius, y - radius, x + radius, y + radius,
                                      outline=LAST_COLOR, width=max(2, int(cell * 0.04)))

        # 棋子
        for rank in range(10):
            row = board[9 - rank] if 0 <= 9 - rank < len(board) else ""
            index = 0
            for ch in row:
                if ch.isdigit():
                    index += int(ch)
                    continue
                file = index
                index += 1
                if file > 8 or ch == ".":
                    continue
                square = FILES[file] + str(rank)
                if animate_move and square == animate_move[1]:
                    continue                      # 稍后用动画画出来
                self._draw_piece(square, ch)

        # 选中与落点提示
        mask_photo = self._skin_photo("mask", int(round(cell))) if self.skin else None
        mask2_photo = self._skin_photo("mask2", int(round(cell))) if self.skin else None
        selected = self.state.get("selected")
        if selected:
            x, y = self._piece_center(str(selected))
            if mask_photo is not None:
                self.create_image(x, y, image=mask_photo)
            else:
                self.create_oval(x - radius * 1.08, y - radius * 1.08,
                                 x + radius * 1.08, y + radius * 1.08,
                                 outline=SELECT_COLOR, width=max(3, int(cell * 0.06)))
        for square in (self.state.get("destinations") or []):
            target = self.piece_at(str(square))
            x, y = self._piece_center(str(square))
            if mask2_photo is not None:
                self.create_image(x, y, image=mask2_photo)      # 皮肤自带的小框
            elif target != ".":
                self.create_oval(x - radius * 1.05, y - radius * 1.05,
                                 x + radius * 1.05, y + radius * 1.05,
                                 outline=CAPTURE_COLOR, width=max(3, int(cell * 0.055)))
            else:
                dot = cell * 0.16
                self.create_oval(x - dot, y - dot, x + dot, y + dot,
                                 fill=DOT_COLOR, outline="")
        # 被将军的红圈
        if self.state.get("check"):
            king_char = "K" if self.state.get("side") == "w" else "k"
            for rank in range(10):
                row = board[9 - rank] if 0 <= 9 - rank < len(board) else ""
                index = 0
                for ch in row:
                    if ch.isdigit():
                        index += int(ch)
                        continue
                    file = index
                    index += 1
                    if ch == king_char:
                        x, y = self._xy(file, rank)
                        self.create_oval(x - radius * 1.1, y - radius * 1.1,
                                         x + radius * 1.1, y + radius * 1.1,
                                         outline=CHECK_COLOR, width=max(3, int(cell * 0.07)))

        if animate_move:
            self._start_animation(animate_move[0], animate_move[1])

    def _draw_piece(self, square: str, piece: str, offset: Tuple[float, float] = (0, 0),
                    tags: Optional[List[str]] = None) -> None:
        x, y = self._piece_center(square)
        x += offset[0]
        y += offset[1]
        cell = self.cell
        radius = cell * 0.42
        palette = self.palette
        tag_list = ["piece:" + square]
        if tags:
            tag_list.extend(tags)
        self._draw_piece_shadow(x, y, radius, tag_list)
        if self.skin is not None:               # 棋子皮肤：直接贴图
            key = ("r" if piece.isupper() else "b") + piece.lower()
            scale = float(getattr(self.skin, "piece_scale", 0.92) or 0.92)
            photo = self._skin_photo(key, int(round(cell * scale)))
            if photo is not None:
                self.create_image(x, y, image=photo, tags=tuple(tag_list) +
                                  ("glyph:" + square,))
                return
        # 牌面
        self.create_oval(x - radius, y - radius, x + radius, y + radius,
                         fill=palette["face"], outline=palette["piece_edge"],
                         width=max(2, int(cell * 0.045)), tags=tuple(tag_list))
        self.create_oval(x - radius * 0.82, y - radius * 0.82, x + radius * 0.82,
                         y + radius * 0.82, outline=palette["piece_edge"], width=1,
                         tags=tuple(tag_list))
        ink = RED_INK if piece.isupper() else BLACK_INK
        self.create_text(x, y, text=self._glyph(piece), font=self.piece_font, fill=ink,
                         tags=tuple(tag_list) + ("glyph:" + square,))

    # ------------------------------------------------------------ 棋子阴影
    #: 阴影参数（都是"占棋子半径的比例"）：
    #: 阴影圆大小 / 往右下偏移 / 高斯模糊半径 / 浓度（0~255，越小越淡）
    SHADOW_SIZE = 0.97
    SHADOW_OFFSET = 0.34
    SHADOW_BLUR = 0.10
    SHADOW_ALPHA = 95

    def _draw_piece_shadow(self, x: float, y: float, radius: float,
                           tag_list: List[str]) -> None:
        """在棋子**右下方**垫一层淡淡的阴影（所有棋子、所有皮肤都一样）。

        ★ 用一张模糊过的半透明图片，而不是 ``create_oval``：
          Tk 的椭圆没有透明度，画出来是硬边的一坨；图片带 alpha 通道，
          Tk 8.6 会把它和棋盘（包括下面的格线）混在一起，才是柔和的投影。

        "从左上角打光"是靠**偏移量比模糊扩散更大**做出来的：阴影圆和棋子一样大、
        往右下挪 0.30 半径，模糊只往四周扩散 0.12 半径 —— 于是右下角露出一条
        柔和的暗边，左上角那半还藏在棋子底下，不会变成一圈轮廓线。
        """
        if not self.piece_shadow:
            return
        try:
            photo = self._shadow_photo(radius)
            if photo is None:
                return
            offset = radius * self.SHADOW_OFFSET
            self.create_image(x + offset, y + offset, image=photo,
                              tags=tuple(tag_list) + ("piece-shadow",))
        except Exception as exc:  # noqa: BLE001
            # ★ 阴影只是装饰：万一贴图失败，绝不能连累棋子本身（不然整盘棋子都没了）
            self._shadow_broken = True
            self._log_once("shadow-fail", f"棋子阴影：画不出来，已自动关闭（{exc}）")

    def _shadow_photo(self, radius: float):
        """生成（并缓存）一张棋子阴影图：圆形 → 高斯模糊 → 半透明黑。"""
        radius = max(4.0, float(radius))
        if self._shadow_broken:
            return None
        key = int(round(radius * 4))              # 尺寸量化一下，缓存不会无限膨胀
        cached = self._shadow_cache.get(key)
        if cached is not None:
            return cached
        photo = None
        try:
            from PIL import Image, ImageDraw, ImageFilter, ImageTk

            box = max(10, int(round(radius * 3.1)))     # 留够模糊扩散的余量
            image = Image.new("RGBA", (box, box), (0, 0, 0, 0))
            shadow_radius = radius * self.SHADOW_SIZE   # 阴影圆≈棋子大小
            inset = (box - shadow_radius * 2.0) / 2.0
            ImageDraw.Draw(image).ellipse([inset, inset, box - inset, box - inset],
                                          fill=(0, 0, 0, self.SHADOW_ALPHA))
            image = image.filter(ImageFilter.GaussianBlur(
                max(1.0, radius * self.SHADOW_BLUR)))
            photo = ImageTk.PhotoImage(image, master=self)
        except Exception:  # noqa: BLE001 - 没有 Pillow 就不显示阴影，不影响其它功能
            self._shadow_broken = True
            photo = None
        self._shadow_cache[key] = photo
        return photo

    # ------------------------------------------------------------ 走子动画
    def _start_animation(self, source: str, target: str) -> None:
        piece = self.piece_at(target)
        if piece == ".":
            piece = self.piece_at(source)
        if piece == ".":
            return
        self._anim = {"from": source, "to": target, "piece": piece, "step": 0, "total": 9}
        self._cancel_anim_job()
        self._anim_job = self.after(16, self._anim_step)

    def _cancel_anim_job(self) -> None:
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except Exception:  # noqa: BLE001
                pass
            self._anim_job = None

    def _anim_step(self) -> None:
        anim = self._anim
        if not anim:
            return
        anim["step"] = int(anim["step"]) + 1
        step = int(anim["step"])
        total = int(anim["total"])
        if step > total:
            self._anim = None
            self._cancel_anim_job()
            self.redraw()
            return
        source = str(anim["from"])
        target = str(anim["to"])
        sx, sy = self._piece_center(source)
        tx, ty = self._piece_center(target)
        ratio = step / total
        x = sx + (tx - sx) * ratio
        y = sy + (ty - sy) * ratio
        self.delete("anim")
        cell = self.cell
        radius = cell * 0.42
        self._draw_piece_shadow(x, y, radius, ["anim"])       # 走子动画也带同样的阴影
        self.create_oval(x - radius, y - radius, x + radius, y + radius, fill=PIECE_FACE,
                         outline=PIECE_EDGE, width=max(2, int(cell * 0.045)),
                         tags=("anim",))
        piece = str(anim["piece"])
        ink = RED_INK if piece.isupper() else BLACK_INK
        self.create_text(x, y, text=self._glyph(piece), font=self.piece_font, fill=ink,
                         tags=("anim",))
        self._anim_job = self.after(16, self._anim_step)

    # ------------------------------------------------------------ 变例箭头
    def _draw_arrows(self) -> None:
        if not self.show_arrows or not self.pv_moves:
            return
        cell = self.cell
        for index, iccs in enumerate(self.pv_moves[:3]):
            if len(iccs) < 4:
                continue
            try:
                fx, fy = self._xy(FILES.index(iccs[0]), int(iccs[1]))
                tx, ty = self._xy(FILES.index(iccs[2]), int(iccs[3]))
            except (ValueError, IndexError):
                continue
            width = max(2, int(cell * (0.09 if index == 0 else 0.06)))
            color = ARROW_COLOR if index == 0 else "#7fb0dd"
            self.create_line(fx, fy, tx, ty, fill=color, width=width, arrow=tk.LAST,
                             arrowshape=(cell * 0.28, cell * 0.32, cell * 0.12),
                             stipple="" if index == 0 else "gray25")


def _number_text(value: int, chinese: bool) -> str:
    digits = "一二三四五六七八九"
    if chinese and 1 <= value <= 9:
        return digits[value - 1]
    return str(value)
