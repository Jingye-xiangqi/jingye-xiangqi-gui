# -*- coding: utf-8 -*-
"""开局库 / 云库测试：OBK 哈希与读写、云库文本、棋谱建库、库着优先。"""

import json
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xqtrainer.board import Board  # noqa: E402
from xqtrainer.book import (BOOK_SUFFIXES, BookFormatError,  # noqa: E402
                            BookManager, ObkBook, PgnBook, SqliteBook, TextBook, obk_hash,
                            write_obk, xq_book_hash, xq_book_move)
from xqtrainer.game import GameTree  # noqa: E402
from xqtrainer.session import Session  # noqa: E402

TMP = Path(__file__).resolve().parent / "_tmp_book"


class TestObkHash(unittest.TestCase):
    def test_start_position_hash_matches_reference(self):
        """起始局面的哈希必须等于参考实现里写死的常量（独立校验哈希表与编码）。"""
        self.assertEqual(obk_hash(Board()), 6665246470371296167)

    def test_hash_is_symmetric_about_side_to_move(self):
        board = Board()
        board.side = "b"
        self.assertEqual(obk_hash(board), obk_hash(Board()))

    def test_hash_changes_after_move(self):
        board = Board()
        before = obk_hash(board)
        board.push(board.find_move_iccs("h2e2"))
        self.assertNotEqual(obk_hash(board), before)


class TestObkWriteRead(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(exist_ok=True)
        self.path = TMP / "test.obk"

    def tearDown(self):
        for item in TMP.glob("*"):
            item.unlink(missing_ok=True)
        TMP.rmdir()

    def build_entries(self):
        board = Board()
        entries = {}
        for iccs, count in (("h2e2", 30), ("b2e2", 5), ("h0g2", 12)):
            move = board.find_move_iccs(iccs)
            board.push(move)
            entries[(1, obk_hash(board))] = count      # 1 = 红方走的
            board.pop()
        board.push(board.find_move_iccs("h2e2"))
        for iccs, count in (("h9g7", 20), ("b9c7", 9)):
            move = board.find_move_iccs(iccs)
            board.push(move)
            entries[(0, obk_hash(board))] = count      # 0 = 黑方走的
            board.pop()
        return entries

    def test_round_trip(self):
        entries = self.build_entries()
        written = write_obk(self.path, entries, info="测试库")
        self.assertEqual(written, len(entries))
        book = ObkBook(self.path)
        self.assertEqual(book.counts, (2, 3))          # 黑 2 条、红 3 条
        self.assertIn("测试库", book.describe())
        hits = book.lookup(Board(), limit=10)
        self.assertEqual([hit.move for hit in hits], ["h2e2", "h0g2", "b2e2"])
        self.assertEqual(hits[0].count, 30)
        board = Board()
        board.push(board.find_move_iccs("h2e2"))
        hits2 = book.lookup(board, limit=10)
        self.assertEqual([hit.move for hit in hits2], ["h9g7", "b9c7"])

    def test_bad_file_rejected(self):
        broken = TMP / "broken.obk"
        broken.write_bytes(b"\x00" * 200)
        with self.assertRaises(ValueError):
            ObkBook(broken)


class TestTextBook(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(exist_ok=True)

    def tearDown(self):
        for item in TMP.glob("*"):
            item.unlink(missing_ok=True)
        TMP.rmdir()

    def test_text_format(self):
        path = TMP / "cloud.txt"
        path.write_text(
            "# 云库示例\n"
            "fen=rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w | h2e2 | w=120 d=60 l=20 count=200\n"
            "fen=rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w | c3c4 | w=80 d=40 l=30 count=150\n",
            encoding="utf-8")
        book = TextBook(path)
        hits = book.lookup(Board(), limit=5)
        self.assertEqual([hit.move for hit in hits], ["h2e2", "c3c4"])
        self.assertEqual(hits[0].count, 200)
        self.assertAlmostEqual(hits[0].win_rate, (120 + 30) / 200, places=6)

    def test_json_format(self):
        path = TMP / "cloud.json"
        payload = {
            "positions": [{
                "fen": "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w",
                "moves": [{"move": "h2e2", "wins": 10, "draws": 5, "losses": 5, "count": 20}],
            }]
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        book = TextBook(path)
        hits = book.lookup(Board(), limit=5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].move, "h2e2")
        self.assertEqual(hits[0].count, 20)
        self.assertAlmostEqual(hits[0].win_rate, 0.625, places=6)


class TestPgnBook(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(exist_ok=True)

    def tearDown(self):
        for item in TMP.glob("*"):
            item.unlink(missing_ok=True)
        TMP.rmdir()

    def make_tree(self, moves, result):
        tree = GameTree(meta={"Result": result})
        for iccs in moves:
            tree.play_iccs(iccs)
        tree.go_root()
        return tree

    def test_build_from_games(self):
        book = PgnBook()
        book.add_game(["h2e2", "h9g7", "h0g2"], "1-0")
        book.add_game(["h2e2", "h9g7", "b0c2"], "0-1")
        book.add_game(["h2e2", "b9c7"], "1/2-1/2")
        hits = book.lookup(Board(), limit=5)
        self.assertEqual([hit.move for hit in hits], ["h2e2"])
        self.assertEqual(hits[0].count, 3)
        self.assertEqual(hits[0].wins, 1)
        self.assertEqual(hits[0].losses, 1)
        self.assertEqual(hits[0].draws, 1)
        board = Board()
        board.push(board.find_move_iccs("h2e2"))
        hits2 = book.lookup(board, limit=5)
        self.assertEqual({hit.move for hit in hits2}, {"h9g7", "b9c7"})

    def test_export_obk_and_read_back(self):
        book = PgnBook()
        for _ in range(3):
            book.add_game(["h2e2", "h9g7", "h0g2", "b9c7"], "1-0")
        book.add_game(["h2e2", "b9c7"], "0-1")
        target = TMP / "from_pgn.obk"
        written = book.to_obk(target)
        self.assertGreaterEqual(written, 4)
        loaded = ObkBook(target)
        hits = loaded.lookup(Board(), limit=5)
        self.assertEqual(hits[0].move, "h2e2")
        self.assertEqual(hits[0].count, 4)


class TestBookManager(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(exist_ok=True)

    def tearDown(self):
        for item in TMP.glob("*"):
            item.unlink(missing_ok=True)
        TMP.rmdir()

    def test_merge_and_session(self):
        obk_path = TMP / "merge.obk"
        board = Board()
        entries = {}
        for iccs, count in (("h2e2", 10), ("h0g2", 3)):
            move = board.find_move_iccs(iccs)
            board.push(move)
            entries[(1, obk_hash(board))] = count
            board.pop()
        write_obk(obk_path, entries)
        cloud_path = TMP / "merge.txt"
        cloud_path.write_text(
            "fen=rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w | h2e2 | w=5 d=3 l=2 count=10\n"
            "fen=rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w | c3c4 | w=1 d=1 l=1 count=3\n",
            encoding="utf-8")
        manager = BookManager()
        manager.load(obk_path)
        # 文本库属于程序内部来源（界面上只允许 PFBOOK/OBK/XQF），这里直接挂上做合并测试
        manager.text = TextBook(cloud_path)
        hits = manager.lookup(Board(), limit=5)
        moves = [hit.move for hit in hits]
        self.assertEqual(moves[0], "h2e2")
        self.assertIn("c3c4", moves)
        top = hits[0]
        self.assertEqual(top.count, 20)                  # 两个库的同一着法合并
        self.assertEqual(top.wins, 5)
        self.assertIn("OBK", top.source)
        self.assertIn("云库", top.source)

    def test_session_book_api(self):
        session = Session()
        path = TMP / "session.obk"
        board = Board()
        move = board.find_move_iccs("h2e2")
        board.push(move)
        write_obk(path, {(1, obk_hash(board)): 10})
        self.assertTrue(session.load_book(str(path)))
        state = session.state()
        self.assertTrue(state["book"]["loaded"])
        hits = state["book"]["hits"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["cn"], "炮二平五")
        self.assertEqual(hits[0]["count"], 10)
        self.assertIsNone(hits[0]["winRate"])       # OBK 只有出现次数，没有胜率
        session.clear_book()
        self.assertFalse(session.state()["book"]["loaded"])


class TestXqBookFormat(unittest.TestCase):
    """公开的 OBK / BHBK 开局库格式：Zobrist 哈希与着法编码（对齐参考实现）。"""

    def setUp(self):
        TMP.mkdir(exist_ok=True)

    def tearDown(self):
        for item in TMP.glob("*"):
            if item.is_file():
                item.unlink(missing_ok=True)
        try:
            TMP.rmdir()
        except OSError:
            pass

    def test_hash_matches_reference_book_key(self):
        """起始局面的键必须等于真实库里第一条记录的 vkey（7101337512282506414）。"""
        self.assertEqual(xq_book_hash(Board()), 7101337512282506414)

    def test_hash_includes_side_to_move(self):
        board = Board()
        board.side = "b"
        self.assertNotEqual(xq_book_hash(board), xq_book_hash(Board()))

    def test_move_decoding(self):
        self.assertEqual(xq_book_move(0x3343), "a9a8")
        self.assertEqual(xq_book_move(0x3343, True), "i9i8")     # 左右镜像
        self.assertEqual(xq_book_move(0xAAA7), "h2e2")           # 炮二平五

    def test_sqlite_book_lookup_with_xq_hash(self):
        """用这套哈希建的库要能按局面查出招法、次数与胜率。"""
        import sqlite3

        TMP.mkdir(exist_ok=True)
        path = TMP / "xqbook.obk"
        connection = sqlite3.connect(path)
        connection.execute(
            "create table bhobk(id integer primary key, vkey integer, vmove integer, "
            "vscore integer, vwin integer, vdraw integer, vlost integer, vvalid integer)")
        connection.execute(
            "insert into bhobk(vkey, vmove, vscore, vwin, vdraw, vlost, vvalid) "
            "values (?,?,?,?,?,?,1)",
            (xq_book_hash(Board()), 0xAAA7, 30, 6, 2, 2))        # 炮二平五：6 胜 2 和 2 负
        connection.commit()
        connection.close()

        manager = BookManager()
        info = manager.load(path)
        self.assertIn("SQLite", info)
        hits = manager.lookup(Board(), limit=5)
        self.assertEqual([hit.move for hit in hits], ["h2e2"])
        self.assertEqual(hits[0].count, 10)
        self.assertAlmostEqual(hits[0].win_rate or 0, (6 + 1.0) / 10, places=3)
        self.assertEqual(hits[0].score, 30)
        manager.clear()

    def build_book(self, name, rows):
        import sqlite3

        TMP.mkdir(exist_ok=True)
        path = TMP / name
        connection = sqlite3.connect(path)
        connection.execute(
            "create table bhobk(id integer primary key, vkey integer, vmove integer, "
            "vscore integer, vwin integer, vdraw integer, vlost integer, vvalid integer)")
        connection.executemany(
            "insert into bhobk(vkey, vmove, vscore, vwin, vdraw, vlost, vvalid) "
            "values (?,?,?,?,?,?,1)", rows)
        connection.commit()
        connection.close()
        return path

    def test_no_fake_moves_when_book_has_nothing(self):
        """库里没有的招法绝不能显示：不合法的记录要丢掉，局面不在库里就留空。"""
        # 当前局面存了一条走不出来的编码（a9→a8，炮不能这样走）
        path = self.build_book("fake.obk", [(xq_book_hash(Board()), 0x3343, 10, 3, 0, 0)])
        manager = BookManager()
        manager.load(path)
        self.assertEqual(manager.lookup(Board(), limit=20), [])
        # 走了几步之后局面不在库里 → 也必须是空列表（不能拿别的局面凑数）
        board = Board()
        for iccs in ("h2e2", "h9g7", "h0g2"):
            board.push(board.find_move_iccs(iccs))
        self.assertEqual(manager.lookup(board, limit=20), [])
        manager.clear()

    def test_session_shows_chinese_notation_only_for_real_hits(self):
        """界面数据：命中时有中文记谱，没有命中时列表为空（不再有乱码）。"""
        path = self.build_book("real.obk", [(xq_book_hash(Board()), 0xAAA7, 30, 6, 2, 2)])
        session = Session()
        self.assertTrue(session.load_book(str(path)))
        hits = session.state()["book"]["hits"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["move"], "h2e2")
        self.assertEqual(hits[0]["cn"], "炮二平五")          # 中文记谱正确
        self.assertEqual(hits[0]["score"], 30)
        self.assertTrue(str(hits[0]["move"]).startswith(("a", "b", "c", "d", "e", "f", "g",
                                                        "h", "i")))
        session.play_move("h2", "e2")                        # 走到库里没有的局面
        self.pump_book(session)
        self.assertEqual(session.state()["book"]["hits"], [])
        session.clear_book()
        manager = BookManager()
        manager.clear()

    @staticmethod
    def pump_book(session) -> None:
        """等"局面变化后重新算库"完成（会话层是同步的，这里留个钩子）。"""
        return None


class TestSqliteBook(unittest.TestCase):
    """SQLite 开局库（跑库.obk / 屠龙.pfBook / 小冰.xqb 这类：key+move+score+win/draw/lost）。"""

    def setUp(self):
        TMP.mkdir(exist_ok=True)

    def tearDown(self):
        for item in TMP.glob("*"):
            item.unlink(missing_ok=True)
        TMP.rmdir()

    def build(self, name: str, rows):
        import sqlite3

        path = TMP / name
        connection = sqlite3.connect(path)
        connection.execute(
            "create table book(id integer primary key, key integer, move integer, "
            "score integer, win integer, draw integer, lost integer, valid integer)")
        connection.executemany(
            "insert into book(key, move, score, win, draw, lost, valid) "
            "values (?,?,?,?,?,?,1)", rows)
        connection.commit()
        connection.close()
        return path

    def test_describe_shows_table_and_rows(self):
        path = self.build("tiny.obk", [(1, 2, 3, 4, 5, 6)])
        book = SqliteBook(path)
        text = book.describe()
        self.assertIn("SQLite", text)
        self.assertIn("1 条记录", text)
        book.close()


class TestBookStrategy(unittest.TestCase):
    """库招策略（最高分 / 最高胜率 / 正分数随机 / 完全随机）、脱谱步数与多库优先级。"""

    START = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w"

    def setUp(self):
        TMP.mkdir(exist_ok=True)

    def tearDown(self):
        for item in TMP.glob("*"):
            item.unlink(missing_ok=True)
        TMP.rmdir()

    def text_book(self, name, lines):
        path = TMP / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return TextBook(path)

    def test_strategies_pick_expected_move(self):
        manager = BookManager()
        manager.text = self.text_book("strategy.txt", [
            f"fen={self.START} | h2e2 | w=9 d=0 l=1 count=10",     # 权重低、胜率 90%
            f"fen={self.START} | h0g2 | w=30 d=10 l=0 count=40",   # 权重最高、胜率 87.5%
            f"fen={self.START} | b2e2 | w=0 d=0 l=0 count=0",      # 权重 0（正分数随机要排除）
        ])
        manager.strategy = "best_score"
        self.assertEqual(manager.best_move(Board()).move, "h0g2")
        manager.strategy = "best_rate"
        self.assertEqual(manager.best_move(Board()).move, "h2e2")
        manager.strategy = "positive_random"
        seen = {manager.best_move(Board()).move for _ in range(80)}
        self.assertTrue(seen, "正分数随机至少要有结果")
        self.assertNotIn("b2e2", seen, "正分数随机不应选到权重为 0 的着法")
        manager.strategy = "random"
        seen = {manager.best_move(Board()).move for _ in range(200)}
        self.assertEqual(seen, {"h2e2", "h0g2", "b2e2"}, "完全随机应该覆盖所有着法")
        manager.strategy = "不存在的策略"            # 非法值回退到默认策略
        self.assertIn(manager.best_move(Board()).move, {"h2e2", "h0g2", "b2e2"})

    def test_out_of_book_and_switch(self):
        manager = BookManager()
        manager.text = self.text_book("ply.txt", [
            f"fen={self.START} | h2e2 | w=5 d=5 l=0 count=10"])
        manager.strategy = "best_score"
        self.assertIsNotNone(manager.best_move(Board(), 0))
        manager.max_ply = 4
        self.assertTrue(manager.in_book(4))
        self.assertFalse(manager.in_book(5))
        self.assertIsNotNone(manager.best_move(Board(), 4))
        self.assertIsNone(manager.best_move(Board(), 5), "超过脱谱步数不该再用库招")
        manager.max_ply = 0                     # 0＝不限
        self.assertTrue(manager.in_book(999))
        manager.use_in_game = False
        self.assertIsNone(manager.best_move(Board(), 0), "关闭库招后不该再用库招")

    def test_multiple_books_priority_and_removal(self):
        first = TMP / "first.obk"
        second = TMP / "second.obk"
        board = Board()
        board.push(board.find_move_iccs("h2e2"))
        write_obk(first, {(1, obk_hash(board)): 5})
        write_obk(second, {(1, obk_hash(board)): 7})
        manager = BookManager()
        self.assertTrue(manager.load(first))
        self.assertTrue(manager.load(second))
        self.assertEqual([item["label"] for item in manager.list_books()],
                         ["first.obk", "second.obk"])
        self.assertTrue(manager.move(str(second), -1))
        self.assertEqual([item["label"] for item in manager.list_books()],
                         ["second.obk", "first.obk"])
        self.assertFalse(manager.move(str(second), -1), "已经在最前面了")
        hit = manager.lookup(Board())[0]
        self.assertEqual(hit.count, 12, "多个库的同一着法要合并")
        self.assertIn("first.obk", hit.book)
        self.assertIn("second.obk", hit.book)
        self.assertTrue(manager.remove(str(first)))
        self.assertEqual(len(manager.sources), 1)
        self.assertTrue(manager.load(first))               # 重新加载 = 加回列表末尾
        self.assertEqual(len(manager.sources), 2)
        self.assertEqual(manager.load(first), manager.sources[-1].book.describe())
        self.assertEqual(len(manager.sources), 2, "重复加载同一个文件不该重复列出来")

    def test_session_book_settings(self):
        session = Session()
        state = session.state()["book"]
        self.assertTrue(state["useInGame"])
        self.assertEqual(state["strategy"], "best_score")    # 默认策略＝最高分
        self.assertEqual(state["maxPly"], 0)
        self.assertTrue(state["inBook"])
        session.set_book_strategy("random")
        session.set_book_max_ply(12)
        session.set_book_use_in_game(False)
        state = session.state()["book"]
        self.assertEqual(state["strategy"], "random")
        self.assertEqual(state["maxPly"], 12)
        self.assertFalse(state["useInGame"])
        self.assertEqual(session.settings["book_enabled"], False)
        # 通过 update_settings 也能改（浏览器版界面与配置文件走这条路）
        session.update_settings({"book_strategy": "best_score", "book_max_ply": 0,
                                 "book_enabled": True})
        state = session.state()["book"]
        self.assertEqual(state["strategy"], "best_score")
        self.assertEqual(state["maxPly"], 0)
        self.assertTrue(state["useInGame"])
        self.assertTrue(state["inBook"])


class TestFormatRules(unittest.TestCase):
    """开局库只允许 PFBOOK / OBK / XQF 三种格式，其它一律拒绝。"""

    def setUp(self):
        TMP.mkdir(exist_ok=True)

    def tearDown(self):
        for item in TMP.glob("*"):
            if item.is_file():
                item.unlink(missing_ok=True)
        TMP.rmdir()

    def test_allowed_suffixes(self):
        self.assertEqual(set(BOOK_SUFFIXES), {".pfbook", ".obk", ".xqb", ".xqf"})

    def test_other_formats_rejected(self):
        for name in ("a.txt", "b.json", "c.pgn", "d.bk", "e.wxf", "f.csv", "g"):
            path = TMP / name
            path.write_text("fen=x | h2e2 | count=1\n", encoding="utf-8")
            with self.subTest(file=name):
                with self.assertRaises(BookFormatError) as ctx:
                    BookManager().load(path)
                self.assertIn("只允许加载", str(ctx.exception))

    def test_pfbook_with_obk_structure(self):
        """PFBOOK 没有公开规范：与 OBK 同结构（仅签名不同）的库要能读出来。"""
        obk = TMP / "src.obk"
        board = Board()
        entries = {}
        for iccs, count in (("h2e2", 9), ("h0g2", 3)):
            move = board.find_move_iccs(iccs)
            board.push(move)
            entries[(1, obk_hash(board))] = count
            board.pop()
        write_obk(obk, entries)
        data = bytearray(obk.read_bytes())
        data[0:2] = b"\x2a\x00"                      # 换成别的签名
        pfbook = TMP / "book.pfbook"
        pfbook.write_bytes(bytes(data))
        manager = BookManager()
        manager.load(pfbook)
        hits = manager.lookup(Board(), limit=5)
        self.assertEqual(hits[0].move, "h2e2")
        self.assertEqual(hits[0].count, 9)

    def test_pfbook_unknown_structure_rejected_with_hint(self):
        path = TMP / "junk.pfbook"
        path.write_bytes(b"\x2a\x00" + b"\x11" * 600)
        with self.assertRaises(BookFormatError) as ctx:
            BookManager().load(path)
        self.assertIn("PFBOOK", str(ctx.exception))

    def test_xqf_as_book(self):
        """XQF 棋谱文件可以当棋谱库加载（按棋谱统计）。"""
        from xqtrainer.xqf import write_xqf

        tree = GameTree(meta={"Result": "1-0"})
        for iccs in ("h2e2", "h9g7", "h0g2", "b9c7"):
            tree.play_iccs(iccs)
        tree.go_root()
        path = TMP / "games.xqf"
        write_xqf(tree, path)
        manager = BookManager()
        manager.load(path)
        hits = manager.lookup(Board(), limit=5)
        self.assertEqual([hit.move for hit in hits], ["h2e2"])
        self.assertEqual(hits[0].count, 1)


class TestPgnFormatRule(unittest.TestCase):
    """棋谱只支持 XQF / PGN 两种格式。"""

    def test_import_rejects_other_kinds(self):
        session = Session()
        self.assertFalse(session.import_data("wxf", b"1. C2.5 H8+7", name="a.wxf"))
        self.assertIn("只支持两种格式", session.message)
        self.assertFalse(session.import_data("cbf", b"\x00" * 100, name="a.cbf"))

    def test_import_accepts_pgn_and_xqf(self):
        session = Session()
        self.assertTrue(session.import_data("pgn", b"1. h2e2 h9g7 *", name="a.pgn"))
        self.assertEqual(session.tree.all_lines()[0], ["h2e2", "h9g7"])


if __name__ == "__main__":
    unittest.main()
