# -*- coding: utf-8 -*-
"""中国象棋规则内核。

坐标约定（与 UCI/ICCS 一致，也是皮卡鱼 Pikafish 使用的表示法）：

* 纵线 file 用字母 a-i 表示，a 在红方视角最左边，i 在最右边；
* 横线 rank 用 0-9 表示，0 是红方底线（下方），9 是黑方底线（上方）；
* 例如开局的炮二平五记作 ``h2e2``。

棋盘内部用 ``cells[rank][file]`` 保存，红方用大写字母，黑方用小写：
``K`` 帅/将、``A`` 仕/士、``B`` 相/象、``N`` 马、``R`` 车、``C`` 炮、``P`` 兵/卒。
FEN 的行序与此相反（FEN 第一行是 rank 9）。
"""

from __future__ import annotations

from typing import Dict, Iterator, List, Optional, Tuple

RED = "w"
BLACK = "b"
EMPTY = "."

FILES = "abcdefghi"
START_FEN = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1"

#: 子力价值（半分制，便于整数运算）
PIECE_VALUE = {"K": 0, "A": 4, "B": 4, "N": 8, "R": 18, "C": 9, "P": 2}

PIECE_NAMES_SIMPLE = {
    "K": "帅", "A": "仕", "B": "相", "N": "马", "R": "车", "C": "炮", "P": "兵",
    "k": "将", "a": "士", "b": "象", "n": "马", "r": "车", "c": "炮", "p": "卒",
}

PIECE_NAMES_TRAD = {
    "K": "帥", "A": "仕", "B": "相", "N": "傌", "R": "俥", "C": "炮", "P": "兵",
    "k": "將", "a": "士", "b": "象", "n": "馬", "r": "車", "c": "砲", "p": "卒",
}


def color_of(piece: str) -> str:
    """返回棋子颜色；``EMPTY`` 会返回黑方，调用前请自行判空。"""
    return RED if piece.isupper() else BLACK


def opponent(color: str) -> str:
    return BLACK if color == RED else RED


def side_name(color: str) -> str:
    return "红方" if color == RED else "黑方"


def sq_name(file: int, rank: int) -> str:
    return FILES[file] + str(rank)


def parse_sq(name: str) -> Tuple[int, int]:
    """把 ``"e2"`` 解析成 ``(file, rank)``。"""
    if len(name) != 2 or name[0] not in FILES or not name[1].isdigit():
        raise ValueError(f"非法坐标: {name!r}")
    return FILES.index(name[0]), int(name[1])


class Move:
    """一步棋。``piece`` 是移动的棋子，``captured`` 是被吃的棋子（无则 ``EMPTY``）。"""

    __slots__ = ("ff", "fr", "tf", "tr", "piece", "captured")

    def __init__(self, ff: int, fr: int, tf: int, tr: int,
                 piece: str = EMPTY, captured: str = EMPTY):
        self.ff = ff
        self.fr = fr
        self.tf = tf
        self.tr = tr
        self.piece = piece
        self.captured = captured

    @property
    def frm(self) -> Tuple[int, int]:
        return (self.ff, self.fr)

    @property
    def to(self) -> Tuple[int, int]:
        return (self.tf, self.tr)

    @property
    def from_name(self) -> str:
        return sq_name(self.ff, self.fr)

    @property
    def to_name(self) -> str:
        return sq_name(self.tf, self.tr)

    def iccs(self) -> str:
        return self.from_name + self.to_name

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, Move)
                and (self.ff, self.fr, self.tf, self.tr)
                == (other.ff, other.fr, other.tf, other.tr))

    def __hash__(self) -> int:
        return hash((self.ff, self.fr, self.tf, self.tr))

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Move {self.iccs()} {self.piece!r}x{self.captured!r}>"


class Board:
    """中国象棋棋盘：FEN 读写、走子生成、合法性判断、走子与悔子。"""

    KING_STEPS = ((0, 1), (0, -1), (1, 0), (-1, 0))
    ADVISOR_STEPS = ((1, 1), (1, -1), (-1, 1), (-1, -1))
    BISHOP_STEPS = ((2, 2), (2, -2), (-2, 2), (-2, -2))
    KNIGHT_STEPS = ((1, 2), (1, -2), (-1, 2), (-1, -2),
                    (2, 1), (2, -1), (-2, 1), (-2, -1))

    def __init__(self, fen: str = START_FEN, strict: bool = False):
        self.cells: List[List[str]] = [[EMPTY] * 9 for _ in range(10)]
        self.side = RED
        self.halfmove = 0
        self.fullmove = 1
        self._squares: Dict[str, set] = {RED: set(), BLACK: set()}
        self._stack: List[Tuple[Move, str]] = []
        self.from_fen(fen)
        if strict:
            problems = self.check_legal_position()
            if problems:
                raise ValueError("；".join(problems))

    # ------------------------------------------------------------ 基础读写
    def from_fen(self, fen: str) -> "Board":
        parts = fen.strip().split()
        if not parts:
            raise ValueError("FEN 为空")
        rows = parts[0].split("/")
        if len(rows) != 10:
            raise ValueError(f"FEN 应有 10 行，实际 {len(rows)} 行")

        self.cells = [[EMPTY] * 9 for _ in range(10)]
        self._squares = {RED: set(), BLACK: set()}
        for fen_row, row_text in enumerate(rows):
            rank = 9 - fen_row
            file = 0
            for ch in row_text:
                if ch.isdigit():
                    file += int(ch)
                    if file > 9:
                        raise ValueError(f"FEN 第 {fen_row + 1} 行超出 9 列")
                else:
                    if file > 8:
                        raise ValueError(f"FEN 第 {fen_row + 1} 行超出 9 列")
                    self._set(file, rank, ch)
                    file += 1
            if file != 9:
                raise ValueError(f"FEN 第 {fen_row + 1} 行不足 9 列")

        self.side = BLACK if (len(parts) > 1 and parts[1].lower() == "b") else RED
        self.halfmove = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
        self.fullmove = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 1
        self._stack = []
        self._validate()
        return self

    def _validate(self) -> None:
        kings = {RED: 0, BLACK: 0}
        for rank in range(10):
            for file in range(9):
                piece = self.cells[rank][file]
                if piece == EMPTY:
                    continue
                if piece.upper() not in PIECE_VALUE:
                    raise ValueError(f"FEN 含非法棋子 {piece!r}")
                if piece.upper() == "K":
                    color = color_of(piece)
                    kings[color] += 1
                    if not self.in_palace(file, rank, color):
                        raise ValueError("将/帅不在九宫内")
        for color, count in kings.items():
            if count != 1:
                raise ValueError(f"{side_name(color)}将/帅数量为 {count}")

    def check_legal_position(self) -> List[str]:
        """检查那些"任何合法对局都走不出来"的局面，返回问题列表（空表示正常）。

        这类局面会让人工棋谱摆错，皮卡鱼等引擎也会直接拒绝（Unsupported position），
        所以用户手动输入 FEN 时应当提前拦下来。
        """
        problems: List[str] = []
        limits = {"A": 2, "B": 2, "N": 2, "R": 2, "C": 2, "P": 5}
        for color in (RED, BLACK):
            label = side_name(color)
            counts = {kind: 0 for kind in limits}
            for file, rank, piece in self.iter_pieces(color):
                kind = piece.upper()
                if kind in counts:
                    counts[kind] += 1
                if kind == "A" and not self.in_palace(file, rank, color):
                    problems.append(f"{label}仕/士不在九宫内（{sq_name(file, rank)}）")
                elif kind == "B" and not self.on_own_side(rank, color):
                    problems.append(f"{label}相/象过河（{sq_name(file, rank)}）")
                elif kind == "P":
                    if color == RED and rank < 3:
                        problems.append(f"{label}兵不可能退到 {sq_name(file, rank)}")
                    elif color == BLACK and rank > 6:
                        problems.append(f"{label}卒不可能进到 {sq_name(file, rank)}")
            for kind, limit in limits.items():
                if counts[kind] > limit:
                    names = {"A": "仕/士", "B": "相/象", "N": "马", "R": "车", "C": "炮", "P": "兵/卒"}
                    problems.append(f"{label}{names[kind]}最多 {limit} 个，当前 {counts[kind]} 个")
        return problems

    def to_fen(self) -> str:
        rows = []
        for rank in range(9, -1, -1):
            empty = 0
            row = []
            for file in range(9):
                piece = self.cells[rank][file]
                if piece == EMPTY:
                    empty += 1
                else:
                    if empty:
                        row.append(str(empty))
                        empty = 0
                    row.append(piece)
            if empty:
                row.append(str(empty))
            rows.append("".join(row))
        return "/".join(rows) + f" {self.side} - - {self.halfmove} {self.fullmove}"

    def copy(self) -> "Board":
        new = Board.__new__(Board)
        new.cells = [row[:] for row in self.cells]
        new.side = self.side
        new.halfmove = self.halfmove
        new.fullmove = self.fullmove
        new._squares = {RED: set(self._squares[RED]), BLACK: set(self._squares[BLACK])}
        new._stack = []
        return new

    def _set(self, file: int, rank: int, piece: str) -> None:
        self.cells[rank][file] = piece
        index = rank * 9 + file
        for color in (RED, BLACK):
            self._squares[color].discard(index)
        if piece != EMPTY:
            self._squares[color_of(piece)].add(index)

    def get(self, file: int, rank: int) -> str:
        if 0 <= file < 9 and 0 <= rank < 10:
            return self.cells[rank][file]
        return EMPTY

    def get_name(self, name: str) -> str:
        file, rank = parse_sq(name)
        return self.get(file, rank)

    # ------------------------------------------------------------ 查询
    def iter_pieces(self, color: Optional[str] = None) -> Iterator[Tuple[int, int, str]]:
        colors = (RED, BLACK) if color is None else (color,)
        for col in colors:
            for index in sorted(self._squares[col]):
                rank, file = divmod(index, 9)
                yield file, rank, self.cells[rank][file]

    def pieces(self, color: Optional[str] = None) -> List[Tuple[int, int, str]]:
        return list(self.iter_pieces(color))

    def count(self, kind: str, color: str) -> int:
        return sum(1 for _f, _r, p in self.iter_pieces(color) if p.upper() == kind)

    def king_square(self, color: str) -> Optional[Tuple[int, int]]:
        for file, rank, piece in self.iter_pieces(color):
            if piece.upper() == "K":
                return (file, rank)
        return None

    def material(self, color: str) -> int:
        return sum(PIECE_VALUE[p.upper()] for _f, _r, p in self.iter_pieces(color))

    @staticmethod
    def in_palace(file: int, rank: int, color: str) -> bool:
        if not (3 <= file <= 5):
            return False
        return 0 <= rank <= 2 if color == RED else 7 <= rank <= 9

    @staticmethod
    def on_own_side(rank: int, color: str) -> bool:
        """相/象是否在己方半场（象不过河）。"""
        return rank <= 4 if color == RED else rank >= 5

    @staticmethod
    def pawn_crossed(rank: int, color: str) -> bool:
        return rank >= 5 if color == RED else rank <= 4

    @staticmethod
    def forward(color: str) -> int:
        return 1 if color == RED else -1

    # ------------------------------------------------------------ 走子生成
    def gen_moves(self, color: Optional[str] = None) -> List[Move]:
        """生成伪合法着法（未过滤送将、白脸将等非法局面）。"""
        color = color or self.side
        moves: List[Move] = []
        cells = self.cells
        for file, rank, piece in self.iter_pieces(color):
            kind = piece.upper()
            if kind == "K":
                for df, dr in self.KING_STEPS:
                    nf, nr = file + df, rank + dr
                    if not self.in_palace(nf, nr, color):
                        continue
                    target = cells[nr][nf]
                    if target == EMPTY or color_of(target) != color:
                        moves.append(Move(file, rank, nf, nr, piece, target))
            elif kind == "A":
                for df, dr in self.ADVISOR_STEPS:
                    nf, nr = file + df, rank + dr
                    if not self.in_palace(nf, nr, color):
                        continue
                    target = cells[nr][nf]
                    if target == EMPTY or color_of(target) != color:
                        moves.append(Move(file, rank, nf, nr, piece, target))
            elif kind == "B":
                for df, dr in self.BISHOP_STEPS:
                    nf, nr = file + df, rank + dr
                    if not (0 <= nf < 9 and 0 <= nr < 10):
                        continue
                    if not self.on_own_side(nr, color):
                        continue
                    if cells[rank + dr // 2][file + df // 2] != EMPTY:  # 塞象眼
                        continue
                    target = cells[nr][nf]
                    if target == EMPTY or color_of(target) != color:
                        moves.append(Move(file, rank, nf, nr, piece, target))
            elif kind == "N":
                for df, dr in self.KNIGHT_STEPS:
                    nf, nr = file + df, rank + dr
                    if not (0 <= nf < 9 and 0 <= nr < 10):
                        continue
                    if abs(df) == 2:
                        leg_f, leg_r = file + df // 2, rank
                    else:
                        leg_f, leg_r = file, rank + dr // 2
                    if cells[leg_r][leg_f] != EMPTY:  # 蹩马腿
                        continue
                    target = cells[nr][nf]
                    if target == EMPTY or color_of(target) != color:
                        moves.append(Move(file, rank, nf, nr, piece, target))
            elif kind == "R":
                for df, dr in self.KING_STEPS:
                    nf, nr = file + df, rank + dr
                    while 0 <= nf < 9 and 0 <= nr < 10:
                        target = cells[nr][nf]
                        if target == EMPTY:
                            moves.append(Move(file, rank, nf, nr, piece, EMPTY))
                        else:
                            if color_of(target) != color:
                                moves.append(Move(file, rank, nf, nr, piece, target))
                            break
                        nf += df
                        nr += dr
            elif kind == "C":
                for df, dr in self.KING_STEPS:
                    nf, nr = file + df, rank + dr
                    screen = False
                    while 0 <= nf < 9 and 0 <= nr < 10:
                        target = cells[nr][nf]
                        if not screen:
                            if target == EMPTY:
                                moves.append(Move(file, rank, nf, nr, piece, EMPTY))
                            else:
                                screen = True
                        elif target != EMPTY:
                            if color_of(target) != color:
                                moves.append(Move(file, rank, nf, nr, piece, target))
                            break
                        nf += df
                        nr += dr
            elif kind == "P":
                step = self.forward(color)
                nf, nr = file, rank + step
                if 0 <= nr < 10:
                    target = cells[nr][nf]
                    if target == EMPTY or color_of(target) != color:
                        moves.append(Move(file, rank, nf, nr, piece, target))
                if self.pawn_crossed(rank, color):
                    for df in (1, -1):
                        nf = file + df
                        if 0 <= nf < 9:
                            target = cells[rank][nf]
                            if target == EMPTY or color_of(target) != color:
                                moves.append(Move(file, rank, nf, rank, piece, target))
        return moves

    def is_attacked(self, file: int, rank: int, by_color: str) -> bool:
        """判断 (file, rank) 是否被 ``by_color`` 方攻击（含白脸将对脸）。"""
        cells = self.cells

        # 兵/卒：正前方一格，以及过河后的左右一格
        pawn_char = "P" if by_color == RED else "p"
        pr = rank - self.forward(by_color)
        if 0 <= pr < 10 and cells[pr][file] == pawn_char:
            return True
        for df in (1, -1):
            pf = file + df
            if 0 <= pf < 9 and self.pawn_crossed(rank, by_color) and cells[rank][pf] == pawn_char:
                return True

        # 将/帅：贴身一步，或同一直线中间无子的对脸
        king_char = "K" if by_color == RED else "k"
        for df, dr in self.KING_STEPS:
            nf, nr = file + df, rank + dr
            if 0 <= nf < 9 and 0 <= nr < 10 and cells[nr][nf] == king_char:
                return True
        king_pos = self.king_square(by_color)
        if king_pos is not None and king_pos[0] == file:
            kf, kr = king_pos
            lo, hi = min(kr, rank) + 1, max(kr, rank)
            if all(cells[r][kf] == EMPTY for r in range(lo, hi)):
                return True

        # 仕/士
        advisor_char = "A" if by_color == RED else "a"
        for df, dr in self.ADVISOR_STEPS:
            nf, nr = file + df, rank + dr
            if 0 <= nf < 9 and 0 <= nr < 10 and cells[nr][nf] == advisor_char:
                return True

        # 马（反向推导马腿位置）
        knight_char = "N" if by_color == RED else "n"
        for df, dr in self.KNIGHT_STEPS:
            nf, nr = file + df, rank + dr
            if not (0 <= nf < 9 and 0 <= nr < 10) or cells[nr][nf] != knight_char:
                continue
            if abs(df) == 2:
                leg_f, leg_r = nf - df // 2, nr
            else:
                leg_f, leg_r = nf, nr - dr // 2
            if cells[leg_r][leg_f] == EMPTY:
                return True

        # 车（第一个子）与炮（越过一个子后的第二个子）
        rook_char = "R" if by_color == RED else "r"
        cannon_char = "C" if by_color == RED else "c"
        for df, dr in self.KING_STEPS:
            nf, nr = file + df, rank + dr
            screen = False
            while 0 <= nf < 9 and 0 <= nr < 10:
                target = cells[nr][nf]
                if target != EMPTY:
                    if not screen:
                        if target in (rook_char, king_char):
                            return True
                        screen = True
                    else:
                        if target == cannon_char:
                            return True
                        break
                nf += df
                nr += dr
        return False

    # ------------------------------------------------------------ 合法性
    def is_check(self, color: str) -> bool:
        king = self.king_square(color)
        if king is None:
            return False
        return self.is_attacked(king[0], king[1], opponent(color))

    def is_checkmate(self, color: Optional[str] = None) -> bool:
        color = color or self.side
        return self.is_check(color) and not self.legal_moves(color)

    def is_stalemate(self, color: Optional[str] = None) -> bool:
        """困毙：无着可走且未被将军。中国象棋判负。"""
        color = color or self.side
        return (not self.is_check(color)) and not self.legal_moves(color)

    def legal_moves(self, color: Optional[str] = None) -> List[Move]:
        color = color or self.side
        result = []
        for move in self.gen_moves(color):
            self.push(move)
            if not self.is_check(color):
                result.append(move)
            self.pop()
        return result

    def legal_moves_from(self, file: int, rank: int) -> List[Move]:
        piece = self.get(file, rank)
        if piece == EMPTY or color_of(piece) != self.side:
            return []
        return [m for m in self.legal_moves() if (m.ff, m.fr) == (file, rank)]

    def find_move(self, from_name: str, to_name: str) -> Optional[Move]:
        ff, fr = parse_sq(from_name)
        tf, tr = parse_sq(to_name)
        for move in self.legal_moves():
            if (move.ff, move.fr, move.tf, move.tr) == (ff, fr, tf, tr):
                return move
        return None

    def find_move_iccs(self, text: str) -> Optional[Move]:
        text = (text or "").strip().lower()
        if len(text) != 4:
            return None
        return self.find_move(text[:2], text[2:])

    # ------------------------------------------------------------ 走子 / 悔子
    def push(self, move: Move) -> Move:
        """执行着法（不校验合法性），可用 ``pop`` 撤销。"""
        piece = self.cells[move.fr][move.ff]
        captured = self.cells[move.tr][move.tf]
        move.piece = piece
        move.captured = captured
        self._set(move.ff, move.fr, EMPTY)
        self._set(move.tf, move.tr, piece)
        self._stack.append((move, captured))
        self.halfmove += 1
        if self.side == BLACK:
            self.fullmove += 1
        self.side = opponent(self.side)
        return move

    def pop(self) -> Optional[Move]:
        if not self._stack:
            return None
        move, captured = self._stack.pop()
        self._set(move.tf, move.tr, EMPTY)
        self._set(move.ff, move.fr, move.piece)
        if captured != EMPTY:
            self._set(move.tf, move.tr, captured)
        self.side = opponent(self.side)
        if self.side == BLACK:
            self.fullmove = max(1, self.fullmove - 1)
        self.halfmove = max(0, self.halfmove - 1)
        return move

    def move_iccs(self, text: str) -> Optional[Move]:
        """按 ICCS 记谱走子，非法返回 ``None``。"""
        move = self.find_move_iccs(text)
        if move is None:
            return None
        return self.push(move)

    # ------------------------------------------------------------ 其它工具
    def perft(self, depth: int, color: Optional[str] = None) -> int:
        """走子生成正确性测试（不计长将、长捉等判罚规则）。"""
        color = color or self.side
        if depth <= 0:
            return 1
        total = 0
        for move in self.legal_moves(color):
            if depth == 1:
                total += 1
            else:
                self.push(move)
                total += self.perft(depth - 1)
                self.pop()
        return total

    def result(self, color: Optional[str] = None) -> Optional[str]:
        """若一方被将死或困毙，返回**胜方** ``"red"`` / ``"black"``，否则 ``None``。"""
        color = color or self.side
        if self.legal_moves(color):
            return None
        return "black" if color == RED else "red"

    def check_flag(self, color: Optional[str] = None) -> bool:
        return self.is_check(color or self.side)

    def ascii(self) -> str:  # pragma: no cover - 调试用
        lines = []
        for rank in range(9, -1, -1):
            lines.append(f"{rank} " + " ".join(self.cells[rank]))
        lines.append("  " + " ".join(FILES))
        return "\n".join(lines)

    def __str__(self) -> str:  # pragma: no cover - 调试用
        return self.to_fen()
