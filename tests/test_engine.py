# -*- coding: utf-8 -*-
"""UCI 引擎封装测试。

用 ``tests/mock_engine.py``（一个极简 UCI 引擎）验证握手、选项解析、搜索信息、
bestmove、stop 以及错误处理。真实引擎（皮卡鱼）也可以用它做同样的事：

    python -m unittest tests.test_engine -v            # 只跑假引擎
    XQ_ENGINE=D:\\Pikafish\\pikafish.exe python -m unittest tests.test_engine
"""

import os
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xqtrainer.board import START_FEN  # noqa: E402
from xqtrainer.engine import EngineError, UciEngine, parse_info  # noqa: E402

MOCK = Path(__file__).resolve().parent / "mock_engine.py"


def engine_command():
    """返回 (可执行文件, 参数列表)：优先使用真实引擎，否则使用假引擎。"""
    real = os.environ.get("XQ_ENGINE")
    if real:
        return real, []
    return sys.executable, [str(MOCK)]


class TestInfoParsing(unittest.TestCase):
    def test_parse_info_line(self):
        line = ("info depth 18 seldepth 24 multipv 2 score cp 35 nodes 1234567 nps 900000 "
                "hashfull 120 tbhits 0 time 1400 wdl 620 900 30 pv h2e2 h9g7 h0g2")
        data = parse_info(line)
        self.assertEqual(data["depth"], 18)
        self.assertEqual(data["seldepth"], 24)
        self.assertEqual(data["multipv"], 2)
        self.assertEqual(data["score"], {"cp": 35})
        self.assertEqual(data["nodes"], 1234567)
        self.assertEqual(data["wdl"], [620, 900, 30])
        self.assertEqual(data["pv"], ["h2e2", "h9g7", "h0g2"])

    def test_parse_mate_score(self):
        data = parse_info("info depth 9 score mate -3 pv e9e8")
        self.assertEqual(data["score"], {"mate": -3})


class TestMockEngine(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.engine = UciEngine(on_event=self.events.append)
        path, args = engine_command()
        self.engine.start(path, args=args, timeout=25)

    def tearDown(self):
        self.engine.stop_process()

    def wait_for(self, kind, timeout=20.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for event in self.events:
                if event.get("type") == kind:
                    return event
            time.sleep(0.05)
        return None

    def test_handshake_options(self):
        names = [option.name for option in self.engine.options]
        self.assertIn("Threads", names)
        self.assertIn("MultiPV", names)
        self.assertEqual(self.engine.state, "ready")
        self.assertTrue(self.engine.is_running())

    def test_search_with_depth(self):
        self.engine.analyze(START_FEN, limit="depth 4", multipv=3)
        bestmove = self.wait_for("bestmove")
        self.assertIsNotNone(bestmove, "没有收到 bestmove")
        self.assertTrue(bestmove["move"])
        infos = [event for event in self.events if event.get("type") == "info"]
        self.assertTrue(infos)
        self.assertTrue(any(line.get("pv") for line in infos))
        self.assertTrue({"depth", "score", "nodes", "time"} <= set(infos[-1]))
        multipv = {line.get("multipv") for line in infos}
        self.assertIn(3, multipv)

    def test_movetime_search(self):
        start = time.time()
        self.engine.analyze(START_FEN, limit="movetime 600")
        bestmove = self.wait_for("bestmove")
        self.assertIsNotNone(bestmove)
        self.assertGreaterEqual(time.time() - start, 0.4)

    def test_infinite_then_stop(self):
        self.engine.analyze(START_FEN, limit="infinite")
        time.sleep(0.3)
        self.assertTrue(self.engine.searching)
        self.engine.stop()
        bestmove = self.wait_for("bestmove")
        self.assertIsNotNone(bestmove)
        self.assertFalse(self.engine.searching)

    def test_bestmove_is_legal(self):
        from xqtrainer.board import Board
        self.engine.analyze(START_FEN, limit="depth 2")
        bestmove = self.wait_for("bestmove")
        board = Board()
        self.assertIsNotNone(board.find_move_iccs(bestmove["move"]))

    def test_set_option_clamped(self):
        self.engine.set_option("Threads", 99999)
        self.assertTrue(self.engine.wait_ready(5))


class TestEngineErrors(unittest.TestCase):
    def test_missing_file(self):
        engine = UciEngine()
        with self.assertRaises(EngineError):
            engine.start(str(MOCK.parent / "not_an_engine.exe"), timeout=2)

    def test_directory_rejected(self):
        engine = UciEngine()
        with self.assertRaises(EngineError):
            engine.start(str(MOCK.parent), timeout=2)

    def test_not_an_engine(self):
        target = MOCK.parent / "_not_engine.txt"
        target.write_text("hello", encoding="utf-8")
        try:
            engine = UciEngine()
            with self.assertRaises(EngineError):
                engine.start(str(target), args=[], timeout=2)
        finally:
            target.unlink(missing_ok=True)

    def test_analyze_without_engine(self):
        engine = UciEngine()
        with self.assertRaises(EngineError):
            engine.analyze(START_FEN, limit="depth 2")


if __name__ == "__main__":
    unittest.main()
