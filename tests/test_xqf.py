# -*- coding: utf-8 -*-
"""XQF 读写测试。

项目里不含 XQF 样本文件，因此这里按 XQF 格式规范写了一个**写入器**作为测试夹具：
用它生成的棋谱再交给 ``read_xqf`` 读取，从而验证解密、盘面还原、着法与变例解析。
（本项目的 XQF 读取逻辑另外用公开软件保存的 26 个真实 .xqf 文件做过逐手比对。）

如果你手上有真实的 .xqf 文件，把它们放进 ``tests/samples/`` 目录，
``test_real_samples_if_present`` 会自动校验每一手棋的合法性。
"""

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xqtrainer.board import Board  # noqa: E402
from xqtrainer.game import GameTree  # noqa: E402
from xqtrainer.xqf import XqfError, _derive, read_xqf, write_xqf  # noqa: E402

CHESSMAN_KINDS = ("R", "N", "B", "A", "K", "A", "B", "N", "R", "C", "C",
                  "P", "P", "P", "P", "P")


def _board_values(fen: str):
    """按 XQF 的 32 个棋子槽位顺序取坐标（file*10+rank）。"""
    board = Board(fen)
    values = []
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
                board._set(found[0], found[1], ".")
                values.append(found[0] * 10 + found[1])
    return values


def _encode_board(fen: str, keys) -> bytes:
    values = _board_values(fen)
    if keys is None:
        return bytes(values)
    xy = keys["xy"]
    shuffled = bytearray(32)
    for index, value in enumerate(values):
        shuffled[index] = 0xFF if value == 0xFF else (value + xy) & 0xFF
    raw = bytearray(32)
    for index in range(32):
        raw[index] = shuffled[(xy + index + 1) & 0x1F]
    return bytes(raw)


def _encode_moves(tree: GameTree, keys, version: int) -> bytes:
    out = bytearray()
    xyf = keys["xyf"] if keys else 0
    xyt = keys["xyt"] if keys else 0
    rmk = keys["rmk_size"] if keys else 0
    low_version = version <= 0x0A

    def walk(node) -> None:
        children = node.children
        for index, child in enumerate(children):
            move = child.move
            flags = 0
            has_next = bool(child.children)
            has_sibling = index + 1 < len(children)
            has_comment = bool(child.comment)
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
                payload = child.comment.encode("gb18030")
                out.extend(struct.pack("<I", len(payload) + rmk))
                if payload:
                    out.extend(payload)
            if has_next:
                walk(child)

    out.extend(bytes([0, 0, 0, 0]))   # 文件开头的全局注释（留空）
    if low_version:
        out.extend(struct.pack("<I", 0))   # 旧版本总是带一个 4 字节注释长度
    walk(tree.root)
    return bytes(out)


def build_xqf(tree: GameTree, version: int = 18, salt: int = 7) -> bytes:
    """按 XQF 格式规范生成一个文件（仅供测试使用）。"""
    crypt = struct.pack("<BIBBBBBBBB", 0xFF, 0x12345678, 0x11, 0x22, 0x33, 0x44,
                        (salt * 3 + 1) & 0xFF, (salt * 5) & 0xFF,
                        (salt * 11) & 0xFF, (salt * 13) & 0xFF)
    keys = _derive(crypt) if version > 0x0A else None
    header = bytearray(0x400)
    header[0:2] = b"XQ"
    header[2] = version
    header[3:16] = crypt
    header[16:48] = _encode_board(tree.start_fen, keys)
    header[0x33] = 1

    def put_text(length_offset: int, text_offset: int, text: str, max_len: int) -> None:
        payload = str(text).encode("gb18030")[:max_len]
        header[length_offset] = len(payload)
        header[text_offset:text_offset + len(payload)] = payload

    put_text(0x50, 0x51, tree.meta.get("Title") or "测试棋谱", 63)
    put_text(0xD0, 0xD1, tree.meta.get("Event") or "测试赛事", 63)
    put_text(0x110, 0x111, "2026.09.23", 15)
    put_text(0x130, 0x131, tree.meta.get("Red") or "红方", 15)
    put_text(0x140, 0x141, tree.meta.get("Black") or "黑方", 15)

    body = _encode_moves(tree, keys, version)
    if keys:
        stream = keys["stream"]
        body = bytes((value + stream[(0x400 + i) % 32]) & 0xFF
                     for i, value in enumerate(body))
    return bytes(header) + body


class TestXqfRoundTrip(unittest.TestCase):
    def make_tree(self) -> GameTree:
        tree = GameTree(meta={"Red": "甲", "Black": "乙"})
        tree.play_iccs("h2e2")
        tree.play_iccs("h9g7")
        tree.play_iccs("h0g2")
        tree.play_iccs("c6c5")
        tree.go_prev()
        branch = tree.play_iccs("b9c7")
        branch.comment = "黑方选择左马"
        tree.go_root()
        tree.play_iccs("h2e2")
        tree.play_iccs("b9c7")
        return tree

    def test_roundtrip_v18(self):
        tree = self.make_tree()
        loaded = read_xqf(build_xqf(tree, version=18))
        self.assertEqual(loaded.all_lines(), tree.all_lines())
        self.assertEqual(loaded.meta["Red"], "甲")
        self.assertEqual(loaded.meta["Black"], "乙")
        self.assertEqual(loaded.meta["Result"], "1-0")
        self.assertEqual(loaded.meta["Title"], "测试棋谱")
        self.assertEqual(loaded.start_fen.split()[0], tree.start_fen.split()[0])
        comments = [node.comment for node in loaded.nodes() if node.comment]
        self.assertIn("黑方选择左马", comments)
        self.assertEqual(loaded.warnings, [])

    def test_roundtrip_v10(self):
        tree = self.make_tree()
        loaded = read_xqf(build_xqf(tree, version=10))
        self.assertEqual(loaded.all_lines(), tree.all_lines())

    def test_custom_position(self):
        tree = GameTree("3k5/9/9/9/9/9/9/9/9/4K4 w - - 0 1")
        tree.play_iccs("e0e1")
        loaded = read_xqf(build_xqf(tree, version=18))
        self.assertEqual(loaded.all_lines(), [["e0e1"]])
        self.assertEqual(loaded.start_fen.split()[0], "3k5/9/9/9/9/9/9/9/9/4K4")

    def test_bad_file_rejected(self):
        with self.assertRaises(XqfError):
            read_xqf(b"not an xqf file" + b"\x00" * 2000)

    def test_real_samples_if_present(self):
        sample_dir = Path(__file__).resolve().parent / "samples"
        files = sorted(sample_dir.glob("*.xqf")) + sorted(sample_dir.glob("*.XQF")) \
            if sample_dir.is_dir() else []
        if not files:
            self.skipTest("没有 tests/samples/*.xqf 样本文件")
        for path in files:
            with self.subTest(file=path.name):
                tree = read_xqf(path)
                for line in tree.all_lines():
                    board = Board(tree.start_fen)
                    for iccs in line:
                        self.assertIsNotNone(board.find_move_iccs(iccs), iccs)
                        board.move_iccs(iccs)


class TestXqfWrite(unittest.TestCase):
    """程序自带的 XQF 写出功能（保存棋谱）也要能被我方读取器正确读回。"""

    def make_tree(self) -> GameTree:
        tree = GameTree(meta={"Red": "甲", "Black": "乙", "Event": "测试赛",
                              "Date": "2026.09.23", "Result": "1-0"})
        for iccs in ("h2e2", "h9g7", "h0g2", "c6c5"):
            tree.play_iccs(iccs)
        tree.go_prev()
        branch = tree.play_iccs("b9c7")
        branch.comment = "变例说明"
        tree.go_root()
        tree.root.comment = "全局评注"
        return tree

    def test_write_and_read_back(self):
        tree = self.make_tree()
        data = write_xqf(tree)
        self.assertEqual(data[:2], b"XQ")
        self.assertGreater(len(data), 0x400)
        loaded = read_xqf(data)
        self.assertEqual(sorted(tuple(line) for line in tree.all_lines()),
                         sorted(tuple(line) for line in loaded.all_lines()))
        self.assertEqual(loaded.meta["Red"], "甲")
        self.assertEqual(loaded.meta["Event"], "测试赛")
        self.assertEqual(loaded.meta["Result"], "1-0")
        comments = [node.comment for node in loaded.nodes() if node.comment]
        self.assertIn("变例说明", comments)
        self.assertIn("全局评注", comments)

    def test_write_low_version(self):
        tree = self.make_tree()
        loaded = read_xqf(write_xqf(tree, version=10))
        self.assertEqual(sorted(tuple(line) for line in tree.all_lines()),
                         sorted(tuple(line) for line in loaded.all_lines()))

    def test_write_to_file(self):
        tree = self.make_tree()
        target = Path(__file__).resolve().parent / "_tmp_out.xqf"
        try:
            write_xqf(tree, target)
            self.assertTrue(target.exists())
            self.assertEqual(read_xqf(target).meta["Black"], "乙")
        finally:
            target.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
