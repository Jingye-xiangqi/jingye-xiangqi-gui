# -*- coding: utf-8 -*-
"""连线功能测试：识别 → 比对 → 上行同步 → 下行点击 → 取消。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("XQ_SETTINGS_FILE",
                      str(Path(tempfile.gettempdir()) / "xq_link_test_settings.json"))

from xqtrainer.board import Board  # noqa: E402
from xqtrainer.link import _boards_equal, validate_board  # noqa: E402
from xqtrainer.session import Session  # noqa: E402


def grid(*rows) -> list:
    """把 10 行字符串拼成 10×9 的棋盘数组（"." 表示空点）。"""
    assert len(rows) == 10, "必须 10 行"
    return [list(row) for row in rows]


#: 标准开局（识别结果里黑方在上，和我们程序里的朝向可能相反）
START_AS_SEEN = grid("rnbakabnr", ".........", ".c.....c.", "p.p.p.p.p",
                     ".........", ".........", "P.P.P.P.P", ".C.....C.",
                     ".........", "RNBAKABNR")
#: 同一局面，但红方在上（换个平台就可能长这样）
START_FLIPPED = grid(*reversed(["rnbakabnr", ".........", ".c.....c.", "p.p.p.p.p",
                                ".........", ".........", "P.P.P.P.P", ".C.....C.",
                                ".........", "RNBAKABNR"]))
#: 初始局面走了一步「马二进三」（正常走子 → 智能确认只要 2 帧）
AFTER_ONE_MOVE = grid("rnbakabnr", ".........", ".c.....c.", "p.p.p.p.p",
                      ".........", ".........", "P.P.P.P.P", ".C....NC.",
                      ".........", "RNBAKAB.R")


class TestBoardRules(unittest.TestCase):
    """方案A 第一步：规则校验——不像一盘合法棋的局面必须被拦下来。"""

    def test_start_position_passes(self):
        self.assertIsNone(validate_board(START_AS_SEEN), "标准开局应该通过校验")
        self.assertIsNone(validate_board(START_FLIPPED),
                          "校验必须与朝向无关（红方在上也要通过）")

    def test_piece_count_limits(self):
        board = grid("rnbakabnr", ".........", ".c.....c.", "p.p.p.p.p",
                     ".........", ".........", "P.P.P.P.P", ".C.....C.",
                     "....r....", "RNBAKABNR")          # 黑车多了一个（3 个）
        self.assertEqual(validate_board(board), "r×3>2")
        board = grid("rnbakabnr", ".........", ".c.....c.", "p.p.p.p.p",
                     ".........", ".........", "P.P.P.P.P", ".C.....C.",
                     "..C.C....", "RNBAKABNR")          # 红炮 4 个
        self.assertEqual(validate_board(board), "C×4>2")
        board = grid("rnbakabnr", ".........", ".c.....c.", "p.p.p.p.p",
                     "....p....", ".........", "P.P.P.P.P", ".C.....C.",
                     ".........", "RNBAKABNR")          # 黑卒 6 个
        self.assertEqual(validate_board(board), "p×6>5")

    def test_kings_must_exist(self):
        board = grid("rnba.abnr", ".........", ".c.....c.", "p.p.p.p.p",
                     ".........", ".........", "P.P.P.P.P", ".C.....C.",
                     ".........", "RNBAKABNR")          # 黑将不见了
        self.assertEqual(validate_board(board), "将帅必须各一个")
        board = grid("rnbakabnr", ".........", ".c.....c.", "p.p.p.p.p",
                     ".........", ".........", "P.P.P.P.P", ".C.....C.",
                     ".........", "RNBA.ABNR")          # 红帅不见了
        self.assertEqual(validate_board(board), "将帅必须各一个")

    def test_advisor_must_stay_in_palace(self):
        # 把一个黑士从底线挪到 (7,4)：数量没超，但出了九宫
        board = grid("rnbak.bnr", ".........", ".c.....c.", "p.p.p.p.p",
                     ".........", ".........", "P.P.P.P.P", ".C..a..C.",
                     ".........", "RNBAKABNR")
        self.assertEqual(validate_board(board), "黑士出九宫(7,4)")
        # 把一个红士挪到 (8,2)：行对、列不在九宫
        board = grid("rnbakabnr", ".........", ".c.....c.", "p.p.p.p.p",
                     ".........", ".........", "P.P.P.P.P", ".C.....C.",
                     "..A......", "RNBAK.BRN")
        self.assertEqual(validate_board(board), "红士出九宫(8,2)")

    def test_elephant_must_not_cross_river(self):
        # 黑方在上（0~4 是黑方半场）：黑象跑到第 5 行＝过河
        board = grid("rnbaka.nr", ".........", ".c.....c.", "p.p.p.p.p",
                     ".........", "....b....", "P.P.P.P.P", ".C.....C.",
                     ".........", "RNBAKABNR")
        self.assertEqual(validate_board(board), "黑象过河(5,4)")
        # 红方在下（5~9 是红方半场）：红相跑到第 4 行＝过河
        board = grid("rnbakabnr", ".........", ".c.....c.", "p.p.p.p.p",
                     "....B....", ".........", "P.P.P.P.P", ".C.....C.",
                     ".........", "RNBAKA.NR")
        self.assertEqual(validate_board(board), "红相过河(4,4)")

    def test_total_pieces_limit(self):
        board = grid("rnbakabnr", ".........", ".c.....c.", "p.p.p.p.p",
                     "xxxxxxxxx", "xxxxxxxxx", "P.P.P.P.P", ".C.....C.",
                     ".........", "RNBAKABNR")          # 多出一堆没见过的子
        self.assertEqual(validate_board(board), "棋子总数 50>32")

    def test_garbage_board_is_rejected(self):
        self.assertEqual(validate_board(None), "棋盘尺寸不对")
        self.assertEqual(validate_board([["."] * 9] * 5), "棋盘尺寸不对")
        self.assertEqual(validate_board(grid(*(["........."] * 10))), "棋子太少")


class TestConfirmFrames(unittest.TestCase):
    """方案A 第二步：多帧确认——只出现一两帧的变化不能被当真。"""

    def setUp(self):
        Path(os.environ["XQ_SETTINGS_FILE"]).unlink(missing_ok=True)
        self.session = Session()
        self.linker = self.session.linker
        self.linker.pieces = 32

    def tearDown(self):
        Path(os.environ["XQ_SETTINGS_FILE"]).unlink(missing_ok=True)

    def test_change_needs_three_frames(self):
        self.session.update_settings({"link_confirm_frames": 3})
        first = grid(*START_AS_SEEN)
        second = grid(*START_FLIPPED)
        self.assertTrue(self.linker._confirm_board(first), "第一帧建立基准")
        self.assertFalse(self.linker._confirm_board(second), "第 1 帧确认：先不采纳")
        self.assertEqual(self.linker.pending_frames, 1)
        self.assertFalse(self.linker._confirm_board(second), "第 2 帧确认：还不采纳")
        self.assertEqual(self.linker.pending_frames, 2)
        self.assertTrue(self.linker._confirm_board(second), "第 3 帧一致 → 采纳")
        self.assertEqual(self.linker.pending_frames, 0)
        self.assertTrue(_boards_equal(self.linker.confirmed_board, second))

    def test_single_frame_setting_accepts_at_once(self):
        self.session.update_settings({"link_confirm_frames": 1})
        self.assertEqual(self.linker.confirm_frames, 1)
        first = grid(*START_AS_SEEN)
        second = grid(*START_FLIPPED)
        self.assertTrue(self.linker._confirm_board(first))
        self.assertTrue(self.linker._confirm_board(second), "N=1 时变化立刻采纳")

    def test_flicker_is_ignored(self):
        """误识别只闪一帧（a→b→a）时，不应该被确认。"""
        self.session.update_settings({"link_confirm_frames": 3})
        a = grid(*START_AS_SEEN)
        b = grid(*START_FLIPPED)
        self.assertTrue(self.linker._confirm_board(a))
        self.assertFalse(self.linker._confirm_board(b))
        self.assertTrue(self.linker._confirm_board(a), "回到原局面＝没变化")
        self.assertEqual(self.linker.pending_frames, 0)
        self.assertTrue(_boards_equal(self.linker.confirmed_board, a))

    def test_confirm_frames_is_clamped(self):
        for value, wanted in ((0, 1), (-3, 1), (9, 5), (4, 4), ("x", 3)):
            self.session.update_settings({"link_confirm_frames": value})
            self.assertEqual(self.linker.confirm_frames, wanted, f"{value!r} 应夹到 {wanted}")

    def test_smart_confirm_two_frames_for_a_normal_move(self):
        """智能确认：正常走子（差一步合法着法）只要 2 帧。"""
        self.session.update_settings({"link_confirm_frames": 3,
                                      "link_smart_confirm": True,
                                      "link_confirm_seconds": 0})   # 只看帧数
        start = grid(*START_AS_SEEN)
        moved = grid(*AFTER_ONE_MOVE)
        self.assertTrue(self.linker._confirm_board(start))
        self.assertFalse(self.linker._confirm_board(moved), "第 1 帧还不能确认")
        self.assertTrue(self.linker._confirm_board(moved), "正常走子第 2 帧就确认")
        self.assertIsNotNone(self.linker.smart_board, "2 帧确认后要留第三帧兜底")

    def test_abnormal_change_still_needs_three_frames(self):
        """异常变化（大跳变/翻转）仍然要 3 帧，稳定性不变。"""
        self.session.update_settings({"link_confirm_frames": 3,
                                      "link_smart_confirm": True,
                                      "link_confirm_seconds": 0})
        start = grid(*START_AS_SEEN)
        flipped = grid(*START_FLIPPED)
        self.assertTrue(self.linker._confirm_board(start))
        self.assertFalse(self.linker._confirm_board(flipped))
        self.assertFalse(self.linker._confirm_board(flipped), "第 2 帧仍不确认")
        self.assertTrue(self.linker._confirm_board(flipped), "第 3 帧才确认")

    def test_time_window_confirms_without_waiting_for_more_frames(self):
        """时间窗：同一个结果持续够久，就不必再等第 3 帧（扫描间隔调大也不翻倍）。"""
        self.session.update_settings({"link_confirm_frames": 3,
                                      "link_confirm_seconds": 0.6})
        start = grid(*START_AS_SEEN)
        flipped = grid(*START_FLIPPED)                 # 异常变化 → 正常要 3 帧
        self.assertTrue(self.linker._confirm_board(start))
        self.assertFalse(self.linker._confirm_board(flipped))
        self.linker.pending_since -= 5.0               # 模拟"已经等了 5 秒"（间隔很大的情况）
        self.assertTrue(self.linker._confirm_board(flipped), "时间窗到了就该确认")

    def test_time_window_can_be_disabled(self):
        self.session.update_settings({"link_confirm_frames": 3,
                                      "link_confirm_seconds": 0})
        start = grid(*START_AS_SEEN)
        flipped = grid(*START_FLIPPED)
        self.assertTrue(self.linker._confirm_board(start))
        self.assertFalse(self.linker._confirm_board(flipped))
        self.linker.pending_since -= 99.0
        self.assertFalse(self.linker._confirm_board(flipped), "时间窗=0 表示只看帧数")

    def test_third_frame_backstop_detects_mismatch(self):
        """2 帧确认后，第三帧和确认结果不一致 → 当成新变化重新确认（自动纠错）。"""
        self.session.update_settings({"link_confirm_frames": 3,
                                      "link_confirm_seconds": 0})
        start = grid(*START_AS_SEEN)
        moved = grid(*AFTER_ONE_MOVE)
        other = grid(*START_FLIPPED)
        self.assertTrue(self.linker._confirm_board(start))
        self.assertFalse(self.linker._confirm_board(moved))
        self.assertTrue(self.linker._confirm_board(moved))
        self.assertFalse(self.linker._confirm_board(other), "第三帧不一致 → 重新数帧")
        self.assertEqual(self.linker.pending_frames, 1)


class TestStrictValidateToggle(unittest.TestCase):
    """严格校验的开关（不依赖模型，直接查设置读取）。"""

    def setUp(self):
        Path(os.environ["XQ_SETTINGS_FILE"]).unlink(missing_ok=True)

    def tearDown(self):
        Path(os.environ["XQ_SETTINGS_FILE"]).unlink(missing_ok=True)

    def test_default_on(self):
        session = Session()
        self.assertTrue(session.settings.get("link_strict_validate"))
        self.assertTrue(session.linker.strict_validate)
        self.assertEqual(int(session.settings.get("link_confirm_frames") or 0), 3)

    def test_setting_round_trip(self):
        session = Session()
        session.update_settings({"link_strict_validate": False, "link_confirm_frames": 5})
        self.assertFalse(session.linker.strict_validate)
        self.assertEqual(session.linker.confirm_frames, 5)


def render(moves, cell: int = 72):
    """把走完这些着法的局面画成一张图（模拟"对方窗口的画面"）。"""
    from xqtrainer.ui import board_image

    board = Board()
    for iccs in moves:
        move = board.find_move_iccs(iccs)
        assert move is not None, iccs
        board.push(move)
    rows = board.to_fen().split()[0].split("/")
    return board_image.render_position(rows, board.side, cell=cell)


def render_fen_as_remote(fen: str, cell: int = 72, rotate: bool = False):
    """按 FEN 画一张"对方窗口的画面"（中局 / 残局接入的用例用）。

    ``rotate=True`` 表示对方平台给的是 180° 视角（执黑时常见）。
    """
    from xqtrainer.ui import board_image

    parts = fen.split()
    rows = parts[0].split("/")
    side = parts[1] if len(parts) > 1 else "w"
    if not rotate:
        return board_image.render_position(rows, side, cell=cell)

    def expand(row: str):
        out = []
        for char in row:
            out.extend(["."] * int(char) if char.isdigit() else [char])
        return out

    grid = [expand(row) for row in rows]
    flipped = [list(reversed(row)) for row in reversed(grid)]
    rotated_rows = []
    for row in flipped:
        text, empty = [], 0
        for piece in row:
            if piece == ".":
                empty += 1
                continue
            if empty:
                text.append(str(empty))
                empty = 0
            text.append(piece)
        if empty:
            text.append(str(empty))
        rotated_rows.append("".join(text) or "9")
    return board_image.render_position(rotated_rows, side, cell=cell)


def render_as_black_sees_it(moves, cell: int = 72):
    """模拟"对方平台给黑方看的棋盘"：整盘转 180°（执黑的人自己那边在下方）。

    注意不能直接拿标准画面 `transpose(ROTATE_180)` —— 那样棋子是倒着的，
    真平台是把**棋盘的朝向**转过去、棋子本身还是正的，所以这里按旋转后的
    rank/file 重新画一遍。
    """
    from xqtrainer.ui import board_image

    board = Board()
    for iccs in moves:
        move = board.find_move_iccs(iccs)
        assert move is not None, iccs
        board.push(move)
    rows = []
    for rank in range(10):                      # 旋转后：最上面是原 rank 0（红方底线）
        cells = [board.cells[rank][file] for file in range(8, -1, -1)]   # 左右也镜像
        text, empty = [], 0
        for piece in cells:
            if piece == ".":
                empty += 1
                continue
            if empty:
                text.append(str(empty))
                empty = 0
            text.append(piece)
        if empty:
            text.append(str(empty))
        rows.append("".join(text) or "9")
    return board_image.render_position(rows, board.side, cell=cell)


class FakeWindow:
    """假的"目标窗口"：返回指定画面，记录被点到的位置。"""

    def __init__(self, image):
        self.image = image
        self.clicks = []
        self.hwnd = 1
        self.scale = 1.0

    def grab(self):
        return self.image

    def click(self, x, y, delay: float = 0.05):
        self.clicks.append((x, y))

    def window_title(self):
        return "测试窗口"


@unittest.skipUnless(Path(ROOT / "model" / "yolov11.onnx").is_file(),
                     "没有 YOLO 模型（连线识别需要它）")
class TestLinkAnyPosition(unittest.TestCase):
    """连线必须支持**任意局面**：开局、中局、残局都要能接管并继续同步。

    （用户报的 bug：自己走几步再连线，只能识别出棋子个数，局面同步不上。）
    """

    #: 走到第 12 手的中局
    MIDGAME = ["h2e2", "h9g7", "h0g2", "i9i8", "i0h0", "h7h3", "b0c2", "b9c7",
               "a0b0", "a9b9", "c3c4", "c6c5"]
    #: 一个残局（红：帅 + 仕；黑：将）
    ENDGAME_FEN = "3k5/9/9/9/9/9/9/9/4A4/3AK4 w - - 0 1"

    def setUp(self):
        Path(os.environ["XQ_SETTINGS_FILE"]).unlink(missing_ok=True)
        self.session = Session()
        self.session.update_settings({"link_confirm_frames": 1,      # 一帧就确认，方便测
                                      "link_strict_validate": True,
                                      "link_orientation": "auto",
                                      "engine_mode": "red"})         # 先按执红
        self.linker = self.session.linker
        self.linker.last_grid = (40.0, 40.0, 72.0, 72.0)
        self.assertTrue(self.linker.start(threaded=False))
        self.linker.running = True

    def tearDown(self):
        self.linker.stop()
        Path(os.environ["XQ_SETTINGS_FILE"]).unlink(missing_ok=True)

    def expected_fen(self, moves) -> str:
        board = Board()
        for iccs in moves:
            board.push(board.find_move_iccs(iccs))
        return board.to_fen().split()[0]

    def adopt(self, image, frames: int = 3) -> None:
        self.linker.win32 = FakeWindow(image)
        for _ in range(frames):
            self.linker._tick()

    def test_midgame_connect_adopts_board(self):
        """本地还在开局、对方已经是中局 → 要把对方局面接管过来。"""
        for rotate in (False, True):
            with self.subTest(rotate=rotate):
                self.session.update_settings({"engine_mode": "black" if rotate else "red"})
                self.session.set_fen("rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/"
                                    "P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1")
                self.linker.orientation = ""
                self.linker._synced_any = False
                self.linker.confirmed_board = None
                self.linker._last_remote_board = None
                self.adopt(render_as_black_sees_it(self.MIDGAME) if rotate
                           else render(self.MIDGAME))
                self.assertEqual(self.session.state()["fen"].split()[0],
                                 self.expected_fen(self.MIDGAME),
                                 f"中局没接管过来（rotate={rotate}）")
                self.assertEqual(self.linker.pieces, 32)

    def test_endgame_connect_adopts_board(self):
        """残局（只剩几个子）也要能接管。"""
        for rotate in (False, True):
            with self.subTest(rotate=rotate):
                self.session.update_settings({"engine_mode": "black" if rotate else "red"})
                self.session.set_fen("rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/"
                                    "P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1")
                self.linker.orientation = ""
                self.linker._synced_any = False
                self.linker.confirmed_board = None
                self.linker._last_remote_board = None
                self.adopt(render_fen_as_remote(self.ENDGAME_FEN, rotate=rotate))
                self.assertEqual(self.session.state()["fen"].split()[0],
                                 self.ENDGAME_FEN.split()[0],
                                 f"残局没接管过来（rotate={rotate}）")
                self.assertEqual(self.linker.pieces, 4)

    def test_move_after_midgame_adopt_syncs(self):
        """接管中局之后，对方再走一步必须能同步过来（方向不能反）。"""
        self.session.update_settings({"engine_mode": "red"})
        self.adopt(render(self.MIDGAME))
        follow = self.MIDGAME + ["e2e6"]
        self.adopt(render(follow), frames=3)
        moves = self.session.state()["moves"]
        self.assertTrue(moves, "接管后的着法没同步上")
        self.assertEqual(moves[-1]["iccs"], "e2e6", moves[-1])

    def test_orientation_is_stable_after_adopt(self):
        """接管局面之后朝向要锁定，不能又被"按执黑推断"翻回去（那样同步会立刻失效）。"""
        self.session.update_settings({"engine_mode": "black"})
        self.adopt(render_as_black_sees_it(self.MIDGAME))
        adopted = self.linker.orientation
        self.assertTrue(adopted, "应该定下朝向")
        for _ in range(3):
            self.linker._tick()
        self.assertEqual(self.linker.orientation, adopted, "接管后朝向被改回去了")
        self.assertTrue(self.linker.matched, "后续帧应该仍与本程序棋盘一致")


@unittest.skipUnless(Path(ROOT / "model" / "yolov11.onnx").is_file(),
                     "没有 YOLO 模型（连线识别需要它）")
class TestLinkOrientation(unittest.TestCase):
    """执黑连线：对方平台把棋盘转 180° 时，识别和点击坐标都要跟着翻，不能镜像。"""

    def setUp(self):
        Path(os.environ["XQ_SETTINGS_FILE"]).unlink(missing_ok=True)
        self.session = Session()
        self.session.update_settings({"link_confirm_frames": 1,        # 一帧就同步，方便测
                                      "link_strict_validate": True,
                                      "link_orientation": "auto",
                                      "engine_mode": "black"})         # 我们执黑
        self.linker = self.session.linker
        self.linker.last_grid = (40.0, 40.0, 72.0, 72.0)
        self.linker.win32 = FakeWindow(render_as_black_sees_it([]))
        # 不启后台循环，用例自己一帧一帧地调 _tick()
        self.assertTrue(self.linker.start(threaded=False))
        self.linker.running = True

    def tearDown(self):
        self.linker.stop()
        Path(os.environ["XQ_SETTINGS_FILE"]).unlink(missing_ok=True)

    def test_orientation_follows_our_side(self):
        """执黑 → 对方棋盘按 180° 换算；执红 → 不翻。"""
        self.assertEqual(self.linker.our_side, "black")
        self.assertEqual(self.linker.preferred_orientation(), "lr+ud")
        self.linker._tick()
        self.assertTrue(self.linker.flipped, "执黑时对方棋盘要按左右镜像换算")
        self.assertTrue(self.linker.vertical, "执黑时对方棋盘要按上下翻转换算")
        self.assertTrue(self.linker.matched, "翻转后识别结果应与本程序棋盘一致")
        self.session.update_settings({"engine_mode": "red"})
        self.assertEqual(self.linker.preferred_orientation(), "",
                         "执红时不该翻转")

    def test_manual_orientation_override(self):
        """手动指定朝向时以手动为准（有些平台不给你翻棋盘）。"""
        self.session.update_settings({"link_orientation": "same"})
        self.assertEqual(self.linker.preferred_orientation(), "")
        self.session.update_settings({"link_orientation": "ud"})
        self.assertEqual(self.linker.preferred_orientation(), "ud")

    def test_mirrored_remote_move_is_synced_correctly(self):
        """对方（红）走马二进三 → 必须同步成马二进三，不能变成镜像的马八进七。"""
        self.linker._tick()
        self.linker.win32 = FakeWindow(render_as_black_sees_it(["h0g2"]))
        self.linker._tick()
        moves = self.session.state()["moves"]
        self.assertEqual(len(moves), 1, moves)
        self.assertEqual(moves[-1]["iccs"], "h0g2",
                         "同步方向反了（识别成了镜像的着法）")

    def test_click_uses_rotated_coordinates(self):
        """我们（黑）走马8进7 → 点到对方窗口的坐标要按 180° 换算。"""
        self.linker._tick()
        self.linker.win32 = FakeWindow(render_as_black_sees_it(["h0g2"]))
        self.linker._tick()
        window = FakeWindow(render_as_black_sees_it(["h0g2"]))
        self.linker.win32 = window
        self.session.play_move("h9", "g7")             # 我们（黑）走一步
        self.assertEqual(len(window.clicks), 2, "应该点出起点和终点")
        x0, y0, cell_x, cell_y = self.linker.last_grid
        # h9：file=7 → 镜像列 8-7=1；rank=9 → 顶行 9-9=0 → 上下翻转 9-0=9
        # g7：file=6 → 映像列 8-6=2；rank=7 → 顶行 2   → 上下翻转 9-2=7
        expected = [(int(x0 + 1 * cell_x), int(y0 + 9 * cell_y)),
                    (int(x0 + 2 * cell_x), int(y0 + 7 * cell_y))]
        self.assertEqual(window.clicks, expected,
                         "点击坐标没有按 180° 换算")


@unittest.skipUnless(Path(ROOT / "model" / "yolov11.onnx").is_file(),
                     "没有 YOLO 模型（连线识别需要它）")
class TestGraphLinker(unittest.TestCase):
    def setUp(self):
        self.session = Session()
        self.linker = self.session.linker
        # 这几条用例是测"识别→同步→点击"的，把确认帧数固定成 3、严格校验打开，
        # 免得受别的用例写进配置文件里的值影响。
        self.session.update_settings({"link_confirm_frames": 3,
                                      "link_strict_validate": True})
        self.linker.last_grid = (40.0, 40.0, 72.0, 72.0)
        self.linker.win32 = FakeWindow(render([]))
        # 不启后台主循环：用例自己一帧一帧地调 _tick()，结果才可复现
        self.assertTrue(self.linker.start(threaded=False))
        self.linker.running = True

    def tearDown(self):
        self.linker.stop()
        try:
            Path(os.environ["XQ_SETTINGS_FILE"]).unlink(missing_ok=True)
        except OSError:
            pass

    def test_recognizer_uses_yolo(self):
        self.assertEqual(self.linker.recognizer.backend, "yolo",
                         self.linker.recognizer.error)
        self.assertTrue(self.linker.model_path)

    def test_scan_recognizes_board(self):
        self.linker._tick()
        self.assertEqual(self.linker.pieces, 32, "初始局面应识别出 32 个棋子")
        self.assertTrue(self.linker.matched, "识别结果应与本程序棋盘一致")

    def test_single_changed_frame_is_not_applied(self):
        """只出现一帧的变化不能同步（方案A 的多帧确认就是防这个）。"""
        self.linker._tick()
        self.linker.win32 = FakeWindow(render(["h0g2"]))
        self.linker._tick()
        self.assertEqual(int(self.session.state()["ply"]), 0, "一帧的变化不该同步")
        self.assertEqual(self.linker.pending_frames, 1, "应该进入待确认状态")

    def test_remote_move_is_applied(self):
        """对方走「马二进三」→ 连续 3 帧确认后本程序跟着走。"""
        self.linker._tick()
        self.linker.win32 = FakeWindow(render(["h0g2"]))
        for _ in range(self.linker.confirm_frames):
            self.linker._tick()
        state = self.session.state()
        self.assertEqual(int(state["ply"]), 1)
        self.assertEqual(state["moves"][-1]["cn"], "马二进三")

    def test_confirm_frames_one_syncs_immediately(self):
        """把确认帧数设成 1，就回到"一帧就同步"的老行为。"""
        self.session.update_settings({"link_confirm_frames": 1})
        self.linker._tick()
        self.linker.win32 = FakeWindow(render(["h0g2"]))
        self.linker._tick()
        self.assertEqual(int(self.session.state()["ply"]), 1)

    def test_illegal_frame_is_dropped(self):
        """规则校验：不像一盘棋的一帧直接作废，不会进同步逻辑。"""
        class FakeRecognizer:
            backend = "yolo"
            error = ""
            grid = None
            reject_reason = ""

            def __init__(self, board):
                self.board = board

            def recognize(self, image):
                return [list(row) for row in self.board]

        illegal = grid("rnbakabnr", ".........", ".c.....c.", "p.p.p.p.p",
                       ".........", ".........", "P.P.P.P.P", ".C.....C.",
                       "....r....", "RNBAKABNR")            # 黑车 3 个
        self.linker.recognizer = FakeRecognizer(illegal)
        self.linker._tick()
        self.assertEqual(self.linker.rejected, 1, "非法局面应该被挡下")
        self.assertIn("规则校验不通过", self.linker.last_error)
        self.assertEqual(int(self.session.state()["ply"]), 0)
        # 关掉严格校验：这一帧不再被拦（交给多帧确认和后面的合法性判断）
        self.session.update_settings({"link_strict_validate": False})
        self.linker._tick()
        self.assertEqual(self.linker.rejected, 1, "关掉校验后不再拦截")

    def test_unchanged_screen_skips_inference(self):
        """画面一模一样 → 不重复跑模型（沿用上一帧的识别结果）。"""
        real = self.linker.recognizer
        calls = {"n": 0}

        class Counting:
            backend = "yolo"
            error = ""
            grid = None
            reject_reason = ""

            def recognize(self, image):
                calls["n"] += 1
                return real.recognize(image)

        self.linker.recognizer = Counting()
        self.linker.win32 = FakeWindow(render([]))
        self.linker._tick()
        self.assertEqual(calls["n"], 1)
        self.linker._tick()                       # 同一张图：应该跳过推理
        self.assertEqual(calls["n"], 1, "画面没变不该再跑模型")
        self.assertGreaterEqual(self.linker.skipped_frames, 1)
        self.linker.win32 = FakeWindow(render(["h0g2"]))
        self.linker._tick()                       # 画面变了：必须重新识别
        self.assertEqual(calls["n"], 2, "画面一变就要重新识别")

    def test_local_move_clicked_while_change_pending(self):
        """局面还在等确认时，本程序的着法（兜底路径）也要能立刻点出去。"""
        self.linker._tick()                       # 建立基准
        self.linker.win32 = FakeWindow(render(["h0g2"]))
        self.linker._tick()                       # 第 1 帧：待确认
        self.assertEqual(self.linker.pending_frames, 1)
        window = FakeWindow(render(["h0g2"]))
        self.linker.win32 = window
        # 本程序走一步（红方走「炮八平五」，事件路径会先点一次）
        self.assertTrue(self.session.play_move("b2", "e2"))
        window.clicks.clear()
        self.linker.sent_ply = 0                  # 假装事件那条路没点出去，交给兜底
        self.linker._tick()
        self.assertTrue(window.clicks, "待确认期间的兜底点击也必须发出去")

    def test_inference_threads_and_warmup(self):
        """推理线程改成 4；连接时做一次预热（省掉首帧冷启动）。"""
        self.assertEqual(self.linker.recognizer.threads, 4)
        if self.linker.recognizer.backend == "yolo":
            self.assertTrue(self.linker.recognizer.warmed_up, "连接时应该预热一次")

    def test_local_move_is_clicked_out(self):
        """本程序走出新招、对方窗口还没出现 → 应该点两步出去。"""
        self.linker._tick()
        self.linker.win32 = FakeWindow(render(["h0g2"]))
        for _ in range(self.linker.confirm_frames):
            self.linker._tick()                  # 上行（3 帧确认后同步）
        window = FakeWindow(render(["h0g2"]))    # 对方窗口还停在上一步
        self.linker.win32 = window
        self.session.play_move("h9", "g7")       # 本程序走一步（模拟引擎）
        # 走子事件会立刻把这一步点到对方窗口
        self.assertEqual(len(window.clicks), 2, "应该点出起点和终点")
        self.linker.win32 = FakeWindow(render(["h0g2", "h9g7"]))
        for _ in range(self.linker.confirm_frames):
            self.linker._tick()
        self.assertEqual(self.linker.win32.clicks, [], "同步过的着法不能再点一次")

    def test_cancel_stops_everything(self):
        self.linker._tick()
        self.assertTrue(self.linker.cancel.is_set() is False)
        self.linker.stop()
        self.assertFalse(self.linker.running)
        self.assertTrue(self.linker.cancel.is_set())
        info = self.linker.status()
        self.assertFalse(info["running"])
        self.assertTrue(info["cancelled"])

    def test_link_never_flips_our_board(self):
        """连线不能自动翻转我们的棋盘（用户执红就该显示红方在下）。"""
        self.session.update_settings({"flip": False})
        self.linker._tick()
        self.assertFalse(self.session.settings.get("flip"),
                         "连线过程中不该改动我们自己的翻转设置")
        # 模拟"对方平台是红方在下"（上下颠倒）：只影响点击坐标，不改界面
        self.linker.vertical = True
        self.linker.flipped = False
        point = self.linker._client_point("h2")          # 红方右炮
        self.assertIsNotNone(point)
        self.linker.vertical = False
        normal = self.linker._client_point("h2")
        self.assertNotEqual(point, normal, "对方棋盘颠倒时点击坐标要跟着换算")
        self.assertFalse(self.session.settings.get("flip"))


if __name__ == "__main__":
    unittest.main()
