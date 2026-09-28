# -*- coding: utf-8 -*-
"""XQF 棋谱文件读取（象棋演播室 / 象棋桥使用的二进制格式）。

文件结构（都是小端）：

* ``0x000`` 头部 1024 字节：``"XQ"``、版本号、密钥块、32 个棋子坐标，
  随后是标题、赛事、日期、地点、红黑双方、用时等以 GB18030 编码的字段；
* ``0x400`` 起是着法区：按字节流顺序存放"走子记录"，每 4 字节一条，
  记录的标志位说明后面是否还有后续着法、是否有变例、是否带注释。

新版（版本号 > 0x0A）的着法与初始盘面都按文件中保存的密钥做了异或/置换，
这里按格式规范完整实现。解析时用自家规则内核逐步校验，遇到坏着会记入
``GameTree.warnings`` 而不是直接失败。
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from .board import Board, Move, color_of
from .game import GameTree

#: 密钥种子串（XQF 规范中的固定字符串）
KEY_STRING = b"[(C) Copyright Mr. Dong Shiwei.]"

#: 32 个棋子槽位对应的棋子类型，前 16 个是红方，后 16 个是黑方
CHESSMAN_KINDS = ("R", "N", "B", "A", "K", "A", "B", "N", "R", "C", "C",
                  "P", "P", "P", "P", "P")

RESULT_MAP = {0: "*", 1: "1-0", 2: "0-1", 3: "1/2-1/2", 4: "1/2-1/2"}
TYPE_MAP = {1: "对局", 2: "残局", 3: "排局", 4: "全局"}


class XqfError(Exception):
    """XQF 文件格式错误。"""


def _derive(crypt: bytes) -> Dict[str, int]:
    """由头部密钥块推导出各加密因子。"""
    if len(crypt) < 13:
        raise XqfError("XQF 密钥块长度不足")
    (key_mask, _product_id, or_a, or_b, or_c, or_d,
     keys_sum, key_xy, key_xyf, key_xyt) = struct.unpack("<BIBBBBBBBB", crypt[:13])

    def expand(bkey: int, base: int) -> int:
        value = ((((bkey * bkey) * 3 + 9) * 3 + 8) * 2 + 1) * 3 + 8
        return (value * base) & 0xFF

    xy = expand(key_xy, key_xy)
    xyf = expand(key_xyf, xy)
    xyt = expand(key_xyt, xyf)
    rmk_size = ((keys_sum * 256 + key_xy) % 32000) + 767
    key_bytes = (
        (keys_sum & key_mask) | or_a,
        (key_xy & key_mask) | or_b,
        (key_xyf & key_mask) | or_c,
        (key_xyt & key_mask) | or_d,
    )
    stream = bytearray(KEY_STRING)
    for i in range(len(stream)):
        stream[i] &= key_bytes[i % 4]
    return {"xy": xy, "xyf": xyf, "xyt": xyt, "rmk_size": rmk_size,
            "stream": bytes(stream)}


def _decode_board(raw: bytes, keys: Optional[Dict[str, object]], version: int) -> List[int]:
    """还原 32 个棋子的坐标（``0xFF`` 表示该子已被吃掉）。"""
    man = bytearray(raw[:32])
    if keys is None:
        return list(man)
    xy = int(keys["xy"])  # type: ignore[arg-type]
    if version >= 12:
        shuffled = bytearray(32)
        for i in range(32):
            shuffled[(xy + i + 1) & 0x1F] = man[i]
        man = shuffled
    out = []
    for value in man:
        value = (value - xy) & 0xFF
        out.append(0xFF if value > 89 else value)
    return out


def _decode_moves(data: bytes, keys: Optional[Dict[str, object]]) -> bytes:
    if keys is None:
        return bytes(data)
    stream = bytes(keys["stream"])  # type: ignore[arg-type]
    out = bytearray(data)
    for i in range(len(out)):
        out[i] = (out[i] - stream[(0x400 + i) % 32]) & 0xFF
    return bytes(out)


class _Cursor:
    """着法区字节流读取器。"""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def read(self, size: int) -> bytes:
        if size <= 0:
            return b""
        chunk = self.data[self.pos:self.pos + size]
        self.pos += len(chunk)
        return chunk

    def read_int(self) -> int:
        chunk = self.read(4)
        if len(chunk) < 4:
            return 0
        return struct.unpack("<I", chunk)[0]

    def read_text(self, size: int) -> str:
        return _decode_text(self.read(size))

    @property
    def eof(self) -> bool:
        return self.pos >= len(self.data)


def _decode_text(raw: bytes) -> str:
    if not raw:
        return ""
    for encoding in ("gb18030", "utf-8", "latin-1"):
        try:
            return raw.decode(encoding).strip("\x00").strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("gb18030", errors="replace").strip("\x00").strip()


def _board_from_mans(mans: List[int]) -> str:
    """把坐标数组转成 FEN 盘面部分（不含行棋方）。"""
    grid = [["." for _ in range(9)] for _ in range(10)]
    for side, base_kind in ((0, 0), (1, 16)):
        for index in range(16):
            value = mans[base_kind + index]
            if value == 0xFF:
                continue
            file, rank = value // 10, value % 10
            if not (0 <= file < 9 and 0 <= rank < 10):
                continue
            piece = CHESSMAN_KINDS[index]
            grid[rank][file] = piece if side == 0 else piece.lower()
    rows = []
    for rank in range(9, -1, -1):
        empty = 0
        row: List[str] = []
        for file in range(9):
            piece = grid[rank][file]
            if piece == ".":
                empty += 1
            else:
                if empty:
                    row.append(str(empty))
                    empty = 0
                row.append(piece)
        if empty:
            row.append(str(empty))
        rows.append("".join(row))
    return "/".join(rows)


def looks_like_xqf(data: bytes) -> bool:
    return len(data) >= 0x400 and data[:2] == b"XQ"


def read_xqf_bytes(data: bytes) -> GameTree:
    """解析 XQF 字节流，返回棋谱树。"""
    if not looks_like_xqf(data):
        raise XqfError("不是有效的 XQF 文件（缺少 XQ 标识或长度不足）")

    magic, version, crypt, board_raw = struct.unpack("<2sB13s32s", data[:48])
    if magic != b"XQ":
        raise XqfError("不是有效的 XQF 文件")

    # 头部中的文本字段（按规范偏移读取）
    def head_text(offset: int, length_offset: int, max_len: int) -> str:
        size = data[length_offset]
        size = min(size, max_len)
        return _decode_text(data[offset:offset + size]) if size else ""

    result_code = data[0x33]
    game_type = data[0x40] + 1
    title = head_text(0x51, 0x50, 63)
    event = head_text(0xD1, 0xD0, 63)
    date_text = head_text(0x111, 0x110, 15)
    site = head_text(0x121, 0x120, 15)
    red_player = head_text(0x131, 0x130, 15)
    black_player = head_text(0x141, 0x140, 15)
    time_rule = head_text(0x151, 0x150, 63)
    red_time = head_text(0x191, 0x190, 15)
    black_time = head_text(0x1A1, 0x1A0, 15)

    keys = None if version <= 0x0A else _derive(crypt)
    mans = _decode_board(board_raw, keys, version)
    layout = _board_from_mans(mans)
    move_stream = _decode_moves(data[0x400:], keys)

    tree = GameTree(f"{layout} w - - 0 1")
    tree.source = "XQF"
    tree.meta.update({
        "Event": event,
        "Site": site,
        "Date": date_text,
        "Red": red_player,
        "Black": black_player,
        "Result": RESULT_MAP.get(result_code, "*"),
        "Title": title,
        "Type": TYPE_MAP.get(game_type, str(game_type)),
        "TimeRule": time_rule,
        "RedTime": red_time,
        "BlackTime": black_time,
    })

    cursor = _Cursor(move_stream)
    ctx = _XqfContext(version, keys)
    root_comment = _read_leading_comment(cursor, ctx)
    if root_comment:
        tree.root.comment = root_comment

    stats = {"branches": 0, "moves": 0}
    _read_steps(tree, cursor, ctx, tree.root, stats)

    # 起始局面的行棋方由第一步棋的颜色决定
    if tree.root.children:
        first = tree.root.children[0]
        parts = tree.root.fen_after.split()
        parts[1] = first.mover
        fen = " ".join(parts)
        tree.root.fen_after = fen
        _refresh_tree_sides(tree, fen)
    tree.meta["Branches"] = str(stats["branches"])
    tree.meta["Moves"] = str(stats["moves"])
    return tree


def _refresh_tree_sides(tree: GameTree, fen: str) -> None:
    """修正根节点行棋方后，重新计算整棵树的 FEN 链与记谱。"""
    def walk(node, moves: List[str]) -> None:
        board = Board(node.fen_after)
        for iccs in moves:
            move = board.find_move_iccs(iccs)
            if move is None:
                return
            board.push(move)
        node.fen_after = board.to_fen()
        for child in node.children:
            walk(child, [child.iccs])

    walk(tree.root, [])


class _XqfContext:
    """着法区解析上下文（版本、密钥、注释长度因子等）。"""

    __slots__ = ("version", "rmk_size", "xyf", "xyt")

    def __init__(self, version: int, keys: Optional[Dict[str, object]]):
        self.version = version
        self.rmk_size = int(keys["rmk_size"]) if keys else 0  # type: ignore[arg-type]
        self.xyf = int(keys["xyf"]) if keys else 0  # type: ignore[arg-type]
        self.xyt = int(keys["xyt"]) if keys else 0  # type: ignore[arg-type]


def _read_leading_comment(cursor: _Cursor, ctx: _XqfContext) -> str:
    """读取着法区开头的全局注释（棋谱说明）。"""
    step_info = cursor.read(4)
    if len(step_info) < 4:
        return ""
    length = 0
    if ctx.version <= 0x0A:
        length = cursor.read_int()
    elif step_info[2] & 0x20:
        length = cursor.read_int() - ctx.rmk_size
    return cursor.read_text(length) if length > 0 else ""


def _read_steps(tree: GameTree, cursor: _Cursor, ctx: _XqfContext,
                parent, stats: Dict[str, int]) -> None:
    """递归读走着法区（与文件写入时的顺序一一对应）。"""
    step_info = cursor.read(4)
    if len(step_info) < 4:
        return
    board_before = Board(parent.fen_after)
    comment_length = 0
    if ctx.version <= 0x0A:
        has_next = bool(step_info[2] & 0xF0)
        has_variation = bool(step_info[2] & 0x0F)
        comment_length = cursor.read_int()
        from_pos = (step_info[0] - 0x18) & 0xFF
        to_pos = (step_info[1] - 0x20) & 0xFF
    else:
        flags = step_info[2] & 0xE0
        has_next = bool(flags & 0x80)
        has_variation = bool(flags & 0x40)
        if flags & 0x20:
            comment_length = cursor.read_int() - ctx.rmk_size
        from_pos = (step_info[0] - 0x18 - ctx.xyf) & 0xFF
        to_pos = (step_info[1] - 0x20 - ctx.xyt) & 0xFF
    comment = cursor.read_text(comment_length) if comment_length > 0 else ""
    move = _make_move(from_pos, to_pos)
    node = parent
    if move is not None:
        piece = board_before.get(move.ff, move.fr)
        if piece != ".":
            mover = color_of(piece)
            try:
                node = tree.play_from(parent, move, reuse=False, side=mover)
                stats["moves"] += 1
                if comment:
                    node.comment = comment
            except ValueError:
                tree.warnings.append(
                    f"跳过无法解析的着法 {move.iccs()}（第 {parent.ply + 1} 手）")
                node = parent
        else:
            tree.warnings.append(f"跳过落空的着法记录 0x{from_pos:02X} 0x{to_pos:02X}")
    if has_next:
        _read_steps(tree, cursor, ctx, node, stats)
    if has_variation:
        stats["branches"] += 1
        _read_steps(tree, cursor, ctx, parent, stats)


def _make_move(from_pos: int, to_pos: int) -> Optional[Move]:
    if from_pos > 89 or to_pos > 89:
        return None
    ff, fr = from_pos // 10, from_pos % 10
    tf, tr = to_pos // 10, to_pos % 10
    if not (0 <= ff < 9 and 0 <= tf < 9 and 0 <= fr < 10 and 0 <= tr < 10):
        return None
    return Move(ff, fr, tf, tr)


def read_xqf(source: Union[str, Path, bytes]) -> GameTree:
    """读取 XQF 棋谱，``source`` 可以是文件路径或字节串。"""
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    else:
        data = Path(source).read_bytes()
    if len(data) < 0x400:
        raise XqfError("XQF 文件长度不足 1024 字节")
    return read_xqf_bytes(data)


# ==================================================================== 写入
def _encode_board_values(fen: str) -> List[int]:
    """按 XQF 的 32 个棋子槽位顺序取坐标（file*10+rank），吃掉的记 0xFF。"""
    board = Board(fen)
    values: List[int] = []
    for side in (0, 1):
        color = "w" if side == 0 else "b"
        for index in range(16):
            kind = CHESSMAN_KINDS[index]
            piece = kind if side == 0 else kind.lower()
            found = None
            for file, rank, current in board.iter_pieces(color):
                if current == piece:
                    found = (file, rank)
                    break
            if found is None:
                values.append(0xFF)
            else:
                board._set(found[0], found[1], EMPTY_CELL)
                values.append(found[0] * 10 + found[1])
    return values


EMPTY_CELL = "."


def _encode_board(fen: str, keys: Optional[Dict[str, object]]) -> bytes:
    values = _encode_board_values(fen)
    if not keys:
        return bytes(values)
    xy = int(keys["xy"])  # type: ignore[arg-type]
    shuffled = bytearray(32)
    for index, value in enumerate(values):
        shuffled[index] = 0xFF if value == 0xFF else (value + xy) & 0xFF
    raw = bytearray(32)
    for index in range(32):
        raw[index] = shuffled[(xy + index + 1) & 0x1F]
    return bytes(raw)


def _encode_moves(tree: GameTree, keys: Optional[Dict[str, object]], version: int) -> bytes:
    """按文件规范的顺序写出着法区（含变例与注释）。"""
    out = bytearray()
    xyf = int(keys["xyf"]) if keys else 0    # type: ignore[arg-type]
    xyt = int(keys["xyt"]) if keys else 0    # type: ignore[arg-type]
    rmk = int(keys["rmk_size"]) if keys else 0  # type: ignore[arg-type]
    low_version = version <= 0x0A

    def walk(node) -> None:
        children = node.children
        for index, child in enumerate(children):
            move = child.move
            has_next = bool(child.children)
            has_sibling = index + 1 < len(children)
            has_comment = bool(child.comment)
            flags = 0
            if low_version:
                if has_next:
                    flags |= 0xF0
                if has_sibling:
                    flags |= 0x0F
            else:
                if has_next:
                    flags |= 0x80
                if has_sibling:
                    flags |= 0x40
                if has_comment:
                    flags |= 0x20
            out.extend([
                (move.ff * 10 + move.fr + 0x18 + xyf) & 0xFF,
                (move.tf * 10 + move.tr + 0x20 + xyt) & 0xFF,
                flags,
                0,
            ])
            if has_comment or low_version:
                payload = child.comment.encode("gb18030", errors="replace")
                out.extend(struct.pack("<I", len(payload) + rmk))
                if payload:
                    out.extend(payload)
            if has_next:
                walk(child)

    # 开头的"全局注释"记录：新版用标志位 0x20 表示有注释，旧版固定带 4 字节长度
    root_comment = (tree.root.comment or "").encode("gb18030", errors="replace")
    out.extend(bytes([0, 0, 0x20 if (root_comment and not low_version) else 0, 0]))
    if low_version or root_comment:
        out.extend(struct.pack("<I", len(root_comment) + rmk))
        if root_comment:
            out.extend(root_comment)
    walk(tree.root)
    return bytes(out)


def write_xqf(tree: GameTree, target: Optional[Union[str, Path]] = None,
              version: int = 18, salt: int = 7) -> bytes:
    """把棋谱树写成 XQF 文件内容（默认版本 18，带加密；``version<=0x0A`` 为旧格式）。

    传入 ``target`` 时会同时写入文件，并返回字节内容。
    """
    crypt = struct.pack("<BIBBBBBBBB", 0xFF, 0x12345678, 0x11, 0x22, 0x33, 0x44,
                        (salt * 3 + 1) & 0xFF, (salt * 5) & 0xFF,
                        (salt * 11) & 0xFF, (salt * 13) & 0xFF)
    keys = _derive(crypt) if version > 0x0A else None
    header = bytearray(0x400)
    header[0:2] = b"XQ"
    header[2] = version
    header[3:16] = crypt
    header[16:48] = _encode_board(tree.start_fen, keys)
    meta = tree.meta
    header[0x33] = {"1-0": 1, "0-1": 2, "1/2-1/2": 3}.get(
        (meta.get("Result") or "").strip(), 0)

    def put_text(length_offset: int, text_offset: int, text: str, max_len: int) -> None:
        payload = str(text or "").encode("gb18030", errors="replace")[:max_len]
        header[length_offset] = len(payload)
        header[text_offset:text_offset + len(payload)] = payload

    put_text(0x50, 0x51, meta.get("Title") or meta.get("Event") or "", 63)
    put_text(0xD0, 0xD1, meta.get("Event") or "", 63)
    put_text(0x110, 0x111, meta.get("Date") or "", 15)
    put_text(0x120, 0x121, meta.get("Site") or "", 15)
    put_text(0x130, 0x131, meta.get("Red") or "", 15)
    put_text(0x140, 0x141, meta.get("Black") or "", 15)
    put_text(0x150, 0x151, meta.get("TimeRule") or "", 63)
    put_text(0x190, 0x191, meta.get("RedTime") or "", 15)
    put_text(0x1A0, 0x1A1, meta.get("BlackTime") or "", 15)

    body = _encode_moves(tree, keys, version)
    if keys:
        stream = keys["stream"]
        body = bytes((value + stream[(0x400 + i) % 32]) & 0xFF
                     for i, value in enumerate(body))
    data = bytes(header) + body
    if target is not None:
        Path(target).write_bytes(data)
    return data
