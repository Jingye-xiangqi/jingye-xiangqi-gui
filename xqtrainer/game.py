# -*- coding: utf-8 -*-
"""棋谱树：主线、变例、PGN 导入导出。

棋谱用一棵树保存。根节点表示起始局面，每个子节点表示一步棋；第一个子节点是主线，
其余子节点是变例分支。这样"打谱 + 记录变例"天然被支持，也便于导出成带括号变例的 PGN。
"""

from __future__ import annotations

import re
from datetime import date
from typing import Dict, Iterator, List, Optional, Tuple

from . import notation as nt
from .board import (BLACK, RED, START_FEN, Board, Move, color_of, side_name)

DEFAULT_RESULT = "*"


def decode_bytes(data: bytes) -> str:
    """解码棋谱文本：UTF-8 / UTF-16（记事本"Unicode"保存）/ GB18030 / Big5 都试。"""
    if data[:3] == b"\xef\xbb\xbf":
        return data.decode("utf-8-sig", errors="replace")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):          # 记事本另存"Unicode"时的 BOM
        return data.decode("utf-16", errors="replace")
    for encoding in ("utf-8", "gb18030", "big5", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("gb18030", errors="replace")


class Node:
    """棋谱树中的一个节点（一步棋，或根节点的起始局面）。"""

    __slots__ = ("id", "move", "parent", "children", "comment", "fen_before", "fen_after",
                 "iccs", "cn", "cn_trad", "wxf", "mover", "captured", "is_check", "is_mate",
                 "ply", "name", "score")

    def __init__(self, node_id: int, fen_before: str, parent: Optional["Node"] = None):
        self.id = node_id
        self.move: Optional[Move] = None
        self.parent = parent
        self.children: List["Node"] = []
        self.comment = ""
        self.fen_before = fen_before
        self.fen_after = fen_before
        self.iccs = ""
        self.cn = ""
        self.cn_trad = ""
        self.wxf = ""
        self.mover = ""
        self.captured: Optional[str] = None
        self.is_check = False
        self.is_mate = False
        self.ply = 0 if parent is None else parent.ply + 1
        self.name = ""
        self.score: Optional[str] = None

    # --- 便捷属性 ---
    @property
    def is_root(self) -> bool:
        return self.parent is None

    @property
    def move_number(self) -> int:
        return (self.ply + 1) // 2 if not self.is_root else 0

    @property
    def is_mainline(self) -> bool:
        node: Optional[Node] = self
        while node is not None and node.parent is not None:
            if node.parent.children and node.parent.children[0] is not node:
                return False
            node = node.parent
        return True

    def add_child(self, child: "Node") -> None:
        child.parent = self
        child.ply = self.ply + 1
        self.children.append(child)

    def remove_child(self, child: "Node") -> None:
        if child in self.children:
            self.children.remove(child)
            child.parent = None

    def to_dict(self, max_nodes: int = 4000, counter: Optional[List[int]] = None) -> Dict[str, object]:
        """序列化成界面用的嵌套结构（超大树会截断，避免界面卡顿）。"""
        counter = counter if counter is not None else [0]
        counter[0] += 1
        data = {
            "id": self.id,
            "iccs": self.iccs,
            "cn": self.cn,
            "wxf": self.wxf,
            "ply": self.ply,
            "mover": self.mover,
            "captured": self.captured,
            "check": self.is_check,
            "mate": self.is_mate,
            "comment": self.comment,
            "main": self.parent is not None and self.parent.children[:1] == [self],
            "children": [],
        }
        for child in self.children:
            if counter[0] >= max_nodes:
                data["truncated"] = True
                break
            data["children"].append(child.to_dict(max_nodes, counter))
        return data


class GameTree:
    """棋谱树，包含起始局面、全部着法与变例。"""

    def __init__(self, fen: str = START_FEN, meta: Optional[Dict[str, str]] = None):
        self._next_id = 0
        self.root = self._new_node(fen, None)
        self.current = self.root
        self.meta: Dict[str, str] = {"Event": "", "Site": "", "Date": "", "Round": "",
                                     "Red": "", "Black": "", "Result": DEFAULT_RESULT}
        if meta:
            self.meta.update({k: str(v) for k, v in meta.items() if v is not None})
        self.warnings: List[str] = []
        self.source = ""

    # ------------------------------------------------------------ 基础
    def _new_node(self, fen_before: str, parent: Optional[Node]) -> Node:
        node = Node(self._next_id, fen_before, parent)
        self._next_id += 1
        return node

    @property
    def start_fen(self) -> str:
        return self.root.fen_after

    def node_by_id(self, node_id: int) -> Optional[Node]:
        stack = [self.root]
        while stack:
            node = stack.pop()
            if node.id == node_id:
                return node
            stack.extend(node.children)
        return None

    def nodes(self) -> Iterator[Node]:
        stack = [self.root]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))

    def count_nodes(self) -> int:
        return sum(1 for _ in self.nodes())

    def line_to(self, node: Optional[Node] = None) -> List[Node]:
        """从根到指定节点（含两端）的路径。"""
        node = node or self.current
        path: List[Node] = []
        while node is not None:
            path.append(node)
            node = node.parent
        path.reverse()
        return path

    def path_iccs(self, node: Optional[Node] = None) -> List[str]:
        return [n.iccs for n in self.line_to(node) if not n.is_root]

    def board_at(self, node: Optional[Node] = None) -> Board:
        node = node or self.current
        return Board(node.fen_after)

    def position_key(self, node: Optional[Node] = None) -> str:
        return " ".join(self.board_at(node).to_fen().split()[:2])

    def repetition_count(self, node: Optional[Node] = None) -> int:
        """当前局面在整条主路径上出现的次数（用于三次重复局面提示）。"""
        node = node or self.current
        key = self.position_key(node)
        return sum(1 for n in self.line_to(node) if self.position_key(n) == key)

    # ------------------------------------------------------------ 走子
    def play(self, move: Move, reuse: bool = True) -> Node:
        """在当前节点下走出一步棋；若该变例已存在则直接进入（``reuse``）。"""
        child = self.play_from(self.current, move, reuse=reuse)
        self.current = child
        return child

    def play_from(self, node: Node, move: Move, reuse: bool = True,
                  side: Optional[str] = None) -> Node:
        """在 ``node`` 之后添加一步棋。``side`` 用于指定走子方（导入老棋谱时容错）。"""
        if reuse:
            for child in node.children:
                if child.move is not None and child.move == move:
                    return child
        child = self.build_child(node, move, side=side)
        node.add_child(child)
        return child

    def build_child(self, node: Node, move: Move, side: Optional[str] = None) -> Node:
        board = Board(node.fen_after)
        if side is not None:
            board.side = side
        legal = board.find_move(move.from_name, move.to_name)
        if legal is None:
            raise ValueError(f"非法着法：{move.iccs()}（局面 {node.fen_after}）")
        info = nt.describe(board, legal)
        board.push(legal)
        child = self._new_node(node.fen_after, node)
        child.move = legal
        child.iccs = str(info["iccs"])
        child.cn = str(info["cn"])
        child.cn_trad = str(info["cn_trad"])
        child.wxf = str(info["wxf"])
        child.mover = str(info["side"])
        child.captured = info["captured"]  # type: ignore[assignment]
        child.fen_after = board.to_fen()
        child.is_mate = board.result() is not None
        child.is_check = board.is_check(board.side)
        return child

    def play_iccs(self, text: str, reuse: bool = True) -> Optional[Node]:
        board = self.board_at()
        move = board.find_move_iccs(text)
        if move is None:
            return None
        return self.play(move, reuse=reuse)

    def delete_subtree(self, node: Node) -> bool:
        """删除某个节点及其全部后续（根节点不可删除）。"""
        if node.is_root or node.parent is None:
            return False
        parent = node.parent
        parent.remove_child(node)
        if self.current is node or self._is_descendant(self.current, node):
            # 当前着法在删除范围内时，退回到被删子树之前的局面
            self.current = parent
        return True

    @staticmethod
    def _is_descendant(node: Node, ancestor: Node) -> bool:
        cur: Optional[Node] = node
        while cur is not None:
            if cur is ancestor:
                return True
            cur = cur.parent
        return False

    def promote_variation(self, node: Node) -> bool:
        """把某个变例提升为主线（放在兄弟节点的最前面）。"""
        if node.parent is None:
            return False
        parent = node.parent
        if parent.children and parent.children[0] is node:
            return False
        parent.remove_child(node)
        node.parent = parent
        parent.children.insert(0, node)
        return True

    # ------------------------------------------------------------ 导航
    def goto(self, node: Node) -> Node:
        self.current = node
        return self.current

    def goto_id(self, node_id: int) -> Node:
        node = self.node_by_id(node_id)
        return self.goto(node) if node is not None else self.current

    def go_root(self) -> Node:
        self.current = self.root
        return self.current

    def go_prev(self) -> Node:
        if self.current.parent is not None:
            self.current = self.current.parent
        return self.current

    def go_next(self, variation: int = 0) -> Node:
        children = self.current.children
        if not children:
            return self.current
        index = min(max(variation, 0), len(children) - 1)
        self.current = children[index]
        return self.current

    def go_end(self, variation: int = 0) -> Node:
        while self.current.children:
            self.go_next(variation)
        return self.current

    def go_depth(self, depth: int) -> Node:
        self.go_root()
        for _ in range(depth):
            if not self.current.children:
                break
            self.go_next()
        return self.current

    # ------------------------------------------------------------ 变例查询
    def variations_at(self, node: Optional[Node] = None) -> List[Dict[str, object]]:
        node = node or self.current
        out = []
        for child in node.children:
            out.append({
                "id": child.id,
                "iccs": child.iccs,
                "cn": child.cn,
                "comment": child.comment,
                "line_length": self.line_length(child),
            })
        return out

    def line_length(self, node: Node) -> int:
        """从该节点起主线还能走多少步。"""
        length = 0
        cur = node
        while cur.children:
            cur = cur.children[0]
            length += 1
        return length

    def all_lines(self) -> List[List[str]]:
        """所有根到叶的着法线路（ICCS），首个元素是主线。"""
        lines: List[List[str]] = []

        def walk(node: Node, path: List[str]) -> None:
            if not node.children:
                if path:
                    lines.append(list(path))
                return
            for child in node.children:
                path.append(child.iccs)
                walk(child, path)
                path.pop()

        walk(self.root, [])
        return lines

    # ------------------------------------------------------------ 结果判定
    def detect_result(self) -> str:
        """推断棋局结果：只看主线末尾（变例里的将死不影响结果），
        主线没分出胜负时再用棋谱标签里的 Result。"""
        node = self.root
        while node.children:
            node = node.children[0]
        board = Board(node.fen_after)
        winner = board.result()          # result() 返回的是胜方（"red" / "black"）
        if winner is not None:
            return "1-0" if winner == "red" else "0-1"
        meta_result = (self.meta.get("Result") or "").strip()
        if meta_result and meta_result != DEFAULT_RESULT:
            return meta_result
        return DEFAULT_RESULT

    def move_text(self, node: Node, style: str = "iccs") -> str:
        if style == "cn":
            return node.cn
        if style == "trad":
            return node.cn_trad
        if style == "wxf":
            return node.wxf
        return node.iccs

    # ------------------------------------------------------------ PGN 导出
    def to_pgn(self, style: str = "iccs", wrap: int = 80) -> str:
        """导出 PGN。``style`` 可选 iccs / cn / trad / wxf。"""
        result = self.detect_result()
        meta = dict(self.meta)
        if not meta.get("Date"):
            meta["Date"] = date.today().strftime("%Y.%m.%d")
        tags = [
            ("Game", meta.get("Game", "Chinese Chess")),
            ("Variant", "Xiangqi"),
            ("Event", meta.get("Event", "")),
            ("Site", meta.get("Site", "")),
            ("Date", meta.get("Date", "")),
            ("Round", meta.get("Round", "")),
            ("Red", meta.get("Red", "")),
            ("Black", meta.get("Black", "")),
            ("Result", result),
        ]
        if self.start_fen.split()[:2] != START_FEN.split()[:2]:
            tags.append(("FEN", self.start_fen))
        header = "\n".join(f'[{name} "{value}"]' for name, value in tags if value != "")

        tokens: List[str] = []
        if self.root.comment:
            tokens.append("{" + self.root.comment + "}")
        self._emit_chain(self.root, style, tokens)
        tokens.append(result)

        body = ""
        line = ""
        for token in tokens:
            if line and len(line) + len(token) + 1 > wrap:
                body += line + "\n"
                line = token
            else:
                line = (line + " " + token) if line else token
        body += line
        return f"{header}\n\n{body}\n"

    def _emit_chain(self, node: Node, style: str, tokens: List[str],
                    need_black_number: bool = False) -> None:
        cur = node
        first = True
        while cur.children:
            child = cur.children[0]
            number = child.move_number
            if child.mover == RED:
                tokens.append(f"{number}.")
            elif first and need_black_number:
                tokens.append(f"{number}...")
            tokens.append(self.move_text(child, style))
            if child.comment:
                tokens.append("{" + child.comment + "}")
            for variation in cur.children[1:]:
                sub: List[str] = []
                vnumber = variation.move_number
                sub.append(f"{vnumber}." if variation.mover == RED else f"{vnumber}...")
                sub.append(self.move_text(variation, style))
                if variation.comment:
                    sub.append("{" + variation.comment + "}")
                self._emit_chain(variation, style, sub)
                tokens.append("(" + " ".join(sub) + ")")
            cur = child
            first = False

    # ------------------------------------------------------------ 界面序列化
    def to_dict(self, max_nodes: int = 4000) -> Dict[str, object]:
        return {
            "root": self.root.to_dict(max_nodes),
            "current": self.current.id,
            "meta": dict(self.meta),
            "result": self.detect_result(),
            "start_fen": self.start_fen,
            "nodes": self.count_nodes(),
            "warnings": list(self.warnings),
        }


# ==================================================================== PGN 解析
_TAG_RE = re.compile(r'^\[\s*([A-Za-z_][\w]*)\s+"(.*)"\s*\]\s*$')
_MOVE_NUMBER_RE = re.compile(r"^\d+\s*\.+(\s*\.\.\.)?$")
_RESULT_TOKENS = {"1-0", "0-1", "1/2-1/2", "*"}

#: ICCS 着法的几种常见写法：C3-C4 / C3xC4 / c3-c4 / c3 c4
_ICCS_TOKEN_RE = re.compile(r"^([A-Ia-i])([0-9])[\-xX]?\s*([A-Ia-i])([0-9])$")


def _normalize_move_token(token: str) -> str:
    """把各种写法的着法统一成标准 ICCS（小写、无分隔符），例如 ``C3-C4`` → ``c3c4``。"""
    text = str(token or "").strip()
    match = _ICCS_TOKEN_RE.match(text)
    if match:
        return (match.group(1) + match.group(2) + match.group(3) + match.group(4)).lower()
    return text


def _split_games(text: str) -> List[Tuple[Dict[str, str], str]]:
    """把多局 PGN 拆成 (标签, 着法文本) 列表。"""
    games: List[Tuple[Dict[str, str], str]] = []
    tags: Dict[str, str] = {}
    body: List[str] = []
    seen_body = False

    def flush() -> None:
        nonlocal tags, body, seen_body
        if tags or any(part.strip() for part in body):
            games.append((dict(tags), "\n".join(body)))
        tags = {}
        body = []
        seen_body = False

    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        match = _TAG_RE.match(line)
        if match and not seen_body:
            tags[match.group(1)] = match.group(2)
            continue
        if match and seen_body:
            flush()
            tags[match.group(1)] = match.group(2)
            continue
        if line:
            seen_body = True
        body.append(raw_line)
    flush()
    return games


def _tokenize(text: str) -> List[str]:
    """把 PGN 着法文本切成 token（注释合并为 {..}）。"""
    tokens: List[str] = []
    buffer: List[str] = []
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if ch == "{":
            if "".join(buffer).strip():
                tokens.extend("".join(buffer).split())
                buffer = []
            end = text.find("}", i + 1)
            end = length if end == -1 else end
            tokens.append("{" + text[i + 1:end] + "}")
            i = end + 1
            continue
        if ch == ";":
            end = text.find("\n", i)
            end = length if end == -1 else end
            if "".join(buffer).strip():
                tokens.extend("".join(buffer).split())
                buffer = []
            tokens.append("{" + text[i + 1:end].strip() + "}")
            i = end + 1
            continue
        if ch in "()":
            if "".join(buffer).strip():
                tokens.extend("".join(buffer).split())
                buffer = []
            tokens.append(ch)
            i += 1
            continue
        buffer.append(ch)
        i += 1
    if "".join(buffer).strip():
        tokens.extend("".join(buffer).replace(",", " ").split())
    return tokens


def parse_pgn(text: str, fen: Optional[str] = None) -> List[GameTree]:
    """解析 PGN（支持 ICCS / WXF / 中文记谱、注释与括号变例，可含多局）。"""
    games: List[GameTree] = []
    for tags, body in _split_games(text):
        start = fen or tags.get("FEN") or START_FEN
        meta = {
            "Event": tags.get("Event", ""),
            "Site": tags.get("Site", ""),
            "Date": tags.get("Date", ""),
            "Round": tags.get("Round", ""),
            "Red": tags.get("Red", tags.get("White", "")),
            "Black": tags.get("Black", tags.get("BlackName", "")),
            "Result": tags.get("Result", DEFAULT_RESULT),
        }
        tree = GameTree(start, meta=meta)
        tree.source = "PGN"
        _apply_movetext(tree, body)
        games.append(tree)
    return games


def parse_wxf(text: str, fen: Optional[str] = None) -> List[GameTree]:
    """解析 WXF 文本棋谱（世界象棋联合会记谱）。

    兼容几种常见写法：

    * 带 PGN 风格标签的文件（``[Red "甲"]`` 等）；
    * 纯着法列表，例如 ``1. C2.5 H8+7 2. H2+3``；
    * 首行是 FEN（自定义起始局面）的文件。
    """
    games: List[GameTree] = []
    for tags, body in _split_games(text):
        start = fen or tags.get("FEN") or START_FEN
        body_lines: List[str] = []
        for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            stripped = line.strip()
            head = stripped.split()[0] if stripped.split() else ""
            if re.match(r"^[rnbakcpRNBAKCP1-9/]+$", head) and head.count("/") == 9:
                start = stripped          # 首行（或任意一行）是 FEN：作为起始局面
                continue
            body_lines.append(line)
        tree = GameTree(start, meta={
            "Event": tags.get("Event", ""),
            "Site": tags.get("Site", ""),
            "Date": tags.get("Date", ""),
            "Round": tags.get("Round", ""),
            "Red": tags.get("Red", tags.get("White", "")),
            "Black": tags.get("Black", ""),
            "Result": tags.get("Result", DEFAULT_RESULT),
        })
        tree.source = "WXF"
        _apply_movetext(tree, "\n".join(body_lines))
        games.append(tree)
    if not games:
        games = [GameTree(fen or START_FEN)]
    return games


def _apply_movetext(tree: GameTree, movetext: str) -> None:
    tokens = _tokenize(movetext)
    stack: List[Node] = []
    node = tree.root
    board = Board(node.fen_after)
    for token in tokens:
        if token == "(":
            stack.append(node)
            node = node.parent if node.parent is not None else node
            board = Board(node.fen_after)
            continue
        if token == ")":
            node = stack.pop() if stack else tree.root
            board = Board(node.fen_after)
            continue
        if token.startswith("{"):
            comment = token[1:-1].strip()
            if comment:
                node.comment = (node.comment + " " + comment).strip()
            continue
        if token.startswith("$") or _MOVE_NUMBER_RE.match(token) or token in _RESULT_TOKENS:
            continue
        move = nt.parse_move(board, _normalize_move_token(token))
        if move is None:
            if nt.looks_like_move(token):
                tree.warnings.append(f"无法识别着法 {token!r}（第 {node.ply + 1} 手）")
            continue
        child = tree.play_from(node, move, reuse=True)
        node = child
        board = Board(node.fen_after)
    tree.current = tree.root
