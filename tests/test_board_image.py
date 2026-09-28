# -*- coding: utf-8 -*-
"""局面图片功能测试：导出图片能画出来，导出的图片能被识别回局面。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xqtrainer.board import START_FEN, Board  # noqa: E402
from xqtrainer.ui import board_image  # noqa: E402

START_ROWS = ["rnbakabnr", "9", "1c5c1", "p1p1p1p1p", "9", "9",
              "P1P1P1P1P", "1C5C1", "9", "RNBAKABNR"]


def expanded(rows):
    """把带数字段的 FEN 行展开成 9 个格子一行（识别结果用的就是这种写法）。"""
    return ["".join(board_image._expand_row(row)) for row in rows]


@unittest.skipUnless(board_image.available(),
                     f"没有 Pillow，跳过图片测试：{board_image.unavailable_reason()}")
class TestBoardImage(unittest.TestCase):
    def test_render_creates_image(self):
        image = board_image.render_position(START_ROWS, "w", cell=64,
                                            title="静夜象棋界面")
        self.assertGreater(image.size[0], 400)
        self.assertGreater(image.size[1], 400)
        colors = image.convert("RGB").getcolors(maxcolors=200000)
        self.assertGreater(len(colors), 20, "棋盘图片不应该只有一两种颜色")

    def test_render_then_recognize_start_position(self):
        image = board_image.render_position(START_ROWS, "w", cell=72)
        result = board_image.recognize_position(image)
        self.assertTrue(result["ok"], result.get("message"))
        self.assertEqual(result["boardRows"], expanded(START_ROWS), result["boardRows"])
        self.assertEqual(result["unknown"], [])

    def test_recognize_after_moves(self):
        board = Board()
        for iccs in ("h2e2", "h9g7", "h0g2", "i9i8", "i0h0"):
            board.push(board.find_move_iccs(iccs))
        rows = board.to_fen().split()[0].split("/")
        image = board_image.render_position(rows, "b", cell=72, theme="walnut")
        result = board_image.recognize_position(image)
        self.assertTrue(result["ok"], result.get("message"))
        self.assertEqual(result["boardRows"], expanded(rows), result["boardRows"])

    def test_recognize_plain_theme(self):
        image = board_image.render_position(START_ROWS, "w", cell=72, theme="plain",
                                            show_numbers=False)
        result = board_image.recognize_position(image)
        self.assertTrue(result["ok"], result.get("message"))
        self.assertEqual(result["boardRows"], expanded(START_ROWS), result["boardRows"])

    def test_recognize_green_theme(self):
        """换一套配色的棋盘（绿呢）也要认得出来。"""
        image = board_image.render_position(START_ROWS, "w", cell=72, theme="green")
        result = board_image.recognize_position(image)
        self.assertTrue(result["ok"], result.get("message"))
        self.assertEqual(result["boardRows"], expanded(START_ROWS), result["boardRows"])

    def test_recognize_rejects_blank_image(self):
        from PIL import Image

        blank = Image.new("RGB", (400, 440), "#ffffff")
        result = board_image.recognize_position(blank)
        self.assertFalse(result["ok"])
        self.assertIn("棋盘", str(result.get("message")))

    def test_start_fen_round_trip(self):
        board = Board(START_FEN)
        rows = board.to_fen().split()[0].split("/")
        image = board_image.render_position(rows, board.side)
        result = board_image.recognize_position(image)
        self.assertTrue(result["ok"])
        rebuilt = Board(result["board"] + " w - - 0 1")
        self.assertEqual(rebuilt.to_fen().split()[0], board.to_fen().split()[0])


if __name__ == "__main__":
    unittest.main()
