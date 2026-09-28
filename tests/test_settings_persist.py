# -*- coding: utf-8 -*-
"""设置记忆：改过的设置要写进 settings.json，下次打开自动恢复。"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 用独立的配置文件测试，别动用户真实的 settings.json
import os  # noqa: E402

os.environ["XQ_SETTINGS_FILE"] = str(Path(tempfile.gettempdir()) /
                                     "xq_persist_test_settings.json")

from xqtrainer.session import Session, settings_file  # noqa: E402


class TestSettingsPersist(unittest.TestCase):
    def setUp(self):
        self.path = settings_file()
        self.backup = self.path.read_text(encoding="utf-8") if self.path.is_file() else None
        if self.path.is_file():
            self.path.unlink()

    def tearDown(self):
        if self.backup is None:
            self.path.unlink(missing_ok=True)
        else:
            self.path.write_text(self.backup, encoding="utf-8")

    def test_all_user_settings_round_trip(self):
        session = Session()
        wanted = {
            "movetime_ms": 2500, "threads": 90, "hash_mb": 512,
            "cloud_learn": False, "cloud_timeout": 6,
            "book_enabled": False, "book_strategy": "best_rate", "book_max_ply": 20,
            "board_theme": "night", "board_scale": 80, "board_skin": "C:\\skins\\large",
            "show_arrows": False, "show_numbers": False, "show_statusbar": False,
            "piece_shadow": False,
            "sound_enabled": False, "engine_path": "C:\\engine\\pikafish.exe",
        }
        session.update_settings(wanted)
        session.save_settings_file()
        self.assertTrue(self.path.is_file(), "配置文件应该被创建")

        reopened = Session()                      # 相当于关掉再重新打开软件
        state = reopened.state()["settings"]
        for key, value in wanted.items():
            self.assertEqual(state.get(key), value, f"{key} 没有恢复")
        self.assertFalse(reopened.cloud.learn)    # 开关要真正生效
        self.assertEqual(reopened.cloud.timeout, 6.0)
        self.assertFalse(reopened.book.use_in_game)
        self.assertEqual(reopened.book.strategy, "best_rate")
        self.assertEqual(reopened.book.max_ply, 20)

    def test_loaded_book_paths_are_remembered(self):
        from tests.test_book import TMP                      # 复用测试临时目录工具
        from xqtrainer.book import obk_hash, write_obk
        from xqtrainer.board import Board

        TMP.mkdir(exist_ok=True)
        path = TMP / "persist.obk"
        board = Board()
        board.push(board.find_move_iccs("h2e2"))
        write_obk(path, {(1, obk_hash(board)): 7})
        try:
            session = Session()
            self.assertTrue(session.load_book(str(path)))
            session.save_settings_file()
            self.assertIn(str(path), (Session().state()["settings"]["book_paths"] or []))
            reopened = Session()
            self.assertTrue(reopened.state()["book"]["loaded"], "上次的库要自动加载回来")
            self.assertTrue(reopened.book.lookup(Board(), limit=3))
            reopened.clear_book()
        finally:
            for item in TMP.glob("*"):
                item.unlink(missing_ok=True)
            try:
                TMP.rmdir()
            except OSError:
                pass

    def test_fresh_install_uses_defaults(self):
        """没有配置文件时（第一次使用）应该是默认值。"""
        session = Session()
        settings = session.state()["settings"]
        self.assertEqual(settings["cloud_learn"], True)      # 云库自动学习默认开
        self.assertEqual(settings["cloud_timeout"], 3.0)     # 超时默认 3 秒
        self.assertEqual(settings["multi_pv"], 1)            # 路线默认 1
        self.assertTrue(settings["book_enabled"])            # 启用库招默认开
        self.assertEqual(settings["book_strategy"], "best_score")
        self.assertEqual(settings["board_theme"], "wood")
        self.assertEqual(settings["board_scale"], 100)

    def test_reset_settings_restores_defaults(self):
        """恢复出厂设置：设置回默认、配置文件被清空重写、引擎与开局库存档清空。"""
        session = Session()
        session.update_settings({"threads": 90, "hash_mb": 512, "cloud_learn": False,
                                 "cloud_timeout": 9, "book_enabled": False,
                                 "book_strategy": "random", "board_theme": "night",
                                 "board_scale": 70, "board_skin": "C:\\skins\\large",
                                 "sound_enabled": False})
        session.save_settings_file()
        self.assertTrue(self.path.is_file())

        session.reset_settings(keep_engine=False)      # 测试里不自动找引擎
        settings = session.state()["settings"]
        self.assertTrue(settings["cloud_learn"])
        self.assertEqual(settings["cloud_timeout"], 3.0)
        self.assertTrue(settings["book_enabled"])
        self.assertEqual(settings["book_strategy"], "best_score")
        self.assertEqual(settings["board_theme"], "wood")
        self.assertEqual(settings["board_scale"], 100)
        # 出厂默认外观：皮肤＝秋分棋者、背景＝03-意式（1.3 起）
        self.assertEqual(settings["board_skin"], "0-秋分棋者")
        self.assertEqual(settings["background"], "03-意式")
        self.assertTrue(settings["sound_enabled"])
        from xqtrainer.session import DEFAULT_SETTINGS

        self.assertEqual(settings["threads"], DEFAULT_SETTINGS["threads"])
        self.assertEqual(settings["hash_mb"], 128)
        self.assertEqual(settings["engine_options_by_engine"], {})
        self.assertEqual(settings["engines"], [])
        self.assertFalse(session.state()["book"]["loaded"], "开局库也要卸载")
        # 文件被删掉后又写回了默认值 → 重启也是默认
        self.assertTrue(self.path.is_file())
        import json

        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["board_theme"],
                         "wood")


if __name__ == "__main__":
    unittest.main()
