# -*- coding: utf-8 -*-
"""皮肤：skin.json 对齐参数解析、目录扫描、棋盘图网格自动检测。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from xqtrainer.ui import skin as skin_module  # noqa: E402


def make_piece(path: Path) -> None:
    """造一张"圆形居中"的棋子图（和转出来的 PNG 形状一致）。"""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse([2, 2, 38, 38], fill=(240, 235, 220, 255),
                                  outline=(120, 90, 60, 255), width=2)
    image.save(path)


def make_board_with_grid(path: Path, left: int = 59, top: int = 79, cell: int = 70,
                         canvas=(900, 1000)) -> None:
    """造一张"棋盘 + 桌子"的图：8×9 网格从 (left, top) 开始，右下留透明边。"""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", canvas, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    grid_w, grid_h = cell * 8, cell * 9
    draw.rectangle([0, 0, left + grid_w + 30, top + grid_h + 30], fill=(25, 50, 35, 255))
    draw.rectangle([left, top, left + grid_w, top + grid_h], fill=(205, 180, 145, 255))
    for index in range(10):
        y = top + index * cell
        draw.line([left, y, left + grid_w, y], fill=(70, 45, 25, 255), width=3)
    for index in range(9):
        x = left + index * cell
        if index in (0, 8):
            draw.line([x, top, x, top + grid_h], fill=(70, 45, 25, 255), width=3)
        else:
            draw.line([x, top, x, top + cell * 4], fill=(70, 45, 25, 255), width=3)
            draw.line([x, top + cell * 5, x, top + grid_h], fill=(70, 45, 25, 255), width=3)
    image.save(path)


def make_skin_folder(root: Path, name: str, with_board: bool = False,
                     with_json: bool = False, complete: bool = True) -> Path:
    """造一套皮肤文件夹（棋子齐全 + 可选棋盘图 / skin.json）。"""
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    for side in ("r", "b"):
        for piece in "kabnrcp":
            if not complete and piece == "n":
                continue
            make_piece(folder / f"{side}{piece}.png")
    if with_board or with_json:
        make_board_with_grid(folder / "board.png")
    if with_json:
        (folder / "skin.json").write_text(json.dumps(
            {"board": "board.png", "pieceScale": 0.9,
             "grid": [59, 79, 560, 630]}, ensure_ascii=False), encoding="utf-8")
    return folder


class TestSkinJson(unittest.TestCase):
    """skin.json 的各种写法都要认（用户批量生成时格式可能略有差别）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xq_json_"))
        self.image_size = (900, 1000)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rect(self, payload):
        return skin_module.resolve_grid_rect(payload, self.image_size)

    def test_rect_as_list_of_pixels(self):
        self.assertEqual(self._rect({"grid": [59, 79, 560, 630]}),
                         (59.0, 79.0, 619.0, 709.0))

    def test_rect_as_list_of_fractions(self):
        rect = self._rect({"gridRect": [0.066, 0.079, 0.622, 0.63]})
        self.assertAlmostEqual(rect[0], 59.4, delta=1)
        self.assertAlmostEqual(rect[3], 709.0, delta=2)

    def test_rect_with_named_edges(self):
        self.assertEqual(self._rect({"board_rect": {"left": 59, "top": 79,
                                                    "right": 619, "bottom": 709}}),
                         (59, 79, 619, 709))

    def test_rect_with_left_top_width_height(self):
        self.assertEqual(self._rect({"grid": {"x": 59, "y": 79, "w": 560, "h": 630}}),
                         (59, 79, 619, 709))

    def test_margins(self):
        self.assertEqual(self._rect({"margins": {"left": 59, "top": 79,
                                                 "right": 281, "bottom": 291}}),
                         (59, 79, 619, 709))

    def test_origin_and_cell(self):
        self.assertEqual(self._rect({"origin": [59, 79], "cell": 70}),
                         (59, 79, 59 + 560, 79 + 630))

    def test_left_top_right_bottom_list_is_detected(self):
        """四元列表既可能是 [x,y,w,h] 也可能是 [l,t,r,b]，要挑更像方格的那个。"""
        self.assertEqual(self._rect({"grid": [59, 79, 619, 709]}), (59, 79, 619, 709))

    def test_piece_scale_and_adjust(self):
        self.assertAlmostEqual(skin_module.parse_piece_scale({"pieceScale": 0.88}), 0.88)
        self.assertAlmostEqual(skin_module.parse_piece_scale({"pieceScale": 92}), 0.92)
        self.assertAlmostEqual(skin_module.parse_piece_scale({}), 0.92)
        self.assertEqual(skin_module.parse_adjust({"adjust": {"dx": 0.1, "dy": -0.2,
                                                              "scale": 110}}),
                         {"dx": 0.1, "dy": -0.2, "scale": 110.0})
        self.assertEqual(skin_module.parse_adjust({}), {})

    def test_sanity_checks(self):
        self.assertFalse(skin_module._rect_is_sane((10, 10, 810, 400), self.image_size),
                         "格子宽高差太多（101×43）→ 不可信")
        self.assertFalse(skin_module._rect_is_sane((10, 10, 90, 100), self.image_size),
                         "网格太小 → 不可信")
        self.assertTrue(skin_module._rect_is_sane((59, 79, 619, 709), self.image_size))

    def test_skin_reads_json(self):
        folder = make_skin_folder(self.tmp, "0-带参数", with_json=True)
        candidate = skin_module.Skin(folder)
        self.assertEqual(candidate.grid_source, "skin.json")
        self.assertEqual(candidate.grid_rect, (59, 79, 619, 709))
        self.assertAlmostEqual(candidate.piece_scale, 0.9)
        self.assertTrue(candidate.ok)

    def test_skin_without_json_falls_back_to_auto(self):
        folder = make_skin_folder(self.tmp, "1-没参数", with_board=True)
        candidate = skin_module.Skin(folder)
        self.assertIsNone(candidate.grid_rect)
        candidate.resolve_grid()
        self.assertEqual(candidate.grid_source, "auto", candidate.grid_rect)
        self.assertIsNotNone(candidate.grid_rect)

    def test_broken_json_does_not_break_the_skin(self):
        folder = make_skin_folder(self.tmp, "2-坏参数", with_board=True)
        (folder / "skin.json").write_text("{ 这不是 json", encoding="utf-8")
        candidate = skin_module.Skin(folder)
        self.assertTrue(candidate.ok)
        self.assertIsNone(candidate.grid_rect)
        self.assertEqual(candidate.piece_scale, 0.92)


class TestBatchSkin(unittest.TestCase):
    """批量转换出来的皮肤：background.png + 比例式 skin.json（当前 78 套用的格式）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xq_batch_"))
        self.folder = self.tmp / "7-某套皮肤"
        self.folder.mkdir()
        for side in ("r", "b"):
            for piece in "kabnrcp":
                make_piece(self.folder / f"{side}{piece}.png")
        make_piece(self.folder / "mask.png")
        make_piece(self.folder / "mask2.png")
        from PIL import Image

        Image.new("RGB", (1200, 1400), (180, 140, 95)).save(self.folder / "background.png")
        (self.folder / "skin.json").write_text(json.dumps({
            "board_w_ratio": 1.0, "board_h_ratio": 1.0,
            "margin_left": 0.0, "margin_top": 0.0,
            "grid_w": 0.8, "grid_h": 0.8,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ratio_schema_is_parsed(self):
        candidate = skin_module.Skin(self.folder)
        self.assertTrue(candidate.ok)
        self.assertEqual(candidate.grid_source, "skin.json")
        self.assertAlmostEqual(candidate.grid_ratio[2], 0.8)
        self.assertAlmostEqual(candidate.grid_ratio[3], 0.8)
        self.assertEqual(candidate.board_ratio, (1.0, 1.0))

    def test_background_is_found(self):
        candidate = skin_module.Skin(self.folder)
        self.assertIsNotNone(candidate.background_path)
        self.assertEqual(candidate.background_path.name, "background.png")

    def test_board_png_is_used_when_present(self):
        """有 board.png 的皮肤仍然走"贴棋盘图"那条路。"""
        make_board_with_grid(self.folder / "board.png")
        (self.folder / "skin.json").write_text(json.dumps(
            {"board": "board.png", "grid": [59, 79, 560, 630]}), encoding="utf-8")
        candidate = skin_module.Skin(self.folder)
        self.assertIsNotNone(candidate.path_of("board"))
        self.assertEqual(candidate.grid_rect, (59, 79, 619, 709))


class TestSkinScan(unittest.TestCase):
    """目录扫描：14 个棋子齐全才算一套；board.png 可选。"""

    def test_desk_is_darker_than_board_surface(self):
        """要求：棋盘面要比周围桌面亮（两者不能同色同纹路，一眼能分出来）。"""
        from PIL import Image, ImageEnhance

        tmp = Path(tempfile.mkdtemp(prefix="xq_contrast_"))
        try:
            folder = tmp / "0-对比度"
            folder.mkdir()
            for side in ("r", "b"):
                for piece in "kabnrcp":
                    make_piece(folder / f"{side}{piece}.png")
            # 造一张"中等偏暗"的背景：棋盘面提亮后应该明显比它亮
            board = Image.new("RGB", (1200, 1400), (110, 80, 50))
            board.save(folder / "background.png")
            (folder / "skin.json").write_text(json.dumps(
                {"margin_left": 0.0, "margin_top": 0.0,
                 "grid_w": 0.8, "grid_h": 0.8}), encoding="utf-8")

            candidate = skin_module.Skin(folder)
            self.assertIsNotNone(candidate.background_path)
            self.assertAlmostEqual(candidate.grid_ratio[2], 0.8)

            original = Image.open(candidate.background_path).convert("L")
            base = sum(original.resize((16, 16)).getdata()) / 256          # 原图亮度
            # 与 board_view 里的系数保持一致：桌面 ×0.86，棋盘面 ×1.30
            desk = base * 0.86
            surface = min(255.0, base * 1.30)
            self.assertGreater(surface - desk, 15,
                               f"棋盘面({surface:.0f}) 应该比桌面({desk:.0f})亮")
            self.assertGreater(base * 0.86, 40,
                               "桌面不能压得太黑（要保留每套皮肤自己的颜色风格）")
            del ImageEnhance
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


    """目录扫描：14 个棋子齐全才算一套；board.png 可选。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xq_skin_"))
        self.root = self.tmp / "skins"
        self.root.mkdir()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scan_lists_complete_skins_only(self):
        make_skin_folder(self.root, "0-样例甲")
        make_skin_folder(self.root, "1-样例乙")
        half = self.root / "2-还没转完"
        half.mkdir()
        make_piece(half / "rk.png")
        items = skin_module.scan_skins(self.root)
        self.assertEqual([item["name"] for item in items], ["0-样例甲", "1-样例乙"])
        self.assertEqual(items[0]["count"], 14)
        self.assertEqual(items[0]["missing"], [])

    def test_missing_piece_skips_the_folder(self):
        make_skin_folder(self.root, "3-少两个马", complete=False)
        self.assertEqual(skin_module.scan_skins(self.root), [])
        single = skin_module.Skin(self.root / "3-少两个马")
        self.assertFalse(single.ok)
        self.assertIn("bn", single.missing)
        self.assertIn("rn", single.missing)

    def test_skins_root_uses_env_override(self):
        import os

        before = os.environ.get("XQ_SKINS_DIR")
        os.environ["XQ_SKINS_DIR"] = str(self.root)
        try:
            self.assertEqual(str(skin_module.skins_root()), str(self.root))
        finally:
            if before is None:
                os.environ.pop("XQ_SKINS_DIR", None)
            else:
                os.environ["XQ_SKINS_DIR"] = before


if __name__ == "__main__":
    unittest.main()
