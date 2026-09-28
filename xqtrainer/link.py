# -*- coding: utf-8 -*-
"""「连线」功能：把本程序连到别的象棋平台窗口上（自动读盘 + 自动走子）。

实现思路与开源项目 TCHESS（sojourners/public-Xiangqi）一致：

1. **选窗口**：用户点「连线」后把系统光标换成圆圈，点哪个窗口就选哪个（WindowFromPoint）；
2. **后台截图**：Windows 的 ``PrintWindow`` 让目标窗口自己重绘一份到内存 DC，
   所以窗口被挡住、不是前台也能截到；
3. **识别棋子**：截图 → 识别出 10×9 的棋盘数组（优先用 TCHESS 的 YOLOv11 ONNX 模型；
   没有 onnxruntime 时退回模板匹配，保证功能可用）；
4. **自动走子**：用 ``PostMessage`` 给目标窗口发鼠标消息（移动/按下/抬起），
   客户区坐标 + DPI 反缩放，不动真实鼠标、不抢焦点；
5. **主循环**：每隔 N 毫秒截图识别一次，和本程序的棋盘比对，得出对方走了哪一步；
   轮到引擎走时把招法点出去。

本模块只负责"看屏幕 / 点屏幕"，棋规与引擎仍由 :mod:`xqtrainer.session` 负责。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

IS_WINDOWS = hasattr(ctypes, "windll")


#: DPI 感知级别（和 Windows 的 DPI_AWARENESS 一致）：
#: 0＝不感知（整个进程被系统按「系统 DPI」虚拟化缩放）、1＝系统级、2＝每显示器（含 V2）。
AWARENESS_UNAWARE = 0
AWARENESS_SYSTEM = 1
AWARENESS_PER_MONITOR = 2


def _load_shcore():
    """shcore.dll（GetProcessDpiAwareness 在这里，不在 user32）。拿不到就返回 None。"""
    try:
        return ctypes.windll.shcore
    except Exception:  # noqa: BLE001
        return None


class _DevMode(ctypes.Structure):
    """DEVMODEW 里我们关心的那几个字段（EnumDisplaySettings 用来看真实分辨率）。"""
    _fields_ = [
        ("dmDeviceName", ctypes.wintypes.WCHAR * 32),
        ("dmSpecVersion", ctypes.wintypes.WORD),
        ("dmDriverVersion", ctypes.wintypes.WORD),
        ("dmSize", ctypes.wintypes.WORD),
        ("dmDriverExtra", ctypes.wintypes.WORD),
        ("dmFields", ctypes.wintypes.DWORD),
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", ctypes.wintypes.DWORD),
        ("dmDisplayFixedOutput", ctypes.wintypes.DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.wintypes.WCHAR * 32),
        ("dmLogPixels", ctypes.wintypes.WORD),
        ("dmBitsPerPel", ctypes.wintypes.DWORD),
        ("dmPelsWidth", ctypes.wintypes.DWORD),
        ("dmPelsHeight", ctypes.wintypes.DWORD),
        ("dmDisplayFlags", ctypes.wintypes.DWORD),
        ("dmDisplayFrequency", ctypes.wintypes.DWORD),
        ("dmICMMethod", ctypes.wintypes.DWORD),
        ("dmICMIntent", ctypes.wintypes.DWORD),
        ("dmMediaType", ctypes.wintypes.DWORD),
        ("dmDitherType", ctypes.wintypes.DWORD),
        ("dmReserved1", ctypes.wintypes.DWORD),
        ("dmReserved2", ctypes.wintypes.DWORD),
        ("dmPanningWidth", ctypes.wintypes.DWORD),
        ("dmPanningHeight", ctypes.wintypes.DWORD),
    ]


def compute_message_scale(own_awareness: int, target_awareness: int,
                          target_dpi: float, system_dpi: float) -> float:
    """算「我们看到的客户区坐标 → 目标窗口自己的客户区坐标」要乘的系数。

    ★ 为什么需要这一步：Windows 只对**窗口**做 DPI 虚拟化，跨进程的
    ``PostMessage`` 送进去的坐标**不做任何换算**。两个进程的"1 个坐标单位"
    代表的物理像素可能不一样：

    * 本程序 DPI 不感知（Tkinter 默认）→ 1 单位 = 系统DPI/96 个物理像素
      （2560x1440 + 150% 缩放时 = 1.5 个物理像素，整个界面被系统放大 1.5 倍）；
    * 目标窗口 DPI 感知（天天象棋等新版客户端）→ 1 单位 = 1 个物理像素；
      两边对不上，不换算就会"点偏 1/3 个棋盘"、越下越乱。
    * 两边都不感知 → 1 单位都是 1 个物理像素，系数正好 1.0。
    * 本程序感知、目标不感知（反向）→ 系数 = 96/目标DPI，需要缩小。

    系数 = 我们 1 单位 / 对方 1 单位 = 要把我们的坐标乘几倍对方才认。
    1080p + 100% 缩放时两边都是 1 → 返回 1.0，与以前行为完全一致。
    """
    try:
        system_dpi = float(system_dpi) or 96.0
    except (TypeError, ValueError):
        system_dpi = 96.0
    try:
        target_dpi = float(target_dpi) or system_dpi
    except (TypeError, ValueError):
        target_dpi = system_dpi
    try:
        aware = int(own_awareness or 0) > 0
        target = int(target_awareness or 0) > 0
    except (TypeError, ValueError):
        aware, target = False, False
    unit_ours = 1.0 if aware else system_dpi / 96.0        # 物理像素/我们的1单位
    unit_theirs = 1.0 if target else target_dpi / 96.0     # 物理像素/对方的1单位
    return max(0.25, min(8.0, unit_ours / unit_theirs))


def debug_log(message: str) -> None:
    """把连线模块的关键信息写进 exe 旁边的 link_debug.log（便于排查"为什么退回模板匹配"）。"""
    try:
        from .session import exe_dir

        path = Path(exe_dir()) / "link_debug.log"
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")
    except Exception:  # noqa: BLE001
        pass


# ==================================================================== 局面校验
#: 每种子力的数量上限（车马炮相/象士各 2、兵/卒 5、将/帅各 1）
PIECE_LIMITS = {"r": 2, "n": 2, "c": 2, "b": 2, "a": 2, "k": 1, "p": 5}

#: ONNX Runtime 的推理线程数。实测（本机、同一张 1416x939 截图）：
#: 1 线程 224ms｜2 线程 150ms｜4 线程 84ms｜8 线程 55ms → 用 4 线程性价比最好。
INFER_THREADS = 4


def _positions(board, letter: str) -> List[Tuple[int, int]]:
    return [(row, col) for row in range(len(board)) for col in range(len(board[row]))
            if board[row][col] == letter]


def _fingerprint(image) -> Optional[bytes]:
    """截图的像素指纹：用来判断"画面和上一帧是不是一模一样"。

    一样就说明目标窗口没变，模型输出必然也一样 → 可以直接沿用上一帧的识别结果，
    省掉一次 84~150ms 的推理（实测 tobytes 约 3.6ms、比较约 0.2ms，净值很划算）。
    """
    try:
        return image.tobytes()
    except Exception:  # noqa: BLE001
        return None


def validate_board(board) -> Optional[str]:
    """检查识别出来的局面"像不像一盘合法的棋"。

    返回 ``None`` 表示通过；否则返回一句简短原因（会写进 link_debug.log，
    并且这一帧作废、等下一帧重认）。检查项：

    * 棋盘必须是 10×9；
    * 每种子力数量上限：车/马/炮/相(象)/士 各 2、兵(卒) 5、将(帅) 各 1；
    * 双方将帅都必须存在；
    * 士不能出九宫；
    * 相/象不能过河（必须在本方半场）；
    * 棋子总数 ≤ 32。

    ★ 校验必须"与朝向无关"：识别结果可能是红方在上、也可能红方在下，
    所以先由红帅的位置判断红方在哪半边，再按这个判断校验士/象。
    """
    if not board or len(board) != 10 or any(len(row) != 9 for row in board):
        return "棋盘尺寸不对"
    counts: Dict[str, int] = {}
    for row in board:
        for cell in row:
            if cell and cell != ".":
                counts[cell] = counts.get(cell, 0) + 1
    total = sum(counts.values())
    if total < 2:
        return "棋子太少"
    for kind, limit in PIECE_LIMITS.items():
        for letter in (kind, kind.upper()):
            count = counts.get(letter, 0)
            if count > limit:
                return f"{letter}×{count}>{limit}"
    if counts.get("k", 0) != 1 or counts.get("K", 0) != 1:
        return "将帅必须各一个"
    if total > 32:
        return f"棋子总数 {total}>32"
    red_king = _positions(board, "K")[0]
    black_king = _positions(board, "k")[0]
    red_top = red_king[0] <= 4                      # 红方在上半场？
    if red_top == (black_king[0] <= 4):
        return "红帅与黑将在同一半场"
    red_palace = (0, 1, 2) if red_top else (7, 8, 9)
    black_palace = (7, 8, 9) if red_top else (0, 1, 2)
    if red_king[1] not in (3, 4, 5) or red_king[0] not in red_palace:
        return "红帅不在九宫"
    if black_king[1] not in (3, 4, 5) or black_king[0] not in black_palace:
        return "黑将不在九宫"
    for rows, letter, label in ((red_palace, "A", "红士"), (black_palace, "a", "黑士")):
        for row, col in _positions(board, letter):
            if col not in (3, 4, 5) or row not in rows:
                return f"{label}出九宫({row},{col})"
    red_half = range(0, 5) if red_top else range(5, 10)
    black_half = range(5, 10) if red_top else range(0, 5)
    for rows, letter, label in ((red_half, "B", "红相"), (black_half, "b", "黑象")):
        for row, col in _positions(board, letter):
            if row not in rows:
                return f"{label}过河({row},{col})"
    return None


def _extra_sys_paths() -> List[str]:
    """打包后 onnxruntime 可能被放到 _MEIPASS / exe 目录，补进 sys.path 再导入一次。"""
    import sys

    paths = []
    try:
        from .session import bundle_dir, exe_dir

        paths += [str(bundle_dir()), str(bundle_dir() / "onnxruntime"),
                  str(exe_dir()), str(exe_dir() / "onnxruntime")]
    except Exception:  # noqa: BLE001
        pass
    return [item for item in paths if item not in sys.path]

#: 模型文件（TCHESS 的 YOLOv11 权重；放在 engine/ 或程序目录即可自动识别）
MODEL_NAMES = ("yolov11.onnx", "model/yolov11.onnx", "yolo5-vin.onnx")

#: 对方棋盘朝向（「连线设置」下拉框用）：值 → 中文说明
ORIENTATIONS = (
    ("auto", "自动（按我方执红 / 执黑推断）"),
    ("same", "与本程序一致（不翻）"),
    ("lr+ud", "上下 + 左右翻转（180°，执黑常见）"),
    ("ud", "只上下翻转"),
    ("lr", "只左右镜像"),
)


def orientation_label(mode: str) -> str:
    """朝向代码 → 中文说明（日志 / 状态栏用）。"""
    for value, label in ORIENTATIONS:
        if value == (mode or "same"):
            return label.split("（")[0]
    return mode or "与本程序一致"


def find_model_file(*extra_dirs) -> Optional[Path]:
    """找一个可用的 YOLO 模型文件（优先放在程序目录/engine 目录里）。"""
    from .session import bundle_dir, exe_dir

    # ★ 一定要同时搜 "程序目录" 和 "程序目录/model、程序目录/engine"：
    #   打包成 exe 后模型在解包目录的 model\ 里，漏掉这一步就会"找不到模型→
    #   退回模板匹配"，表现就是"连线了但识别不动"。
    roots = [Path(item) for item in extra_dirs if item]
    for base in (exe_dir(), bundle_dir()):
        roots += [base, base / "model", base / "engine", base / "assets"]
    seen = set()
    for root in roots:
        if root in seen or not root:
            continue
        seen.add(root)
        for name in ("yolov11.onnx", "yolo5-vin.onnx", "middle.onnx"):
            candidate = root / name
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
    return None


# ==================================================================== Win32 部分
class Win32:
    """Windows 上用到的 API（截图 / 发鼠标消息 / 选窗口 / DPI）。"""

    WM_MOUSEMOVE = 0x0200
    WM_LBUTTONDOWN = 0x0201
    WM_LBUTTONUP = 0x0202

    def __init__(self) -> None:
        self.user32 = ctypes.windll.user32 if IS_WINDOWS else None
        self.gdi32 = ctypes.windll.gdi32 if IS_WINDOWS else None
        self.shcore = None                         # 测试时可以塞一个假的进来
        self.hwnd: Optional[int] = None
        self.scale = 1.0
        self._picked: Optional[int] = None
        self.last_capture = ""                     # 最近一次成功截图用的方式
        self.capture_scale = (1.0, 1.0)            # 截图尺寸 ÷ 客户区尺寸

    # ---------------------------------------------------------- 选窗口
    def pick_window(self, on_ready: Optional[Callable[[int], None]] = None,
                    timeout: float = 30.0,
                    cancel: Optional[threading.Event] = None) -> Optional[int]:
        """把光标换成圆圈，等用户点一下目标窗口，返回窗口句柄。"""
        if not IS_WINDOWS:
            return None
        self._picked = None
        previous = self.user32.GetCursor()          # 记下原光标以便恢复
        circle = self._load_circle_cursor()
        if circle:
            self.user32.SetSystemCursor(circle, 32512)   # OCR_NORMAL

        start = time.time()
        pressed = False
        while time.time() - start < timeout and self._picked is None:
            if cancel is not None and cancel.is_set():      # 用户点了「取消连线」
                break
            state = self.user32.GetAsyncKeyState(0x01)   # 鼠标左键
            if state & 0x8000:
                if not pressed:
                    pressed = True
                    point = wintypes.POINT()
                    self.user32.GetCursorPos(ctypes.byref(point))
                    print_point = ctypes.wintypes.POINT(point.x, point.y)
                    hwnd = self.user32.WindowFromPoint(print_point)
                    if hwnd:
                        root = self.user32.GetAncestor(hwnd, 2)   # GA_ROOT
                        self._picked = root or hwnd
            else:
                pressed = False
            time.sleep(0.05)

        self._restore_cursor(previous)
        if self._picked:
            self.hwnd = self._picked
            self.scale = self._screen_scale()
            if on_ready:
                on_ready(self.hwnd)
        return self._picked

    def _load_circle_cursor(self):
        """找一张圆圈光标图（优先用皮肤文件夹里的 circle.ico）。"""
        candidates = []
        try:
            from .session import exe_dir

            candidates += [exe_dir() / "ui" / "circle.ico", exe_dir() / "circle.ico"]
        except Exception:  # noqa: BLE001
            pass
        candidates += list(Path.cwd().glob("**/circle.ico"))[:3]
        for path in candidates:
            try:
                if Path(path).is_file():
                    handle = self.user32.LoadCursorFromFileW(str(path))
                    if handle:
                        return handle
            except Exception:  # noqa: BLE001
                continue
        return None

    def _restore_cursor(self, previous) -> None:
        try:
            if previous:
                self.user32.SetCursor(previous)
            self.user32.SystemParametersInfoW(0x0057, 0, None, 2)   # SPI_SETCURSORS
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------- 窗口信息
    def client_rect(self) -> Tuple[int, int, int, int]:
        """目标窗口客户区（x, y, width, height）：x/y 是屏幕坐标，宽高是客户区尺寸。

        ★ 每次都现取、不缓存：用户在连线过程中拖动/缩放对方窗口也能跟得上
        （"识别窗口要跟着窗口位置走，不能写死坐标"）。
        """
        if not self.hwnd:
            return (0, 0, 0, 0)
        rect = wintypes.RECT()
        if not self.user32.GetClientRect(wintypes.HWND(self.hwnd), ctypes.byref(rect)):
            return (0, 0, 0, 0)
        point = wintypes.POINT(0, 0)
        self.user32.ClientToScreen(wintypes.HWND(self.hwnd), ctypes.byref(point))
        return (int(point.x), int(point.y),
                max(0, int(rect.right - rect.left)), max(0, int(rect.bottom - rect.top)))

    def client_size(self) -> Tuple[int, int]:
        """目标窗口客户区的宽高（本程序看到的坐标空间里的像素）。"""
        _left, _top, width, height = self.client_rect()
        return width, height

    def window_title(self) -> str:
        if not self.hwnd:
            return ""
        length = self.user32.GetWindowTextLengthW(wintypes.HWND(self.hwnd))
        buffer = ctypes.create_unicode_buffer(length + 2)
        self.user32.GetWindowTextW(wintypes.HWND(self.hwnd), buffer, length + 1)
        return buffer.value

    def _screen_scale(self) -> float:
        """目标窗口所在显示器的缩放系数（1.0＝100%）。"""
        try:
            dpi = self.target_dpi()
            return dpi / 96.0 if dpi else 1.0
        except Exception:  # noqa: BLE001
            return 1.0

    # ---------------------------------------------------------- 分辨率 / DPI
    def own_dpi_awareness(self) -> int:
        """本程序自己的 DPI 感知级别：0＝不看 DPI（会被系统缩放）、1＝系统级、2＝每显示器。

        Tkinter 默认是 0（Windows 会把界面拉伸显示），此时我们拿到的客户区坐标是
        **逻辑像素**，而 DPI-aware 的客户端（天天象棋等）收到鼠标消息是按**物理像素**
        解释的 —— 所以发消息前必须乘上目标窗口的缩放系数。
        """
        shcore = self.shcore if self.shcore is not None else _load_shcore()
        if shcore is not None:
            try:
                value = ctypes.c_int(0)
                # GetProcessDpiAwareness(None, &value) —— Win8.1+，在 shcore.dll 里
                shcore.GetProcessDpiAwareness(None, ctypes.byref(value))
                return int(value.value)
            except Exception:  # noqa: BLE001
                pass
        try:
            # 退路：线程级感知（user32，Win10 1607+）
            context = self.user32.GetThreadDpiAwarenessContext()
            return int(self.user32.GetAwarenessFromDpiAwarenessContext(context))
        except Exception:  # noqa: BLE001
            return AWARENESS_UNAWARE

    def window_dpi_awareness(self) -> int:
        """目标窗口的 DPI 感知级别（0＝不看 DPI，1/2＝系统级/每显示器）。"""
        if not self.hwnd:
            return AWARENESS_UNAWARE
        try:
            context = self.user32.GetWindowDpiAwarenessContext(wintypes.HWND(self.hwnd))
            if context:
                return int(self.user32.GetAwarenessFromDpiAwarenessContext(context))
        except Exception:  # noqa: BLE001
            pass
        try:                                        # 没有上面两个 API → 用 DPI 反推
            dpi = self.target_dpi()
            if dpi and abs(dpi - 96) > 1:
                return AWARENESS_PER_MONITOR
        except Exception:  # noqa: BLE001
            pass
        return AWARENESS_UNAWARE

    def system_dpi(self) -> int:
        """系统（主显示器）DPI；拿不到按 96 算。"""
        try:
            return int(self.user32.GetDpiForSystem() or 96)
        except Exception:  # noqa: BLE001
            return 96

    def target_dpi(self) -> int:
        """目标窗口所在显示器的 DPI；拿不到退回系统 DPI。"""
        if self.hwnd:
            try:
                dpi = int(self.user32.GetDpiForWindow(wintypes.HWND(self.hwnd)) or 0)
                if dpi:
                    return dpi
            except Exception:  # noqa: BLE001
                pass
        return self.system_dpi()

    def screen_metrics(self) -> Dict[str, object]:
        """屏幕的**实际**分辨率与缩放系数（自动检测用）。

        ★ 关键点：DPI 不感知的进程调 ``GetSystemMetrics`` 拿到的是被系统缩放过的
        "逻辑"分辨率（2K + 150% 时是 1707x960），所以这里另外用
        ``GetDeviceCaps(DESKTOPHORZRES/DESKTOPVERTRES)`` 取真正的物理分辨率
        （2K 就是 2560x1440），两个都记下来，日志里一看就清楚。
        """
        info: Dict[str, object] = {"width": 0, "height": 0,
                                   "logical_width": 0, "logical_height": 0,
                                   "dpi": 96, "scale": 1.0}
        if not IS_WINDOWS:
            return info
        try:
            info["logical_width"] = int(self.user32.GetSystemMetrics(0))
            info["logical_height"] = int(self.user32.GetSystemMetrics(1))
        except Exception:  # noqa: BLE001
            pass
        dpi = self.system_dpi()
        info["dpi"] = dpi
        info["scale"] = round(dpi / 96.0, 3)
        width = height = 0
        try:
            hdc = self.user32.GetDC(None)
            try:
                width = int(self.gdi32.GetDeviceCaps(hdc, 118))      # DESKTOPHORZRES
                height = int(self.gdi32.GetDeviceCaps(hdc, 117))     # DESKTOPVERTRES
            finally:
                self.user32.ReleaseDC(None, hdc)
        except Exception:  # noqa: BLE001
            pass
        if not width or not height:
            try:                                 # 退路：EnumDisplaySettings 当前显示模式
                mode = _DevMode()
                if self.user32.EnumDisplaySettingsW(None, -1, ctypes.byref(mode)):
                    width, height = int(mode.dmPelsWidth), int(mode.dmPelsHeight)
            except Exception:  # noqa: BLE001
                pass
        if width and height:
            info["width"], info["height"] = width, height
        else:
            info["width"] = info["logical_width"]
            info["height"] = info["logical_height"]
        return info

    def message_scale(self) -> float:
        """给目标窗口发鼠标消息时，客户区坐标要乘的系数。

        具体规则见 :func:`compute_message_scale`：

        * 本程序 DPI 不感知、目标 DPI 感知 → 乘「系统 DPI ÷ 96」
          （150% 缩放就是 ×1.5）—— 不乘的话点出去的位置会缩水，2K/4K 屏上完全对不上；
        * 双方都不感知、或双方都感知 → 坐标空间一致，返回 1.0；
        * 本程序感知、目标不感知 → 按 96/目标DPI 缩小。
        """
        if not IS_WINDOWS or not self.hwnd:
            return 1.0
        return compute_message_scale(self.own_dpi_awareness(), self.window_dpi_awareness(),
                                     self.target_dpi(), self.system_dpi())

    def describe_environment(self) -> str:
        """一行环境信息（写进 link_debug.log，方便排查"某台电脑上连不上"）。"""
        parts = []
        try:
            screen = self.screen_metrics()
            if screen.get("width"):
                text = f"屏幕 {screen['width']}x{screen['height']}"
                if (screen.get("logical_width")
                        and int(screen["logical_width"]) != int(screen["width"])):
                    text += f"（本程序看到 {screen['logical_width']}"
                    text += f"x{screen['logical_height']}）"
                parts.append(text)
            parts.append(f"系统缩放 {float(screen.get('scale') or 1.0):.2f}x"
                         f"（DPI {screen.get('dpi')}）")
        except Exception:  # noqa: BLE001
            pass
        try:
            parts.append(f"本程序DPI感知={self.own_dpi_awareness()}")
        except Exception:  # noqa: BLE001
            pass
        if self.hwnd:
            try:
                parts.append(f"目标DPI感知={self.window_dpi_awareness()}")
            except Exception:  # noqa: BLE001
                pass
            try:
                parts.append(f"目标DPI={self.target_dpi()}")
            except Exception:  # noqa: BLE001
                pass
            try:
                left, top, width, height = self.client_rect()
                parts.append(f"客户区={width}x{height}@{left},{top}")
            except Exception:  # noqa: BLE001
                pass
            try:
                parts.append(f"消息坐标系数={self.message_scale():.2f}")
            except Exception:  # noqa: BLE001
                pass
        if self.last_capture:
            try:
                sx, sy = self.capture_scale
                if abs(sx - 1) > 0.02 or abs(sy - 1) > 0.02:
                    parts.append(f"截图/客户区={sx:.3f}x{sy:.3f}")
            except Exception:  # noqa: BLE001
                pass
        return "｜".join(parts)

    # ---------------------------------------------------------- 后台截图
    def _note_capture_scale(self, image) -> None:
        """记下"截图尺寸 ÷ 客户区尺寸"。

        ★ 这两个尺寸**不一定相等**：DPI 虚拟化时系统给我们的截图可能被缩放过，
        某些窗口（尤其是自己按物理像素渲染的 2K/4K 客户端）也可能直接返回物理尺寸。
        只要不相等，后面把"截图里的棋盘坐标"换算成"客户区坐标"时就要按比例缩放。
        """
        try:
            client_w, client_h = self.client_size()
            if client_w <= 0 or client_h <= 0 or not image:
                return
            sx = float(image.width) / float(client_w)
            sy = float(image.height) / float(client_h)
            self.capture_scale = (sx, sy)
            if abs(sx - 1.0) > 0.02 or abs(sy - 1.0) > 0.02:
                debug_log(f"截图：{self.last_capture or '?'} 拿到 {image.width}x{image.height}，"
                          f"客户区 {client_w}x{client_h} → 棋盘坐标按 "
                          f"{sx:.3f}/{sy:.3f} 换算")
        except Exception:  # noqa: BLE001
            pass

    def grab(self):
        """抓目标窗口的客户区画面。

        有些客户端（例如中国象棋2017）不吃 ``PrintWindow``，会返回一张全黑图，
        所以这里按顺序试三种方式，哪个能拿到"有内容"的图就用哪个：

        1. ``PrintWindow(PW_CLIENTONLY|PW_RENDERFULLCONTENT)``：后台重绘，最理想；
        2. ``PrintWindow(0)``：老式写法，兼容老程序；
        3. ``BitBlt`` 从**窗口自己的 DC** 拷（前台窗口立刻可用）；
        4. ``BitBlt`` 从**屏幕 DC** 按客户区矩形拷（窗口没被遮挡时一定能拿到）。
        """
        if not IS_WINDOWS or not self.hwnd:
            return None
        from PIL import Image

        hwnd = wintypes.HWND(self.hwnd)
        rect = wintypes.RECT()
        self.user32.GetClientRect(hwnd, ctypes.byref(rect))
        width = max(1, int(rect.right - rect.left))
        height = max(1, int(rect.bottom - rect.top))

        attempts = (
            ("PrintWindow(客户区+完整内容)", lambda mem: self.user32.PrintWindow(
                hwnd, mem, 0x1 | 0x2)),
            ("PrintWindow(默认)", lambda mem: self.user32.PrintWindow(hwnd, mem, 0)),
            ("BitBlt(窗口DC)", lambda mem: self._bitblt_window(hwnd, mem, width, height)),
            ("BitBlt(屏幕DC)", lambda mem: self._bitblt_screen(hwnd, mem, width, height)),
        )
        for name, action in attempts:
            image = self._capture_with(hwnd, width, height, action)
            if image is not None and _has_content(image):
                self.last_capture = name
                self._note_capture_scale(image)
                return image
            debug_log(f"截图：{name} 没拿到有效画面，换下一种")
        # 最后兜底：PIL 直接截屏幕上的这块区域
        try:
            from PIL import ImageGrab

            left, top, _w, _h = self.client_rect()
            image = ImageGrab.grab(bbox=(left, top, left + width, top + height)).convert("RGB")
            if _has_content(image):
                self.last_capture = "屏幕截图(PIL)"
                self._note_capture_scale(image)
                return image
        except Exception as exc:  # noqa: BLE001
            debug_log(f"截图：屏幕截图也失败：{exc}")
        self.last_capture = ""
        return None

    def _capture_with(self, hwnd, width: int, height: int, action):
        """按给定方式把像素拷进内存 DC，再转成 PIL 图。"""
        hdc_window = self.user32.GetWindowDC(hwnd)
        hdc_mem = self.gdi32.CreateCompatibleDC(hdc_window)
        try:
            hbitmap = self.gdi32.CreateCompatibleBitmap(hdc_window, width, height)
            old = self.gdi32.SelectObject(hdc_mem, hbitmap)
            try:
                action(hdc_mem)
                image = self._dib_to_image(hdc_mem, hbitmap, width, height)
            finally:
                self.gdi32.SelectObject(hdc_mem, old)
                self.gdi32.DeleteObject(hbitmap)
            return image
        except Exception as exc:  # noqa: BLE001
            debug_log(f"截图：{action} 出错：{exc}")
            return None
        finally:
            self.gdi32.DeleteDC(hdc_mem)
            self.user32.ReleaseDC(hwnd, hdc_window)

    def _bitblt_window(self, hwnd, mem, width: int, height: int) -> int:
        hdc = self.user32.GetDC(hwnd)
        try:
            return self.gdi32.BitBlt(mem, 0, 0, width, height, hdc, 0, 0, 0x00CC0020)
        finally:
            self.user32.ReleaseDC(hwnd, hdc)

    def _bitblt_screen(self, hwnd, mem, width: int, height: int) -> int:
        left, top, _w, _h = self.client_rect()
        hdc = self.user32.GetDC(None)          # 屏幕 DC
        try:
            return self.gdi32.BitBlt(mem, 0, 0, width, height, hdc, left, top, 0x00CC0020)
        finally:
            self.user32.ReleaseDC(None, hdc)

    def _dib_to_image(self, hdc_mem, hbitmap, width: int, height: int):
        from PIL import Image

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                        ("bmiColors", wintypes.DWORD * 3)]

        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height               # 负数＝自上而下
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        buffer = ctypes.create_string_buffer(width * height * 4)
        self.gdi32.GetDIBits(hdc_mem, hbitmap, 0, height, buffer, ctypes.byref(info), 0)
        image = Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA", 0, 1)
        return image.convert("RGB")

    # ---------------------------------------------------------- 发鼠标消息
    def click(self, x: int, y: int, delay: float = 0.05) -> None:
        """在目标窗口客户区 (x, y) 点一下（不移动真实鼠标）。

        ★ 分辨率/DPI 适配：这里传进来的 (x, y) 是**我们看到的**客户区坐标
        （逻辑像素）。目标窗口如果自己处理 DPI（常见于 2K/4K 屏 + 125%~200% 缩放），
        它收到消息时按自己的物理像素解释，所以要乘上 :meth:`message_scale`
        （1080p / 100% 缩放时系数就是 1.0，行为与以前完全一样）。
        """
        if not IS_WINDOWS or not self.hwnd:
            return
        hwnd = wintypes.HWND(self.hwnd)
        factor = self.message_scale()
        target_x = max(0, int(round(float(x) * factor)))
        target_y = max(0, int(round(float(y) * factor)))
        lparam = wintypes.LPARAM((target_y << 16) | (target_x & 0xFFFF))
        self.user32.PostMessageW(hwnd, self.WM_MOUSEMOVE, wintypes.WPARAM(1), lparam)
        self.user32.PostMessageW(hwnd, self.WM_LBUTTONDOWN, wintypes.WPARAM(1), lparam)
        if delay > 0:
            time.sleep(delay)
        self.user32.PostMessageW(hwnd, self.WM_LBUTTONUP, wintypes.WPARAM(0), lparam)

    def click_front(self, x: int, y: int, delay: float = 0.05) -> None:
        """备用方案：把窗口提到前台，用真实鼠标点（有些客户端不吃消息点击）。

        ★ 这里**不能**乘 :meth:`message_scale`：真实鼠标走的是"屏幕坐标"，
        而 ``ClientToScreen`` 和 ``SetCursorPos`` 都在**本程序自己的坐标空间**里
        （DPI 不感知时系统会自动把逻辑坐标换算成物理位置），乘了反而会点偏。
        """
        if not IS_WINDOWS or not self.hwnd:
            return
        try:
            import pyautogui  # 可选依赖，没有就用 ctypes
        except ImportError:
            pyautogui = None
        left, top, _w, _h = self.client_rect()
        if pyautogui is not None:
            pyautogui.click(left + x, top + y)
            return
        self.user32.SetCursorPos(left + x, top + y)
        self.user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(delay)
        self.user32.mouse_event(0x0004, 0, 0, 0, 0)


# ==================================================================== 识别部分
class BoardRecognizer:
    """从截图里认出 10×9 的棋盘数组。

    优先用 TCHESS 的 YOLOv11 模型（需要 ``onnxruntime``）；没有就退回模板匹配
    （用本程序自己的棋子字形做模板，对“标准画法”的棋盘有效）。
    """

    #: 类别顺序必须和 TCHESS 的模型一致（index 14 = '0' 表示棋盘）
    LABELS = list("nbakrcpRNAKBCP") + ["0"]
    SIZE = 640
    CONFIDENCE = 0.5

    def __init__(self, model_path: Optional[Path] = None):
        self.model_path = model_path
        #: 只在画面的一部分里找棋盘：``(left, top, width, height)`` 像素；None＝整张图
        self.region: Optional[Tuple[int, int, int, int]] = None
        self.session = None
        self.backend = "template"
        self.error = ""
        self.reject_reason = ""      # 最近一帧被"几何检查"挡掉的原因
        self.threads = INFER_THREADS
        self.warmed_up = False       # 是否做过预热推理（省掉首帧冷启动）
        self.grid: Optional[Tuple[float, float, float, float]] = None
        self._load_model()

    def _load_model(self) -> None:
        if not self.model_path:
            self.error = "没找到模型文件"
            debug_log("识别：没找到模型文件（请把 yolov11.onnx 放到程序目录或 model 子目录）")
            return
        import sys

        try:
            import onnxruntime as ort
        except ImportError as exc:
            debug_log(f"识别：导入 onnxruntime 失败（{exc}），补 sys.path 后重试")
            for extra in _extra_sys_paths():
                sys.path.insert(0, extra)
            try:
                import onnxruntime as ort      # noqa: F811
            except ImportError as exc2:
                self.error = f"没有 onnxruntime：{exc2}"
                debug_log(f"识别：onnxruntime 仍然导入失败：{exc2}（已退回模板匹配）")
                return
        try:
            options = ort.SessionOptions()
            options.intra_op_num_threads = INFER_THREADS
            self.session = ort.InferenceSession(str(self.model_path), options,
                                                providers=["CPUExecutionProvider"])
            self.backend = "yolo"
            self.error = ""
            debug_log(f"识别：YOLO 就绪｜onnxruntime {getattr(ort, '__version__', '?')}"
                      f"｜推理线程 {INFER_THREADS}｜模型 {self.model_path}")
            self.warm_up()
        except Exception as exc:  # noqa: BLE001
            self.error = f"模型加载失败：{exc}"
            debug_log(f"识别：模型加载失败：{exc}")

    def warm_up(self) -> None:
        """空推理预热：把第一次推理的冷启动开销挪到"点连线"这一刻。

        皮卡鱼的 ONNX 模型第一次跑会额外花 100~200ms（内存分配 / 算子准备），
        提前跑一次空图，之后每帧就是稳定的推理时间。
        """
        if self.session is None or self.backend != "yolo":
            return
        try:
            import numpy as np

            blank = np.zeros((1, 3, self.SIZE, self.SIZE), dtype=np.float32)
            start = time.perf_counter()
            self.session.run(None, {self.session.get_inputs()[0].name: blank})
            self.warmed_up = True
            debug_log(f"识别：预热完成（{(time.perf_counter() - start) * 1000:.0f} ms，"
                      f"线程 {INFER_THREADS}）")
        except Exception as exc:  # noqa: BLE001
            debug_log(f"识别：预热失败：{exc}")

    # ---------------------------------------------------------- 对外入口
    def recognize(self, image) -> Optional[List[List[str]]]:
        """返回 10×9 的棋子数组（"." 表示空点），识别不出来返回 None。"""
        if image is None:
            return None
        self.reject_reason = ""
        offset = (0, 0)
        region = self.region
        if region and image.width > 8 and image.height > 8:
            left = max(0, min(image.width - 8, int(region[0])))
            top = max(0, min(image.height - 8, int(region[1])))
            width = max(8, min(image.width - left, int(region[2])))
            height = max(8, min(image.height - top, int(region[3])))
            image = image.crop((left, top, left + width, top + height))
            offset = (left, top)
        board = None
        if self.backend == "yolo" and self.session is not None:
            try:
                board = self._recognize_yolo(image)
            except Exception as exc:  # noqa: BLE001
                self.error = f"识别失败：{exc}"
                board = None
        if not board:
            board = self._recognize_template(image)
        # ★ 只截了一部分画面时，把棋盘位置换算回整张截图的坐标（点击走子要用）
        if board and offset != (0, 0) and self.grid:
            self.grid = (self.grid[0] + offset[0], self.grid[1] + offset[1],
                         self.grid[2], self.grid[3])
        return board

    # ---------------------------------------------------------- YOLO 后端
    def _recognize_yolo(self, image) -> Optional[List[List[str]]]:
        import numpy as np
        from PIL import Image

        size = self.SIZE
        scale = min(size / image.width, size / image.height)
        resized = image.resize((max(1, int(image.width * scale)),
                                max(1, int(image.height * scale))), Image.BILINEAR)
        canvas = Image.new("RGB", (size, size), (114, 114, 114))
        left = (size - resized.width) // 2
        top = (size - resized.height) // 2
        canvas.paste(resized, (left, top))
        data = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
        inputs = {self.session.get_inputs()[0].name: data}
        output = self.session.run(None, inputs)[0]
        return self._decode_output(output, image, scale, left, top)

    def _decode_output(self, output, image, scale, left, top):
        """解析 YOLO 输出 ``[1, 4+类别数, N]``（TCHESS 的 yolov11 就是这种）。

        注意两点（踩过坑）：
        * 输入归一化到 0~1（不是 0~255）；
        * **按类别分别做 NMS** —— 否则棋盘框会把棋子框一起抑制掉。
        """
        import numpy as np

        array = np.squeeze(output)
        if array.ndim != 2:
            return None
        if array.shape[0] < array.shape[1]:        # [4+C, N] → [N, 4+C]
            array = array.transpose()
        classes = array.shape[1] - 4
        if classes <= 1:
            return None
        raw: Dict[int, List[Tuple[float, float, float, float, float]]] = {}
        for row in array:
            cx, cy, w, h = (float(row[0]), float(row[1]), float(row[2]), float(row[3]))
            cls_scores = row[4:4 + classes]
            index = int(np.argmax(cls_scores))
            score = float(cls_scores[index])
            if score < self.CONFIDENCE:
                continue
            raw.setdefault(index, []).append((cx, cy, w, h, score))

        boxes, labels = [], []
        for index, items in raw.items():           # 每个类别内部做 NMS
            items.sort(key=lambda item: -item[4])
            kept: List[Tuple[float, float, float, float]] = []
            for item in items:
                box = (item[0] - item[2] / 2, item[1] - item[3] / 2, item[2], item[3])
                if all(_iou(box, other) <= 0.45 for other in kept):
                    kept.append(box)
            for box in kept:
                boxes.append(((box[0] - left) / scale, (box[1] - top) / scale,
                              box[2] / scale, box[3] / scale))
                labels.append(index)
        if not boxes:
            return None
        return self._boxes_to_board(boxes, labels, image)

    @staticmethod
    def _nms(boxes, scores, iou_threshold: float = 0.45):
        order = sorted(range(len(scores)), key=lambda i: -scores[i])
        keep = []
        while order:
            current = order.pop(0)
            keep.append(current)
            keep_list = [current]
            for other in list(order):
                if _iou(boxes[current], boxes[other]) > iou_threshold:
                    order.remove(other)
            del keep_list
        return keep

    def _boxes_to_board(self, boxes, labels, image):
        """按 TCHESS 的做法：找到棋盘框 → 8×9 网格 → 每个棋子落到某个交叉点。"""
        board_box = None
        board_score = -1.0
        pieces = []
        for box, label in zip(boxes, labels):
            name = self.LABELS[label] if 0 <= label < len(self.LABELS) else ""
            if name == "0" or not name:             # 棋盘/背景类 → 棋盘框
                if box[2] * box[3] > board_score:
                    board_box, board_score = box, box[2] * box[3]
                continue
            pieces.append((box, name))
        if board_box is None:
            if len(pieces) < 8:                     # 没检出棋盘框，棋子太少就不猜了
                return None
            xs = [b[0] for b, _ in pieces]
            ys = [b[1] for b, _ in pieces]
            widths = [b[2] for b, _ in pieces]
            heights = [b[3] for b, _ in pieces]
            cell = max(1.0, (sum(widths) / len(widths)))
            board_box = (min(xs), min(ys),
                         (max(xs) - min(xs)) + cell, (max(ys) - min(ys)) + cell)
            del heights
        x, y, w, h = board_box
        cell_x, cell_y = w / 8.0, h / 9.0
        # ★ 记住棋盘在截图里的位置与格宽：点走子时要靠它换算客户区坐标
        self.grid = (x, y, cell_x, cell_y)
        # 棋子间距一致性：两个检测框中心都落在"半格以内"说明是重复检测或粘连，
        # 遇到这种帧直接作废（否则会把两个子塞进同一格，后面的差分全错）。
        centres = [((box[0] + box[2] / 2), (box[1] + box[3] / 2)) for box, _ in pieces]
        for index in range(len(centres)):
            for other in range(index + 1, len(centres)):
                dx = abs(centres[index][0] - centres[other][0]) / max(cell_x, 1e-6)
                dy = abs(centres[index][1] - centres[other][1]) / max(cell_y, 1e-6)
                if dx < 0.5 and dy < 0.5:
                    self.reject_reason = "棋子间距异常（同一格有两个棋子）"
                    debug_log(f"识别：{self.reject_reason}｜"
                              f"({centres[index][0]:.0f},{centres[index][1]:.0f}) 与 "
                              f"({centres[other][0]:.0f},{centres[other][1]:.0f})")
                    return None
        board = [["." for _ in range(9)] for _ in range(10)]
        for box, name in pieces:
            col = int(round((box[0] + box[2] / 2 - x) / cell_x))
            row = int(round((box[1] + box[3] / 2 - y) / cell_y))
            if 0 <= row <= 9 and 0 <= col <= 8:
                if board[row][col] != ".":            # 两个子落到同一格 → 这一帧不可信
                    self.reject_reason = f"同一格检出两个棋子({row},{col})"
                    debug_log(f"识别：{self.reject_reason}")
                    return None
                board[row][col] = name
        return board

    # ---------------------------------------------------------- 模板匹配后端
    def _recognize_template(self, image) -> Optional[List[List[str]]]:
        """没有模型时的降级方案：找到棋盘线 → 每个交叉点比对棋子字形模板。"""
        try:
            from .ui import board_image
        except Exception:  # noqa: BLE001
            return None
        grid = board_image._detect_grid(image)
        if grid is None:
            return None
        x0, y0, cell_x, cell_y = grid
        self.grid = (x0, y0, cell_x, cell_y)
        templates = board_image.templates(False)
        board = [["." for _ in range(9)] for _ in range(10)]
        for row in range(10):
            for col in range(9):
                cx = x0 + col * cell_x
                cy = y0 + row * cell_y
                radius = min(cell_x, cell_y) * 0.40
                box = (int(cx - radius), int(cy - radius),
                       int(cx + radius), int(cy + radius))
                box = (max(0, box[0]), max(0, box[1]),
                       min(image.width, box[2]), min(image.height, box[3]))
                if box[2] - box[0] < 6 or box[3] - box[1] < 6:
                    continue
                patch = image.crop(box)
                red = board_image._ink_mask(patch, board_image.READ_RED)
                black = board_image._ink_mask(patch, board_image.READ_BLACK)
                total = float(patch.width * patch.height)
                red_ratio = red.histogram()[255] / total
                black_ratio = black.histogram()[255] / total
                if red_ratio < 0.012 and black_ratio < 0.012:
                    continue
                color = (board_image.READ_RED if red_ratio >= black_ratio
                         else board_image.READ_BLACK)
                mask = board_image._normalized(
                    board_image._clip_circle(red if color == board_image.READ_RED else black))
                best, score = "", 0.0
                for piece, template in templates[color].items():
                    value = board_image._similarity(mask, template)
                    if value > score:
                        best, score = piece, value
                if best and score >= 0.34:
                    board[row][col] = best
        return board


def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    return inter / (aw * ah + bw * bh - inter)


def _has_content(image) -> bool:
    """判断截到的图是不是"有内容"（全黑/全同色说明这个截图方式没拿到画面）。"""
    try:
        colors = image.convert("RGB").getcolors(maxcolors=4096)
        if colors is None:
            return True                     # 颜色太多，肯定是真实画面
        return len(colors) > 4 or sum(count for count, _c in colors) < 16
    except Exception:  # noqa: BLE001
        return True


# ==================================================================== 连线主循环
class GraphLinker:
    """连线主循环：定时截图识别 → 与本程序棋盘比对 → 自动走子。

    只做两件事：
    * **上行**：识别到对方走了棋 → 把这步棋记到本程序里（走棋谱树/暂停分析都走现有逻辑）；
    * **下行**：本程序（引擎执红/执黑模式）走出的新招 → 在对方窗口里点出去。
    """

    def __init__(self, session, on_event: Optional[Callable[[str, dict], None]] = None):
        self.session = session
        self.on_event = on_event or (lambda kind, payload: None)
        self.win32 = Win32()
        self.model_path = self._resolve_model()
        self.recognizer = BoardRecognizer(self.model_path)
        self.running = False
        self.paused = False
        self.thread: Optional[threading.Thread] = None
        self.target_title = ""
        self.last_board: Optional[List[List[str]]] = None
        self.last_grid: Optional[Tuple[float, float, float, float]] = None
        self.last_error = ""
        self.sent_ply = -1
        self.flipped = False
        self.scans = 0
        self.cancel = threading.Event()        # 「取消连线」用它打断选窗口/主循环
        self.matched = False
        self.pieces = 0
        self.orientation = ""
        self.vertical = False
        self._click_attempts = 0
        self._applying_remote = False          # 正把对方的招法同步进来（不要回声点回去）
        #: 本次连线是否已经同步过对方的招法（同步过后不再用"棋子证据"改棋盘朝向）
        self._synced_any = False
        # --- 方案A：规则校验 + 多帧确认 ---
        self.confirmed_board: Optional[List[List[str]]] = None   # 已确认的对方局面
        self.pending_board: Optional[List[List[str]]] = None     # 等待确认的新局面
        self.pending_frames = 0                                  # 已经连续看到几帧
        self.pending_since = 0.0                                 # 待确认局面的第一帧时刻
        self.smart_board: Optional[List[List[str]]] = None       # 2 帧确认后的兜底比对用
        self.rejected = 0                                        # 被规则校验挡掉的帧数
        # --- 性能优化：静态画面跳过推理 ---
        self.last_image_key: Optional[bytes] = None              # 上一帧画面的像素指纹
        self.skipped_frames = 0                                  # 因画面没变而跳过推理的帧数
        self.reused_frames = 0
        debug_log(f"连线初始化｜frozen={getattr(__import__('sys'), 'frozen', False)}"
                  f"｜模型={self.model_path or '未找到'}"
                  f"｜识别后端={self.recognizer.backend}"
                  f"｜错误={self.recognizer.error or '无'}")

    # ---------------------------------------------------------------- 生命周期
    def _resolve_model(self) -> Optional[Path]:
        """识别模型文件：设置里指定的优先，没有就自动找程序目录里的。"""
        try:
            value = str(self.session.settings.get("link_model_path") or "").strip().strip('"')
        except Exception:  # noqa: BLE001
            value = ""
        if value:
            candidate = Path(value)
            if candidate.is_file():
                return candidate
            debug_log(f"连线：设置里的识别模型不存在（{value}），改用自动查找")
        return find_model_file()

    def reload_recognizer(self) -> None:
        """按最新设置重建识别器（在「连线设置」里换了模型后调用）。"""
        self.model_path = self._resolve_model()
        self.recognizer = BoardRecognizer(self.model_path)
        try:
            self.recognizer.warm_up()
        except Exception:  # noqa: BLE001
            pass
        debug_log(f"连线：识别模型已重载｜模型={self.model_path or '未找到'}"
                  f"｜识别后端={self.recognizer.backend}"
                  f"｜错误={self.recognizer.error or '无'}")

    def _region_rect(self, width: int, height: int):
        """棋盘区域（百分比）→ 像素矩形；整窗时返回 None。

        ★ 设置里存的是**百分比**（0~100），所以 1080p 和 2K/4K 上会自动按比例
        换算成各自的像素，不用改设置。
        """
        left, top, box_w, box_h = self._area_percent()
        if (left <= 0.5 and top <= 0.5 and box_w >= 99.5 and box_h >= 99.5):
            return None                          # 整窗 → 不用裁剪（自动找棋盘）
        x = int(width * left / 100.0)
        y = int(height * top / 100.0)
        w = int(width * box_w / 100.0)
        h = int(height * box_h / 100.0)
        w = max(16, min(width - x, w))
        h = max(16, min(height - y, h))
        return (x, y, w, h)

    def _area_percent(self) -> Tuple[float, float, float, float]:
        """棋盘区域设置（左/上/宽/高，百分比）；老版本存的像素值会自动换算。

        默认是「整窗」（0/0/100/100）＝ 让识别器自己在整张截图里找棋盘，
        这也是各种分辨率下最省事的默认值。
        """
        def number(key: str, default: float) -> float:
            try:
                return float(self.session.settings.get(key))
            except (TypeError, ValueError):
                return float(default)

        left = number("link_area_x", 0.0)
        top = number("link_area_y", 0.0)
        box_w = number("link_area_w", 100.0)
        box_h = number("link_area_h", 100.0)
        if left > 100.0 or top > 100.0 or box_w > 100.0 or box_h > 100.0:
            # 老版本（1.2 及以前）存的是**像素**：用当前客户区尺寸换算成百分比，
            # 顺手写回设置里，下次起就是百分比了。
            width, height = self._client_size()
            if width > 0 and height > 0:
                left = max(0.0, min(100.0, left / width * 100.0))
                top = max(0.0, min(100.0, top / height * 100.0))
                box_w = max(1.0, min(100.0, box_w / width * 100.0))
                box_h = max(1.0, min(100.0, box_h / height * 100.0))
                self._store_area_percent(left, top, box_w, box_h)
                debug_log(f"连线：棋盘区域是旧版像素值，已按客户区 {width}x{height} "
                          f"换算成百分比 {left:.1f}/{top:.1f}/{box_w:.1f}/{box_h:.1f}")
            else:
                left = top = 0.0
                box_w = box_h = 100.0
        return (max(0.0, min(100.0, left)), max(0.0, min(100.0, top)),
                max(1.0, min(100.0, box_w)), max(1.0, min(100.0, box_h)))

    def _store_area_percent(self, left, top, box_w, box_h) -> None:
        """把换算好的百分比写回设置（直接改 + 存盘，不走 update_settings——
        那是界面线程用的，会顺带触发一次局面重算与分析）。"""
        try:
            settings = self.session.settings
            settings["link_area_x"] = round(float(left), 2)
            settings["link_area_y"] = round(float(top), 2)
            settings["link_area_w"] = round(float(box_w), 2)
            settings["link_area_h"] = round(float(box_h), 2)
            save = getattr(self.session, "save_settings_file", None)
            if callable(save):
                save()
        except Exception:  # noqa: BLE001
            pass

    def _client_size(self) -> Tuple[int, int]:
        """目标窗口客户区尺寸（本程序看到的坐标空间；拿不到返回 0,0）。"""
        try:
            _left, _top, width, height = self.win32.client_rect()
            return int(width), int(height)
        except Exception:  # noqa: BLE001
            return 0, 0

    def _to_client_grid(self, grid, image):
        """把"截图里的棋盘网格"换算成"目标窗口客户区坐标"。

        ★ 这一步就是"截图尺寸和客户区尺寸对不上"时的换算：

        * 识别器给出的 ``grid = (x0, y0, 格宽, 格高)`` 是**截图**里的像素；
        * 点击时要用的是**客户区**坐标（再乘 :meth:`Win32.message_scale` 发给对方）；
        * 两者一般相等（1080p + 100% 缩放），但 DPI 虚拟化/某些客户端自己按物理
          像素渲染时，截图可能比客户区大或小，不换算就会整体偏移，越走越乱。

        所以这里按 ``客户区尺寸 ÷ 截图尺寸`` 缩放换算一遍；比例接近 1 就直接用。
        """
        if not grid:
            return None
        try:
            x0, y0, cell_x, cell_y = (float(value) for value in grid)
        except (TypeError, ValueError):
            return None
        width, height = self._client_size()
        try:
            img_w = float(getattr(image, "width", 0) or 0)
            img_h = float(getattr(image, "height", 0) or 0)
        except Exception:  # noqa: BLE001
            img_w = img_h = 0.0
        if width <= 0 or height <= 0 or img_w <= 0 or img_h <= 0:
            return (x0, y0, cell_x, cell_y)
        scale_x = width / img_w
        scale_y = height / img_h
        if abs(scale_x - 1.0) <= 0.02 and abs(scale_y - 1.0) <= 0.02:
            return (x0, y0, cell_x, cell_y)
        return (x0 * scale_x, y0 * scale_y, cell_x * scale_x, cell_y * scale_y)

    def start(self, threaded: bool = True) -> bool:
        """开始连线。``threaded=False`` 时只做初始化、不启后台循环（自测用）。"""
        if not IS_WINDOWS:
            self.last_error = "连线功能目前只支持 Windows"
            return False
        if self.recognizer.backend != "yolo":
            self.last_error = (self.recognizer.error or "识别模型未就绪")
        # ★ 把屏幕分辨率 / 缩放 / DPI 感知 / 尺寸换算都写进日志：
        #   用户在 2K/4K 上连线失败时，看一眼这一行就知道坐标系有没有对上。
        try:
            debug_log(f"连线环境：{self.win32.describe_environment()}")
        except Exception:  # noqa: BLE001
            pass
        self.cancel.clear()
        # 订阅"本程序走出了一步棋"：引擎执红/执黑时靠它把招法点到对方窗口
        if self._on_local_move not in self.session.move_listeners:
            self.session.move_listeners.append(self._on_local_move)
        # 记下当前手数：之后本程序走出的新招才会点给对方（不会把历史着法重放一遍）
        try:
            moves = self.session.state().get("moves") or []
            # 同上：state()["moves"] 不带 ply，用 state()["ply"] 才准
            self.sent_ply = int(self.session.state().get("ply") or len(moves))
        except Exception:  # noqa: BLE001
            self.sent_ply = 0
        self.running = True
        # 重新连线时把确认状态和画面指纹清干净（否则会拿上一次的旧状态做判断）
        self.confirmed_board = None
        self.pending_board = None
        self.pending_frames = 0
        self.pending_since = 0.0
        self.smart_board = None
        self.last_image_key = None
        self.skipped_frames = 0
        self.reused_frames = 0
        self._synced_any = False                 # 重新连线时清掉（允许再按证据纠一次朝向）
        self.orientation = ""
        self._last_remote_board = None
        if threaded:
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
        return True

    def stop(self) -> None:
        self.cancel.set()
        try:
            if self._on_local_move in self.session.move_listeners:
                self.session.move_listeners.remove(self._on_local_move)
        except (AttributeError, ValueError):
            pass
        self.running = False
        self.paused = False
        self.sent_ply = -1

    def pause(self, paused: bool = True) -> None:
        self.paused = bool(paused)

    @property
    def settings(self) -> Dict[str, object]:
        return self.session.settings

    @property
    def our_side(self) -> str:
        """我们在对方平台执哪一方：``"red"`` / ``"black"`` / ``""``（不确定）。

        优先看引擎模式（引擎执红＝我们执红、引擎执黑＝我们执黑）；
        纯手动打谱时看棋盘有没有翻转（翻成黑在下说明用户在执黑）。
        """
        try:
            mode = str(self.settings.get("engine_mode") or "").strip().lower()
        except Exception:  # noqa: BLE001
            mode = ""
        if mode in ("red", "black"):
            return mode
        try:
            if bool(self.settings.get("flip")):
                return "black"
        except Exception:  # noqa: BLE001
            pass
        return "red"

    def preferred_orientation(self) -> str:
        """对方棋盘的朝向：手动指定 > 按我方执红/执黑推断。

        ★ 为什么要按"执红/执黑"定，而不是靠棋子位置猜：
          象棋开局局面是**左右对称**的，"不翻"和"180° 翻转"两种猜法的棋子重合度
          完全一样，光看棋子分不出谁对谁错；一旦猜错，同步进来的局面和点出去的
          招法都会整体镜像（而且越同步越"自洽"，自己永远纠不回来）。
          多数象棋平台会把"你那一方"转到棋盘下方，所以执黑时对方的棋盘相当于
          把我们这边**上下 + 左右都翻**（180°），执红时不用翻。
        """
        manual = str(self.settings.get("link_orientation") or "auto").strip().lower()
        if manual in ("same", "ud", "lr", "lr+ud"):
            return "" if manual == "same" else manual
        return "lr+ud" if self.our_side == "black" else ""

    @property
    def strict_validate(self) -> bool:
        """是否启用"识别结果必须像一盘合法的棋"的规则校验（默认开）。"""
        return bool(self.settings.get("link_strict_validate", True))

    @property
    def confirm_frames(self) -> int:
        """连续多少帧识别结果完全相同才确认变化（默认 3，范围 1~5）。"""
        raw = self.settings.get("link_confirm_frames")
        if raw is None or raw == "":
            return 3
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return 3
        return max(1, min(5, value))

    @property
    def confirm_seconds(self) -> float:
        """确认时间窗的"每一帧预算"（默认 0.6 秒，范围 0~5）。

        实际时间窗 = 本值 × (要求帧数 − 1)：要求 3 帧时是 1.2 秒、要求 2 帧时是 0.6 秒。
        同一个结果只要"至少看到 2 帧"且持续时间到了这个窗口，就直接确认，
        这样把扫描间隔调大（例如 2 秒）也不会让等待翻倍。
        """
        raw = self.settings.get("link_confirm_seconds")
        if raw is None or raw == "":
            return 0.6
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return 0.6
        return max(0.0, min(5.0, value))

    @property
    def smart_confirm(self) -> bool:
        """智能确认：正常走子（差一步合法着法）只要 2 帧，异常变化仍要 3 帧。"""
        return bool(self.settings.get("link_smart_confirm", True))

    def status(self) -> Dict[str, object]:
        return {
            "running": self.running,
            "paused": self.paused,
            "backend": self.recognizer.backend,
            "model": str(self.model_path or ""),
            "error": self.last_error or self.recognizer.error,
            "window": self.target_title,
            "hwnd": int(self.win32.hwnd or 0),
            "scans": self.scans,
            "flipped": self.flipped,
            "matched": self.matched,
            "pieces": self.pieces,
            "cancelled": self.cancel.is_set(),
            "confirmFrames": self.confirm_frames,
            "confirmSeconds": self.confirm_seconds,
            "smartConfirm": self.smart_confirm,
            "pendingFrames": self.pending_frames,
            "strictValidate": self.strict_validate,
            "rejected": self.rejected,
            "skippedFrames": self.skipped_frames,
            "messageScale": self._message_scale(),
            "screen": self._screen_text(),
        }

    def _message_scale(self) -> float:
        """（状态栏用）发给目标窗口的坐标要乘的系数；出错按 1.0。"""
        try:
            return float(self.win32.message_scale())
        except Exception:  # noqa: BLE001
            return 1.0

    def _screen_text(self) -> str:
        """（状态栏用）屏幕分辨率 + 缩放，例如 ``2560x1440×1.50``。"""
        try:
            info = self.win32.screen_metrics()
            width = int(info.get("width") or 0)
            height = int(info.get("height") or 0)
            scale = float(info.get("scale") or 1.0)
            if not width:
                return ""
            text = f"{width}x{height}"
            if abs(scale - 1.0) > 0.01:
                text += f"×{scale:.2f}"
            return text
        except Exception:  # noqa: BLE001
            return ""

    # ---------------------------------------------------------------- 主循环
    def _loop(self) -> None:
        while self.running and not self.cancel.is_set():
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"连线出错：{exc}"
            time.sleep(max(0.2, float(self.settings.get("link_scan_seconds") or 1.0)))
        self.running = False

    def _looks_like_one_move(self, previous: List[List[str]], board: List[List[str]]) -> bool:
        """新局面是不是"就差一步合法着法"（＝正常走子）。

        先用几何判断（只差一子移动、走的还是同一个子），再用我们自己的规则确认这步
        确实合法（含"轮到谁走"）。任一环节不成立就按"异常变化"处理（多要一帧）。
        """
        if previous is None:
            return False
        move = _diff_move(previous, board)
        if not move:
            return False
        if str(getattr(self, "orientation", "") or ""):
            return True            # 朝向与本地不同（镜像/颠倒），不做合法性判断
        try:
            board_obj = self.session.tree.board_at()
            return board_obj.find_move_iccs(move[0] + move[1]) is not None
        except Exception:  # noqa: BLE001
            return True

    def _confirm_board(self, board: List[List[str]]) -> bool:
        """多帧确认（智能 + 时间窗）：判断这个新局面能不能当成"对方的真实局面"。

        * 第一帧：直接建立基准（不用等）。
        * 和已确认局面一样：直接放行（没有变化）。
        * 出现变化：
            - **正常走子**（差一步合法着法）→ 只要 **2 帧**一致就确认；
            - **异常变化**（噪声 / 动画中间态 / 大跳变）→ 仍要 ``confirm_frames`` 帧（默认 3）。
          "帧数"之外再加一个**时间窗**：同一个结果至少看到 2 帧、并且持续时间达到
          ``confirm_seconds × (要求帧数 − 1)``（默认 0.6×2＝1.2 秒 / 0.6×1＝0.6 秒）时，
          直接确认——这样把扫描间隔调大也不会让等待翻倍。
        * 2 帧就确认的"正常走子"还会保留第三帧兜底：下一帧如果和确认结果不一致，
          记一条日志，并把它当成新的变化重新确认（等于自动纠错）。
        """
        base_frames = self.confirm_frames
        snapshot = [list(row) for row in board]
        if self.confirmed_board is None:               # 第一帧直接当基准，不用等
            self.confirmed_board = snapshot
            self.pending_board = None
            self.pending_frames = 0
            self.pending_since = 0.0
            debug_log(f"连线：首帧建立基准局面（棋子 {self.pieces} 个，"
                      f"确认帧数={base_frames}）")
            return True
        if _boards_equal(self.confirmed_board, snapshot):
            self.pending_board = None                  # 和已确认局面一致 → 清掉待确认
            self.pending_frames = 0
            self.pending_since = 0.0
            if self.smart_board is not None:
                # 智能确认（2 帧）之后的第一帧也对得上 → 兜底通过
                debug_log("连线：第三帧兜底通过（与智能确认结果一致）")
                self.smart_board = None
            return True
        if self.smart_board is not None and not _boards_equal(self.smart_board, snapshot):
            debug_log("连线：第三帧与智能确认的结果不一致 → 按新的变化重新确认（自动纠错）")
            self.smart_board = None
        if self.pending_board is not None and _boards_equal(self.pending_board, snapshot):
            self.pending_frames += 1
        else:
            self.pending_board = snapshot
            self.pending_frames = 1
            self.pending_since = time.monotonic()

        simple = self.smart_confirm and self._looks_like_one_move(
            self.confirmed_board, snapshot)
        need = min(2, base_frames) if simple else base_frames
        window = self.confirm_seconds * max(0, need - 1)
        elapsed = time.monotonic() - (self.pending_since or time.monotonic())
        by_frames = self.pending_frames >= need
        # 时间窗设成 0 就是"关掉时间窗、只看帧数"
        by_window = window > 0 and self.pending_frames >= 2 and elapsed >= window
        if by_frames or by_window:
            reason = (f"正常走子·{need} 帧" if simple else f"{self.pending_frames} 帧")
            if by_window and not by_frames:
                reason += f"·时间窗 {window:.1f}s"
            debug_log(f"连线：棋盘变化已确认（{reason}，连续 {self.pending_frames} 帧一致，"
                      f"棋子 {self.pieces} 个）")
            self.confirmed_board = snapshot
            self.pending_board = None
            self.pending_frames = 0
            self.pending_since = 0.0
            # 2 帧就确认的，留一个"第三帧兜底"
            self.smart_board = snapshot if (simple and need < base_frames) else None
            return True
        debug_log(f"连线：棋盘发生变化 第{self.pending_frames}帧确认"
                  f"（需要 {need} 帧"
                  + (f"或持续 {window:.1f} 秒" if window else "")
                  + f"，{'正常走子' if simple else '异常变化'}，棋子 {self.pieces} 个）")
        return False

    def _on_local_move(self, iccs: str, ply: int, source: str) -> None:
        """本程序走出一步棋 → 在对方窗口点出来（引擎执红/执黑就靠它自动走棋）。"""
        if self._applying_remote or not self.running or self.paused:
            return
        if len(iccs) != 4:
            return
        debug_log(f"连线：本程序走出 {iccs}（第 {ply} 手，{source}）→ 点给对面")
        # 点成功才记"已发送"：万一这次没点成（窗口/坐标缺失），下一帧的兜底路径会补点。
        if self.click_move(iccs[:2], iccs[2:4]):
            self.sent_ply = max(int(self.sent_ply or 0), int(ply or 0))
            self._last_clicked = (int(ply or 0), False)

    def _flush_local_move(self, local: List[List[str]],
                          remote: Optional[List[List[str]]] = None) -> None:
        """把"本程序新走出、还没点给对方"的招法点出去（兜底路径）。

        主路径是走子事件（``_on_local_move``，一走出就点，不等任何确认）；
        这里是每一帧的兜底：万一事件那条路没赶上（例如当时暂停过），下一帧补点。
        **它在多帧确认之前执行**，所以不会被确认延迟拖慢。
        """
        moves = self.session.state().get("moves") or []
        if not moves:
            return
        last = moves[-1]
        # ★ 注意：state()["moves"] 里没有 ply 字段，这里必须用 state()["ply"]
        #   （或者用列表长度）来算手数——以前用 last["ply"] 永远是 0，
        #   结果是这条"兜底点击"路径其实一直没生效。
        ply = int(self.session.state().get("ply") or len(moves))
        iccs = str(last.get("iccs") or "")
        # 只有"本程序自己走的"新招才点出去：跳过刚从对方同步过来的那一步，
        # 并且对方窗口已经出现过的着法不重复点。
        if not (len(iccs) == 4 and ply > max(self.sent_ply, 0)
                and ply != getattr(self, "_remote_ply", -1)):
            return
        dst = self._board_rc(iccs[2:4])
        already = bool(remote and dst is not None and local[dst[0]][dst[1]] != "."
                       and remote[dst[0]][dst[1]] == local[dst[0]][dst[1]])
        self.click_move(iccs[:2], iccs[2:4])     # 本程序的新招一律点出去
        self._last_clicked = (ply, already)
        self.sent_ply = ply
        debug_log(f"连线：点出本程序着法 {iccs}（第 {ply} 手，"
                  f"对方已出现={already}，方式="
                  f"{'后台发消息' if self.settings.get('link_back_mode', True) else '前台真实点击'}）")

    def _tick(self) -> None:
        if self.paused or not self.win32.hwnd:
            return
        image = self.win32.grab()
        if image is None:
            self.last_error = "截图失败（目标窗口可能已关闭）"
            self.on_event("scan", {"board": None, "error": self.last_error,
                                   "scans": self.scans})
            return
        self.scans += 1
        # 棋盘区域：用户可以在「连线设置」里限定只扫描画面的一部分
        self.recognizer.region = self._region_rect(image.width, image.height)
        # ★ 静态画面跳过推理：先比对像素指纹，画面和上一帧完全一样就直接沿用上一帧的
        #   识别结果（输入相同 → 模型输出必然相同），待机时 CPU 就这样省下来。
        #   注意：复用结果仍然会走"规则校验 + 多帧确认"，稳定性不受影响。
        fingerprint = _fingerprint(image)
        reused = (fingerprint is not None and fingerprint == self.last_image_key
                  and self.last_board is not None)
        if reused:
            board = [list(row) for row in self.last_board]
            self.skipped_frames += 1
            self.reused_frames += 1
        else:
            board = self.recognizer.recognize(image)
            self.last_image_key = fingerprint
            # ★ 连"没通过校验"的结果也一起缓存：画面一模一样时模型输出必然一样，
            #   没必要每秒重新跑一遍模型（这正是待机时省 CPU 的地方）。
            self.last_board = [list(row) for row in board] if board else None
        if not board:
            self.last_error = (self.recognizer.reject_reason
                               or self.recognizer.error or "这一帧没认出棋盘")
            self.on_event("scan", {"board": None, "error": self.last_error,
                                   "scans": self.scans})
            return
        # ★ 方案A 第一步：规则校验。识别结果必须"像一盘合法的棋"，
        #   否则这一帧直接作废（不写日志的噪声太多，只在 link_debug.log 里记一行）。
        if self.strict_validate:
            reason = validate_board(board)
            if reason:
                self.rejected += 1
                self.last_error = f"规则校验不通过：{reason}"
                debug_log(f"连线：规则校验不通过：{reason}"
                          f"（第 {self.scans} 次扫描，这一帧作废）")
                self.on_event("scan", {"board": None, "error": self.last_error,
                                       "scans": self.scans, "rejected": self.rejected})
                return
        if self.recognizer.grid:
            # ★ 供点击换算坐标用：截图里的坐标 → 客户区坐标（两者不一致时按比例缩回）
            self.last_grid = self._to_client_grid(self.recognizer.grid, image)
        self.last_error = ""
        self.pieces = sum(1 for row in board for cell in row if cell != ".")
        # ★ 本程序自己的着法永远不被确认延迟波及：先把"该点出去的招法"处理掉，
        #   再去管对方局面的多帧确认（原来这段在确认之后，会被挡一帧到两帧）。
        local = self._session_board()
        self._flush_local_move(local)
        # ★ 方案A 第二步：多帧确认。连续 N 帧识别结果完全相同，才把它交给后面的
        #   同步逻辑；只出现一两帧的变化（多半是误识别）先等等看。
        if not self._confirm_board(board):
            self.matched = False
            self.on_event("scan", {"board": None, "error": "", "scans": self.scans,
                                   "pieces": self.pieces, "matched": None,
                                   "pending": True, "confirm": self.pending_frames,
                                   "need": self.confirm_frames,
                                   "reused": reused,
                                   "rejected": self.rejected})
            return
        # ★ 对方棋盘朝向：**按我们执红/执黑推断**（详见 preferred_orientation）。
        #   这里仍然把四种朝向都算一遍，但只用来"在还没同步过任何一步时"做一次纠偏：
        #   开局左右对称，光靠棋子分不出"不翻"和"180°"，所以默认听执红/执黑的。
        candidates = (("", board), ("lr", _flip(board)),
                      ("ud", _flip_ud(board)), ("lr+ud", _flip(_flip_ud(board))))
        preferred = self.preferred_orientation()
        scores = {}
        board_by_mode = {}
        for tag, candidate in candidates:
            board_by_mode[tag] = candidate
            # ★ 只比较"有棋子的格子"：空格对朝向判断没有意义（否则四种朝向分数几乎一样，
            #   很容易挑错朝向 → 后面的差分就不是"只差一步"，局面自然同步不上）。
            score = 0
            for i in range(10):
                for j in range(9):
                    want, got = local[i][j], candidate[i][j]
                    if want != "." and got == want:
                        score += 2                     # 棋子位置正确：加分
                    elif want != "." and got == ".":
                        score -= 1                     # 该有的子没认出来
                    elif want == "." and got != ".":
                        score -= 1                     # 空位多出了子
                    elif want != "." and got != ".":
                        score -= 1                     # 颜色/子力对不上（镜像时会大量出现）
            scores[tag] = score
        best_tag = max(scores, key=lambda tag: scores[tag])
        manual = str(self.settings.get("link_orientation") or "auto").strip().lower()
        if manual in ("same", "ud", "lr", "lr+ud"):
            mode = "" if manual == "same" else manual        # 手动指定 → 一直听它的
        elif self._synced_any and self.orientation:
            # ★ 已经接管/同步过局面：沿用上一次定下来的朝向。
            #   这时的"棋子证据"是**自我一致**的（镜像的局面同样自洽），再按它改
            #   只会把已经对好的坐标又翻回去，中局/残局接入就是这么坏的。
            mode = self.orientation
        else:
            mode = preferred
            if best_tag != preferred and scores[best_tag] >= scores[preferred] + 12:
                # 还没同步过任何一步（我们本地还是原来那个局面）时棋子证据可信：
                # 有些平台不给你翻棋盘，或者你连的是同一盘棋的中局，按证据纠正。
                mode = best_tag
                debug_log(f"连线：棋子证据显示对方棋盘朝向更像「{orientation_label(mode)}」，"
                          f"按证据纠正（本地分数 {scores[preferred]} → {scores[mode]}）")
        remote = board_by_mode[mode]
        changed = (mode != getattr(self, "orientation", ""))
        self.orientation = mode
        self.flipped = "lr" in mode            # 对方棋盘左右镜像（点击坐标要跟着镜像）
        self.vertical = "ud" in mode           # 对方棋盘上下颠倒（点击坐标要跟着颠倒）
        if changed:
            # ★ 这里**只记录朝向用于点击换算，绝不改动我们自己的棋盘朝向**：
            #   是否翻转棋盘完全由用户点「翻转棋盘」决定，连线不掺和。
            debug_log(f"连线：对方棋盘朝向 = {orientation_label(mode)}（{mode or 'same'}）"
                      f"｜我方执{'黑' if self.our_side == 'black' else '红'}"
                      f"（仅用于点击坐标换算，不翻转我们的棋盘）")
        self.on_event("orientation", {"mode": mode, "vertical": self.vertical,
                                     "flipped": self.flipped})
        self.matched = _boards_equal(remote, local)
        self.on_event("scan", {"board": remote, "error": "", "scans": self.scans,
                               "pieces": self.pieces, "matched": self.matched,
                               "flipped": self.flipped})
        if not self.matched:
            diffs = [(i, j) for i in range(10) for j in range(9)
                     if remote[i][j] != local[i][j]]
            debug_log(f"连线扫描#{self.scans}：棋子{self.pieces}｜朝向={mode or '一致'}"
                      f"｜不同格={len(diffs)}｜{diffs[:6]}")
            # ★ 中局 / 残局接入：本地棋盘和对方差得很多（不是"一步棋"），连续两帧一致
            #   说明这就是对方当前的真实局面 → 直接接管成我们的局面。
            if len(diffs) > 6:
                if getattr(self, "_adopt_candidate", None) == remote:
                    self.adopt_remote(remote)
                    self._adopt_candidate = None
                else:
                    self._adopt_candidate = [list(row) for row in remote]
            else:
                self._adopt_candidate = None
        else:
            self._adopt_candidate = None

        # ★ 关键：用"**两次识别结果**之间的差异"来推对方走了哪一步。
        #   模型可能对某几个格子有固定误读（每次扫描都一样），拿"识别 vs 本地棋盘"
        #   做差分会被这些固定误差挡住、永远算不出一整步棋；而两次识别之间的差异里，
        #   固定误差会互相抵消，剩下的就是对方刚走的那一步。
        previous = getattr(self, "_last_remote_board", None)
        self._last_remote_board = [list(row) for row in remote]
        if previous is not None and not _boards_equal(previous, remote):
            move = _diff_move(previous, remote)
            if move:
                source, target = move
                self._applying_remote = True
                try:
                    applied = self.session.play_move(source, target)
                finally:
                    self._applying_remote = False
                debug_log(f"连线：对方走了 {source}->{target}（按两次识别结果推断）"
                          f"｜同步{'成功' if applied else '失败（本程序认为这步不合法）'}")
                if applied:
                    self._synced_any = True      # 同步过以后就不再用棋子证据改朝向了
                    self.on_event("remote_move", {"from": source, "to": target})
                    self._remote_ply = int(self.session.state().get("ply") or 0)
            else:
                cells = [(i, j) for i in range(10) for j in range(9)
                         if previous[i][j] != remote[i][j]]
                debug_log(f"连线：两次识别有 {len(cells)} 格不同，但拼不成一步棋：{cells[:6]}")

        # 上行：对方走了棋 → 记进本程序
        if not _boards_equal(remote, local):
            move = _diff_move(local, remote)
            if move:
                source, target = move
                self._applying_remote = True            # 标记：这一步是对方走的
                try:
                    applied = self.session.play_move(source, target)
                finally:
                    self._applying_remote = False
                if applied:
                    self.on_event("remote_move", {"from": source, "to": target})
                    # 对方走的这一步不要再点回去 → 记住它的手数
                    self._remote_ply = int(self.session.state().get("ply") or 0)

        # 下行（兜底）：本程序有新的招法还没点出去 → 补点一次。
        #   （主路径是走子事件，早在 _tick 开头就已经点过；这里通常什么都不做。）
        self._flush_local_move(local, remote)

    @staticmethod
    def _board_rc(square: str) -> Optional[Tuple[int, int]]:
        """ICCS 坐标 → (行, 列)：行 0 是黑方底线（与识别结果一致）。"""
        from .board import FILES

        if len(square) < 2:
            return None
        file = FILES.find(square[0].lower())
        try:
            rank = int(square[1])
        except ValueError:
            return None
        if file < 0 or not 0 <= rank <= 9:
            return None
        return 9 - rank, file

    # ---------------------------------------------------------------- 棋盘与坐标
    def adopt_remote(self, board: List[List[str]]) -> bool:
        """把对方平台的当前局面接管为我们自己的局面（中局 / 残局连线用）。

        连线时对方往往已经下到中局了，我们本地还停在初始局面；这时直接把识别到的
        局面设成我们的当前局面，之后对方的每一步就能正常同步、引擎也能接着走。
        先行方按模式推断：引擎执红 → 对方（黑）走；引擎执黑 → 对方（红）走；
        其它情况按红先。
        """
        rows = []
        for row in board:
            text = []
            empty = 0
            for cell in row:
                if cell in (".", "", " "):
                    empty += 1
                    continue
                if empty:
                    text.append(str(empty))
                    empty = 0
                text.append(cell)
            if empty:
                text.append(str(empty))
            rows.append("".join(text) or "9")
        mode = str(self.session.settings.get("engine_mode") or "")
        side = {"red": "b", "black": "w"}.get(mode, "w")
        fen = "/".join(rows) + f" {side} - - 0 {max(1, (self.pieces or 32) // 4)}"
        try:
            ok = bool(self.session.set_fen(fen))
        except Exception as exc:  # noqa: BLE001
            ok = False
            debug_log(f"连线：接管局面失败：{exc}")
        if ok:
            self._remote_ply = int(self.session.state().get("ply") or 0)
            self.sent_ply = 0
            self._last_remote_board = [list(row) for row in board]
            self._synced_any = True               # 接管过局面就不再用棋子证据改朝向
            debug_log(f"连线：已接管对方当前局面（{self.pieces} 个棋子，"
                      f"{'红' if side == 'w' else '黑'}先）→ 之后按这个局面继续同步")
            self.on_event("adopt", {"fen": fen, "pieces": self.pieces})
        else:
            debug_log("连线：接管局面失败（识别结果不是合法局面，等下一帧再试）")
        return ok

    def _session_board(self) -> List[List[str]]:
        """本程序当前棋盘：10 行 × 9 列，第 0 行是黑方底线（和识别结果一致）。"""
        try:
            board = self.session.tree.board_at()
        except Exception:  # noqa: BLE001
            return [["." for _ in range(9)] for _ in range(10)]
        rows = []
        for rank in range(9, -1, -1):
            rows.append([board.cells[rank][file] for file in range(9)])
        return rows

    def click_move(self, source: str, target: str) -> bool:
        """（见下方实现）"""
        return self._click_move(source, target)

    def _click_move(self, source: str, target: str) -> bool:
        """在对方窗口里把这步棋点出来（起点 + 终点各点一下）。"""
        if not (source and target) or not self.win32.hwnd:
            debug_log(f"连线：跳过点击 {source}{target}（窗口或坐标缺失）")
            return False
        if self.last_grid is None:
            debug_log("连线：跳过点击（还没有识别到棋盘坐标）")
            return False
        point1 = self._client_point(source)
        point2 = self._client_point(target)
        if not point1 or not point2:
            debug_log(f"连线：跳过点击 {source}{target}（坐标换算失败）")
            return False
        delay = float(self.settings.get("link_click_delay_ms") or 60) / 1000.0
        move_delay = float(self.settings.get("link_move_delay_ms") or 200) / 1000.0
        use_back = bool(self.settings.get("link_back_mode", True))
        if use_back:
            self.win32.click(point1[0], point1[1], delay)
        else:
            self.win32.click_front(point1[0], point1[1], delay)
        time.sleep(move_delay)
        if use_back:
            self.win32.click(point2[0], point2[1], delay)
        else:
            self.win32.click_front(point2[0], point2[1], delay)
        self._click_attempts += 1
        factor = self._message_scale()
        extra = ""
        if abs(factor - 1.0) > 0.01:
            extra = (f"｜坐标系数 ×{factor:.2f} → 实际点 {int(round(point1[0] * factor))},"
                     f"{int(round(point1[1] * factor))} 与 {int(round(point2[0] * factor))},"
                     f"{int(round(point2[1] * factor))}")
        debug_log(f"连线：已点击 {source}->{target} 客户区坐标 {point1} {point2}"
                  f"（{'后台' if use_back else '前台'}）{extra}")
        self.on_event("click", {"from": source, "to": target})
        return True

    def _client_point(self, square: str) -> Optional[Tuple[int, int]]:
        """ICCS 坐标（如 h2）→ 目标窗口**客户区**像素坐标。

        客户区坐标与窗口在屏幕上的位置无关（窗口拖到哪儿都一样），所以这里不用管
        位置；真正发给对方之前由 :meth:`Win32.message_scale` 统一做分辨率/DPI 换算。
        """
        if not square or len(square) < 2 or self.last_grid is None:
            return None
        from .board import FILES

        file = FILES.find(square[0].lower())
        try:
            rank = int(square[1])
        except ValueError:
            return None
        if file < 0:
            return None
        x0, y0, cell_x, cell_y = self.last_grid
        col, row = file, 9 - rank
        if self.flipped:
            col = 8 - col
        if self.vertical:
            row = 9 - row
        x = int(x0 + col * cell_x)
        y = int(y0 + row * cell_y)
        width, height = self._client_size()
        if width > 0 and height > 0:            # 保险：别点到客户区外面去
            x = max(0, min(width - 1, x))
            y = max(0, min(height - 1, y))
        return (x, y)


def _boards_equal(a, b) -> bool:
    if not a or not b:
        return False
    return all(a[i][j] == b[i][j] for i in range(10) for j in range(9))


def _flip(board):
    """左右镜像（有些平台把棋盘画成红黑左右颠倒）。"""
    return [list(reversed(row)) for row in board]


def _flip_ud(board):
    """上下翻转（有些平台红方在下、有些红方在上）。"""
    return [list(row) for row in reversed(board)]


def _diff_move(local, remote) -> Optional[Tuple[str, str]]:
    """两个棋盘只差一步棋时，算出 (起点, 终点)，否则返回 None。

    识别难免有个别格子认错，所以这里不要求"正好差 2~3 格"，而是：
    在差异格里找一个"本地有子、对面变空"的起点，和一个"本地空、对面有同一个子"的终点。
    """
    from .board import FILES

    diffs = [(i, j) for i in range(10) for j in range(9) if local[i][j] != remote[i][j]]
    if not diffs or len(diffs) > 6:                 # 差太多说明不是"一步棋"
        return None
    sources = [(i, j) for i, j in diffs
               if local[i][j] != "." and remote[i][j] in (".", "")]
    targets = [(i, j) for i, j in diffs
               if local[i][j] == "." and remote[i][j] not in (".", "")]
    if len(sources) != 1 or len(targets) != 1:
        # 兼容吃子：起点空了、终点换了个子（被吃的那个子算作同一格的变化）
        moved = [(i, j) for i, j in diffs
                 if local[i][j] != "." and remote[i][j] not in (".", "")]
        if len(sources) == 1 and len(moved) == 1:
            targets = moved
        else:
            return None
    source, target = sources[0], targets[0]
    if remote[target[0]][target[1]] != local[source[0]][source[1]]:
        return None                     # 走的不是同一个子（多半是识别错了一格，放弃这一轮）
    try:
        source_name = FILES[source[1]] + str(9 - source[0])
        target_name = FILES[target[1]] + str(9 - target[0])
        return source_name, target_name
    except (IndexError, KeyError):
        return None
