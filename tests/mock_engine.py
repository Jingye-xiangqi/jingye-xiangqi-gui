#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一个极简 UCI 引擎，仅用于自动化测试（不能用来下棋）。

它实现了 UCI 握手、常用选项、``position`` / ``go`` / ``stop`` / ``quit``，
并用真实规则内核产生合法着法与 PV，因此可以完整测试界面与引擎的通讯流程。
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xqtrainer.board import Board, Move  # noqa: E402


class MockEngine:
    def __init__(self) -> None:
        self.board = Board()
        self.moves = []
        self.multipv = 1
        self.show_wdl = False
        self.stop_flag = threading.Event()
        self.running = True
        self.lock = threading.Lock()

    # ---------------------------------------------------------------- 命令
    def handle(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        if line == "uci":
            self.send("id name MockEngine 1.0")
            self.send("id author xqtrainer tests")
            self.send("option name Threads type spin default 1 min 1 max 64")
            self.send("option name Hash type spin default 16 min 1 max 4096")
            self.send("option name MultiPV type spin default 1 min 1 max 10")
            self.send("option name UCI_ShowWDL type check default false")
            self.send("option name Clear Hash type button")
            self.send("option name EvalFile type string default <empty>")
            self.send("option name Style type combo default Normal var Normal var Aggressive")
            self.send("uciok")
        elif line == "isready":
            self.send("readyok")
        elif line == "ucinewgame":
            pass
        elif line.startswith("setoption"):
            self.set_option(line)
        elif line.startswith("position"):
            self.set_position(line)
        elif line.startswith("go"):
            self.go(line)
        elif line == "stop":
            self.stop_flag.set()
        elif line == "quit":
            self.running = False

    def set_option(self, line: str) -> None:
        if "MultiPV" in line:
            try:
                self.multipv = int(line.rsplit("value", 1)[1].strip())
            except (IndexError, ValueError):
                pass
        if "UCI_ShowWDL" in line:
            self.show_wdl = "true" in line.lower()

    def set_position(self, line: str) -> None:
        rest = line[len("position"):].strip()
        if rest.startswith("startpos"):
            fen = None
            rest = rest[len("startpos"):].strip()
        else:
            rest = rest[len("fen"):].strip()
            parts = []
            while rest and not rest.startswith("moves"):
                token, _, rest = rest.partition(" ")
                parts.append(token)
                rest = rest.strip()
            fen = " ".join(parts[:4])
        self.board = Board(fen) if fen else Board()
        rest = rest[len("moves"):].strip() if rest.startswith("moves") else ""
        self.moves = rest.split() if rest else []
        for text in self.moves:
            if self.board.move_iccs(text) is None:
                break

    def go(self, line: str) -> None:
        tokens = line.split()
        depth_limit = 0
        movetime = 0.0
        infinite = "infinite" in tokens
        if "depth" in tokens:
            try:
                depth_limit = int(tokens[tokens.index("depth") + 1])
            except (IndexError, ValueError):
                depth_limit = 0
        if "movetime" in tokens:
            try:
                movetime = int(tokens[tokens.index("movetime") + 1]) / 1000.0
            except (IndexError, ValueError):
                movetime = 0.0
        self.stop_flag.clear()
        thread = threading.Thread(target=self.search, args=(depth_limit, movetime, infinite),
                                  daemon=True)
        thread.start()

    def search(self, depth_limit: int, movetime: float, infinite: bool) -> None:
        with self.lock:
            board = self.board.copy()
            moves = board.legal_moves()
        if not moves:
            self.send("bestmove (none)")
            return
        pv = [move.iccs() for move in moves[:5]]
        deadline = time.time() + movetime if movetime else 0
        depth = 0
        while self.running:
            if not infinite and depth_limit and depth >= depth_limit:
                break
            if not infinite and deadline and time.time() >= deadline:
                break
            if self.stop_flag.is_set():
                break
            depth += 1
            for index in range(min(self.multipv, len(moves))):
                best = moves[index]
                tail = [move.iccs() for move in moves[:5]]
                score = 12 * depth - index * 15
                extra = ""
                if self.show_wdl:
                    win = max(0, min(1000, 500 + score * 8))
                    loss = max(0, min(1000 - win, 400 - score * 6))
                    extra = " wdl %d %d %d" % (win, max(0, 1000 - win - loss), loss)
                self.send(
                    "info depth %d seldepth %d multipv %d score cp %d nodes %d nps %d "
                    "time %d%s pv %s" % (depth, depth + 1, index + 1, score, depth * 137,
                                         137000, depth * 3, extra,
                                         " ".join([best.iccs()] + tail)))
            time.sleep(0.01)
            if not infinite and not deadline and not depth_limit and depth >= 6:
                break
        self.send("bestmove " + pv[0] + " ponder " + (pv[1] if len(pv) > 1 else ""))

    def send(self, text: str) -> None:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()


def main() -> int:
    engine = MockEngine()
    for raw in sys.stdin:
        engine.handle(raw)
        if not engine.running:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
