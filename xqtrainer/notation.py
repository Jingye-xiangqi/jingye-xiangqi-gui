# -*- coding: utf-8 -*-
"""记谱模块：ICCS、WXF 与中文记谱的生成与解析。

三种记谱都指向同一盘棋：

* **ICCS**（``h2e2``）：国际通用的坐标记谱，也是 UCI 协议使用的格式，无歧义；
* **WXF**（``C2.5``、``H2+3``）：世界象棋联合会记谱，纵线用 1-9 表示；
* **中文记谱**（``炮二平五``）：中文棋谱最常用的记谱，红黑各从己方右手边数纵线。

解析采用"生成并匹配"的方式：先枚举当前局面的全部合法着法，算出它们的记谱文本，
再与输入文本比对，因此不会出现歧义或错判。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .board import (BLACK, PIECE_NAMES_SIMPLE, PIECE_NAMES_TRAD, RED, Board, Move,
                    color_of)

CN_DIGITS = "一二三四五六七八九"
AN_DIGITS = "0123456789"

#: 记谱中使用的棋子名（繁/简）
GLYPHS = {"simple": PIECE_NAMES_SIMPLE, "trad": PIECE_NAMES_TRAD}

#: 各家记谱里可能出现的异体字，统一成简化字后匹配
GLYPH_ALIASES = {
    "車": "车", "俥": "车", "馬": "马", "傌": "马", "砲": "炮", "包": "炮",
    "帥": "帅", "將": "将", "士": "仕", "仕": "仕", "象": "相", "相": "相",
    "卒": "卒", "兵": "兵",
    "進": "进", "後": "后", "前": "前", "中": "中", "退": "退", "平": "平",
}


def file_number(color: str, file: int) -> int:
    """纵线编号：红方从己方右手边数（i=一 ... a=九），黑方从己方右手边数（a=1 ... i=9）。"""
    return (9 - file) if color == RED else (file + 1)


def cn_number(value: int) -> str:
    """1-9 转中文数字，超出范围时退化为阿拉伯数字。"""
    if 1 <= value <= 9:
        return CN_DIGITS[value - 1]
    return str(value)


def number_text(color: str, value: int) -> str:
    """中文棋谱的通行写法：红方用中文数字（炮二平五），黑方用阿拉伯数字（炮8平5）。"""
    return cn_number(value) if color == RED else str(value)


def _same_file_pieces(board: Board, color: str, kind: str, file: int) -> List[Tuple[int, int, str]]:
    """同一纵线上同类棋子，按"由前到后"排序（前的离对方更近）。"""
    items = [(f, r, p) for f, r, p in board.iter_pieces(color)
             if p.upper() == kind and f == file]
    items.sort(key=lambda item: item[1], reverse=(color == RED))
    return items


def piece_label(board: Board, move: Move, traditional: bool = False) -> str:
    """棋子称谓，例如 ``炮二``、``前车``、``后卒``。"""
    names = GLYPHS["trad" if traditional else "simple"]
    color = color_of(move.piece)
    kind = move.piece.upper()
    name = names[move.piece]
    group = _same_file_pieces(board, color, kind, move.ff)
    if len(group) > 1:
        order = [item for item in group if (item[0], item[1]) == (move.ff, move.fr)]
        index = group.index(order[0]) if order else 0
        if kind == "P":
            if len(group) == 2:
                prefix = "前" if index == 0 else "后"
            elif len(group) == 3:
                prefix = ("前", "中", "后")[index]
            else:
                prefix = number_text(color, index + 1)
        else:
            prefix = "前" if index == 0 else ("后" if index == len(group) - 1 else "中")
        return prefix + name
    return name + number_text(color, file_number(color, move.ff))


def to_chinese(board: Board, move: Move, traditional: bool = False) -> str:
    """生成中文记谱，例如 ``炮二平五``、``马8进7``。"""
    color = color_of(move.piece)
    kind = move.piece.upper()
    label = piece_label(board, move, traditional)
    if move.fr == move.tr:
        return label + "平" + number_text(color, file_number(color, move.tf))
    forward = (move.tr > move.fr) if color == RED else (move.tr < move.fr)
    verb = "进" if forward else "退"
    if kind in ("N", "B", "A"):
        return label + verb + number_text(color, file_number(color, move.tf))
    return label + verb + number_text(color, abs(move.tr - move.fr))


def to_wxf(board: Board, move: Move) -> str:
    """生成 WXF 记谱，例如 ``C2.5``、``H2+3``、``E3+5``。"""
    color = color_of(move.piece)
    kind = move.piece.upper()
    group = _same_file_pieces(board, color, kind, move.ff)
    prefix = ""
    if len(group) > 1:
        order = [item for item in group if (item[0], item[1]) == (move.ff, move.fr)]
        index = group.index(order[0]) if order else 0
        prefix = "+" if index == 0 else ("-" if index == len(group) - 1 else "+-")
    head = prefix + WXF_LETTERS.get(kind, kind) + str(file_number(color, move.ff))
    if move.fr == move.tr:
        return head + "." + str(file_number(color, move.tf))
    forward = (move.tr > move.fr) if color == RED else (move.tr < move.fr)
    sign = "+" if forward else "-"
    if kind in ("N", "B", "A"):
        return head + sign + str(file_number(color, move.tf))
    return head + sign + str(abs(move.tr - move.fr))


def to_iccs(move: Move) -> str:
    return move.iccs()


def describe(board: Board, move: Move) -> Dict[str, object]:
    """返回一步棋的全部表示，便于界面与导出使用。"""
    color = color_of(move.piece)
    return {
        "iccs": move.iccs(),
        "cn": to_chinese(board, move),
        "cn_trad": to_chinese(board, move, traditional=True),
        "wxf": to_wxf(board, move),
        "from": move.from_name,
        "to": move.to_name,
        "piece": move.piece,
        "captured": None if move.captured == "." else move.captured,
        "side": color,
    }


# ---------------------------------------------------------------- 解析
_ICCS_RE = re.compile(r"^([a-i])([0-9])([a-i])([0-9])$")
_WXF_RE = re.compile(r"^([+-]{0,2})([KABEHNRCP])([1-9])([.\-+])([1-9])$", re.IGNORECASE)

#: WXF 记谱的棋子字母（马用 H，相/象用 E），这里同时兼容 N / B 写法
WXF_LETTERS = {"K": "K", "A": "A", "B": "E", "N": "H", "R": "R", "C": "C", "P": "P"}
WXF_ALIASES = {"N": "H", "B": "E"}


def _normalize_cn(text: str) -> str:
    out = []
    for ch in text:
        if ch in GLYPH_ALIASES:
            out.append(GLYPH_ALIASES[ch])
        elif ch in "０１２３４５６７８９":
            out.append(str("０１２３４５６７８９".index(ch)))
        else:
            out.append(ch)
    text = "".join(out)
    for i, digit in enumerate(CN_DIGITS, start=1):
        text = text.replace(digit, str(i))
    return text.replace(" ", "").strip()


def parse_iccs(board: Board, text: str) -> Optional[Move]:
    text = (text or "").strip().lower()
    if not _ICCS_RE.match(text):
        return None
    return board.find_move(text[:2], text[2:])


def parse_wxf(board: Board, text: str) -> Optional[Move]:
    text = (text or "").strip().upper()
    if not _WXF_RE.match(text):
        return None
    target = _normalize_wxf(text)
    for move in board.legal_moves():
        if _normalize_wxf(to_wxf(board, move)) == target:
            return move
    return None


def _normalize_wxf(text: str) -> str:
    """统一 WXF 记谱中的等价写法（N→H、B→E）。"""
    text = text.replace(" ", "").upper()
    for index, ch in enumerate(text):
        if ch.isalpha() and ch in WXF_ALIASES:
            text = text[:index] + WXF_ALIASES[ch] + text[index + 1:]
            break
    return text


def parse_chinese(board: Board, text: str) -> Optional[Move]:
    target = _normalize_cn(text or "")
    if len(target) < 3:
        return None
    for move in board.legal_moves():
        if _normalize_cn(to_chinese(board, move)) == target:
            return move
    return None


def parse_move(board: Board, text: str) -> Optional[Move]:
    """按 ICCS → WXF → 中文记谱的顺序解析一步棋，失败返回 ``None``。"""
    text = (text or "").strip().rstrip(".,;")
    if not text or text in ("*", "-", "1-0", "0-1", "1/2-1/2"):
        return None
    for parser in (parse_iccs, parse_wxf, parse_chinese):
        move = parser(board, text)
        if move is not None:
            return move
    return None


def looks_like_move(text: str) -> bool:
    """判断一个 token 是否可能是着法（用于 PGN 词法分析）。"""
    text = (text or "").strip()
    if not text:
        return False
    if _ICCS_RE.match(text.lower()):
        return True
    if _WXF_RE.match(text.upper()):
        return True
    return bool(re.match(r"^[前后中一二三四五六七八九]?[车马炮兵卒仕士相象帅将帥車馬砲]", text))
