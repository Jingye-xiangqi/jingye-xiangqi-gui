# -*- coding: utf-8 -*-
"""打包内置引擎：路径定位、默认加载、设置记忆。

PyInstaller onefile 每次运行都会把随 exe 打包的文件解包到**不同的临时目录**
（``sys._MEIPASS``），所以这里用两个假的解包目录来模拟"打开两次 exe"：

* 第一次打开：没有配置文件 → 自动加载内置皮卡鱼；
* 第二次打开（新的临时目录）→ 还是内置皮卡鱼，而且它的 UCI 选项没丢；
* 用户自己换成别的引擎 → 下次打开优先用用户选的那个（设置记忆）；
* 用户选的引擎文件不在了 → 回退到内置皮卡鱼。
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from xqtrainer.session import BUILTIN_ENGINE_PREFIX, Session  # noqa: E402

ENGINE_NAME = "pikafish-bmi2.exe"


class TestBundledEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xq_map_"))
        self.settings = self.tmp / "settings.json"
        self.settings.unlink(missing_ok=True)
        # 用独立配置文件，别动用户真实的 settings.json；跑完要还原环境变量
        self._env_before = os.environ.get("XQ_SETTINGS_FILE")
        os.environ["XQ_SETTINGS_FILE"] = str(self.settings)
        self.unpack_a = self._make_unpack("_MEIaaa")
        self.unpack_b = self._make_unpack("_MEIbbb")
        self._saved = (getattr(sys, "frozen", None), getattr(sys, "_MEIPASS", None),
                       sys.executable)
        sys.frozen = True                      # 假装是被打包后的 exe 在跑
        sys.executable = str(self.unpack_a / "app.exe")
        sys._MEIPASS = str(self.unpack_a)

    def tearDown(self):
        frozen, meipass, exe = self._saved
        for name, value in (("frozen", frozen), ("_MEIPASS", meipass)):
            if value is None:
                if hasattr(sys, name):
                    delattr(sys, name)
            else:
                setattr(sys, name, value)
        sys.executable = exe
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)
        self.settings.unlink(missing_ok=True)
        if self._env_before is None:
            os.environ.pop("XQ_SETTINGS_FILE", None)
        else:
            os.environ["XQ_SETTINGS_FILE"] = self._env_before

    def _make_unpack(self, name: str) -> Path:
        """造一个假的 _MEIPASS 目录（引擎文件用空文件占位，只验证路径逻辑）。"""
        root = self.tmp / name
        engine_dir = root / "engine"
        engine_dir.mkdir(parents=True, exist_ok=True)
        (engine_dir / ENGINE_NAME).write_bytes(b"MZ-fake")
        (root / "app.exe").write_bytes(b"MZ")
        return root

    def _engine(self, unpack: Path) -> str:
        return str(unpack / "engine" / ENGINE_NAME)

    def test_first_launch_picks_bundled_engine(self):
        session = Session()
        self.assertEqual(session.bundled_engine_path(), Path(self._engine(self.unpack_a)))
        self.assertEqual(session.autodetect_engine()[0], self._engine(self.unpack_a))
        self.assertEqual(session.startup_engine_path(), self._engine(self.unpack_a))
        self.assertTrue(session.is_bundled_engine_path(self._engine(self.unpack_a)))
        self.assertIn("内置", session.engine_label(self._engine(self.unpack_a)))

    def test_bundled_engine_is_remembered_with_stable_marker(self):
        session = Session()
        session.settings["engine_path"] = session.engine_tag(self._engine(self.unpack_a))
        session.settings["engine_options_by_engine"] = {
            session.engine_tag(self._engine(self.unpack_a)): {"Threads": 4}}
        session.save_settings_file()
        data = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(data["engine_path"], BUILTIN_ENGINE_PREFIX + ENGINE_NAME)

        sys._MEIPASS = str(self.unpack_b)                  # 换了个临时解包目录
        sys.executable = str(self.unpack_b / "app.exe")
        again = Session()
        self.assertEqual(again.startup_engine_path(), self._engine(self.unpack_b))
        self.assertEqual(again.settings["engine_options_by_engine"],
                         {BUILTIN_ENGINE_PREFIX + ENGINE_NAME: {"Threads": 4}})

    def test_user_engine_wins_and_falls_back_when_missing(self):
        other = self.tmp / "another-engine.exe"
        other.write_bytes(b"MZ")
        session = Session()
        session.settings["engine_path"] = str(other)
        session.save_settings_file()
        self.assertEqual(Session().startup_engine_path(), str(other))
        self.assertFalse(session.is_bundled_engine_path(str(other)))

        session.settings["engine_path"] = str(self.tmp / "nope" / "gone.exe")
        session.save_settings_file()
        self.assertEqual(Session().startup_engine_path(), self._engine(self.unpack_a))

    def test_engine_list_marks_builtin(self):
        session = Session()
        with_engine = Path(self._engine(self.unpack_a))
        session.remember_engine(str(with_engine), "Pikafish 2026-01-31")
        known = session.known_engines()
        self.assertEqual(len(known), 1)
        self.assertEqual(known[0]["path"], BUILTIN_ENGINE_PREFIX + ENGINE_NAME)
        self.assertTrue(known[0]["builtin"])
        self.assertEqual(known[0]["name"], "Pikafish 2026-01-31")
        self.assertTrue(session.remove_engine(BUILTIN_ENGINE_PREFIX + ENGINE_NAME))
        self.assertEqual(session.known_engines(), [])

    def test_external_engines_are_not_treated_as_builtin(self):
        other = self.tmp / "pikafish-bmi2.exe"      # 同名但不在解包目录里
        other.write_bytes(b"MZ")
        session = Session()
        self.assertFalse(session.is_bundled_engine_path(str(other)))
        self.assertEqual(session.engine_tag(str(other)), str(other))


if __name__ == "__main__":
    unittest.main()
