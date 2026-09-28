# -*- coding: utf-8 -*-
"""静夜象棋界面 (Jingye Xiangqi) 核心包。

模块划分：

    board.py     规则内核：棋盘表示、走子生成、合法性判断、FEN 读写
    notation.py  记谱：ICCS / WXF / 中文记谱 的生成与解析
    game.py      棋谱树：主线与变例、PGN 导入导出
    xqf.py       XQF 二进制棋谱读取
    engine.py    UCI 引擎封装（皮卡鱼 Pikafish 等）
    session.py   会话层：棋局状态、引擎分析、人机对战编排
    server.py    本地 HTTP + SSE 服务，向浏览器界面提供 API
    web/         浏览器界面（棋盘、棋谱、分析、引擎面板）
"""

__version__ = "1.6"
