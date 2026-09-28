#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""静夜象棋界面启动脚本。

用法::

    python run.py                     # 启动界面（自动打开浏览器）
    python run.py --port 9000         # 指定端口
    python run.py --engine 路径/pikafish.exe   # 启动时自动加载引擎
    python run.py --no-browser        # 不自动打开浏览器

程序只用 Python 标准库，无需安装任何第三方依赖。
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from xqtrainer.server import AppServer  # noqa: E402
from xqtrainer.session import Session, web_root  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="静夜象棋界面：打谱 / 分析 / 人机对战")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    parser.add_argument("--engine", default="", help="启动时自动加载的引擎路径（皮卡鱼等）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--threads", type=int, default=0, help="引擎线程数")
    parser.add_argument("--hash", type=int, default=0, help="引擎哈希表大小（MB）")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    session = Session()
    if args.threads:
        session.settings["threads"] = args.threads
    if args.hash:
        session.settings["hash_mb"] = args.hash

    server = AppServer(session, web_root(), host=args.host, port=args.port)
    try:
        server.start()
    except OSError as exc:
        print(f"无法启动服务：{exc}")
        print(f"端口 {args.port} 可能已被占用，可用 --port 指定其它端口。")
        return 1

    print("=" * 56)
    print("  静夜象棋界面 1.6  Jingye Xiangqi")
    print(f"  界面地址：{server.url}")
    print("  关闭本窗口即可退出程序（Ctrl+C）")
    print("=" * 56)

    # 命令行指定 → 上次用户选的（设置记忆）→ exe 里打包的皮卡鱼 → 自动查找
    engine_path = session.startup_engine_path(args.engine)
    if engine_path:
        print(f"  自动加载引擎：{session.engine_label(engine_path)}")
    else:
        print("  未找到皮卡鱼引擎，可在界面右侧「引擎设置」中选择路径")
    if engine_path:
        threading.Thread(target=session.load_engine, args=(engine_path,), daemon=True).start()

    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(0.6), webbrowser.open(server.url)),
                         daemon=True).start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n正在退出…")
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
