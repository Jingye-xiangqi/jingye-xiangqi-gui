# -*- coding: utf-8 -*-
"""连线分辨率 / DPI 适配测试：1080p、2K、4K × 100% / 125% / 150% / 200% 缩放。

★ 本机只有 1080p 屏，所以这里搭一块"**模拟屏幕**"把 2K/4K 的坐标系摆出来：

  * 目标窗口的客户区按**物理像素**给定（例如 2K 上 1478x1638 的棋盘窗口）；
  * 本程序 DPI 不感知时，Windows 会**虚拟化**我们看到的每一个接口
    （GetClientRect / 截图 / ClientToScreen 都按「系统DPI÷96」缩小）；
  * **``PostMessage`` 的坐标不做任何换算** —— 这正是"1080p 正常、2K 上连线
    完全不同步"的根：对方窗口按物理像素解释坐标，我们不乘缩放系数就会整体
    缩到 2/3，点出去的每一步都落在别的交叉点上。

模拟屏幕里的换算规则（``SimScreen``）是照着 Windows 的定义单独写的，
**不引用被测代码**，这样"点了哪儿"才有意义：link.py 少乘/多乘系数就会失败。
"""

import ctypes
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from xqtrainer.link import Win32, compute_message_scale  # noqa: E402
from xqtrainer.session import Session  # noqa: E402
from xqtrainer.ui import board_image  # noqa: E402

SETTINGS = Path(tempfile.gettempdir()) / "xq_link_resolution_test_settings.json"

#: 标准开局（红方在下，和本程序自己的朝向一致）
START_ROWS = ["rnbakabnr", "9", "1c5c1", "p1p1p1p1p", "9", "9",
              "P1P1P1P1P", "1C5C1", "9", "RNBAKABNR"]


def decode_lparam(lparam: int) -> tuple:
    """把鼠标消息的 lparam 拆回 (x, y)（低 16 位 x、高 16 位 y，都是有符号的）。"""
    value = _as_int(lparam) & 0xFFFFFFFF
    x = ctypes.c_short(value & 0xFFFF).value
    y = ctypes.c_short((value >> 16) & 0xFFFF).value
    return (x, y)


def _as_int(value) -> int:
    """ctypes 的标量（c_longlong 等）在 Python 3.13 上不能用 int() 直接转，取 .value。"""
    return int(getattr(value, "value", value))


# ==================================================================== 模拟屏幕
class SimScreen:
    """一块模拟屏幕：只定义"Windows 会怎么换算"，不碰被测代码。"""

    def __init__(self, physical_size, system_dpi, target_dpi,
                 own_awareness, target_awareness, origin=(0, 0), name=""):
        self.physical_size = (int(physical_size[0]), int(physical_size[1]))
        self.system_dpi = int(system_dpi)
        self.target_dpi = int(target_dpi)
        self.own_awareness = int(own_awareness)
        self.target_awareness = int(target_awareness)
        self.origin = (int(origin[0]), int(origin[1]))
        self.name = name

    @property
    def our_units_per_pixel(self) -> float:
        """我们坐标空间里 1 单位代表几个物理像素（DPI 不感知＝被系统放大）。"""
        return 1.0 if self.own_awareness else self.system_dpi / 96.0

    @property
    def target_units_per_pixel(self) -> float:
        """目标窗口自己的坐标空间里 1 单位代表几个物理像素。"""
        return 1.0 if self.target_awareness else self.target_dpi / 96.0

    def to_our_space(self, value: float) -> float:
        return value / self.our_units_per_pixel

    def client_rect_our_space(self) -> tuple:
        """客户区 (x, y, w, h)：我们看到的（被虚拟化过的）那一份。"""
        x, y = self.origin
        width, height = self.physical_size
        return (self.to_our_space(x), self.to_our_space(y),
                self.to_our_space(width), self.to_our_space(height))

    def message_to_physical(self, value: float) -> float:
        """对方窗口收到坐标后，它认为的**物理**像素位置。"""
        return value * self.target_units_per_pixel


# ==================================================================== 假 API
class FakeShcore:
    def __init__(self, awareness: int):
        self.awareness = int(awareness)

    def GetProcessDpiAwareness(self, _process, pointer):
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int))[0] = self.awareness
        return 0


class FakeUser32:
    """假的 user32：按模拟屏幕回答查询，并记录发出去的消息 / 光标位置。"""

    def __init__(self, own_awareness: int, target_awareness: int,
                 target_dpi: int, system_dpi: int):
        self.own_awareness = int(own_awareness)
        self.target_awareness = int(target_awareness)
        self.target_dpi = int(target_dpi)
        self.system_dpi = int(system_dpi)
        self.messages = []          # [(message, lparam), ...]
        self.cursor = None

    # --- DPI / 屏幕
    def GetThreadDpiAwarenessContext(self):
        return 1

    def GetWindowDpiAwarenessContext(self, _hwnd):
        return 2

    def GetAwarenessFromDpiAwarenessContext(self, context):
        return self.own_awareness if int(context) == 1 else self.target_awareness

    def GetDpiForWindow(self, _hwnd):
        return self.target_dpi

    def GetDpiForSystem(self):
        return self.system_dpi

    def GetSystemMetrics(self, _index):
        return 0

    def GetDC(self, _hwnd):
        return 1

    def ReleaseDC(self, _hwnd, _hdc):
        return 1

    # --- 交互
    def PostMessageW(self, _hwnd, message, _wparam, lparam):
        self.messages.append((_as_int(message), _as_int(lparam)))
        return 1

    def SetCursorPos(self, x, y):
        self.cursor = (_as_int(x), _as_int(y))
        return 1

    def mouse_event(self, *_args):
        return None


class FakeGdi32:
    def __init__(self, screen: SimScreen):
        self.screen = screen

    def GetDeviceCaps(self, _hdc, index):
        # 118＝DESKTOPHORZRES、117＝DESKTOPVERTRES（物理分辨率）
        return int(self.screen.physical_size[0 if index == 118 else 1])


class SimWindow(Win32):
    """模拟"对方象棋窗口"：截图按我们看到的坐标空间给，点击按对方的坐标空间收。"""

    def __init__(self, screen: SimScreen, physical_image: Image.Image,
                 capture_physical: bool = False):
        super().__init__()
        self.screen = screen
        self.physical_image = physical_image
        self.capture_physical = bool(capture_physical)
        self.user32 = FakeUser32(screen.own_awareness, screen.target_awareness,
                                 screen.target_dpi, screen.system_dpi)
        self.gdi32 = FakeGdi32(screen)
        self.shcore = FakeShcore(screen.own_awareness)
        self.hwnd = 1
        self.clicks = []            # 对方窗口收到的落点（对方坐标空间）
        self.captures = 0

    # --- 窗口信息
    def client_rect(self):
        x, y, width, height = self.screen.client_rect_our_space()
        return (int(round(x)), int(round(y)), int(round(width)), int(round(height)))

    def window_title(self):
        return "模拟窗口"

    # --- 截图
    def grab(self):
        self.captures += 1
        if self.capture_physical:
            image = self.physical_image            # 有的客户端直接给物理尺寸
        else:
            ratio = 1.0 / self.screen.our_units_per_pixel
            size = (max(1, int(round(self.physical_image.width * ratio))),
                    max(1, int(round(self.physical_image.height * ratio))))
            image = self.physical_image.resize(size, Image.BILINEAR)
        self._note_capture_scale(image)
        return image

    # --- 点击：走真实的 Win32.click（含 lparam 打包），只把 API 换成假的
    def click(self, x, y, delay: float = 0.05):
        before = len(self.user32.messages)
        super().click(x, y, delay)
        for message, lparam in self.user32.messages[before:]:
            if message == self.WM_LBUTTONUP:       # 抬手消息＝最终落点
                self.clicks.append(decode_lparam(lparam))


class _Size:
    """只带 width/height 的假图（换算只看尺寸）。"""

    def __init__(self, width, height):
        self.width, self.height = int(width), int(height)


class _StubWindow:
    """假的 win32 外壳：只回答客户区（其它查询都当没有）。"""

    def __init__(self, client_size):
        self.hwnd = 1
        self._client = (0, 0, int(client_size[0]), int(client_size[1]))

    def client_rect(self):
        return self._client


class _SettingsMixin:
    """每个用例用独立的临时 settings.json，别和别的测试互相污染。"""

    def setUp(self):
        self._old_env = os.environ.get("XQ_SETTINGS_FILE")
        os.environ["XQ_SETTINGS_FILE"] = str(SETTINGS)
        SETTINGS.unlink(missing_ok=True)

    def tearDown(self):
        SETTINGS.unlink(missing_ok=True)
        if self._old_env is None:
            os.environ.pop("XQ_SETTINGS_FILE", None)
        else:
            os.environ["XQ_SETTINGS_FILE"] = self._old_env


# ==================================================================== 坐标系数
class TestMessageScale(unittest.TestCase):
    """客户区坐标 → 目标窗口坐标 的缩放系数（不依赖真机也能全测）。"""

    #: (本程序感知, 目标感知, 目标DPI, 系统DPI, 期望系数, 说明)
    CASES = (
        (0, 0, 96, 96, 1.0, "1080p/100%：两边都不感知，系数 1"),
        (0, 0, 144, 144, 1.0, "2K/150%：两边都不感知，系数仍是 1"),
        (0, 1, 96, 96, 1.0, "1080p/100%：目标感知，但缩放是 100%"),
        (0, 1, 120, 120, 1.25, "2K/125%：目标感知 → 乘 1.25"),
        (0, 1, 144, 144, 1.5, "2K/150%：目标感知 → 乘 1.5"),
        (0, 1, 192, 192, 2.0, "4K/200%：目标感知 → 乘 2.0"),
        (1, 1, 144, 144, 1.0, "两边都感知：都是物理像素"),
        (1, 0, 144, 144, 96 / 144, "本程序感知、目标不感知 → 反而要缩小"),
        (0, 1, 192, 96, 1.0, "目标在别的显示器上（系统仍是 100%）→ 不缩放"),
    )

    def test_scale_matrix(self):
        for own, target, target_dpi, system_dpi, want, note in self.CASES:
            with self.subTest(note=note):
                got = compute_message_scale(own, target, target_dpi, system_dpi)
                self.assertAlmostEqual(got, want, places=4, msg=note)

    def test_scale_is_clamped(self):
        self.assertGreaterEqual(compute_message_scale(0, 1, 100000, 96), 0.25)
        self.assertLessEqual(compute_message_scale(1, 0, 1, 96), 8.0)


# ==================================================================== 网格换算
class TestGridToClient(_SettingsMixin, unittest.TestCase):
    """截图尺寸 ≠ 客户区尺寸时的换算（2K/DPI 虚拟化最容易踩这里）。"""

    def _linker(self, client_size):
        session = Session()
        linker = session.linker
        linker.win32 = _StubWindow(client_size)
        return linker

    def test_identity_when_sizes_match(self):
        linker = self._linker((1000, 800))
        grid = (10.0, 20.0, 30.0, 40.0)
        self.assertEqual(linker._to_client_grid(grid, _Size(1000, 800)), grid)

    def test_scales_when_capture_is_bigger(self):
        # 截图 1500x1200（物理尺寸）但客户区只有 1000x800 → 整体缩回 2/3
        linker = self._linker((1000, 800))
        got = linker._to_client_grid((15.0, 30.0, 45.0, 60.0), _Size(1500, 1200))
        self.assertEqual(got, (10.0, 20.0, 30.0, 40.0))

    def test_scales_when_capture_is_smaller(self):
        linker = self._linker((1200, 900))
        got = linker._to_client_grid((10.0, 20.0, 30.0, 40.0), _Size(600, 450))
        self.assertEqual(got, (20.0, 40.0, 60.0, 80.0))

    def test_missing_client_size_falls_back(self):
        linker = self._linker((0, 0))
        grid = (1.0, 2.0, 3.0, 4.0)
        self.assertEqual(linker._to_client_grid(grid, _Size(800, 600)), grid)


# ==================================================================== 棋盘区域
class TestRegionPercent(_SettingsMixin, unittest.TestCase):
    """棋盘区域参数：百分比 → 像素，自动跟着分辨率走。"""

    def _linker(self, client_size=(1920, 1080)):
        session = Session()
        linker = session.linker
        linker.win32 = _StubWindow(client_size)
        return linker

    def test_default_is_full_window(self):
        linker = self._linker()
        self.assertIsNone(linker._region_rect(1920, 1080), "默认整窗＝让识别器自己找棋盘")
        self.assertEqual(linker._area_percent(), (0.0, 0.0, 100.0, 100.0))

    def test_percent_follows_resolution(self):
        linker = self._linker()
        linker.session.settings.update({"link_area_x": 10.0, "link_area_y": 10.0,
                                        "link_area_w": 50.0, "link_area_h": 40.0})
        self.assertEqual(linker._region_rect(1920, 1080), (192, 108, 960, 432))
        self.assertEqual(linker._region_rect(2560, 1440), (256, 144, 1280, 576))
        self.assertEqual(linker._region_rect(3840, 2160), (384, 216, 1920, 864))

    def test_legacy_pixel_setting_is_migrated(self):
        """老版本（1.2 及以前）存的是像素，要能自动换算成百分比。"""
        linker = self._linker((1600, 900))
        linker.session.settings.update({"link_area_x": 160.0, "link_area_y": 90.0,
                                        "link_area_w": 800.0, "link_area_h": 450.0})
        self.assertEqual(linker._region_rect(1600, 900), (160, 90, 800, 450))
        self.assertEqual(linker._area_percent(), (10.0, 10.0, 50.0, 50.0))
        self.assertEqual(linker.session.settings.get("link_area_w"), 50.0,
                         "换算结果要写回设置")

    def test_broken_values_fall_back_to_full_window(self):
        linker = self._linker((0, 0))
        linker.session.settings.update({"link_area_w": 800.0, "link_area_h": 600.0})
        self.assertIsNone(linker._region_rect(1920, 1080))


# ==================================================================== 端到端
@unittest.skipUnless((ROOT / "model" / "yolov11.onnx").is_file(),
                     "没有 YOLO 模型（连线识别需要它）")
class TestResolutionPipeline(_SettingsMixin, unittest.TestCase):
    """端到端：模拟屏截图 → 识别 → 算坐标 → PostMessage，检查**落点**。

    这就是"2K 分辨率模拟测试"：2K/4K 的窗口客户区按物理像素给，本程序按 DPI
    不感知的样子去截屏/换算，最后必须点到对方棋盘的正确交叉点上。
    """

    #: (说明, 物理格宽, DPI, 本程序感知, 目标感知, 截图返回物理尺寸?)
    CASES = (
        ("1080p@100%（一直正常的老环境）", 96, 96, 0, 0, False),
        ("2K@125%", 108, 120, 0, 1, False),
        ("2K@150%（用户报的失败环境）", 120, 144, 0, 1, False),
        ("4K@200%", 160, 192, 0, 1, False),
        ("2K@150% + 截图直接给物理尺寸", 120, 144, 0, 1, True),
        ("2K@150% + 目标窗口不感知 DPI", 120, 144, 0, 0, False),
        ("2K@150% + 本程序自己感知 DPI", 120, 144, 2, 1, False),
        ("4K@200% + 本程序感知、目标不感知", 160, 192, 2, 0, False),
    )

    def _prepare(self, cell_phys, dpi, own_aware, target_aware, capture_physical,
                 origin=(0, 0)):
        session = Session()
        linker = session.linker
        # 这几项直接在设置里写好：不走 update_settings，免得顺带触发局面重算/云库查询
        session.settings.update({"link_confirm_frames": 1, "link_orientation": "same",
                                 "link_strict_validate": True, "link_smart_confirm": True,
                                 "link_click_delay_ms": 0, "link_move_delay_ms": 0,
                                 "engine_mode": "red"})
        physical = board_image.render_position(START_ROWS, "w", cell=cell_phys)
        screen = SimScreen(physical.size, dpi, dpi, own_aware, target_aware, origin=origin)
        window = SimWindow(screen, physical, capture_physical=capture_physical)
        linker.win32 = window
        linker.last_grid = None
        linker.orientation = ""
        linker.flipped = False
        linker.vertical = False
        linker.confirmed_board = None
        linker._last_remote_board = None
        linker._synced_any = False
        linker.recognizer.region = None
        return session, linker, screen, window, physical

    @staticmethod
    def _expected_physical(physical_image, cell_phys: int, square: str):
        """某一步棋的起点/终点在**物理像素**上的位置（按绘制棋盘的真实网格算）。"""
        from xqtrainer.board import FILES

        margin = int(cell_phys * 0.62)
        points = []
        for token in (square[:2], square[2:]):
            col = FILES.find(token[0].lower())
            row = 9 - int(token[1])
            points.append((margin + col * cell_phys, margin + row * cell_phys))
        return points

    def test_clicks_land_on_the_right_physical_point(self):
        for name, cell, dpi, own, target, physical_capture in self.CASES:
            with self.subTest(case=name):
                session, linker, screen, window, physical = self._prepare(
                    cell, dpi, own, target, physical_capture)
                linker._tick()
                self.assertTrue(linker.matched, f"{name}：识别结果应与本程序棋盘一致")
                self.assertEqual(linker.pieces, 32, f"{name}：应识别到 32 个棋子")
                self.assertIsNotNone(linker.last_grid, f"{name}：没有算出棋盘网格")

                window.clicks.clear()
                self.assertTrue(linker.click_move("e0", "e9"), f"{name}：点击失败")
                self.assertEqual(len(window.clicks), 2, f"{name}：应该点起点和终点各一次")

                expected = self._expected_physical(physical, cell, "e0e9")
                tolerance = cell * 0.30
                for index, (got, want) in enumerate(zip(window.clicks, expected)):
                    got_physical = (screen.message_to_physical(got[0]),
                                    screen.message_to_physical(got[1]))
                    dx = abs(got_physical[0] - want[0])
                    dy = abs(got_physical[1] - want[1])
                    self.assertLessEqual(
                        dx, tolerance,
                        f"{name}：第 {index + 1} 点横坐标偏了 {dx:.0f}px"
                        f"（坐标系数 {linker.win32.message_scale():.2f}，"
                        f"收到 {got}，期望物理 {want}，实到 {got_physical}）")
                    self.assertLessEqual(
                        dy, tolerance,
                        f"{name}：第 {index + 1} 点纵坐标偏了 {dy:.0f}px"
                        f"（坐标系数 {linker.win32.message_scale():.2f}，"
                        f"收到 {got}，期望物理 {want}，实到 {got_physical}）")

    def test_2k_scale_factor_is_applied(self):
        """2K@150% 必须乘 1.5，否则落点会整体缩到 2/3（用户报的现象）。"""
        session, linker, screen, window, physical = self._prepare(120, 144, 0, 1, False)
        linker._tick()
        self.assertAlmostEqual(linker.win32.message_scale(), 1.5, places=3)
        point = linker._client_point("e0")
        window.clicks.clear()
        self.assertTrue(linker.click_move("e0", "e0"))
        got = window.clicks[0]
        self.assertEqual(got, (int(round(point[0] * 1.5)), int(round(point[1] * 1.5))),
                         "发给对方窗口的坐标必须乘上缩放系数")

    def test_1080p_scale_factor_is_one(self):
        """1080p/100% 的系数必须是 1.0：老环境行为不能变。"""
        session, linker, screen, window, physical = self._prepare(96, 96, 0, 0, False)
        linker._tick()
        self.assertAlmostEqual(linker.win32.message_scale(), 1.0, places=3)

    def test_capture_and_clicks_follow_window_position(self):
        """窗口在屏幕上的位置随便挪，识别和点击都要跟着走（不写死坐标）。"""
        results = []
        for origin in ((0, 0), (800, 300), (1900, 40)):
            session, linker, screen, window, physical = self._prepare(
                120, 144, 0, 1, False, origin=origin)
            linker._tick()
            self.assertTrue(linker.matched, f"窗口在 {origin} 时识别失败")
            left, top, _w, _h = linker.win32.client_rect()
            self.assertEqual((int(round(left)), int(round(top))),
                             (int(round(screen.to_our_space(origin[0]))),
                              int(round(screen.to_our_space(origin[1])))),
                             "客户区原点必须跟着窗口位置走")
            window.clicks.clear()
            self.assertTrue(linker.click_move("e0", "e9"))
            # 后台点击用的是**客户区坐标**，与窗口位置无关 → 三次结果应当一致
            results.append(list(window.clicks))
            # 前台真实点击用的是屏幕坐标 → 必须加上客户区原点
            window.user32.cursor = None
            linker.win32.click_front(30, 40, 0)
            self.assertEqual(
                window.user32.cursor,
                (int(round(screen.to_our_space(origin[0]) + 30)),
                 int(round(screen.to_our_space(origin[1]) + 40))),
                "前台点击的光标位置要跟着窗口位置走")
        self.assertEqual(results[0], results[1], "客户区坐标不该随窗口位置变化")
        self.assertEqual(results[1], results[2], "客户区坐标不该随窗口位置变化")

    def test_environment_description_reports_screen(self):
        """日志里的环境信息要能看出屏幕分辨率/缩放，方便用户在 2K 上报错时排查。"""
        session, linker, screen, window, physical = self._prepare(120, 144, 0, 1, False)
        text = linker.win32.describe_environment()
        self.assertIn(f"{physical.width}x{physical.height}", text,
                      "环境信息里要写实际分辨率")
        self.assertIn("1.50", text, "环境信息里要写缩放系数")
        status = linker.status()
        self.assertAlmostEqual(float(status["messageScale"]), 1.5, places=3)
        self.assertIn("×", str(status["screen"]))


if __name__ == "__main__":
    unittest.main()
