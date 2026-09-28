# -*- coding: utf-8 -*-
"""UCI 引擎封装（皮卡鱼 Pikafish / 象眼 / 其它 UCI 中国象棋引擎）。

引擎以子进程方式运行，本模块负责：

* 启动进程、发送 ``uci`` 握手、读取引擎声明的全部选项（界面据此自动生成设置项）；
* 把 ``go`` 的搜索过程（``info`` 行）解析成结构化数据，供界面做思考过程可视化；
* 处理 ``bestmove``、``stop``、``setoption``、``position`` 等命令，并做超时与容错。

所有事件通过 ``on_event`` 回调抛出（在读取线程中执行），事件类型见 ``EVENT_*``。
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

EVENT_STATUS = "status"
EVENT_OPTIONS = "options"
EVENT_INFO = "info"
EVENT_BESTMOVE = "bestmove"
EVENT_LOG = "log"

STATUS_LOADING = "loading"
STATUS_READY = "ready"
STATUS_THINKING = "thinking"
STATUS_ERROR = "error"
STATUS_STOPPED = "stopped"


class EngineError(Exception):
    """引擎启动或通讯失败。"""


class EngineOption:
    """引擎声明的一个 UCI 选项。"""

    def __init__(self, name: str, kind: str, default: str = "", minimum: Optional[int] = None,
                 maximum: Optional[int] = None, values: Optional[List[str]] = None):
        self.name = name
        self.type = kind
        self.default = default
        self.min = minimum
        self.max = maximum
        self.values = values or []

    def to_dict(self) -> Dict[str, object]:
        return {"name": self.name, "type": self.type, "default": self.default,
                "min": self.min, "max": self.max, "values": self.values}


def _parse_option(line: str) -> Optional[EngineOption]:
    """解析 ``option name X type spin default 1 min 1 max 1024``。"""
    if not line.startswith("option name "):
        return None
    rest = line[len("option name "):]
    if " type " not in rest:
        return None
    name, rest2 = rest.split(" type ", 1)
    parts = rest2.split()
    if not parts:
        return None
    kind = parts[0]
    default = ""
    minimum = maximum = None
    values: List[str] = []
    index = 1
    while index < len(parts):
        token = parts[index]
        if token == "default" and index + 1 < len(parts):
            default = parts[index + 1]
            index += 2
        elif token == "min" and index + 1 < len(parts):
            try:
                minimum = int(parts[index + 1])
            except ValueError:
                minimum = None
            index += 2
        elif token == "max" and index + 1 < len(parts):
            try:
                maximum = int(parts[index + 1])
            except ValueError:
                maximum = None
            index += 2
        elif token in ("var", "value") and index + 1 < len(parts):
            values.append(parts[index + 1])
            index += 2
        else:
            index += 1
    return EngineOption(name, kind, default, minimum, maximum, values)


def parse_info(line: str) -> Dict[str, object]:
    """把一行 ``info ...`` 解析成字典（只保留界面需要的字段）。"""
    tokens = line.split()
    if tokens and tokens[0] == "info":
        tokens = tokens[1:]
    data: Dict[str, object] = {}
    pv: List[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "pv":
            pv = tokens[index + 1:]
            break
        if token == "score" and index + 2 < len(tokens):
            kind = tokens[index + 1]
            value = tokens[index + 2]
            score: Dict[str, object] = {}
            try:
                score[kind] = int(value)
            except ValueError:
                score[kind] = 0
            index += 3
            while index < len(tokens) and tokens[index] in ("lowerbound", "upperbound"):
                score[tokens[index]] = True
                index += 1
            data["score"] = score
            continue
        if token == "wdl" and index + 3 < len(tokens):
            try:
                data["wdl"] = [int(tokens[index + 1]), int(tokens[index + 2]),
                               int(tokens[index + 3])]
            except ValueError:
                pass
            index += 4
            continue
        if token in ("depth", "seldepth", "multipv", "nodes", "nps", "hashfull", "tbhits",
                     "time", "currmovenumber"):
            if index + 1 < len(tokens):
                try:
                    data[token] = int(tokens[index + 1])
                except ValueError:
                    pass
            index += 2
            continue
        if token == "currmove" and index + 1 < len(tokens):
            data["currmove"] = tokens[index + 1]
            index += 2
            continue
        if token == "string":
            data["string"] = " ".join(tokens[index + 1:])
            break
        index += 1
    if pv:
        data["pv"] = pv
    return data


class UciEngine:
    """一个 UCI 引擎进程。"""

    def __init__(self, on_event: Optional[Callable[[Dict[str, object]], None]] = None,
                 log_lines: bool = False):
        self.on_event = on_event
        #: 是否把引擎的每一行原始输出都抛给界面（高频信息，默认关闭）
        self.log_lines = log_lines
        self.process: Optional[subprocess.Popen] = None
        self.path = ""
        self.name = ""
        self.author = ""
        self.options: List[EngineOption] = []
        #: 当前已下发的选项值（键为选项名），供界面回显
        self.current_options: Dict[str, str] = {}
        self.state = STATUS_STOPPED
        self.last_error = ""
        self.searching = False
        self.last_info: Dict[int, Dict[str, object]] = {}
        self.last_bestmove: Optional[str] = None
        self._lock = threading.RLock()
        self._reader: Optional[threading.Thread] = None
        self._uciok = threading.Event()
        self._readyok = threading.Event()
        self._quit = False
        self.search_id = 0
        self._current_search: Optional[Dict[str, object]] = None
        self._discard_bestmoves = 0
        #: 串行化 analyze/stop 这类命令序列（读取线程不参与，避免互相等待）
        self._command_lock = threading.RLock()
        self._parsed_depth = -1
        self._parsed_in_depth = 0
        self._parsed_multipv = -1
        self._last_info_time = 0.0
        self._last_output = time.time()
        self._search_info_count = 0
        self._monitor: Optional[threading.Thread] = None

    # ------------------------------------------------------------ 事件
    def _emit(self, event: Dict[str, object]) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(event)
        except Exception:  # noqa: BLE001 - 界面回调异常不应影响引擎线程
            pass

    def _set_state(self, state: str, detail: str = "") -> None:
        self.state = state
        if state == STATUS_ERROR:
            self.last_error = detail
        self._emit({"type": EVENT_STATUS, "state": state, "detail": detail, "path": self.path})

    # ------------------------------------------------------------ 启动 / 关闭
    def start(self, path: str, options: Optional[Dict[str, object]] = None,
              timeout: float = 40.0, args: Optional[List[str]] = None) -> None:
        """启动引擎并完成 UCI 握手；``options`` 中的选项会在握手后立即下发。

        ``args`` 用于那些需要额外命令行参数的引擎（例如脚本包装、Java 引擎）。
        """
        self.stop_process()
        exe = Path(path).expanduser()
        if not exe.exists():
            raise EngineError(f"找不到引擎文件：{exe}")
        if exe.is_dir():
            raise EngineError(f"请选择引擎可执行文件，而不是目录：{exe}")
        self.path = str(exe)
        self.name = exe.name
        self.author = ""
        self.options = []
        self.current_options = {}
        self.last_info = {}
        self._current_search = None
        self._discard_bestmoves = 0
        self.searching = False
        self._uciok.clear()
        self._readyok.clear()
        self._quit = False
        self._set_state(STATUS_LOADING, f"正在启动 {exe.name}")

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            command = [str(exe)] + [str(item) for item in (args or [])]
            self.process = subprocess.Popen(  # noqa: S603 - 用户显式选择的引擎路径
                command,
                cwd=str(exe.parent),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            self._set_state(STATUS_ERROR, f"无法启动引擎：{exc}")
            raise EngineError(f"无法启动引擎：{exc}") from exc

        self._reader = threading.Thread(target=self._read_loop, name="uci-reader", daemon=True)
        self._reader.start()
        self._monitor = threading.Thread(target=self._monitor_loop, name="uci-monitor", daemon=True)
        self._monitor.start()
        self.send("uci")
        if not self._uciok.wait(timeout):
            self._set_state(STATUS_ERROR, "引擎未响应 uci 命令（可能是文件损坏或缺少 NNUE 权重）")
            raise EngineError("引擎未响应 uci 命令（超时）")
        if options:
            for name, value in options.items():
                self.set_option(name, value)
        self._readyok.clear()
        self.send("isready")
        if not self._readyok.wait(timeout):
            self._set_state(STATUS_ERROR, "引擎未响应 isready 命令（超时）")
            raise EngineError("引擎未响应 isready 命令（超时）")
        self._set_state(STATUS_READY, f"{self.name} 已就绪")

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop_process(self) -> None:
        """发送 quit 并等待进程退出。"""
        process = self.process
        if process is None:
            return
        self._quit = True
        try:
            if process.poll() is None:
                self.send("quit")
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
        except Exception:  # noqa: BLE001
            try:
                process.kill()
            except Exception:  # noqa: BLE001
                pass
        self.process = None
        self.searching = False
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        self._set_state(STATUS_STOPPED, "引擎已停止")

    # ------------------------------------------------------------ 命令
    def send(self, command: str) -> None:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            return
        try:
            process.stdin.write(command + "\n")
            process.stdin.flush()
        except (OSError, ValueError) as exc:
            self._set_state(STATUS_ERROR, f"向引擎发送命令失败：{exc}")

    def set_option(self, name: str, value: object) -> None:
        meta = self.option(name)
        if meta is not None and meta.type == "check":
            text = "true" if str(value).lower() in ("true", "1", "on", "yes") else "false"
        elif meta is not None and meta.type == "spin":
            try:
                number = int(float(str(value)))
            except (TypeError, ValueError):
                number = int(meta.default or 0)
            if meta.min is not None:
                number = max(meta.min, number)
            if meta.max is not None:
                number = min(meta.max, number)
            text = str(number)
        else:
            text = str(value)
        if text == "":
            self.send(f"setoption name {name}")
        else:
            self.send(f"setoption name {name} value {text}")
        self.current_options[name] = text

    def option_value(self, name: str) -> str:
        """返回该选项当前已下发的值（未设置过则回退到引擎声明的默认值）。"""
        if name in self.current_options:
            return str(self.current_options[name])
        meta = self.option(name)
        return str(meta.default) if meta is not None else ""

    def probe_options(self, timeout: float = 15.0) -> List[EngineOption]:
        """重新向引擎询问 uci，刷新选项列表（不同引擎支持的选项不一样）。"""
        if not self.is_running():
            return self.options
        self._uciok.clear()
        self.send("uci")
        self._uciok.wait(timeout)
        return self.options

    def option(self, name: str) -> Optional[EngineOption]:
        for option in self.options:
            if option.name.lower() == name.lower():
                return option
        return None

    def new_game(self) -> None:
        self.send("ucinewgame")
        self.clear_hash()
        self._readyok.clear()
        self.send("isready")
        self._readyok.wait(10)

    def clear_hash(self) -> None:
        if self.option("Clear Hash") is not None:
            self.send("setoption name Clear Hash")

    def set_position(self, fen: str, moves: Optional[List[str]] = None) -> None:
        command = f"position fen {self._normalize_fen(fen)}"
        if moves:
            command += " moves " + " ".join(moves)
        self.send(command)

    @staticmethod
    def _normalize_fen(fen: str) -> str:
        """引擎只关心前 4 段，这里保证 FEN 合法且带先后手。"""
        parts = fen.split()
        if len(parts) < 2:
            parts = parts + ["w"]
        base = " ".join(parts[:4])
        if len(parts) < 4:
            base += " - -"
        return base

    def analyze(self, fen: str, moves: Optional[List[str]] = None, limit: str = "infinite",
                multipv: Optional[int] = None,
                on_start: Optional[Callable[[int], None]] = None) -> int:
        """开始搜索。``limit`` 形如 ``infinite`` / ``movetime 1000`` / ``depth 12`` / ``nodes 50000``。

        ``on_start`` 会在发送 ``go`` 之前、拿到搜索编号后立刻被调用，
        便于调用方在引擎可能返回结果之前登记本次搜索（避免竞态）。
        """
        with self._command_lock:
            if not self.is_running():
                raise EngineError("引擎未运行，请先加载引擎")
            if self._current_search is not None:
                self._cancel_current()
                self._drain_current_search()
            if multipv is not None:
                self.set_option("MultiPV", multipv)
            self.last_info = {}
            self.last_bestmove = None
            self.search_id += 1
            search_id = self.search_id
            self._search_info_count = 0
            self.set_position(fen, moves)
            self._current_search = {"id": search_id, "cancelled": False}
            self.searching = True
            if on_start is not None:
                on_start(search_id)
            self._set_state(STATUS_THINKING, "思考中")
            self.send(f"go {limit}".strip())
            return search_id

    def stop(self) -> None:
        with self._command_lock:
            self._cancel_current()

    def _cancel_current(self) -> None:
        """中止当前搜索：被中止的搜索仍会返回 bestmove，需要标记为作废。"""
        current = self._current_search
        if current is not None and not current.get("cancelled"):
            current["cancelled"] = True
            self.send("stop")

    def _drain_current_search(self, timeout: float = 2.0) -> None:
        """等待被中止的搜索返回它的 bestmove。"""
        deadline = time.time() + timeout
        while self._current_search is not None and self.is_running() and time.time() < deadline:
            time.sleep(0.005)
        if self._current_search is not None:
            # 引擎没有响应 stop（异常引擎），后续多出来的 bestmove 一律丢弃
            self._discard_bestmoves += 1
            self._current_search = None
            self.searching = False

    def ponderhit(self) -> None:
        self.send("ponderhit")

    # ------------------------------------------------------------ 读取线程
    def _read_loop(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        try:
            for raw in process.stdout:
                line = raw.strip()
                if not line:
                    continue
                self._handle_line(line)
                if self._quit:
                    break
        except Exception as exc:  # noqa: BLE001 - 读取失败时给出状态
            self._set_state(STATUS_ERROR, f"读取引擎输出失败：{exc}")
        finally:
            if not self._quit:
                self.searching = False
                self._set_state(STATUS_STOPPED, "引擎进程已退出")

    def _handle_line(self, line: str) -> None:
        self._last_output = time.time()
        if self.log_lines:
            self._emit({"type": EVENT_LOG, "line": line})
        if line.startswith("info string") and (
                "CRITICAL ERROR" in line or "Unsupported position" in line):
            # 例如局面不合法时皮卡鱼会拒绝搜索，此时必须中止本次搜索，否则界面会一直"思考中"
            detail = line.split("info string", 1)[1].strip()
            self.last_error = detail
            with self._command_lock:
                if self._current_search is not None:
                    self._current_search["cancelled"] = True
                    self.send("stop")
            self._set_state(STATUS_ERROR, f"引擎拒绝该局面：{detail}")
            return
        if line.startswith("id name "):
            self.name = line[len("id name "):].strip()
            return
        if line.startswith("id author "):
            self.author = line[len("id author "):].strip()
            return
        if line.startswith("option name "):
            option = _parse_option(line)
            if option is not None:
                self.options = [o for o in self.options if o.name != option.name] + [option]
            return
        if line == "uciok":
            self.options.sort(key=lambda item: item.name.lower())
            for option in self.options:
                self.current_options.setdefault(option.name, str(option.default))
            self._uciok.set()
            self._emit({"type": EVENT_OPTIONS,
                        "options": [option.to_dict() for option in self.options],
                        "name": self.name, "author": self.author, "path": self.path})
            return
        if line == "readyok":
            self._readyok.set()
            if not self.searching:
                self._set_state(STATUS_READY, f"{self.name} 已就绪")
            return
        if line.startswith("info "):
            # 高频保护：引擎在深搜时会不停推送同一深度的 info 行。
            # 这里保证每个 multipv 分支都能被解析到（否则界面上只会剩第 1 条推荐），
            # 同时把同一分支的重复行限频丢弃，避免拖慢界面线程。
            head = line[:96].split()
            depth = -1
            if len(head) > 2 and head[1] == "depth":
                try:
                    depth = int(head[2])
                except ValueError:
                    depth = -1
            multipv = -1
            if len(head) > 6 and head[5] == "multipv":
                try:
                    multipv = int(head[6])
                except ValueError:
                    multipv = -1
            now = time.time()
            if depth != self._parsed_depth:
                self._parsed_depth = depth
                self._parsed_in_depth = 0
                self._parsed_multipv = -1
            if multipv == self._parsed_multipv:
                if self._parsed_in_depth >= 4 or now - self._last_info_time < 0.05:
                    return
            self._parsed_in_depth += 1
            self._parsed_multipv = multipv
            self._last_info_time = now
            data = parse_info(line)
            if data:
                index = int(data.get("multipv", 1) or 1)  # type: ignore[arg-type]
                self.last_info[index] = data
                self._search_info_count += 1
                data["type"] = EVENT_INFO
                data["multipv"] = index
                data["engine_time"] = time.time()
                data["search_id"] = self.search_id
                self._emit(data)
            return

        if line.startswith("bestmove"):
            tokens = line.split()
            move = tokens[1] if len(tokens) > 1 else ""
            ponder = tokens[3] if len(tokens) > 3 and tokens[2] == "ponder" else ""
            current = self._current_search
            if self._discard_bestmoves > 0:
                # 上一轮 stop 后残留的 bestmove
                self._discard_bestmoves -= 1
                cancelled = True
                search_id = int(current["id"]) if current else self.search_id  # type: ignore[arg-type]
            else:
                self._current_search = None
                self.searching = False
                cancelled = bool(current and current.get("cancelled"))
                search_id = int(current["id"]) if current else self.search_id  # type: ignore[arg-type]
                if not cancelled:
                    self.last_bestmove = move
            self._set_state(STATUS_READY, "搜索结束")
            self._emit({"type": EVENT_BESTMOVE, "move": move, "ponder": ponder,
                        "search_id": search_id, "cancelled": cancelled})
            return

    def _monitor_loop(self) -> None:
        """看门狗：搜索开始后长时间没有任何输出（例如引擎卡死）就中止并报错。"""
        while not self._quit:
            time.sleep(1.0)
            if not self.is_running():
                if self._quit:
                    return
                continue
            with self._command_lock:
                if self._current_search is None or self._search_info_count > 0:
                    continue
                if time.time() - self._last_output < 15.0:
                    continue
                self.send("stop")
                self._current_search = None
                self.searching = False
            self._set_state(STATUS_ERROR, "引擎 15 秒没有任何输出，已中止搜索（可尝试重新加载引擎）")

    # ------------------------------------------------------------ 组合命令
    def apply_default_options(self, threads: Optional[int] = None,
                              hash_mb: Optional[int] = None,
                              multi_pv: Optional[int] = None,
                              show_wdl: Optional[bool] = None,
                              eval_file: Optional[str] = None) -> None:
        """下发常用选项；引擎不支持的选项会被忽略。"""
        if threads and self.option("Threads"):
            self.set_option("Threads", threads)
        if hash_mb and self.option("Hash"):
            self.set_option("Hash", hash_mb)
        if multi_pv and self.option("MultiPV"):
            self.set_option("MultiPV", multi_pv)
        if show_wdl is not None and self.option("UCI_ShowWDL"):
            self.set_option("UCI_ShowWDL", show_wdl)
        if eval_file and self.option("EvalFile"):
            self.set_option("EvalFile", eval_file)

    def wait_ready(self, timeout: float = 15.0) -> bool:
        self._readyok.clear()
        self.send("isready")
        return self._readyok.wait(timeout)

    def signature(self) -> str:
        return f"{self.name} {self.author}".strip()
