#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""静夜象棋界面 —— 桌面版入口（打包成 exe 后双击运行的就是这个程序）。

双击后直接弹出一个独立的象棋窗口（原生 Tkinter 界面），**不需要浏览器**：

* 自动在常见位置查找象棋引擎（皮卡鱼 pikafish.exe），找到就自动加载；
* 棋盘、棋谱、分析、引擎、局面四个面板都在同一个窗口里；
* 关闭窗口即退出程序（引擎进程也会一起结束）。

命令行参数（便于脚本调用或排查问题）::

    desktop_app.exe --engine D:\\Pikafish\\pikafish.exe   # 指定引擎
    desktop_app.exe --threads 8 --hash 512                # 引擎资源
    desktop_app.exe --web                                 # 改用浏览器界面（旧的备用方式）
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APP_NAME = "静夜象棋界面1.6"
APP_SUBTITLE = "打谱 · 分析 · 人机对战"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} —— {APP_SUBTITLE}")
    parser.add_argument("--engine", default="", help="启动时自动加载的引擎路径")
    parser.add_argument("--threads", type=int, default=0, help="引擎线程数")
    parser.add_argument("--hash", type=int, default=0, help="引擎哈希表大小（MB）")
    parser.add_argument("--web", action="store_true",
                        help="改用浏览器界面（默认是原生桌面窗口）")
    parser.add_argument("--port", type=int, default=8765, help="--web 模式下的端口")
    parser.add_argument("--host", default="127.0.0.1", help="--web 模式下的监听地址")
    parser.add_argument("--no-browser", action="store_true", help="--web 模式下不自动开浏览器")
    return parser


def launch_native(args) -> int:
    """原生桌面窗口（默认方式）。"""
    from xqtrainer.session import Session
    from xqtrainer.ui import XiangqiWindow

    session = Session()
    if args.threads:
        session.settings["threads"] = args.threads
    if args.hash:
        session.settings["hash_mb"] = args.hash

    window = XiangqiWindow(session)
    # 启动时自动加载哪个引擎（顺序很重要）：
    #   ① 命令行 --engine 指定的；
    #   ② **用户上次手动选的**（设置记忆 —— 文件还在就继续用它）；
    #   ③ 打包在 exe 里的皮卡鱼（第一次打开就是它，直接读 sys._MEIPASS）；
    #   ④ 常见位置自动查找。
    # 引擎设置对话框里的「浏览…／加载引擎」照旧保留，用户随时可以换成别的引擎。
    engine_path = session.startup_engine_path(args.engine)
    if engine_path:
        threading.Thread(target=session.load_engine, args=(engine_path,), daemon=True).start()
    window.after(300, lambda: window._set_status(
        f"{APP_NAME} 就绪" + (f"　引擎：{session.engine_label(engine_path)}" if engine_path
                            else "　未找到引擎，可在「引擎设置」里选择")))
    window.mainloop()
    return 0


def launch_web(args) -> int:
    """可选的浏览器界面（原来的方式，保留给习惯用浏览器的场景）。"""
    import socket
    import webbrowser

    from xqtrainer.server import AppServer
    from xqtrainer.session import Session, web_root

    def pick_port(preferred: int, host: str, tries: int = 12) -> int:
        for offset in range(tries):
            port = preferred + offset
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                try:
                    probe.bind((host, port))
                    return port
                except OSError:
                    continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((host, 0))
            return int(probe.getsockname()[1])

    session = Session()
    if args.threads:
        session.settings["threads"] = args.threads
    if args.hash:
        session.settings["hash_mb"] = args.hash
    port = pick_port(args.port, args.host)
    server = AppServer(session, web_root(), host=args.host, port=port)
    try:
        server.start()
    except OSError as exc:
        print(f"无法启动本地服务：{exc}")
        return 1

    engine_path = session.startup_engine_path(args.engine)
    if engine_path:
        threading.Thread(target=session.load_engine, args=(engine_path,), daemon=True).start()

    print("=" * 56)
    print(f"  {APP_NAME}（浏览器界面）")
    print(f"  地址：{server.url}")
    print("=" * 56)
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(0.4), webbrowser.open(server.url)),
                         daemon=True).start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.web:
        return launch_web(args)
    try:
        return launch_native(args)
    except Exception as exc:  # noqa: BLE001 - 没有图形环境时给出提示
        print(f"{APP_NAME} 启动失败：{exc}")
        print("如果系统缺少图形环境，可以加 --web 参数改用浏览器界面。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
