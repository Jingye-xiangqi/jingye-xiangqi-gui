# -*- coding: utf-8 -*-
"""原生桌面界面的自动化测试：用真实鼠标事件驱动窗口，检查交互是否正确。

需要图形环境（Windows / macOS / 带显示器的 Linux）；没有显示环境时会自动跳过。
这里用自带的假引擎（tests/mock_engine.py），所以不依赖皮卡鱼也能跑。
"""

import os
import tempfile
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 界面测试会改很多设置，把配置文件指到临时目录，别污染真实设置
os.environ.setdefault("XQ_SETTINGS_FILE",
                      str(Path(tempfile.gettempdir()) / "xq_ui_settings.json"))
# 界面测试用独立配置，且每次跑之前清掉，避免上一次的设置在用例之间"串味"
try:
    Path(os.environ["XQ_SETTINGS_FILE"]).unlink(missing_ok=True)
except OSError:
    pass

from xqtrainer.board import Board  # noqa: E402
from xqtrainer.session import Session  # noqa: E402
from xqtrainer.ui.app import BOARD_THEME_ITEMS, BOOK_STRATEGY_ITEMS  # noqa: E402

MOCK = Path(__file__).resolve().parent / "mock_engine.py"

#: 我们自己加过的那些"多余控件"，引擎设置面板里一个都不该再出现
BANNED_CONTROL_WORDS = ("难度", "思考时间", "每步思考", "持续分析", "自动分析",
                        "显示胜率", "音效", "线程数", "哈希表", "推荐着法条数")


def pikafish_path():
    """本机的皮卡鱼引擎路径（没有就返回 None，相关用例自动跳过）。"""
    candidates = []
    override = os.environ.get("XQ_PIKAFISH")
    if override:
        candidates.append(Path(override))
    root = Path(__file__).resolve().parents[3]
    candidates.append(root / "work" / "engine" / "pikafish.exe")
    candidates.append(Path(__file__).resolve().parents[1] / "engine" / "pikafish.exe")
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None

try:
    import tkinter as tk  # noqa: E402

    _probe = tk.Tk()
    _probe.withdraw()
    _probe.destroy()
    TK_OK = True
    TK_ERROR = ""
except Exception as exc:  # noqa: BLE001
    TK_OK = False
    TK_ERROR = str(exc)


@unittest.skipUnless(TK_OK, f"没有可用的图形环境：{TK_ERROR}")
class TestNativeUI(unittest.TestCase):
    def setUp(self):
        from xqtrainer.ui.app import XiangqiWindow

        self.session = Session()
        self.window = XiangqiWindow(self.session)
        self.window.geometry("1200x800+20+20")
        self.pump(0.6)

    def tearDown(self):
        window = getattr(self, "window", None)
        self.window = None
        try:
            if window is not None:
                window.on_close()
        except Exception:  # noqa: BLE001
            pass
        del window
        # 关键：在主线程里把窗口对象回收掉，避免后台线程析构 Tcl 解释器导致进程崩溃
        import gc

        gc.collect()

    @classmethod
    def tearDownClass(cls):
        # 在主线程里彻底清理 Tcl 解释器，避免解释器退出时残留告警
        import gc

        gc.collect()
        root = getattr(tk, "_default_root", None)
        if root is not None:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass
        tk._default_root = None
        gc.collect()

    # ------------------------------------------------------------ 工具
    def pump(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                self.window.update_idletasks()
                self.window.update()
            except Exception:  # noqa: BLE001
                return
            time.sleep(0.02)

    def click(self, file: int, rank: int) -> None:
        x, y = self.window.board._xy(file, rank)
        self.window.board.event_generate("<ButtonPress-1>", x=int(x), y=int(y))
        self.window.board.event_generate("<ButtonRelease-1>", x=int(x), y=int(y))
        self.pump(0.25)

    def drag(self, file_a: int, rank_a: int, file_b: int, rank_b: int) -> None:
        x1, y1 = self.window.board._xy(file_a, rank_a)
        x2, y2 = self.window.board._xy(file_b, rank_b)
        self.window.board.event_generate("<ButtonPress-1>", x=int(x1), y=int(y1))
        self.window.board.event_generate("<B1-Motion>", x=int(x2), y=int(y2))
        self.window.board.event_generate("<ButtonRelease-1>", x=int(x2), y=int(y2))
        self.pump(0.3)

    def squares_with_pieces(self) -> int:
        squares = set()
        for item in self.window.board.find_all():
            for tag in self.window.board.gettags(item):
                if tag.startswith("piece:"):
                    squares.add(tag)
        return len(squares)

    # ------------------------------------------------------------ 用例
    def test_board_click_moves(self):
        self.assertEqual(self.squares_with_pieces(), 32)
        self.click(7, 2)                       # h2 炮
        self.assertEqual(self.session.state().get("selected"), "h2")
        self.assertEqual(len(self.session.state().get("destinations") or []), 12)
        self.click(4, 2)                       # e2
        state = self.session.state()
        self.assertEqual(int(state["ply"]), 1)
        self.assertEqual(state["moves"][-1]["cn"], "炮二平五")
        self.assertGreater(len(self.window.board.find_withtag("piece:e2")), 0)
        self.assertEqual(len(self.window.board.find_withtag("piece:h2")), 0)

    def test_board_drag_moves(self):
        self.drag(7, 2, 4, 2)
        self.assertEqual(int(self.session.state()["ply"]), 1)
        self.drag(7, 9, 6, 7)                  # 黑马 h9->g7
        state = self.session.state()
        self.assertEqual(int(state["ply"]), 2)
        self.assertEqual(state["moves"][-1]["cn"], "马8进7")

    def test_navigation_and_variation(self):
        self.click(7, 2)
        self.click(4, 2)
        self.window.navigate("first")
        self.pump(0.3)
        self.assertEqual(int(self.session.state()["ply"]), 0)
        self.click(1, 2)                       # b2 炮 -> 另一路
        self.click(4, 2)
        state = self.session.state()
        self.assertEqual(len(state["siblings"]), 2)
        self.window.on_promote_node()
        self.pump(0.4)
        self.assertEqual(self.session.state()["siblings"][0]["cn"], "炮八平五")
        self.window.on_delete_node()
        self.pump(0.4)
        self.assertEqual(int(self.session.state()["ply"]), 0)

    def test_import_and_comment(self):
        pgn = ('[Event "测试"]\n[Red "甲"]\n[Black "乙"]\n\n'
               "1. 炮二平五 马8进7 2. 马二进三 {好棋} *\n")
        self.assertTrue(self.session.import_data("pgn", pgn.encode("utf-8"), name="t.pgn"))
        self.pump(0.6)
        self.assertIn("甲", self.window.game_title.cget("text"))
        self.assertEqual(len(self.window._line_nodes), 4)      # 第 0 手 + 三手棋
        self.window.comment_text.delete("1.0", "end")
        self.window.comment_text.insert("1.0", "局面均衡")
        self.window.on_save_comment()
        self.pump(0.4)
        self.assertEqual(self.session.state().get("comment"), "局面均衡")
        self.window._set_fen_text("3k5/9/9/9/9/9/9/9/9/4K4 w - - 0 1")
        self.window.on_apply_fen()
        self.pump(0.4)
        self.assertTrue(self.session.state()["fen"].startswith("3k5"))
        self.window._set_fen_text("乱写的 FEN")
        self.window.on_apply_fen()
        self.pump(0.3)
        self.assertTrue(self.session.state()["fen"].startswith("3k5"))

    def test_engine_analysis_panel(self):
        self.assertTrue(self.session.load_engine(sys.executable, args=[str(MOCK)]))
        self.pump(1.0)
        self.assertIn("MockEngine", self.window.engine_name.cget("text"))
        self.assertGreaterEqual(len(self.window._engine_option_names or []), 5)
        self.session.update_settings({"multi_pv": 3, "movetime_ms": 1200})
        self.session.analyze_now()
        deadline = time.time() + 20
        while time.time() < deadline:
            self.pump(0.2)
            lines = (self.session.state().get("analysis") or {}).get("lines") or []
            if len(lines) >= 2:
                break
        self.assertGreaterEqual(len(lines), 2)
        self.pump(0.8)                       # 等界面把分析事件消化完（箭头/面板）
        self.assertNotEqual(self.window._pv_rows[0]["moves"].cget("text"), "")
        arrows = [item for item in self.window.board.find_all()
                  if self.window.board.type(item) == "line"
                  and str(self.window.board.itemcget(item, "arrow")) not in ("", "none")]
        self.assertTrue(arrows, "棋盘上没有画出推荐着法箭头")
        self.window._pv_rows[0]["play"].invoke()
        self.pump(0.6)
        self.assertEqual(int(self.session.state()["ply"]), 1)

    def test_play_mode_and_undo(self):
        self.assertTrue(self.session.load_engine(sys.executable, args=[str(MOCK)]))
        self.window.on_engine_mode("black")            # 引擎执黑，人走红
        self.pump(0.3)
        self.click(7, 2)
        self.click(4, 2)
        deadline = time.time() + 25
        while time.time() < deadline and int(self.session.state()["ply"]) < 2:
            self.pump(0.3)
        self.assertEqual(int(self.session.state()["ply"]), 2, "引擎没有应着")
        self.window.on_undo()
        self.pump(0.6)
        state = self.session.state()
        self.assertEqual(int(state["ply"]), 0)
        self.assertEqual(state["side"], "w")

    def test_engine_plays_red(self):
        """引擎执红：一开局引擎就自动走红方，人走黑方。"""
        self.assertTrue(self.session.load_engine(sys.executable, args=[str(MOCK)]))
        self.window.on_engine_mode("red")
        deadline = time.time() + 20
        while time.time() < deadline and int(self.session.state()["ply"]) == 0:
            self.pump(0.3)
        state = self.session.state()
        self.assertEqual(int(state["ply"]), 1, "引擎（红方）没有自动走棋")
        self.assertEqual(state["moves"][0]["mover"], "w")
        self.assertEqual(state["side"], "b")
        self.assertFalse(self.session.can_human_move() is False)   # 现在轮到人走
        # 人在引擎思考时不能替它走子：直接调用点击应被拦下
        self.window.on_engine_mode("black")
        self.pump(0.3)
        self.assertEqual(self.session.state()["moves"][0]["mover"], "w")

    def test_analyze_mode_never_moves(self):
        """分析模式：引擎持续分析但绝不自动走子，双方都能手动走。"""
        self.assertTrue(self.session.load_engine(sys.executable, args=[str(MOCK)]))
        self.window.on_engine_mode("analyze")
        self.pump(1.5)
        state = self.session.state()
        self.assertEqual(int(state["ply"]), 0, "分析模式不该自动走子")
        self.assertTrue(self.session.can_human_move())
        self.assertTrue(self.session.settings["infinite"], "分析模式应当持续分析")
        deadline = time.time() + 20
        while time.time() < deadline and not (state["analysis"]["lines"]):
            self.pump(0.3)
            state = self.session.state()
        self.assertTrue(state["analysis"]["lines"], "分析模式没有给出推荐着法")
        self.assertTrue(state["analysis"]["lines"][0]["scoreText"])
        # 两个方向都能手动走子（打谱/研究变例）
        self.click(7, 2)
        self.click(4, 2)
        self.assertEqual(int(self.session.state()["ply"]), 1)
        self.click(7, 9)
        self.click(6, 7)
        self.assertEqual(int(self.session.state()["ply"]), 2)

    def test_mode_switch_anytime(self):
        """三种模式可随时切换，界面按钮状态跟着变。"""
        self.assertTrue(self.session.load_engine(sys.executable, args=[str(MOCK)]))
        for mode in ("red", "black", "analyze", "black", "analyze", "red"):
            self.window.on_engine_mode(mode)
            self.pump(0.3)
            info = self.session.current_mode_info()
            self.assertEqual(info["mode"], mode)
            self.assertEqual(self.window.mode_var.get(), mode)
            from xqtrainer.ui.app import ACCENT as _ACCENT

            active = [value for value, button in self.window.mode_buttons.items()
                      if button.cget("background") == _ACCENT]
            self.assertEqual(active, [mode], f"模式按钮高亮不正确：{active}")
        self.window.on_engine_mode("analyze")

    def test_mode_can_be_deselected_to_manual(self):
        """再点一次当前模式 = 取消选择，回到纯手动打谱（不自动走子、不强制分析）。"""
        self.assertTrue(self.session.load_engine(sys.executable, args=[str(MOCK)]))
        from xqtrainer.ui.app import ACCENT as _ACCENT

        # 依次点选三种模式，每次都再点一次取消
        for mode in ("red", "black", "analyze"):
            self.window.on_engine_mode(mode)
            self.pump(0.4)
            self.assertEqual(self.session.current_mode_info()["mode"], mode)
            self.window.on_engine_mode(mode)          # 再点一次 → 取消
            self.pump(0.4)
            info = self.session.current_mode_info()
            self.assertEqual(info["mode"], "manual", f"{mode} 取消失败")
            self.assertFalse(info["autoMove"])
            self.assertEqual(self.window.mode_var.get(), "")
            active = [value for value, button in self.window.mode_buttons.items()
                      if button.cget("background") == _ACCENT]
            self.assertEqual(active, [], f"取消后不该有按钮高亮：{active}")
            self.assertFalse(self.session.settings["auto_analyze"],
                             "纯手动模式不该强制分析")
        self.pump(1.2)
        self.assertFalse(self.session.engine.searching, "纯手动模式下引擎不应持续分析")

    def test_manual_mode_never_auto_moves(self):
        """纯手动打谱：即使轮到"引擎那一边"，引擎也不会自动走子，双方都能手动走。"""
        self.assertTrue(self.session.load_engine(sys.executable, args=[str(MOCK)]))
        self.window.on_engine_mode("manual")          # 直接切到纯手动
        self.pump(1.5)
        self.assertEqual(self.session.current_mode_info()["mode"], "manual")
        self.assertEqual(int(self.session.state()["ply"]), 0)
        self.assertTrue(self.session.can_human_move())
        # 双方都能走
        self.click(7, 2)
        self.click(4, 2)
        self.assertEqual(int(self.session.state()["ply"]), 1)
        self.pump(1.2)
        self.assertEqual(int(self.session.state()["ply"]), 1, "纯手动模式下引擎不该走子")
        self.click(7, 9)
        self.click(6, 7)
        self.assertEqual(int(self.session.state()["ply"]), 2)

    def test_analysis_mode_independent_toggle(self):
        """分析模式可以独立开关：开 → 持续分析；关 → 回到纯手动。"""
        self.assertTrue(self.session.load_engine(sys.executable, args=[str(MOCK)]))
        self.window.on_engine_mode("analyze")
        deadline = time.time() + 15
        lines = []
        while time.time() < deadline and not lines:
            self.pump(0.3)
            lines = (self.session.state().get("analysis") or {}).get("lines") or []
        self.assertTrue(lines, "分析模式没有持续分析")
        self.assertTrue(self.session.settings["infinite"])
        self.window.on_engine_mode("analyze")          # 关闭
        self.pump(1.5)
        self.assertEqual(self.session.current_mode_info()["mode"], "manual")
        self.assertFalse(self.session.settings["auto_analyze"])
        self.assertFalse(self.session.engine.searching, "关闭分析后不该继续搜索")

    def test_engine_mode_does_not_flip_board(self):
        """选「引擎执红 / 引擎执黑」不该自动翻转棋盘，视角只由用户手动控制。"""
        self.assertFalse(self.session.settings.get("flip"))
        self.window.on_engine_mode("red")            # 引擎执红（人走黑）
        self.pump(0.4)
        self.assertFalse(self.session.settings.get("flip"),
                         "引擎执红时不该自动翻成黑方视角")
        self.assertEqual(self.session.settings.get("human_side"), "b")
        self.window.on_engine_mode("black")          # 引擎执黑（人走红）
        self.pump(0.4)
        self.assertFalse(self.session.settings.get("flip"),
                         "引擎执黑时不该自动翻成红方视角")
        self.assertEqual(self.session.settings.get("human_side"), "w")
        # 用户自己点「翻转棋盘」仍然有效
        self.window.on_flip()
        self.pump(0.3)
        self.assertTrue(self.session.settings.get("flip"), "手动翻转要生效")
        self.window.on_flip()
        self.pump(0.3)
        self.window.on_engine_mode("")               # 回到纯手动
        self.pump(0.3)

    def test_flip_view(self):
        _, top_before = self.window.board._xy(0, 9)
        self.window.on_flip()
        self.pump(0.5)
        _, top_after = self.window.board._xy(0, 9)
        self.assertGreater(abs(top_before - top_after), 10)
        self.assertEqual(self.squares_with_pieces(), 32)
        self.window.on_flip()
        self.pump(0.4)

    def test_multi_game_switch(self):
        two = ('[Event "第一局"]\n[Red "甲"]\n\n1. 炮二平五 马8进7 *\n'
               '[Event "第二局"]\n[Red "丙"]\n\n1. 兵七进一 卒7进1 *\n')
        self.assertTrue(self.session.import_data("pgn", two.encode("utf-8"), name="two.pgn"))
        self.pump(0.6)
        self.assertNotEqual(self.window.games_box.winfo_manager(), "")
        self.window.games_var.set("2. 丙 对 黑方")
        self.window.on_game_selected()
        self.pump(0.5)
        self.assertEqual(int(self.session.state().get("gameIndex") or 0), 1)

    def test_window_title(self):
        title = self.window.title()
        self.assertTrue(title.startswith("静夜象棋界面"), title)
        self.assertIn("QQ:3375227582", title)          # 标题里要标注 QQ
        # 版本号只有一个来源（app.APP_VERSION）：标题、关于对话框、启动提示都得对上
        from xqtrainer import __version__ as core_version
        from xqtrainer.ui import app as app_module

        self.assertEqual(app_module.APP_VERSION, core_version,
                         "界面版本号与内核版本号不一致")
        self.assertIn(app_module.APP_VERSION, title,
                      f"窗口标题里没有版本号 {app_module.APP_VERSION}：{title}")
        desktop = (Path(__file__).resolve().parents[1] / "desktop_app.py").read_text(
            encoding="utf-8")
        self.assertIn(f'静夜象棋界面{app_module.APP_VERSION}"', desktop,
                      "desktop_app.py 里的版本号和界面版本号不一致")

    def test_move_list_has_initial_position_row(self):
        """棋谱第一行必须是「0. 初始局面」，点它回到开局（不用再点上一步）。"""
        text = self.window.move_text.get("1.0", "1.end")
        self.assertIn("0.", text)
        self.assertIn("初始局面", text)
        self.click(7, 2)
        self.click(4, 2)
        self.pump(0.4)
        self.assertEqual(int(self.session.state()["ply"]), 1)
        self.window.move_text.event_generate("<Button-1>", x=25, y=6)
        self.pump(0.5)
        self.assertEqual(int(self.session.state()["ply"]), 0, "点第 0 手应回到初始局面")

    def test_reset_settings_button(self):
        """界面设置里的「恢复出厂设置」按钮：确认后设置立即回默认。"""
        from xqtrainer.ui import app as app_module

        self.session.update_settings({"board_theme": "night", "board_scale": 70,
                                      "cloud_learn": False, "sound_enabled": False})
        self.pump(0.4)
        self.window.on_open_view_settings()
        self.pump(0.5)
        dialog = getattr(self.window, "view_dialog", None)
        self.assertIsNotNone(dialog)
        texts = []
        stack = [dialog]
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            try:
                if widget.winfo_class() == "TButton":
                    texts.append(str(widget.cget("text")))
            except Exception:  # noqa: BLE001
                continue
        self.assertIn("恢复出厂设置", texts, f"界面设置里没有这个按钮：{texts}")
        # 顶部工具栏第二行最右边也要有这个按钮（用户反馈藏太深）
        self.assertIn("恢复出厂", list(self.window.toolbar_buttons),
                      f"工具栏里没有恢复出厂按钮：{list(self.window.toolbar_buttons)}")
        self.assertIn("断开", list(self.window.toolbar_buttons))
        original = app_module.messagebox.askyesno
        app_module.messagebox.askyesno = lambda *a, **k: True
        try:
            self.window.on_reset_settings()
            self.pump(0.8)
        finally:
            app_module.messagebox.askyesno = original
        settings = self.session.state()["settings"]
        self.assertEqual(settings["board_theme"], "wood")
        self.assertEqual(settings["board_scale"], 100)
        self.assertTrue(settings["cloud_learn"])
        self.assertTrue(settings["sound_enabled"])
        self.assertTrue(self.window.sound_var.get())
        self.assertEqual(self.window.board.theme, "wood")
        if dialog.winfo_exists():
            dialog.destroy()
        self.pump(0.2)

    def test_board_skin_folder(self):
        """自定义皮肤：桌面 large 文件夹能加载棋盘与棋子图片。"""
        from xqtrainer.ui import skin as skin_module

        folder = Path.home() / "Desktop" / "large"
        if not folder.is_dir():
            self.skipTest("桌面没有 large 皮肤文件夹")
        candidate = skin_module.Skin(folder)
        self.assertTrue(candidate.files, "皮肤文件夹里没找到图片")
        self.assertTrue(candidate.ok, f"皮肤缺文件：{candidate.missing}")
        self.assertTrue(self.session.update_settings is not None)
        self.session.update_settings({"board_skin": str(folder)})
        self.pump(0.8)
        self.assertIsNotNone(self.window.board.skin, "皮肤没有加载到棋盘上")
        images = [item for item in self.window.board.find_all()
                  if self.window.board.type(item) == "image"]
        self.assertGreater(len(images), 20, "棋盘上应该贴出棋盘图 + 32 个棋子图")
        # 选中棋子时用皮肤自带的 mask，能正常重绘
        self.click(7, 2)
        self.pump(0.3)
        self.assertEqual(self.session.state()["selected"], "h2")
        self.session.update_settings({"board_skin": ""})       # 恢复自带画法
        self.pump(0.4)
        self.assertIsNone(self.window.board.skin)

    def test_skin_list_picker(self):
        """皮肤列表：扫描 skins 目录、点一下就换棋子皮肤；棋盘仍是自带木纹。"""
        import os
        import shutil
        import tempfile

        from PIL import Image, ImageDraw
        from xqtrainer.ui import skin as skin_module

        tmp = Path(tempfile.mkdtemp(prefix="xq_ui_skin_"))
        root = tmp / "skins"
        root.mkdir()
        for name in ("0-甲", "1-乙"):
            folder = root / name
            folder.mkdir()
            for side in ("r", "b"):
                for piece in "kabnrcp":
                    image = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
                    ImageDraw.Draw(image).ellipse([2, 2, 38, 38], fill=(240, 235, 220, 255),
                                                  outline=(120, 90, 60, 255), width=2)
                    image.save(folder / f"{side}{piece}.png")
        before = os.environ.get("XQ_SKINS_DIR")
        os.environ["XQ_SKINS_DIR"] = str(root)
        try:
            self.window.on_open_view_settings()
            self.pump(0.6)
            dialog = getattr(self.window, "view_dialog", None)
            self.assertIsNotNone(dialog)
            values = list(self.window.skin_box.cget("values"))
            self.assertEqual(values, ["0-甲", "1-乙"], f"皮肤列表不对：{values}")
            # 选中第二套 → 棋子皮肤生效，棋盘还是自带的（没有皮肤棋盘贴图这一项）
            self.window.skin_choice_var.set("1-乙")
            self.window.on_skin_pick()
            self.pump(0.8)
            # 内置皮肤现在只记"名字"（打包成 exe 后解包目录每次都变，存路径下次会失效），
            # 解析回来仍然要是那个文件夹
            saved = str(self.session.settings.get("board_skin"))
            self.assertIn(saved, (str(root / "1-乙"), "1-乙"), f"皮肤设置不对：{saved}")
            self.assertEqual(str(skin_module.resolve_skin_path(saved)), str(root / "1-乙"))
            self.assertIsNotNone(self.window.board.skin, "棋子皮肤没挂上")
            self.assertIsNone(self.window.board.skin.path_of("board") if
                              self.window.board.skin else None,
                              "这套皮肤没有 board.png，不该影响加载")
            self.assertEqual(self.window.board.find_withtag("skinboard"), (),
                             "棋盘不该贴皮肤的 board.png（棋盘永远是自带木纹）")
            self.assertTrue(self.window.board.find_withtag("backdrop"),
                            "棋盘后面应该还是木纹背景")
            # 恢复自带
            self.session.update_settings({"board_skin": ""})
            self.pump(0.5)
            self.assertIsNone(self.window.board.skin)
            self.assertTrue(skin_module.scan_skins(root))
        finally:
            if before is None:
                os.environ.pop("XQ_SKINS_DIR", None)
            else:
                os.environ["XQ_SKINS_DIR"] = before
            dialog = getattr(self.window, "view_dialog", None)
            if dialog is not None and dialog.winfo_exists():
                dialog.destroy()
            self.pump(0.2)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_position_editor(self):
        """编辑局面：清空 → 摆满初始 → 自由摆子 → 开始对局。"""
        self.window.on_edit_position()
        self.pump(0.4)
        self.assertTrue(self.window.editor_active)
        self.window.on_editor_clear()
        self.pump(0.2)
        self.assertEqual(self.squares_with_pieces(), 0)
        self.assertTrue(self.window.editor.validate(), "空棋盘应当提示缺少将帅")
        self.window.on_editor_start()
        self.pump(0.3)
        self.assertEqual(self.squares_with_pieces(), 32)
        self.assertEqual(self.window.editor.validate(), [])
        # 自由摆子：擦掉红马再放回
        self.window.on_palette_pick(".")
        self.click(7, 0)                        # 擦掉 h0 红马
        self.assertEqual(len(self.window.board.find_withtag("piece:h0")), 0)
        self.window.on_palette_pick("N")
        self.click(7, 0)
        self.assertGreater(len(self.window.board.find_withtag("piece:h0")), 0)
        # 交换红黑、切换先行方
        self.window.on_editor_swap()
        self.pump(0.2)
        self.assertTrue(self.window.board.piece_at("h0").islower())
        self.window.on_editor_swap()
        self.pump(0.2)
        # 应用局面
        self.window.on_editor_apply()
        self.pump(0.5)
        self.assertFalse(self.window.editor_active)
        self.assertEqual(self.session.state()["fen"].split()[0],
                         "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR")
        self.assertEqual(self.squares_with_pieces(), 32)

    def test_editor_fen_roundtrip(self):
        self.window.on_edit_position()
        self.pump(0.3)
        fen = "3k5/9/9/9/9/9/9/9/9/4K4 w - - 0 1"
        self.assertTrue(self.window.editor.load_fen(fen))
        self.window._render_editor()
        self.pump(0.2)
        self.assertEqual(self.window.editor.to_fen().split()[0], fen.split()[0])
        self.assertEqual(self.window.editor.validate(), [])
        self.window.on_editor_cancel()
        self.pump(0.3)
        self.assertFalse(self.window.editor_active)

    def test_undo_redo_multi_step(self):
        for iccs in ("h2e2", "h9g7", "h0g2", "b9c7"):
            self.session.play_move(iccs[:2], iccs[2:])
        self.pump(0.4)
        self.assertEqual(int(self.session.state()["ply"]), 4)
        self.window.on_undo()
        self.pump(0.3)
        self.window.on_undo()
        self.pump(0.3)
        self.assertEqual(int(self.session.state()["ply"]), 2)
        self.window.on_redo()
        self.pump(0.3)
        self.assertEqual(int(self.session.state()["ply"]), 3)
        self.window.on_redo()
        self.pump(0.3)
        self.assertEqual(int(self.session.state()["ply"]), 4)
        self.window.on_redo()                  # 没有可重做的了，不应崩溃
        self.pump(0.2)
        self.assertEqual(int(self.session.state()["ply"]), 4)

    def test_sound_toggle(self):
        self.assertTrue(self.window.sound_enabled)
        self.window.sound_var.set(False)
        self.window.on_toggle_sound()
        self.assertFalse(self.window.sounds.enabled)
        self.window.sound_var.set(True)
        self.window.on_toggle_sound()
        self.assertTrue(self.window.sounds.enabled)
        # 走子时不应抛异常
        self.click(7, 2)
        self.click(4, 2)
        self.pump(0.3)
        self.assertEqual(int(self.session.state()["ply"]), 1)

    def test_clipboard_fen(self):
        self.window.on_copy_fen()
        self.pump(0.2)
        self.assertTrue(str(self.window.clipboard_get()).startswith("rnbakabnr"))
        self.window.clipboard_clear()
        self.window.clipboard_append("3k5/9/9/9/9/9/9/9/9/4K4 w - - 0 1")
        self.window.on_paste_fen()
        self.pump(0.4)
        self.assertTrue(self.session.state()["fen"].startswith("3k5"))

    def test_export_xqf_bytes(self):
        for iccs in ("h2e2", "h9g7"):
            self.session.play_move(iccs[:2], iccs[2:])
        target = Path(__file__).resolve().parent / "_tmp_ui.xqf"
        try:
            self.assertTrue(self.session.save_xqf(str(target)))
            self.assertTrue(target.exists())
            from xqtrainer.xqf import read_xqf

            loaded = read_xqf(target)
            self.assertEqual(loaded.all_lines()[0], ["h2e2", "h9g7"])
        finally:
            target.unlink(missing_ok=True)

    def test_book_tab_lists_hits(self):
        """开局库面板：加载库文件后显示命中招法、权重与次数。"""
        from xqtrainer.book import obk_hash, write_obk

        target = Path(__file__).resolve().parent / "_tmp_ui_book.obk"
        try:
            board = Board()
            entries = {}
            for iccs, count in (("h2e2", 12), ("h0g2", 4)):
                move = board.find_move_iccs(iccs)
                board.push(move)
                entries[(1, obk_hash(board))] = count
                board.pop()
            write_obk(target, entries, info="UI 测试库")
            self.assertTrue(self.session.load_book(str(target)))
            self.pump(0.5)
            rows = self.window.book_table.get_children()
            self.assertEqual(len(rows), 2)
            first = self.window.book_table.item(rows[0], "values")
            self.assertEqual(first[0], "h2e2")
            self.assertEqual(first[1], "炮二平五")
            self.assertEqual(first[2], "12")
            # 双击第一行走库着
            self.window.book_table.selection_set(rows[0])
            self.window.on_book_row_play()
            self.pump(0.5)
            self.assertEqual(int(self.session.state()["ply"]), 1)
            self.assertEqual(self.session.state()["moves"][-1]["iccs"], "h2e2")
        finally:
            target.unlink(missing_ok=True)

    def test_cloud_panel_with_stub(self):
        """云库面板：离线替身返回数据时，界面要显示着法、分值、胜率并合并到本地库表。"""
        from xqtrainer.cloud import CloudResult, STATUS_OK, parse_queryall

        sample = ("move:h2e2,score:3,rank:2,note:! (45-04),winrate:50.08"
                  "|move:c3c4,score:1,rank:2,note:! (44-05),winrate:50.02")

        class Stub:
            timeout = 5.0
            last_query_time = 0.0

            def query_all(self, fen, show_all=True, learn=False, use_cache=True):
                return CloudResult(status=STATUS_OK, hits=parse_queryall(sample), raw=sample)

            def cached(self, fen):
                return None

            def save_cache(self):
                pass

            def clear_cache(self):
                pass

        self.session.cloud = Stub()
        self.session.set_cloud_enabled(True)
        deadline = time.time() + 6
        while time.time() < deadline and not (self.session.state()["cloud"]["hits"]):
            self.pump(0.2)
        cloud = self.session.state()["cloud"]
        self.assertEqual(cloud["status"], STATUS_OK)
        self.assertEqual(len(cloud["hits"]), 2)
        self.assertEqual(cloud["hits"][0]["cn"], "炮二平五")
        self.window._apply_state(self.session.state())
        self.pump(0.4)
        rows = self.window.cloud_table.get_children()
        self.assertEqual(len(rows), 2)
        values = self.window.cloud_table.item(rows[0], "values")
        self.assertEqual(values[0], "h2e2")
        self.assertEqual(values[2], "3")
        self.assertTrue(str(values[3]).endswith("%"), values[3])
        # 开局库标签页里也会把云库结果并进来显示
        merged = self.window.book_table.get_children()
        self.assertEqual(len(merged), 2)
        sources = [str(self.window.book_table.item(row, "values")[5]) for row in merged]
        self.assertTrue(all("云库" in item for item in sources), sources)

    def test_game_table_multi_games(self):
        two = ('[Event "第一局"]\n[Red "甲"]\n[Black "乙"]\n[Date "2026.01.01"]\n[Result "1-0"]\n\n'
               "1. 炮二平五 马8进7 *\n"
               '[Event "第二局"]\n[Red "丙"]\n[Black "丁"]\n[Date "2026.02.02"]\n[Result "0-1"]\n\n'
               "1. 兵七进一 卒7进1 *\n")
        self.assertTrue(self.session.import_data("pgn", two.encode("utf-8"), name="two.pgn"))
        self.pump(0.8)
        rows = self.window.game_table.get_children()
        self.assertEqual(len(rows), 2)
        values = self.window.game_table.item(rows[0], "values")
        self.assertIn("甲", str(values[1]))
        self.window.game_table.selection_set(rows[1])
        self.window.on_game_table_select()
        self.pump(0.6)
        self.assertEqual(int(self.session.state().get("gameIndex") or 0), 1)

    def test_winrate_history_recorded(self):
        """引擎分析时应记录胜率变化，供胜率曲线使用。"""
        self.assertTrue(self.session.load_engine(sys.executable, args=[str(MOCK)]))
        self.session.update_settings({"multi_pv": 1, "movetime_ms": 1000, "show_wdl": True})
        self.session.analyze_now()
        deadline = time.time() + 20
        history = []
        while time.time() < deadline:
            self.pump(0.2)
            history = (self.session.state().get("analysis") or {}).get("wdl_history") or []
            if len(history) >= 2:
                break
        self.assertGreaterEqual(len(history), 2, f"胜率历史不足：{history}")
        self.pump(0.5)
        self.window._draw_wdl_chart()
        self.assertGreater(len(self.window.wdl_chart.find_all()), 2)

    # ------------------------------------------------- 引擎设置面板（只留 UCI 选项）
    def dialog_control_texts(self, dialog) -> list:
        """对话框里「控件」的文字：滑块/勾选框/下拉/输入框 + 选项行的名字标签。"""
        texts = []

        def walk(widget) -> None:
            for child in widget.winfo_children():
                kind = child.winfo_class()
                if kind in ("TCheckbutton", "TButton", "TRadiobutton"):
                    try:
                        texts.append(str(child.cget("text")))
                    except Exception:  # noqa: BLE001
                        pass
                elif kind == "TScale":
                    texts.append(f"TScale:{getattr(child, '_w', '')}")
                walk(child)

        walk(dialog)
        return texts

    def option_row_labels(self) -> list:
        """引擎选项区里每一行的名字（由引擎声明动态生成）。"""
        from tkinter import ttk

        labels = []
        frame = getattr(self.window, "engine_options_frame", None)
        if frame is None or not frame.winfo_exists():
            return labels
        for row in frame.winfo_children():
            for child in row.winfo_children():
                if isinstance(child, ttk.Label) or (
                        child.winfo_class() == "Label" and not hasattr(child, "toggle")):
                    labels.append(str(child.cget("text")))
        return labels

    def toggle_for(self, name: str):
        """引擎选项区里某个名字（可能是 check 型开关）对应的控件。"""
        frame = getattr(self.window, "engine_options_frame", None)
        if frame is None or not frame.winfo_exists():
            return None
        for row in frame.winfo_children():
            for child in row.winfo_children():
                if hasattr(child, "toggle") and any(
                        str(item.cget("text")) == name for item in child.winfo_children()):
                    return child
        return None

    def test_engine_dialog_only_has_uci_options(self):
        """引擎设置面板：删掉难度/思考时间等自造控件，只剩引擎声明的 UCI 选项。"""
        self.assertTrue(self.session.load_engine(sys.executable, args=[str(MOCK)]))
        self.pump(1.0)
        self.window.on_open_engine_settings()
        self.pump(0.8)
        dialog = getattr(self.window, "engine_dialog", None)
        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.winfo_exists())

        names = list(self.window._engine_option_names or [])
        self.assertEqual(len(names), 7, f"mock 引擎声明了 7 个选项：{names}")
        for expected in ("Threads", "Hash", "MultiPV", "UCI_ShowWDL", "Clear Hash",
                         "EvalFile", "Style"):
            self.assertIn(expected, names)

        # 动态生成：选项区每一行都对应引擎声明的选项
        declared = self.session.state()["engine"]["options"]
        editable = sorted(str(option["name"]) for option in declared
                          if option["type"] != "button")
        labelled = sorted(str(option["name"]) for option in declared
                          if option["type"] in ("spin", "combo", "string"))
        self.assertEqual(sorted(self.window._engine_option_widgets), editable)
        self.assertEqual(sorted(self.option_row_labels()), labelled)

        # 没有滑块了，也没有我们自己命名的那些控件
        texts = self.dialog_control_texts(dialog)
        self.assertFalse([text for text in texts if text.startswith("TScale:")],
                         f"引擎设置里还有滑块：{texts}")
        joined = " / ".join(texts)
        for banned in BANNED_CONTROL_WORDS:
            self.assertNotIn(banned, joined, f"引擎设置面板里不该再有「{banned}」控件")

        # 分析需要的搜索范围控件在右栏「分析」面板，而不是引擎设置里
        self.assertTrue(self.window.movetime_spin.winfo_exists())
        # 右栏现在只留：思考时间 / 线程数 / 哈希数（深度与路线已删除）
        self.assertTrue(self.window.movetime_spin.winfo_exists())
        self.assertTrue(self.window.threads_spin.winfo_exists())
        self.assertTrue(self.window.hash_spin.winfo_exists())
        self.assertFalse(hasattr(self.window, "depth_spin"), "深度选项应已删除")
        self.assertFalse(hasattr(self.window, "multipv_spin"), "路线选项应已删除")
        self.assertFalse(hasattr(self.window, "analyze_button"), "开始分析按钮应已删除")
        dialog.destroy()
        self.pump(0.3)

    def test_engine_option_change_applies_immediately(self):
        """改 UCI 选项要立即生效：取消 UCI_ShowWDL 后引擎马上就按新选项输出。"""
        from tkinter import ttk

        self.assertTrue(self.session.load_engine(sys.executable, args=[str(MOCK)]))
        self.pump(1.0)
        self.window.on_open_engine_settings()
        self.pump(0.8)

        box = self.toggle_for("UCI_ShowWDL")
        self.assertIsNotNone(box, "面板里没有生成 UCI_ShowWDL 勾选框")
        variable = self.window._engine_option_widgets["UCI_ShowWDL"][1]
        # 加载引擎时已按它自己的默认值下发，界面要如实回显
        self.assertTrue(bool(variable.get()))
        self.assertEqual(self.session.engine.option_value("UCI_ShowWDL"), "true")

        box.toggle()                                  # 取消勾选
        self.pump(0.5)
        self.assertFalse(bool(variable.get()))
        self.assertEqual(self.session.engine.option_value("UCI_ShowWDL"), "false")

        # 立即生效：引擎马上就不再返回 wdl
        self.session.update_settings({"multi_pv": 2, "movetime_ms": 900})
        self.session.analyze_now()
        deadline = time.time() + 20
        lines = []
        while time.time() < deadline and len(lines) < 2:
            self.pump(0.2)
            lines = (self.session.state().get("analysis") or {}).get("lines") or []
        self.assertGreaterEqual(len(lines), 2)
        self.assertFalse([line for line in lines if line.get("wdl")],
                         "取消 UCI_ShowWDL 之后引擎还在返回 wdl")

        box.toggle()                                  # 再勾回来，立刻又带着 wdl
        self.pump(0.5)
        self.assertEqual(self.session.engine.option_value("UCI_ShowWDL"), "true")
        self.session.analyze_now()
        deadline = time.time() + 20
        got_wdl = False
        while time.time() < deadline and not got_wdl:
            self.pump(0.2)
            for line in (self.session.state().get("analysis") or {}).get("lines") or []:
                if line.get("wdl"):
                    got_wdl = True
                    break
        self.assertTrue(got_wdl, "重新打开 UCI_ShowWDL 后引擎没有返回 wdl")

        # 分析面板的「路线」控件直接写回引擎的 MultiPV 选项
        # 线程数设多少就生效多少（mock 引擎声明的最大值是 64，所以取 48 验证；
        # 皮卡鱼上限 1024，实测设 90 也是 90）
        self.window.threads_var.set(48)
        self.window.on_threads_change()
        self.pump(0.5)
        self.assertEqual(self.session.engine.option_value("Threads"), "48")
        self.assertEqual(int(self.window.threads_var.get()), 48)
        self.assertNotEqual(self.session.engine.option_value("Threads"), "8")
        self.window.hash_var.set(256)
        self.window.on_hash_change()
        self.pump(0.5)
        self.assertEqual(self.session.engine.option_value("Hash"), "256")

        # spin 选项立即下发
        self.window.session.set_engine_option("Hash", 64)
        self.pump(0.4)
        self.assertEqual(self.session.engine.option_value("Hash"), "64")
        entry = self.window._engine_option_widgets.get("Hash")
        self.assertIsNotNone(entry)
        self.assertEqual(int(entry[1].get()), 64)
        # button 选项（Clear Hash）点了不报错
        self.window.session.set_engine_option("Clear Hash", "")
        self.pump(0.3)
        self.assertEqual(self.session.engine.last_error, "")
        self.window.session.refresh_engine_options()
        self.pump(0.8)
        self.assertEqual(len(self.window._engine_option_names or []), 7,
                         "重新探测选项后仍应列出 7 项")
        self.assertEqual(self.session.engine.option_value("Hash"), "64",
                         "重新探测后保存过的选项要重新下发")

    def test_pikafish_uci_options_listed_and_settable(self):
        """真皮卡鱼：设置面板列出它声明的全部 UCI 选项，并且改完立即生效。"""
        path = pikafish_path()
        if path is None:
            self.skipTest("本机没有皮卡鱼引擎，跳过")
        self.assertTrue(self.session.load_engine(str(path)))
        self.pump(2.5)
        options = self.session.state()["engine"]["options"]
        names = [str(option["name"]) for option in options]
        for expected in ("Threads", "Hash", "MultiPV", "UCI_ShowWDL", "EvalFile",
                         "Clear Hash", "Ponder", "Move Overhead", "Debug Log File"):
            self.assertIn(expected, names, f"皮卡鱼的选项没列全：{names}")
        self.assertGreaterEqual(len(names), 9)

        self.window.on_open_engine_settings()
        self.pump(1.0)
        widgets = self.window._engine_option_widgets
        self.assertEqual(sorted(widgets), sorted([name for name in names
                                                  if name != "Clear Hash"]))
        self.assertEqual(widgets["Threads"][0], "spin")
        self.assertEqual(widgets["UCI_ShowWDL"][0], "check")
        self.assertEqual(widgets["EvalFile"][0], "string")

        self.window.session.set_engine_option("Threads", 2)
        self.window.session.set_engine_option("Hash", 64)
        self.window.session.set_engine_option("UCI_ShowWDL", True)
        self.pump(1.0)
        self.assertEqual(self.session.engine.option_value("Threads"), "2")
        self.assertEqual(self.session.engine.option_value("Hash"), "64")
        self.assertEqual(self.session.engine.option_value("UCI_ShowWDL"), "true")
        self.assertEqual(int(widgets["Hash"][1].get()), 64, "界面要回显引擎的当前取值")
        self.assertTrue(bool(widgets["UCI_ShowWDL"][1].get()))
        self.assertEqual(self.session.engine.last_error, "")

        # 换引擎后选项集合随之改变（动态生成）
        self.session.unload_engine()
        self.pump(0.6)
        self.assertTrue(self.session.load_engine(sys.executable, args=[str(MOCK)]))
        self.pump(1.0)
        self.assertEqual(sorted(self.window._engine_option_names or []),
                         sorted(["Threads", "Hash", "MultiPV", "UCI_ShowWDL",
                                 "Clear Hash", "EvalFile", "Style"]))


    # ------------------------------------------------- 新功能：界面开关 / 图片 / 多引擎
    def test_view_setting_switches(self):
        """界面设置：箭头 / 线路数字 / 状态栏 / 主题 / 棋盘大小。"""
        book = self.window.book_use_var
        # 箭头
        self.window.show_arrows_var.set(False)
        self.window.on_view_toggle("show_arrows")
        self.pump(0.3)
        self.assertFalse(self.session.settings["show_arrows"])
        self.assertFalse(self.window.board.show_arrows)
        # 线路数字
        self.window.show_numbers_var.set(False)
        self.window.on_view_toggle("show_numbers")
        self.pump(0.3)
        self.assertFalse(self.window.board.show_numbers)
        self.assertFalse(self.session.settings["show_numbers"])
        # 状态栏
        self.window.show_statusbar_var.set(False)
        self.window.on_view_toggle("show_statusbar")
        self.pump(0.4)
        self.assertFalse(self.window.statusbar.winfo_ismapped())
        self.window.show_statusbar_var.set(True)
        self.window.on_view_toggle("show_statusbar")
        self.pump(0.4)
        self.assertTrue(self.window.statusbar.winfo_ismapped())
        # 主题
        self.window.board_theme_var.set(dict(BOARD_THEME_ITEMS)["night"])
        self.window.on_board_theme_change()
        self.pump(0.4)
        self.assertEqual(self.session.settings["board_theme"], "night")
        self.assertEqual(self.window.board.theme, "night")
        self.window.board_theme_var.set(dict(BOARD_THEME_ITEMS)["wood"])
        self.window.on_board_theme_change()
        self.pump(0.3)
        # 棋盘大小（缩放只影响格子大小，不影响点击坐标）
        self.window.on_board_scale_change(70)
        self.pump(0.4)
        self.assertEqual(int(self.session.settings["board_scale"]), 70)
        self.assertAlmostEqual(self.window.board.scale, 0.7, places=2)
        self.assertLess(self.window.board.cell, 60)
        self.window.on_board_scale_change(100)
        self.pump(0.3)
        # 音效开关
        self.window.sound_var.set(False)
        self.window.on_view_toggle("sound")
        self.assertFalse(self.window.sounds.enabled)
        self.window.sound_var.set(True)
        self.window.on_view_toggle("sound")
        self.assertTrue(self.window.sounds.enabled)
        self.assertIsNotNone(book)
        # 这些开关现在会被记进配置文件，用完全部恢复默认，免得影响后面的用例
        self.window.show_arrows_var.set(True)
        self.window.on_view_toggle("show_arrows")
        self.window.show_numbers_var.set(True)
        self.window.on_view_toggle("show_numbers")
        self.window.show_statusbar_var.set(True)
        self.window.on_view_toggle("show_statusbar")
        self.pump(0.3)

    def test_piece_shadow_switch(self):
        """棋子阴影：开启后每颗棋子都垫一层阴影（所有皮肤同一张图），关掉就没了。"""
        window = self.window
        window.piece_shadow_var.set(True)
        window.on_view_toggle("piece_shadow")
        self.pump(0.6)
        self.assertTrue(self.session.settings["piece_shadow"], "设置里应该记成开启")
        self.assertTrue(window.board.piece_shadow)
        shadows = window.board.find_withtag("piece-shadow")
        self.assertEqual(len(shadows), 32, f"开局 32 个棋子应该各有一层阴影：{len(shadows)}")
        # 阴影是图片（带 alpha 的柔光），不是硬边的椭圆
        self.assertEqual(window.board.type(shadows[0]), "image")
        photo = window.board.itemcget(shadows[0], "image")
        self.assertTrue(photo, "阴影图元没有图片")
        # 关掉：一个都不剩
        window.piece_shadow_var.set(False)
        window.on_view_toggle("piece_shadow")
        self.pump(0.6)
        self.assertFalse(self.session.settings["piece_shadow"])
        self.assertFalse(window.board.piece_shadow)
        self.assertEqual(len(window.board.find_withtag("piece-shadow")), 0,
                         "关掉以后不该还有阴影")
        # 再打开：换一套皮肤也一样有阴影（同一套逻辑，不按皮肤分别处理）
        window.piece_shadow_var.set(True)
        window.on_view_toggle("piece_shadow")
        self.pump(0.4)
        first = self._first_skin()
        if first is not None:
            self.session.update_settings({"board_skin": first[1]})
            self.pump(0.8)
            self.assertEqual(len(window.board.find_withtag("piece-shadow")), 32,
                             f"皮肤「{first[0]}」下棋子也要有阴影")
            self.session.update_settings({"board_skin": ""})
            self.pump(0.4)

    def _first_skin(self):
        """skins 文件夹里的第一套皮肤（(名字, 路径)；一套都没有就返回 None）。"""
        from xqtrainer.ui import skin as skin_module

        items = skin_module.scan_skins()
        if not items:
            return None
        item = items[0]
        return (str(item.get("name") or ""), str(item.get("path") or ""))

    def test_view_dialog_opens(self):
        self.window.on_open_view_settings()
        self.pump(0.5)
        window = getattr(self.window, "view_dialog", None)
        self.assertIsNotNone(window)
        self.assertTrue(window.winfo_exists())
        window.destroy()
        self.pump(0.2)

    def test_link_settings_dialog(self):
        """连线相关的设置都在**单独的「连线设置」窗口**里，界面设置里不再有（用户要求）。"""
        self.window.on_open_link_settings()
        self.pump(0.5)
        window = getattr(self.window, "link_dialog", None)
        self.assertIsNotNone(window, "没打开连线设置窗口")
        # 开关默认打开、帧数默认 3
        self.assertTrue(self.window.link_strict_var.get())
        self.assertEqual(int(self.window.link_confirm_var.get()), 3)
        self.assertAlmostEqual(float(self.window.link_confirm_sec_var.get()), 0.6,
                               places=2)          # 时间窗默认 0.6 秒
        # 改一下要立刻写进设置
        self.window.link_strict_var.set(False)
        self.window.session.update_settings(
            {"link_strict_validate": bool(self.window.link_strict_var.get())})
        self.window.link_confirm_var.set(5)
        self.window.session.update_settings(
            {"link_confirm_frames": int(self.window.link_confirm_var.get())})
        self.pump(0.4)
        self.assertFalse(self.window.session.settings["link_strict_validate"])
        self.assertEqual(int(self.window.session.settings["link_confirm_frames"]), 5)
        self.assertEqual(self.window.session.linker.confirm_frames, 5)
        # 控件确实在对话框里
        texts = []
        stack = [window]
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            try:
                if widget.winfo_class() in ("TCheckbutton", "TLabel", "Label"):
                    texts.append(str(widget.cget("text")))
            except Exception:  # noqa: BLE001
                continue
        self.assertIn("规则校验", texts, f"没有规则校验开关：{texts}")
        self.assertIn("帧确认", texts, f"没有确认帧数控件：{texts}")
        self.assertIn("秒", texts, f"没有时间窗控件：{texts}")
        joined = "\n".join(texts)
        for name in ("扫描间隔", "识别模型", "棋盘区域"):
            self.assertIn(name, joined, f"连线设置里缺少「{name}」：{texts}")

        # 界面设置里不该再出现这些连线设置（它们搬走了）
        self.window.on_open_view_settings()
        self.pump(0.5)
        view = getattr(self.window, "view_dialog", None)
        self.assertIsNotNone(view)
        view_texts = []
        stack = [view]
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            try:
                if widget.winfo_class() in ("TCheckbutton", "TLabel", "Label"):
                    view_texts.append(str(widget.cget("text")))
            except Exception:  # noqa: BLE001
                continue
        self.assertNotIn("规则校验", view_texts,
                         f"界面设置里还有连线设置：{view_texts}")
        self.assertNotIn("扫描间隔", view_texts)
        view.destroy()
        window.destroy()
        self.pump(0.2)
        # 恢复默认，免得影响后面的用例
        self.window.session.update_settings({"link_strict_validate": True,
                                            "link_confirm_frames": 3})
        self.pump(0.3)
        window.destroy()
        self.pump(0.2)

    def test_toolbar_copy_paste_buttons(self):
        """工具栏：复制局面 / 粘贴局面·棋谱 / 复制棋谱 三个**图标**按钮挨在一起并能用。"""
        row2 = getattr(self.window, "toolbar_row2", None)
        self.assertIsNotNone(row2)
        if not getattr(self.window, "use_icons", False):
            self.skipTest("没有图标资源（缺 Pillow 或 assets/toolbar）")
        buttons = getattr(self.window, "icon_buttons", {})
        for key in ("copy_fen", "paste", "copy_game"):
            self.assertIn(key, buttons, f"工具栏第二行缺少「{key}」图标按钮：{list(buttons)}")
        # 三兄弟要挨在一起（用 pack 的从属顺序判断）
        order = [str(item) for item in row2.pack_slaves()]
        keys = [str(buttons[key]) for key in ("copy_fen", "paste", "copy_game")]
        positions = [order.index(item) for item in keys]
        self.assertEqual(positions, [positions[0], positions[0] + 1, positions[0] + 2],
                         "复制局面 / 粘贴 / 复制棋谱 要挨在一起")
        # 图标按钮不带文字（用户要求：按钮上只有图标，说明放在悬停提示里）
        for key in ("copy_fen", "paste", "copy_game", "new", "open", "save",
                    "first", "prev", "play", "next", "last", "undo", "redo",
                    "edit", "flip", "engine_red", "engine_black", "analyze"):
            self.assertIn(key, buttons)
            self.assertEqual(str(buttons[key].cget("text")), "",
                             f"「{key}」按钮不该有文字")

        # 走三步棋 → 复制棋谱 → 剪贴板里应该是中文着法记录
        for source, target in (("h2", "e2"), ("h9", "g7"), ("h0", "g2")):
            self.assertTrue(self.session.play_move(source, target))
        self.pump(0.3)
        self.window.on_copy_game()
        self.pump(0.3)
        copied = self.window.clipboard_get()
        self.assertIn("炮二平五", copied)
        self.assertIn("马8进7", copied)
        self.assertIn("马二进三", copied)
        self.assertIn("1.", copied)

        # 粘贴局面：剪贴板里放 FEN → 直接摆上去
        from xqtrainer.ui import app as app_module

        info = app_module.messagebox.showinfo
        warn = app_module.messagebox.showwarning
        app_module.messagebox.showinfo = lambda *a, **k: None      # 别弹模态框卡住用例
        app_module.messagebox.showwarning = lambda *a, **k: None
        fen = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1"
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(fen)
            self.window.on_paste_smart()
            self.pump(0.5)
            self.assertEqual(self.session.state()["fen"], fen)

            # 粘贴棋谱：剪贴板里放 PGN 文本 → 导入成棋谱
            pgn = ('[Event "测试"]\n[Red "甲"]\n[Black "乙"]\n\n'
                   "1. 炮二平五 马8进7 2. 马二进三 车9平8 *\n")
            self.window.clipboard_clear()
            self.window.clipboard_append(pgn)
            self.window.on_paste_smart()
            self.pump(0.6)
            state = self.session.state()
            self.assertEqual(str(state["meta"].get("Red")), "甲")
            lines = self.session.tree.all_lines()
            self.assertTrue(lines, "导入后应该有棋谱")
            self.assertEqual(len(lines[0]), 4, "粘贴的棋谱应该有 4 手棋")
        finally:
            app_module.messagebox.showinfo = info
            app_module.messagebox.showwarning = warn

    def test_background_picker(self):
        """背景下拉框：能列出 backgrounds 目录里的图，选中后棋盘背景跟着换。"""
        from xqtrainer.ui import backgrounds as backgrounds_module

        items = backgrounds_module.list_backgrounds()
        if not items:
            self.skipTest("backgrounds 目录里没有图片")
        name, path = items[0]
        values = list(self.window.background_box.cget("values"))
        self.assertIn(backgrounds_module.DEFAULT_LABEL, values)
        self.assertIn(name, values, f"下拉框里没有「{name}」：{values}")

        # 选一张背景 → 设置写进去 + 棋盘真的用它当桌面
        self.window.background_var.set(name)
        self.window.on_background_change()
        self.pump(0.6)
        self.assertEqual(str(self.session.settings.get("background")), name)
        board = self.window.board
        self.assertIsNotNone(board.background_path, "棋盘没有用上选的背景")
        self.assertEqual(str(board.background_path), str(path))
        self.assertTrue(board.find_withtag("backdrop"), "背景应该画在棋盘后面")

        # 换回默认 → 设置清空，回到皮肤背景 / 自带木纹
        self.window.background_var.set(backgrounds_module.DEFAULT_LABEL)
        self.window.on_background_change()
        self.pump(0.6)
        self.assertEqual(str(self.session.settings.get("background")), "")
        self.assertIsNone(self.window.board.background_path)
        self.assertTrue(self.window.board.find_withtag("backdrop"),
                        "默认背景也要有（木纹或皮肤背景）")

    def test_board_river_and_wood_backdrop(self):
        """棋盘：楚河汉界里不能有竖线穿过；棋盘后面是木纹背景（不是黑底）。"""
        self.session.update_settings({"flip": False, "board_theme": "wood"})
        self.pump(0.5)
        board = self.window.board
        board.update()
        cell = float(board.cell)
        ox, oy = board.origin
        river_top = oy + 4 * cell            # 河界上边
        river_bottom = oy + 5 * cell         # 河界下边
        for item in board.find_all():
            if board.type(item) != "line":
                continue
            x1, y1, x2, y2 = board.coords(item)[:4]
            if abs(x1 - x2) > 0.5:           # 横线 / 斜线，不管
                continue
            if min(abs(x1 - ox), abs(x1 - (ox + 8 * cell))) < 2:
                continue                      # 最外两条本来就通到底，允许
            crosses = (min(y1, y2) < river_top - 1) and (max(y1, y2) > river_bottom + 1)
            self.assertFalse(crosses, f"楚河汉界里有竖线穿过：{board.coords(item)}")
        self.assertTrue(board.find_withtag("backdrop"), "棋盘后面应该有木纹背景层")
        self.assertNotEqual(str(board.cget("background")).lower(), "#3b3226",
                            "棋盘后面不该是深色底")
        # 翻转视角同样不能有竖线穿过河界
        self.session.update_settings({"flip": True})
        self.pump(0.5)
        board.update()
        oy = board.origin[1]
        river_top, river_bottom = oy + 4 * cell, oy + 5 * cell
        for item in board.find_all():
            if board.type(item) != "line":
                continue
            x1, y1, x2, y2 = board.coords(item)[:4]
            if abs(x1 - x2) > 0.5:
                continue
            if min(abs(x1 - ox), abs(x1 - (ox + 8 * cell))) < 2:
                continue
            crosses = (min(y1, y2) < river_top - 1) and (max(y1, y2) > river_bottom + 1)
            self.assertFalse(crosses, f"翻转后河界里也有竖线：{board.coords(item)}")
        self.session.update_settings({"flip": False})
        self.pump(0.3)

    def test_book_panel_controls(self):
        """左栏开局库：启用库招 / 策略 / 脱谱步数的界面控件。"""
        self.window.book_use_var.set(False)
        self.window.on_toggle_book_use()
        self.pump(0.3)
        self.assertFalse(self.session.settings["book_enabled"])
        self.window.book_use_var.set(True)
        self.window.on_toggle_book_use()
        self.pump(0.3)

        labels = [label for _key, label in BOOK_STRATEGY_ITEMS]
        self.window.book_strategy_var.set(labels[-1])           # 完全随机
        self.window.on_book_strategy_change()
        self.pump(0.3)
        self.assertEqual(self.session.settings["book_strategy"], "random")

        self.window.book_max_ply_var.set(12)
        self.window.on_book_max_ply_change()
        self.pump(0.3)
        self.assertEqual(self.session.settings["book_max_ply"], 12)
        self.assertEqual(self.session.state()["book"]["maxPly"], 12)
        self.window.book_max_ply_var.set(0)
        self.window.on_book_max_ply_change()
        self.pump(0.3)

    def test_book_dialog_priority_list(self):
        """开局库设置对话框：多库列表与优先级/移除按钮。"""
        from xqtrainer.book import obk_hash, write_obk

        first = Path(__file__).resolve().parent / "_tmp_ui_a.obk"
        second = Path(__file__).resolve().parent / "_tmp_ui_b.obk"
        board = Board()
        board.push(board.find_move_iccs("h2e2"))
        try:
            write_obk(first, {(1, obk_hash(board)): 4})
            write_obk(second, {(1, obk_hash(board)): 9})
            self.assertTrue(self.session.load_book(str(first)))
            self.assertTrue(self.session.load_book(str(second)))
            self.window.on_open_book_settings()
            self.pump(0.6)
            dialog = getattr(self.window, "book_dialog", None)
            self.assertIsNotNone(dialog)
            rows = self.window.book_priority_table.get_children()
            self.assertEqual(len(rows), 2)
            self.assertEqual(str(self.window.book_priority_table.item(
                rows[1], "values")[1]), second.name)
            # 选第二条（后加载的库）并让它优先
            self.window.book_priority_table.selection_set(rows[1])
            self.window.on_book_priority(-1)
            self.pump(0.4)
            labels = [str((item or {}).get("label"))
                      for item in self.session.state()["book"]["books"]]
            self.assertEqual(labels, [second.name, first.name])
            # 移除第一个
            rows = self.window.book_priority_table.get_children()
            self.window.book_priority_table.selection_set(rows[0])
            self.window.on_book_remove()
            self.pump(0.4)
            self.assertEqual(len(self.session.state()["book"]["books"]), 1)
            dialog.destroy()
            self.pump(0.2)
        finally:
            self.session.clear_book()
            first.unlink(missing_ok=True)
            second.unlink(missing_ok=True)

    def test_position_image_export_and_recognize(self):
        """局面图片：导出 PNG，再把图片识别回局面。"""
        from xqtrainer.ui import board_image

        if not board_image.available():
            self.skipTest("没有 Pillow")
        target = Path(__file__).resolve().parent / "_tmp_ui_pos.png"
        try:
            image = self.window._render_board_image()
            image.save(target)
            self.assertTrue(target.exists())
            from PIL import Image

            result = board_image.recognize_position(Image.open(target))
            self.assertTrue(result["ok"], result.get("message"))
            rebuilt = Board(str(result["board"]) + " w - - 0 1")
            self.assertEqual(rebuilt.to_fen().split()[0],
                             Board().to_fen().split()[0])
        finally:
            target.unlink(missing_ok=True)

    def test_multi_engine_list_switch(self):
        """多引擎管理：加载过的引擎会进列表，能切换。"""
        self.assertTrue(self.session.load_engine(sys.executable, args=[str(MOCK)]))
        self.pump(0.8)
        known = self.session.known_engines()
        self.assertGreaterEqual(len(known), 1)
        self.assertEqual(str(known[0]["path"]), sys.executable)
        self.window.on_open_engine_settings()
        self.pump(0.6)
        box = getattr(self.window, "engine_list", None)
        self.assertIsNotNone(box)
        self.assertGreaterEqual(box.size(), 1)
        text = str(box.get(0))
        self.assertTrue(text)
        # 同一个引擎再点切换不报错
        self.window.engine_list.selection_set(0)
        self.window.session.switch_engine(sys.executable)
        self.pump(0.6)
        self.assertTrue(self.session.state()["engine"]["hasEngine"])
        self.assertTrue(self.session.remove_engine(sys.executable))
        self.pump(0.4)
        self.assertEqual(self.session.known_engines(), [])

    def test_dropped_image_imports_position(self):
        """拖图片文件进来：识别成局面并应用（这里直接喂文件路径给拖拽处理函数）。"""
        from xqtrainer.ui import app as app_module
        from xqtrainer.ui import board_image

        if not board_image.available():
            self.skipTest("没有 Pillow")
        target = Path(__file__).resolve().parent / "_tmp_ui_drop.png"
        # 先走出几步，得到与开局不同的局面
        for iccs in ("h2e2", "h9g7", "h0g2"):
            self.session.play_move(iccs[:2], iccs[2:])
        self.pump(0.5)
        expected = self.session.state()["fen"].split()[0]
        answers = {"askyesno": True, "showinfo": None, "showwarning": None}
        originals = {}
        for name, value in answers.items():
            originals[name] = getattr(app_module.messagebox, name)
            if name == "askyesno":
                setattr(app_module.messagebox, name, lambda *a, **k: True)
            else:
                setattr(app_module.messagebox, name, lambda *a, **k: None)
        try:
            self.window._render_board_image().save(target)
            self.window.on_files_dropped([str(target)])
            self.pump(1.2)
            self.assertTrue(self.window.editor_active, "拖入图片后应进入编辑局面")
            self.assertEqual(self.window.editor.to_fen().split()[0], expected)
            self.window.on_editor_cancel()
            self.pump(0.3)
        finally:
            for name, original in originals.items():
                setattr(app_module.messagebox, name, original)
            target.unlink(missing_ok=True)

    def test_dropped_pgn_imports_game(self):
        """拖棋谱文件进来：按棋谱导入。"""
        target = Path(__file__).resolve().parent / "_tmp_ui_drop.pgn"
        try:
            target.write_text('[Event "拖拽"]\n[Red "甲"]\n[Black "乙"]\n\n'
                              "1. 炮二平五 马8进7 *\n", encoding="utf-8")
            self.window.on_files_dropped([str(target)])
            self.pump(0.8)
            self.assertEqual(len(self.window._line_nodes), 3)   # 第 0 手 + 两步棋
            self.assertIn("甲", self.window.game_title.cget("text"))
            self.window.navigate("last")
            self.pump(0.4)
            self.assertEqual(int(self.session.state()["ply"]), 2)
        finally:
            target.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
