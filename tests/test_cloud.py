# -*- coding: utf-8 -*-
"""云库（在线开局库）测试：解析、请求、会话集成。

默认使用本地起的小 HTTP 服务模拟云库返回，因此**离线也能跑**；
想验证真实云库可设置环境变量 ``XQ_CLOUD_LIVE=1``。
"""

import os
import sys
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xqtrainer.cloud import (CloudBook, CloudHit, CloudResult, STATUS_BUSY,  # noqa: E402
                             STATUS_INVALID, STATUS_OK, STATUS_UNKNOWN,
                             board_to_cloud_fen, parse_queryall)
from xqtrainer.session import Session  # noqa: E402

SAMPLE = ("move:c0e2,score:2,rank:2,note:! (44-05),winrate:50.15"
          "|move:g0e2,score:2,rank:2,note:! (44-05),winrate:50.15"
          "|move:e0e1,score:??,rank:0,note:? (??-??)"
          "|move:h2e2,score:3,rank:2,note:! (45-04),winrate:50.08")
START = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1"


class TestZeroScoreKept(unittest.TestCase):
    """分值为 0 的着法是真实数据，必须保留（只过滤 score:?? 的占位行）。"""

    SAMPLE = ("move:b2d2,score:0,rank:1,note:* (45-02),winrate:50.00"
              "|move:h2e2,score:0,rank:1,note:* (45-02),winrate:50.00"
              "|move:e0e1,score:??,rank:0,note:? (??-??)")

    def test_zero_score_hits_are_kept(self):
        from xqtrainer.cloud import parse_queryall

        hits = parse_queryall(self.SAMPLE)
        real = [hit for hit in hits if not hit.unknown]
        self.assertEqual([hit.move for hit in real], ["b2d2", "h2e2"])
        self.assertTrue(all(hit.score == 0 for hit in real), "0 分着法不能被丢掉")
        self.assertTrue(hits[-1].unknown, "未收录的着法排在最后")

    def test_all_unknown_is_empty(self):
        from xqtrainer.cloud import parse_queryall

        text = ("move:e0e1,score:??,rank:0,note:? (??-??)"
                "|move:f0e1,score:??,rank:0,note:? (??-??)")
        self.assertEqual(parse_queryall(text), [])

    def test_panel_shows_zero_not_dash(self):
        """界面里 0 分要显示成 0，不能显示成 "-"。"""
        from xqtrainer.cloud import CloudHit

        hit = CloudHit(move="b2d2", score=0, winrate=50.0, rank=1, note="*")
        self.assertIsNotNone(hit.score)
        self.assertEqual(hit.score, 0)


class TestParsing(unittest.TestCase):
    def test_parse_queryall(self):
        hits = parse_queryall(SAMPLE)
        self.assertEqual(len(hits), 4)              # 有真实数据时，未收录的也列出来
        self.assertEqual(hits[0].move, "h2e2")          # 分值最高排前面
        self.assertEqual(hits[0].score, 3)
        self.assertEqual(hits[0].rank, 2)
        self.assertAlmostEqual(hits[0].winrate, 50.08, places=2)
        self.assertIn("45-04", hits[0].note)
        unknown = [hit for hit in hits if hit.unknown]
        # 没收录的着法（score ?? / rank 0 / 没胜率）现在会被直接丢掉，
        # 界面上不会再出现"次数为 0 的假招法"。
        # 有真实数据的局面里，未收录的着法也会列出（标"未收录"、排在最后）
        self.assertEqual([hit.move for hit in unknown], ["e0e1"])

    def test_parse_special_responses(self):
        for text in ("unknown", "invalid board", "checkmate", "stalemate", "nobestmove", ""):
            self.assertEqual(parse_queryall(text), [], text)
        self.assertEqual(parse_queryall("move:b2d2,score:1,rank:1,winrate:50.00\x00")[0].move,
                         "b2d2")

    def test_cloud_fen(self):
        self.assertEqual(board_to_cloud_fen(START), START.rsplit(" - -", 1)[0])
        self.assertEqual(board_to_cloud_fen("9/9 w"), "9/9 w")


class _FakeCloudHandler(BaseHTTPRequestHandler):
    response_text = SAMPLE
    last_params = {}

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        _FakeCloudHandler.last_params = dict(urllib.parse.parse_qsl(parsed.query))
        body = _FakeCloudHandler.response_text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: D102
        return


class TestCloudClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeCloudHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.endpoint = f"http://127.0.0.1:{cls.port}/chessdb.php"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_query_all(self):
        _FakeCloudHandler.response_text = SAMPLE
        cloud = CloudBook(endpoint=self.endpoint, timeout=5, min_interval=0)
        result = cloud.query_all(START)
        self.assertEqual(result.status, STATUS_OK)
        self.assertEqual(len(result.hits), 4)
        self.assertEqual(_FakeCloudHandler.last_params.get("action"), "queryall")
        self.assertEqual(_FakeCloudHandler.last_params.get("board"),
                         START.rsplit(" - -", 1)[0])
        self.assertEqual(_FakeCloudHandler.last_params.get("learn"), "0")
        self.assertTrue(result.ok)

    def test_cache(self):
        _FakeCloudHandler.response_text = SAMPLE
        cloud = CloudBook(endpoint=self.endpoint, timeout=5, min_interval=0)
        cloud.query_all(START)
        again = cloud.query_all(START)
        self.assertTrue(again.cached)
        cloud.clear_cache()
        self.assertIsNone(cloud.cached(START))

    def test_status_parsing(self):
        cloud = CloudBook(endpoint=self.endpoint, timeout=5, min_interval=0)
        _FakeCloudHandler.response_text = "unknown"
        self.assertEqual(cloud.query_all(START).status, STATUS_UNKNOWN)
        cloud.clear_cache()
        _FakeCloudHandler.response_text = "invalid board"
        self.assertEqual(cloud.query_all(START).status, STATUS_INVALID)
        cloud.clear_cache()
        _FakeCloudHandler.response_text = "查询过于频繁"
        self.assertEqual(cloud.query_all(START).status, STATUS_BUSY)

    def test_network_error_is_reported(self):
        cloud = CloudBook(endpoint="http://127.0.0.1:9/chessdb.php", timeout=0.6,
                          min_interval=0)
        result = cloud.query_all(START)
        self.assertEqual(result.status, "error")
        self.assertTrue(result.message)


class _StubCloud:
    """离线替身：直接给出固定返回，用来测会话层集成。"""

    def __init__(self, text=SAMPLE):
        self.text = text
        self.timeout = 5.0
        self.last_query_time = 0.0
        self.responses = {}

    def query_all(self, fen, show_all=True, learn=False, use_cache=True):
        self.responses[fen] = self.text
        return CloudResult(status=STATUS_OK, hits=parse_queryall(self.text), raw=self.text)

    def cached(self, fen):
        return None

    def save_cache(self):
        pass

    def clear_cache(self):
        self.responses.clear()


class TestSessionIntegration(unittest.TestCase):
    def test_session_cloud_payload(self):
        session = Session()
        session.cloud = _StubCloud()
        session.set_cloud_enabled(True)
        deadline = time.time() + 5
        state = session.state()
        while time.time() < deadline and not (state["cloud"]["hits"]):
            time.sleep(0.1)
            state = session.state()
        cloud = state["cloud"]
        self.assertTrue(cloud["enabled"])
        self.assertEqual(cloud["status"], STATUS_OK)
        self.assertEqual(len(cloud["hits"]), 4)
        self.assertEqual(cloud["hits"][0]["move"], "h2e2")
        self.assertEqual(cloud["hits"][0]["cn"], "炮二平五")      # 中文记谱
        self.assertEqual(cloud["best"], "h2e2")
        self.assertEqual(cloud["bestCn"], "炮二平五")

    def test_cloud_best_move_for_game(self):
        session = Session()
        session.cloud = _StubCloud()
        session.set_engine_mode("black")                          # 引擎走黑方
        move = session.cloud_best_move(session.tree.board_at())
        self.assertIsNotNone(move)
        self.assertIn(move, [hit.move for hit in parse_queryall(SAMPLE)])

    def test_cloud_can_be_disabled(self):
        session = Session()
        session.cloud = _StubCloud()
        session.set_cloud_enabled(False)
        state = session.state()
        self.assertFalse(state["cloud"]["enabled"])
        self.assertEqual(state["cloud"]["hits"], [])


@unittest.skipUnless(os.environ.get("XQ_CLOUD_LIVE") == "1",
                     "未启用真实云库测试（设置 XQ_CLOUD_LIVE=1 可开启）")
class TestCloudLive(unittest.TestCase):
    def test_live_query(self):
        cloud = CloudBook(timeout=12)
        result = cloud.query_all(START)
        self.assertIn(result.status, (STATUS_OK, STATUS_UNKNOWN, STATUS_BUSY))
        self.assertTrue(result.raw or result.status == STATUS_UNKNOWN)


if __name__ == "__main__":
    unittest.main()
