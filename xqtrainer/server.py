# -*- coding: utf-8 -*-
"""本地 HTTP + SSE 服务：把会话层的能力暴露给浏览器界面。

* ``GET  /``             界面首页
* ``GET  /api/state``    当前局面状态
* ``GET  /api/tree``     棋谱树
* ``GET  /api/events``   SSE 事件流（局面变化、引擎思考信息、提示信息）
* ``POST /api/action``   所有操作（走子、导航、导入导出、引擎设置……）

服务只监听本机地址，供本程序自带的界面使用。
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import queue
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

from .board import START_FEN
from .session import Session

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


class EventHub:
    """SSE 订阅中心。"""

    def __init__(self) -> None:
        self._queues: List[queue.Queue] = []
        self._lock = threading.Lock()
        self._last_state: Optional[Dict[str, object]] = None

    def subscribe(self) -> queue.Queue:
        channel: queue.Queue = queue.Queue(maxsize=256)
        with self._lock:
            self._queues.append(channel)
        return channel

    def unsubscribe(self, channel: queue.Queue) -> None:
        with self._lock:
            if channel in self._queues:
                self._queues.remove(channel)

    def publish(self, event: Dict[str, object]) -> None:
        with self._lock:
            channels = list(self._queues)
        for channel in channels:
            try:
                channel.put_nowait(event)
            except queue.Full:
                try:
                    channel.get_nowait()
                    channel.put_nowait(event)
                except queue.Empty:
                    pass

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._queues)


class AppServer:
    """把 Session 包装成一个本地 Web 服务。"""

    def __init__(self, session: Session, web_root: Path, host: str = "127.0.0.1",
                 port: int = 8765):
        self.session = session
        self.web_root = Path(web_root).resolve()
        self.host = host
        self.port = port
        self.hub = EventHub()
        self.session.broadcast = self.hub.publish
        self.httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        handler, httpd_class = self._make_handler()
        self.httpd = httpd_class((self.host, self.port), handler)
        self.port = int(self.httpd.server_address[1])
        self._thread = threading.Thread(target=self.httpd.serve_forever,
                                        name="http-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        self.session.engine.stop_process()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    # ------------------------------------------------------------ 请求处理
    def _make_handler(self):
        server = self

        class HttpServer(ThreadingHTTPServer):
            daemon_threads = True
            # Windows 上 SO_REUSEADDR 允许两个进程绑定同一端口，
            # 这里禁用，保证"端口被占用"能被正确报出来。
            allow_reuse_address = os.name != "nt"

            def handle_error(self, request, client_address) -> None:
                exc = sys.exc_info()[1]
                if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
                    return  # 浏览器关掉页面或刷新时的正常断连
                super().handle_error(request, client_address)

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "JingyeXiangqi/2.0"

            def log_message(self, fmt: str, *args) -> None:  # 静音默认日志
                return

            # ---------------- 基础响应
            def _send_json(self, payload: Dict[str, object], status: int = 200) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_body(self) -> Dict[str, object]:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    return {}
                raw = self.rfile.read(length)
                try:
                    return json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return {}

            # ---------------- 路由
            def do_GET(self) -> None:  # noqa: N802
                path = urlparse(self.path).path
                if path in ("/", "/index.html"):
                    return self._serve_static("index.html")
                if path == "/api/state":
                    return self._send_json({"ok": True, "state": server.session.state()})
                if path == "/api/tree":
                    return self._send_json({"ok": True, "tree": server.session.tree_payload()})
                if path == "/api/events":
                    return self._serve_events()
                if path.startswith("/api/"):
                    return self._send_json({"ok": False, "error": "未知接口"}, 404)
                return self._serve_static(path.lstrip("/"))

            def do_POST(self) -> None:  # noqa: N802
                path = urlparse(self.path).path
                if path != "/api/action":
                    return self._send_json({"ok": False, "error": "未知接口"}, 404)
                payload = self._read_body()
                try:
                    result = server.dispatch(str(payload.get("action", "")), payload)
                except Exception as exc:  # noqa: BLE001 - 统一转成界面提示
                    result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    try:
                        result["state"] = server.session.state()
                    except Exception:  # noqa: BLE001
                        pass
                return self._send_json(result)

            # ---------------- 静态文件
            def _serve_static(self, relative: str) -> None:
                target = (server.web_root / relative).resolve()
                if not str(target).startswith(str(server.web_root)) or not target.is_file():
                    return self._send_bytes("404 Not Found".encode(), "text/plain; charset=utf-8", 404)
                content_type = MIME_TYPES.get(target.suffix.lower()) or \
                    mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                return self._send_bytes(target.read_bytes(), content_type)

            # ---------------- SSE
            def _serve_events(self) -> None:
                channel = server.hub.subscribe()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-transform")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                try:
                    self._write_event({"type": "state", "state": server.session.state()})
                    while True:
                        try:
                            event = channel.get(timeout=15)
                        except queue.Empty:
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                            continue
                        self._write_event(event)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    server.hub.unsubscribe(channel)

            def _write_event(self, event: Dict[str, object]) -> None:
                data = json.dumps(event, ensure_ascii=False)
                chunk = f"data: {data}\n\n".encode("utf-8")
                self.wfile.write(chunk)
                self.wfile.flush()

        return Handler, HttpServer

    # ------------------------------------------------------------ 动作分发
    def dispatch(self, action: str, payload: Dict[str, object]) -> Dict[str, object]:
        session = self.session
        if action == "state":
            return {"ok": True, "state": session.state()}
        if action == "tree":
            return {"ok": True, "tree": session.tree_payload()}
        if action == "new":
            fen = str(payload.get("fen") or "") or START_FEN
            session.new_game(fen)
            return {"ok": True, "state": session.state()}
        if action == "set_fen":
            ok = session.set_fen(str(payload.get("fen") or ""))
            return {"ok": ok, "state": session.state()}
        if action == "move":
            ok = session.play_move(str(payload.get("from") or ""), str(payload.get("to") or ""))
            return {"ok": ok, "state": session.state()}
        if action == "select":
            session.select(str(payload.get("square") or ""))
            return {"ok": True, "state": session.state()}
        if action == "nav":
            session.navigate(str(payload.get("to") or "next"),
                             node_id=payload.get("id"),  # type: ignore[arg-type]
                             index=int(payload.get("index") or 0))
            return {"ok": True, "state": session.state()}
        if action == "delete":
            ok = session.delete_node(int(payload.get("id") or 0))
            return {"ok": ok, "state": session.state()}
        if action == "promote":
            ok = session.promote_node(int(payload.get("id") or 0))
            return {"ok": ok, "state": session.state()}
        if action == "comment":
            session.set_comment(payload.get("id"), str(payload.get("text") or ""))  # type: ignore[arg-type]
            return {"ok": True, "state": session.state()}
        if action == "flip":
            session.flip_board()
            return {"ok": True, "state": session.state()}
        if action == "import":
            kind = str(payload.get("kind") or "pgn")
            raw = payload.get("data") or ""
            if payload.get("encoding") == "base64":
                try:
                    data = base64.b64decode(str(raw))
                except Exception:  # noqa: BLE001
                    return {"ok": False, "error": "文件内容无法解码"}
            else:
                data = str(raw).encode("utf-8")
            ok = session.import_data(kind, data, name=str(payload.get("name") or ""),
                                     game_index=int(payload.get("gameIndex") or 0))
            return {"ok": ok, "state": session.state()}
        if action == "export":
            text = session.export_text(payload.get("style") if payload.get("style") else None)
            return {"ok": True, "text": text,
                    "filename": getattr(session, "export_filename", "棋谱.pgn")}
        if action == "engine_load":
            ok = session.load_engine(str(payload.get("path") or ""))
            return {"ok": ok, "state": session.state()}
        if action == "engine_unload":
            session.unload_engine()
            return {"ok": True, "state": session.state()}
        if action == "engine_option":
            ok = session.set_engine_option(str(payload.get("name") or ""), payload.get("value"))
            return {"ok": ok, "state": session.state()}
        if action == "engine_autodetect":
            return {"ok": True, "paths": session.autodetect_engine()}
        if action == "engine_move":
            session.engine_move_now()
            return {"ok": True, "state": session.state()}
        if action == "browse":
            return {"ok": True, "listing": session.browse(str(payload.get("path") or ""))}
        if action == "settings":
            session.update_settings(dict(payload.get("patch") or {}))
            return {"ok": True, "state": session.state()}
        if action == "analyze":
            if str(payload.get("mode") or "start") == "stop":
                session.stop_analysis()
            else:
                session.analyze_now()
            return {"ok": True, "state": session.state()}
        return {"ok": False, "error": f"未知操作：{action}"}
