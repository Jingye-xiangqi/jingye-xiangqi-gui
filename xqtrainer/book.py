# -*- coding: utf-8 -*-
"""开局库 / 云库：加载库文件、按局面匹配招法，并给出权重与胜率。

支持四类来源（可同时加载，结果合并后按权重排序）：

* **OBK**：象棋巫师 / MoonRiver 开局库（``.obk``，部分 ``.bk`` 也是这个格式）。
  格式为 128 字节头 + 两段按 64 位哈希排序的记录数组（每条 10 字节：8 字节 key + 2 字节计数），
  哈希算法与常量见 :mod:`xqtrainer.data.obk_hash`；库中记录的是"局面出现次数"，
  因此某个招法的权重＝走完这步后局面在棋谱库中出现的次数。
* **云库 / 通用文本库**：文本或 JSON，直接记录"局面 → 招法 + 胜/和/负 + 出现次数"。
  注意：界面上的「加载开局库」按规范只接受 **PFBOOK / OBK / XQF** 三种格式，
  文本库类主要在程序内部与测试中使用（在线云库见 :mod:`xqtrainer.cloud`）。
* **棋谱生成的库**：把导入的 PGN/XQF 棋谱聚合成开局库（统计每种着法的胜率和出现次数）。
* **内存库**：程序内临时建立的库（例如从当前棋谱树统计）。
"""

from __future__ import annotations

import json
import random
import sqlite3
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .board import FILES, RED, Board, color_of, sq_name
from .data.obk_hash import hash_table
from .data.xq_book_hash import C90, SAN90, ZOBRIST_PLAYER, ZOBRIST_TABLE
from .game import GameTree, decode_bytes, parse_pgn
from .xqf import read_xqf

OBK_SIGNATURE = 13579
OBK_HEADER_SIZE = 128
OBK_ITEM_SIZE = 10
PIECE_TYPE = {"K": 0, "A": 1, "B": 2, "R": 3, "C": 4, "N": 5, "P": 6}

#: 允许加载的开局库格式（后缀）
BOOK_SUFFIXES = (".pfbook", ".obk", ".xqb", ".xqf")
BOOK_FORMAT_NAMES = {"pfbook": "PFBOOK（鹏飞象棋开局库）",
                     "obk": "OBK（象棋巫师 / MoonRiver 开局库）",
                     "xqb": "XQB（象棋开局库，SQLite 格式）",
                     "xqf": "XQF（棋谱库，按棋谱统计）"}

#: 库招选择策略（键 → 说明）
BOOK_STRATEGIES = {
    "best_score": "最高分",
    "best_rate": "最高胜率",
    "positive_random": "正分数随机",
    "random": "完全随机",
}
DEFAULT_BOOK_STRATEGY = "best_score"      # 默认"最高分"


class BookFormatError(ValueError):
    """文件不是被允许的开局库格式。"""


# ---------------------------------------------------------------- 数据模型
@dataclass
class BookHit:
    """一条库中招法。"""

    move: str                       # ICCS，例如 h2e2
    cn: str = ""                    # 中文记谱（由调用方补全）
    count: int = 0                  # 出现次数
    wins: int = 0
    draws: int = 0
    losses: int = 0
    source: str = ""
    book: str = ""                  # 命中的库文件名（多库时区分来源）
    score: float = 0.0              # 排序权重（次数，或按胜率换算）

    @property
    def total(self) -> int:
        return max(self.count, self.wins + self.draws + self.losses)

    @property
    def win_rate(self) -> Optional[float]:
        total = self.wins + self.draws + self.losses
        if total <= 0:
            return None
        return (self.wins + self.draws * 0.5) / total

    def to_dict(self) -> Dict[str, object]:
        rate = self.win_rate
        return {
            "move": self.move,
            "cn": self.cn,
            "count": self.count,
            "wins": self.wins,
            "draws": self.draws,
            "losses": self.losses,
            "source": self.source,
            "book": self.book,
            "score": round(self.score, 2),
            "winRate": None if rate is None else round(rate * 100, 1),
        }


def _merge(hits: Iterable[BookHit]) -> List[BookHit]:
    """合并同一着法在不同库里的数据，并按权重排序。"""
    merged: Dict[str, BookHit] = {}
    for hit in hits:
        current = merged.get(hit.move)
        if current is None:
            merged[hit.move] = hit
            continue
        current.count += hit.count
        current.wins += hit.wins
        current.draws += hit.draws
        current.losses += hit.losses
        if hit.source and current.source and hit.source not in current.source:
            current.source = current.source + "+" + hit.source
        elif hit.source and not current.source:
            current.source = hit.source
        if hit.book and current.book and hit.book not in current.book:
            current.book = current.book + "+" + hit.book
        elif hit.book and not current.book:
            current.book = hit.book
        current.score = max(current.score, hit.score)
    result = list(merged.values())
    for hit in result:
        hit.score = max(hit.score, float(hit.count))
    result.sort(key=lambda item: (-item.score, -item.count))
    return result


# ---------------------------------------------------------------- OBK
_TABLE = hash_table()


def obk_hash(board: Board) -> int:
    """计算 OBK 开局库使用的 64 位局面哈希（只与棋子摆放有关，与先行方无关）。"""
    key = 0
    for file, rank, piece in board.iter_pieces():
        pos = (9 - rank) * 9 + file
        side = 1 if piece.isupper() else 0
        key ^= _TABLE[side * 7 * 90 + PIECE_TYPE[piece.upper()] * 90 + pos]
    return key


#: 库着编码的几种可能写法（真实库文件里见过这几种，逐个试到"能走出合法着法"为止）
#: 开局库（BHBK / pfBook / XQB）的棋子编号：红 0-6 帅仕相马车炮兵，黑 7-13
XQ_BOOK_PIECE_INDEX = {"K": 0, "A": 1, "B": 2, "N": 3, "R": 4, "C": 5, "P": 6,
                       "k": 7, "a": 8, "b": 9, "n": 10, "r": 11, "c": 12, "p": 13}


def xq_book_hash(board: Board, left_right_swap: bool = False) -> int:
    """开局库使用的 64 位 Zobrist（含先行方）。

    公开实现（sojourners/public-Xiangqi）里的算法：
    ``square = c90[x + y * 9]``，``y = 9 - rank``（y=0 是黑方底线），
    ``key ^= ZOBRIST_TABLE[piece * 256 + square]``，红方走棋时再异或 ZOBRIST_PLAYER。
    ``left_right_swap`` 为真时把棋盘左右镜像（有些库存的是镜像局面）。
    """
    key = 0
    for rank in range(10):
        row = board.cells[rank]
        y = 9 - rank
        for file in range(9):
            piece = row[file]
            if piece == ".":
                continue
            x = (8 - file) if left_right_swap else file
            index = x + y * 9
            chess = XQ_BOOK_PIECE_INDEX.get(piece)
            if chess is None:
                continue
            key ^= ZOBRIST_TABLE[chess * 256 + C90[index]]
    if board.side == RED:
        key ^= ZOBRIST_PLAYER
    return key - (1 << 64) if key >= (1 << 63) else key


def xq_book_move(vmove: int, left_right_swap: bool = False) -> Optional[str]:
    """库里的着法编码 → ICCS（如 h2e2）。"""
    table = {C90[index]: SAN90[index] for index in range(90)}
    first = (int(vmove) >> 8) & 0xFF
    second = int(vmove) & 0xFF
    if left_right_swap:
        first = (first & ~15) | (14 - first % 16)
        second = (second & ~15) | (14 - second % 16)
    source, target = table.get(first), table.get(second)
    if not source or not target or source == target:
        return None
    return source + target


MOVE_SCHEMES = (
    ("byte_rank9", "high/8bit, square=(9-rank)*9+file"),
    ("byte_rank0", "high/8bit, square=rank*9+file"),
    ("byte_file10", "high/8bit, square=file*10+rank"),
    ("seven_bit", "two 7-bit squares"),
    ("swap_byte", "high/8bit 但 from/to 对调"),
)


def decode_book_move(value: int, scheme: str) -> Optional[Tuple[int, int]]:
    """把库里的走子数字解成两个"格号"(0..89)，返回 (from, to)。"""
    if value is None:
        return None
    value = int(value)
    if scheme == "seven_bit":
        return (value >> 7) & 0x7F, value & 0x7F
    high, low = (value >> 8) & 0xFF, value & 0xFF
    if scheme == "swap_byte":
        high, low = low, high
    return high, low


def square_to_iccs(square: int, numbering: str) -> Optional[str]:
    """格号 → ICCS 坐标（如 h2）。"""
    if not 0 <= square <= 89:
        return None
    if numbering == "byte_file10":
        file, rank = divmod(square, 10)
    elif numbering == "byte_rank0":
        rank, file = divmod(square, 9)
    else:                                   # (9-rank)*9+file：a9=0，自上而下
        row, file = divmod(square, 9)
        rank = 9 - row
    if not (0 <= file <= 8 and 0 <= rank <= 9):
        return None
    return FILES[file] + str(rank)


class SqliteBook:
    """SQLite 开局库（跑库.obk、屠龙商业库.pfBook、小冰裸奔库.xqb 等实际都是这种）。

    表结构大同小异：``key``/``vkey`` 是局面哈希，``move``/``vmove`` 是着法编码，
    另外还有 ``score``/``vscore`` 与 ``win``/``draw``/``lost``（胜/和/负次数）。
    因为各家的哈希与编码写法不完全一致，这里查询时会依次试几种常见写法，
    并用"这一步在当前局面下是否合法"来自动确认编码。
    """

    name = "SQLite 开局库"

    def __init__(self, path: Optional[Path] = None):
        self.path: Optional[Path] = None
        self.table = ""
        self.key_col = "key"
        self.move_col = "move"
        self.score_col = ""
        self.win_col = ""
        self.draw_col = ""
        self.lost_col = ""
        self.valid_col = ""
        self.rows = 0
        self.scheme = ""                    # 已经确认可用的编码（空＝还没确认）
        self.fallback = False               # 是否只能"按库内容"列出来（局面键匹配不上）
        self._connection: Optional[sqlite3.Connection] = None
        if path is not None:
            self.load(path)

    # ------------------------------------------------------------ 加载
    def load(self, path: Path) -> None:
        path = Path(path)
        self.close()
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cursor = connection.cursor()
        tables = cursor.execute(
            "select name from sqlite_master where type='table'").fetchall()
        chosen = None
        for (name,) in tables:
            columns = [row[1].lower() for row in
                       cursor.execute(f'pragma table_info("{name}")')]
            has_key = any(item in columns for item in ("key", "vkey"))
            has_move = any(item in columns for item in ("move", "vmove"))
            if has_key and has_move:
                chosen = (name, columns)
                break
        if chosen is None:
            connection.close()
            raise ValueError("这个数据库里没有找到开局库表（需要 key/move 两列）")
        self.table, columns = chosen
        self.key_col = "vkey" if "vkey" in columns else "key"
        self.move_col = "vmove" if "vmove" in columns else "move"
        self.score_col = "vscore" if "vscore" in columns else (
            "score" if "score" in columns else "")
        self.win_col = "vwin" if "vwin" in columns else ("win" if "win" in columns else "")
        self.draw_col = "vdraw" if "vdraw" in columns else (
            "draw" if "draw" in columns else "")
        self.lost_col = "vlost" if "vlost" in columns else (
            "lost" if "lost" in columns else "")
        self.valid_col = "vvalid" if "vvalid" in columns else (
            "valid" if "valid" in columns else "")
        self.rows = cursor.execute(f'select count(*) from "{self.table}"').fetchone()[0]
        self._connection = connection
        self.path = path
        # 有些库的 key 是"双精度位模式"，这里记住表里 key 的存储类型
        self.key_type = cursor.execute(
            f'select typeof("{self.key_col}") from "{self.table}" '
            f'where "{self.key_col}" is not null limit 1').fetchone()

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:  # noqa: BLE001
                pass
            self._connection = None

    def describe(self) -> str:
        name = Path(self.path).name if self.path else "未加载"
        return (f"{name}（SQLite 开局库：表 {self.table}，{self.rows} 条记录，"
                f"含分值/胜负统计）")

    def rows_preview(self, limit: int = 200) -> List[Dict[str, object]]:
        """把库里的记录列出来（键 / 着法 / 分值 / 胜和负 / 胜率），供界面浏览。"""
        if self._connection is None:
            return []
        columns = [self.key_col, self.move_col]
        for column in (self.score_col, self.win_col, self.draw_col, self.lost_col):
            if column:
                columns.append(column)
        select = ", ".join(f'"{column}"' for column in columns)
        output: List[Dict[str, object]] = []
        try:
            rows = self._connection.execute(
                f'select {select} from "{self.table}" limit ?', (int(limit),)).fetchall()
        except Exception:  # noqa: BLE001
            return []
        for row in rows:
            values = dict(zip(columns, row))
            wins = int(values.get(self.win_col) or 0)
            draws = int(values.get(self.draw_col) or 0)
            losses = int(values.get(self.lost_col) or 0)
            total = wins + draws + losses
            raw_move = values.get(self.move_col)
            output.append({
                "key": str(values.get(self.key_col)),
                "move": self._decode_any(raw_move),
                "rawMove": raw_move,
                "score": values.get(self.score_col),
                "wins": wins, "draws": draws, "losses": losses,
                "count": total,
                "winRate": round((wins + draws * 0.5) / total * 100, 1) if total else None,
            })
        return output

    def _decode_any(self, raw) -> str:
        """给浏览用：不依赖当前局面，把能解出来的写法都试一遍。"""
        for scheme, _description in MOVE_SCHEMES:
            pair = decode_book_move(raw, scheme)
            if pair is None:
                continue
            numbering = ("byte_file10" if scheme == "byte_file10" else
                         "byte_rank0" if scheme == "byte_rank0" else "rank9")
            source = square_to_iccs(pair[0], numbering)
            target = square_to_iccs(pair[1], numbering)
            if source and target and source != target:
                return f"{source}{target}"
        return str(raw)

    # ------------------------------------------------------------ 查询
    def _key_forms(self, board: Board) -> List[object]:
        """这个局面在库里的键值（整数 / 双精度位模式 / 8 字节 BLOB 三种存法都试）。

        键值算两套：公开的 OBK 哈希，以及这类库实际用的 Zobrist（含先行方，
        并额外尝试左右镜像，因为有些库存的是镜像局面）；能命中哪套就用哪套。
        """
        forms: List[object] = []
        for value in (obk_hash(board), xq_book_hash(board),
                      xq_book_hash(board, left_right_swap=True)):
            value &= 0xFFFFFFFFFFFFFFFF
            signed = value - (1 << 64) if value >= (1 << 63) else value
            forms.extend([signed, struct.unpack("<d", struct.pack("<Q", value))[0],
                          struct.pack("<Q", value), struct.pack(">Q", value)])
        return forms

    def _key_form_pairs(self, board: Board,
                        mirror: bool = False) -> List[Tuple[object, bool]]:
        """(键的存法, 是否需要左右镜像解码)。

        这类库只认它自己的 Zobrist（含先行方），按参考实现有两种存法：整数、双精度位模式。
        绝不能再拿别的哈希去碰运气 —— 那会把**别的局面**的记录当成当前局面的招法显示出来
        （之前"凭空多出一招"就是这么来的）。
        """
        output: List[Tuple[object, bool]] = []
        for value, swap in ((xq_book_hash(board, mirror), mirror),):
            unsigned = value & 0xFFFFFFFFFFFFFFFF
            signed = unsigned - (1 << 64) if unsigned >= (1 << 63) else unsigned
            for form in (signed, struct.unpack("<d", struct.pack("<Q", unsigned))[0]):
                output.append((form, swap))
        return output

    def lookup(self, board: Board, limit: int = 12) -> List[BookHit]:
        # 只返回"当前局面在库里确实有、而且这一步在当前局面下合法"的招法；
        # 库里没有就返回空列表（界面上留空），绝不拿库内容或其它局面的记录凑数。
        return self._lookup_by_key(board, limit)

    def _lookup_by_key(self, board: Board, limit: int = 12) -> List[BookHit]:
        if self._connection is None:
            return []
        hits: List[BookHit] = []
        columns = [self.move_col]
        for column in (self.score_col, self.win_col, self.draw_col, self.lost_col):
            if column:
                columns.append(column)
        select = ", ".join(f'"{column}"' for column in columns)
        best: Dict[str, Dict[str, object]] = {}
        # 先按"当前局面的键"查；万一这个库存的是镜像局面，再试一次镜像键。
        for mirror in (False, True):
            found_rows = False
            for form, swap in self._key_form_pairs(board, mirror):
                try:
                    rows = self._connection.execute(
                        f'select {select} from "{self.table}" '
                        f'where "{self.key_col}" = ? limit 80', (form,)).fetchall()
                except Exception:  # noqa: BLE001
                    continue
                for row in rows:
                    found_rows = True
                    values = dict(zip(columns, row))
                    raw = values.get(self.move_col)
                    move = xq_book_move(raw, swap) if raw is not None else None
                    # 必须能在当前局面里走：解不出来或不合法的记录一律丢掉
                    if not move or board.find_move_iccs(move) is None:
                        continue
                    try:
                        score = float(values.get(self.score_col) or 0)
                    except (TypeError, ValueError):
                        score = 0.0
                    wins = int(values.get(self.win_col) or 0)
                    draws = int(values.get(self.draw_col) or 0)
                    losses = int(values.get(self.lost_col) or 0)
                    current = best.get(move)
                    # 同一个局面里同一步可能存了多行（不同来源的统计），
                    # 取分值最高的那一行，并**用它自己的**胜负次数（不累加，避免
                    # 出现次数翻倍、胜率被拉低这种"数据对不上"的问题）。
                    if current is None or score > float(current["score"]):
                        best[move] = {"score": score, "wins": wins, "draws": draws,
                                      "losses": losses}
            if found_rows and best:
                break
        for move, data in best.items():
            wins = int(data["wins"])
            draws = int(data["draws"])
            losses = int(data["losses"])
            hits.append(BookHit(
                move=move, count=wins + draws + losses or 1,
                wins=wins, draws=draws, losses=losses,
                score=float(data["score"]), source="开局库"))
        # 注意：这类 SQLite 库存的是"**当前局面**的键 + 该局面可走的着法"，
        # 所以绝不能再用传统 OBK 那种"子局面哈希"去猜着法 —— 那会把下一个局面的
        # 招法当成当前局面的招法显示出来（就是之前的"假招法"问题）。
        hits.sort(key=lambda item: (-item.score, -item.count))
        return hits[:limit]

    def _fallback_hits(self, limit: int = 12) -> List[BookHit]:
        """按库内容列出记录（用于键匹配不上的库；分值/胜负统计照实显示）。"""
        output: List[BookHit] = []
        for row in self.rows_preview(limit):
            try:
                score = float(row.get("score") or 0)
            except (TypeError, ValueError):
                score = 0.0
            output.append(BookHit(
                move=str(row.get("move") or ""),
                count=int(row.get("count") or 0) or 1,
                wins=int(row.get("wins") or 0),
                draws=int(row.get("draws") or 0),
                losses=int(row.get("losses") or 0),
                score=score,
                source="开局库内容"))
        return output

    def _decode_row(self, board: Board, values: Dict[str, object]) -> Optional[str]:
        """把一行里的着法解成 ICCS；用"是否合法"自动确认编码写法。"""
        raw = values.get(self.move_col)
        schemes = (self.scheme,) + tuple(item[0] for item in MOVE_SCHEMES)
        for scheme in schemes:
            if not scheme:
                continue
            pair = decode_book_move(raw, scheme)
            if pair is None:
                continue
            numbering = ("byte_file10" if scheme == "byte_file10" else
                         "byte_rank0" if scheme == "byte_rank0" else "rank9")
            if scheme == "seven_bit":
                numbering = "rank9"
            source = square_to_iccs(pair[0], numbering)
            target = square_to_iccs(pair[1], numbering)
            if not source or not target or source == target:
                continue
            if board.find_move_iccs(source + target) is not None:
                self.scheme = scheme          # 记住了：以后先用这个
                return source + target
        return None


class ObkBook:
    """OBK 开局库（后缀常为 .obk，少数为 .bk）。"""

    name = "OBK 开局库"

    def __init__(self, path: Optional[Path] = None):
        self.path: Optional[Path] = None
        self.info = ""
        self.counts: Tuple[int, int] = (0, 0)
        self._data: Tuple[bytes, bytes] = (b"", b"")
        if path is not None:
            self.load(path)

    def load(self, path: Path) -> None:
        data = Path(path).read_bytes()
        if len(data) < OBK_HEADER_SIZE:
            raise ValueError("文件太小，不是有效的 OBK 开局库")
        signature, _property, _reserve = struct.unpack_from("<HHI", data, 0)
        if signature != OBK_SIGNATURE:
            # 一些 PFBOOK / BK 库与 OBK 结构相同，只是签名不同：按结构判断
            if not self._looks_like_obk_structure(data):
                raise ValueError(f"文件签名不是 OBK（0x{signature:04X}），结构也不匹配")
        size_black, size_red = struct.unpack_from("<qq", data, 8)
        if size_black < 0 or size_red < 0:
            raise ValueError("OBK 记录数异常")
        self.info = data[32:128].split(b"\x00", 1)[0].decode("gb18030", errors="replace").strip()
        offset = OBK_HEADER_SIZE
        black = data[offset:offset + size_black * OBK_ITEM_SIZE]
        offset += size_black * OBK_ITEM_SIZE
        red = data[offset:offset + size_red * OBK_ITEM_SIZE]
        if len(black) < size_black * OBK_ITEM_SIZE or len(red) < size_red * OBK_ITEM_SIZE:
            raise ValueError("OBK 文件数据不完整")
        self._data = (black, red)
        self.counts = (int(size_black), int(size_red))
        self.path = Path(path)

    @staticmethod
    def _looks_like_obk_structure(data: bytes) -> bool:
        """不带签名时按结构判断：头部两个长度与文件大小吻合、记录按 key 升序。"""
        try:
            size_black, size_red = struct.unpack_from("<qq", data, 8)
        except struct.error:
            return False
        if size_black < 0 or size_red < 0:
            return False
        expected = OBK_HEADER_SIZE + (size_black + size_red) * OBK_ITEM_SIZE
        if expected != len(data) or size_black + size_red == 0:
            return False
        offset = OBK_HEADER_SIZE
        for size in (size_black, size_red):
            if size <= 1:
                offset += size * OBK_ITEM_SIZE
                continue
            previous = struct.unpack_from("<Q", data, offset)[0]
            for index in range(1, min(size, 200)):      # 抽样检查升序
                current = struct.unpack_from("<Q", data, offset + index * OBK_ITEM_SIZE)[0]
                if current < previous:
                    return False
                previous = current
            offset += size * OBK_ITEM_SIZE
        return True

    def _find(self, key: int, side_index: int) -> int:
        """二分查找（记录按 key 升序排列），返回出现次数，0 表示未命中。"""
        blob = self._data[side_index]
        count = self.counts[side_index]
        low, high = 0, count - 1
        while low <= high:
            middle = (low + high) // 2
            offset = middle * OBK_ITEM_SIZE
            # 注意：key 是小端存储的 u64，必须按数值比较（字节序比较不单调）
            current = struct.unpack_from("<Q", blob, offset)[0]
            if current == key:
                return struct.unpack_from("<H", blob, offset + 8)[0]
            if current < key:
                low = middle + 1
            else:
                high = middle - 1
        return 0

    def lookup(self, board: Board, limit: int = 12) -> List[BookHit]:
        if not self._data[0] and not self._data[1]:
            return []
        side_index = 1 if board.side == RED else 0
        work = board.copy()
        hits: List[BookHit] = []
        for move in board.legal_moves():
            work.push(move)
            count = self._find(obk_hash(work), side_index)
            work.pop()
            if count > 0:
                hits.append(BookHit(move=move.iccs(), count=int(count), source="OBK",
                                    score=float(count)))
        hits.sort(key=lambda item: -item.count)
        return hits[:limit]

    def describe(self) -> str:
        if self.path is None:
            return "未加载"
        return (f"{self.path.name}（黑方记录 {self.counts[0]} 条、红方记录 {self.counts[1]} 条）"
                + (f"　{self.info}" if self.info else ""))


def write_obk(target: Path, entries: Dict[Tuple[int, int], int],
              info: str = "静夜象棋界面 生成") -> int:
    """写出 OBK 开局库文件。

    ``entries`` 的键是 ``(side_index, key)``：``side_index`` 0=黑方走的、1=红方走的，
    ``key`` 是走完这步之后的局面哈希（:func:`obk_hash`），值是该局面出现次数。
    返回写入的记录总数。
    """
    black = sorted((key, count) for (side, key), count in entries.items() if side == 0)
    red = sorted((key, count) for (side, key), count in entries.items() if side == 1)
    header = bytearray(OBK_HEADER_SIZE)
    struct.pack_into("<HHI", header, 0, OBK_SIGNATURE, 0, 0)
    struct.pack_into("<qq", header, 8, len(black), len(red))
    text = info.encode("gb18030", errors="replace")[:88]
    header[32:32 + len(text)] = text
    body = bytearray()
    for key, count in black + red:
        body += struct.pack("<QH", key, max(1, min(0xFFFF, int(count))))
    Path(target).write_bytes(bytes(header) + bytes(body))
    return len(black) + len(red)


# ---------------------------------------------------------------- 文本 / 云库
class TextBook:
    """云库/通用文本库。

    支持两种写法（每行一条，``#`` 开头为注释）::

        fen=rnbakabnr/9/1c5c1/... w | h2e2 | w=12 d=5 l=3 count=20
        moves: h2e2 w=12 d=5 l=3 count=20        （仅用于开局局面）

    也支持 JSON: ``{"版本": ..., "局面": [{"fen": "...", "move": "h2e2", "wins": 1, ...}]}``。
    局面的键使用"棋子摆放 + 先行方"，与 FEN 前两段一致。
    """

    name = "云库/文本库"

    def __init__(self, path: Optional[Path] = None):
        self.path: Optional[Path] = None
        self.entries: Dict[str, List[BookHit]] = {}
        self.total = 0
        if path is not None:
            self.load(path)

    @staticmethod
    def key_of(board: Board) -> str:
        parts = board.to_fen().split()
        return parts[0] + " " + parts[1]

    def load(self, path: Path) -> None:
        path = Path(path)
        raw = path.read_bytes()
        text = decode_bytes(raw)
        self.path = path
        self.entries = {}
        stripped = text.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            self._load_json(text)
        else:
            self._load_lines(text)
        self.total = sum(len(items) for items in self.entries.values())
        if self.total == 0:
            raise ValueError("没有解析到任何招法（请检查文件格式）")

    def _load_json(self, text: str) -> None:
        data = json.loads(text)
        records = data.get("positions") or data.get("局面") or data
        if isinstance(records, dict):
            records = [dict(value, fen=key) for key, value in records.items()]
        for item in records:
            fen = str(item.get("fen") or item.get("position") or "").strip()
            moves = item.get("moves")
            if moves is None:
                moves = [item]
            for entry in moves:
                move = str(entry.get("move") or entry.get("iccs") or "").strip().lower()
                if not fen or not move:
                    continue
                key = " ".join(fen.split()[:2])
                self.entries.setdefault(key, []).append(BookHit(
                    move=move, count=int(entry.get("count") or 0),
                    wins=int(entry.get("wins") or entry.get("win") or 0),
                    draws=int(entry.get("draws") or entry.get("draw") or 0),
                    losses=int(entry.get("losses") or entry.get("loss") or 0),
                    source="云库", score=float(entry.get("score") or entry.get("count") or 0)))

    def _load_lines(self, text: str) -> None:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            fen = ""
            rest = line
            if line.lower().startswith("fen="):
                head, _, rest = line.partition("|")
                fen = head.split("=", 1)[1].strip()
            elif line.lower().startswith("moves:"):
                fen = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w"
                rest = line.split(":", 1)[1]
            else:
                parts = [part.strip() for part in line.split("|")]
                if len(parts) >= 2:
                    fen, rest = parts[0], parts[1]
            tokens = [token for token in rest.replace(",", " ").split() if token]
            if not fen or not tokens:
                continue
            move = tokens[0].lower()
            stats = {"w": 0, "d": 0, "l": 0, "count": 0}
            for token in tokens[1:]:
                if "=" not in token:
                    continue
                key, _, value = token.partition("=")
                key = key.strip().lower()
                try:
                    number = int(float(value))
                except ValueError:
                    continue
                if key in ("w", "win", "wins", "胜"):
                    stats["w"] = number
                elif key in ("d", "draw", "draws", "和"):
                    stats["d"] = number
                elif key in ("l", "loss", "losses", "负"):
                    stats["l"] = number
                elif key in ("count", "n", "次数"):
                    stats["count"] = number
            if not stats["count"]:
                stats["count"] = stats["w"] + stats["d"] + stats["l"]
            key = " ".join(fen.split()[:2])
            self.entries.setdefault(key, []).append(BookHit(
                move=move, count=stats["count"], wins=stats["w"], draws=stats["d"],
                losses=stats["l"], source="云库", score=float(stats["count"])))

    def lookup(self, board: Board, limit: int = 12) -> List[BookHit]:
        hits = self.entries.get(self.key_of(board), [])
        return sorted(hits, key=lambda item: (-item.count, -item.score))[:limit]

    def describe(self) -> str:
        if self.path is None:
            return "未加载"
        return f"{self.path.name}（{len(self.entries)} 个局面 / {self.total} 条招法）"


# ---------------------------------------------------------------- 棋谱建库
class PgnBook:
    """用棋谱（PGN/XQF）聚合出来的开局库，统计每种着法的胜/和/负与出现次数。"""

    name = "棋谱库"

    def __init__(self, depth: int = 30):
        self.depth = depth
        self.entries: Dict[str, List[BookHit]] = {}
        self.games = 0
        self.path: Optional[Path] = None

    def key_of(self, board: Board) -> str:
        return TextBook.key_of(board)

    def add_game(self, line: Sequence[str], result: str = "*") -> None:
        """``line`` 是 ICCS 着法序列，``result`` 为 1-0 / 0-1 / 1/2-1/2。"""
        board = Board()
        self.games += 1
        for index, iccs in enumerate(line[:self.depth]):
            move = board.find_move_iccs(iccs)
            if move is None:
                break
            key = self.key_of(board)
            hit = None
            for item in self.entries.setdefault(key, []):
                if item.move == iccs:
                    hit = item
                    break
            if hit is None:
                hit = BookHit(move=iccs, source="棋谱")
                self.entries[key].append(hit)
            hit.count += 1
            mover = color_of(move.piece)
            winner = {"1-0": RED, "0-1": "b", "1/2-1/2": None}.get(result)
            if winner is None:
                if result in ("1-0", "0-1"):
                    pass
                hit.draws += 1
            elif winner == mover:
                hit.wins += 1
            else:
                hit.losses += 1
            board.push(move)
        for items in self.entries.values():
            for item in items:
                item.score = float(item.count)

    def add_tree(self, tree: GameTree) -> None:
        result = tree.detect_result()
        for line in tree.all_lines():
            self.add_game(line, result)

    def load(self, path: Path) -> None:
        path = Path(path)
        data = path.read_bytes()
        if data[:2] == b"XQ":
            self.add_tree(read_xqf(data))
        else:
            for tree in parse_pgn(decode_bytes(data)):
                self.add_tree(tree)
        self.path = path
        if not self.entries:
            raise ValueError("棋谱里没有可用的着法")

    def lookup(self, board: Board, limit: int = 12) -> List[BookHit]:
        hits = self.entries.get(self.key_of(board), [])
        return sorted(hits, key=lambda item: (-item.count, -item.score))[:limit]

    def describe(self) -> str:
        if self.path is None:
            return f"未加载（内存库：{self.games} 局）"
        return (f"{self.path.name}（{self.games} 局 / {len(self.entries)} 个局面）")

    def to_obk(self, target: Path, info: str = "") -> int:
        """把棋谱库导出成 OBK 开局库（可给皮卡鱼以外的引擎使用）。"""
        entries: Dict[Tuple[int, int], int] = {}
        for fen_key, hits in self.entries.items():
            parts = fen_key.split()
            if len(parts) < 2:
                continue
            try:
                board = Board(" ".join(parts[:2]) + " - - 0 1")
            except ValueError:
                continue
            side_index = 1 if board.side == RED else 0
            for hit in hits:
                move = board.find_move_iccs(hit.move)
                if move is None:
                    continue
                board.push(move)
                key = obk_hash(board)
                board.pop()
                slot = (side_index, key)
                entries[slot] = entries.get(slot, 0) + max(1, hit.count)
        text = info or (f"build from {self.path.name}" if self.path else "静夜象棋界面 生成")
        return write_obk(target, entries, info=text)


# ---------------------------------------------------------------- 统一入口
class BookSource:
    """一个已加载的开局库（含文件路径，便于多库排序与显示）。"""

    def __init__(self, path: str, book) -> None:
        self.path = str(path or "")
        self.book = book

    @property
    def label(self) -> str:
        return Path(self.path).name if self.path else getattr(self.book, "name", "内存库")

    def describe(self) -> str:
        try:
            return f"{self.label}：{self.book.describe()}"
        except Exception:  # noqa: BLE001
            return self.label


class BookManager:
    """管理**多个**已加载的开局库（按优先级排序），并把结果合并成一张表。

    顺序即优先级：列表靠前的库先被查询，同名着法会合并权重。人机对战走库着时按
    :attr:`strategy` 选择的策略挑招（最高分 / 最高胜率 / 正分数随机 / 完全随机），
    超过 :attr:`max_ply` 手（脱谱步数）之后就不再使用库招。
    """

    def __init__(self):
        self.sources: List[BookSource] = []
        self.last_error = ""
        self.use_in_game = True
        self.strategy = DEFAULT_BOOK_STRATEGY
        self.max_ply = 0                       # 脱谱步数：0＝不限

    # -------------------------------------------------- 兼容旧接口（单个库）
    def _first(self, kind):
        for source in self.sources:
            if isinstance(source.book, kind):
                return source.book
        return None

    def _replace_kind(self, kind, book) -> None:
        index = None
        kept: List[BookSource] = []
        for position, source in enumerate(self.sources):
            if isinstance(source.book, kind):
                if index is None:
                    index = position
                continue
            kept.append(source)
        self.sources = kept
        if book is not None:
            entry = BookSource(getattr(book, "path", "") or "", book)
            if index is None:
                self.sources.append(entry)
            else:
                self.sources.insert(min(index, len(self.sources)), entry)

    @property
    def obk(self):
        return self._first(ObkBook)

    @obk.setter
    def obk(self, book) -> None:
        self._replace_kind(ObkBook, book)

    @property
    def text(self):
        return self._first(TextBook)

    @text.setter
    def text(self, book) -> None:
        self._replace_kind(TextBook, book)

    @property
    def pgn(self):
        return self._first(PgnBook)

    @pgn.setter
    def pgn(self, book) -> None:
        self._replace_kind(PgnBook, book)

    @property
    def sqlite(self):
        return self._first(SqliteBook)

    @sqlite.setter
    def sqlite(self, book) -> None:
        self._replace_kind(SqliteBook, book)

    # ------------------------------------------------------------ 多库管理
    def add(self, path: Path, book) -> BookSource:
        """加入一个库（同一个文件重复加载 = 重新读取，并保持原有优先级）。"""
        key = str(Path(path))
        for source in self.sources:
            if source.path == key:
                source.book = book
                return source
        entry = BookSource(key, book)
        self.sources.append(entry)
        return entry

    def remove(self, path: str) -> bool:
        key = str(path)
        before = len(self.sources)
        self.sources = [source for source in self.sources if source.path != key]
        return len(self.sources) != before

    def move(self, path: str, delta: int) -> bool:
        """在优先级列表里上移/下移一个库。"""
        key = str(path)
        for index, source in enumerate(self.sources):
            if source.path != key:
                continue
            target = index + delta
            if target < 0 or target >= len(self.sources):
                return False
            self.sources[index], self.sources[target] = (
                self.sources[target], self.sources[index])
            return True
        return False

    def list_books(self) -> List[Dict[str, object]]:
        return [{"path": source.path, "label": source.label, "describe": source.describe(),
                 "order": index + 1}
                for index, source in enumerate(self.sources)]

    def in_book(self, ply: int = 0) -> bool:
        """当前手数是否还在"谱内"（脱谱步数之内）。"""
        return not self.max_ply or int(ply) <= int(self.max_ply)

    # ------------------------------------------------------------ 加载
    def load(self, path: Path) -> str:
        """加载开局库。**只接受 PFBOOK / OBK / XQF 三种格式**，其它一律拒绝。"""
        path = Path(path)
        suffix = path.suffix.lower()
        errors: List[str] = []
        if suffix not in BOOK_SUFFIXES:
            allowed = "、".join(BOOK_FORMAT_NAMES[item.strip(".")] for item in BOOK_SUFFIXES)
            raise BookFormatError(
                f"不支持的开局库格式「{suffix or '无后缀'}」。开局库只允许加载：{allowed}；"
                f"棋谱请用顶栏「导入棋谱」（只支持 XQF / PGN）。"
                f"如果这个文件本身是 OBK 结构（例如 .xob / .bk），把它重命名为 .obk 即可加载。")
        # 1) SQLite 开局库：跑库.obk / 屠龙.pfBook / 小冰.xqb 这些常见库实际都是 SQLite
        try:
            with path.open("rb") as handle:
                magic = handle.read(16)
            if magic.startswith(b"SQLite format 3"):
                book = SqliteBook(path)
                self.add(path, book)
                return book.describe()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"SQLite：{exc}")
        try:
            if suffix in (".obk", ".pfbook"):
                book = ObkBook(path)
                self.add(path, book)
                return book.describe()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"OBK：{exc}")
        try:
            if suffix in (".xqf", ".pfbook", ".xqb"):
                book = PgnBook()
                book.load(path)
                self.add(path, book)
                return book.describe()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"XQF：{exc}")
        if suffix == ".xqf":
            raise BookFormatError("这个 XQF 文件里没有可用的着法，无法作为棋谱库加载。"
                                  + ("（" + "；".join(errors) + "）" if errors else ""))
        if suffix == ".pfbook":
            raise BookFormatError(
                "无法识别这个 .pfbook 文件。PFBOOK 是鹏飞象棋的私有格式、没有公开规范，"
                "本程序已尝试按其可能的 OBK 结构（128 字节头 + 哈希记录）和棋谱结构解析，"
                "都不匹配。建议改用 OBK 开局库，或把棋谱导入后点「用当前棋谱建库」再「导出 OBK」。"
                + ("　（细节：" + "；".join(errors) + "）" if errors else ""))
        if suffix == ".xqb":
            raise BookFormatError(
                "无法识别这个 .xqb 文件。本程序已尝试按 SQLite 开局库、OBK 结构与棋谱结构解析，"
                "都不匹配。" + ("　（细节：" + "；".join(errors) + "）" if errors else ""))
        raise BookFormatError("；".join(errors) or "无法加载该开局库")

    def clear(self) -> None:
        # 关掉 SQLite 连接，否则文件会被一直占用（换库/卸载时要能释放）
        for source in self.sources:
            close = getattr(source.book, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass
        self.sources = []
        self.last_error = ""

    @property
    def loaded(self) -> bool:
        return bool(self.sources)

    def describe(self) -> str:
        parts = [source.describe() for source in self.sources]
        return "；".join(parts) if parts else "未加载开局库"

    def book_rows_preview(self, limit: int = 300) -> List[Dict[str, object]]:
        """列出库里前若干条记录（支持 SQLite 类库；其它库返回空表）。"""
        for source in self.sources:
            fetch = getattr(source.book, "rows_preview", None)
            if callable(fetch):
                rows = fetch(limit)
                for row in rows:
                    row["book"] = source.label
                return rows
        return []

    # ------------------------------------------------------------ 查询
    def lookup(self, board: Board, limit: int = 12) -> List[BookHit]:
        hits: List[BookHit] = []
        for source in self.sources:
            try:
                found = source.book.lookup(board, limit)
            except Exception:  # noqa: BLE001
                continue
            for hit in found:
                hit.book = source.label
            hits.extend(found)
        return _merge(hits)[:limit]

    def choose(self, hits: List[BookHit]) -> Optional[BookHit]:
        """按策略从命中招法里挑一个。"""
        if not hits:
            return None
        strategy = self.strategy if self.strategy in BOOK_STRATEGIES else DEFAULT_BOOK_STRATEGY
        if strategy == "best_score":
            return max(hits, key=lambda item: (item.score, item.count))
        if strategy == "best_rate":
            rated = [item for item in hits if item.win_rate is not None]
            pool = rated or hits
            return max(pool, key=lambda item: (item.win_rate or 0.0, item.count, item.score))
        if strategy == "positive_random":
            pool = [item for item in hits if item.score > 0] or hits
            weights = [max(1.0, float(item.count or 1)) for item in pool]
            return random.choices(pool, weights=weights, k=1)[0]
        return random.choice(hits)                    # 完全随机

    def best_move(self, board: Board, ply: int = 0) -> Optional[BookHit]:
        if not self.use_in_game or not self.in_book(ply):
            return None
        return self.choose(self.lookup(board, limit=12))
