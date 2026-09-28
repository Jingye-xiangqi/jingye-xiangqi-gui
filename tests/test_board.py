# -*- coding: utf-8 -*-
"""规则内核测试：FEN、走子生成、合法性、将死/困毙、记谱。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xqtrainer import notation as nt  # noqa: E402
from xqtrainer.board import BLACK, RED, START_FEN, Board  # noqa: E402


class TestBoardBasics(unittest.TestCase):
    def test_start_fen_roundtrip(self):
        board = Board()
        self.assertEqual(board.to_fen(), START_FEN)
        self.assertEqual(len(board.legal_moves()), 44)

    def test_fen_positions(self):
        board = Board("4k4/9/9/9/9/9/9/9/9/4K4 w - - 0 1")
        self.assertEqual(board.king_square(RED), (4, 0))
        self.assertEqual(board.king_square(BLACK), (4, 9))
        # 白脸将：中间无子时双方互相"攻击"
        self.assertTrue(board.is_check(RED))
        self.assertTrue(board.is_check(BLACK))

    def test_bad_fen_rejected(self):
        for fen in ("", "9/9/9", "4k4/9/9/9/9/9/9/9/9/4K3K w - - 0 1"):
            with self.assertRaises(ValueError):
                Board(fen)

    def test_palace_and_river_rules(self):
        # 帅不能出九宫，仕不能出九宫，相不能过河
        board = Board("4k4/9/9/9/9/9/9/9/9/4K4 w - - 0 1")
        self.assertFalse(board.in_palace(4, 3, RED))
        self.assertTrue(board.in_palace(3, 0, RED))
        self.assertTrue(board.on_own_side(4, RED))
        self.assertFalse(board.on_own_side(5, RED))


class TestPerft(unittest.TestCase):
    """标准中国象棋 perft 值，用来验证走子生成的正确性。"""

    EXPECTED = {1: 44, 2: 1920, 3: 79666}

    def test_perft(self):
        board = Board()
        for depth, expected in self.EXPECTED.items():
            with self.subTest(depth=depth):
                self.assertEqual(board.perft(depth), expected)


class TestLegality(unittest.TestCase):
    def test_cannot_expose_king(self):
        # 红车在 e1 挡住"白脸将"，因此它不能离开 e 线，但可以沿线移动
        board = Board("4k4/9/9/9/9/9/9/9/4R4/4K4 w - - 0 1")
        moves = {move.iccs() for move in board.legal_moves()}
        self.assertIn("e1e2", moves)
        self.assertNotIn("e1a1", moves)
        self.assertNotIn("e1f1", moves)

    def test_cannon_needs_screen(self):
        # 炮吃子必须隔一个子：e0 的炮打不到 e9 的将（中间没有炮架）
        board = Board("4k4/9/9/9/9/9/9/9/9/4CK3 w - - 0 1")
        moves = {move.iccs() for move in board.legal_moves()}
        self.assertNotIn("e0e9", moves)
        # 放一个炮架在 e5 之后就能打到
        board = Board("4k4/9/9/9/4P4/9/9/9/9/4CK3 w - - 0 1")
        moves = {move.iccs() for move in board.legal_moves()}
        self.assertIn("e0e9", moves)

    def test_checkmate(self):
        # 黑将困在 d9：e9 的车将军（且被 e0 的车保护），d8 被 d0 的车控制
        board = Board("3kR4/9/9/9/9/9/9/9/9/3RRK3 b - - 0 1")
        self.assertTrue(board.is_check(BLACK))
        self.assertTrue(board.is_checkmate(BLACK))
        self.assertEqual(board.result(), "red")

    def test_stalemate_is_loss(self):
        # 黑将无着可走且未被将军（红马控制 d8 与 e9）-> 困毙，判红胜
        board = Board("3k5/9/5N3/9/9/9/9/9/9/4K4 b - - 0 1")
        self.assertFalse(board.is_check(BLACK))
        self.assertEqual(board.legal_moves(), [])
        self.assertTrue(board.is_stalemate(BLACK))
        self.assertEqual(board.result(), "red")

    def test_push_pop_restores(self):
        board = Board()
        before = board.to_fen()
        move = board.find_move_iccs("h2e2")
        board.push(move)
        self.assertNotEqual(board.to_fen(), before)
        board.pop()
        self.assertEqual(board.to_fen(), before)

    def test_illegal_move_rejected(self):
        board = Board()
        self.assertIsNone(board.find_move_iccs("h2e3"))
        self.assertIsNone(board.find_move("a0", "a5"))
        self.assertIsNotNone(board.find_move("a0", "a1"))


if __name__ == "__main__":
    unittest.main()
