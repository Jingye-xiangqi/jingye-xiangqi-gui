# -*- coding: utf-8 -*-
"""原生桌面主窗口（Tkinter）：不做任何网络通讯，直接驱动 Session。"""

from __future__ import annotations

import queue
import os
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

from ..board import FILES, side_name
from ..book import BOOK_STRATEGIES
from ..game import decode_bytes
from ..session import Session, exe_dir
from ..sounds import SoundPlayer
from ..link import ORIENTATIONS, orientation_label
from .board_view import GLYPHS_SIMPLE, GLYPHS_TRAD, THEMES, BoardView
from . import backgrounds as backgrounds_module
from .editor import PALETTE_BLACK, PALETTE_RED, PositionEditor
from .icons import IconSet
from .icons import available as icons_available

UI_FONT = ("Microsoft YaHei UI", 9)
UI_FONT_BOLD = ("Microsoft YaHei UI", 9, "bold")
TITLE_FONT = ("Microsoft YaHei UI", 11, "bold")
MONO_FONT = ("Consolas", 9)

BG = "#f3efe6"
PANEL_BG = "#fbf8f1"
ACCENT = "#8c2f22"
MUTED = "#6f6a5e"
GREEN = "#2f7d4f"
BLUE = "#2f6fb0"
RED_INK = "#b3271f"
BLACK_INK = "#25303a"
CHART_BG = "#ffffff"
CHART_LINE = "#c9a24a"
CHART_GRID = "#e3ded3"
TOOLBAR_BG = "#eae3d5"
BUTTON_BG = "#fdfbf6"
BUTTON_FG = "#332f27"

#: 软件名（不带版本号，界面里显示名字的地方用它）
APP_BASE_NAME = "静夜象棋界面"
#: 版本号：窗口标题 = 软件名 + 版本号 + （QQ:…）
APP_VERSION = "1.6"
APP_QQ = "QQ:3375227582"
#: 窗口标题：静夜象棋界面1.6（QQ:3375227582）
APP_NAME = f"{APP_BASE_NAME}{APP_VERSION}（{APP_QQ}）"

#: 三种模式（顶部显眼的三选一）
ENGINE_MODES = (
    ("red", "引擎执红", "引擎走红方 · 你走黑方"),
    ("black", "引擎执黑", "引擎走黑方 · 你走红方"),
    ("analyze", "分析模式", "引擎持续分析 · 不自动走子"),
)

#: 三个模式按钮对应的图标
MODE_ICONS = {"red": "engine_red", "black": "engine_black", "analyze": "analyze"}

#: 工具栏图标按钮：键 → (图标名, 提示文字)
TOOLBAR_ICONS_ROW1 = (
    ("new", "new", "新建棋局"),
    ("open", "open", "打开棋谱（Ctrl+O）"),
    ("save", "save", "保存棋谱（Ctrl+S）"),
)
TOOLBAR_ICONS_ROW2 = (
    ("first", "first", "回到开局（Home）"),
    ("prev", "prev", "上一步（←）"),
    ("play", "play", "自动播放（空格）"),
    ("next", "next", "下一步（→）"),
    ("last", "last", "跳到末尾（End）"),
    ("undo", "undo", "悔棋（Ctrl+Z）"),
    ("redo", "redo", "重做（Ctrl+Y）"),
    ("edit", "edit", "编辑局面：自由摆子 / 清空 / 初始局面 / FEN"),
    ("copy_fen", "copy_fen", "复制局面：把当前局面 FEN 复制到剪贴板"),
    ("paste", "paste", "粘贴局面/棋谱：图片→识别局面，FEN→摆上去，棋谱文本→导入"),
    ("copy_game", "copy_game", "复制棋谱：把全部着法（中文记谱）复制到剪贴板"),
    ("flip", "flip", "翻转棋盘：切换红黑视角"),
)

#: 开局库策略（下拉框用：键 + 中文说明）
BOOK_STRATEGY_ITEMS = list(BOOK_STRATEGIES.items())


def make_check(parent, text: str, variable, command=None, background: str = PANEL_BG,
               foreground: str = "#332f27", font=None) -> tk.Frame:
    """统一的开关控件：**打勾＝开启、空框＝关闭**（不依赖系统主题的叉号画法）。

    返回一个 Frame，`.toggle` 是点击处理；变量变化时会自动刷新勾号。
    """
    row = tk.Frame(parent, background=background)
    box = tk.Label(row, text="", width=2, relief="solid", borderwidth=1,
                   background="#ffffff", foreground="#1d7a3d",
                   font=(font or ("Microsoft YaHei UI", 10, "bold")), cursor="hand2")
    box.pack(side="left")
    label = tk.Label(row, text=text, background=background, foreground=foreground,
                     font=font or UI_FONT, cursor="hand2")
    label.pack(side="left", padx=(5, 0))

    def refresh(*_args) -> None:
        try:
            on = bool(variable.get())
        except Exception:  # noqa: BLE001
            on = False
        box.configure(text="✔" if on else "")

    def toggle(_event=None) -> None:
        try:
            variable.set(not bool(variable.get()))
        except Exception:  # noqa: BLE001
            return
        refresh()
        if command is not None:
            command()

    box.bind("<Button-1>", toggle)
    label.bind("<Button-1>", toggle)
    row.toggle = toggle                     # type: ignore[attr-defined]
    row.refresh = refresh                   # type: ignore[attr-defined]
    try:
        variable.trace_add("write", lambda *args: refresh())
    except Exception:  # noqa: BLE001
        pass
    row.pack = row.pack                      # type: ignore[assignment]
    refresh()
    return row

#: 棋盘主题（下拉框用：键 + 中文名）
BOARD_THEME_ITEMS = [(key, value["name"]) for key, value in THEMES.items()]


class XiangqiWindow(tk.Tk):
    """棋盘 + 棋谱 + 分析 + 引擎 + 局面 的原生窗口。"""

    def __init__(self, session: Session, title: str = APP_NAME):
        super().__init__()
        self.session = session
        self.title(title)
        self.geometry("1400x900")
        self.minsize(1180, 760)
        self.configure(bg=BG)
        self._set_icon()

        self.events: "queue.Queue[Dict[str, object]]" = queue.Queue()
        self.session.broadcast = self.events.put

        self.state: Dict[str, object] = {}
        self._last_ply = 0
        self._last_tree_rev = -1
        self._last_analysis_stamp = 0.0
        self._autoplay_job: Optional[str] = None
        self._autoplay = False
        self._engine_option_widgets: Dict[str, tk.Widget] = {}
        self._pv_rows: List[Dict[str, tk.Widget]] = []
        self._pv_index = 0
        self._closing = False
        self._drain_job: Optional[str] = None
        self._spin_editing = False
        self._engine_path_focus = False
        self._engine_option_names: Optional[List[str]] = None
        self._engine_option_signature: Optional[list] = None
        self._line_nodes: Dict[int, int] = {}
        self.tree_data: Dict[str, object] = {}
        self.editor = PositionEditor()
        self.editor_active = False
        self.sound_enabled = True
        self.sounds = SoundPlayer(enabled=True)
        self._last_sound_ply = 0
        # 界面共享变量（工具栏 / 设置对话框都会用）
        self.mode_var = tk.StringVar(value="")
        self.mode_buttons: Dict[str, tk.Button] = {}
        self.engine_path_var = tk.StringVar(value="")
        # 音效开关也按上次的来（存在 settings.json 里）
        self.sound_enabled = bool((session.settings or {}).get("sound_enabled", True))
        self.sounds.enabled = self.sound_enabled
        # 分析参数（引擎自身的选项都在「引擎设置」里动态生成，这里只放程序级的搜索限制）
        self.movetime_sec_var = tk.DoubleVar(value=1.5)
        self.depth_var = tk.IntVar(value=0)
        self.multipv_var = tk.IntVar(value=1)
        self.threads_var = tk.IntVar(value=max(1, os.cpu_count() or 4))
        self.hash_var = tk.IntVar(value=128)
        self.book_use_var = tk.BooleanVar(value=True)
        self.book_max_ply_var = tk.IntVar(value=0)
        self.book_strategy_var = tk.StringVar(value=BOOK_STRATEGY_ITEMS[0][1])
        # 界面显示设置
        self.show_arrows_var = tk.BooleanVar(value=True)
        self.show_numbers_var = tk.BooleanVar(value=True)
        self.show_statusbar_var = tk.BooleanVar(value=True)
        self.piece_shadow_var = tk.BooleanVar(value=True)
        self.board_theme_var = tk.StringVar(value=BOARD_THEME_ITEMS[0][1])
        self.board_scale_var = tk.IntVar(value=100)
        self.skin_choice_var = tk.StringVar(value="")      # 皮肤列表里当前选中项
        self._skin_items: List[Dict[str, object]] = []     # 扫描到的皮肤（名字→路径）
        self.book_merge_var = tk.BooleanVar(value=True)
        self.cloud_use_var = tk.BooleanVar(value=True)
        self.cloud_endpoint_var = tk.StringVar(value="")
        self.cloud_timeout_var = tk.IntVar(value=3)
        self.sound_var = tk.BooleanVar(
            value=bool((session.settings or {}).get("sound_enabled", True)))
        self.engine_dialog: Optional[tk.Toplevel] = None
        self.book_dialog: Optional[tk.Toplevel] = None
        self.view_dialog: Optional[tk.Toplevel] = None

        self._build_style()
        self._build_widgets()
        self._bind_shortcuts()
        self._drain_job = self.after(60, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._enable_file_drop()
        self.after(200, self._initial_refresh)

    # ================================================================ 外观
    def _set_icon(self) -> None:
        for candidate in (Path(__file__).resolve().parents[2] / "assets" / "app.ico",
                          exe_dir() / "assets" / "app.ico"):
            try:
                if candidate.exists():
                    self.iconbitmap(str(candidate))
                    return
            except Exception:  # noqa: BLE001
                continue

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=UI_FONT, background=BG)
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL_BG, relief="flat")
        style.configure("TLabel", background=BG, font=UI_FONT)
        style.configure("Panel.TLabel", background=PANEL_BG, font=UI_FONT)
        style.configure("Title.TLabel", background=BG, font=TITLE_FONT, foreground=ACCENT)
        style.configure("Muted.TLabel", background=PANEL_BG, foreground=MUTED, font=UI_FONT)
        style.configure("TButton", font=UI_FONT, padding=(8, 3))
        style.configure("Accent.TButton", font=UI_FONT_BOLD, padding=(8, 3),
                        foreground="#ffffff", background=ACCENT)
        style.map("Accent.TButton", background=[("active", "#a83a28")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", font=UI_FONT, padding=(14, 6))
        style.configure("TCheckbutton", background=PANEL_BG, font=UI_FONT)
        style.configure("TRadiobutton", background=PANEL_BG, font=UI_FONT)
        style.configure("TScale", background=PANEL_BG)
        style.configure("Treeview", font=UI_FONT, rowheight=22)

    # ================================================================ 布局
    def _build_widgets(self) -> None:
        """界面骨架：顶部一排工具栏 + 主区三栏（左：开局库/云库，中：棋盘，右：棋谱+分析）。"""
        self._build_toolbar()
        body = ttk.Frame(self, style="TFrame")
        body.pack(side="top", fill="both", expand=True, padx=6, pady=(0, 2))
        self.body_frame = body

        self.left_panel = ttk.Frame(body, style="Panel.TFrame", width=302)
        self.left_panel.pack(side="left", fill="y")
        self.left_panel.pack_propagate(False)

        self.right_panel = ttk.Frame(body, style="Panel.TFrame", width=336)
        self.right_panel.pack(side="right", fill="y", padx=(6, 0))
        self.right_panel.pack_propagate(False)

        self.center_panel = ttk.Frame(body, style="TFrame")
        self.center_panel.pack(side="left", fill="both", expand=True, padx=(6, 0))

        self._build_left_panel(self.left_panel)
        self._build_center_panel(self.center_panel)
        self._build_right_panel(self.right_panel)
        self._build_statusbar()

    # ---------------------------------------------------------------- 顶部工具栏
    def _build_toolbar(self) -> None:
        """顶部工具栏：**两行**、都靠左排列（按钮多，一行放不下会被挤掉）。

        第一行：设置类文字按钮（界面/引擎/开局库/连线/断开/恢复出厂，最左边）
                + 文件（新建/打开/保存）+ 三种引擎模式 —— 后面这些都是**图标按钮、不带文字**
        第二行：播放（首/上一步/播放/下一步/末尾/悔棋/重做）+ 局面（编辑/复制局面/
                粘贴局面·棋谱/复制棋谱/翻转棋盘）—— 同样是图标按钮
        右侧：背景选择下拉框、音效开关、连线状态、当前模式徽标。

        图标由 ``tools/make_toolbar_icons.py`` 生成、放在 ``assets/toolbar``；
        鼠标悬停有文字提示。万一没有 Pillow / 图标文件，会自动退回文字按钮，功能不受影响。
        """
        outer = tk.Frame(self, background=TOOLBAR_BG)
        outer.pack(side="top", fill="x")
        row1 = tk.Frame(outer, background=TOOLBAR_BG)
        row1.pack(side="top", fill="x")
        row2 = tk.Frame(outer, background=TOOLBAR_BG)
        row2.pack(side="top", fill="x")
        bar = row1                       # 下面 add() 默认加到第一行
        self.toolbar_buttons: Dict[str, tk.Button] = {}
        #: 图标按钮：键 → 按钮（供测试/状态切换用）
        self.icon_buttons: Dict[str, tk.Button] = {}
        self._icon_now: Dict[str, str] = {}          # 键 → 当前图标名（播放会换成停止）
        self._icon_selected: set = set()             # 处于"选中"状态的图标按钮
        self.icon_size = 30
        self.icons = IconSet(self, self.icon_size) if icons_available() else None
        self.use_icons = self.icons is not None      # 没有图标就退回文字按钮

        # ★ 右侧这些状态/开关**贴在两行自己的右端**（以前挂在整块工具栏上，
        #   会多挤出一条"左边全空"的横带，工具栏和内容区之间就多出一大块空白）。
        self.mode_badge = tk.Label(row2, text="", font=("Microsoft YaHei UI", 9, "bold"),
                                   fg=ACCENT, background=TOOLBAR_BG)
        self.mode_badge.pack(side="right", padx=(6, 8))
        self.link_badge = tk.Label(row2, text="连线：未连接",
                                   font=("Microsoft YaHei UI", 9), fg=MUTED,
                                   background=TOOLBAR_BG)
        self.link_badge.pack(side="right", padx=(6, 0))
        self.sound_box = make_check(row2, "音效", self.sound_var, self.on_toggle_sound,
                                    background=TOOLBAR_BG, foreground=BUTTON_FG,
                                    font=("Microsoft YaHei UI", 9))
        self.sound_box.pack(side="right", padx=(8, 0))
        self._tooltip(self.sound_box, "走子 / 吃子 / 将军音效开关")
        # 背景选择（backgrounds 文件夹里的图；默认＝跟随皮肤 / 自带木纹）
        background_box = tk.Frame(row1, background=TOOLBAR_BG)
        background_box.pack(side="right", padx=(10, 4))
        tk.Label(background_box, text="背景", background=TOOLBAR_BG, foreground=BUTTON_FG,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.background_var = tk.StringVar(value=backgrounds_module.DEFAULT_LABEL)
        self.background_box = ttk.Combobox(
            background_box, width=14, state="readonly", textvariable=self.background_var,
            values=backgrounds_module.choices())
        self.background_box.pack(side="left", padx=(4, 0))
        self.background_box.bind("<<ComboboxSelected>>", self.on_background_change)
        self._tooltip(self.background_box,
                      "桌面背景：backgrounds 文件夹里的图（默认＝跟随皮肤 / 自带木纹）")
        # 两行都靠左、各自留一点上下间距（不再让按钮挤成一行被裁掉）
        row1.pack_configure(padx=6, pady=(4, 2))
        row2.pack_configure(padx=6, pady=(0, 4))

        def add(text: str, command, accent: bool = False, tip: str = "") -> tk.Button:
            return add_to(bar, text, command, accent, tip)

        def add_to(target, text: str, command, accent: bool = False,
                   tip: str = "") -> tk.Button:
            button = tk.Button(
                target, text=text, command=command, relief="raised", borderwidth=1,
                padx=7, pady=2, cursor="hand2", font=("Microsoft YaHei UI", 9),
                background=ACCENT if accent else BUTTON_BG,
                foreground="#ffffff" if accent else BUTTON_FG,
                activebackground=ACCENT, activeforeground="#ffffff")
            button.pack(side="left", padx=1)
            if tip:
                self._tooltip(button, tip)
            self.toolbar_buttons[text] = button
            return button

        def separator(target=None) -> None:
            tk.Frame(target or bar, width=1, height=22, background="#c8bfae").pack(
                side="left", padx=6, pady=4)

        def add_icon(target, key: str, icon: str, tip: str, command,
                     mode: bool = False) -> Optional[tk.Button]:
            """加一个**图标按钮**（不带文字，鼠标悬停显示提示）。

            没有图标资源时（缺 Pillow / 缺 PNG）自动退回文字按钮，功能一样。
            """
            if not self.use_icons:
                source = tip() if callable(tip) else tip
                text = str(source).split("（")[0].split("：")[0].strip() or key
                return add_to(target, text, command, tip=tip)
            photo = self.icons.get(icon, "normal")
            if photo is None:
                source = tip() if callable(tip) else tip
                text = str(source).split("（")[0].split("：")[0].strip() or key
                return add_to(target, text, command, tip=tip)
            patch = 3 if mode else 2
            button = tk.Button(target, image=photo, command=command, relief="flat",
                               borderwidth=1, highlightthickness=0, padx=patch, pady=2,
                               cursor="hand2", background=TOOLBAR_BG,
                               activebackground=TOOLBAR_BG)
            button.pack(side="left", padx=1)
            button._icon_photo = photo            # 保住引用，别被回收
            self.icon_buttons[key] = button
            self._icon_now[key] = icon

            def swap(variant: str, name=key) -> None:
                if variant == "normal" and name in self._icon_selected:
                    variant = "press"
                photo = self.icons.get(self._icon_now.get(name, icon), variant)
                if photo is not None:
                    button.configure(image=photo)
                    button._icon_photo = photo

            button.bind("<Enter>", lambda _event: swap("hover"))
            button.bind("<Leave>", lambda _event: swap("normal"))
            button.bind("<ButtonPress-1>", lambda _event: swap("press"), add="+")
            button.bind("<ButtonRelease-1>", lambda _event: swap("hover"), add="+")
            self._tooltip(button, tip)
            return button

        # ★ 第一行最左边：设置类按钮保留文字（用户要求）
        add("界面", self.on_open_view_settings,
            tip="界面设置：背景 / 箭头 / 线路数字 / 状态栏 / 主题 / 棋盘大小 / 棋子皮肤")
        add("引擎", self.on_open_engine_settings,
            tip="引擎路径 + 引擎自带的 UCI 选项（自动探测，改完立即生效）")
        add("开局库", self.on_open_book_settings, tip="本地开局库与在线云库设置")
        add("连线", self.on_toggle_link,
            tip="连到别的象棋平台（天天象棋/QQ象棋等）：点一下选窗口，之后自动读盘与走子")
        add("连线设置", self.on_open_link_settings,
            tip="连线设置：扫描间隔 / 点击节奏 / 识别模型 / 棋盘区域 / 确认方式")
        add("断开", self.on_cancel_link, tip="取消连线：卡住时点它退出，不会卡死")
        add("恢复出厂", self.on_reset_settings,
            tip="恢复出厂设置：删除 settings.json，所有设置回到默认值（会先弹确认框）",
            accent=True)
        separator(row1)
        # 文件（图标）
        add_icon(row1, "new", "new", "新建棋局", self.on_new)
        add_icon(row1, "open", "open", "打开棋谱（Ctrl+O）", self.on_import)
        add_icon(row1, "save", "save", "保存棋谱（Ctrl+S）", self.on_save_game)
        separator(row1)
        # 三种模式（互斥，可取消；都不选＝纯手动打谱）—— 图标按钮，选中时高亮
        self.mode_var = tk.StringVar(value="")
        for value, title, subtitle in ENGINE_MODES:
            key = MODE_ICONS[value]
            button = add_icon(row1, key, key,
                              f"{title}：{subtitle}（再点一次取消，都不选＝纯手动打谱）",
                              lambda v=value: self.on_engine_mode(v), mode=True)
            if button is not None:
                self.mode_buttons[value] = button
        # ★ 第二行：播放 + 局面（全部图标，不带文字）
        bar = row2
        self.toolbar_row2 = row2
        add_icon(row2, "first", "first", "回到开局（Home）",
                 lambda: self.navigate("first"))
        add_icon(row2, "prev", "prev", "上一步（←）", lambda: self.navigate("prev"))
        self.autoplay_button = add_icon(
            row2, "play", "play",
            lambda: ("暂停自动播放（空格）" if getattr(self, "_autoplay", False)
                     else "自动播放（空格）"), self.toggle_autoplay)
        add_icon(row2, "next", "next", "下一步（→）", lambda: self.navigate("next"))
        add_icon(row2, "last", "last", "跳到末尾（End）", lambda: self.navigate("last"))
        separator(row2)
        add_icon(row2, "undo", "undo", "悔棋（Ctrl+Z）", self.on_undo)
        add_icon(row2, "redo", "redo", "重做（Ctrl+Y）", self.on_redo)
        separator(row2)
        # 局面：编辑 / 复制局面 / 粘贴局面·棋谱 / 复制棋谱 / 翻转棋盘
        add_icon(row2, "edit", "edit",
                 "编辑局面：自由摆子 / 清空 / 初始局面 / FEN", self.on_edit_position)
        add_icon(row2, "copy_fen", "copy_fen",
                 "复制局面：把当前局面 FEN 复制到剪贴板", self.on_copy_fen)
        add_icon(row2, "paste", "paste",
                 "粘贴局面/棋谱：图片→识别局面，FEN→摆上去，棋谱文本→导入棋谱",
                 self.on_paste_smart)
        add_icon(row2, "copy_game", "copy_game",
                 "复制棋谱：把全部着法（中文记谱）复制到剪贴板", self.on_copy_game)
        add_icon(row2, "flip", "flip", "翻转棋盘：切换红黑视角", self.on_flip)

    def _tooltip(self, widget: tk.Widget, text) -> None:
        """鼠标悬停提示（tkinter 没有内置的，简单实现一个）。

        ``text`` 也可以是一个函数（每次显示时现算），例如播放按钮要在
        「自动播放 / 暂停」之间切换提示。
        """
        holder: Dict[str, object] = {}

        def show(_event=None) -> None:
            if holder.get("window") is not None:
                return
            message = text() if callable(text) else text
            if not message:
                return
            window = tk.Toplevel(widget)
            window.wm_overrideredirect(True)
            window.attributes("-topmost", True)
            tk.Label(window, text=str(message), background="#fffbe8", foreground="#3a352c",
                     font=("Microsoft YaHei UI", 8), borderwidth=1, relief="solid",
                     padx=6, pady=2).pack()
            window.wm_geometry(f"+{widget.winfo_rootx()}+{widget.winfo_rooty() + 30}")
            holder["window"] = window

        def hide(_event=None) -> None:
            window = holder.get("window")
            if window is not None:
                try:
                    window.destroy()
                except Exception:  # noqa: BLE001
                    pass
                holder["window"] = None

        widget.bind("<Enter>", show, add="+")
        widget.bind("<Leave>", hide, add="+")
        widget.bind("<ButtonPress>", hide, add="+")

    # ---------------------------------------------------------------- 左栏：开局库 + 云库
    def _build_left_panel(self, parent: ttk.Frame) -> None:
        notebook = ttk.Notebook(parent)
        notebook.pack(fill="both", expand=True, padx=4, pady=4)
        self.book_notebook = notebook
        book_tab = ttk.Frame(notebook, style="Panel.TFrame")
        cloud_tab = ttk.Frame(notebook, style="Panel.TFrame")
        notebook.add(book_tab, text="开局库")
        notebook.add(cloud_tab, text="云库")
        self._build_book_table_panel(book_tab)
        self._build_cloud_table_panel(cloud_tab)

    def _build_book_table_panel(self, parent: ttk.Frame) -> None:
        """左栏「开局库」：当前局面命中的库着法。"""
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", padx=5, pady=(5, 2))
        ttk.Button(row, text="加载…", width=7, command=self.on_load_book).pack(side="left")
        ttk.Button(row, text="设置…", width=7, command=self.on_open_book_settings).pack(
            side="left", padx=3)
        row2 = ttk.Frame(parent, style="Panel.TFrame")
        row2.pack(fill="x", padx=5, pady=(0, 2))
        make_check(row2, "启用库招", self.book_use_var,
                   self.on_toggle_book_use).pack(side="left")
        self.book_strategy_box = ttk.Combobox(
            row2, width=9, state="readonly", textvariable=self.book_strategy_var,
            values=[item[1] for item in BOOK_STRATEGY_ITEMS])
        self.book_strategy_box.pack(side="left", padx=(6, 0))
        self.book_strategy_box.bind("<<ComboboxSelected>>", self.on_book_strategy_change)
        ttk.Label(row2, text="脱谱", style="Muted.TLabel").pack(side="left", padx=(6, 1))
        self.book_max_ply_spin = ttk.Spinbox(
            row2, from_=0, to=60, width=3, justify="center",
            textvariable=self.book_max_ply_var, command=self.on_book_max_ply_change)
        self.book_max_ply_spin.pack(side="left")
        self.book_max_ply_spin.bind("<Return>", lambda event: self.on_book_max_ply_change())
        self.book_info = ttk.Label(parent, text="未加载开局库", style="Muted.TLabel",
                                   wraplength=260, justify="left")
        self.book_info.pack(anchor="w", padx=5)

        table_frame = ttk.Frame(parent, style="Panel.TFrame")
        table_frame.pack(fill="both", expand=True, padx=5, pady=(2, 5))
        scroll = ttk.Scrollbar(table_frame, orient="vertical")
        scroll.pack(side="right", fill="y")
        columns = ("move", "cn", "count", "rate", "score", "source")
        self.book_table = ttk.Treeview(table_frame, columns=columns, show="headings",
                                       height=10, yscrollcommand=scroll.set)
        for key, title, width in (("move", "着法", 44), ("cn", "中文", 54),
                                  ("count", "次数", 34), ("rate", "胜率", 40),
                                  ("score", "分值", 36), ("source", "来源", 46)):
            self.book_table.heading(key, text=title)
            self.book_table.column(key, width=width, anchor="center")
        self.book_table.pack(side="left", fill="both", expand=True)
        scroll.config(command=self.book_table.yview)
        self.book_table.bind("<Double-1>", self.on_book_row_play)
        self._book_hits: List[Dict[str, object]] = []

    def _build_cloud_table_panel(self, parent: ttk.Frame) -> None:
        """左栏「云库」：在线云库着法。"""
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", padx=5, pady=(5, 2))
        self.cloud_enable_var = tk.BooleanVar(value=True)
        make_check(row, "启用", self.cloud_enable_var,
                   lambda: self.session.set_cloud_enabled(
                       self.cloud_enable_var.get())).pack(side="left")
        ttk.Button(row, text="查询", width=6,
                   command=lambda: self.session.refresh_cloud()).pack(side="left", padx=3)
        ttk.Button(row, text="设置…", width=7, command=self.on_open_book_settings).pack(
            side="left")
        row_learn = ttk.Frame(parent, style="Panel.TFrame")
        row_learn.pack(fill="x", padx=5, pady=(0, 2))
        self.cloud_learn_var = tk.BooleanVar(value=True)     # 云库自动学习默认开启
        make_check(row_learn, "自动学习", self.cloud_learn_var,
                   self.on_toggle_cloud_learn).pack(side="left")
        self._tooltip(row_learn.winfo_children()[0],
                      "云库自动学习：开启后查询带 learn=1，给出全部招法、分值与胜率")
        self.cloud_status = ttk.Label(parent, text="尚未查询", style="Muted.TLabel",
                                      wraplength=260, justify="left")
        self.cloud_status.pack(anchor="w", padx=5)

        table_frame = ttk.Frame(parent, style="Panel.TFrame")
        table_frame.pack(fill="both", expand=True, padx=5, pady=(2, 5))
        scroll = ttk.Scrollbar(table_frame, orient="vertical")
        scroll.pack(side="right", fill="y")
        columns = ("move", "cn", "score", "rate", "rank", "note")
        self.cloud_table = ttk.Treeview(table_frame, columns=columns, show="headings",
                                        height=10, yscrollcommand=scroll.set)
        for key, title, width in (("move", "着法", 44), ("cn", "中文", 58),
                                  ("score", "分值", 38), ("rate", "胜率", 46),
                                  ("rank", "排序", 34), ("note", "备注", 46)):
            self.cloud_table.heading(key, text=title)
            self.cloud_table.column(key, width=width, anchor="center")
        self.cloud_table.pack(side="left", fill="both", expand=True)
        scroll.config(command=self.cloud_table.yview)
        self.cloud_table.bind("<Double-1>", self.on_cloud_row_play)
        self._cloud_hits: List[Dict[str, object]] = []

    # ---------------------------------------------------------------- 中栏：棋盘
    def _build_center_panel(self, parent: ttk.Frame) -> None:
        self.board = BoardView(parent, on_click=self.on_board_click,
                               on_move=self.on_board_move,
                               on_right_click=self.on_board_right_click)
        self.board.pack(side="top", fill="both", expand=True)
        # Ctrl + 滚轮：放大／缩小棋盘
        self.board.bind("<Control-MouseWheel>", self.on_board_zoom_wheel)
        self.board_info = ttk.Label(parent, text="红方走棋", style="TLabel",
                                    font=UI_FONT_BOLD, foreground=ACCENT)
        self.board_info.pack(side="top", pady=(2, 0))
        self._build_editor_strip(parent)

    def on_board_wheel(self, event) -> None:
        """滚轮滚动棋盘（棋盘比可视区域大时才有用）。"""
        if self.board_vscroll.winfo_ismapped():
            self.board.yview_scroll(int(-event.delta / 120) * 2, "units")
        return "break"

    def on_board_zoom_wheel(self, event) -> str:
        """Ctrl + 滚轮缩放棋盘。"""
        step = 5 if event.delta > 0 else -5
        self.on_board_scale_change(step, relative=True)
        return "break"

    def on_board_scale_change(self, value=None, relative: bool = False) -> None:
        """棋盘大小（百分比 60-160）。"""
        try:
            current = int(float(self.board_scale_var.get()))
        except (tk.TclError, ValueError):
            current = 100
        if relative:
            target = current + int(value or 0)
        elif value is not None:
            target = int(float(value))
        else:
            target = current
        target = max(60, min(100, target))     # 100% ＝ 撑满中间区域
        self.board_scale_var.set(target)
        self.session.update_settings({"board_scale": target})

    def on_view_toggle(self, key: str) -> None:
        """界面设置里的开关（箭头 / 线路数字 / 状态栏 / 音效）。"""
        variables = {"show_arrows": self.show_arrows_var,
                     "show_numbers": self.show_numbers_var,
                     "show_statusbar": self.show_statusbar_var,
                     "piece_shadow": self.piece_shadow_var}
        if key == "sound":
            self.on_toggle_sound()
            return
        variable = variables.get(key)
        if variable is None:
            return
        self.session.update_settings({key: bool(variable.get())})
        names = {"show_arrows": "棋步提示箭头", "show_numbers": "棋盘线路数字",
                 "show_statusbar": "状态栏", "piece_shadow": "棋子阴影"}
        self._set_status(f"{names.get(key, key)}：{'开' if variable.get() else '关'}")

    def on_board_theme_change(self, _event=None) -> None:
        label = self.board_theme_var.get()
        for key, name in BOARD_THEME_ITEMS:
            if name == label:
                self.session.update_settings({"board_theme": key})
                self._set_status(f"棋盘主题：{name}")
                return

    def refresh_background_list(self) -> None:
        """重新扫描 backgrounds 文件夹，刷新下拉框选项。"""
        values = backgrounds_module.choices()
        box = getattr(self, "background_box", None)
        if box is not None and box.winfo_exists():
            box.configure(values=values)
        current = self.background_var.get() if hasattr(self, "background_var") else ""
        if current and current not in values:
            # 选的背景文件被删了 → 回到默认
            self.background_var.set(backgrounds_module.DEFAULT_LABEL)
            self.session.update_settings({"background": ""})

    def on_background_change(self, _event=None) -> None:
        """切换桌面背景（backgrounds 文件夹里的图）。"""
        label = self.background_var.get()
        name = "" if label == backgrounds_module.DEFAULT_LABEL else label
        self.session.update_settings({"background": name})
        self._set_status("背景：" + (name or "默认（皮肤 / 木纹）"))

    def on_choose_skin(self) -> None:
        """选择棋盘棋子皮肤文件夹。"""
        from . import skin as skin_module

        current = str((self.state.get("settings") or {}).get("board_skin") or "")
        initial = current if current and Path(current).is_dir() else str(Path.home() / "Desktop")
        folder = filedialog.askdirectory(title="选择棋盘棋子皮肤文件夹",
                                         initialdir=initial)
        if not folder:
            return
        candidate = skin_module.Skin(Path(folder))
        if not candidate.files:
            messagebox.showwarning("皮肤不可用",
                                   f"这个文件夹里没有找到棋盘/棋子图片：\n{folder}\n\n"
                                   "需要 board 与 ra/rb/rc/rk/rn/rp/rr、ba/bb/bc/bk/bn/bp/br"
                                   "，BMP 或 PNG。")
            return
        self.session.update_settings({"board_skin": str(Path(folder))})
        if candidate.missing:
            self._set_status(f"已用皮肤 {candidate.name}　缺少 {'、'.join(candidate.missing[:5])}")
        else:
            self._set_status(f"已用皮肤：{candidate.name}")

    def on_autodetect_skin(self) -> None:
        """在桌面等常见位置自动找皮肤文件夹。"""
        from . import skin as skin_module

        found = skin_module.find_skin_folders()
        if not found:
            messagebox.showinfo("自动查找皮肤",
                                "没找到皮肤文件夹。\n请把带 board / ra… / ba… 图片的文件夹"
                                "放在桌面，或点「选择文件夹…」手动指定。")
            return
        best = max(found, key=lambda item: len(item.files))
        self.session.update_settings({"board_skin": str(best.folder)})
        self._set_status(f"已找到并使用皮肤：{best.name}　{len(best.files)} 个图片")

    # ---------------------------------------------------------------- 皮肤列表
    def refresh_skin_list(self) -> None:
        """扫描程序旁边的 skins 文件夹，把里面的皮肤列到下拉框里。"""
        from . import skin as skin_module

        self._skin_items = skin_module.scan_skins()
        names = [str(item["name"]) for item in self._skin_items]
        box = getattr(self, "skin_box", None)
        if box is not None and box.winfo_exists():
            box.configure(values=names)
            current = str((self.state.get("settings") or {}).get("board_skin") or "")
            picked = ""
            resolved = skin_module.resolve_skin_path(current) if current else None
            wanted = os.path.normcase(str(resolved)) if resolved is not None else ""
            for item in self._skin_items:
                path = os.path.normcase(str(item["path"]))
                if path == wanted or path == os.path.normcase(current):
                    picked = str(item["name"])
                    break
            self.skin_choice_var.set(picked)
        label = getattr(self, "skin_count_label", None)
        if label is not None and label.winfo_exists():
            root = skin_module.skins_root()
            if names:
                label.configure(text=f"共 {len(names)} 套（{root}）")
            else:
                label.configure(text=f"skins 文件夹里还没有皮肤（{root}）")
        try:
            from ..link import debug_log

            roots = "、".join(str(item) for item in skin_module.skins_roots())
            debug_log(f"皮肤：扫描到 {len(names)} 套（{roots}）")
        except Exception:  # noqa: BLE001
            pass
        return None

    def on_skin_pick(self) -> None:
        """皮肤列表里选中哪一套就立刻换哪一套。"""
        from . import skin as skin_module

        name = self.skin_choice_var.get()
        path = ""
        for item in self._skin_items:
            if str(item["name"]) == name:
                path = str(item["path"])
                break
        if not path:
            return
        candidate = skin_module.Skin(Path(path))
        # 内置皮肤记"名字"就好：打包成 exe 后解包目录每次都变，存路径下次就打不开了
        value = path
        try:
            folder = Path(path)
            if any(os.path.normcase(str(folder.parent)) == os.path.normcase(str(base))
                   for base in skin_module.skins_roots()):
                value = folder.name
        except OSError:
            value = path
        self.session.update_settings({"board_skin": value})
        if candidate.missing:
            self._set_status(f"已用皮肤 {candidate.name}　缺少 {'、'.join(candidate.missing[:4])}")
        else:
            self._set_status(f"已用皮肤：{candidate.name}")

    # ---------------------------------------------------------------- 连线其他平台
    def on_toggle_link(self) -> None:
        """点「连线」：先选目标窗口，然后开始自动读盘/走子；再点一次停止。"""
        linker = getattr(self.session, "linker", None)
        if linker is None:
            messagebox.showwarning("连线", "连线模块没有初始化成功。")
            return
        if linker.running:
            self.on_cancel_link()
            return
        linker.on_event = self.on_link_event
        linker.cancel.clear()
        self._set_status("请把鼠标移到对方象棋窗口上点一下…")

        def pick() -> None:
            hwnd = linker.win32.pick_window(timeout=30, cancel=linker.cancel)
            if linker.cancel.is_set():
                self.after(0, lambda: self._set_status("已取消连线"))
                self.after(0, self._refresh_link_badge)
                return
            if not hwnd:
                self.after(0, lambda: self._set_status("没选中窗口，连线取消"))
                self.after(0, self._refresh_link_badge)
                return
            linker.target_title = linker.win32.window_title()
            self.after(0, self._link_started)

        self._link_thread = threading.Thread(target=pick, daemon=True)
        self._link_thread.start()

    def on_cancel_link(self) -> None:
        """取消连线：无论处于"选窗口"还是"正在识别"，点它都能立刻退出。"""
        linker = getattr(self.session, "linker", None)
        if linker is None:
            return
        linker.stop()                     # 置 cancel 标志 + running=False（线程会自己退出）
        linker.win32.hwnd = None
        linker.target_title = ""
        linker.sent_ply = -1
        self._set_status("已取消连线")
        self._refresh_link_badge()

    def _link_started(self) -> None:
        linker = self.session.linker
        linker.start()
        note = f"连线中：{linker.target_title or '已选中窗口'}"
        if linker.recognizer.backend != "yolo":
            note += "　模板匹配"
        self._set_status(note)
        self._refresh_link_badge()

    def on_link_event(self, kind: str, payload: dict) -> None:
        """连线模块的回调（在它的线程里调用）→ 回到主线程更新界面。"""
        def update() -> None:
            if kind == "remote_move":
                self._set_status(f"对方走了：{payload.get('from')}→{payload.get('to')}")
            elif kind == "click":
                self._set_status(f"已自动走子：{payload.get('from')}→{payload.get('to')}")
            elif kind == "scan":
                self._link_last_scan = payload
                if payload.get("pending"):
                    note = (f"连线：局面变化确认中 "
                            f"{payload.get('confirm')}/{payload.get('need')} 帧"
                            f"｜识别到 {payload.get('pieces')} 个棋子")
                elif payload.get("board"):
                    note = (f"连线扫描 {payload.get('scans')} 次｜识别到 "
                            f"{payload.get('pieces')} 个棋子｜"
                            + ("与本地棋盘一致" if payload.get("matched")
                               else "检测到局面变化")
                            + ("　已翻转" if payload.get("flipped") else ""))
                else:
                    note = (f"连线扫描 {payload.get('scans')} 次｜"
                            f"{payload.get('error') or '没认出棋盘'}")
                self._set_status(note)
            self._refresh_link_badge()
        try:
            self.after(0, update)
        except Exception:  # noqa: BLE001
            pass

    def _refresh_link_badge(self) -> None:
        linker = getattr(self.session, "linker", None)
        if linker is None or not hasattr(self, "link_badge"):
            return
        status = linker.status()
        if status["running"]:
            text = f"连线：{status['window'] or '已连接'}"
            try:                                   # 有 DPI 缩放时把系数亮出来（2K/4K 一眼可查）
                factor = float(status.get("messageScale") or 1.0)
                if abs(factor - 1.0) > 0.01:
                    text += f"　坐标×{factor:.2f}"
            except (TypeError, ValueError):
                pass
            if status.get("scans"):
                text += f"　扫描{status['scans']}次"
                if status.get("pieces"):
                    text += f"/识别{status['pieces']}子"
            if status["error"]:
                text += f"　{status['error'][:20]}"
            color = ACCENT if status["error"] else GREEN
        elif status.get("cancelled"):
            text = "连线：已取消"
            color = MUTED
        else:
            text = "连线：未连接"
            color = MUTED
        if linker.recognizer.backend != "yolo":
            text += "　[模板匹配]"
        self.link_badge.configure(text=text, fg=color)

    def on_reset_settings(self) -> None:
        """恢复出厂设置：删掉 settings.json，所有设置回到默认值（立即生效）。"""
        try:
            from ..session import settings_file

            path = settings_file()
        except Exception:  # noqa: BLE001
            path = "settings.json"
        if not messagebox.askyesno(
                "恢复出厂设置",
                "确定要恢复出厂设置吗？\n\n"
                "· 所有设置回到默认值\n"
                "· 引擎列表和开局库列表会清空\n"
                "· 配置文件会被删除：\n"
                f"　{path}\n\n"
                "不会删除引擎和棋谱文件。"):
            return
        self.session.reset_settings()
        # 界面变量也跟着回到默认，用户不用重启
        settings = self.session.settings
        if hasattr(self, "sound_var"):
            self.sound_var.set(True)
            self.sound_enabled = True
            self.sounds.enabled = True
        if hasattr(self, "cloud_learn_var"):
            self.cloud_learn_var.set(bool(settings.get("cloud_learn", True)))
        if hasattr(self, "book_use_var"):
            self.book_use_var.set(bool(settings.get("book_enabled", True)))
            self.book_max_ply_var.set(int(settings.get("book_max_ply") or 0))
        if hasattr(self, "engine_path_var"):
            self.engine_path_var.set(str(settings.get("engine_path") or ""))
        # ★ 右栏的思考时间 / 线程 / 哈希 也要跟着回默认（之前漏了，重置后仍显示 96 线程）
        self.movetime_sec_var.set(round(float(settings.get("movetime_ms") or 1500) / 1000, 1))
        self.threads_var.set(int(settings.get("threads") or 1))
        self.hash_var.set(int(settings.get("hash_mb") or 128))
        self.depth_var.set(int(settings.get("depth_limit") or 0))
        self.multipv_var.set(int(settings.get("multi_pv") or 1))
        self._apply_state(self.session.state())

    def _update_board_scrollbars(self) -> None:
        """棋盘始终铺满中间区域（大小用缩放比例控制），不需要滚动条。"""
        return

    def _sync_view_settings(self) -> None:
        """把界面显示设置应用到棋盘、状态栏与各个开关上。"""
        settings = self.state.get("settings") or {}
        arrows = bool(settings.get("show_arrows", True))
        numbers = bool(settings.get("show_numbers", True))
        statusbar = bool(settings.get("show_statusbar", True))
        piece_shadow = bool(settings.get("piece_shadow", True))
        theme = str(settings.get("board_theme") or "wood")
        try:
            scale = int(float(settings.get("board_scale") or 100))
        except (TypeError, ValueError):
            scale = 100
        for variable, value in ((self.show_arrows_var, arrows),
                                (self.show_numbers_var, numbers),
                                (self.show_statusbar_var, statusbar),
                                (self.piece_shadow_var, piece_shadow)):
            if bool(variable.get()) != bool(value):
                variable.set(value)
        try:
            if int(self.board_scale_var.get() or 0) != scale:
                self.board_scale_var.set(scale)
        except (tk.TclError, ValueError):
            self.board_scale_var.set(scale)
        for key, name in BOARD_THEME_ITEMS:
            if key == theme and self.board_theme_var.get() != name:
                self.board_theme_var.set(name)
        background = str(settings.get("background") or "")
        label = background or backgrounds_module.DEFAULT_LABEL
        if hasattr(self, "background_var") and self.background_var.get() != label:
            self.background_var.set(label)
        self.board.set_show_arrows(arrows)
        self.board.set_show_numbers(numbers)
        self.board.set_piece_shadow(piece_shadow)
        self.board.set_theme(theme)
        self.board.set_scale(scale)
        self._update_board_scrollbars()
        bar = getattr(self, "statusbar", None)
        shown = getattr(self, "_statusbar_visible", None)
        if bar is not None and bar.winfo_exists() and shown != statusbar:
            self._statusbar_visible = statusbar
            if statusbar:
                before = getattr(self, "body_frame", None)
                if before is not None and before.winfo_exists():
                    bar.pack(side="bottom", fill="x", padx=8, pady=(0, 6), before=before)
                else:
                    bar.pack(side="bottom", fill="x", padx=8, pady=(0, 6))
            else:
                bar.pack_forget()

    def _build_editor_strip(self, parent: ttk.Frame) -> None:
        """编辑局面时的棋子调色板（平时隐藏，点「编辑局面」才出现）。"""
        self.editor_strip = tk.Frame(parent, background="#efe7d6", bd=1, relief="solid")
        rows = tk.Frame(self.editor_strip, background="#efe7d6")
        rows.pack(fill="x", padx=4, pady=3)
        self.palette_buttons: Dict[str, tk.Button] = {}
        for index, piece in enumerate(PALETTE_RED + PALETTE_BLACK):
            text = GLYPHS_SIMPLE.get(piece, piece)
            color = RED_INK if piece.isupper() else BLACK_INK
            button = tk.Button(rows, text=text, width=2, font=("KaiTi", 13, "bold"),
                               fg=color, relief="raised", borderwidth=1,
                               command=lambda p=piece: self.on_palette_pick(p))
            button.pack(side="left", padx=1)
            self.palette_buttons[piece] = button
        tk.Button(rows, text="擦", width=2, font=UI_FONT, relief="raised",
                  command=lambda: self.on_palette_pick(".")).pack(side="left", padx=(6, 1))
        tools = tk.Frame(self.editor_strip, background="#efe7d6")
        tools.pack(fill="x", padx=4, pady=(0, 3))
        for text, command in (("清空棋盘", self.on_editor_clear),
                              ("初始局面", self.on_editor_start),
                              ("交换红黑", self.on_editor_swap),
                              ("开始对局", self.on_editor_apply),
                              ("取消", self.on_editor_cancel)):
            ttk.Button(tools, text=text, command=command).pack(side="left", padx=1)
        self.editor_side_var = tk.StringVar(value="w")
        for value, text in (("w", "红先"), ("b", "黑先")):
            ttk.Radiobutton(tools, text=text, value=value, variable=self.editor_side_var,
                            command=self.on_editor_side).pack(side="left", padx=(6, 0))
        self.editor_hint = ttk.Label(tools, text="", style="Muted.TLabel")
        self.editor_hint.pack(side="left", padx=8)

        fen_row = tk.Frame(self.editor_strip, background="#efe7d6")
        fen_row.pack(fill="x", padx=4, pady=(0, 4))
        ttk.Label(fen_row, text="FEN").pack(side="left")
        self.fen_text = tk.Entry(fen_row, font=MONO_FONT, relief="flat")
        self.fen_text.pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(fen_row, text="应用", width=5, command=self.on_apply_fen).pack(side="left")
        ttk.Button(fen_row, text="粘贴FEN", width=8,
                   command=self.on_editor_paste).pack(side="left", padx=2)
        ttk.Button(fen_row, text="复制FEN", width=8,
                   command=self.on_editor_copy).pack(side="left")

    # ---------------------------------------------------------------- 右栏：棋谱 + 分析
    def _build_right_panel(self, parent: ttk.Frame) -> None:
        split = ttk.PanedWindow(parent, orient="vertical")
        split.pack(fill="both", expand=True, padx=4, pady=4)
        game_frame = ttk.Frame(split, style="Panel.TFrame")
        analysis_frame = ttk.Frame(split, style="Panel.TFrame")
        split.add(game_frame, weight=3)
        split.add(analysis_frame, weight=4)
        self._build_game_panel(game_frame)
        self._build_analysis_panel(analysis_frame)

    def _build_game_panel(self, parent: ttk.Frame) -> None:
        """右栏上半：棋谱信息 + 对局列表 + 着法树 + 评注。"""
        self.game_title = ttk.Label(parent, text="未导入棋谱", style="Panel.TLabel",
                                    font=UI_FONT_BOLD, foreground=ACCENT)
        self.game_title.pack(anchor="w", padx=6, pady=(5, 0))
        self.game_meta = ttk.Label(parent, text="　", style="Muted.TLabel", justify="left")
        self.game_meta.pack(anchor="w", padx=6)

        head = ttk.Frame(parent, style="Panel.TFrame")
        head.pack(fill="x", padx=6, pady=(2, 2))
        ttk.Label(head, text="棋谱", style="Muted.TLabel").pack(side="left")
        self.games_var = tk.StringVar(value="")
        self.games_box = ttk.Combobox(head, textvariable=self.games_var, width=18,
                                      state="readonly", font=UI_FONT)
        self.games_box.bind("<<ComboboxSelected>>", self.on_game_selected)
        self.notation_var = tk.StringVar(value="cn")
        notation = ttk.Combobox(head, textvariable=self.notation_var, width=5,
                                state="readonly", font=UI_FONT,
                                values=("cn", "trad", "iccs", "wxf"))
        notation.pack(side="right")
        notation.bind("<<ComboboxSelected>>", lambda event: self.session.update_settings(
            {"notation": self.notation_var.get()}))
        self.notation_box = notation

        self.game_table_frame = ttk.Frame(parent, style="Panel.TFrame")
        columns = ("no", "red", "black", "result", "date")
        self.game_table = ttk.Treeview(self.game_table_frame, columns=columns,
                                       show="headings", height=3)
        for key, title, width in (("no", "序", 26), ("red", "红方", 66),
                                  ("black", "黑方", 66), ("result", "结果", 44),
                                  ("date", "日期", 60)):
            self.game_table.heading(key, text=title)
            self.game_table.column(key, width=width, anchor="center")
        self.game_table.pack(fill="x")
        self.game_table.bind("<<TreeviewSelect>>", self.on_game_table_select)

        tree_frame = ttk.Frame(parent, style="Panel.TFrame")
        tree_frame.pack(fill="both", expand=True, padx=6, pady=(2, 2))
        scroll = ttk.Scrollbar(tree_frame, orient="vertical")
        scroll.pack(side="right", fill="y")
        self.move_text = tk.Text(tree_frame, wrap="none", height=8, font=UI_FONT,
                                 background="#ffffff", relief="flat", highlightthickness=1,
                                 highlightbackground="#ded7c8", yscrollcommand=scroll.set,
                                 cursor="arrow")
        self.move_text.pack(side="left", fill="both", expand=True)
        scroll.config(command=self.move_text.yview)
        self.move_text.tag_configure("main", foreground="#2b2b2b")
        self.move_text.tag_configure("variation", foreground="#6b6b6b")
        self.move_text.tag_configure("current", background="#f0d493", font=UI_FONT_BOLD)
        self.move_text.tag_configure("checkmark", foreground=ACCENT)
        self.move_text.tag_configure("comment", foreground=GREEN)
        self.move_text.configure(state="disabled")
        self.move_text.bind("<Button-1>", self.on_move_list_click)
        self.move_text.bind("<Button-3>", self.on_move_list_menu)
        self.move_menu = tk.Menu(self, tearoff=0)
        self.move_menu.add_command(label="设为主线", command=self.on_promote_node)
        self.move_menu.add_command(label="删除该分支", command=self.on_delete_node)
        self.move_menu.add_command(label="复制该局面 FEN", command=self.on_copy_fen)

        comment_frame = ttk.Frame(parent, style="Panel.TFrame")
        comment_frame.pack(fill="x", padx=6, pady=(0, 5))
        self.comment_text = tk.Text(comment_frame, height=2, font=UI_FONT, wrap="word",
                                    relief="flat", highlightthickness=1,
                                    highlightbackground="#ded7c8")
        self.comment_text.pack(side="left", fill="x", expand=True)
        ttk.Button(comment_frame, text="保存评注", width=9,
                   command=self.on_save_comment).pack(side="left", padx=(4, 0))

    def _build_analysis_panel(self, parent: ttk.Frame) -> None:
        """右栏下半：引擎状态 + 评分/胜率 + 推荐着法。"""
        strip = ttk.Frame(parent, style="Panel.TFrame")
        strip.pack(fill="x", padx=6, pady=(5, 2))
        self.engine_dot = tk.Canvas(strip, width=10, height=10, highlightthickness=0,
                                    background=PANEL_BG)
        self.engine_dot.pack(side="left")
        self.engine_dot_id = self.engine_dot.create_oval(1, 1, 9, 9, fill="#b9b3a6",
                                                         outline="")
        self.engine_name = ttk.Label(strip, text="未加载引擎", style="Panel.TLabel")
        self.engine_name.pack(side="left", padx=(6, 0))
        # 程序级的搜索范围：思考时间 / 深度上限 / 推荐着法条数
        # （线程、哈希、MultiPV 这类都属于引擎自己的 UCI 选项，在「引擎设置」里设）
        controls = ttk.Frame(parent, style="Panel.TFrame")
        controls.pack(fill="x", padx=6, pady=(3, 0))
        ttk.Label(controls, text="思考", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w")
        self.movetime_spin = ttk.Spinbox(controls, from_=0.2, to=60.0, increment=0.2,
                                         width=5, justify="center",
                                         textvariable=self.movetime_sec_var,
                                         command=self.on_movetime_change)
        self.movetime_spin.grid(row=0, column=1, sticky="w", padx=(3, 1))
        ttk.Label(controls, text="秒", style="Muted.TLabel").grid(
            row=0, column=2, sticky="w", padx=(0, 8))
        ttk.Label(controls, text="线程", style="Muted.TLabel").grid(
            row=0, column=3, sticky="w")
        self.threads_spin = ttk.Spinbox(controls, from_=1, to=1024, increment=1, width=5,
                                        justify="center", textvariable=self.threads_var,
                                        command=self.on_threads_change)
        self.threads_spin.grid(row=0, column=4, sticky="w", padx=(3, 8))
        ttk.Label(controls, text="哈希", style="Muted.TLabel").grid(
            row=0, column=5, sticky="w")
        self.hash_spin = ttk.Spinbox(controls, from_=8, to=65536, increment=64, width=6,
                                     justify="center", textvariable=self.hash_var,
                                     command=self.on_hash_change)
        self.hash_spin.grid(row=0, column=6, sticky="w", padx=(3, 0))
        for widget in (self.movetime_spin, self.threads_spin, self.hash_spin):
            widget.bind("<Return>", lambda event: (
                self.on_movetime_change(), self.on_threads_change(),
                self.on_hash_change()))
            widget.bind("<FocusIn>", lambda event: setattr(self, "_spin_editing", True))
            widget.bind("<FocusOut>", lambda event: setattr(self, "_spin_editing", False))

        self.mode_tip = ttk.Label(parent, text="", style="Muted.TLabel",
                                  wraplength=310, justify="left")
        self.mode_tip.pack(anchor="w", padx=6)

        grid = ttk.Frame(parent, style="Panel.TFrame")
        grid.pack(fill="x", padx=6, pady=2)
        self.stat_labels: Dict[str, ttk.Label] = {}
        items = (("depth", "深度"), ("score", "评分"), ("judge", "判断"),
                 ("nodes", "节点"), ("nps", "速度"), ("time", "用时"))
        for index, (key, title) in enumerate(items):
            cell = ttk.Frame(grid, style="Panel.TFrame")
            cell.grid(row=index // 3, column=index % 3, sticky="w", padx=3)
            ttk.Label(cell, text=title, style="Muted.TLabel").pack(anchor="w")
            label = ttk.Label(cell, text="-", style="Panel.TLabel", font=UI_FONT_BOLD)
            label.pack(anchor="w")
            self.stat_labels[key] = label
        for column in range(3):
            grid.columnconfigure(column, weight=1)

        self.wdl_canvas = tk.Canvas(parent, height=10, background=PANEL_BG,
                                    highlightthickness=0)
        self.wdl_canvas.pack(fill="x", padx=6, pady=(2, 0))
        self.wdl_label = ttk.Label(parent, text="胜率：-", style="Muted.TLabel")
        self.wdl_label.pack(anchor="w", padx=6)
        self.chart_canvas = tk.Canvas(parent, height=54, background=CHART_BG,
                                      highlightthickness=1, highlightbackground="#e0dacd")
        self.chart_canvas.pack(fill="x", padx=6, pady=(2, 0))
        self.chart_canvas.bind("<Configure>", lambda event: self._draw_chart())
        self.wdl_chart = tk.Canvas(parent, height=44, background=CHART_BG,
                                   highlightthickness=1, highlightbackground="#e0dacd")
        self.wdl_chart.pack(fill="x", padx=6, pady=(2, 2))
        self.wdl_chart.bind("<Configure>", lambda event: self._draw_wdl_chart())

        ttk.Label(parent, text="引擎推荐着法",
                  style="Muted.TLabel").pack(anchor="w", padx=6)
        self.pv_container = ttk.Frame(parent, style="Panel.TFrame")
        self.pv_container.pack(fill="both", expand=True, padx=6, pady=(0, 5))
        self._pv_hint = ttk.Label(self.pv_container, text="", style="Muted.TLabel",
                                  wraplength=310, justify="left")
        for _ in range(5):
            row = ttk.Frame(self.pv_container, style="Panel.TFrame")
            score = tk.Label(row, text="", width=7, font=UI_FONT_BOLD, background="#eee8db",
                             foreground="#2b2b2b")
            score.pack(side="left", padx=(0, 4))
            play = ttk.Button(row, text="走这步", width=7)
            play.pack(side="right", padx=(4, 0))
            body = ttk.Frame(row, style="Panel.TFrame")
            body.pack(side="left", fill="x", expand=True)
            moves = ttk.Label(body, text="", style="Panel.TLabel", anchor="w")
            moves.pack(fill="x")
            sub = ttk.Label(body, text="", style="Muted.TLabel", anchor="w")
            sub.pack(fill="x")
            index = len(self._pv_rows)
            for widget in (row, score, moves, sub, body):
                widget.bind("<Button-1>", lambda event, i=index: self.on_pv_row_click(i))
                widget.bind("<Double-1>", lambda event, i=index: self.on_pv_row_play(i))
            self._pv_rows.append({"frame": row, "score": score, "moves": moves,
                                  "sub": sub, "play": play})

    def on_pv_row_play(self, index: int) -> None:
        """双击推荐行 = 直接走这步。"""
        lines = (self.state.get("analysis") or {}).get("lines") or []
        if index < len(lines):
            pv = lines[index].get("pvIccs") or []
            if pv:
                self.on_play_pv(str(pv[0])[:2], str(pv[0])[2:4])

    # ---------------------------------------------------------------- 设置对话框
    def _dialog(self, title: str, width: int, height: int) -> tk.Toplevel:
        window = tk.Toplevel(self)
        window.title(title)
        window.configure(background=BG)
        window.geometry(f"{width}x{height}")
        window.transient(self)
        window.resizable(True, True)
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        return window

    def on_open_engine_settings(self) -> None:
        """引擎设置对话框（平时不占用界面）。"""
        if getattr(self, "engine_dialog", None) is not None and \
                self.engine_dialog.winfo_exists():
            self.engine_dialog.lift()
            return
        window = self._dialog("引擎设置", 520, 620)
        self.engine_dialog = window
        _build_engine_settings_dialog(self, window)

    def on_open_book_settings(self) -> None:
        """开局库 / 云库设置对话框。"""
        if getattr(self, "book_dialog", None) is not None and self.book_dialog.winfo_exists():
            self.book_dialog.lift()
            return
        window = self._dialog("开局库设置", 640, 660)
        self.book_dialog = window
        _build_book_settings_dialog(self, window)

    def _render_engine_dialog(self) -> None:
        """刷新已打开的引擎设置对话框。"""
        window = getattr(self, "engine_dialog", None)
        if window is None or not window.winfo_exists():
            return
        _fill_engine_settings_dialog(self, window)

    def on_open_view_settings(self) -> None:
        """界面设置对话框（箭头 / 线路数字 / 状态栏 / 主题 / 棋盘大小）。"""
        if getattr(self, "view_dialog", None) is not None and self.view_dialog.winfo_exists():
            self.refresh_skin_list()          # 打开时重新扫一遍 skins 文件夹
            self.view_dialog.lift()
            return
        window = self._dialog("界面设置", 420, 360)
        self.view_dialog = window
        _build_view_settings_dialog(self, window)

    def _render_view_dialog(self) -> None:
        window = getattr(self, "view_dialog", None)
        if window is None or not window.winfo_exists():
            return
        _fill_view_settings_dialog(self, window)

    def _render_book_dialog(self) -> None:
        window = getattr(self, "book_dialog", None)
        if window is None or not window.winfo_exists():
            return
        _fill_book_settings_dialog(self, window)

    def on_open_link_settings(self) -> None:
        """连线设置对话框（扫描间隔 / 识别模型 / 棋盘区域 / 确认方式）。"""
        if getattr(self, "link_dialog", None) is not None and self.link_dialog.winfo_exists():
            _fill_link_settings_dialog(self, self.link_dialog)
            self.link_dialog.lift()
            return
        window = self._dialog("连线设置", 660, 560)
        self.link_dialog = window
        _build_link_settings_dialog(self, window)

    def _render_link_dialog(self) -> None:
        window = getattr(self, "link_dialog", None)
        if window is None or not window.winfo_exists():
            return
        _fill_link_settings_dialog(self, window)

    def on_choose_link_model(self) -> None:
        """选择识别模型文件（.onnx）。"""
        current = str(self.session.settings.get("link_model_path") or "")
        initial = (str(Path(current).parent) if current and Path(current).is_file()
                   else str(Path.home() / "Desktop"))
        path = filedialog.askopenfilename(
            title="选择识别模型（yolov11.onnx）", initialdir=initial,
            filetypes=[("ONNX 模型", "*.onnx"), ("所有文件", "*.*")])
        if not path:
            return
        self.session.update_settings({"link_model_path": str(Path(path))})
        self._reload_link_recognizer()
        self._set_status(f"识别模型：{Path(path).name}")

    def on_auto_link_model(self) -> None:
        """识别模型改回"自动查找"。"""
        self.session.update_settings({"link_model_path": ""})
        self._reload_link_recognizer()
        self._set_status("识别模型：自动查找（程序目录 / model 子目录里的 yolov11.onnx）")

    def on_link_orientation_change(self, _event=None) -> None:
        """切换"对方棋盘朝向"（自动 / 不翻 / 180° / 只上下 / 只左右）。"""
        label = self.link_orientation_var.get()
        value = "auto"
        for item_value, item_label in ORIENTATIONS:
            if item_label == label:
                value = item_value
                break
        self.session.update_settings({"link_orientation": value})
        self._render_link_dialog()
        self._set_status(f"对方棋盘朝向：{orientation_label(value)}"
                         + ("（按我方执红/执黑自动推断）" if value == "auto" else ""))

    def _reload_link_recognizer(self) -> None:
        """按最新设置重建识别器：换了模型或棋盘区域后马上生效。"""
        linker = getattr(self.session, "linker", None)
        if linker is None:
            return
        try:
            linker.reload_recognizer()
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"识别模型加载失败：{exc}")
        self._render_link_dialog()

    def on_link_area_change(self, key: str) -> None:
        """棋盘区域（百分比）改了 → 写进设置。"""
        variable = (getattr(self, "link_area_vars", None) or {}).get(key)
        if variable is None:
            return
        try:
            value = max(0.0, min(100.0, float(variable.get())))
        except (tk.TclError, ValueError):
            return
        self.session.update_settings({key: value})
        self._set_status("棋盘区域已更新（下一次扫描生效）")

    def on_link_area_reset(self) -> None:
        """棋盘区域恢复"整窗"（＝自动找棋盘）。"""
        patch = {"link_area_x": 0.0, "link_area_y": 0.0,
                 "link_area_w": 100.0, "link_area_h": 100.0}
        self.session.update_settings(patch)
        for key, value in patch.items():
            variable = (getattr(self, "link_area_vars", None) or {}).get(key)
            if variable is not None:
                variable.set(value)
        self._set_status("棋盘区域：整窗（自动找棋盘）")

    def on_preview_link(self) -> None:
        """「预览识别」：抓一帧对方窗口的画面跑一次识别，并把棋盘网格画出来。

        ★ 换分辨率（1080p → 2K/4K）、换平台、换屏幕缩放以后，先点这里看一眼：
        只要这张图上有蓝色网格、棋子数对，说明"看"这一半没问题，剩下的就是走子。
        截图每次都按**对方窗口现在的位置**现抓，窗口拖到哪儿就跟到哪儿。

        抓图 + 识别一共只要几百毫秒，所以直接在界面线程里做完再显示，
        不绕后台线程（后台线程回调 Tk 在个别环境下会报 "main thread is not in main loop"）。
        """
        linker = getattr(self.session, "linker", None)
        if linker is None:
            return
        if not linker.win32.hwnd:
            messagebox.showinfo("预览识别",
                                "请先点「开始连线」选中对方象棋窗口，然后再回来看这张预览。")
            return
        self._set_status("正在抓取对方窗口画面…")
        try:
            self.update_idletasks()
        except tk.TclError:
            pass
        try:
            image = linker.win32.grab()
            if image is None:
                raise RuntimeError("截图失败（对方窗口可能已经关闭）")
            linker.recognizer.region = linker._region_rect(image.width, image.height)
            board = linker.recognizer.recognize(image)
            self._show_link_preview(image, board, linker.recognizer.grid, linker)
            pieces = sum(1 for row in (board or []) for cell in row if cell != ".")
            self._set_status(f"预览：截图 {image.width}×{image.height}｜识别到 {pieces} 个棋子"
                             if board else "预览：这一帧没认出棋盘（看预览窗口里的提示）")
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"预览失败：{exc}")

    def _show_link_preview(self, image, board, grid, linker) -> None:
        """显示预览（位置按连线设置窗口现算，不写死坐标）。"""
        try:
            from PIL import ImageDraw

            try:
                from PIL import ImageTk
            except Exception:  # noqa: BLE001 - 没有 ImageTk 就退回 Tk 自带的 PNG
                ImageTk = None
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"预览不可用：{exc}")
            return

        preview = image.copy()
        if grid:
            x0, y0, cell_x, cell_y = grid
            draw = ImageDraw.Draw(preview)
            for col in range(9):
                x = int(x0 + col * cell_x)
                draw.line([(x, int(y0)), (x, int(y0 + 9 * cell_y))],
                          fill=(0, 190, 255), width=2)
            for row in range(10):
                y = int(y0 + row * cell_y)
                draw.line([(int(x0), y), (int(x0 + 8 * cell_x), y)],
                          fill=(0, 190, 255), width=2)
        limit = 900
        if preview.width > limit:
            ratio = limit / float(preview.width)
            preview = preview.resize((limit, max(1, int(preview.height * ratio))))

        window = tk.Toplevel(self)
        window.title("连线识别预览")
        try:
            if ImageTk is not None:
                photo = ImageTk.PhotoImage(preview)
            else:
                import base64
                import io

                buffer = io.BytesIO()
                preview.save(buffer, format="PNG")
                photo = tk.PhotoImage(data=base64.b64encode(buffer.getvalue()))
            holder = ttk.Label(window, image=photo)
            holder.image = photo            # 留个引用，不然会被回收变成空白
            holder.pack()
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"预览显示失败：{exc}")
            window.destroy()
            return

        pieces = 0
        if board:
            pieces = sum(1 for row in board for cell in row if cell != ".")
        lines = [f"截图 {image.width}×{image.height}｜认出棋子 {pieces} 个"
                 + (f"｜棋盘网格 {grid[2]:.1f}×{grid[3]:.1f}px" if grid else "｜没认出棋盘")]
        try:
            lines.append(_link_screen_text(self))
        except Exception:  # noqa: BLE001
            pass
        if not board:
            lines.append("提示：看清棋盘有没有被窗口挡住；也可以把「棋盘区域」缩小到只圈住棋盘再试试。")
        ttk.Label(window, text="\n".join(lines), justify="left",
                  style="Muted.TLabel").pack(anchor="w", padx=8, pady=6)
        ttk.Button(window, text="关闭", command=window.destroy).pack(pady=(0, 8))
        self._place_preview(window)

    def _place_preview(self, window) -> None:
        """把预览窗口摆在「连线设置」窗口旁边，并夹在屏幕内（每次都现算）。"""
        try:
            window.update_idletasks()
            width = max(200, window.winfo_reqwidth())
            height = max(120, window.winfo_reqheight())
            parent = getattr(self, "link_dialog", None)
            if parent is None or not parent.winfo_exists():
                parent = self
            x = parent.winfo_rootx() + (parent.winfo_width() or 320) + 10
            y = parent.winfo_rooty()
            screen_w = window.winfo_screenwidth()
            screen_h = window.winfo_screenheight()
            if x + width > screen_w:                 # 右边放不下 → 放到左边
                x = parent.winfo_rootx() - width - 10
            x = max(0, min(int(x), max(0, int(screen_w - width))))
            y = max(0, min(int(y), max(0, int(screen_h - height))))
            window.geometry(f"+{x}+{y}")
        except Exception:  # noqa: BLE001
            pass

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self, style="TFrame")
        bar.pack(side="bottom", fill="x", padx=8, pady=(0, 6))
        self.statusbar = bar
        self.status_message = ttk.Label(bar, text="就绪", foreground=ACCENT)
        self.status_message.pack(side="left")
        self.status_analysis = ttk.Label(bar, text="分析：-", foreground=MUTED)
        self.status_analysis.pack(side="right")
        self.status_engine = ttk.Label(bar, text="引擎：未加载", foreground=MUTED)
        self.status_engine.pack(side="right", padx=(0, 12))

    def _bind_shortcuts(self) -> None:
        self.bind("<Left>", lambda event: self.navigate("prev"))
        self.bind("<Right>", lambda event: self.navigate("next"))
        self.bind("<Home>", lambda event: self.navigate("first"))
        self.bind("<End>", lambda event: self.navigate("last"))
        self.bind("<space>", lambda event: self.toggle_autoplay())
        self.bind("<Control-z>", lambda event: self.on_undo())
        self.bind("<Control-y>", lambda event: self.on_redo())
        self.bind("<Control-o>", lambda event: self.on_import())
        self.bind("<Control-s>", lambda event: self.on_export())
        # 三个设置对话框的快捷键（工具栏按钮被窗口宽度挤掉时也能用）
        self.bind("<Control-i>", lambda event: self.on_open_view_settings())
        self.bind("<Control-e>", lambda event: self.on_open_engine_settings())
        self.bind("<Control-b>", lambda event: self.on_open_book_settings())
        self.bind("<F5>", lambda event: self.session.analyze_now())
        self.bind("<Escape>", lambda event: self.session.select(""))

    # ================================================================ 事件分发
    def _initial_refresh(self) -> None:
        self._apply_state(self.session.state())
        self._refresh_tree()
        self._render_move_list()
        self._render_engine()
        self._render_position()

    def _drain_events(self) -> None:
        if self._closing or not self.winfo_exists():
            return
        drained = 0
        while drained < 200:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            drained += 1
            try:
                self._handle_event(event)
            except Exception as exc:  # noqa: BLE001 单个事件出错不应拖垮界面
                print("界面事件处理异常:", repr(exc), file=sys.stderr)
        self._drain_job = self.after(60, self._drain_events)

    def _handle_event(self, event: Dict[str, object]) -> None:
        etype = event.get("type")
        if etype == "state":
            self._apply_state(event.get("state") or {})          # type: ignore[arg-type]
        elif etype == "analysis":
            self.state["analysis"] = event.get("analysis") or {}
            self._render_analysis()
            self._render_status()
        elif etype == "engine":
            self.state["engine"] = event.get("engine") or {}
            self._render_engine()
            self._render_status()
        elif etype == "cloud":
            self.state["cloud"] = event.get("cloud") or {}
            self._render_book()
        elif etype == "toast":
            self._set_status(str(event.get("text") or ""))

    def _set_status(self, text: str) -> None:
        if text:
            self.status_message.configure(text=text)

    def _apply_state(self, state: Dict[str, object]) -> None:
        if not state:
            return
        try:
            new_ply = int(state.get("ply") or 0)
        except (TypeError, ValueError):
            new_ply = 0
        old_ply = int(self.state.get("ply") or 0)
        animate = bool(state.get("lastMove")) and new_ply == old_ply + 1
        tree_changed = state.get("treeRev") != self.state.get("treeRev")
        settings_changed = state.get("settings") != self.state.get("settings")
        self.state = state
        self.board.set_state(state, animate=animate)
        if animate or (new_ply > old_ply and state.get("lastMove")):
            self._play_move_sound(new_ply, old_ply)
        if tree_changed:
            self._refresh_tree()
        self._render_move_list()
        self._render_analysis()
        self._render_book()
        self._render_engine(settings_changed=settings_changed)
        self._sync_analysis_controls()
        self._sync_view_settings()
        self._render_position()
        self._render_status()

    def _refresh_tree(self) -> None:
        self.tree_data = self.session.tree_payload()

    def _play_move_sound(self, new_ply: int, old_ply: int) -> None:
        """按着法性质播放音效：将军/绝杀 > 吃子 > 普通落子。"""
        if not self.sound_enabled:
            return
        moves = self.state.get("moves") or []
        if not moves:
            return
        last = moves[-1]
        if int(last.get("ply") or new_ply) == self._last_sound_ply:
            return
        self._last_sound_ply = int(last.get("ply") or new_ply)
        if last.get("mate") or last.get("check"):
            self.sounds.play("check")
        elif last.get("captured"):
            self.sounds.play("capture")
        else:
            self.sounds.play("move")

    # ================================================================ 棋谱面板
    def _tree_rows(self) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []

        def walk_variation(node: Dict[str, object], indent: int, first: bool) -> None:
            current: Optional[Dict[str, object]] = node
            while current:
                rows.append({"node": current, "indent": indent, "first": first})
                first = False
                children = current.get("children") or []
                for index in range(1, len(children)):
                    walk_variation(children[index], indent + 1, True)
                current = children[0] if children else None

        def walk(node: Dict[str, object], indent: int) -> None:
            children = node.get("children") or []
            if not children:
                return
            rows.append({"node": children[0], "indent": indent, "first": True})
            for index in range(1, len(children)):
                walk_variation(children[index], indent + 1, True)
            walk(children[0], indent)

        tree = getattr(self, "tree_data", None) or {}
        root = tree.get("root")
        if root:
            walk(root, 0)
        return rows

    def _render_move_list(self) -> None:
        rows = self._tree_rows()
        widget = self.move_text
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        self._line_nodes: Dict[int, int] = {}
        notation = (self.state.get("settings") or {}).get("notation", "cn")
        current_id = self.state.get("current")
        limit = 2000
        # 棋谱最前面永远有一行"初始局面（第 0 手）"，点一下就能回到开局，不用再点上一步
        root_id = int(self.state.get("rootId") or 0)
        root_tags: List[str] = ["main"]
        if int(current_id or -1) == root_id:
            root_tags.append("current")
        widget.insert("end", "  0. 初始局面", tuple(root_tags))
        widget.insert("end", "\n")
        self._line_nodes[1] = root_id
        for index, row in enumerate(rows[:limit], start=1):
            node = row["node"]
            indent = int(row["indent"])
            number = (int(node.get("ply") or 0) + 1) // 2
            is_red = node.get("mover") == "w"
            if notation == "iccs":
                label = str(node.get("iccs") or "")
            elif notation == "wxf":
                label = str(node.get("wxf") or "")
            else:
                label = str(node.get("cn") or "")
            if is_red:
                prefix = f"{number:>3}. "
            elif row["first"]:
                prefix = f"{number:>3}… "
            else:
                prefix = "     "
            tags: List[str] = ["variation" if indent else "main"]
            if int(node.get("id") or -1) == int(current_id or -1):
                tags.append("current")
            widget.insert("end", "  " * indent + prefix + label, tuple(tags))
            children = node.get("children") or []
            if len(children) > 1:
                widget.insert("end", f" 变×{len(children) - 1}", ("comment",))
            if node.get("mate"):
                widget.insert("end", " 将死", ("checkmark",))
            if node.get("comment"):
                widget.insert("end", " ✎", ("comment",))
            widget.insert("end", "\n")
            self._line_nodes[index + 1] = int(node.get("id") or 0)
        if len(rows) > limit:
            widget.insert("end", f"只显示前 {limit} 手\n", ("variation",))
        widget.configure(state="disabled")
        for line, node_id in self._line_nodes.items():
            if int(current_id or -1) == node_id:
                widget.see(f"{line}.0")
                break

    def on_move_list_click(self, event) -> None:
        index = self.move_text.index(f"@{event.x},{event.y}")
        line = int(str(index).split(".")[0])
        node_id = getattr(self, "_line_nodes", {}).get(line)
        if node_id is not None:
            self.session.navigate("node", node_id=node_id)

    def on_move_list_menu(self, event) -> None:
        index = self.move_text.index(f"@{event.x},{event.y}")
        line = int(str(index).split(".")[0])
        node_id = getattr(self, "_line_nodes", {}).get(line)
        if node_id is not None:
            self.session.navigate("node", node_id=node_id)
        try:
            self.move_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.move_menu.grab_release()

    # ================================================================ 分析面板
    def _render_analysis(self) -> None:
        analysis = self.state.get("analysis") or {}
        lines = analysis.get("lines") or []
        best = lines[0] if lines else {}
        mode_info = self.state.get("modeInfo") or {}
        if hasattr(self, "mode_tip"):
            if mode_info.get("mode") == "analyze":
                self.mode_tip.configure(text="分析中")
            elif mode_info.get("mode") == "manual" or mode_info.get("manual"):
                self.mode_tip.configure(text="")
            elif mode_info.get("engineToMove"):
                self.mode_tip.configure(text="等你走子")
            else:
                self.mode_tip.configure(text="引擎思考中")
        self.stat_labels["depth"].configure(text=str(analysis.get("depth") or "-"))
        self.stat_labels["score"].configure(text=str(best.get("scoreText") or "-"))
        self.stat_labels["judge"].configure(text=str(best.get("judgement") or "-"))
        self.stat_labels["nodes"].configure(text=_format_count(analysis.get("nodes")))
        nps = analysis.get("nps") or 0
        self.stat_labels["nps"].configure(text=_format_count(nps) + "/s" if nps else "-")
        used = analysis.get("time") or 0
        self.stat_labels["time"].configure(text=f"{used / 1000:.1f}s" if used else "-")
        # 分析由顶部的「分析模式」按钮控制（F5 也可），右栏不再放开始/停止按钮
        self._draw_wdl(best.get("wdl"))
        self._draw_chart()
        self._draw_wdl_chart()
        self._render_pv_rows(lines)

    def _draw_wdl_chart(self) -> None:
        """红方胜率随搜索深度的变化曲线。"""
        canvas = self.wdl_chart
        canvas.delete("all")
        width = max(canvas.winfo_width(), 120)
        height = max(canvas.winfo_height(), 50)
        history = (self.state.get("analysis") or {}).get("wdl_history") or []
        # 50% 基准线
        canvas.create_line(0, height / 2, width, height / 2, fill=CHART_GRID)
        if len(history) < 2:
            canvas.create_text(8, 12, anchor="w", text="等待胜率…",
                               fill="#9a9384", font=("Microsoft YaHei UI", 8))
            return
        values = [float(item[1]) for item in history]
        step = width / max(1, len(values) - 1)
        points = []
        for index, value in enumerate(values):
            points.append(index * step)
            points.append(height - (value / 100.0) * (height - 8) - 4)
        canvas.create_line(*points, fill="#c0392b", width=2)
        canvas.create_text(width - 6, 10, anchor="e", text=f"当前 {values[-1]:.1f}%",
                           fill="#8c2f22", font=("Microsoft YaHei UI", 8))
        canvas.create_text(6, height - 10, anchor="w", text="0%", fill="#b0a99b",
                           font=("Microsoft YaHei UI", 7))
        canvas.create_text(6, 8, anchor="w", text="100%", fill="#b0a99b",
                           font=("Microsoft YaHei UI", 7))

    def _render_pv_rows(self, lines: List[Dict[str, object]]) -> None:
        if not lines:
            engine = self.state.get("engine") or {}
            message = "等待分析…" if engine.get("hasEngine") else "未加载引擎"
            self._pv_hint.configure(text=message)
            self._pv_hint.pack(fill="x", pady=1)
        else:
            self._pv_hint.pack_forget()
        for index, row in enumerate(self._pv_rows):
            if index < len(lines):
                line = lines[index]
                score_text = str(line.get("scoreText") or "-")
                cp = line.get("scoreCp")
                mate = line.get("scoreMate")
                if mate is not None:
                    background, foreground = "#f2dfae", "#7a5410"
                elif cp is None:
                    background, foreground = "#eee8db", "#4a453c"
                elif int(cp) >= 0:
                    background, foreground = "#f6dedb", RED_INK
                else:
                    background, foreground = "#dfe9f5", "#1f4f86"
                row["score"].configure(text=score_text, background=background,
                                       foreground=foreground)
                pv = line.get("pvCn") or line.get("pvIccs") or []
                row["moves"].configure(text="　".join(str(item) for item in pv[:7]) or "-")
                detail = f"深度 {line.get('depth', 0)}　{_format_count(line.get('nodes'))} 节点"
                if line.get("bound"):
                    detail += "　" + ("至少" if line.get("bound") == "lowerbound" else "至多")
                row["sub"].configure(text=detail)
                pv_iccs = line.get("pvIccs") or []
                head = str(pv_iccs[0]) if pv_iccs else ""
                state = "normal" if len(head) >= 4 else "disabled"
                row["play"].configure(
                    state=state,
                    command=(lambda frm=head[0:2], to=head[2:4]: self.on_play_pv(frm, to)))
                active = index == self._pv_index
                row["frame"].configure(style="Panel.TFrame")
                row["moves"].configure(foreground=ACCENT if active else "#2b2b2b")
                row["frame"].pack(fill="x", pady=1)
            else:
                row["frame"].pack_forget()
        self._update_arrows()

    def on_pv_row_click(self, index: int) -> None:
        self._pv_index = index
        self._render_analysis()

    def on_play_pv(self, from_name: str, to_name: str) -> None:
        if len(from_name) == 2 and len(to_name) == 2:
            self.session.play_move(from_name, to_name)

    def _update_arrows(self) -> None:
        analysis = self.state.get("analysis") or {}
        lines = analysis.get("lines") or []
        if not lines:
            self.board.set_pv([])
            return
        index = min(self._pv_index, len(lines) - 1)
        line = lines[index]
        self.board.set_pv(list(line.get("pvIccs") or [])[:3])

    def _draw_wdl(self, wdl) -> None:
        canvas = self.wdl_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 120)
        height = 12
        if not wdl or len(wdl) != 3:
            canvas.create_rectangle(0, 3, width, height - 3, fill="#e6e0d4", outline="")
            self.wdl_label.configure(text="胜率：-")
            return
        total = max(1, sum(int(item) for item in wdl))
        x = 0
        for value, color in zip(wdl, ("#d4553f", "#9a9384", "#42566b")):
            span = width * int(value) / total
            canvas.create_rectangle(x, 1, x + span, height - 1, fill=color, outline="")
            x += span
        # 皮卡鱼的 WDL 是千分比，这里换算成百分数
        self.wdl_label.configure(
            text=f"胜率：红胜 {wdl[0] / 10:.1f}%　和棋 {wdl[1] / 10:.1f}%　黑胜 {wdl[2] / 10:.1f}%")

    def _draw_chart(self) -> None:
        canvas = self.chart_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 120)
        height = max(canvas.winfo_height(), 60)
        canvas.create_line(0, height / 2, width, height / 2, fill=CHART_GRID)
        history = (self.state.get("analysis") or {}).get("history") or []
        if len(history) < 2:
            canvas.create_text(8, 14, anchor="w", text="评分曲线",
                               fill="#9a9384", font=("Microsoft YaHei UI", 8))
            return
        values = [float(item[1]) for item in history]
        limit = max(150.0, max(abs(value) for value in values))
        step = width / max(1, len(values) - 1)
        points: List[float] = []
        for index, value in enumerate(values):
            points.append(index * step)
            points.append(height / 2 - (value / limit) * (height / 2 - 8))
        canvas.create_line(*points, fill=CHART_LINE, width=2, smooth=False)
        canvas.create_text(6, 8, anchor="w", text=f"+{limit:.0f}",
                           fill="#b0a99b", font=("Microsoft YaHei UI", 7))
        canvas.create_text(6, height - 6, anchor="w", text=f"-{limit:.0f}",
                           fill="#b0a99b", font=("Microsoft YaHei UI", 7))

    # ================================================================ 引擎面板
    def _render_engine(self, settings_changed: bool = True) -> None:
        engine = self.state.get("engine") or {}
        settings = self.state.get("settings") or {}
        state_name = str(engine.get("state") or "stopped")
        color = {"ready": GREEN, "thinking": "#d8a12c", "error": "#c0392b"}.get(
            state_name, "#b9b3a6")
        if getattr(self, "engine_dot", None) is not None and self.engine_dot.winfo_exists():
            self.engine_dot.itemconfigure(self.engine_dot_id, fill=color)
        if getattr(self, "engine_name", None) is not None and self.engine_name.winfo_exists():
            self.engine_name.configure(text=str(engine.get("name") or "未加载引擎"))
        if str(settings.get("notation") or "cn") != self.notation_var.get():
            self.notation_var.set(str(settings.get("notation") or "cn"))
        # 引擎声明的 UCI 选项：对话框打开时按它自动生成控件
        self._render_engine_options(engine.get("options") or [])
        # 设置对话框里的内容（只有打开时才刷新）
        self._render_engine_dialog()
        self._render_view_dialog()
        self._render_book_dialog()
        self._render_link_dialog()
        games = self.state.get("games") or []
        if len(games) > 1:
            self.games_box.configure(values=[f"{index + 1}. {label}"
                                             for index, label in enumerate(games)])
            self.games_var.set(f"{int(self.state.get('gameIndex') or 0) + 1}. "
                               f"{games[int(self.state.get('gameIndex') or 0)]}")
            self.games_box.pack(side="left", padx=(10, 0))
        else:
            self.games_box.pack_forget()
        self._render_game_table(games)

    def _render_game_table(self, games: List[str]) -> None:
        """多局棋谱的对局列表（显示红方 / 黑方 / 结果 / 日期）。"""
        if not hasattr(self, "game_table"):
            return
        if len(games) <= 1:
            self.game_table_frame.pack_forget()
            return
        meta = self.state.get("meta") or {}
        current = int(self.state.get("gameIndex") or 0)
        signature = (len(games), current)
        if getattr(self, "_game_table_signature", None) == signature:
            return
        self._game_table_signature = signature
        self.game_table.delete(*self.game_table.get_children())
        for index, label in enumerate(games):
            label = label.replace("（", "|").replace("）", "")
            parts = [part.strip() for part in label.split("|")]
            head = parts[0] if parts else label
            red, _, black = head.partition(" 对 ")
            extras = parts[1] if len(parts) > 1 else ""
            tokens = [token.strip() for token in extras.split("·")] if extras else []
            result = tokens[0] if tokens else ""
            date = tokens[-1] if len(tokens) >= 2 else ""
            self.game_table.insert("", "end", iid=str(index), values=(
                index + 1, red or "红方", black or "黑方", result or "-", date or "-"))
        if str(current) in self.game_table.get_children():
            self.game_table.selection_set(str(current))
        self.game_table_frame.pack(fill="x", padx=8, pady=(0, 4))

    def on_game_table_select(self, _event=None) -> None:
        selection = self.game_table.selection()
        if not selection:
            return
        try:
            index = int(selection[0])
        except ValueError:
            return
        if index != int(self.state.get("gameIndex") or 0):
            self.session.select_game(index)

    def _render_engine_options(self, options: List[Dict[str, object]]) -> None:
        """按引擎自己声明的 UCI 选项动态生成控件。

        选项名、类型、取值范围全部来自引擎 ``uci`` 命令的回应，界面不写死任何引擎
        或选项；换一个引擎就换成它自己的那一套。任何改动都会立刻 ``setoption``。
        """
        names = [str(option.get("name")) for option in options]
        self._engine_option_names = names
        signature = [
            (str(option.get("name")), str(option.get("type")),
             tuple(str(item) for item in (option.get("values") or [])))
            for option in options
        ]
        frame = getattr(self, "engine_options_frame", None)
        if frame is None or not frame.winfo_exists():
            self._engine_option_signature = signature
            return
        if signature == getattr(self, "_engine_option_signature", None):
            self._sync_engine_option_values()          # 选项没变，只回填取值
            return
        self._engine_option_signature = signature
        for widget in self.engine_options_frame.winfo_children():
            widget.destroy()
        self._engine_option_widgets = {}
        if not options:
            return
        for option in options:
            name = str(option.get("name"))
            kind = str(option.get("type"))
            current = self._engine_option_value(name) or str(option.get("default") or "")
            row = ttk.Frame(self.engine_options_frame, style="Panel.TFrame")
            row.pack(fill="x", pady=1)
            if kind == "check":
                variable = tk.BooleanVar(value=str(current).lower() == "true")
                make_check(
                    row, name, variable,
                    lambda n=name, v=variable: self.session.set_engine_option(
                        n, bool(v.get()))).pack(anchor="w")
                self._engine_option_widgets[name] = ("check", variable)
            elif kind == "spin":
                ttk.Label(row, text=name, style="Panel.TLabel", width=18, anchor="w").pack(
                    side="left")
                try:
                    start = int(float(current or 0))
                except (TypeError, ValueError):
                    start = int(option.get("min") or 0)
                variable = tk.IntVar(value=start)
                spin = ttk.Spinbox(row, from_=int(option.get("min") or 0),
                                   to=int(option.get("max") or 100), textvariable=variable,
                                   width=8, justify="center",
                                   command=lambda n=name, v=variable:
                                   self.session.set_engine_option(n, v.get()))
                spin.pack(side="right")
                spin.bind("<Return>", lambda event, n=name, v=variable:
                          self.session.set_engine_option(n, v.get()))
                self._engine_option_widgets[name] = ("spin", variable)
            elif kind == "combo":
                ttk.Label(row, text=name, style="Panel.TLabel", width=18, anchor="w").pack(
                    side="left")
                variable = tk.StringVar(value=str(current))
                box = ttk.Combobox(row, textvariable=variable, width=12, state="readonly",
                                   values=[str(item) for item in option.get("values") or []])
                box.pack(side="right")
                box.bind("<<ComboboxSelected>>",
                         lambda event, n=name, v=variable:
                         self.session.set_engine_option(n, v.get()))
                self._engine_option_widgets[name] = ("combo", variable)
            elif kind == "button":
                ttk.Button(row, text=name, width=18,
                           command=lambda n=name: self.session.set_engine_option(n, "")).pack(
                    anchor="w")
            else:
                ttk.Label(row, text=name, style="Panel.TLabel", width=18, anchor="w").pack(
                    side="left")
                variable = tk.StringVar(value="" if current in (None, "<empty>") else str(current))
                entry = ttk.Entry(row, textvariable=variable, width=18)
                entry.pack(side="right", fill="x", expand=True)
                entry.bind("<Return>", lambda event, n=name, v=variable:
                           self.session.set_engine_option(n, v.get()))
                entry.bind("<FocusIn>", lambda event: setattr(self, "_option_editing", True))
                entry.bind("<FocusOut>", lambda event, n=name, v=variable: (
                    setattr(self, "_option_editing", False),
                    self.session.set_engine_option(n, v.get())))
                self._engine_option_widgets[name] = ("string", variable)

    def _engine_option_value(self, name: str) -> str:
        """引擎当前生效的选项值（先看引擎反馈，再退回已保存值）。"""
        values = (self.state.get("engine") or {}).get("values") or {}
        if name in values:
            return str(values.get(name))
        settings = self.state.get("settings") or {}
        by_engine = settings.get("engine_options_by_engine") or {}
        path = str((self.state.get("engine") or {}).get("path") or "")
        saved = by_engine.get(path) or {}
        if name in saved:
            return str(saved.get(name))
        return ""

    def _sync_engine_option_values(self) -> None:
        """把引擎的当前取值回填到各控件（正在输入的那个不打断）。"""
        values = (self.state.get("engine") or {}).get("values") or {}
        if not values:
            return
        editing = bool(getattr(self, "_option_editing", False))
        for name, entry in list(getattr(self, "_engine_option_widgets", {}).items()):
            if not entry or name not in values:
                continue
            kind, variable = entry
            text = str(values.get(name))
            try:
                if kind == "check":
                    want = text.lower() == "true"
                    if bool(variable.get()) != want:
                        variable.set(want)
                elif kind == "spin":
                    want = int(float(text or 0))
                    if int(variable.get() or 0) != want:
                        variable.set(want)
                elif kind == "combo":
                    if str(variable.get()) != text:
                        variable.set(text)
                elif kind == "string" and not editing:
                    shown = "" if text == "<empty>" else text
                    if str(variable.get()) != shown:
                        variable.set(shown)
            except (tk.TclError, TypeError, ValueError):
                continue

    # ================================================================ 局面面板
    def _render_position(self) -> None:
        state = self.state
        self._render_game_head()
        # 棋盘下方的一行局面信息
        material = state.get("material") or {"red": 0, "black": 0}
        repetition = int(state.get("repetition") or 1)
        parts = [f"{state.get('sideName') or '-'}走棋"]
        if state.get("resultName"):
            parts.append(str(state["resultName"]))
        elif state.get("check"):
            parts.append("被将军")
        parts.append(f"第 {state.get('ply', 0)} 手")
        parts.append(f"子力 红 {material.get('red', 0) / 2:.0f} : 黑 {material.get('black', 0) / 2:.0f}")
        if repetition > 1:
            parts.append(f"重复第 {repetition} 次")
        if hasattr(self, "board_info"):
            self.board_info.configure(text="　·　".join(parts))
        if hasattr(self, "fen_text") and self.focus_get() is not self.fen_text:
            fen = str(state.get("fen") or "")
            if self._fen_text_value() != fen:
                self._set_fen_text(fen)

    # ------------------------------------------------------------ FEN 输入框
    def _fen_text_value(self) -> str:
        widget = getattr(self, "fen_text", None)
        if widget is None or not widget.winfo_exists():
            return ""
        try:
            if isinstance(widget, tk.Text):
                return widget.get("1.0", "end").strip()
            return str(widget.get()).strip()
        except Exception:  # noqa: BLE001
            return ""

    def _set_fen_text(self, value: str) -> None:
        widget = getattr(self, "fen_text", None)
        if widget is None or not widget.winfo_exists():
            return
        try:
            if isinstance(widget, tk.Text):
                widget.delete("1.0", "end")
                widget.insert("1.0", value)
            else:
                widget.delete(0, "end")
                widget.insert(0, value)
        except Exception:  # noqa: BLE001
            pass

    def _render_game_head(self) -> None:
        meta = self.state.get("meta") or {}
        red = str(meta.get("Red") or "")
        black = str(meta.get("Black") or "")
        if red or black:
            title = f"{red or '红方'} 对 {black or '黑方'}"
        elif meta.get("Title") or meta.get("Event"):
            title = str(meta.get("Title") or meta.get("Event"))
        elif self.state.get("moveCount"):
            title = "当前棋谱"
        else:
            title = "未导入棋谱"
        self.game_title.configure(text=title)
        parts = []
        for key, label in (("Event", "赛事"), ("Site", "地点"), ("Date", "日期"),
                           ("Result", "结果"), ("Type", "类型"), ("Round", "轮次")):
            value = meta.get(key)
            if value:
                parts.append(f"{label}：{value}")
        moves = int(self.state.get("ply") or 0)
        parts.append(f"当前第 {moves} 手")
        self.game_meta.configure(text="　".join(parts) if parts else "　")

    def _render_status(self) -> None:
        state = self.state
        engine = state.get("engine") or {}
        analysis = state.get("analysis") or {}
        self._render_mode_buttons()
        self.status_engine.configure(
            text="引擎：" + (str(engine.get("name") or "") if engine.get("hasEngine")
                           else ("已加载未运行" if engine.get("path") else "未加载")))
        lines = analysis.get("lines") or []
        if lines:
            best = lines[0]
            self.status_analysis.configure(
                text=f"分析：深 {analysis.get('depth', 0)}　{best.get('scoreText')}　{best.get('judgement')}")
        else:
            self.status_analysis.configure(text="分析：-")
        comment = str(state.get("comment") or "")
        if hasattr(self, "comment_text") and self.focus_get() is not self.comment_text:
            if self.comment_text.get("1.0", "end").strip() != comment.strip():
                self.comment_text.delete("1.0", "end")
                self.comment_text.insert("1.0", comment)

    # ================================================================ 棋盘交互
    def on_board_click(self, square: str) -> None:
        if not square:
            return
        if self.editor_active:
            self.editor.click_square(square)
            self._render_editor()
            return
        if not self.session.can_human_move():
            self._set_status("引擎走棋中…")
            return
        selected = self.state.get("selected")
        destinations = self.state.get("destinations") or []
        piece = self.board.piece_at(square)
        side = self.state.get("side")
        if selected and square in destinations:
            self.session.play_move(str(selected), square)
            return
        if piece != "." and (piece.isupper() == (side == "w")):
            if selected != square:
                self.session.select(square)
            return
        if selected:
            self.session.select("")

    def on_board_move(self, source: str, target: str) -> None:
        if not self.editor_active and not self.session.can_human_move():
            self._set_status("现在是引擎走棋，不能替引擎落子")
            self.board.redraw()
            return
        destinations = self.state.get("destinations") or []
        selected = self.state.get("selected")
        if selected == source and target in destinations:
            self.session.play_move(source, target)
            return
        # 拖拽时可能还没选中该子，先选中再走
        piece = self.board.piece_at(source)
        side = self.state.get("side")
        if piece != "." and (piece.isupper() == (side == "w")):
            board = self.session.tree.board_at()
            move = board.find_move(source, target)
            if move is not None:
                self.session.play_move(source, target)
                return
        self.board.redraw()

    def on_board_right_click(self, square: Optional[str] = None, event=None) -> None:
        """右键棋盘：编辑局面时删掉该格的棋子，平时弹出快捷菜单。"""
        if self.editor_active:
            if square:
                self.editor.erase(square)
                self._render_editor()
                self._set_status(f"已清掉 {square} 上的棋子")
            return
        self.session.select("")
        self.show_board_menu(event)

    def show_board_menu(self, event=None) -> None:
        """棋盘右键快捷菜单：思考时间 / 深度 / 模式 / 局面与图片操作。"""
        menu = tk.Menu(self, tearoff=0)
        settings = self.state.get("settings") or {}
        mode_info = self.state.get("modeInfo") or {}
        current_mode = str(mode_info.get("mode") or "manual")

        time_menu = tk.Menu(menu, tearoff=0)
        current_ms = int(settings.get("movetime_ms") or 1500)
        for seconds in (0.5, 1, 2, 3, 5, 8, 15):
            value = int(seconds * 1000)
            label = f"{seconds:g} 秒"
            time_menu.add_radiobutton(
                label=label, value=value, variable=self._menu_movetime_var,
                command=lambda v=value: self.on_menu_movetime(v))
        time_menu.add_separator()
        depth_menu = tk.Menu(time_menu, tearoff=0)
        for depth in (0, 6, 8, 10, 12, 16, 20, 24):
            depth_menu.add_radiobutton(
                label="不限" if depth == 0 else f"{depth} 层", value=depth,
                variable=self._menu_depth_var,
                command=lambda d=depth: self.on_menu_depth(d))
        time_menu.add_cascade(label="搜索深度", menu=depth_menu)
        menu.add_cascade(label=f"思考时间 {current_ms / 1000:g} 秒", menu=time_menu)

        mode_menu = tk.Menu(menu, tearoff=0)
        for value, title, _sub in ENGINE_MODES:
            mode_menu.add_radiobutton(
                label=title, value=value, variable=self.mode_var,
                command=lambda v=value: self.on_engine_mode(v))
        mode_menu.add_separator()
        mode_menu.add_radiobutton(label="纯手动打谱", value="",
                                  variable=self.mode_var,
                                  command=lambda: self.on_engine_mode(""))
        mode_menu.entryconfigure(
            {"red": 0, "black": 1, "analyze": 2}.get(current_mode, 3), state="disabled")
        menu.add_cascade(label="对局模式", menu=mode_menu)

        menu.add_separator()
        menu.add_command(label="编辑局面", command=self.on_edit_position)
        menu.add_command(label="复制 FEN", command=self.on_copy_fen)
        menu.add_command(label="粘贴 FEN", command=self.on_paste_fen)
        menu.add_command(label="翻转棋盘", command=self.on_flip)
        menu.add_separator()
        menu.add_command(label="复制局面图片", command=self.on_copy_position_image)
        menu.add_command(label="粘贴局面图片", command=self.on_paste_position_image)
        menu.add_command(label="从图片文件导入局面…", command=self.on_import_position_image)
        menu.add_command(label="导出局面图片…", command=self.on_export_position_image)

        if self._menu_movetime_var.get() != current_ms:
            self._menu_movetime_var.set(current_ms)
        try:
            self._menu_depth_var.set(int(settings.get("depth_limit") or 0))
        except (tk.TclError, ValueError):
            self._menu_depth_var.set(0)
        x = getattr(event, "x_root", None)
        y = getattr(event, "y_root", None)
        if x is None or y is None:
            x = self.board.winfo_rootx() + 40
            y = self.board.winfo_rooty() + 40
        try:
            menu.tk_popup(int(x), int(y))
        finally:
            menu.grab_release()

    def on_menu_movetime(self, value: int) -> None:
        self.movetime_sec_var.set(round(int(value) / 1000, 1))
        self.on_movetime_change()
        self._set_status(f"思考时间：{int(value) / 1000:g} 秒")

    def on_menu_depth(self, depth: int) -> None:
        self.depth_var.set(int(depth))
        self.on_depth_change()
        self._set_status("深度：不限" if not depth else f"深度：{depth} 层")

    # ------------------------------------------------------------ 局面图片
    def _board_rows_for_image(self) -> List[str]:
        """当前局面的棋盘行（10 行，FEN 写法），翻转视角时按屏幕方向输出。"""
        rows = (self.state.get("board") or [])
        if not rows:
            return ["9"] * 10
        rows = [str(row) for row in rows]
        if bool((self.state.get("settings") or {}).get("flip")):
            rows = [row[::-1] for row in rows[::-1]]
        return rows

    def _render_board_image(self):
        """把当前局面画成图片（需要 Pillow；没装时会抛异常，由调用方提示）。"""
        from . import board_image

        settings = self.state.get("settings") or {}
        return board_image.render_position(
            self._board_rows_for_image(), str(self.state.get("side") or "w"),
            cell=72, theme=str(settings.get("board_theme") or "wood"),
            show_numbers=bool(settings.get("show_numbers", True)))

    def on_copy_position_image(self) -> None:
        """把当前局面画成图片放进剪贴板。"""
        from . import board_image

        if not board_image.available():
            messagebox.showwarning("复制局面图片",
                                   f"这个功能需要 Pillow：{board_image.unavailable_reason()}")
            return
        try:
            image = self._render_board_image()
            self._copy_image_to_clipboard(image)
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning("复制局面图片", f"复制失败：{exc}")
            return
        self._set_status("局面图片已复制到剪贴板")

    def _copy_image_to_clipboard(self, image) -> None:
        """把 PIL 图片放进 Windows 剪贴板（做成 BMP 再交给系统）。"""
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "position.bmp"
            image.convert("RGB").save(path, "BMP")
            data = path.read_bytes()
        self.clipboard_clear()
        self.clipboard_append(data.decode("latin-1"))   # 用 BMP 原始字节占位
        self.update()
        # Tk 的剪贴板不能直接放图片，这里用 Win32 API 放 CF_DIB
        try:
            import ctypes

            dib = data[14:]                              # 去掉 BMP 文件头
            ctypes.windll.user32.OpenClipboard(0)
            ctypes.windll.user32.EmptyClipboard()
            ctypes.windll.kernel32.GlobalAlloc.restype = ctypes.c_void_p
            handle = ctypes.windll.kernel32.GlobalAlloc(0x0002, len(dib))
            pointer = ctypes.windll.kernel32.GlobalLock(ctypes.c_void_p(handle))
            ctypes.memmove(pointer, dib, len(dib))
            ctypes.windll.kernel32.GlobalUnlock(ctypes.c_void_p(handle))
            ctypes.windll.user32.SetClipboardData(8, ctypes.c_void_p(handle))
            ctypes.windll.user32.CloseClipboard()
        except Exception:  # noqa: BLE001
            pass

    def on_export_position_image(self) -> None:
        """把当前局面导出成 PNG 图片。"""
        from . import board_image

        if not board_image.available():
            messagebox.showwarning("导出局面图片",
                                   f"这个功能需要 Pillow：{board_image.unavailable_reason()}")
            return
        path = filedialog.asksaveasfilename(
            title="导出局面图片", defaultextension=".png", initialfile="局面.png",
            filetypes=[("PNG 图片", "*.png"), ("JPEG 图片", "*.jpg")])
        if not path:
            return
        try:
            image = self._render_board_image()
            image.save(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning("导出局面图片", f"保存失败：{exc}")
            return
        self._set_status(f"局面图片已保存：{Path(path).name}")

    def on_import_position_image(self) -> None:
        """从图片文件导入局面（自动识别）。"""
        path = filedialog.askopenfilename(
            title="从图片导入局面",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"),
                       ("所有文件", "*.*")])
        if not path:
            return
        self.import_position_image_file(path)

    def on_paste_position_image(self) -> None:
        """粘贴剪贴板里的图片并识别成局面。"""
        from . import board_image

        if not board_image.available():
            messagebox.showwarning("粘贴局面图片",
                                   f"这个功能需要 Pillow：{board_image.unavailable_reason()}")
            return
        from PIL import ImageGrab

        try:
            data = ImageGrab.grabclipboard()
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning("粘贴局面图片", f"读不到剪贴板图片：{exc}")
            return
        if hasattr(data, "save") and not isinstance(data, list):
            self.apply_recognized_image(data)
            return
        if isinstance(data, list) and data:
            self.import_position_image_file(str(data[0]))
            return
        messagebox.showinfo("粘贴局面图片",
                            "剪贴板里没有图片。\n可以先用画图 / 截图工具复制棋盘再试。")

    def import_position_image_file(self, path: str) -> None:
        """从图片文件识别局面。"""
        from . import board_image

        if not board_image.available():
            messagebox.showwarning("图片导入局面",
                                   f"这个功能需要 Pillow：{board_image.unavailable_reason()}")
            return
        from PIL import Image

        try:
            image = Image.open(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning("图片导入局面", f"打不开这张图片：{exc}")
            return
        self.apply_recognized_image(image)

    def apply_recognized_image(self, image) -> None:
        """识别图片里的局面，确认后应用到编辑局面里。"""
        from . import board_image

        self._set_status("正在识别图片里的局面…")
        try:
            result = board_image.recognize_position(image)
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning("图片识别失败", str(exc))
            return
        if not result.get("ok"):
            messagebox.showinfo("图片识别", str(result.get("message") or "识别失败"))
            return
        board = str(result.get("board") or "")
        side = "w" if str(self.editor.side) != "b" else "b"
        fen = f"{board} {side} - - 0 1"
        unknown = result.get("unknown") or []
        detail = ""
        if unknown:
            detail = f"\n\n有 {len(unknown)} 个位置没认准（已留空）：{'、'.join(unknown[:8])}" \
                     f"{'…' if len(unknown) > 8 else ''}"
        if not messagebox.askyesno(
                "图片识别结果",
                f"识别出的局面：\n{board}\n\n{str(self.state.get('sideName') or '红')}先"
                f"{detail}\n\n要用这个局面吗？"):
            return
        if not self.session.set_fen(fen):
            messagebox.showwarning("图片识别", "识别结果不是合法局面，已放弃。")
            return
        self.on_edit_position()          # 打开编辑局面，方便手动修一修
        self._set_status("已按图片识别出局面" + ("，有不确定的位置" if unknown else ""))

    def _enable_file_drop(self) -> None:
        """支持把图片文件拖到窗口上（Windows：WM_DROPFILES）。"""
        import os

        # 默认开启（把图片文件拖到窗口上就能导入局面）。
        # 极少数情况下如果拖拽影响了鼠标操作，可以设 XQ_DISABLE_DROP=1 关掉它。
        if sys.platform != "win32" or os.environ.get("XQ_DISABLE_DROP") == "1":
            self._drop_callback = None
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32
            hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
            shell32.DragAcceptFiles(wintypes.HWND(hwnd), True)
            if ctypes.sizeof(ctypes.c_void_p) == 8:
                user32.GetWindowLongPtrW.restype = ctypes.c_void_p
                user32.SetWindowLongPtrW.restype = ctypes.c_void_p
                get_proc = user32.GetWindowLongPtrW
                set_proc = user32.SetWindowLongPtrW
            else:
                get_proc, set_proc = user32.GetWindowLongW, user32.SetWindowLongW
            GWLP_WNDPROC = -4
            old_proc = ctypes.c_void_p(get_proc(wintypes.HWND(hwnd), GWLP_WNDPROC))
            user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                               ctypes.c_uint, ctypes.c_void_p,
                                               ctypes.c_void_p]
            user32.CallWindowProcW.restype = ctypes.c_ssize_t
            prototype = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
                                           ctypes.c_void_p, ctypes.c_void_p)

            def handler(window_handle, message, wparam, lparam):
                if message == 0x0233:                     # WM_DROPFILES
                    try:
                        count = shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)
                        files = []
                        for index in range(count):
                            length = shell32.DragQueryFileW(wparam, index, None, 0)
                            buffer = ctypes.create_unicode_buffer(length + 1)
                            shell32.DragQueryFileW(wparam, index, buffer, length + 1)
                            files.append(buffer.value)
                        if files:
                            self.after(0, lambda: self.on_files_dropped(files))
                    except Exception:  # noqa: BLE001
                        pass
                    shell32.DragFinish(wparam)
                    return 0
                try:
                    return user32.CallWindowProcW(old_proc, window_handle, message,
                                                  wparam, lparam)
                except Exception:  # noqa: BLE001
                    return 0

            self._drop_callback = prototype(handler)
            set_proc.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
            set_proc.restype = ctypes.c_void_p
            set_proc(wintypes.HWND(hwnd), GWLP_WNDPROC,
                     ctypes.cast(self._drop_callback, ctypes.c_void_p))
        except Exception:  # noqa: BLE001
            self._drop_callback = None

    def on_files_dropped(self, files: List[str]) -> None:
        """把拖进来的文件用起来：图片当局面识别，棋谱当棋谱导入。"""
        if not files:
            return
        path = Path(files[0])
        suffix = path.suffix.lower()
        if suffix in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"):
            self.import_position_image_file(str(path))
        elif suffix in (".pgn", ".xqf"):
            try:
                data = path.read_bytes()
            except OSError as exc:
                self._set_status(f"读不了这个文件：{exc}")
                return
            kind = "xqf" if suffix == ".xqf" else "pgn"
            self.session.import_data(kind, data, name=path.name)
        else:
            self._set_status(f"不认识的文件类型：{suffix or path.name}")

    # ================================================================ 工具栏动作
    def on_new(self) -> None:
        self.session.new_game()

    def on_import(self) -> None:
        path = filedialog.askopenfilename(
            title="导入棋谱",
            filetypes=[("象棋棋谱", "*.xqf *.pgn"), ("XQF 棋谱", "*.xqf"),
                       ("PGN 棋谱", "*.pgn")])
        if not path:
            return
        suffix = Path(path).suffix.lower()
        if suffix not in (".xqf", ".pgn"):
            messagebox.showwarning(
                "格式不支持",
                f"棋谱只支持两种格式：XQF 与 PGN。\n当前选择的是「{suffix or '无后缀'}」文件。")
            return
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            messagebox.showerror("导入失败", f"无法读取文件：{exc}")
            return
        kind = "xqf" if suffix == ".xqf" else "pgn"
        self.session.import_data(kind, data, name=Path(path).name)

    def on_export(self) -> None:
        notation = str((self.state.get("settings") or {}).get("notation") or "iccs")
        text = self.session.export_text(notation)
        default_name = getattr(self.session, "export_filename", "棋谱.pgn")
        path = filedialog.asksaveasfilename(title="导出 PGN", defaultextension=".pgn",
                                            initialfile=default_name,
                                            filetypes=[("PGN 棋谱", "*.pgn")])
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8-sig")
        except OSError as exc:
            messagebox.showerror("导出失败", f"无法写入文件：{exc}")
            return
        self._set_status(f"已导出到 {path}")

    def on_export_xqf(self) -> None:
        default_name = getattr(self.session, "export_filename", "棋谱.pgn").replace(".pgn", ".xqf")
        path = filedialog.asksaveasfilename(title="保存 XQF 棋谱", defaultextension=".xqf",
                                            initialfile=default_name,
                                            filetypes=[("XQF 棋谱", "*.xqf")])
        if not path:
            return
        if self.session.save_xqf(path):
            self._set_status(f"已保存到 {path}")

    def on_save_game(self) -> None:
        """工具栏「保存棋谱」：按所选后缀保存为 PGN 或 XQF。"""
        notation = str((self.state.get("settings") or {}).get("notation") or "iccs")
        default = getattr(self.session, "export_filename", "棋谱.pgn")
        path = filedialog.asksaveasfilename(
            title="保存棋谱", defaultextension=".pgn", initialfile=default,
            filetypes=[("PGN 棋谱", "*.pgn"), ("XQF 棋谱", "*.xqf")])
        if not path:
            return
        suffix = Path(path).suffix.lower()
        if suffix == ".xqf":
            if self.session.save_xqf(path):
                self._set_status(f"已保存 XQF：{Path(path).name}")
            return
        if suffix not in (".pgn", ""):
            messagebox.showwarning("格式不支持",
                                   "棋谱只支持保存为 PGN 或 XQF 两种格式。")
            return
        try:
            Path(path).write_text(self.session.export_text(notation), encoding="utf-8-sig")
        except OSError as exc:
            messagebox.showerror("保存失败", f"无法写入文件：{exc}")
            return
        self._set_status(f"已保存 PGN：{Path(path).name}")

    def on_copy_fen(self) -> None:
        fen = str(self.state.get("fen") or "")
        self.clipboard_clear()
        self.clipboard_append(fen)
        self._set_status("FEN 已复制到剪贴板")

    # ---------------------------------------------------------------- 棋谱/局面 复制粘贴
    @staticmethod
    def _chinese_moves(fen: str, line: List[str]) -> List[str]:
        """把一串 ICCS 着法（如 h2e2）转成中文记谱，从 ``fen`` 开始摆。"""
        from .. import notation as nt
        from ..board import Board

        board = Board(fen)
        out: List[str] = []
        for iccs in line:
            move = board.find_move_iccs(str(iccs))
            if move is None:
                out.append(str(iccs))
                continue
            out.append(nt.to_chinese(board, move))
            board.push(move)
        return out

    def on_copy_game(self) -> None:
        """复制棋谱：把当前棋谱的所有着法（中文记谱）复制到剪贴板。"""
        tree = self.session.tree
        try:
            lines = tree.all_lines()
        except Exception:  # noqa: BLE001
            lines = []
        if not lines:
            messagebox.showinfo("复制棋谱", "当前还没有着法可以复制。")
            return
        meta = self.state.get("meta") or {}
        parts = [f"{APP_BASE_NAME} · 棋谱"]
        info = []
        if meta.get("Red"):
            info.append(f"红方：{meta['Red']}")
        if meta.get("Black"):
            info.append(f"黑方：{meta['Black']}")
        result = str(self.state.get("resultName") or meta.get("Result") or "").strip()
        if result:
            info.append(f"结果：{result}")
        if meta.get("Date"):
            info.append(f"日期：{meta['Date']}")
        if info:
            parts.append("　".join(info))
        parts.append("")
        main = self._chinese_moves(tree.start_fen, lines[0])
        for index in range(0, len(main), 2):
            number = index // 2 + 1
            parts.append(f"{number}. " + "　".join(main[index:index + 2]))
        if len(lines) > 1:                       # 有变例就附在后面
            parts.append("")
            parts.append(f"变例（{len(lines) - 1} 条）：")
            for line in lines[1:]:
                parts.append("　" + "　".join(self._chinese_moves(tree.start_fen, line)))
        text = "\n".join(parts)
        self.clipboard_clear()
        self.clipboard_append(text)
        note = f"棋谱已复制到剪贴板（主线 {len(main)} 手"
        if len(lines) > 1:
            note += f"，变例 {len(lines) - 1} 条"
        self._set_status(note + "）")

    @staticmethod
    def _looks_like_fen(text: str) -> bool:
        """粗略判断一段文本是不是 FEN 局面（棋盘 10 行 + 行棋方）。"""
        head = (text or "").strip().splitlines()[0].strip() if text.strip() else ""
        fields = head.split()
        if len(fields) < 2 or fields[0].count("/") != 9:
            return False
        if fields[1] not in ("w", "b", "r"):
            return False
        return all(ch in "rnbakcpRNBAKCP123456789/" for ch in fields[0])

    def on_paste_smart(self) -> None:
        """粘贴局面/棋谱：剪贴板里是什么就按什么处理。

        * 图片 → 识别成局面（和「粘贴局面图片」一样，确认后进编辑局面）
        * FEN 文本 → 直接摆成这个局面
        * 棋谱文本（PGN / 中文着法 / 坐标着法）→ 当作棋谱导入
        """
        from . import board_image

        data = None
        if board_image.available():                  # 先看有没有图片
            try:
                from PIL import ImageGrab

                data = ImageGrab.grabclipboard()
            except Exception:  # noqa: BLE001
                data = None
        if hasattr(data, "save") and not isinstance(data, list):
            self.apply_recognized_image(data)
            return
        if isinstance(data, list) and data:
            self.import_position_image_file(str(data[0]))
            return
        try:
            text = (self.clipboard_get() or "").strip()
        except Exception:  # noqa: BLE001
            text = ""
        if not text:
            messagebox.showinfo("粘贴局面/棋谱",
                                "剪贴板里没有图片，也没有文字。\n"
                                "可以复制的棋盘截图、FEN 局面代码，或者一段棋谱。")
            return
        if self._looks_like_fen(text):
            self._set_fen_text(text.splitlines()[0].strip())
            if self.session.set_fen(text.splitlines()[0].strip()):
                self._set_status("已粘贴局面（FEN）")
            else:
                messagebox.showwarning("粘贴局面/棋谱", "剪贴板里的 FEN 不合法。")
            return
        if self.session.import_data("pgn", text.encode("utf-8"), name="剪贴板棋谱"):
            self._set_status("已导入剪贴板里的棋谱")
            return
        messagebox.showinfo("粘贴局面/棋谱",
                            "剪贴板里的内容既不是局面（FEN / 图片），也不像棋谱。")

    def on_paste_fen(self) -> None:
        try:
            text = self.clipboard_get().strip()
        except Exception:  # noqa: BLE001
            text = ""
        if not text:
            messagebox.showinfo("粘贴 FEN", "剪贴板里没有内容。")
            return
        self._set_fen_text(text)
        if self.session.set_fen(text):
            self._set_status("已从剪贴板导入局面")
        else:
            self._set_status("剪贴板里的 FEN 不合法")

    # ---------------------------------------------------------------- 局面编辑
    def on_edit_position(self) -> None:
        if self.editor_active:
            self.on_editor_cancel()
            return
        fen = str(self.state.get("fen") or "")
        if fen and not self.editor.load_fen(fen):
            self.editor.set_start()
        self.editor_active = True
        if hasattr(self, "editor_side_var"):
            self.editor_side_var.set(self.editor.side)
        self.session.select("")
        self.session.stop_analysis()
        self.board.set_edit_mode(True)
        if hasattr(self, "editor_strip"):
            self.editor_strip.pack(side="top", fill="x", pady=(3, 0), before=None)
        self._render_editor()
        self._set_status("编辑局面：选一个棋子点棋盘落子，点「擦」清除；摆好后点「开始对局」")

    def on_editor_cancel(self) -> None:
        if not self.editor_active:
            return
        self.editor_active = False
        self.board.set_edit_mode(False)
        if hasattr(self, "editor_strip"):
            self.editor_strip.pack_forget()
        self._apply_state(self.session.state())
        self._refresh_tree()
        self._set_status("已取消编辑，回到当前棋局")

    def on_palette_pick(self, piece: str) -> None:
        self.editor.select(piece)
        for key, button in getattr(self, "palette_buttons", {}).items():
            button.configure(relief="sunken" if key == piece else "raised")

    def on_editor_clear(self) -> None:
        self.editor.clear()
        self._render_editor()

    def on_editor_start(self) -> None:
        self.editor.set_start()
        self.editor_side_var.set(self.editor.side)
        self._render_editor()

    def on_editor_swap(self) -> None:
        self.editor.swap_colors()
        self.editor_side_var.set(self.editor.side)
        self._render_editor()

    def on_editor_side(self) -> None:
        self.editor.set_side(self.editor_side_var.get())
        self._render_editor()

    def on_editor_copy(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.editor.to_fen())
        self._set_status("编辑中的局面 FEN 已复制")

    def on_editor_paste(self) -> None:
        try:
            text = self.clipboard_get().strip()
        except Exception:  # noqa: BLE001
            text = ""
        if not text:
            self._set_status("剪贴板里没有 FEN")
            return
        if self.editor.load_fen(text):
            self.editor_side_var.set(self.editor.side)
            self._render_editor()
            self._set_status("已粘贴局面，可继续摆子")
        else:
            self._set_status("剪贴板内容不是合法的 FEN")

    def on_editor_apply(self) -> None:
        problems = self.editor.validate()
        if problems:
            messagebox.showwarning("局面还不完整", "请先修正：\n\n" + "\n".join(
                f"· {item}" for item in problems[:6]))
            self._set_status("局面不合法：" + problems[0])
            return
        fen = self.editor.to_fen()
        self.editor_active = False
        self.board.set_edit_mode(False)
        if hasattr(self, "editor_strip"):
            self.editor_strip.pack_forget()
        if self.session.set_fen(fen):
            self._set_status("已按编辑的局面开始对局")
        else:
            self._set_status("这个局面无法使用")

    def _render_editor(self) -> None:
        settings = self.state.get("settings") or {}
        self.board.set_state(self.editor.state_for_view(settings), animate=False)
        problems = self.editor.validate()
        counts = self.editor.counts()
        red = sum(value for key, value in counts.items() if key.isupper())
        black = sum(value for key, value in counts.items() if key.islower())
        text = (f"红 {red} · 黑 {black} · 先行"
                f"{'红' if self.editor.side == 'w' else '黑'}")
        if problems:
            text += "　" + problems[0]
        if hasattr(self, "editor_hint"):
            self.editor_hint.configure(text=text)

    def on_toggle_sound(self) -> None:
        self.sound_enabled = bool(self.sound_var.get())
        self.sounds.enabled = self.sound_enabled
        self.session.update_settings({"sound_enabled": self.sound_enabled})   # 记到配置文件
        if self.sound_enabled:
            self.sounds.play("move")
            self._set_status("音效已打开")
        else:
            self._set_status("音效已关闭")

    # ---------------------------------------------------------------- 开局库
    def on_toggle_book_use(self) -> None:
        """「启用库招」开关：打开后轮到引擎时优先走库里的着法。"""
        self.session.set_book_use_in_game(self.book_use_var.get())
        self._set_status("已启用库招" if self.book_use_var.get() else "已关闭库招")

    def on_toggle_cloud_learn(self) -> None:
        """云库自动学习开关：开了才会返回全部招法、分值与胜率。"""
        enabled = bool(self.cloud_learn_var.get())
        self.session.set_cloud_learn(enabled)
        self._set_status("云库自动学习：已开启" if enabled
                         else "云库自动学习：已关闭")

    def on_book_strategy_change(self, _event=None) -> None:
        label = self.book_strategy_var.get()
        for key, text in BOOK_STRATEGY_ITEMS:
            if text == label:
                self.session.set_book_strategy(key)
                self._set_status(f"库招策略：{text}")
                return

    def on_book_max_ply_change(self) -> None:
        try:
            value = max(0, min(60, int(float(self.book_max_ply_var.get()))))
        except (tk.TclError, ValueError):
            value = 0
        self.book_max_ply_var.set(value)
        self.session.set_book_max_ply(value)

    def on_load_book(self) -> None:
        path = filedialog.askopenfilename(
            title="加载开局库",
            filetypes=[("开局库", "*.pfbook *.obk *.xqf"),
                       ("PFBOOK 开局库", "*.pfbook"), ("OBK 开局库", "*.obk"),
                       ("XQF 棋谱库", "*.xqf")])
        if not path:
            return
        if self.session.load_book(path):
            self._set_status(f"已加载开局库：{Path(path).name}")
        else:
            messagebox.showwarning(
                "无法加载开局库",
                (self.session.book_status or "加载失败") +
                "\n\n开局库只允许加载三种格式：\n"
                "· PFBOOK\n"
                "· OBK\n"
                "· XQF\n\n"
                "其它格式一律不支持；"
                "棋谱请用顶栏「导入棋谱」，只支持 XQF 与 PGN。")

    def on_build_book(self) -> None:
        count = self.session.build_book_from_current()
        self._set_status(f"已用当前棋谱生成开局库：{count} 个局面")

    def _selected_book_path(self) -> str:
        """开局库设置对话框里选中的库文件路径。"""
        table = getattr(self, "book_priority_table", None)
        if table is None or not table.winfo_exists():
            return ""
        selection = table.selection()
        if not selection:
            rows = table.get_children()
            if not rows:
                return ""
            selection = (rows[0],)
        values = table.item(selection[0], "values")
        books = ((self.state.get("book") or {}).get("books")) or []
        try:
            index = int(str(values[0])) - 1
        except (IndexError, ValueError):
            return ""
        if 0 <= index < len(books):
            return str(books[index].get("path") or "")
        return ""

    def on_book_priority(self, delta: int) -> None:
        """把选中的库上移／下移（delta<0 更优先）。"""
        path = self._selected_book_path()
        if not path:
            messagebox.showinfo("开局库优先级", "先选择一个开局库（列表里点一行）。")
            return
        if not self.session.move_book(path, delta):
            self._set_status("已经到头了")

    def on_book_remove(self) -> None:
        path = self._selected_book_path()
        if not path:
            messagebox.showinfo("移除开局库", "先选择一个开局库（列表里点一行）。")
            return
        self.session.remove_book(path)
        self._set_status(f"已移除开局库：{Path(path).name}")

    def on_browse_book(self) -> None:
        """浏览库内容：直接列出库里的记录（着法 / 分值 / 胜率和），便于确认库是否可用。"""
        rows = self.session.book.book_rows_preview(300)
        window = self._dialog("开局库内容", 620, 460)
        frame = ttk.Frame(window, style="Panel.TFrame")
        frame.pack(fill="both", expand=True, padx=10, pady=8)
        info = self.session.book.describe()
        ttk.Label(frame, text=info, style="Muted.TLabel", wraplength=580,
                  justify="left").pack(anchor="w")
        if not rows:
            ttk.Label(frame, text="这个库没有可浏览的记录。",
                      style="Muted.TLabel").pack(anchor="w", pady=8)
        else:
            table_frame = ttk.Frame(frame, style="Panel.TFrame")
            table_frame.pack(fill="both", expand=True, pady=(6, 0))
            scroll = ttk.Scrollbar(table_frame, orient="vertical")
            scroll.pack(side="right", fill="y")
            columns = ("move", "score", "count", "rate", "wdl", "key", "book")
            table = ttk.Treeview(table_frame, columns=columns, show="headings", height=14,
                                 yscrollcommand=scroll.set)
            for key, title, width in (("move", "着法", 70), ("score", "分值", 60),
                                      ("count", "次数", 50), ("rate", "胜率", 60),
                                      ("wdl", "胜/和/负", 80), ("key", "局面键", 150),
                                      ("book", "库文件", 110)):
                table.heading(key, text=title)
                table.column(key, width=width, anchor="center")
            table.pack(side="left", fill="both", expand=True)
            scroll.config(command=table.yview)
            for row in rows:
                table.insert("", "end", values=(
                    row.get("move"), row.get("score") if row.get("score") is not None else "-",
                    row.get("count"), "-" if row.get("winRate") is None
                    else f"{row['winRate']}%",
                    f"{row.get('wins')}/{row.get('draws')}/{row.get('losses')}",
                    row.get("key"), row.get("book")))
            ttk.Label(frame, text=f"共 {len(rows)} 条",
                      style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
        ttk.Button(frame, text="关闭", command=window.destroy).pack(anchor="e", pady=(6, 0))

    def on_export_obk(self) -> None:
        path = filedialog.asksaveasfilename(title="导出 OBK 开局库", defaultextension=".obk",
                                            initialfile="我的开局库.obk",
                                            filetypes=[("OBK 开局库", "*.obk")])
        if not path:
            return
        if self.session.save_book_as_obk(path):
            self._set_status(f"已导出：{Path(path).name}")

    def on_book_row_play(self, _event=None) -> None:
        selection = self.book_table.selection()
        if not selection:
            return
        index = self.book_table.index(selection[0])
        if 0 <= index < len(self._book_hits):
            move = str(self._book_hits[index].get("move") or "")
            if len(move) == 4:
                self.session.play_move(move[:2], move[2:])

    def on_cloud_row_play(self, _event=None) -> None:
        selection = self.cloud_table.selection()
        if not selection:
            return
        index = self.cloud_table.index(selection[0])
        if 0 <= index < len(self._cloud_hits):
            move = str(self._cloud_hits[index].get("move") or "")
            if len(move) == 4:
                self.session.play_move(move[:2], move[2:])

    def _render_book(self) -> None:
        book = (self.state.get("book") or {})
        hits = book.get("hits") or []
        cloud = (self.state.get("cloud") or {})
        cloud_hits = cloud.get("hits") or []
        merged = list(hits)
        if getattr(self, "book_merge_var", None) is not None and self.book_merge_var.get():
            existing = {str(item.get("move")) for item in merged}
            for item in cloud_hits:
                if str(item.get("move")) in existing:
                    continue
                merged.append({"move": item.get("move"), "cn": item.get("cn"),
                               "count": None, "winRate": item.get("winrate"),
                               "source": "云库", "score": item.get("score")})
        self._book_hits = merged
        if not hasattr(self, "book_table"):
            return
        books = book.get("books") or []
        status = str(book.get("status") or "未加载开局库")
        if len(books) > 1:
            status = f"共 {len(books)} 个库：\n" + "、".join(
                str(item.get("label")) for item in books)
        tips = [f"策略：{BOOK_STRATEGIES.get(str(book.get('strategy')), '')}"]
        max_ply = int(book.get("maxPly") or 0)
        tips.append(f"脱谱 {max_ply} 手" if max_ply else "脱谱不限")
        if any(item.get("offBook") for item in merged):
            tips.append(f"按库内容显示 {len(merged)} 条")
        if not book.get("inBook", True):
            tips.append("已脱谱（不用库招）")
        elif not book.get("useInGame", True):
            tips.append("库招已关闭")
        self.book_info.configure(text=status + "\n" + "　".join(tips),
                                 foreground=ACCENT if not book.get("loaded") else MUTED)
        if self.book_use_var.get() != bool(book.get("useInGame", True)):
            self.book_use_var.set(bool(book.get("useInGame", True)))
        self._sync_book_strategy_box(str(book.get("strategy") or ""))
        try:
            if int(self.book_max_ply_var.get() or 0) != max_ply:
                self.book_max_ply_var.set(max_ply)
        except (tk.TclError, ValueError):
            self.book_max_ply_var.set(max_ply)
        self.book_table.delete(*self.book_table.get_children())
        for hit in merged:
            rate = hit.get("winRate")
            source = str(hit.get("source") or "")
            cn = str(hit.get("cn") or "")
            if not cn and hit.get("offBook"):
                cn = "—"
            name = str(hit.get("book") or "")
            if name and name not in source and "云库" not in source:
                source = f"{source}·{name}" if source else name
            self.book_table.insert("", "end", values=(
                str(hit.get("move") or ""),
                cn,
                "-" if hit.get("count") is None else str(hit.get("count")),
                "-" if rate is None else f"{rate:.1f}%",
                # 0 分是正常分值（均势着法），只有"没有分值"才显示 "-"
                "-" if hit.get("score") is None else f"{float(hit['score']):.0f}",
                source))
        self._render_cloud(cloud)

    def _sync_book_strategy_box(self, key: str) -> None:
        """把策略下拉框跟后台设置对齐（只在需要时改，避免打断选择）。"""
        for item_key, label in BOOK_STRATEGY_ITEMS:
            if item_key == key and self.book_strategy_var.get() != label:
                self.book_strategy_var.set(label)
                return

    def _render_cloud(self, cloud: Dict[str, object]) -> None:
        """云库面板：状态、开关与云库着法表。"""
        if not hasattr(self, "cloud_table"):
            return
        if self.cloud_enable_var.get() != bool(cloud.get("enabled", True)):
            self.cloud_enable_var.set(bool(cloud.get("enabled", True)))
        if self.cloud_use_var.get() != bool(cloud.get("useForMove", True)):
            self.cloud_use_var.set(bool(cloud.get("useForMove", True)))
        if self.cloud_learn_var.get() != bool(cloud.get("learn", False)):
            self.cloud_learn_var.set(bool(cloud.get("learn", False)))
        status = str(cloud.get("statusText") or "尚未查询")
        extra = []
        if cloud.get("cached"):
            extra.append("缓存")
        if cloud.get("elapsed"):
            extra.append(f"{float(cloud['elapsed']):.2f}s")
        if cloud.get("message"):
            extra.append(str(cloud["message"]))
        text = "云库：" + status + ("　" + "　".join(extra) if extra else "")
        if cloud.get("best"):
            text += f"\n云库最佳着法：{cloud.get('best')} {cloud.get('bestCn') or ''}"
        self.cloud_status.configure(
            text=text, foreground=MUTED if cloud.get("status") == "ok" else ACCENT)
        hits = cloud.get("hits") or []
        self._cloud_hits = list(hits)
        self.cloud_table.delete(*self.cloud_table.get_children())
        for hit in hits:
            score = hit.get("score")
            rate = hit.get("winrate")
            self.cloud_table.insert("", "end", values=(
                str(hit.get("move") or ""),
                str(hit.get("cn") or ""),
                "未收录" if hit.get("unknown") else ("" if score is None else score),
                "-" if rate is None else f"{float(rate):.1f}%",
                "" if hit.get("rank") is None else hit.get("rank"),
                str(hit.get("note") or "")))

    def on_flip(self) -> None:
        self.session.flip_board()

    def on_about(self) -> None:
        engine = self.state.get("engine") or {}
        engine_text = str(engine.get("name") or "未加载")
        from .. import __version__ as core_version
        messagebox.showinfo(
            f"关于 {APP_NAME}",
            f"{APP_BASE_NAME}　版本 {APP_VERSION}（{APP_QQ}）\n"
            f"内核版本 {core_version}\n\n"
            f"当前引擎：{engine_text}\n"
            "引擎协议：UCI\n")

    def on_mode_change(self) -> None:
        self.session.update_settings({"mode": self.mode_var.get()})

    def on_engine_mode(self, engine_mode: str) -> None:
        """切换模式；再次点击当前模式即取消，回到纯手动打谱。"""
        self.session.set_engine_mode(engine_mode)

    def _render_mode_buttons(self) -> None:
        """按当前模式给三个按钮上色（都不选中＝纯手动打谱）。"""
        info = (self.state.get("modeInfo") or {})
        current = str(info.get("mode") or "")
        if current == "manual":
            current = ""
        for value, title, _subtitle in ENGINE_MODES:
            button = self.mode_buttons.get(value)
            if button is None:
                continue
            key = MODE_ICONS.get(value, "")
            if value == current:
                self._icon_selected.add(key)
            else:
                self._icon_selected.discard(key)
            selected = value == current
            button.configure(background=ACCENT if selected else TOOLBAR_BG,
                             foreground="#ffffff" if selected else BUTTON_FG,
                             relief="sunken" if selected else "raised", borderwidth=1)
            if getattr(self, "use_icons", False) and key in self.icon_buttons:
                photo = self.icons.get(self._icon_now.get(key, key), "normal")
                if photo is not None:
                    button.configure(image=photo)
                    button._icon_photo = photo
        badge_text = str(info.get("name") or "纯手动打谱")
        if info.get("engineToMove"):
            badge_text += "　⏳ 引擎思考中…"
        elif info.get("autoMove"):
            badge_text += "　轮到你走"
        elif info.get("mode") == "analyze":
            badge_text += "　持续分析中"
        self.mode_badge.configure(text=badge_text)
        if self.mode_var.get() != current:
            self.mode_var.set(current)

    def on_game_selected(self, _event=None) -> None:
        value = self.games_var.get()
        try:
            index = int(value.split(".", 1)[0]) - 1
        except (ValueError, IndexError):
            return
        self.session.select_game(index)

    def navigate(self, target: str) -> None:
        self.session.navigate(target)

    def toggle_autoplay(self) -> None:
        if self._autoplay:
            self._stop_autoplay()
        else:
            self._start_autoplay()

    def _start_autoplay(self) -> None:
        self._autoplay = True
        if self.autoplay_button is not None:
            self._set_play_icon("stop")
        self._autoplay_tick()

    def _stop_autoplay(self) -> None:
        self._autoplay = False
        if self.autoplay_button is not None:
            self._set_play_icon("play")
        if self._autoplay_job is not None:
            try:
                self.after_cancel(self._autoplay_job)
            except Exception:  # noqa: BLE001
                pass
            self._autoplay_job = None

    def _set_play_icon(self, icon: str) -> None:
        """把"播放"按钮的图案换成 播放 / 停止（没有图标时退回文字 ▶ / ■）。"""
        button = self.autoplay_button
        if button is None:
            return
        if getattr(self, "use_icons", False):
            self._icon_now["play"] = icon
            photo = self.icons.get(icon, "normal")
            if photo is not None:
                button.configure(image=photo)
                button._icon_photo = photo
        else:
            button.configure(text="■" if icon == "stop" else "▶")

    def _autoplay_tick(self) -> None:
        if not self._autoplay:
            return
        before = int(self.state.get("ply") or 0)
        self.session.navigate("next")
        after = int(self.session.tree.current.ply)
        if after == before:
            self._stop_autoplay()
            return
        interval = max(0.3, float(self.speed_var.get()))
        self._autoplay_job = self.after(int(interval * 1000), self._autoplay_tick)

    def on_undo(self) -> None:
        self.session.undo()

    def on_redo(self) -> None:
        self.session.redo()

    def on_delete_node(self) -> None:
        node_id = int(self.state.get("current") or 0)
        if node_id == 0:
            messagebox.showinfo("删除分支", "已经在开局，没有可删除的分支。")
            return
        self.session.delete_node(node_id)

    def on_promote_node(self) -> None:
        node_id = int(self.state.get("current") or 0)
        if node_id == 0:
            return
        self.session.promote_node(node_id)

    def on_save_comment(self) -> None:
        text = self.comment_text.get("1.0", "end").strip()
        self.session.set_comment(int(self.state.get("current") or 0), text)
        self._set_status("评注已保存")

    def on_analyze_toggle(self) -> None:
        analysis = self.state.get("analysis") or {}
        if analysis.get("running"):
            self.session.stop_analysis()
        else:
            self.session.analyze_now()

    # ================================================================ 引擎动作
    def on_choose_engine(self) -> None:
        path = filedialog.askopenfilename(
            title="选择引擎",
            filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")])
        if path:
            self.engine_path_var.set(path)
            self.session.load_engine(path)

    def on_autodetect_engine(self) -> None:
        found = self.session.autodetect_engine()
        if not found:
            messagebox.showinfo("自动查找",
                                "没有在常见位置找到皮卡鱼引擎。\n"
                                "请点「浏览…」手动选择 pikafish.exe。")
            return
        self.engine_path_var.set(found[0])
        if messagebox.askyesno("自动查找",
                               f"找到引擎：\n{self.session.engine_label(found[0])}\n\n现在加载吗？"):
            self.session.load_engine(found[0])

    def on_load_engine(self) -> None:
        path = self.engine_path_var.get().strip().strip('"')
        if not path:
            self.on_choose_engine()
            return
        self.session.load_engine(path)

    def on_unload_engine(self) -> None:
        self.session.unload_engine()

    def on_movetime_change(self) -> None:
        """每步思考时间（秒）。这是程序发给引擎的搜索时限，不是引擎选项。"""
        try:
            seconds = max(0.2, min(60.0, float(self.movetime_sec_var.get())))
        except (tk.TclError, ValueError):
            seconds = 1.5
        self.movetime_sec_var.set(round(seconds, 1))
        self.session.update_settings({"movetime_ms": int(round(seconds * 1000))})

    def on_depth_change(self) -> None:
        """深度上限（0＝不限）。同样是程序侧的搜索限制。"""
        try:
            depth = max(0, min(200, int(float(self.depth_var.get()))))
        except (tk.TclError, ValueError):
            depth = 0
        self.depth_var.set(depth)
        self.session.update_settings({"depth_limit": depth})

    def on_multipv_change(self) -> None:
        """推荐着法条数：同时写回引擎自己的 MultiPV 选项。"""
        try:
            count = max(1, min(12, int(float(self.multipv_var.get()))))
        except (tk.TclError, ValueError):
            count = 3
        self.multipv_var.set(count)
        self.session.update_settings({"multi_pv": count})
        if self.session.engine.option("MultiPV") is not None:
            self.session.set_engine_option("MultiPV", count)

    def on_threads_change(self) -> None:
        """线程数：直接下发给引擎的 Threads 选项，设多少就是多少。"""
        try:
            count = max(1, min(1024, int(float(self.threads_var.get()))))
        except (tk.TclError, ValueError):
            count = max(1, os.cpu_count() or 4)
        self.threads_var.set(count)
        self.session.update_settings({"threads": count})
        if self.session.engine.option("Threads") is not None:
            self.session.set_engine_option("Threads", count)
            self._set_status(f"引擎线程数：{count}")
        else:
            self._set_status(f"线程数已记下：{count}（加载引擎后生效）")

    def on_hash_change(self) -> None:
        """哈希表大小（MB）：同样直接下发给引擎的 Hash 选项。"""
        try:
            size = max(8, min(65536, int(float(self.hash_var.get()))))
        except (tk.TclError, ValueError):
            size = 128
        self.hash_var.set(size)
        self.session.update_settings({"hash_mb": size})
        if self.session.engine.option("Hash") is not None:
            self.session.set_engine_option("Hash", size)
            self._set_status(f"引擎哈希表：{size} MB")
        else:
            self._set_status(f"哈希表已记下：{size} MB（加载引擎后生效）")

    def on_refresh_engine_options(self) -> None:
        """重新向引擎询问它支持哪些 UCI 选项（不同引擎各不相同）。"""
        self.session.refresh_engine_options()

    def _selected_engine_path(self) -> str:
        """引擎列表里选中的那一项（没选就取第一项）。"""
        box = getattr(self, "engine_list", None)
        if box is None or not box.winfo_exists():
            return ""
        selection = box.curselection()
        index = int(selection[0]) if selection else 0
        known = (self.state.get("engine") or {}).get("known") or []
        if 0 <= index < len(known):
            return str(known[index].get("path") or "")
        return ""

    def on_engine_switch(self) -> None:
        """切换到列表里选中的引擎。"""
        path = self._selected_engine_path()
        if not path:
            messagebox.showinfo("切换引擎", "先在下拉列表里选一个引擎（或先添加引擎）。")
            return
        current = self.session.engine_tag(
            str((self.state.get("engine") or {}).get("path") or ""))
        if current and current == self.session.engine_tag(path):
            self._set_status("已经在用这个引擎了")
            return
        if self.session.switch_engine(path):
            self.engine_path_var.set(self.session.resolve_engine_tag(path) or path)
        else:
            messagebox.showwarning("切换引擎",
                                   f"这个引擎加载失败：\n{self.session.engine_label(path)}")

    def on_engine_remove(self) -> None:
        """从列表里移除选中的引擎。"""
        path = self._selected_engine_path()
        if not path:
            return
        label = self.session.engine_label(path)
        if messagebox.askyesno("移除引擎", f"从列表里移除这个引擎吗？\n{label}"):
            self.session.remove_engine(path)

    def _sync_analysis_controls(self) -> None:
        """把握手器的搜索范围与后台设置保持同步（手打时不打断输入）。"""
        settings = self.state.get("settings") or {}
        if self._spin_editing:
            return
        movetime = int(settings.get("movetime_ms") or 1500)
        try:
            stale = abs(float(self.movetime_sec_var.get()) - movetime / 1000) > 0.01
        except (tk.TclError, ValueError):
            stale = True
        if stale:
            self.movetime_sec_var.set(round(movetime / 1000, 1))
        try:
            depth = int(settings.get("depth_limit") or 0)
            if int(self.depth_var.get() or 0) != depth:
                self.depth_var.set(depth)
            multipv = int(settings.get("multi_pv") or 3)
            if int(self.multipv_var.get() or 0) != multipv:
                self.multipv_var.set(multipv)
            # 线程 / 哈希也跟设置同步（恢复出厂设置后立刻回到默认值 1 / 128）
            threads = int(settings.get("threads") or 1)
            if int(self.threads_var.get() or 0) != threads:
                self.threads_var.set(threads)
            hash_mb = int(settings.get("hash_mb") or 128)
            if int(self.hash_var.get() or 0) != hash_mb:
                self.hash_var.set(hash_mb)
        except (tk.TclError, ValueError):
            pass

    # ================================================================ 局面动作
    def on_apply_fen(self) -> None:
        fen = self._fen_text_value()
        if not fen:
            return
        if not self.session.set_fen(fen):
            self._set_status("FEN 不合法，未应用")
        else:
            self._set_status("已应用新局面")

    # ================================================================ 关闭
    def on_close(self) -> None:
        self._closing = True
        try:
            self.session.save_settings_file()      # 关窗口时把设置存好
        except Exception:  # noqa: BLE001
            pass
        self._stop_autoplay()
        for job in list(getattr(self, "_debounce_jobs", {}).values()):
            try:
                self.after_cancel(job)
            except Exception:  # noqa: BLE001
                pass
        self._debounce_jobs = {}
        if self._drain_job is not None:
            try:
                self.after_cancel(self._drain_job)
            except Exception:  # noqa: BLE001
                pass
            self._drain_job = None
        try:
            self.board._cancel_anim_job()
        except Exception:  # noqa: BLE001
            pass
        self._dispose_tk_variables()
        try:
            self.session.engine.stop_process()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()

    def _dispose_tk_variables(self) -> None:
        """在销毁窗口前显式释放 Tk 变量，避免解释器退出时报 Tcl 残留错误。"""
        for name, value in list(vars(self).items()):
            if isinstance(value, tk.Variable):
                try:
                    value.__del__()
                except Exception:  # noqa: BLE001
                    pass
                setattr(self, name, None)


# ==================================================================== 设置对话框
def _dialog_label(parent, text: str, font=None):
    from tkinter import ttk

    return ttk.Label(parent, text=text, style="Panel.TLabel", font=font or UI_FONT)


def _build_engine_settings_dialog(app, window) -> None:
    """引擎设置对话框：只放「引擎路径」和该引擎自己声明的 UCI 选项（动态生成）。

    选项名 / 类型 / 取值范围完全来自引擎 ``uci`` 命令的回应（spin / check / combo /
    string / button 五种都能处理），界面不写死任何引擎或选项：皮卡鱼显示皮卡鱼那一套，
    换成别的引擎就换成那一套。任何修改都立即 ``setoption`` 生效，并按引擎分别记住。
    """
    import tkinter as tk
    from tkinter import ttk

    frame = ttk.Frame(window, style="Panel.TFrame")
    frame.pack(fill="both", expand=True, padx=12, pady=10)

    ttk.Label(frame, text="引擎路径", style="Muted.TLabel").pack(anchor="w")
    path_row = ttk.Frame(frame, style="Panel.TFrame")
    path_row.pack(fill="x", pady=(2, 2))
    app.engine_path_widget = ttk.Entry(path_row, textvariable=app.engine_path_var,
                                       font=UI_FONT)
    app.engine_path_widget.pack(side="left", fill="x", expand=True)
    app.engine_path_widget.bind("<FocusIn>",
                                lambda event: setattr(app, "_engine_path_focus", True))
    app.engine_path_widget.bind("<FocusOut>",
                                lambda event: setattr(app, "_engine_path_focus", False))
    ttk.Button(path_row, text="浏览…", width=8, command=app.on_choose_engine).pack(
        side="left", padx=3)
    buttons = ttk.Frame(frame, style="Panel.TFrame")
    buttons.pack(fill="x", pady=(0, 2))
    ttk.Button(buttons, text="自动查找", command=app.on_autodetect_engine).pack(side="left")
    ttk.Button(buttons, text="加载引擎", style="Accent.TButton",
               command=app.on_load_engine).pack(side="left", padx=4)
    ttk.Button(buttons, text="卸载", command=app.on_unload_engine).pack(side="left")
    ttk.Button(buttons, text="重新探测选项", command=app.on_refresh_engine_options).pack(
        side="left", padx=4)
    app.engine_hint = ttk.Label(frame, text="", style="Muted.TLabel", wraplength=520,
                                justify="left")
    app.engine_hint.pack(anchor="w", pady=(2, 4))

    # 多引擎管理：添加过的引擎都在这里，随时切换 / 移除
    ttk.Label(frame, text="已添加的引擎", style="Muted.TLabel").pack(anchor="w")
    list_frame = ttk.Frame(frame, style="Panel.TFrame")
    list_frame.pack(fill="x", pady=(2, 2))
    scroll = ttk.Scrollbar(list_frame, orient="vertical")
    scroll.pack(side="right", fill="y")
    app.engine_list = tk.Listbox(list_frame, height=3, font=UI_FONT, activestyle="none",
                                 selectmode="browse", yscrollcommand=scroll.set,
                                 highlightthickness=1, highlightbackground="#ded7c8")
    app.engine_list.pack(side="left", fill="x", expand=True)
    scroll.config(command=app.engine_list.yview)
    app.engine_list.bind("<Double-1>", lambda event: app.on_engine_switch())
    engine_buttons = ttk.Frame(frame, style="Panel.TFrame")
    engine_buttons.pack(fill="x", pady=(0, 4))
    ttk.Button(engine_buttons, text="切换到这个引擎",
               command=app.on_engine_switch).pack(side="left")
    ttk.Button(engine_buttons, text="移除",
               command=app.on_engine_remove).pack(side="left", padx=4)
    ttk.Button(engine_buttons, text="添加别的引擎…",
               command=app.on_choose_engine).pack(side="left")

    head = ttk.Frame(frame, style="Panel.TFrame")
    head.pack(fill="x")
    ttk.Label(head, text="引擎支持的 UCI 选项", style="Panel.TLabel",
              font=UI_FONT_BOLD).pack(side="left")
    app.engine_option_count = ttk.Label(head, text="", style="Muted.TLabel")
    app.engine_option_count.pack(side="right")

    wrapper = ttk.Frame(frame, style="Panel.TFrame")
    wrapper.pack(fill="both", expand=True, pady=(2, 4))
    canvas = tk.Canvas(wrapper, background=PANEL_BG, highlightthickness=0)
    scroll = ttk.Scrollbar(wrapper, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    app.engine_options_frame = ttk.Frame(canvas, style="Panel.TFrame")
    app._engine_option_signature = None        # 新框架，控件必须重新生成
    app._engine_option_widgets = {}
    canvas.create_window((0, 0), window=app.engine_options_frame, anchor="nw")
    app.engine_options_frame.bind(
        "<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<MouseWheel>",
                lambda event: canvas.yview_scroll(int(-event.delta / 120), "units"))

    ttk.Button(frame, text="关闭", command=window.destroy).pack(anchor="e", pady=(4, 0))
    _fill_engine_settings_dialog(app, window)


def _fill_engine_settings_dialog(app, window) -> None:
    """刷新引擎设置对话框：路径、提示与动态生成的 UCI 选项列表。"""
    if not window.winfo_exists():
        return
    engine = app.state.get("engine") or {}
    settings = app.state.get("settings") or {}
    path = str(engine.get("path") or settings.get("engine_path") or "")
    if path:
        # 配置里内置引擎存的是 "@builtin/xxx" 标记，这里显示成本次运行的真实路径
        shown = app.session.resolve_engine_tag(path) or path
        if not app._engine_path_focus and app.engine_path_var.get() != shown:
            app.engine_path_var.set(shown)
    if engine.get("error"):
        hint = str(engine.get("error"))
    elif engine.get("path"):
        builtin = "　内置" if app.session.is_bundled_engine_path(
            str(engine.get("path"))) else ""
        hint = (f"{engine.get('name') or ''}".strip()
                + ("　运行中" if engine.get("hasEngine") else "　未运行")
                + builtin)
    else:
        hint = "未加载引擎"
    app.engine_hint.configure(text=hint,
                              foreground=ACCENT if engine.get("error") else MUTED)
    options = engine.get("options") or []
    app.engine_option_count.configure(
        text=f"共 {len(options)} 项" if options else "未探测到选项")
    box = getattr(app, "engine_list", None)
    if box is not None and box.winfo_exists():
        known = engine.get("known") or []
        signature = [str(item.get("path")) for item in known]
        if signature != getattr(app, "_engine_list_signature", None):
            app._engine_list_signature = signature
            box.delete(0, "end")
            current = app.session.engine_tag(str(engine.get("path") or ""))
            for item in known:
                using = "　使用中" if str(item.get("path")) == current else ""
                box.insert("end", f"{item.get('name') or ''}{using}".strip())
            if known:
                box.selection_clear(0, "end")
                box.selection_set(0)
    app._render_engine_options(options)


def _build_view_settings_dialog(app, window) -> None:
    """界面设置对话框：开关、棋盘主题、棋盘大小。"""
    import tkinter as tk
    from tkinter import ttk

    frame = ttk.Frame(window, style="Panel.TFrame")
    frame.pack(fill="both", expand=True, padx=12, pady=10)

    ttk.Label(frame, text="显示开关", style="Panel.TLabel",
              font=UI_FONT_BOLD).pack(anchor="w")
    make_check(frame, "棋步提示箭头", app.show_arrows_var,
               lambda: app.on_view_toggle("show_arrows")).pack(anchor="w")
    make_check(frame, "棋盘线路数字", app.show_numbers_var,
               lambda: app.on_view_toggle("show_numbers")).pack(anchor="w")
    make_check(frame, "棋子阴影", app.piece_shadow_var,
               lambda: app.on_view_toggle("piece_shadow")).pack(anchor="w")
    make_check(frame, "底部状态栏", app.show_statusbar_var,
               lambda: app.on_view_toggle("show_statusbar")).pack(anchor="w")
    make_check(frame, "音效", app.sound_var,
               lambda: app.on_view_toggle("sound")).pack(anchor="w")

    ttk.Separator(frame).pack(fill="x", pady=6)
    ttk.Label(frame, text="棋盘外观", style="Panel.TLabel",
              font=UI_FONT_BOLD).pack(anchor="w")
    skin_row = ttk.Frame(frame, style="Panel.TFrame")
    skin_row.pack(fill="x", pady=(2, 2))
    ttk.Label(skin_row, text="棋子皮肤", style="Muted.TLabel").pack(side="left")
    app.skin_label = ttk.Label(skin_row, text="自带画法", style="Panel.TLabel")
    app.skin_label.pack(side="left", padx=(4, 6))
    ttk.Button(skin_row, text="选择文件夹…", width=11,
               command=app.on_choose_skin).pack(side="left")
    ttk.Button(skin_row, text="自动查找", width=9,
               command=app.on_autodetect_skin).pack(side="left", padx=3)
    ttk.Button(skin_row, text="恢复自带", width=9,
               command=lambda: app.session.update_settings({"board_skin": ""})).pack(
        side="left")
    # 皮肤列表：自动扫描 exe 旁边的 skins 文件夹，点一下就能换
    list_row = ttk.Frame(frame, style="Panel.TFrame")
    list_row.pack(fill="x", pady=(2, 2))
    ttk.Label(list_row, text="皮肤列表", style="Muted.TLabel").pack(side="left")
    app.skin_box = ttk.Combobox(list_row, width=22, state="readonly",
                                textvariable=app.skin_choice_var)
    app.skin_box.pack(side="left", padx=(4, 4))
    app.skin_box.bind("<<ComboboxSelected>>", lambda event: app.on_skin_pick())
    ttk.Button(list_row, text="刷新", width=6,
               command=app.refresh_skin_list).pack(side="left", padx=(0, 4))
    app.skin_count_label = ttk.Label(list_row, text="", style="Muted.TLabel")
    app.skin_count_label.pack(side="left")
    theme_row = ttk.Frame(frame, style="Panel.TFrame")
    theme_row.pack(fill="x", pady=(2, 2))
    ttk.Label(theme_row, text="棋盘主题", style="Muted.TLabel").pack(side="left")
    theme_box = ttk.Combobox(theme_row, width=16, state="readonly",
                             textvariable=app.board_theme_var,
                             values=[name for _key, name in BOARD_THEME_ITEMS])
    theme_box.pack(side="left", padx=(6, 0))
    theme_box.bind("<<ComboboxSelected>>", app.on_board_theme_change)

    background_row = ttk.Frame(frame, style="Panel.TFrame")
    background_row.pack(fill="x", pady=(2, 2))
    ttk.Label(background_row, text="桌面背景", style="Muted.TLabel").pack(side="left")
    app.background_box = ttk.Combobox(background_row, width=16, state="readonly",
                                      textvariable=app.background_var,
                                      values=backgrounds_module.choices())
    app.background_box.pack(side="left", padx=(6, 4))
    app.background_box.bind("<<ComboboxSelected>>", app.on_background_change)
    ttk.Button(background_row, text="刷新", width=6,
               command=app.refresh_background_list).pack(side="left")

    size_row = ttk.Frame(frame, style="Panel.TFrame")
    size_row.pack(fill="x", pady=(4, 0))
    ttk.Label(size_row, text="棋盘大小", style="Muted.TLabel").pack(side="left")
    app.board_scale_label = ttk.Label(size_row, text="100%", style="Panel.TLabel")
    app.board_scale_label.pack(side="right")
    scale = ttk.Scale(frame, from_=60, to=100, variable=app.board_scale_var,
                      command=lambda value: app.on_board_scale_change(value))
    scale.pack(fill="x", pady=(2, 2))
    quick = ttk.Frame(frame, style="Panel.TFrame")
    quick.pack(fill="x")
    for text, value in (("小 60%", 60), ("中 80%", 80), ("撑满 100%", 100)):
        ttk.Button(quick, text=text, width=9,
                   command=lambda v=value: app.on_board_scale_change(v)).pack(side="left",
                                                                              padx=(0, 4))
    ttk.Separator(frame).pack(fill="x", pady=6)
    ttk.Label(frame, text="连线设置已经单独放到工具栏的「连线设置」窗口里",
              style="Muted.TLabel").pack(anchor="w")
    reset_row = ttk.Frame(frame, style="Panel.TFrame")
    reset_row.pack(fill="x")
    ttk.Button(reset_row, text="恢复出厂设置", command=app.on_reset_settings).pack(
        side="left")
    ttk.Button(frame, text="关闭", command=window.destroy).pack(anchor="e", pady=(8, 0))
    app.refresh_skin_list()               # 建好控件后先把 skins 文件夹扫一遍
    _fill_view_settings_dialog(app, window)


def _fill_view_settings_dialog(app, window) -> None:
    """刷新界面设置对话框（数值回显）。"""
    if not window.winfo_exists():
        return
    settings = app.state.get("settings") or {}
    try:
        scale = int(float(settings.get("board_scale") or 100))
    except (TypeError, ValueError):
        scale = 100
    label = getattr(app, "board_scale_label", None)
    if label is not None and label.winfo_exists():
        label.configure(text=f"{scale}%")
    skin_label = getattr(app, "skin_label", None)
    if skin_label is not None and skin_label.winfo_exists():
        value = str(settings.get("board_skin") or "")
        if not value:
            skin_label.configure(text="自带画法")
        else:
            from . import skin as skin_module

            folder = skin_module.resolve_skin_path(value)
            if folder is None:
                skin_label.configure(text=f"{value}　找不到")
            else:
                candidate = skin_module.Skin(folder)
                text = candidate.name if candidate.ok else f"{candidate.name}　图不全"
                skin_label.configure(text=text)


def _link_screen_text(app) -> str:
    """连线设置里那行"屏幕 / 缩放 / 坐标系数"说明（排查 2K/4K 连线问题用）。"""
    linker = getattr(app.session, "linker", None)
    win32 = getattr(linker, "win32", None)
    if win32 is None:
        return ""
    lines = []
    try:
        info = win32.screen_metrics()
    except Exception:  # noqa: BLE001
        info = {}
    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    scale = float(info.get("scale") or 1.0)
    if width:
        text = f"屏幕实际分辨率 {width}×{height}"
        logical = int(info.get("logical_width") or 0)
        if logical and logical != width:
            text += (f"（本程序看到 {logical}×"
                     f"{int(info.get('logical_height') or 0)}）")
        text += (f"，系统缩放 {scale * 100:.0f}%" if abs(scale - 1.0) > 0.01
                 else "，系统缩放 100%")
        lines.append(text)
    try:
        lines.append(f"点走子的坐标系数 ×{float(win32.message_scale()):.2f}"
                     "（2K/4K 有缩放时自动换算，1080p 是 ×1.00）")
    except Exception:  # noqa: BLE001
        pass
    try:
        if win32.hwnd:
            left, top, cw, ch = win32.client_rect()
            lines.append(f"目标窗口客户区 {cw}×{ch}＠{left},{top}"
                         "（识别窗口跟着窗口位置走，拖动窗口也能跟上）")
        else:
            lines.append("还没选中目标窗口：点「开始连线」后，在对方象棋窗口上点一下就选中了。")
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)


def _build_link_settings_dialog(app, window) -> None:
    """连线设置对话框：所有跟"连线到别的象棋平台"有关的设置都在这里。

    从「界面设置」里搬出来的（用户要求）：扫描间隔 / 点击节奏 / 确认方式 /
    识别模型 / 棋盘区域 / 后台模式，外加「开始连线」「断开」两个按钮。
    """
    import tkinter as tk
    from tkinter import ttk

    frame = ttk.Frame(window, style="Panel.TFrame")
    frame.pack(fill="both", expand=True, padx=12, pady=10)
    settings = app.session.settings

    ttk.Label(frame, text="连线其他象棋平台（天天象棋 / QQ 象棋 …）",
              style="Panel.TLabel", font=UI_FONT_BOLD).pack(anchor="w")
    ttk.Label(frame, text="点工具栏的「连线」选目标窗口；下面的参数影响读取与走子节奏。",
              style="Muted.TLabel").pack(anchor="w", pady=(0, 6))

    # ---- 节奏
    row = ttk.Frame(frame, style="Panel.TFrame")
    row.pack(fill="x", pady=(2, 0))
    ttk.Label(row, text="扫描间隔", style="Muted.TLabel").pack(side="left")
    app.link_scan_var = tk.DoubleVar(value=float(settings.get("link_scan_seconds") or 1.0))
    ttk.Spinbox(row, from_=0.3, to=10.0, increment=0.2, width=5, justify="center",
                textvariable=app.link_scan_var,
                command=lambda: app.session.update_settings(
                    {"link_scan_seconds": float(app.link_scan_var.get())})).pack(
        side="left", padx=(2, 8))
    ttk.Label(row, text="秒", style="Muted.TLabel").pack(side="left", padx=(0, 12))
    ttk.Label(row, text="点击按下", style="Muted.TLabel").pack(side="left")
    app.link_click_var = tk.IntVar(value=int(settings.get("link_click_delay_ms") or 60))
    ttk.Spinbox(row, from_=10, to=500, increment=10, width=5, justify="center",
                textvariable=app.link_click_var,
                command=lambda: app.session.update_settings(
                    {"link_click_delay_ms": int(app.link_click_var.get())})).pack(
        side="left", padx=(2, 8))
    ttk.Label(row, text="毫秒", style="Muted.TLabel").pack(side="left", padx=(0, 12))
    ttk.Label(row, text="两步间隔", style="Muted.TLabel").pack(side="left")
    app.link_move_var = tk.IntVar(value=int(settings.get("link_move_delay_ms") or 200))
    ttk.Spinbox(row, from_=0, to=2000, increment=20, width=5, justify="center",
                textvariable=app.link_move_var,
                command=lambda: app.session.update_settings(
                    {"link_move_delay_ms": int(app.link_move_var.get())})).pack(
        side="left", padx=(2, 2))
    ttk.Label(row, text="毫秒", style="Muted.TLabel").pack(side="left")

    # ---- 确认方式
    row2 = ttk.Frame(frame, style="Panel.TFrame")
    row2.pack(fill="x", pady=(6, 0))
    app.link_strict_var = tk.BooleanVar(value=bool(settings.get("link_strict_validate", True)))
    make_check(row2, "规则校验", app.link_strict_var,
               lambda: app.session.update_settings(
                   {"link_strict_validate": bool(app.link_strict_var.get())})).pack(side="left")
    ttk.Label(row2, text="连续", style="Muted.TLabel").pack(side="left", padx=(12, 2))
    app.link_confirm_var = tk.IntVar(value=int(settings.get("link_confirm_frames") or 3))
    ttk.Spinbox(row2, from_=1, to=5, width=3, justify="center",
                textvariable=app.link_confirm_var,
                command=lambda: app.session.update_settings(
                    {"link_confirm_frames": max(1, min(5, int(app.link_confirm_var.get())))})).pack(
        side="left")
    ttk.Label(row2, text="帧确认", style="Muted.TLabel").pack(side="left", padx=(2, 0))
    ttk.Label(row2, text="或持续", style="Muted.TLabel").pack(side="left", padx=(12, 2))
    app.link_confirm_sec_var = tk.DoubleVar(value=float(settings.get("link_confirm_seconds") or 0.6))
    ttk.Spinbox(row2, from_=0.0, to=5.0, increment=0.1, width=4, justify="center",
                textvariable=app.link_confirm_sec_var,
                command=lambda: app.session.update_settings(
                    {"link_confirm_seconds": max(
                        0.0, min(5.0, float(app.link_confirm_sec_var.get())))})).pack(side="left")
    ttk.Label(row2, text="秒", style="Muted.TLabel").pack(side="left", padx=(2, 0))

    row3 = ttk.Frame(frame, style="Panel.TFrame")
    row3.pack(fill="x", pady=(2, 0))
    app.link_back_var = tk.BooleanVar(value=bool(settings.get("link_back_mode", True)))
    make_check(row3, "后台模式", app.link_back_var,
               lambda: app.session.update_settings(
                   {"link_back_mode": bool(app.link_back_var.get())})).pack(side="left")
    app.link_smart_var = tk.BooleanVar(value=bool(settings.get("link_smart_confirm", True)))
    make_check(row3, "智能确认", app.link_smart_var,
               lambda: app.session.update_settings(
                   {"link_smart_confirm": bool(app.link_smart_var.get())})).pack(
        side="left", padx=(12, 0))
    app.link_auto_var = tk.BooleanVar(value=bool(settings.get("link_auto_confirm", True)))
    make_check(row3, "走子前后各确认一次", app.link_auto_var,
               lambda: app.session.update_settings(
                   {"link_auto_confirm": bool(app.link_auto_var.get())})).pack(
        side="left", padx=(12, 0))

    # ---- 识别模型
    ttk.Separator(frame).pack(fill="x", pady=8)
    # ---- 对方棋盘朝向（执黑时最容易出问题的地方）
    ttk.Label(frame, text="对方棋盘朝向", style="Panel.TLabel",
              font=UI_FONT_BOLD).pack(anchor="w")
    orient_row = ttk.Frame(frame, style="Panel.TFrame")
    orient_row.pack(fill="x", pady=(2, 0))
    app.link_orientation_var = tk.StringVar(value=orientation_label(
        settings.get("link_orientation")))
    orient_box = ttk.Combobox(orient_row, width=28, state="readonly",
                              textvariable=app.link_orientation_var,
                              values=[label for _value, label in ORIENTATIONS])
    orient_box.pack(side="left")
    orient_box.bind("<<ComboboxSelected>>", app.on_link_orientation_change)
    app.link_orientation_now = ttk.Label(orient_row, text="", style="Muted.TLabel")
    app.link_orientation_now.pack(side="left", padx=(8, 0))
    ttk.Label(frame, text="执黑时多数平台把棋盘转 180°，识别坐标会自动跟着翻；"
                          "万一对方平台不翻，把这里改成「与本程序一致」。",
              style="Muted.TLabel").pack(anchor="w", pady=(2, 0))
    ttk.Separator(frame).pack(fill="x", pady=8)
    ttk.Label(frame, text="识别模型", style="Panel.TLabel",
              font=UI_FONT_BOLD).pack(anchor="w")
    model_row = ttk.Frame(frame, style="Panel.TFrame")
    model_row.pack(fill="x", pady=(2, 0))
    app.link_model_label = ttk.Label(model_row, text="自动（程序目录里的 yolov11.onnx）",
                                     style="Panel.TLabel")
    app.link_model_label.pack(side="left")
    ttk.Button(model_row, text="浏览…", width=7,
               command=app.on_choose_link_model).pack(side="left", padx=(8, 3))
    ttk.Button(model_row, text="自动查找", width=9,
               command=app.on_auto_link_model).pack(side="left")
    app.link_backend_label = ttk.Label(frame, text="", style="Muted.TLabel")
    app.link_backend_label.pack(anchor="w", pady=(2, 0))

    # ---- 棋盘区域
    ttk.Label(frame, text="棋盘区域（占目标窗口的百分比；默认整窗＝自动找）",
              style="Panel.TLabel", font=UI_FONT_BOLD).pack(anchor="w", pady=(8, 0))
    area_row = ttk.Frame(frame, style="Panel.TFrame")
    area_row.pack(fill="x", pady=(2, 0))
    app.link_area_vars = {}
    for key, label, default in (("link_area_x", "左", 0.0), ("link_area_y", "上", 0.0),
                                ("link_area_w", "宽", 100.0), ("link_area_h", "高", 100.0)):
        ttk.Label(area_row, text=label, style="Muted.TLabel").pack(side="left", padx=(0, 2))
        variable = tk.DoubleVar(value=float(settings.get(key) or default))
        app.link_area_vars[key] = variable
        ttk.Spinbox(area_row, from_=0.0, to=100.0, increment=1.0, width=5,
                    justify="center", textvariable=variable,
                    command=lambda k=key: app.on_link_area_change(k)).pack(side="left",
                                                                          padx=(0, 8))
        ttk.Label(area_row, text="%", style="Muted.TLabel").pack(side="left", padx=(0, 10))
    ttk.Button(area_row, text="整窗（自动）", width=11,
               command=app.on_link_area_reset).pack(side="left")

    # ---- 屏幕 / 坐标换算（换分辨率、换电脑时先看这一行）
    ttk.Separator(frame).pack(fill="x", pady=8)
    ttk.Label(frame, text="屏幕与坐标换算（自动适配 1080p / 2K / 4K 与缩放）",
              style="Panel.TLabel", font=UI_FONT_BOLD).pack(anchor="w")
    app.link_screen_label = ttk.Label(frame, text="", style="Muted.TLabel",
                                      justify="left", wraplength=640)
    app.link_screen_label.pack(anchor="w", pady=(2, 0))
    preview_row = ttk.Frame(frame, style="Panel.TFrame")
    preview_row.pack(fill="x", pady=(6, 0))
    ttk.Button(preview_row, text="预览识别", width=10,
               command=app.on_preview_link).pack(side="left")
    ttk.Label(preview_row, text="抓一张对方窗口的画面，看看棋盘认出来了没有"
                                "（换分辨率/换平台时先看这张）",
              style="Muted.TLabel").pack(side="left", padx=(8, 0))

    # ---- 按钮
    ttk.Separator(frame).pack(fill="x", pady=8)
    buttons = ttk.Frame(frame, style="Panel.TFrame")
    buttons.pack(fill="x")
    ttk.Button(buttons, text="开始连线", style="Accent.TButton",
               command=app.on_toggle_link).pack(side="left")
    ttk.Button(buttons, text="断开", command=app.on_cancel_link).pack(side="left", padx=4)
    ttk.Button(buttons, text="关闭", command=window.destroy).pack(side="right")
    _fill_link_settings_dialog(app, window)


def _fill_link_settings_dialog(app, window) -> None:
    """刷新连线设置窗口里那些"会从别处变"的值（确认参数 / 模型 / 识别后端）。"""
    if not window.winfo_exists():
        return
    settings = app.state.get("settings") or app.session.settings
    screen_label = getattr(app, "link_screen_label", None)
    if screen_label is not None and screen_label.winfo_exists():
        screen_label.configure(text=_link_screen_text(app))

    def sync(variable, wanted, cast=float):
        if variable is None or not hasattr(variable, "get"):
            return
        try:
            if abs(cast(variable.get()) - cast(wanted)) > 1e-6:
                variable.set(cast(wanted))
        except (TypeError, ValueError, tk.TclError):
            pass

    sync(getattr(app, "link_strict_var", None),
         bool(settings.get("link_strict_validate", True)), bool)
    sync(getattr(app, "link_smart_var", None),
         bool(settings.get("link_smart_confirm", True)), bool)
    sync(getattr(app, "link_auto_var", None),
         bool(settings.get("link_auto_confirm", True)), bool)
    sync(getattr(app, "link_back_var", None),
         bool(settings.get("link_back_mode", True)), bool)
    try:
        frames = max(1, min(5, int(settings.get("link_confirm_frames") or 3)))
    except (TypeError, ValueError):
        frames = 3
    sync(getattr(app, "link_confirm_var", None), frames, int)
    try:
        seconds = max(0.0, min(5.0, float(settings.get("link_confirm_seconds") or 0.6)))
    except (TypeError, ValueError):
        seconds = 0.6
    sync(getattr(app, "link_confirm_sec_var", None), seconds, float)

    label = getattr(app, "link_model_label", None)
    if label is not None and label.winfo_exists():
        value = str(settings.get("link_model_path") or "").strip()
        if value and Path(value).is_file():
            label.configure(text=f"{Path(value).name}　（{Path(value).parent}）")
        else:
            from ..link import find_model_file

            found = find_model_file()
            label.configure(text=(f"自动：{found.name}" if found
                                  else "自动（没找到模型，将退回模板匹配）"))
    backend = getattr(app, "link_backend_label", None)
    if backend is not None and backend.winfo_exists():
        linker = getattr(app.session, "linker", None)
        if linker is None:
            backend.configure(text="")
        else:
            name = "YOLO 模型" if linker.recognizer.backend == "yolo" else "模板匹配（降级）"
            error = linker.recognizer.error or ""
            backend.configure(text=f"当前识别方式：{name}" + (f"　{error}" if error else ""))
    variable = getattr(app, "link_orientation_var", None)
    if variable is not None and hasattr(variable, "get"):
        want = orientation_label(settings.get("link_orientation"))
        if str(variable.get()) != want:
            variable.set(want)
    now = getattr(app, "link_orientation_now", None)
    if now is not None and now.winfo_exists():
        linker = getattr(app.session, "linker", None)
        if linker is None:
            now.configure(text="")
        else:
            side = "黑" if linker.our_side == "black" else "红"
            current = orientation_label(linker.preferred_orientation())
            now.configure(text=f"我方执{side}　→　实际用：{current}")


def _build_book_settings_dialog(app, window) -> None:
    """开局库设置对话框（多本地库 + 策略 + 云库）。"""
    import tkinter as tk
    from tkinter import ttk

    frame = ttk.Frame(window, style="Panel.TFrame")
    frame.pack(fill="both", expand=True, padx=12, pady=10)

    ttk.Label(frame, text="本地开局库", style="Panel.TLabel",
              font=UI_FONT_BOLD).pack(anchor="w")
    row = ttk.Frame(frame, style="Panel.TFrame")
    row.pack(fill="x", pady=2)
    ttk.Button(row, text="添加库文件…", style="Accent.TButton",
               command=app.on_load_book).pack(side="left")
    ttk.Button(row, text="↑ 优先", width=7,
               command=lambda: app.on_book_priority(-1)).pack(side="left", padx=(3, 0))
    ttk.Button(row, text="↓ 靠后", width=7,
               command=lambda: app.on_book_priority(1)).pack(side="left", padx=3)
    ttk.Button(row, text="移除", width=6,
               command=app.on_book_remove).pack(side="left")
    ttk.Button(row, text="全部卸载", width=8,
               command=lambda: app.session.clear_book()).pack(side="left", padx=3)
    row_b = ttk.Frame(frame, style="Panel.TFrame")
    row_b.pack(fill="x", pady=(0, 2))
    ttk.Button(row_b, text="用当前棋谱建库",
               command=app.on_build_book).pack(side="left")
    ttk.Button(row_b, text="导出 OBK…", command=app.on_export_obk).pack(side="left", padx=3)
    ttk.Button(row_b, text="浏览库内容…",
               command=app.on_browse_book).pack(side="left", padx=3)

    table_frame = ttk.Frame(frame, style="Panel.TFrame")
    table_frame.pack(fill="x", pady=(2, 2))
    scroll = ttk.Scrollbar(table_frame, orient="vertical")
    scroll.pack(side="right", fill="y")
    app.book_priority_table = ttk.Treeview(
        table_frame, columns=("order", "name", "detail"), show="headings", height=4,
        yscrollcommand=scroll.set, selectmode="browse")
    for key, title, width in (("order", "优先", 40), ("name", "库文件", 170),
                              ("detail", "内容", 300)):
        app.book_priority_table.heading(key, text=title)
        app.book_priority_table.column(key, width=width,
                                       anchor="center" if key == "order" else "w")
    app.book_priority_table.pack(side="left", fill="x", expand=True)
    scroll.config(command=app.book_priority_table.yview)
    app._book_table_signature = None      # 新表格，必须重新填充

    options = ttk.Frame(frame, style="Panel.TFrame")
    options.pack(fill="x", pady=(2, 2))
    make_check(options, "启用库招", app.book_use_var,
               app.on_toggle_book_use).pack(side="left")
    ttk.Label(options, text="　策略", style="Muted.TLabel").pack(side="left")
    strategy_box = ttk.Combobox(options, width=16, state="readonly",
                                textvariable=app.book_strategy_var,
                                values=[item[1] for item in BOOK_STRATEGY_ITEMS])
    strategy_box.pack(side="left", padx=(2, 8))
    strategy_box.bind("<<ComboboxSelected>>", app.on_book_strategy_change)
    ttk.Label(options, text="脱谱步数", style="Muted.TLabel").pack(side="left")
    spin = ttk.Spinbox(options, from_=0, to=60, width=4, justify="center",
                       textvariable=app.book_max_ply_var,
                       command=app.on_book_max_ply_change)
    spin.pack(side="left", padx=4)
    spin.bind("<Return>", lambda event: app.on_book_max_ply_change())
    ttk.Label(options, text="手后不用库招", style="Muted.TLabel").pack(side="left")

    make_check(frame, "左栏开局库同时显示云库结果", app.book_merge_var,
               app._render_book).pack(anchor="w")
    app.book_dialog_info = ttk.Label(frame, text="未加载开局库", style="Muted.TLabel",
                                     wraplength=500, justify="left")
    app.book_dialog_info.pack(anchor="w", pady=(2, 4))

    ttk.Separator(frame).pack(fill="x", pady=4)
    ttk.Label(frame, text="在线云库", style="Panel.TLabel",
              font=UI_FONT_BOLD).pack(anchor="w")
    cloud_row = ttk.Frame(frame, style="Panel.TFrame")
    cloud_row.pack(fill="x", pady=2)
    make_check(cloud_row, "启用云库", app.cloud_enable_var,
               lambda: app.session.set_cloud_enabled(
                   app.cloud_enable_var.get())).pack(side="left")
    make_check(cloud_row, "自动学习", app.cloud_learn_var,
               app.on_toggle_cloud_learn).pack(side="left", padx=(8, 0))
    ttk.Button(cloud_row, text="立即查询",
               command=lambda: app.session.refresh_cloud()).pack(side="left", padx=6)
    ttk.Button(cloud_row, text="清空缓存",
               command=lambda: app.session.clear_cloud_cache()).pack(side="left")
    endpoint_row = ttk.Frame(frame, style="Panel.TFrame")
    endpoint_row.pack(fill="x", pady=2)
    ttk.Label(endpoint_row, text="接口地址", style="Muted.TLabel").pack(side="left")
    entry = ttk.Entry(endpoint_row, textvariable=app.cloud_endpoint_var, font=UI_FONT)
    entry.pack(side="left", fill="x", expand=True, padx=4)
    entry.bind("<Return>", lambda event: app.session.update_settings(
        {"cloud_endpoint": app.cloud_endpoint_var.get()}))
    ttk.Label(endpoint_row, text="超时", style="Muted.TLabel").pack(side="left")
    ttk.Spinbox(endpoint_row, from_=2, to=30, textvariable=app.cloud_timeout_var, width=4,
                command=lambda: app.session.update_settings(
                    {"cloud_timeout": float(app.cloud_timeout_var.get())})).pack(side="left",
                                                                                padx=4)
    ttk.Label(endpoint_row, text="秒", style="Muted.TLabel").pack(side="left")
    make_check(frame, "人机对战时优先走云库着法", app.cloud_use_var,
               lambda: app.session.set_cloud_use_for_move(
                   app.cloud_use_var.get())).pack(anchor="w", pady=(2, 0))
    app.cloud_dialog_status = ttk.Label(frame, text="尚未查询", style="Muted.TLabel",
                                        wraplength=500, justify="left")
    app.cloud_dialog_status.pack(anchor="w", pady=(2, 4))

    ttk.Button(frame, text="关闭", command=window.destroy).pack(anchor="e", pady=(6, 0))
    _fill_book_settings_dialog(app, window)


def _fill_book_settings_dialog(app, window) -> None:
    """刷新开局库设置对话框里的状态文字。"""
    if not window.winfo_exists():
        return
    book = app.state.get("book") or {}
    cloud = app.state.get("cloud") or {}
    settings = app.state.get("settings") or {}
    app.book_dialog_info.configure(
        text=str(book.get("status") or "未加载开局库"),
        foreground=ACCENT if not book.get("loaded") else MUTED)
    table = getattr(app, "book_priority_table", None)
    if table is not None and table.winfo_exists():
        books = book.get("books") or []
        signature = [str(item.get("path")) for item in books]
        if signature != getattr(app, "_book_table_signature", None):
            app._book_table_signature = signature
            table.delete(*table.get_children())
            for index, item in enumerate(books):
                table.insert("", "end", values=(index + 1, str(item.get("label") or ""),
                                                str(item.get("describe") or "")))
            rows = table.get_children()
            if rows:
                table.selection_set(rows[0])
    app._sync_book_strategy_box(str(book.get("strategy") or ""))
    if app.book_max_ply_var.get() != int(book.get("maxPly") or 0):
        app.book_max_ply_var.set(int(book.get("maxPly") or 0))
    if app.book_use_var.get() != bool(book.get("useInGame", True)):
        app.book_use_var.set(bool(book.get("useInGame", True)))
    if app.cloud_enable_var.get() != bool(cloud.get("enabled", True)):
        app.cloud_enable_var.set(bool(cloud.get("enabled", True)))
    if app.cloud_use_var.get() != bool(cloud.get("useForMove", True)):
        app.cloud_use_var.set(bool(cloud.get("useForMove", True)))
    if not app.cloud_endpoint_var.get():
        app.cloud_endpoint_var.set(str(settings.get("cloud_endpoint") or ""))
        app.cloud_timeout_var.set(int(float(settings.get("cloud_timeout") or 3)))
    text = f"云库：{cloud.get('statusText', '尚未查询')}"
    if cloud.get("cached"):
        text += "　缓存"
    if cloud.get("elapsed"):
        text += f"　{float(cloud['elapsed']):.2f}s"
    if cloud.get("best"):
        text += f"\n云库最佳着法：{cloud.get('best')} {cloud.get('bestCn') or ''}"
    if cloud.get("message"):
        text += f"\n{cloud.get('message')}"
    app.cloud_dialog_status.configure(text=text,
                                      foreground=MUTED if cloud.get("status") == "ok"
                                      else ACCENT)


def _format_count(value) -> str:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return "-"
    if number >= 1000000:
        return f"{number / 1000000:.2f}M"
    if number >= 1000:
        return f"{number / 1000:.1f}k"
    return str(number) if number else "-"


def run_desktop(engine_path: str = "", server: bool = False) -> int:
    """启动原生桌面界面（exe 双击运行走这里）。"""
    session = Session()
    window = XiangqiWindow(session)
    # 指定 → 上次用户选的（记忆）→ exe 里打包的皮卡鱼 → 自动查找
    engine_path = session.startup_engine_path(engine_path)
    if engine_path:
        threading.Thread(target=session.load_engine, args=(engine_path,), daemon=True).start()
    window.mainloop()
    return 0
