# -*- coding: utf-8 -*-
"""棋谱树与 PGN 导入导出测试。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xqtrainer.board import Board  # noqa: E402
from xqtrainer.game import GameTree, decode_bytes, parse_pgn, parse_wxf  # noqa: E402


class TestPgnIccsVariants(unittest.TestCase):
    """真实棋谱里常见的 ICCS 写法（大写 + 连字符，如 C3-C4）必须能读。"""

    SAMPLE = ('[Format "ICCS"]\n[Event "对局"]\n\n'
              "{#1,1#}\n\n  1. C3-C4 {#1,0,0#本地库: 跑库}\n\n     B7-C7 {注释}\n\n"
              "  2. H2-E2 G9-E7\n  3. B0-A2 A9-A8 *\n")

    def test_dashed_uppercase_iccs(self):
        from xqtrainer.game import parse_pgn

        games = parse_pgn(self.SAMPLE)
        self.assertEqual(len(games), 1)
        tree = games[0]
        self.assertEqual(tree.count_nodes(), 7)          # 根 + 6 手
        line = tree.all_lines()[0]
        self.assertEqual(line, ["c3c4", "b7c7", "h2e2", "g9e7", "b0a2", "a9a8"])
        # 中文记谱也要正确（这一步以前读不出来，界面就是空棋谱）
        self.assertEqual(tree.current.children[0].cn, "兵七进一")

    def test_capture_marker_and_lowercase(self):
        from xqtrainer.game import parse_pgn

        for text, expected in (("1. C3xC4 B7-C7 *\n", "c3c4"),
                               ("1. c3-c4 b7-c7 *\n", "c3c4")):
            tree = parse_pgn(text)[0]
            self.assertEqual(tree.all_lines()[0][0], expected, text)

    def test_real_desktop_pgn_if_exists(self):
        """如果桌面上放着真实棋谱，必须能整盘读出来。"""
        folder = Path.home() / "Desktop"
        files = sorted(folder.glob("*.pgn")) if folder.is_dir() else []
        if not files:
            self.skipTest("桌面没有 pgn 文件")
        from xqtrainer.game import decode_bytes, parse_pgn

        data = files[0].read_bytes()
        games = parse_pgn(decode_bytes(data))
        self.assertTrue(games)
        self.assertGreater(games[0].count_nodes(), 10, f"{files[0].name} 没有读出手数")


class TestGameTree(unittest.TestCase):
    def setUp(self):
        self.tree = GameTree()

    def test_play_and_notation(self):
        node = self.tree.play_iccs("h2e2")
        self.assertEqual(node.cn, "炮二平五")
        node = self.tree.play_iccs("h9g7")
        self.assertEqual(node.cn, "马8进7")
        self.assertEqual(len(self.tree.line_to()), 3)
        self.assertEqual(self.tree.path_iccs(), ["h2e2", "h9g7"])

    def test_variation_branch(self):
        self.tree.play_iccs("h2e2")
        self.tree.play_iccs("h9g7")
        self.tree.go_prev()
        self.tree.play_iccs("b9c7")       # 形成变例
        self.assertEqual(len(self.tree.root.children[0].children), 2)
        self.assertEqual(self.tree.all_lines(),
                         [["h2e2", "h9g7"], ["h2e2", "b9c7"]])
        # 重复走已有变例会直接进入该节点，不会重复添加
        self.tree.go_root()
        self.tree.play_iccs("h2e2")
        self.tree.play_iccs("h9g7")
        self.assertEqual(len(self.tree.root.children[0].children), 2)

    def test_delete_and_promote(self):
        self.tree.play_iccs("h2e2")
        self.tree.play_iccs("h9g7")
        self.tree.go_prev()
        branch = self.tree.play_iccs("b9c7")
        self.assertTrue(self.tree.promote_variation(branch))
        self.assertEqual(self.tree.root.children[0].children[0].iccs, "b9c7")
        self.assertTrue(self.tree.delete_subtree(branch))
        self.assertEqual(len(self.tree.root.children[0].children), 1)
        self.assertFalse(self.tree.delete_subtree(self.tree.root))

    def test_illegal_move_rejected(self):
        from xqtrainer.board import Move
        self.assertIsNone(self.tree.play_iccs("h2e5"))
        with self.assertRaises(ValueError):
            self.tree.play(Move(7, 2, 4, 5))

    def test_comment_and_repetition(self):
        board = Board()
        for iccs in ("h2e2", "h9g7", "e2h2", "g7h9", "h2e2", "h9g7"):
            node = self.tree.play_iccs(iccs)
            self.assertIsNotNone(node, iccs)
        self.tree.play_iccs("e2h2")
        self.tree.current.comment = "循环着法"
        self.assertEqual(self.tree.current.comment, "循环着法")
        # 该局面（黑方走棋）此前出现过一次，故为第 2 次出现
        self.assertEqual(self.tree.repetition_count(), 2)


class TestPgn(unittest.TestCase):
    PGN = (
        '[Game "Chinese Chess"]\n[Event "测试赛"]\n[Red "甲"]\n[Black "乙"]\n'
        '[Result "1-0"]\n\n'
        "1. 炮二平五 马8进7 2. 马二进三 车9平8 3. 车一平二 {稳健} 炮8进4 "
        "(3... 卒7进1 4. 兵七进一) 4. 兵三进一 1-0\n"
    )

    def test_parse_chinese_pgn(self):
        tree = parse_pgn(self.PGN)[0]
        self.assertEqual(tree.meta["Red"], "甲")
        main = tree.root.children[0]
        self.assertEqual(main.iccs, "h2e2")
        self.assertEqual(main.cn, "炮二平五")
        lines = tree.all_lines()
        self.assertEqual(lines[0],
                         ["h2e2", "h9g7", "h0g2", "i9h9", "i0h0", "h7h3", "g3g4"])
        self.assertEqual(lines[1],
                         ["h2e2", "h9g7", "h0g2", "i9h9", "i0h0", "g6g5", "c3c4"])
        comments = [node.comment for node in tree.nodes() if node.comment]
        self.assertIn("稳健", comments)

    def test_pgn_roundtrip(self):
        tree = parse_pgn(self.PGN)[0]
        text = tree.to_pgn(style="iccs")
        again = parse_pgn(text)[0]
        self.assertEqual(again.all_lines(), tree.all_lines())
        self.assertEqual(again.meta["Red"], "甲")

    def test_export_chinese_style(self):
        tree = parse_pgn(self.PGN)[0]
        text = tree.to_pgn(style="cn")
        self.assertIn("炮二平五", text)
        self.assertIn("马8进7", text)
        self.assertIn("(", text)   # 变例

    def test_import_iccs_and_wxf(self):
        tree = parse_pgn("1. h2e2 h9g7 2. H2+3 E3+5 *")[0]
        self.assertEqual(tree.all_lines()[0], ["h2e2", "h9g7", "h0g2", "c9e7"])
        self.assertEqual(tree.warnings, [])

    def test_decode_bytes(self):
        text = "1. 炮二平五 马8进7"
        self.assertEqual(decode_bytes(text.encode("gb18030")), text)
        self.assertEqual(decode_bytes(text.encode("utf-8")), text)


class TestWxfImport(unittest.TestCase):
    """WXF（世界象棋联合会记谱）文本棋谱导入。"""

    def test_with_tags(self):
        text = ('[Game "Chinese Chess"]\n[Red "甲"]\n[Black "乙"]\n\n'
                "1. C2.5 H8+7 2. H2+3 R9.8 3. R1.2 *\n")
        games = parse_wxf(text)
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0].meta["Red"], "甲")
        self.assertEqual(games[0].all_lines()[0],
                         ["h2e2", "h9g7", "h0g2", "i9h9", "i0h0"])

    def test_plain_move_list(self):
        games = parse_wxf("1. C2.5 H8+7 2. H2+3")
        self.assertEqual(games[0].all_lines()[0], ["h2e2", "h9g7", "h0g2"])

    def test_first_line_fen(self):
        text = "3k5/9/9/9/9/9/9/9/9/4K4 w - - 0 1\n1. K5+1"
        games = parse_wxf(text)
        self.assertEqual(games[0].start_fen.split()[0], "3k5/9/9/9/9/9/9/9/9/4K4")
        self.assertEqual(games[0].all_lines()[0], ["e0e1"])

    def test_with_iccs_moves(self):
        games = parse_wxf("1. h2e2 h9g7")
        self.assertEqual(games[0].all_lines()[0], ["h2e2", "h9g7"])


if __name__ == "__main__":
    unittest.main()
