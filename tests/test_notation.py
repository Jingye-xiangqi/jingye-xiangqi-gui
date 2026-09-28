# -*- coding: utf-8 -*-
"""记谱测试：ICCS / WXF / 中文记谱的生成与解析。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xqtrainer import notation as nt  # noqa: E402
from xqtrainer.board import Board  # noqa: E402


class TestNotation(unittest.TestCase):
    def setUp(self):
        self.board = Board()

    def text_of(self, iccs):
        move = self.board.find_move_iccs(iccs)
        self.assertIsNotNone(move, iccs)
        return nt.to_chinese(self.board, move)

    def test_red_moves(self):
        self.assertEqual(self.text_of("h2e2"), "炮二平五")
        self.assertEqual(self.text_of("b2e2"), "炮八平五")
        self.assertEqual(self.text_of("h0g2"), "马二进三")
        self.assertEqual(self.text_of("i0i1"), "车一进一")
        self.assertEqual(self.text_of("c3c4"), "兵七进一")
        self.assertEqual(self.text_of("e0e1"), "帅五进一")

    def test_black_moves(self):
        board = Board("rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR b - - 0 1")
        cases = {"h7e7": "炮8平5", "h9g7": "马8进7", "a6a5": "卒1进1", "b7b6": "炮2进1"}
        for iccs, expected in cases.items():
            move = board.find_move_iccs(iccs)
            self.assertIsNotNone(move, iccs)
            self.assertEqual(nt.to_chinese(board, move), expected, iccs)

    def test_front_back_prefix(self):
        board = Board("3k5/9/9/9/9/9/9/R8/9/R3K4 w - - 0 1")
        texts = {move.iccs(): nt.to_chinese(board, move) for move in board.legal_moves()
                 if move.piece == "R"}
        self.assertEqual(texts["a2a3"], "前车进一")
        self.assertEqual(texts["a0a1"], "后车进一")
        self.assertNotIn("a0a2", texts)

    def test_wxf(self):
        self.assertEqual(nt.to_wxf(self.board, self.board.find_move_iccs("h2e2")), "C2.5")
        self.assertEqual(nt.to_wxf(self.board, self.board.find_move_iccs("h0g2")), "H2+3")
        self.assertEqual(nt.to_wxf(self.board, self.board.find_move_iccs("g0e2")), "E3+5")
        # 解析：支持简体、繁体、全角数字、WXF
        for text, iccs in (("炮二平五", "h2e2"), ("馬２進３", "h0g2"), ("H2+3", "h0g2"),
                           ("N2+3", "h0g2"), ("C2.5", "h2e2"), ("炮八平五", "b2e2"),
                           ("砲２平５", "h2e2")):
            move = nt.parse_move(self.board, text)
            self.assertIsNotNone(move, text)
            self.assertEqual(move.iccs(), iccs, text)
        # 车一进三 会被自己的兵挡住，属于非法着法
        self.assertIsNone(nt.parse_move(self.board, "车一进三"))

    def test_traditional_glyphs(self):
        move = self.board.find_move_iccs("h0g2")
        self.assertEqual(nt.to_chinese(self.board, move, traditional=True), "傌二进三")

    def test_roundtrip_all_opening_moves(self):
        for move in self.board.legal_moves():
            text = nt.to_chinese(self.board, move)
            parsed = nt.parse_chinese(self.board, text)
            self.assertIsNotNone(parsed, text)
            self.assertEqual(parsed.iccs(), move.iccs(), text)
            wxf = nt.to_wxf(self.board, move)
            parsed_wxf = nt.parse_wxf(self.board, wxf)
            self.assertIsNotNone(parsed_wxf, wxf)
            self.assertEqual(parsed_wxf.iccs(), move.iccs(), wxf)


if __name__ == "__main__":
    unittest.main()
