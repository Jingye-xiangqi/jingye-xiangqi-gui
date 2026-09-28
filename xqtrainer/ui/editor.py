# -*- coding: utf-8 -*-
"""局面编辑器：在草稿棋盘上自由摆放棋子，再生成 FEN 开始对局。

草稿用 FEN 行序保存（row 0 是黑方底线那一行），可以直接交给棋盘控件显示，
因此编辑过程中允许"暂时不合法"（例如还没摆将帅），只在应用时校验。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..board import FILES, START_FEN, Board

PALETTE_RED = ("K", "A", "B", "N", "R", "C", "P")
PALETTE_BLACK = ("k", "a", "b", "n", "r", "c", "p")


class PositionEditor:
    """局面草稿。"""

    def __init__(self) -> None:
        self.grid: List[List[str]] = [["." for _ in range(9)] for _ in range(10)]
        self.side = "w"
        self.piece = "K"          # 当前画笔；"." 表示橡皮擦
        self.revision = 0

    # ------------------------------------------------------------ 基本操作
    def touch(self) -> None:
        self.revision += 1

    def clear(self) -> None:
        self.grid = [["." for _ in range(9)] for _ in range(10)]
        self.touch()

    def set_start(self) -> None:
        board = Board(START_FEN)
        self.grid = [[board.cells[9 - row][file] for file in range(9)] for row in range(10)]
        self.side = "w"
        self.touch()

    def select(self, piece: str) -> None:
        self.piece = piece or "."

    # ------------------------------------------------------------ 摆子
    @staticmethod
    def _pos(square: str) -> Optional[tuple]:
        if not square or len(square) != 2 or square[0] not in FILES:
            return None
        if not square[1].isdigit() or not (0 <= int(square[1]) <= 9):
            return None
        return FILES.index(square[0]), int(square[1])

    def click_square(self, square: str) -> None:
        """点击棋盘：放子 / 擦除 / 同名棋子则移除。"""
        pos = self._pos(square)
        if pos is None:
            return
        file, rank = pos
        row = 9 - rank
        current = self.grid[row][file]
        if self.piece == ".":
            self.grid[row][file] = "."
        elif current == self.piece:
            self.grid[row][file] = "."          # 再点一次取消
        else:
            self.grid[row][file] = self.piece
        self.touch()

    def erase(self, square: str) -> None:
        pos = self._pos(square)
        if pos is None:
            return
        file, rank = pos
        self.grid[9 - rank][file] = "."
        self.touch()

    def swap_colors(self) -> None:
        for row in range(10):
            for file in range(9):
                piece = self.grid[row][file]
                if piece != ".":
                    self.grid[row][file] = piece.lower() if piece.isupper() else piece.upper()
        self.side = "b" if self.side == "w" else "w"
        self.touch()

    def set_side(self, side: str) -> None:
        self.side = "b" if str(side).lower().startswith("b") else "w"
        self.touch()

    # ------------------------------------------------------------ FEN
    def board_rows(self) -> List[str]:
        rows = []
        for row in self.grid:
            empty = 0
            text: List[str] = []
            for piece in row:
                if piece == ".":
                    empty += 1
                else:
                    if empty:
                        text.append(str(empty))
                        empty = 0
                    text.append(piece)
            if empty:
                text.append(str(empty))
            rows.append("".join(text))
        return rows

    def to_fen(self) -> str:
        return "/".join(self.board_rows()) + f" {self.side} - - 0 1"

    def load_fen(self, fen: str) -> bool:
        try:
            board = Board(fen)
        except ValueError:
            return False
        self.grid = [[board.cells[9 - row][file] for file in range(9)] for row in range(10)]
        self.side = "b" if board.side == "b" else "w"
        self.touch()
        return True

    def counts(self) -> Dict[str, int]:
        counter: Dict[str, int] = {}
        for row in self.grid:
            for piece in row:
                if piece != ".":
                    counter[piece] = counter.get(piece, 0) + 1
        return counter

    def validate(self) -> List[str]:
        """返回问题列表（空表示可以开始对局）。"""
        problems: List[str] = []
        counts = self.counts()
        if counts.get("K", 0) != 1:
            problems.append(f"红方要有且只有 1 个帅（当前 {counts.get('K', 0)} 个）")
        if counts.get("k", 0) != 1:
            problems.append(f"黑方要有且只有 1 个将（当前 {counts.get('k', 0)} 个）")
        if problems:
            return problems
        try:
            board = Board(self.to_fen())
        except ValueError as exc:
            return [str(exc)]
        return board.check_legal_position()

    def state_for_view(self, settings: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        """给棋盘控件用的最小状态（编辑模式下不显示提示/箭头/上一手）。"""
        return {
            "board": self.board_rows(),
            "side": self.side,
            "settings": dict(settings or {}),
            "selected": None,
            "destinations": [],
            "lastMove": None,
            "check": False,
        }
