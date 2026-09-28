# -*- coding: utf-8 -*-
"""端到端测试：HTTP 接口 + 会话 + 引擎（假引擎）。"""

import json
import sys
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_xqf import build_xqf  # noqa: E402  （测试夹具写入器）
from xqtrainer.board import START_FEN  # noqa: E402
from xqtrainer.game import GameTree  # noqa: E402
from xqtrainer.server import AppServer  # noqa: E402
from xqtrainer.session import Session  # noqa: E402

MOCK = Path(__file__).resolve().parent / "mock_engine.py"
WEB_ROOT = Path(__file__).resolve().parents[1] / "xqtrainer" / "web"


class TestServer(unittest.TestCase):
    def setUp(self):
        self.session = Session()
        self.server = AppServer(self.session, WEB_ROOT, port=0)
        self.server.start()
        self.base = self.server.url.rstrip("/")

    def tearDown(self):
        self.server.stop()

    # ------------------------------------------------------------ 工具
    def action(self, action, **payload):
        body = json.dumps(dict(action=action, **payload)).encode("utf-8")
        request = urllib.request.Request(self.base + "/api/action", data=body,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=20) as response:
            return response.read()

    # ------------------------------------------------------------ 用例
    def test_index_page(self):
        html = self.get("/").decode("utf-8")
        self.assertIn("静夜象棋界面", html)
        self.assertIn("app.js", html)
        css = self.get("/style.css").decode("utf-8")
        self.assertIn(".board", css)
        js = self.get("/app.js").decode("utf-8")
        self.assertIn("renderBoard", js)

    def test_state_and_moves(self):
        data = self.action("state")
        self.assertTrue(data["ok"])
        state = data["state"]
        self.assertEqual(state["side"], "w")
        self.assertEqual(len(state["legal"]), 16)          # 开局 16 个可动棋子
        self.assertEqual(sum(len(v) for v in state["legal"].values()), 44)

        moved = self.action("move", **{"from": "h2", "to": "e2"})
        self.assertTrue(moved["ok"])
        state = moved["state"]
        self.assertEqual(state["side"], "b")
        self.assertEqual(state["moves"][-1]["cn"], "炮二平五")
        self.assertEqual(state["lastMove"]["iccs"], "h2e2")

        # 非法着法
        bad = self.action("move", **{"from": "a0", "to": "a5"})
        self.assertFalse(bad["ok"])

        back = self.action("nav", to="prev")
        self.assertEqual(back["state"]["ply"], 0)
        forward = self.action("nav", to="next")
        self.assertEqual(forward["state"]["moves"][0]["iccs"], "h2e2")

    def test_select_returns_destinations(self):
        self.action("select", square="h2")
        state = self.action("state")["state"]
        self.assertIn("e2", state["destinations"])
        # 炮二可走：向前 h3-h6、向后 h1、左右 c2-i2、以及隔炮打黑马 h9
        self.assertEqual(sorted(state["destinations"]),
                         ["c2", "d2", "e2", "f2", "g2", "h1", "h3", "h4", "h5", "h6", "h9", "i2"])

    def test_variation_flow(self):
        self.action("move", **{"from": "h2", "to": "e2"})
        self.action("move", **{"from": "h9", "to": "g7"})
        self.action("nav", to="prev")
        self.action("move", **{"from": "b9", "to": "c7"})
        tree = json.loads(self.get("/api/tree").decode("utf-8"))["tree"]
        self.assertEqual(tree["nodes"], 4)
        siblings = self.action("state")["state"]["siblings"]
        self.assertEqual(len(siblings), 2)
        self.assertEqual({item["iccs"] for item in siblings}, {"h9g7", "b9c7"})

    def test_import_xqf_and_export_pgn(self):
        tree = GameTree(meta={"Red": "甲", "Black": "乙"})
        tree.play_iccs("h2e2")
        tree.play_iccs("h9g7")
        tree.play_iccs("h0g2")
        import base64
        payload = base64.b64encode(build_xqf(tree)).decode("ascii")
        data = self.action("import", kind="xqf", name="test.xqf", encoding="base64", data=payload)
        self.assertTrue(data["ok"])
        state = data["state"]
        self.assertEqual(state["meta"]["Red"], "甲")
        self.assertEqual(state["ply"], 0)
        self.assertEqual(len(state["legal"]), 16)
        exported = self.action("export", style="iccs")
        self.assertTrue(exported["ok"])
        self.assertIn("h2e2", exported["text"])
        self.assertIn("h9g7", exported["text"])
        self.assertTrue(exported["filename"].endswith(".pgn"))

    def test_comment_and_delete(self):
        self.action("move", **{"from": "h2", "to": "e2"})
        self.action("move", **{"from": "h9", "to": "g7"})
        node_id = self.action("state")["state"]["current"]
        self.action("comment", id=node_id, text="这是评注")
        self.assertEqual(self.action("state")["state"]["comment"], "这是评注")
        self.action("delete", id=node_id)
        self.assertEqual(self.action("state")["state"]["ply"], 1)

    def test_set_fen_and_flip(self):
        fen = "3k5/9/9/9/9/9/9/9/9/4K4 w - - 0 1"
        data = self.action("set_fen", fen=fen)
        self.assertTrue(data["ok"])
        self.assertEqual(data["state"]["fen"].split()[0], fen.split()[0])
        bad = self.action("set_fen", fen="not a fen")
        self.assertFalse(bad["ok"])
        self.action("flip")
        self.assertTrue(self.action("state")["state"]["settings"]["flip"])
        self.action("new")
        self.assertEqual(self.action("state")["state"]["fen"], START_FEN)

    def test_settings_and_modes(self):
        data = self.action("settings", patch={"mode": "play", "human_side": "b", "difficulty": 3})
        state = data["state"]
        self.assertEqual(state["settings"]["mode"], "play")
        self.assertEqual(state["settings"]["human_side"], "b")
        self.assertEqual(state["settings"]["difficulty"], 3)

    def test_engine_analysis_with_mock(self):
        self.assertTrue(self.session.load_engine(sys.executable, args=[str(MOCK)]))
        # 用限时搜索（持续分析模式下引擎不会返回 bestmove，属于预期行为）
        self.session.update_settings({"infinite": False, "movetime_ms": 1000})
        self.action("move", **{"from": "h2", "to": "e2"})
        self.session.analyze_now()
        deadline = time.time() + 20
        lines = []
        while time.time() < deadline:
            state = self.action("state")["state"]
            lines = state["analysis"]["lines"]
            if lines:
                break
            time.sleep(0.2)
        self.assertTrue(lines, "没有得到分析结果")
        self.assertEqual(lines[0]["multipv"], 1)
        self.assertIn("scoreText", lines[0])
        self.assertTrue(lines[0]["pvCn"], "PV 没有中文记谱")
        deadline = time.time() + 20
        while time.time() < deadline and self.session.engine.last_bestmove is None:
            time.sleep(0.2)
        self.assertIsNotNone(self.session.engine.last_bestmove)

    def test_engine_vs_mode(self):
        self.assertTrue(self.session.load_engine(sys.executable, args=[str(MOCK)]))
        self.action("settings", patch={"mode": "play", "human_side": "w", "difficulty": 2})
        self.action("move", **{"from": "h2", "to": "e2"})
        deadline = time.time() + 20
        state = self.action("state")["state"]
        while time.time() < deadline and state["side"] != "w":
            time.sleep(0.2)
            state = self.action("state")["state"]
        self.assertEqual(state["side"], "w", "引擎没有应着")
        self.assertEqual(len(state["moves"]), 2)

    def test_multipv_lines_all_visible(self):
        """引擎同一深度连续发出的 1/2/3 条路线都必须显示出来（限频不能整批丢弃）。"""
        self.assertTrue(self.session.load_engine(sys.executable, args=[str(MOCK)]))
        self.action("settings", patch={"multi_pv": 3, "movetime_ms": 1500,
                                       "auto_analyze": True})
        self.session.analyze_now()
        deadline = time.time() + 20
        lines = []
        while time.time() < deadline:
            lines = self.action("state")["state"]["analysis"]["lines"]
            if len(lines) >= 3:
                break
            time.sleep(0.2)
        self.assertGreaterEqual(len(lines), 3,
                                f"只收到 {len(lines)} 条推荐路线：{[l['multipv'] for l in lines]}")
        self.assertEqual([line["multipv"] for line in lines][:3], [1, 2, 3])
        for line in lines:
            self.assertTrue(line["pvCn"], "推荐路线缺少中文记谱")

    def test_browse_listing(self):
        data = self.action("browse", path=str(Path(__file__).resolve().parent))
        self.assertTrue(data["ok"])
        listing = data["listing"]
        self.assertTrue(listing["path"])
        names = [entry["name"] for entry in listing["entries"]]
        # 只列出目录与可执行文件（引擎），不列普通源码文件
        self.assertNotIn("test_server.py", names)
        self.assertTrue(listing["parent"])

    def test_sse_stream(self):
        with urllib.request.urlopen(self.base + "/api/events", timeout=20) as response:
            self.assertEqual(response.headers.get("Content-Type"),
                             "text/event-stream; charset=utf-8")
            line = response.readline().decode("utf-8")
            self.assertTrue(line.startswith("data: "))
            payload = json.loads(line[len("data: "):])
            self.assertEqual(payload["type"], "state")
            self.assertIn("board", payload["state"])


if __name__ == "__main__":
    unittest.main()
