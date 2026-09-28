# -*- coding: utf-8 -*-
"""会话层：棋局状态、引擎分析、人机对战编排。

界面（浏览器）通过 :class:`Session` 的公开方法操作棋局，所有状态变化通过
``broadcast`` 回调推送给界面。这一层是整个程序的"大脑"：

* 管理棋谱树与当前着法；
* 维护引擎进程与 UCI 选项，把搜索信息整理成可以直接显示的推荐着法；
* 打谱模式/分析模式/人机对战模式的行为差异；
* 导入导出（PGN / XQF）、局面 FEN、记谱显示。
"""

from __future__ import annotations

import base64
import glob
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import notation as nt
from .book import (BOOK_STRATEGIES, DEFAULT_BOOK_STRATEGY, BookHit, BookManager, ObkBook,
                   PgnBook, TextBook, obk_hash, write_obk)
from .cloud import CloudBook, CloudResult, STATUS_OK, STATUS_TEXT
from .board import FILES, RED, BLACK, START_FEN, Board, Move, color_of, opponent, side_name
from .engine import (EVENT_BESTMOVE, EVENT_INFO, EVENT_LOG, EVENT_OPTIONS, EVENT_STATUS,
                     STATUS_ERROR, STATUS_READY, STATUS_STOPPED, STATUS_THINKING, EngineError,
                     UciEngine)
from .game import DEFAULT_RESULT, GameTree, Node, decode_bytes, parse_pgn
from .xqf import XqfError, read_xqf, write_xqf

DEFAULT_SETTINGS: Dict[str, object] = {
    "mode": "study",          # study 打谱 / analyze 分析 / play 人机对战
    # "" 纯手动打谱 / analyze 只分析不自动走子 / red 引擎执红 / black 引擎执黑
    "engine_mode": "",
    "notation": "cn",         # cn 中文 / trad 繁体 / iccs 坐标 / wxf
    "flip": False,
    "human_side": RED,
    "difficulty": 6,
    "movetime_ms": 1500,
    "depth_limit": 0,
    "infinite": False,
    "auto_analyze": True,
    "multi_pv": 1,                             # 默认只要 1 条推荐路线
    "threads": 1,                              # 默认 1 线程（用户可在右栏改）
    "hash_mb": 128,
    "show_wdl": True,
    "engine_path": "",
    "engine_options": {},              # 兼容旧格式（扁平）
    "engine_options_by_engine": {},    # {引擎路径: {选项名: 值}} 每个引擎各存一份
    "play_delay_ms": 1000,
    "engine_move_sound": False,
    "cloud_enabled": True,                     # 在线云库
    "cloud_learn": True,                       # 云库自动学习默认开启（learn=1，开放全部数据）
    "book_enabled": True,                      # 启用库招（人机对战优先走库着）
    "book_strategy": DEFAULT_BOOK_STRATEGY,    # 库招选择策略
    "book_max_ply": 0,                         # 脱谱步数（超过就不再用库招，0＝不限）
    # 界面显示设置（原生界面用；浏览器版会忽略）
    "show_arrows": True,                       # 棋步提示箭头
    "show_numbers": True,                      # 棋盘线路数字
    "show_statusbar": True,                    # 底部状态栏
    "piece_shadow": True,                      # 棋子阴影（右下投影，模仿鲨鱼象棋）
    "board_theme": "wood",                     # 棋盘主题
    "board_scale": 100,                        # 棋盘大小（百分比）
    # 皮肤可以写文件夹全路径，也可以只写 skin 文件夹的名字（如 "0-秋分棋者"）——
    # 打包成 exe 后解包目录每次都变，写名字才不会被路径失效坑到。
    "board_skin": "0-秋分棋者",                 # 默认用「秋分棋者」，空＝自带矢量画法
    "background": "03-意式",                    # 默认桌面背景（backgrounds 里的名字）
    "sound_enabled": True,                     # 音效开关（记到配置文件里）
    "book_paths": [],                          # 上次加载的开局库（按优先级）
    # 连线（连到别的象棋平台窗口）
    "link_scan_seconds": 1.0,                  # 多久截图识别一次
    "link_click_delay_ms": 60,                 # 鼠标按下到抬起的时长
    "link_move_delay_ms": 200,                 # 起点与终点两次点击的间隔
    "link_back_mode": True,                    # True＝后台发消息点击（推荐）
    "link_auto_confirm": True,                 # 走子前后各确认一次识别结果
    "link_confirm_frames": 3,                  # 连续 N 帧识别相同才确认（1~5）
    "link_strict_validate": True,              # 规则校验：不合法的一帧直接作废
    "link_confirm_seconds": 0.6,               # 时间窗：同一结果持续够久也确认
    "link_smart_confirm": True,                # 智能确认：正常走子只要 2 帧
    # 连线设置窗口里改的两项：识别模型（空＝自动找）与棋盘区域（占窗口的百分比）
    "link_model_path": "",                     # 识别模型 .onnx（空＝自动在程序目录里找）
    #: 对方棋盘朝向：auto＝按我方执红/执黑推断（执黑时对方棋盘是 180° 的）
    "link_orientation": "auto",
    "link_area_x": 0.0,                        # 棋盘区域 左（%）
    "link_area_y": 0.0,                        # 棋盘区域 上（%）
    "link_area_w": 100.0,                      # 棋盘区域 宽（%）
    "link_area_h": 100.0,                      # 棋盘区域 高（%）
    "cloud_endpoint": "http://www.chessdb.cn/chessdb.php",
    "cloud_timeout": 3.0,                      # 云库超时默认 3 秒
    "cloud_use_for_move": True,                # 人机对战时优先走云库着法
}

PACKAGE_ROOT = Path(__file__).resolve().parent
APP_ROOT = PACKAGE_ROOT.parent

#: 只影响界面显示、不需要重算局面的设置
VIEW_SETTING_KEYS = frozenset({"show_arrows", "show_numbers", "show_statusbar",
                               "piece_shadow",
                               "board_theme", "board_scale", "board_skin",
                               "background"})

#: 连线相关设置：改了不需要重算局面，也不该打断正在跑的分析
LINK_SETTING_KEYS = frozenset({
    "link_scan_seconds", "link_click_delay_ms", "link_move_delay_ms",
    "link_back_mode", "link_auto_confirm", "link_confirm_frames",
    "link_strict_validate", "link_confirm_seconds", "link_smart_confirm",
    "link_model_path", "link_orientation",
    "link_area_x", "link_area_y", "link_area_w", "link_area_h",
})

#: 随 exe 一起打包的皮卡鱼引擎文件名（按优先顺序找）。
#: 目前 engine\ 里放的是官方 Pikafish 2026-09-25（Windows x86-64 universal），
#: 打包时复制成 pikafish.exe；其余名字留着兼容用户自己换引擎的情况。
BUNDLED_ENGINE_NAMES = ("pikafish.exe", "pikafish-bmi2.exe",
                        "Pikafish-Windows-x86-64-universal.exe",
                        "pikafish-avx2.exe", "pikafish-sse41-popcnt.exe")

#: 打包内置的引擎在配置里用这个"稳定标记"表示。
#: PyInstaller 每次运行都会把内置文件解包到**不同的临时目录**（``sys._MEIPASS``），
#: 所以内置引擎的真实路径每次启动都不一样——直接把它记进 settings.json，
#: 下次打开就找不到引擎了，引擎的 UCI 选项也会跟着丢。用 ``@builtin/文件名``
#: 当"身份证"，每次启动都重新解析成当时那个临时目录里的真实文件。
BUILTIN_ENGINE_PREFIX = "@builtin/"

#: 需要记到 settings.json 里、下次打开要继续用的设置
PERSISTED_KEYS = (
    "movetime_ms", "threads", "hash_mb",                 # 思考时间 / 线程 / 哈希
    "cloud_learn", "cloud_timeout", "cloud_enabled",      # 云库
    "book_enabled", "book_strategy", "book_max_ply", "book_paths",   # 开局库
    "board_theme", "board_scale", "board_skin",           # 主题 / 大小 / 皮肤
    "background",                                        # 桌面背景图
    "show_arrows", "show_numbers", "show_statusbar", "piece_shadow", "sound_enabled",
    "notation", "flip", "multi_pv", "depth_limit",
    "engine_path", "engines",                             # 上次用的引擎
    "engine_options_by_engine",                           # 每个引擎各自的 UCI 选项
    "link_scan_seconds", "link_click_delay_ms", "link_move_delay_ms",
    "link_back_mode", "link_auto_confirm",
    "link_confirm_frames", "link_strict_validate",
    "link_confirm_seconds", "link_smart_confirm",
    "link_model_path",                                   # 识别模型
    "link_orientation",                                  # 对方棋盘朝向
    "link_area_x", "link_area_y", "link_area_w", "link_area_h",   # 棋盘区域
)


def settings_file() -> Path:
    """配置文件位置：优先放在程序（exe）旁边，写不进去就退回用户目录。"""
    override = os.environ.get("XQ_SETTINGS_FILE")
    if override:
        return Path(override)
    candidates = [exe_dir() / "settings.json"]
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "jingye-xiangqi" / "settings.json")
    candidates.append(Path.home() / ".jingye-xiangqi" / "settings.json")
    for candidate in candidates:
        try:
            if candidate.parent.exists() and os.access(candidate.parent, os.W_OK):
                return candidate
            candidate.parent.mkdir(parents=True, exist_ok=True)
            if os.access(candidate.parent, os.W_OK):
                return candidate
        except OSError:
            continue
    return candidates[0]


def bundle_dir() -> Path:
    """程序所在目录：源码运行是项目根目录，PyInstaller 打包后是解包目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return APP_ROOT


def exe_dir() -> Path:
    """可执行文件所在目录；源码运行时退化为项目目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return APP_ROOT


def web_root() -> Path:
    """定位界面文件目录，兼容源码运行与单文件 exe 运行。"""
    candidates = [
        PACKAGE_ROOT / "web",                    # 源码运行
        bundle_dir() / "xqtrainer" / "web",      # onefile 解包目录
        exe_dir() / "xqtrainer" / "web",         # 绿色版：exe 同级保留 web 目录
        exe_dir() / "web",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


class Session:
    """一个棋局会话（整个程序共享一个）。"""

    def __init__(self, broadcast: Optional[Callable[[Dict[str, object]], None]] = None):
        self.broadcast = broadcast or (lambda event: None)
        self.lock = threading.RLock()
        self.tree = GameTree()
        self.settings = dict(DEFAULT_SETTINGS)
        self.engine = UciEngine(on_event=self._on_engine_event)
        self.engine_path = ""
        self.engine_options: List[Dict[str, object]] = []
        self.analysis: Dict[str, object] = self._empty_analysis()
        self.pending: Optional[Dict[str, object]] = None
        self.selected: Optional[str] = None
        self.message = ""
        self.message_level = "info"
        self.last_import_name = ""
        self.last_import_games: List[str] = []
        self.games: List[GameTree] = []
        self.game_index = 0
        self._redo_stack: List[int] = []
        self.book = BookManager()
        self.book_status = "未加载开局库"
        #: 本程序走出一步棋时通知外界（连线功能用它把这一步点到对方平台上）
        self.move_listeners: List[Callable[[str, int, str], None]] = []
        self._sync_book_settings()
        self.cloud_result: Optional[CloudResult] = None
        self.cloud_hits: List[Dict[str, object]] = []
        self._cloud_token: Optional[Tuple[int, str]] = None
        self.cloud = CloudBook(timeout=float(self.settings.get("cloud_timeout", 3.0)),
                               cache_path=Path(os.environ.get("TEMP", ".")) /
                               "jingye_xiangqi_cloud_cache.json")
        # 连线模块：连到别的象棋平台窗口（只负责"看屏幕 / 点屏幕"）
        from .link import GraphLinker

        self.linker = GraphLinker(self)
        self.cloud.learn = bool(self.settings.get("cloud_learn", True))
        self._sync_mode_settings()
        self.load_settings_file()          # 记得上次的设置（思考时间/线程/皮肤/引擎/库…）
        self._last_info_emit = 0.0
        self._last_info_work = 0.0
        self._last_info_multipv = 0
        self._analysis_board: Optional[Board] = None
        self._tree_rev = 0
        self._state_rev = 0
        self._auto_analyze_timer: Optional[threading.Timer] = None
        self._engine_side_playing = False
        self._engine_move_retries = 0

    # ================================================================== 工具
    # ================================================================ 设置持久化
    def load_settings_file(self) -> None:
        """启动时把上次的设置读回来（文件不存在就保持默认值）。"""
        path = settings_file()
        self.settings_path = path
        try:
            if not path.is_file():
                return
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        for key in PERSISTED_KEYS:
            if key in data:
                self.settings[key] = data[key]
        # 生效到各个子系统
        self._sync_book_settings()
        self.cloud.timeout = float(self.settings.get("cloud_timeout") or 3.0)
        self.cloud.learn = bool(self.settings.get("cloud_learn", True))
        self.cloud.endpoint = str(self.settings.get("cloud_endpoint")
                                  or getattr(self.cloud, "endpoint", "") or
                                  self.cloud.endpoint)
        strategy = str(self.settings.get("book_strategy") or DEFAULT_BOOK_STRATEGY)
        self.book.strategy = strategy if strategy in BOOK_STRATEGIES else DEFAULT_BOOK_STRATEGY
        # 把上次加载过的开局库重新加载回来
        for item in list(self.settings.get("book_paths") or []):
            try:
                if Path(str(item)).is_file():
                    self.book.load(Path(str(item)))
            except (OSError, ValueError):
                continue
        if self.book.sources:
            self.book_status = self.book.describe()

    def save_settings_file(self) -> None:
        """把用户设置写进 settings.json（每次改动都会写，量很小）。"""
        path = getattr(self, "settings_path", None) or settings_file()
        payload = {key: self.settings.get(key) for key in PERSISTED_KEYS}
        payload["book_paths"] = [source.path for source in self.book.sources]
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        except OSError:
            pass

    def reset_settings(self, keep_engine: bool = True) -> None:
        """恢复出厂设置：删掉 settings.json，所有设置回到默认值（立即生效）。

        ``keep_engine`` 为真时，重置完会像第一次打开那样自动重新找引擎并加载，
        这样用户不用手动再加载一遍。
        """
        path = getattr(self, "settings_path", None) or settings_file()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        with self.lock:
            self.settings = dict(DEFAULT_SETTINGS)
            self.settings["engine_options_by_engine"] = {}
            self.settings["engine_options"] = {}
            self.settings["engines"] = []
            self.settings["book_paths"] = []
            self.settings_path = path
            self.clear_book()                       # 开局库也回到"没加载"
            self._sync_mode_settings()
            self._sync_book_settings()
        # 云库 / 引擎存档等外围状态一并回到默认
        self.cloud.learn = bool(self.settings.get("cloud_learn", True))
        try:
            self.cloud.timeout = float(self.settings.get("cloud_timeout") or 3.0)
        except (TypeError, ValueError):
            self.cloud.timeout = 3.0
        self.cloud.clear_cache()
        self.engine_options = []
        self.settings["engine_path"] = ""
        self._notify("已恢复出厂设置：所有设置回到默认值（界面、开局库、引擎选项都已重置）")
        self._bump_tree()
        self._after_position_change(force=True)
        self.save_settings_file()
        if keep_engine:
            def reload_engine() -> None:
                found = self.autodetect_engine()
                if found:
                    self.load_engine(found[0])
            timer = threading.Timer(0.6, reload_engine)
            timer.daemon = True
            timer.start()

    def _sync_mode_settings(self) -> None:
        """把模式映射成内部的走子与设置状态。

        三个模式互斥且都可以"不选"：都不选＝纯手动打谱（引擎不自动走子、也不强制分析）。
        """
        engine_mode = str(self.settings.get("engine_mode") or "")
        if engine_mode == "red":
            self.settings["mode"] = "play"
            self.settings["human_side"] = BLACK      # 引擎执红，人走黑
            self.settings["infinite"] = False
            self.settings["auto_analyze"] = True
            # ★ 不改 flip：棋盘视角完全由用户点「翻转棋盘」决定，
            #   选模式不会自动翻棋盘（用户就是要保持自己原来的视角）。
        elif engine_mode == "black":
            self.settings["mode"] = "play"
            self.settings["human_side"] = RED        # 引擎执黑，人走红
            self.settings["infinite"] = False
            self.settings["auto_analyze"] = True
        elif engine_mode == "analyze":
            self.settings["engine_mode"] = "analyze"
            self.settings["mode"] = "analyze"
            self.settings["infinite"] = True         # 分析模式：引擎一直算
            self.settings["auto_analyze"] = True
        else:
            # 纯手动打谱：不自动走子，也不自动分析
            self.settings["engine_mode"] = ""
            self.settings["mode"] = "study"
            self.settings["infinite"] = False
            self.settings["auto_analyze"] = False

    def set_engine_mode(self, engine_mode: str) -> None:
        """切换模式：``red`` 引擎执红 / ``black`` 引擎执黑 / ``analyze`` 只分析。

        三者互斥且都可以取消：再次点击当前模式（或传入空值）即回到**纯手动打谱**。
        """
        engine_mode = engine_mode if engine_mode in ("red", "black", "analyze") else ""
        with self.lock:
            if engine_mode and str(self.settings.get("engine_mode") or "") == engine_mode:
                engine_mode = ""                      # 点当前模式 = 取消选择
            self.settings["engine_mode"] = engine_mode
            self._sync_mode_settings()
            if engine_mode == "red":
                text = "引擎执红（你走黑方）—— 轮到红方引擎会自动走棋"
            elif engine_mode == "black":
                text = "引擎执黑（你走红方）—— 轮到黑方引擎会自动走棋"
            elif engine_mode == "analyze":
                text = "分析模式 —— 引擎持续分析并给出推荐着法，不会自动走子"
            else:
                text = "纯手动打谱 —— 引擎不自动走子、也不强制分析（可点「开始分析」随时分析）"
            self._notify(text)
            self._after_position_change(force=True)

    def current_mode_info(self) -> Dict[str, object]:
        engine_mode = str(self.settings.get("engine_mode") or "")
        names = {"red": "引擎执红", "black": "引擎执黑", "analyze": "分析模式",
                 "": "纯手动打谱"}
        if engine_mode == "red":
            detail = "引擎走红方，你走黑方"
            engine_side = RED
        elif engine_mode == "black":
            detail = "引擎走黑方，你走红方"
            engine_side = BLACK
        elif engine_mode == "analyze":
            detail = "引擎只分析、给提示，不自动走子（双方都可手动走子）"
            engine_side = None
        else:
            detail = "引擎不自动走子、也不强制分析，双方都自己走（可随时点「开始分析」）"
            engine_side = None
        board = self.tree.board_at()
        waiting = bool(engine_side and board.side == engine_side and board.result() is None)
        return {"mode": engine_mode or "manual", "name": names[engine_mode], "detail": detail,
                "engineSide": engine_side, "autoMove": engine_mode in ("red", "black"),
                "engineToMove": waiting, "manual": engine_mode == ""}

    def can_human_move(self) -> bool:
        """当前是否轮到人走（纯手动与分析模式永远可以走）。"""
        info = self.current_mode_info()
        if not info["autoMove"]:
            return True
        return not info["engineToMove"]

    def auto_analyze_enabled(self) -> bool:
        """当前是否处于「引擎持续分析」状态。"""
        return bool(self.settings.get("auto_analyze")) and str(
            self.settings.get("mode") or "") in ("analyze", "play")

    @staticmethod
    def _empty_analysis() -> Dict[str, object]:
        return {"running": False, "lines": [], "bestmove": None, "depth": 0, "seldepth": 0,
                "nodes": 0, "nps": 0, "time": 0, "history": [], "wdl_history": [],
                "fen": "", "side": RED, "token": None}

    def _notify(self, text: str, level: str = "info") -> None:
        self.message = text
        self.message_level = level
        if text:
            self.broadcast({"type": "toast", "text": text, "level": level})

    def _bump_state(self) -> None:
        self._state_rev += 1
        self.broadcast({"type": "state", "state": self.state()})
        self._save_settings_soon()

    def notify_move(self, node, source: str = "local") -> None:
        """本程序走出了一步棋：通知监听者（iccs, 手数, 来源）。"""
        for callback in list(getattr(self, "move_listeners", [])):
            try:
                callback(str(getattr(node, "iccs", "")), int(getattr(node, "ply", 0)), source)
            except Exception:  # noqa: BLE001
                continue

    def _save_settings_soon(self) -> None:
        """状态一变就把设置存下来（最多每 2 秒写一次盘，避免频繁写文件）。"""
        now = time.time()
        if now - getattr(self, "_last_settings_save", 0.0) < 2.0:
            return
        self._last_settings_save = now
        self.save_settings_file()

    def _bump_tree(self) -> None:
        self._tree_rev += 1

    # ================================================================== 状态
    def state(self) -> Dict[str, object]:
        tree = self.tree
        node = tree.current
        board = tree.board_at(node)
        legal = self._legal_map(board)
        path = tree.line_to(node)
        moves = []
        for item in path:
            if item.is_root:
                continue
            moves.append({
                "id": item.id,
                "iccs": item.iccs,
                "cn": item.cn,
                "cn_trad": item.cn_trad,
                "wxf": item.wxf,
                "mover": item.mover,
                "captured": item.captured,
                "check": item.is_check,
                "mate": item.is_mate,
                "comment": item.comment,
            })
        last = path[-1] if len(path) > 1 else None
        result = board.result()
        status = self._status_text(board, result)
        return {
            "fen": board.to_fen(),
            "board": board.to_fen().split()[0].split("/"),
            "side": board.side,
            "sideName": side_name(board.side),
            "ply": node.ply,
            "moves": moves,
            "current": node.id,
            "rootId": int(self.tree.root.id),      # 初始局面（第 0 手）的节点
            "rootComment": tree.root.comment,
            "comment": node.comment,
            "variations": tree.variations_at(node),
            "siblings": ([{"id": item.id, "iccs": item.iccs, "cn": item.cn}
                          for item in node.parent.children] if node.parent is not None else []),
            "lastMove": ({"from": last.move.from_name, "to": last.move.to_name,
                          "iccs": last.iccs} if last is not None and last.move else None),
            "check": board.is_check(board.side),
            "result": result,
            "resultName": self._result_name(result, board),
            "legal": legal,
            "selected": self.selected,
            "destinations": sorted(legal.get(self.selected, [])) if self.selected else [],
            "material": {"red": board.material(RED), "black": board.material(BLACK)},
            "piecesCount": {"red": sum(1 for _ in board.iter_pieces(RED)),
                            "black": sum(1 for _ in board.iter_pieces(BLACK))},
            "repetition": tree.repetition_count(node),
            "meta": dict(tree.meta),
            "warnings": list(tree.warnings)[:20],
            "source": tree.source,
            "settings": dict(self.settings),
            "engine": self.engine_status(),
            "analysis": self._analysis_payload(),
            "message": self.message,
            "messageLevel": self.message_level,
            "treeRev": self._tree_rev,
            "stateRev": self._state_rev,
            "startFen": tree.start_fen,
            "canUndo": node.parent is not None,
            "treeNodes": tree.count_nodes(),
            "games": list(self.last_import_games),
            "gameIndex": self.game_index,
            "moveCount": len(moves),
            "book": {
                "loaded": self.book.loaded,
                "status": self.book_status,
                "useInGame": self.book.use_in_game,
                "strategy": self.book.strategy,
                "strategyName": BOOK_STRATEGIES.get(
                    self.book.strategy, BOOK_STRATEGIES[DEFAULT_BOOK_STRATEGY]),
                "maxPly": self.book.max_ply,
                "inBook": self.book.in_book(node.ply),
                "books": self.book.list_books(),
                "hits": self.book_hits(),
            },
            "modeInfo": self.current_mode_info(),
            "canHumanMove": self.can_human_move(),
            "cloud": self.cloud_payload(),
        }

    def cloud_payload(self) -> Dict[str, object]:
        """云库面板需要的数据。"""
        enabled = bool(self.settings.get("cloud_enabled", True))
        result = self.cloud_result
        payload: Dict[str, object] = {
            "enabled": enabled,
            "learn": bool(self.settings.get("cloud_learn", True)),
            "endpoint": str(self.settings.get("cloud_endpoint") or ""),
            "status": "disabled" if not enabled else (result.status if result else "idle"),
            "statusText": ("未启用" if not enabled
                           else (STATUS_TEXT.get(result.status, result.status) if result
                                 else "尚未查询")),
            "message": (result.message if result else ""),
            "cached": bool(result.cached) if result else False,
            "elapsed": round(float(result.elapsed), 2) if result else 0.0,
            "hits": list(self.cloud_hits),
            "useForMove": bool(self.settings.get("cloud_use_for_move", True)),
            "queryTime": self.cloud.last_query_time,
        }
        if result is not None and result.status == STATUS_OK and result.hits:
            best = result.hits[0]
            payload["best"] = best.move
            payload["bestCn"] = self.cloud_hits[0].get("cn") if self.cloud_hits else ""
            payload["bestScore"] = best.score
        return payload

    # ================================================================== 云库
    def set_cloud_enabled(self, enabled: bool) -> None:
        self.settings["cloud_enabled"] = bool(enabled)
        if enabled:
            self._notify("已启用在线云库")
            self._query_cloud_async(force=True)
        else:
            self.cloud_result = None
            self.cloud_hits = []
            self._notify("已关闭在线云库")
            self._bump_state()

    def set_cloud_learn(self, enabled: bool) -> None:
        """云库自动学习：开启后查询带 learn=1，云库才会开放全部招法、分值与胜率。"""
        enabled = bool(enabled)
        self.settings["cloud_learn"] = enabled
        self.cloud.learn = enabled
        self.cloud.clear_cache()               # 之前的缓存是不完整的结果，清掉重查
        self._notify("云库自动学习已开启：将获取全部招法、分值与胜率（会向云库提交当前局面）"
                     if enabled else "云库自动学习已关闭（只读已有数据）")
        if self.settings.get("cloud_enabled", True):
            self._query_cloud_async(force=True)
        else:
            self._bump_state()

    def set_cloud_use_for_move(self, enabled: bool) -> None:
        self.settings["cloud_use_for_move"] = bool(enabled)
        self._bump_state()

    def refresh_cloud(self) -> None:
        self._query_cloud_async(force=True)
        self._notify("正在查询云库…")

    def clear_cloud_cache(self) -> None:
        self.cloud.clear_cache()
        try:
            self.cloud.save_cache()
        except Exception:  # noqa: BLE001
            pass
        self._notify("已清空云库缓存")

    def _query_cloud_async(self, force: bool = False) -> None:
        if not self.settings.get("cloud_enabled", True):
            return
        board = self.tree.board_at()
        token = (self.tree.current.id, board.to_fen())
        if not force and self._cloud_token == token:
            return
        self._cloud_token = token
        thread = threading.Thread(target=self._cloud_worker, args=(token,), daemon=True)
        thread.start()

    def _cloud_worker(self, token: Tuple[int, str]) -> None:
        _, fen = token
        try:
            result = self.cloud.query_all(fen)
        except Exception as exc:  # noqa: BLE001
            result = CloudResult(status="error", message=str(exc))
        with self.lock:
            if self._cloud_token != token:
                return                                   # 局面已经变了
            self.cloud_result = result
            board = self.tree.board_at()
            hits: List[Dict[str, object]] = []
            for hit in result.hits[:40]:
                data = hit.to_dict()
                move = board.find_move_iccs(hit.move)
                if move is not None:
                    data["cn"] = nt.to_chinese(board, move)
                hits.append(data)
            self.cloud_hits = hits
        self.broadcast({"type": "cloud", "cloud": self.cloud_payload()})
        try:
            self.cloud.save_cache()
        except Exception:  # noqa: BLE001
            pass

    def cloud_best_move(self, board: Optional[Board] = None, timeout: float = 2.5) -> Optional[str]:
        """人机对战时用云库着法（优先用缓存，最多等 ``timeout`` 秒）。"""
        if not self.settings.get("cloud_enabled", True) or \
                not self.settings.get("cloud_use_for_move", True):
            return None
        board = board or self.tree.board_at()
        fen = board.to_fen()
        result = self.cloud.cached(fen)
        if result is None:
            original = self.cloud.timeout
            self.cloud.timeout = min(original, timeout)
            try:
                result = self.cloud.query_all(fen)
            finally:
                self.cloud.timeout = original
        if result is None or not result.ok or not result.hits:
            return None
        for hit in result.hits:
            if board.find_move_iccs(hit.move) is not None:
                return hit.move
        return None

    def tree_payload(self, max_nodes: int = 2500) -> Dict[str, object]:
        return self.tree.to_dict(max_nodes)

    def _legal_map(self, board: Board) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for move in board.legal_moves():
            out.setdefault(move.from_name, []).append(move.to_name)
        return out

    def _status_text(self, board: Board, result: Optional[str]) -> str:
        if result is not None:
            return f"{self._result_name(result, board)}（{'将死' if board.is_check(board.side) else '困毙'}）"
        text = f"{side_name(board.side)}走棋"
        if board.is_check(board.side):
            text += "（被将军）"
        return text

    @staticmethod
    def _result_name(result: Optional[str], board: Board) -> str:
        if result == "red":
            return "红方胜"
        if result == "black":
            return "黑方胜"
        return ""

    def engine_status(self) -> Dict[str, object]:
        return {
            "state": self.engine.state,
            "name": self.engine.name,
            "author": self.engine.author,
            "path": self.engine.path,
            "error": self.engine.last_error,
            "searching": self.engine.searching,
            "options": self.engine_options,
            "values": dict(self.engine.current_options),
            "known": self.known_engines(),
            "settings": dict(self.settings),
            "hasEngine": bool(self.engine.path) and self.engine.is_running(),
        }

    def _analysis_payload(self) -> Dict[str, object]:
        data = dict(self.analysis)
        data["running"] = bool(self.engine.searching and self.engine.is_running())
        return data

    # ================================================================== 棋局操作
    def new_game(self, fen: str = START_FEN, meta: Optional[Dict[str, str]] = None) -> None:
        with self.lock:
            self.tree = GameTree(fen, meta=meta)
            self.selected = None
            self._bump_tree()
            self._notify("已新建棋局")
            self._after_position_change()

    def set_fen(self, fen: str) -> bool:
        try:
            board = Board(fen)
        except ValueError as exc:
            self._notify(f"FEN 不合法：{exc}", "error")
            return False
        problems = board.check_legal_position()
        if problems:
            self._notify("这个局面在正常对局中不可能出现：" + "；".join(problems[:3]), "error")
            return False
        with self.lock:
            self.tree = GameTree(fen, meta=dict(self.tree.meta))
            self.selected = None
            self._bump_tree()
            self._notify("已设置局面")
            self._after_position_change()
        return True

    def play_move(self, from_name: str, to_name: str) -> bool:
        with self.lock:
            board = self.tree.board_at()
            move = board.find_move(from_name, to_name)
            if move is None:
                self._notify(f"不能走 {from_name}-{to_name}", "warn")
                self.selected = None
                self._bump_state()
                return False
            node = self.tree.play(move)
            self._redo_stack = []
            self.selected = None
            self._bump_tree()
            info = []
            if node.is_mate:
                info.append(f"{side_name(node.mover)}将死对手")
            elif node.is_check:
                info.append(f"{side_name(node.mover)}将军")
            if node.captured:
                info.append("吃子")
            self._notify(f"{node.cn}（{node.iccs}）" + ("，" + "，".join(info) if info else ""))
            self.notify_move(node, "human")      # 连线功能：把这步点到对方平台
            self._after_position_change(played_by="human")
        return True

    def select(self, square: str) -> None:
        with self.lock:
            board = self.tree.board_at()
            square = (square or "").strip().lower()
            valid = (len(square) == 2 and square[0] in FILES and square[1].isdigit()
                     and 0 <= int(square[1]) <= 9)
            if not valid:
                self.selected = None
            else:
                piece = board.get_name(square)
                if piece == "." or color_of(piece) != board.side:
                    self.selected = None
                else:
                    self.selected = square
            self._bump_state()

    def navigate(self, to: str, node_id: Optional[int] = None, index: int = 0) -> None:
        with self.lock:
            tree = self.tree
            if to == "first":
                tree.go_root()
            elif to == "prev":
                tree.go_prev()
            elif to == "next":
                tree.go_next(index)
            elif to == "last":
                tree.go_end()
            elif to == "node" and node_id is not None:
                tree.goto_id(node_id)
            self.selected = None
            self._after_position_change()

    def delete_node(self, node_id: int) -> bool:
        with self.lock:
            node = self.tree.node_by_id(node_id)
            if node is None or node.is_root:
                return False
            label = node.cn or node.iccs
            if not self.tree.delete_subtree(node):
                return False
            self._bump_tree()
            self._notify(f"已删除分支：{label}")
            self._after_position_change()
        return True

    def promote_node(self, node_id: int) -> bool:
        with self.lock:
            node = self.tree.node_by_id(node_id)
            if node is None or not self.tree.promote_variation(node):
                return False
            self._bump_tree()
            self.tree.goto(node)
            self._notify(f"已把 {node.cn} 设为主线")
            self._after_position_change()
        return True

    def set_comment(self, node_id: Optional[int], text: str) -> None:
        with self.lock:
            node = self.tree.node_by_id(node_id) if node_id is not None else self.tree.current
            if node is None:
                return
            node.comment = text.strip()
            self._bump_tree()
            self._bump_state()

    def flip_board(self) -> None:
        with self.lock:
            self.settings["flip"] = not bool(self.settings.get("flip"))
            self._bump_state()

    def undo(self, steps: int = 1, to_human: Optional[bool] = None) -> bool:
        """悔棋。人机对战默认退到轮到自己走棋的局面（等于撤回"对方 + 自己"各一手）。"""
        with self.lock:
            if self.tree.current.is_root:
                self._notify("已经在开局，不能悔棋", "warn")
                return False
            self._stop_timer()
            self.pending = None
            if self.engine.searching:
                self.engine.stop()
            human = self.settings.get("human_side", RED)
            if to_human is None:
                to_human = str(self.settings.get("mode", "study")) == "play"
            played = 0
            limit = max(1, steps)
            if to_human:
                # 需要回到"轮到自己走棋"的局面，允许最多回退 4 手（自己 + 对方 + 缓冲）
                limit = max(limit, 4)
            while played < limit and not self.tree.current.is_root:
                self._redo_stack.append(self.tree.current.id)
                self.tree.go_prev()
                played += 1
                if to_human and self.tree.board_at().side == human:
                    break
            self.selected = None
            self._bump_tree()
            node = self.tree.current
            self._notify(f"已悔棋 {played} 手，现在轮到{side_name(self.tree.board_at().side)}"
                         + (f"（{node.cn}）" if node.move else "（开局）"))
            self._after_position_change(played_by="human")
        return True

    def redo(self) -> bool:
        """重做（把刚才悔掉的着法再走回来）。"""
        with self.lock:
            if not self._redo_stack:
                self._notify("没有可以重做的着法", "warn")
                return False
            target_id = self._redo_stack.pop()
            node = self.tree.node_by_id(target_id)
            if node is None or node.parent is not self.tree.current:
                self._redo_stack = []
                self._notify("无法重做：棋谱已经变化", "warn")
                self._bump_state()
                return False
            self._stop_timer()
            self.pending = None
            if self.engine.searching:
                self.engine.stop()
            self.tree.goto(node)
            self.selected = None
            self._bump_tree()
            self._notify(f"已重做：{node.cn}（{node.iccs}）")
            self._after_position_change(played_by="human")
        return True

    # ================================================================== 导入 / 导出
    def import_data(self, kind: str, data: bytes, name: str = "", game_index: int = 0) -> bool:
        """导入棋谱。``kind`` 为 ``pgn`` 或 ``xqf``。"""
        kind = (kind or "").lower()
        try:
            if kind == "xqf":
                tree = read_xqf(data)
                games: List[GameTree] = [tree]
            elif kind in ("pgn", "txt"):
                games = parse_pgn(decode_bytes(data))
            else:
                self._notify("棋谱只支持两种格式：XQF 与 PGN。"
                             f"（当前文件被识别为 {kind or '未知'}）", "error")
                return False
        except (XqfError, ValueError, OSError) as exc:
            self._notify(f"导入失败：{exc}", "error")
            return False
        if not games:
            self._notify("文件里没有找到棋局", "error")
            return False
        if kind in ("pgn", "txt"):
            # 解析出来却一步棋都没有 → 明确告诉用户，别让人以为"读进去了但是空的"
            nodes = sum(game.count_nodes() for game in games)
            if nodes <= len(games):
                self._notify("这个 PGN 里没有解析到任何着法（可能是没见过的写法或编码）。"
                             "请把文件发给我看一下，标题栏里有 QQ。", "error")
                return False
        index = max(0, min(game_index, len(games) - 1))
        with self.lock:
            self.tree = games[index]
            self.games = games
            self.game_index = index
            self.last_import_name = name or ""
            self.last_import_games = [self._game_label(g) for g in games]
            self.selected = None
            self._bump_tree()
            notes = []
            if self.tree.warnings:
                notes.append(f"{len(self.tree.warnings)} 处着法无法识别（已跳过）")
            if len(games) > 1:
                notes.append(f"文件含 {len(games)} 局棋，当前显示第 {index + 1} 局")
            self._notify((f"已导入 {Path(name).name}" if name else "已导入棋谱")
                         + ("，" + "，".join(notes) if notes else ""),
                         "warn" if notes else "info")
            self._after_position_change()
        return True

    def select_game(self, index: int) -> bool:
        """切换最近导入文件里的第 index 局棋（多局 PGN 时使用）。"""
        with self.lock:
            if not self.games or not (0 <= index < len(self.games)):
                return False
            self._stop_timer()
            self.pending = None
            if self.engine.searching:
                self.engine.stop()
            self.tree = self.games[index]
            self.game_index = index
            self.selected = None
            self._bump_tree()
            self._notify(f"已切换到第 {index + 1} 局：{self.last_import_games[index]}")
            self._after_position_change()
        return True

    @staticmethod
    def _game_label(tree: GameTree) -> str:
        meta = tree.meta
        red = meta.get("Red") or "红方"
        black = meta.get("Black") or "黑方"
        event = meta.get("Event") or ""
        date_text = meta.get("Date") or ""
        label = f"{red} 对 {black}"
        extras = [item for item in (event, date_text, meta.get("Result", "")) if item and item != "*"]
        if extras:
            label += "（" + " · ".join(extras) + "）"
        return label

    def export_text(self, style: Optional[str] = None) -> str:
        style = style or str(self.settings.get("notation", "iccs"))
        style = {"cn": "cn", "trad": "trad", "iccs": "iccs", "wxf": "wxf"}.get(style, "iccs")
        with self.lock:
            text = self.tree.to_pgn(style=style)
        meta = self.tree.meta
        red = meta.get("Red") or "红方"
        black = meta.get("Black") or "黑方"
        self.export_filename = f"{red}对{black}.pgn"
        return text

    def copy_fen(self) -> str:
        return self.tree.board_at().to_fen()

    # ================================================================== 开局库
    def book_hits(self) -> List[Dict[str, object]]:
        """当前局面在开局库/云库里的命中招法（带中文记谱、胜率、次数）。"""
        if not self.book.loaded:
            return []
        board = self.tree.board_at()
        try:
            hits = self.book.lookup(board, limit=200)
        except Exception:  # noqa: BLE001
            return []
        payload: List[Dict[str, object]] = []
        for hit in hits:
            move = board.find_move_iccs(hit.move)
            data = hit.to_dict()
            if move is None:
                # 库里记录的着法在当前局面下不合法（多半是"按库内容"列出的演示库），
                # 仍然显示出来：中文留空、其它字段照实显示。
                data["cn"] = ""
                data["offBook"] = True
            else:
                data["cn"] = nt.to_chinese(board, move)
            payload.append(data)
        return payload

    def load_book(self, path: str) -> bool:
        try:
            self.book_status = self.book.load(Path(path))
        except (OSError, ValueError) as exc:
            self.book.last_error = str(exc)
            self.book_status = f"加载失败：{exc}"
            self._notify(f"开局库加载失败：{exc}", "error")
            self._bump_state()
            return False
        self.book_status = self.book.describe()
        self._notify(f"开局库已加载（共 {len(self.book.sources)} 个）：{self.book_status}")
        self._bump_state()
        return True

    def remove_book(self, path: str) -> bool:
        """从优先级列表里移除一个已经加载的开局库。"""
        removed = self.book.remove(str(path))
        self.book_status = self.book.describe()
        self._notify("已移除开局库" if removed else "这个开局库没有加载", 
                     "info" if removed else "warn")
        self._bump_state()
        return removed

    def move_book(self, path: str, delta: int) -> bool:
        """调整开局库的优先级（delta<0 上移＝更优先）。"""
        moved = self.book.move(str(path), int(delta))
        self.book_status = self.book.describe()
        if moved:
            names = "、".join(source.label for source in self.book.sources)
            self._notify(f"开局库优先级：{names}")
        self._bump_state()
        return moved

    def set_book_strategy(self, strategy: str) -> None:
        """库招选择策略：最高分 / 最高胜率 / 正分数随机 / 完全随机。"""
        strategy = strategy if strategy in BOOK_STRATEGIES else DEFAULT_BOOK_STRATEGY
        self.book.strategy = strategy
        self.settings["book_strategy"] = strategy
        self._notify(f"库招策略：{BOOK_STRATEGIES[strategy]}")
        self._bump_state()

    def set_book_max_ply(self, value: int) -> None:
        """脱谱步数：超过这个手数就不再用库招（0＝不限）。"""
        try:
            value = max(0, int(float(value or 0)))
        except (TypeError, ValueError):
            value = 0
        self.book.max_ply = value
        self.settings["book_max_ply"] = value
        self._notify("脱谱步数：不限" if not value else f"脱谱步数：{value} 手之后不再用库招")
        self._bump_state()

    def _sync_book_settings(self) -> None:
        """让开局库管理器与设置保持一致。"""
        self.book.use_in_game = bool(self.settings.get("book_enabled", True))
        strategy = str(self.settings.get("book_strategy") or DEFAULT_BOOK_STRATEGY)
        self.book.strategy = strategy if strategy in BOOK_STRATEGIES else DEFAULT_BOOK_STRATEGY
        try:
            self.book.max_ply = max(0, int(float(self.settings.get("book_max_ply") or 0)))
        except (TypeError, ValueError):
            self.book.max_ply = 0

    def clear_book(self) -> None:
        self.book.clear()
        self.book_status = "未加载开局库"
        self._notify("已卸载开局库")
        self._bump_state()

    def set_book_use_in_game(self, enabled: bool) -> None:
        """启用/关闭库招（人机对战时会优先走库里的着法）。"""
        self.book.use_in_game = bool(enabled)
        self.settings["book_enabled"] = bool(enabled)
        self._notify("已启用库招" if enabled else "已关闭库招（引擎自己思考）")
        self._bump_state()

    def build_book_from_current(self, depth: int = 30) -> int:
        """用当前棋谱现场生成一个内存开局库（用于人机对战走库着）。"""
        book = PgnBook(depth=depth)
        book.add_tree(self.tree)
        self.book.pgn = book
        self.book_status = book.describe()
        self._notify(f"已从当前棋谱生成开局库：{len(book.entries)} 个局面")
        self._bump_state()
        return len(book.entries)

    def save_book_as_obk(self, path: str) -> bool:
        """把当前棋谱库导出为 OBK 文件。"""
        if self.book.pgn is None:
            self.build_book_from_current()
        if self.book.pgn is None:
            self._notify("还没有可导出的开局库", "warn")
            return False
        try:
            count = self.book.pgn.to_obk(Path(path))
        except OSError as exc:
            self._notify(f"导出 OBK 失败：{exc}", "error")
            return False
        self._notify(f"已导出 OBK 开局库（{count} 条记录）")
        return True

    def export_xqf_bytes(self, version: int = 18) -> bytes:
        """把当前棋谱导出为 XQF 字节内容（含变例、评注与结果）。"""
        with self.lock:
            return write_xqf(self.tree, version=version)

    def save_xqf(self, path: str, version: int = 18) -> bool:
        try:
            data = self.export_xqf_bytes(version=version)
            Path(path).write_bytes(data)
        except (OSError, XqfError, ValueError) as exc:
            self._notify(f"保存 XQF 失败：{exc}", "error")
            return False
        self._notify(f"已保存 XQF：{Path(path).name}")
        return True

    # ================================================================== 引擎
    def load_engine(self, path: str, options: Optional[Dict[str, object]] = None,
                    args: Optional[List[str]] = None) -> bool:
        # 先把 "@builtin/xxx"（打包内置引擎的稳定标记）还原成本次运行的真实路径
        path = self.resolve_engine_tag(path)
        path = (path or "").strip().strip('"')
        if not path:
            self._notify("请先选择引擎可执行文件", "warn")
            return False
        self._stop_timer()
        try:
            self.engine.start(path, options=options, args=args)
        except EngineError as exc:
            self._notify(str(exc), "error")
            self.broadcast({"type": "engine", "engine": self.engine_status()})
            return False
        # 内置引擎在配置里存稳定标记（临时解包目录每次都不一样，存真实路径下次就找不到）
        self.settings["engine_path"] = self.engine_tag(path)
        self.engine_path = path
        self.remember_engine(path, self.engine.name)      # 记进"已添加的引擎"列表
        self.save_settings_file()                         # 下次打开还用它
        self._apply_engine_settings()
        self.engine.new_game()
        self._notify(f"引擎已加载：{self.engine.name}")
        # 写一行日志（exe 旁边的 link_debug.log）：方便确认"引擎到底是不是从 exe 里加载的"
        try:
            from .link import debug_log

            debug_log(f"引擎：已加载 {self.engine.name}｜{path}"
                      f"｜内置={self.is_bundled_engine_path(path)}"
                      f"｜线程={self.engine.option_value('Threads')}"
                      f"｜哈希={self.engine.option_value('Hash')}")
        except Exception:  # noqa: BLE001 - 记日志失败不影响加载
            pass
        self.broadcast({"type": "engine", "engine": self.engine_status()})
        self._after_position_change()
        return True

    def _apply_engine_settings(self) -> None:
        """把该引擎上次保存的 UCI 选项重新下发（不同引擎各存一份）。"""
        if not self.engine.is_running():
            return
        saved = self._saved_options_for_engine()
        for name, value in dict(saved).items():
            if self.engine.option(name) is not None:
                self.engine.set_option(name, value)
        # 首次使用某个引擎时给几个"开箱好用"的默认值（仍然是引擎自己的选项，界面可改）
        for name, value in self._default_options_for_engine().items():
            if name not in saved and self.engine.option(name) is not None:
                self.engine.set_option(name, value)
                saved[name] = value
        self._store_engine_options(saved)

    def _saved_options_for_engine(self) -> Dict[str, object]:
        """取出当前引擎保存的选项值（兼容早期的扁平结构）。"""
        by_engine = dict(self.settings.get("engine_options_by_engine") or {})
        path = self._engine_key(self.engine.path or self.settings.get("engine_path") or "")
        saved = dict(by_engine.get(path) or {})
        if not saved:
            legacy = self.settings.get("engine_options") or {}
            saved = {key: value for key, value in dict(legacy).items()
                     if self.engine.option(key) is not None}
        return saved

    def _store_engine_options(self, values: Dict[str, object]) -> None:
        path = self._engine_key(self.engine.path or self.settings.get("engine_path") or "")
        if not path:
            return
        by_engine = dict(self.settings.get("engine_options_by_engine") or {})
        by_engine[path] = dict(values)
        self.settings["engine_options_by_engine"] = by_engine

    def _default_options_for_engine(self) -> Dict[str, object]:
        """新引擎的初始默认值（只对引擎确实声明了的选项生效）。

        ★ 线程数**不再人为封顶到 8**：用户设成多少就一直生效多少，
          这里只在"第一次用这个引擎、用户还没设过"时给一个合理的初始值。
        """
        cpu = max(1, os.cpu_count() or 4)
        try:
            threads = int(self.settings.get("threads") or cpu)
        except (TypeError, ValueError):
            threads = cpu
        try:
            hash_mb = int(self.settings.get("hash_mb") or 128)
        except (TypeError, ValueError):
            hash_mb = 128
        return {
            "Threads": max(1, threads),
            "Hash": max(8, hash_mb),
            "UCI_ShowWDL": "true",
            # 皮卡鱼自带选项的默认值（引擎没声明这两项时会自动忽略）
            "Repetition Rule": "SkyRule",              # 重复局面判罚规则：天规
            "ScoreType": "PawnValueNormalized",        # 评分显示：归一化兵值（不是 Elo）
        }

    def _engine_key(self, path: str) -> str:
        """引擎存档用的键：把路径规范化，避免同一个引擎因写法不同而丢设置。

        打包内置的皮卡鱼解包目录每次运行都不同，所以用它自己的稳定标记当键，
        用户给它设过的 UCI 选项下次打开才不会丢。
        """
        text = self.engine_tag(path)
        if not text:
            return ""
        if text.startswith(BUILTIN_ENGINE_PREFIX):
            return text.lower()
        try:
            text = str(Path(text).resolve())
        except OSError:
            pass
        return text.lower() if os.name == "nt" else text

    def refresh_engine_options(self) -> None:
        """重新探测引擎支持的 UCI 选项（不同引擎选项不同，动态生成）。"""
        if not self.engine.is_running():
            self._notify("请先加载引擎", "warn")
            return
        with self.lock:
            self.engine.probe_options()
            self.engine_options = [option.to_dict() for option in self.engine.options]
        self._apply_engine_settings()
        self._notify(f"已探测到 {len(self.engine_options)} 个 UCI 选项")
        self.broadcast({"type": "engine", "engine": self.engine_status()})

    def set_engine_option(self, name: str, value: object) -> bool:
        if not self.engine.is_running():
            self._notify("引擎尚未加载", "warn")
            return False
        with self.lock:
            self.engine.set_option(name, value)          # 立即生效
            saved = self._saved_options_for_engine()
            saved[name] = self.engine.option_value(name)
            self._store_engine_options(saved)
            self.settings["engine_options"] = saved       # 兼容旧字段
            # 线程数 / 哈希表同时记进设置：以后新引擎的初始值就按用户设的来，
            # 不会再莫名其妙回到 8。
            if name in ("Threads", "Hash"):
                key = "threads" if name == "Threads" else "hash_mb"
                try:
                    self.settings[key] = int(float(self.engine.option_value(name) or 0))
                except (TypeError, ValueError):
                    pass
        self.broadcast({"type": "engine", "engine": self.engine_status()})
        self._notify(f"引擎选项 {name} = {self.engine.option_value(name)}")
        return True

    # ------------------------------------------------------------ 多引擎管理
    def known_engines(self) -> List[Dict[str, str]]:
        """已经添加过的引擎（可随时切换，选项各自保存）。"""
        return [dict(item) for item in (self.settings.get("engines") or [])]

    def remember_engine(self, path: str, name: str = "") -> None:
        path = self.engine_tag(str(path or "").strip().strip('"'))
        if not path:
            return
        builtin = path.startswith(BUILTIN_ENGINE_PREFIX)
        label = str(name or "").strip() or Path(self.resolve_engine_tag(path) or path).stem
        engines = [dict(item) for item in (self.settings.get("engines") or [])
                   if str(item.get("path")) != path]
        entry: Dict[str, object] = {"path": path, "name": label}
        if builtin:
            entry["builtin"] = True
        engines.insert(0, entry)
        self.settings["engines"] = engines[:12]                # 最多记住 12 个

    def remove_engine(self, path: str) -> bool:
        """从列表里删掉一个引擎（如果它正在运行就顺便卸载）。"""
        path = self.engine_tag(str(path or ""))
        engines = [dict(item) for item in (self.settings.get("engines") or [])]
        kept = [item for item in engines if str(item.get("path")) != path]
        if len(kept) == len(engines):
            return False
        self.settings["engines"] = kept
        if self._engine_key(self.engine.path or "") == self._engine_key(path):
            self.unload_engine()
        self._notify(f"已移除引擎：{self.engine_label(path) or Path(path).name}")
        self._bump_state()
        return True

    def switch_engine(self, path: str) -> bool:
        """切换到另一个引擎（选项按引擎分别记住并自动恢复）。"""
        if not self.load_engine(str(path)):
            return False
        self._notify(f"已切换引擎：{self.engine.name}（{Path(str(path)).name}）")
        return True

    def unload_engine(self) -> None:
        self._stop_timer()
        self.pending = None
        self.engine.stop_process()
        self.analysis = self._empty_analysis()
        self.broadcast({"type": "engine", "engine": self.engine_status()})
        self._bump_state()

    def bundled_engine_path(self) -> Optional[Path]:
        """内置皮卡鱼引擎的路径。

        ★ PyInstaller 打包后，随 exe 一起打进去的资源会被解包到 ``sys._MEIPASS``
        （临时目录，见 :func:`bundle_dir`）。所以**直接从这个临时目录加载**即可，
        不需要用户在旁边放任何文件；引擎与它的权重 ``pikafish.nnue`` 在同一个目录里，
        皮卡鱼能自己找到权重。

        顺序：``_MEIPASS/engine``（打包内置）→ ``exe 旁边/engine``（绿色版或用户自己放的）
        → ``_MEIPASS`` 根目录。
        """
        bases: List[Path] = []
        try:
            bases.append(bundle_dir() / "engine")
            bases.append(exe_dir() / "engine")
            bases.append(bundle_dir())
        except Exception:  # noqa: BLE001
            pass
        for base in bases:
            for name in BUNDLED_ENGINE_NAMES:
                candidate = base / name
                try:
                    if candidate.is_file():
                        return candidate
                except OSError:
                    continue
        return None

    def is_bundled_engine_path(self, path: str) -> bool:
        """这个路径是不是"打包在 exe 里"的那个内置皮卡鱼。

        判断依据是文件名对得上、而且位置就在打包解包目录（``sys._MEIPASS``）里；
        用户自己放在别处的同引擎**不算**内置（照常按用户选的路径处理）。
        """
        text = str(path or "").strip().strip('"')
        if not text:
            return False
        if text.startswith(BUILTIN_ENGINE_PREFIX):
            return True
        name = Path(text).name
        if name.lower() not in {item.lower() for item in BUNDLED_ENGINE_NAMES}:
            return False
        try:
            candidate = Path(text).resolve()
        except OSError:
            return False
        for base in (bundle_dir() / "engine", bundle_dir()):
            try:
                if candidate == (base / name).resolve():
                    return True
            except OSError:
                continue
        return False

    def engine_tag(self, path: str) -> str:
        """把引擎路径转成"能记住"的写法：内置引擎 → ``@builtin/文件名``。"""
        text = str(path or "").strip().strip('"')
        if not text:
            return ""
        if text.startswith(BUILTIN_ENGINE_PREFIX):
            return text
        if self.is_bundled_engine_path(text):
            return f"{BUILTIN_ENGINE_PREFIX}{Path(text).name}"
        return text

    def resolve_engine_tag(self, path: str) -> str:
        """把 :meth:`engine_tag` 记住的标记还原成（本次运行）真实的引擎文件路径。"""
        text = str(path or "").strip().strip('"')
        if not text:
            return ""
        if not text.startswith(BUILTIN_ENGINE_PREFIX):
            return text
        name = Path(text[len(BUILTIN_ENGINE_PREFIX):]).name
        bases = [bundle_dir() / "engine", bundle_dir(),
                 exe_dir() / "engine", exe_dir()]
        if name:
            for base in bases:
                candidate = base / name
                try:
                    if candidate.is_file():
                        return str(candidate)
                except OSError:
                    continue
        bundled = self.bundled_engine_path()
        return str(bundled) if bundled is not None else ""

    def engine_label(self, path: str) -> str:
        """给界面/提示用的人话名字（内置引擎不用显示临时目录路径）。"""
        text = str(path or "")
        if text.startswith(BUILTIN_ENGINE_PREFIX) or self.is_bundled_engine_path(text):
            return "内置皮卡鱼"
        return text

    def startup_engine_path(self, explicit: str = "") -> str:
        """启动时该自动加载哪个引擎。

        优先级：命令行指定 → **用户上次手动选的**（设置记忆，文件还在才用）
        → **打包在 exe 里的皮卡鱼**（第一次打开就是它）→ 常见位置自动查找。
        """
        explicit = str(explicit or "").strip().strip('"')
        if explicit:
            return self.resolve_engine_tag(explicit)
        saved = str(self.settings.get("engine_path") or "").strip().strip('"')
        if saved:
            resolved = self.resolve_engine_tag(saved)
            try:
                if resolved and Path(resolved).is_file():
                    return resolved
            except OSError:
                pass
        bundled = self.bundled_engine_path()
        if bundled is not None:
            return str(bundled)
        found = self.autodetect_engine()
        return found[0] if found else ""

    def _unpack_bundled_engine(self) -> Optional[Path]:
        """兼容旧名字：返回内置引擎路径（**直接读 _MEIPASS**，不再往 exe 旁边复制）。"""
        return self.bundled_engine_path()

    def autodetect_engine(self) -> List[str]:
        """在常见位置寻找皮卡鱼引擎。"""
        found: List[str] = []
        # 优先用 exe 里打包的皮卡鱼（直接从解包目录 sys._MEIPASS 加载）
        bundled = self.bundled_engine_path()
        if bundled is not None:
            found.append(str(bundled))
        candidates: List[Path] = []
        for name in ("pikafish", "pikafish-avx2", "pikafish-bmi2", "pikafish-sse41-popcnt"):
            which = shutil.which(name)
            if which:
                candidates.append(Path(which))
        roots = [APP_ROOT, APP_ROOT / "engine", exe_dir(), exe_dir() / "engine",
                 bundle_dir(), bundle_dir() / "engine",   # exe 里打包的引擎
                 Path.cwd(), Path.home(),
                 Path.home() / "Documents", Path.home() / "Downloads", Path.home() / "Desktop",
                 Path.home() / "静夜象棋界面"]
        patterns = ("pikafish*.exe", "Pikafish*.exe", "pikafish*")
        for root in roots:
            try:
                if not root.is_dir():
                    continue
                for pattern in patterns:
                    for item in root.glob(pattern):
                        candidates.append(item)
                    for item in root.glob(f"*/{pattern}"):
                        candidates.append(item)
            except OSError:
                continue
        for path in candidates:
            try:
                if path.is_file() and path.suffix.lower() in (".exe", ""):
                    resolved = str(path.resolve())
                    if resolved not in found:
                        found.append(resolved)
            except OSError:
                continue
        return found[:12]

    def browse(self, path: str = "") -> Dict[str, object]:
        """给界面用的简易文件浏览器（只列目录与可执行文件）。"""
        entries: List[Dict[str, str]] = []
        raw = (path or "").strip().strip('"')
        if not raw:
            if os.name == "nt":
                drives = [f"{chr(letter)}:\\" for letter in range(65, 91)
                          if Path(f"{chr(letter)}:\\").exists()]
                for drive in drives:
                    entries.append({"name": drive, "path": drive, "kind": "dir"})
                return {"path": "", "parent": "", "entries": entries}
            raw = str(Path.home())
        target = Path(raw).expanduser()
        if target.is_file():
            target = target.parent
        if not target.is_dir():
            return {"path": raw, "parent": "", "entries": [], "error": "目录不存在"}
        try:
            for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                name = item.name
                if name.startswith(".") or name.startswith("$"):
                    continue
                if item.is_dir():
                    entries.append({"name": name, "path": str(item), "kind": "dir"})
                elif item.suffix.lower() in (".exe", "") and item.is_file():
                    entries.append({"name": name, "path": str(item), "kind": "file"})
        except OSError as exc:
            return {"path": str(target), "parent": "", "entries": [], "error": str(exc)}
        parent = str(target.parent) if target.parent != target else ""
        return {"path": str(target), "parent": parent, "entries": entries}

    def update_settings(self, patch: Dict[str, object]) -> None:
        patch = dict(patch or {})
        if "engine_mode" in patch:
            self.set_engine_mode(str(patch.pop("engine_mode")))
            if not patch:
                return
        engine_changed = False
        book_changed = False
        legacy_options: Dict[str, object] = {}
        with self.lock:
            before = dict(self.settings)
            for key, value in patch.items():
                if key in ("mode", "notation", "flip", "human_side", "difficulty", "movetime_ms",
                           "depth_limit", "infinite", "auto_analyze", "multi_pv", "threads",
                           "hash_mb", "show_wdl", "engine_path", "play_delay_ms",
                           "book_enabled", "book_strategy", "book_max_ply",
                           "show_arrows", "show_numbers", "show_statusbar",
                           "piece_shadow",
                           "board_theme", "board_scale", "board_skin",
                           "background",
                           "sound_enabled", "cloud_learn", "cloud_timeout",
                           "cloud_enabled", "link_scan_seconds", "link_click_delay_ms",
                           "link_move_delay_ms", "link_back_mode", "link_auto_confirm",
                           "link_confirm_frames", "link_strict_validate",
                           "link_confirm_seconds", "link_smart_confirm",
                           "link_model_path",
                           "link_orientation",
                           "link_area_x", "link_area_y", "link_area_w", "link_area_h"):
                    self.settings[key] = value
            changed = {key for key in self.settings if before.get(key) != self.settings.get(key)}
            # 兼容旧界面（浏览器版）按 mode / human_side 切换的写法，同步出 engine_mode
            if {"mode", "human_side"} & changed:
                if self.settings.get("mode") == "play":
                    self.settings["engine_mode"] = (
                        "red" if self.settings.get("human_side") == BLACK else "black")
                elif self.settings.get("mode") in ("analyze", "study"):
                    self.settings["engine_mode"] = "analyze"
            if {"threads", "hash_mb", "multi_pv", "show_wdl"} & changed:
                engine_changed = True
                # 兼容旧的扁平设置（浏览器版界面仍在用）：映射到引擎自己的 UCI 选项
                if "threads" in changed:
                    legacy_options["Threads"] = self.settings.get("threads")
                if "hash_mb" in changed:
                    legacy_options["Hash"] = self.settings.get("hash_mb")
                if "multi_pv" in changed:
                    legacy_options["MultiPV"] = self.settings.get("multi_pv")
                if "show_wdl" in changed:
                    legacy_options["UCI_ShowWDL"] = bool(self.settings.get("show_wdl"))
            if {"book_enabled", "book_strategy", "book_max_ply"} & changed:
                self._sync_book_settings()
                book_changed = True
            if {"cloud_learn", "cloud_timeout", "cloud_enabled"} & changed:
                self.cloud.learn = bool(self.settings.get("cloud_learn", True))
                try:
                    self.cloud.timeout = float(self.settings.get("cloud_timeout") or 3.0)
                except (TypeError, ValueError):
                    self.cloud.timeout = 3.0
        if book_changed:
            self._bump_state()
        if engine_changed:
            self._apply_engine_settings()
            if self.engine.is_running():
                for name, value in legacy_options.items():
                    if self.engine.option(name) is not None:
                        self.set_engine_option(name, value)     # 立即生效
                self.engine.new_game()
        with self.lock:
            # 纯界面显示设置不需要重算局面（否则分析模式会被无谓地打断）
            # 连线设置同理：改扫描间隔/棋盘区域不该把用户的局面分析打断
            view_only = bool(changed) and changed <= (VIEW_SETTING_KEYS | LINK_SETTING_KEYS)
            if changed and not view_only:
                self._after_position_change(force=True)
            else:
                self._bump_state()

    # ================================================================== 分析 / 对战
    def _stop_timer(self) -> None:
        timer = self._auto_analyze_timer
        self._auto_analyze_timer = None
        if timer is not None:
            timer.cancel()

    def _after_position_change(self, force: bool = True, played_by: str = "human") -> None:
        """局面变化后统一入口：刷新界面、安排分析或让引擎走棋。"""
        self.analysis = self._empty_analysis()
        self._engine_move_retries = 0
        self.broadcast({"type": "analysis", "analysis": self._analysis_payload()})
        self._bump_state()
        self._stop_timer()
        self._query_cloud_async()
        delay = 0.05 if played_by != "human" else 0.12
        timer = threading.Timer(delay, self._start_search)
        timer.daemon = True
        self._auto_analyze_timer = timer
        timer.start()

    def _start_search(self) -> None:
        with self.lock:
            self._auto_analyze_timer = None
            if not self.engine.is_running():
                return
            board = self.tree.board_at()
            over = board.result() is not None
            mode = str(self.settings.get("mode", "study"))
            engine_to_move = (mode == "play"
                              and board.side != self.settings.get("human_side", RED))
            if engine_to_move and not over:
                self._start_engine_move(board)
                return
            if over or not self.settings.get("auto_analyze", True):
                # 只有"自动分析"发起的搜索需要停下；用户点「开始分析」发起的搜索要跑完
                if self.engine.searching and not self.analysis.get("explicit"):
                    self.engine.stop()
                return
            self._start_analysis(board)

    def _start_analysis(self, board: Optional[Board] = None) -> None:
        board = board or self.tree.board_at()
        self.analysis = self._empty_analysis()
        self.analysis["fen"] = board.to_fen()
        self.analysis["side"] = board.side
        self.analysis["explicit"] = True      # 用户主动发起的分析（不随自动分析策略停止）
        if self.settings.get("infinite"):
            limit = "infinite"
        else:
            limit = f"movetime {int(self.settings.get('movetime_ms', 1500) or 1500)}"
            depth = int(self.settings.get("depth_limit", 0) or 0)
            if depth > 0:
                limit += f" depth {depth}"
        token = {"kind": "analyze", "node": self.tree.current.id}

        def register(search_id: int) -> None:
            self.analysis["searchId"] = search_id
            self.analysis["token"] = token

        self._analysis_board = board.copy()
        self._last_info_work = 0.0
        try:
            self.engine.analyze(board.to_fen(), limit=limit,
                multipv=int(self.settings.get("multi_pv", 1) or 1),
                                on_start=register)
        except EngineError as exc:
            self._notify(str(exc), "error")
            return
        self.broadcast({"type": "analysis", "analysis": self._analysis_payload()})

    def analyze_now(self) -> None:
        with self.lock:
            if not self.engine.is_running():
                self._notify("请先加载引擎", "warn")
                return
            self._stop_timer()
            self._start_analysis()

    def stop_analysis(self) -> None:
        with self.lock:
            self._stop_timer()
            if self.engine.searching:
                self.engine.stop()
            self.broadcast({"type": "analysis", "analysis": self._analysis_payload()})

    def _start_engine_move(self, board: Optional[Board] = None) -> None:
        board = board or self.tree.board_at()
        # 开局库优先：库里有明确着法时直接走库着，更像人类开局
        if self.book.use_in_game and self.book.loaded:
            ply = int(getattr(self.tree.current, "ply", 0) or 0)
            try:
                hit = self.book.best_move(board, ply)     # 超过脱谱步数就返回 None
            except Exception:  # noqa: BLE001
                hit = None
            if hit is not None and hit.count > 0 and board.find_move_iccs(hit.move):
                node_id = self.tree.current.id
                timer = threading.Timer(0.4, self._play_book_move, args=(hit.move, node_id))
                timer.daemon = True
                timer.start()
                self._notify(f"用开局库着法：{hit.move}"
                             f"（{hit.book or hit.source}，命中 {hit.count} 次）")
                self.analysis["running"] = False
                self.analysis["bestmove"] = hit.move
                self.broadcast({"type": "analysis", "analysis": self._analysis_payload()})
                return
            # 本地库没有命中时，试一下云库（有缓存则秒回）
            cloud_move = self.cloud_best_move(board)
            if cloud_move and board.find_move_iccs(cloud_move):
                node_id = self.tree.current.id
                timer = threading.Timer(0.4, self._play_book_move, args=(cloud_move, node_id))
                timer.daemon = True
                timer.start()
                self._notify(f"用云库着法：{cloud_move}")
                self.analysis["running"] = False
                self.analysis["bestmove"] = cloud_move
                self.broadcast({"type": "analysis", "analysis": self._analysis_payload()})
                return
        # 引擎棋力完全交给引擎：思考时间 / 深度用右栏「分析」面板的搜索范围，
        # 线程、哈希、MultiPV 等则由引擎自己的 UCI 选项决定（「引擎设置」里改）。
        movetime = max(200, int(self.settings.get("movetime_ms", 1500) or 1500))
        depth = max(0, int(self.settings.get("depth_limit", 0) or 0))
        randomness = 1
        limit = f"movetime {movetime}"
        if depth:
            limit += f" depth {depth}"
        multipv = max(1, randomness)
        node_id = self.tree.current.id
        fen = board.to_fen()
        self.analysis = self._empty_analysis()
        self.analysis["fen"] = fen
        self.analysis["side"] = board.side

        def register(search_id: int) -> None:
            self.analysis["searchId"] = search_id
            self.analysis["token"] = {"kind": "play", "node": node_id}
            self.pending = {"kind": "play", "node": node_id, "searchId": search_id,
                            "randomness": randomness, "choices": {}, "fen": fen}

        self._analysis_board = board.copy()
        self._last_info_work = 0.0
        try:
            self.engine.analyze(fen, limit=limit, multipv=multipv, on_start=register)
        except EngineError as exc:
            self._notify(str(exc), "error")
            return
        self._notify(f"引擎思考中（每步 {movetime / 1000:.1f} 秒）")
        self.broadcast({"type": "engine", "engine": self.engine_status()})

    def _play_book_move(self, iccs: str, node_id: int) -> None:
        with self.lock:
            if self.tree.current.id != node_id:
                return
            node = self.tree.play_iccs(iccs)
            if node is None:
                return
            self._redo_stack = []
            self._bump_tree()
            self._notify(f"开局库：{node.cn}（{node.iccs}）")
            self._after_position_change(played_by="engine")

    def engine_move_now(self) -> None:
        with self.lock:
            if not self.engine.is_running():
                self._notify("请先加载引擎", "warn")
                return
            board = self.tree.board_at()
            if board.result() is not None:
                self._notify("棋局已结束", "warn")
                return
            if self.engine.searching:
                self.engine.stop()
            self._start_engine_move(board)

    def _choose_engine_move(self, pending: Dict[str, object], bestmove: str) -> Optional[str]:
        choices = list((pending.get("choices") or {}).values())
        randomness = int(pending.get("randomness", 1) or 1)
        if randomness > 1 and len(choices) > 1:
            import random
            pool = choices[:randomness]
            weights = [3] + [1] * (len(pool) - 1)
            return random.choices(pool, weights=weights, k=1)[0]
        if choices:
            return str(choices[0])
        return bestmove or None

    # ================================================================== 引擎事件
    def _on_engine_event(self, event: Dict[str, object]) -> None:
        etype = event.get("type")
        if etype == EVENT_OPTIONS:
            self.engine_options = list(event.get("options") or [])  # type: ignore[arg-type]
            self.broadcast({"type": "engine", "engine": self.engine_status()})
            return
        if etype == EVENT_STATUS:
            if event.get("state") == STATUS_ERROR:
                # 引擎报错（例如拒绝某个局面、进程异常）时，别让人机对战一直"思考中"
                if self.pending is not None:
                    self.pending = None
                    self.analysis["running"] = False
                    self._notify(f"引擎出错：{event.get('detail')}", "error")
            self.broadcast({"type": "engine", "engine": self.engine_status()})
            return
        if etype == EVENT_INFO:
            self._handle_info(event)
            return
        if etype == EVENT_BESTMOVE:
            self._handle_bestmove(event)
            return
        if etype == EVENT_LOG:
            return

    def _handle_info(self, event: Dict[str, object]) -> None:
        # 引擎每秒可能推送成千上万行 info，这里只做限频处理，
        # 否则 Python 端的解析与记谱换算会拖慢整个界面。
        now = time.time()
        if event.get("search_id") != self.analysis.get("searchId"):
            return
        board = self._analysis_board
        if board is None:
            return
        index = int(event.get("multipv", 1) or 1)
        pv = event.get("pv") or []
        pending = self.pending
        if pending is not None and pending.get("kind") == "play" and pv:
            with self.lock:
                choices = dict(pending.get("choices") or {})
                choices[index] = pv[0]
                pending["choices"] = choices
        # 限频只在同一条推荐路线（multipv 分支）内生效：
        # 引擎每次深度的 1/2/3 条路线是紧接着发出的，不能被一起丢掉，
        # 否则界面上只会剩下第一条推荐着法。
        if (now - self._last_info_work < 0.1 and self.analysis.get("lines")
                and index == self._last_info_multipv):
            return
        self._last_info_work = now
        self._last_info_multipv = index
        with self.lock:
            if event.get("search_id") != self.analysis.get("searchId"):
                return
            line = self._make_line(board, index, event)
            lines = {int(item["multipv"]): item for item in self.analysis.get("lines", [])}  # type: ignore[union-attr]
            lines[index] = line
            self.analysis["lines"] = [lines[key] for key in sorted(lines)]
            depth = int(event.get("depth", 0) or 0)
            self.analysis["depth"] = depth
            self.analysis["seldepth"] = int(event.get("seldepth", 0) or 0)
            self.analysis["nodes"] = int(event.get("nodes", 0) or 0)
            self.analysis["nps"] = int(event.get("nps", 0) or 0)
            self.analysis["time"] = int(event.get("time", 0) or 0)
            history = list(self.analysis.get("history") or [])
            if line.get("scoreCp") is not None:
                if not history or history[-1][0] != depth:
                    history.append([depth, line["scoreCp"]])
                    self.analysis["history"] = history[-240:]
            wdl = line.get("wdl")
            if isinstance(wdl, (list, tuple)) and len(wdl) == 3:
                wdl_history = list(self.analysis.get("wdl_history") or [])
                if not wdl_history or wdl_history[-1][0] != depth:
                    total = max(1, sum(int(value) for value in wdl))
                    rate = (int(wdl[0]) + int(wdl[1]) * 0.5) / total
                    wdl_history.append([depth, round(rate * 100, 1)])
                    self.analysis["wdl_history"] = wdl_history[-240:]
            if now - self._last_info_emit >= 0.12:
                self._last_info_emit = now
                self.broadcast({"type": "analysis", "analysis": self._analysis_payload()})

    def _handle_bestmove(self, event: Dict[str, object]) -> None:
        with self.lock:
            if event.get("cancelled"):
                return  # 被中止的搜索返回的着法，直接丢弃
            bestmove = str(event.get("move") or "")
            pending = self.pending
            expected = (pending.get("searchId") if pending is not None
                        else self.analysis.get("searchId"))
            if expected is None or event.get("search_id") != expected:
                return  # 上一次搜索的过期结果
            self.analysis["bestmove"] = bestmove
            self.pending = None
            self.analysis["running"] = False
            if pending is None:
                self.broadcast({"type": "analysis", "analysis": self._analysis_payload()})
                return
            if pending.get("kind") != "play":
                self.broadcast({"type": "analysis", "analysis": self._analysis_payload()})
                return
            if self.tree.current.id != pending.get("node"):
                return  # 局面已经变化，丢弃这次思考结果
            chosen = self._choose_engine_move(pending, bestmove)
            if not chosen:
                self._notify("引擎没有给出着法", "warn")
                self._bump_state()
                return
            node = self.tree.play_iccs(chosen)
            if node is None:
                if self._engine_move_retries < 1:
                    # 极少见：引擎给出的着法在当前局面非法（例如旧版本引擎或外部改动），
                    # 重新请求一次，避免人机对战卡住。
                    self._engine_move_retries += 1
                    self._notify(f"引擎给出的着法 {chosen} 不合法，正在重新思考", "warn")
                    board = self.tree.board_at()
                    retry = threading.Timer(0.05, self._start_engine_move, args=(board,))
                    retry.daemon = True
                    retry.start()
                    self._bump_state()
                    return
                self._notify(f"引擎返回了非法着法：{chosen}", "error")
                self._bump_state()
                return
            self._redo_stack = []
            self._bump_tree()
            self._notify(f"引擎：{node.cn}（{node.iccs}）"
                         + ("，将军" if node.is_check and not node.is_mate else "")
                         + ("，绝杀" if node.is_mate else ""))
            self.notify_move(node, "engine")     # 连线功能：把这步点到对方平台
            self._after_position_change(played_by="engine")

    # ================================================================== 分析数据
    def _make_line(self, board: Board, index: int, info: Dict[str, object]) -> Dict[str, object]:
        side = board.side
        score = dict(info.get("score") or {})  # type: ignore[arg-type]
        cp = score.get("cp")
        mate = score.get("mate")
        cp_red = None if cp is None else (int(cp) if side == RED else -int(cp))
        mate_red = None if mate is None else (int(mate) if side == RED else -int(mate))
        pv_iccs = [str(move) for move in (info.get("pv") or [])]
        pv_cn = self._pv_notation(board, pv_iccs)
        wdl = info.get("wdl")
        if isinstance(wdl, (list, tuple)) and len(wdl) == 3:
            win, draw, loss = (int(value) for value in wdl)
            if side != RED:
                win, loss = loss, win
            wdl_red = [win, draw, loss]
        else:
            wdl_red = None
        return {
            "multipv": index,
            "depth": int(info.get("depth", 0) or 0),
            "seldepth": int(info.get("seldepth", 0) or 0),
            "scoreCp": cp_red,
            "scoreMate": mate_red,
            "scoreText": self._score_text(cp_red, mate_red),
            "judgement": self._judgement(cp_red, mate_red),
            "wdl": wdl_red,
            "pvIccs": pv_iccs,
            "pvCn": pv_cn,
            "pvText": " ".join(pv_cn) if pv_cn else "",
            "nodes": int(info.get("nodes", 0) or 0),
            "nps": int(info.get("nps", 0) or 0),
            "time": int(info.get("time", 0) or 0),
            "bound": "lowerbound" if score.get("lowerbound") else
                     ("upperbound" if score.get("upperbound") else ""),
        }

    @staticmethod
    def _score_text(cp_red: Optional[int], mate_red: Optional[int]) -> str:
        if mate_red is not None:
            if mate_red == 0:
                return "已被将死"
            side = "红方" if mate_red > 0 else "黑方"
            return f"{side}{abs(mate_red)} 步杀"
        if cp_red is None:
            return "—"
        # 皮卡鱼给的 cp 就是它自己的分值（几十到几百），直接按整数显示，不再换算成"兵"
        return f"{cp_red:+d}"

    @staticmethod
    def _judgement(cp_red: Optional[int], mate_red: Optional[int]) -> str:
        if mate_red is not None:
            return "红方胜势" if mate_red > 0 else "黑方胜势"
        if cp_red is None:
            return "—"
        value = abs(cp_red)
        if value < 30:
            return "均势"
        side = "红方" if cp_red > 0 else "黑方"
        if value < 90:
            return f"{side}稍优"
        if value < 200:
            return f"{side}占优"
        if value < 500:
            return f"{side}优势"
        return f"{side}大优"

    def _pv_notation(self, board: Board, pv_iccs: List[str], limit: int = 12) -> List[str]:
        """把引擎给出的 PV 翻译成中文记谱。"""
        if not pv_iccs:
            return []
        work = board.copy()
        out: List[str] = []
        for text in pv_iccs[:limit]:
            move = work.find_move_iccs(text)
            if move is None:
                break
            out.append(nt.to_chinese(work, move))
            work.push(move)
        return out
