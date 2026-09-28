#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「静夜象棋界面」打包成单文件 exe（双击即可运行，不需要 Python 环境）。

用法::

    pip install pyinstaller            # 打包工具（运行程序本身不需要）
    python build_exe.py                # 生成 dist/静夜象棋界面.exe
    python build_exe.py --out "C:\\Users\\Administrator\\Desktop\\静夜象棋界面"
    python build_exe.py --name 我的象棋 --no-console   # 仅调试时用 --console 看输出

打包内容：Python 代码 + 原生界面（xqtrainer/ui）+ 备用浏览器界面（xqtrainer/web）
+ 图标（assets）；运行时会在临时目录解包，因此**单个 exe 即可运行**。
棋盘与棋子是程序实时绘制的矢量图形，不需要额外的图片素材。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_NAME = "静夜象棋界面"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="打包成单文件 exe")
    parser.add_argument("--name", default=DEFAULT_NAME, help=f"exe 名称（默认 {DEFAULT_NAME}）")
    parser.add_argument("--out", default="", help="把生成的 exe 复制到该目录（例如桌面文件夹）")
    parser.add_argument("--console", action="store_true", help="保留控制台窗口（调试用）")
    parser.add_argument("--keep-build", action="store_true", help="保留 build 中间文件")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    # 1) 图标（有 Pillow 就重新生成，没有就用已有文件）
    icon = ROOT / "assets" / "app.ico"
    try:
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")],
                       cwd=ROOT, check=True)
    except Exception as exc:  # noqa: BLE001
        if icon.exists():
            print(f"（跳过图标生成：{exc}，使用已有 {icon.name}）")
        else:
            print(f"（跳过图标生成：{exc}）")

    # 2) PyInstaller 参数
    dist_dir = ROOT / "dist"
    build_dir = ROOT / "build"
    spec_dir = ROOT / "build"
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile",
        "--name", args.name,
        "--distpath", str(dist_dir),
        "--workpath", str(build_dir),
        "--specpath", str(spec_dir),
        # 界面文件与图标随 exe 一起打包
        "--add-data", f"{ROOT / 'xqtrainer' / 'web'}{';' if sys.platform.startswith('win') else ':'}xqtrainer/web",
        "--add-data", f"{ROOT / 'assets'}{';' if sys.platform.startswith('win') else ':'}assets",
        # 连线功能：识别模型 + 圆圈光标（onnxruntime 会在下面单独收集）
        "--add-data", f"{ROOT / 'model'}{';' if sys.platform.startswith('win') else ':'}model",
        "--add-data", f"{ROOT / 'ui'}{';' if sys.platform.startswith('win') else ':'}ui",
        # 皮卡鱼引擎（可执行文件 + 神经网络权重）一起打包：用户不用再单独放引擎
        "--add-data", f"{ROOT / 'engine'}{';' if sys.platform.startswith('win') else ':'}engine",
        # 皮肤（78 套：棋盘图 / 棋子图 / background.png / skin.json）一起打包
        "--add-data", f"{ROOT / 'skins'}{';' if sys.platform.startswith('win') else ':'}skins",
        # 桌面背景（木纹/秋天/意式/海洋/冰雪/黄昏/森林/夏至）一起打包
        "--add-data", f"{ROOT / 'backgrounds'}{';' if sys.platform.startswith('win') else ':'}backgrounds",
        # onnxruntime 的 DLL 与数据文件要一起打包（连线识别用）
        "--collect-all", "onnxruntime",
        # 体积优化：排除用不到的大模块
        # 注意：**不能排除 numpy** —— onnxruntime 依赖它，排除后 exe 里会
        # "import numpy failed"，连线识别就只能退回模板匹配。
        "--exclude-module", "pandas",
        "--exclude-module", "matplotlib",
        # 注意：不能排除 PIL —— 局面图片的导出/识别、剪贴板图片都靠它
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc_data",
    ]
    if icon.exists():
        command += ["--icon", str(icon)]
    if not args.console:
        command += ["--noconsole"]
    command.append(str(ROOT / "desktop_app.py"))

    print("执行：", " ".join(command[:6]), "…")
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        print("打包失败，请检查上面的错误信息。")
        return result.returncode

    exe_name = f"{args.name}.exe" if sys.platform.startswith("win") else args.name
    exe_path = dist_dir / exe_name
    if not exe_path.exists():
        print(f"没有找到生成的文件：{exe_path}")
        return 1

    if args.out:
        target_dir = Path(args.out).expanduser()
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(exe_path, target_dir / exe_name)
        print(f"已复制到：{target_dir / exe_name}")

    if not args.keep_build:
        shutil.rmtree(build_dir, ignore_errors=True)

    size_mb = exe_path.stat().st_size / 1024 / 1024
    print(f"打包完成：{exe_path}（{size_mb:.1f} MB）")
    print("双击该 exe 即可运行：皮卡鱼引擎（pikafish.exe + pikafish.nnue）已经打包在 exe 里，")
    print("第一次打开会自动从 sys._MEIPASS 加载它；用户仍可在「引擎设置」里点「浏览…」换成别的引擎。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
