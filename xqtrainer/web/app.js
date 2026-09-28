/* 静夜象棋界面 前端脚本
 * 与后端通过 /api/action 通讯，通过 /api/events (SSE) 接收局面与引擎信息推送。
 */
(function () {
  "use strict";

  const GLYPH = {
    simple: { K: "帅", A: "仕", B: "相", N: "马", R: "车", C: "炮", P: "兵",
              k: "将", a: "士", b: "象", n: "马", r: "车", c: "炮", p: "卒" },
    trad: { K: "帥", A: "仕", B: "相", N: "傌", R: "俥", C: "炮", P: "兵",
            k: "將", a: "士", b: "象", n: "馬", r: "車", c: "砲", p: "卒" }
  };
  const CN_NUM = ["一", "二", "三", "四", "五", "六", "七", "八", "九"];
  const FILES = "abcdefghi";
  const SVG_NS = "http://www.w3.org/2000/svg";

  const state = {
    board: [],            // 10 行 FEN 文本，index 0 = 黑方底线
    side: "w",
    ply: 0,
    current: 0,
    selected: null,
    destinations: [],
    lastMove: null,
    flip: false,
    settings: { notation: "cn", mode: "analyze", engine_mode: "analyze", human_side: "w" },
    modeInfo: {},
    canHumanMove: true,
    analysis: { lines: [], history: [] },
    engine: { options: [], state: "stopped" },
    tree: null,
    treeRev: -1,
    meta: {},
    pvIndex: 0,
    autoPlaying: false,
    autoTimer: null,
    playInterval: 1.2,
    dragging: null,
    prevPly: 0
  };

  const els = {};
  const pieceEls = {};

  /* ------------------------------------------------------------ 基础工具 */
  function $(id) { return document.getElementById(id); }

  function squareName(file, rank) { return FILES[file] + rank; }

  function parseSquare(sq) {
    const file = FILES.indexOf(sq[0]);
    const rank = parseInt(sq[1], 10);
    return { file: file, rank: rank };
  }

  function boardPieceAt(sq) {
    const pos = parseSquare(sq);
    if (pos.file < 0 || pos.rank < 0 || pos.rank > 9) return ".";
    const row = state.board[9 - pos.rank];
    if (!row) return ".";
    let file = 0;
    for (let i = 0; i < row.length; i++) {
      const ch = row[i];
      if (ch >= "1" && ch <= "9") { file += parseInt(ch, 10); }
      else {
        if (file === pos.file) return ch;
        file += 1;
      }
    }
    return ".";
  }

  function colorOf(piece) { return piece && piece === piece.toUpperCase() ? "w" : "b"; }

  // 视图坐标（考虑翻转）
  function viewCoords(file, rank) {
    return state.flip ? { file: 8 - file, rank: 9 - rank } : { file: file, rank: rank };
  }

  function pieceLeftTop(file, rank) {
    const v = viewCoords(file, rank);
    return {
      left: ((50 + v.file * 100) / 900) * 100,
      top: ((950 - v.rank * 100) / 1000) * 100
    };
  }

  function squareFromPoint(clientX, clientY) {
    const rect = els.board.getBoundingClientRect();
    if (!rect.width) return null;
    const vx = ((clientX - rect.left) / rect.width) * 900;
    const vy = ((clientY - rect.top) / rect.height) * 1000;
    let file = Math.round((vx - 50) / 100);
    let rank = Math.round((950 - vy) / 100);
    if (file < 0 || file > 8 || rank < 0 || rank > 9) return null;
    const dx = Math.abs(50 + file * 100 - vx);
    const dy = Math.abs(950 - rank * 100 - vy);
    if (dx > 62 || dy > 62) return null;
    if (state.flip) { file = 8 - file; rank = 9 - rank; }
    return squareName(file, rank);
  }

  function notationOf(move) {
    const style = state.settings.notation || "cn";
    if (style === "iccs") return move.iccs;
    if (style === "wxf") return move.wxf;
    if (style === "trad") return move.cn_trad || move.cn;
    return move.cn;
  }

  function formatNodes(n) {
    if (!n) return "-";
    if (n > 1000000) return (n / 1000000).toFixed(2) + "M";
    if (n > 1000) return (n / 1000).toFixed(1) + "k";
    return String(n);
  }

  function toast(text, level) {
    if (!text) return;
    const el = $("toast");
    el.textContent = text;
    el.className = "toast" + (level && level !== "info" ? " " + level : "");
    clearTimeout(el._timer);
    el._timer = setTimeout(function () { el.className = "toast hidden"; }, 3200);
  }

  async function api(action, payload) {
    const body = Object.assign({ action: action }, payload || {});
    const resp = await fetch("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    let data = {};
    try { data = await resp.json(); } catch (err) { data = { ok: false, error: "服务器返回异常" }; }
    if (!data.ok && data.error) toast(data.error, "error");
    if (data.state) applyState(data.state);
    return data;
  }

  function syncSettings(patch) {
    api("settings", { patch: patch }).then(function (data) {
      if (data.state) applyState(data.state);
    });
  }

  /* ------------------------------------------------------------ 棋盘绘制 */
  function svgEl(name, attrs) {
    const el = document.createElementNS(SVG_NS, name);
    for (const key in attrs) el.setAttribute(key, attrs[key]);
    return el;
  }

  function buildBoard() {
    const svg = els.svg;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    const stroke = "#7a5629";
    const thin = { stroke: stroke, "stroke-width": 2, fill: "none" };

    // 外框
    svg.appendChild(svgEl("rect", {
      x: 34, y: 34, width: 832, height: 932, rx: 6,
      fill: "none", stroke: stroke, "stroke-width": 5
    }));
    // 横线
    for (let rank = 0; rank < 10; rank++) {
      const y = 950 - rank * 100;
      svg.appendChild(svgEl("line", Object.assign({ x1: 50, y1: y, x2: 850, y2: y }, thin)));
    }
    // 竖线（河界处断开）
    for (let file = 0; file < 9; file++) {
      const x = 50 + file * 100;
      if (file === 0 || file === 8) {
        svg.appendChild(svgEl("line", Object.assign({ x1: x, y1: 50, x2: x, y2: 950 }, thin)));
      } else {
        svg.appendChild(svgEl("line", Object.assign({ x1: x, y1: 50, x2: x, y2: 450 }, thin)));
        svg.appendChild(svgEl("line", Object.assign({ x1: x, y1: 550, x2: x, y2: 950 }, thin)));
      }
    }
    // 九宫斜线
    const palace = [
      [350, 950, 550, 750], [550, 950, 350, 750],
      [350, 50, 550, 250], [550, 50, 350, 250]
    ];
    palace.forEach(function (p) {
      svg.appendChild(svgEl("line", Object.assign({ x1: p[0], y1: p[1], x2: p[2], y2: p[3] }, thin)));
    });

    // 兵炮位的十字标记
    const marks = [];
    [[1, 2], [7, 2], [1, 7], [7, 7]].forEach(function (m) { marks.push([m[0], m[1], true]); });
    [[0, 3], [2, 3], [4, 3], [6, 3], [8, 3], [0, 6], [2, 6], [4, 6], [6, 6], [8, 6]]
      .forEach(function (m) { marks.push([m[0], m[1], false]); });
    marks.forEach(function (m) {
      const file = m[0];
      const rank = m[1];
      const x = 50 + file * 100;
      const y = 950 - rank * 100;
      const gap = 9;
      const len = 16;
      const dirs = [[-1, -1], [1, -1], [-1, 1], [1, 1]];
      dirs.forEach(function (d) {
        if (file === 0 && d[0] < 0) return;
        if (file === 8 && d[0] > 0) return;
        const cx = x + d[0] * gap;
        const cy = y + d[1] * gap;
        svg.appendChild(svgEl("path", {
          d: "M " + (cx + d[0] * len) + " " + cy + " L " + cx + " " + cy +
             " L " + cx + " " + (cy + d[1] * len),
          fill: "none", stroke: stroke, "stroke-width": 2, "stroke-linecap": "round"
        }));
      });
    });

    // 河界文字
    const riverStyle = {
      "font-family": "KaiTi, STKaiti, Kaiti SC, SimSun, serif",
      "font-size": "62", fill: "rgba(110,74,32,.78)", "text-anchor": "middle",
      "dominant-baseline": "middle", "letter-spacing": "12"
    };
    const left = svgEl("text", Object.assign({ x: 250, y: 502 }, riverStyle));
    left.textContent = "楚 河";
    const right = svgEl("text", Object.assign({ x: 650, y: 502 }, riverStyle));
    right.textContent = "汉 界";
    svg.appendChild(left);
    svg.appendChild(right);

    // 坐标
    const coordStyle = {
      "font-family": "KaiTi, serif", "font-size": "24",
      fill: "rgba(90,60,25,.75)", "text-anchor": "middle", "dominant-baseline": "middle"
    };
    for (let file = 0; file < 9; file++) {
      const x = 50 + file * 100;
      const bottom = svgEl("text", Object.assign({ x: x, y: 985 }, coordStyle));
      bottom.textContent = CN_NUM[8 - file];
      svg.appendChild(bottom);
      const top = svgEl("text", Object.assign({ x: x, y: 16 }, coordStyle));
      top.textContent = String(file + 1);
      svg.appendChild(top);
    }

    // 变例箭头层
    const defs = svgEl("defs", {});
    const marker = svgEl("marker", {
      id: "arrowhead", viewBox: "0 0 10 10", refX: 8, refY: 5,
      markerWidth: 6, markerHeight: 6, orient: "auto-start-reverse"
    });
    marker.appendChild(svgEl("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "#2f7fd0" }));
    defs.appendChild(marker);
    svg.appendChild(defs);
    const arrows = svgEl("g", { id: "pv-arrows" });
    svg.appendChild(arrows);
  }

  function renderArrows() {
    const layer = $("pv-arrows");
    if (!layer) return;
    while (layer.firstChild) layer.removeChild(layer.firstChild);
    const analysis = state.analysis || {};
    const lines = analysis.lines || [];
    const line = lines[state.pvIndex] || lines[0];
    if (!line || !line.pvIccs || !line.pvIccs.length) return;
    const count = Math.min(3, line.pvIccs.length);
    for (let i = 0; i < count; i++) {
      const iccs = line.pvIccs[i];
      if (!iccs || iccs.length < 4) break;
      const from = parseSquare(iccs.slice(0, 2));
      const to = parseSquare(iccs.slice(2, 4));
      const a = pieceLeftTop(from.file, from.rank);
      const b = pieceLeftTop(to.file, to.rank);
      const x1 = (a.left / 100) * 900;
      const y1 = (a.top / 100) * 1000;
      const x2 = (b.left / 100) * 900;
      const y2 = (b.top / 100) * 1000;
      const shrink = 46;
      const angle = Math.atan2(y2 - y1, x2 - x1);
      const sx = x1 + Math.cos(angle) * shrink;
      const sy = y1 + Math.sin(angle) * shrink;
      const ex = x2 - Math.cos(angle) * shrink;
      const ey = y2 - Math.sin(angle) * shrink;
      layer.appendChild(svgEl("line", {
        x1: sx, y1: sy, x2: ex, y2: ey,
        stroke: i === 0 ? "#2f7fd0" : "rgba(47,127,208,.45)",
        "stroke-width": i === 0 ? 9 : 6,
        "stroke-linecap": "round",
        "marker-end": "url(#arrowhead)",
        opacity: i === 0 ? 0.9 : 0.55
      }));
    }
  }

  function desiredBoard() {
    const map = {};
    for (let rowIndex = 0; rowIndex < state.board.length; rowIndex++) {
      const rank = 9 - rowIndex;
      const row = state.board[rowIndex] || "";
      let file = 0;
      for (let i = 0; i < row.length; i++) {
        const ch = row[i];
        if (ch >= "1" && ch <= "9") file += parseInt(ch, 10);
        else if (ch !== "/" && ch !== " ") {
          if (file < 9) map[squareName(file, rank)] = ch;
          file += 1;
        }
      }
    }
    return map;
  }

  function placePiece(el, square) {
    const pos = parseSquare(square);
    const lt = pieceLeftTop(pos.file, pos.rank);
    el.style.left = lt.left + "%";
    el.style.top = lt.top + "%";
  }

  function glyphOf(piece) {
    const table = (state.settings.notation === "trad") ? GLYPH.trad : GLYPH.simple;
    return table[piece] || piece;
  }

  function createPiece(square, piece) {
    const el = document.createElement("div");
    el.className = "piece " + (piece === piece.toUpperCase() ? "red" : "black");
    el.dataset.square = square;
    el.dataset.piece = piece;
    el.textContent = glyphOf(piece);
    placePiece(el, square);
    pieceEls[square] = el;
    els.pieces.appendChild(el);
    return el;
  }

  function renderPieces(animate) {
    const desired = desiredBoard();
    const last = state.lastMove;
    let moverSquare = null;

    if (animate && last && pieceEls[last.from]) {
      const el = pieceEls[last.from];
      if (el.dataset.piece === desired[last.to] && !desired[last.from]) {
        moverSquare = last.from;
      }
    }

    Object.keys(pieceEls).forEach(function (square) {
      const el = pieceEls[square];
      if (desired[square] === el.dataset.piece) return;
      if (square === moverSquare) return;
      el.remove();
      delete pieceEls[square];
    });

    if (moverSquare) {
      const el = pieceEls[moverSquare];
      delete pieceEls[moverSquare];
      el.dataset.square = last.to;
      el.classList.add("moving");
      placePiece(el, last.to);
      pieceEls[last.to] = el;
      setTimeout(function () { el.classList.remove("moving"); }, 260);
    }

    Object.keys(desired).forEach(function (square) {
      const piece = desired[square];
      const el = pieceEls[square];
      if (!el) {
        createPiece(square, piece);
        return;
      }
      if (el.dataset.piece !== piece) {
        el.dataset.piece = piece;
        el.textContent = glyphOf(piece);
        el.className = "piece " + (piece === piece.toUpperCase() ? "red" : "black");
      }
      placePiece(el, square);
    });

    Object.keys(pieceEls).forEach(function (square) {
      const el = pieceEls[square];
      el.classList.toggle("selected", state.selected === square);
      const waiting = !!state.modeInfo.engineToMove;
      el.style.cursor = waiting ? "progress" : "pointer";
    });
  }

  function renderOverlays() {
    els.pieces.querySelectorAll(".hl, .dot").forEach(function (el) { el.remove(); });
    const kingChar = state.side === "w" ? "K" : "k";
    const desired = desiredBoard();

    if (state.lastMove) {
      [state.lastMove.from, state.lastMove.to].forEach(function (square) {
        const el = document.createElement("div");
        el.className = "hl last";
        const pos = parseSquare(square);
        const lt = pieceLeftTop(pos.file, pos.rank);
        el.style.left = lt.left + "%";
        el.style.top = lt.top + "%";
        els.pieces.appendChild(el);
      });
    }

    if (state.check) {
      Object.keys(desired).forEach(function (square) {
        if (desired[square] === kingChar) {
          const el = document.createElement("div");
          el.className = "hl check";
          const pos = parseSquare(square);
          const lt = pieceLeftTop(pos.file, pos.rank);
          el.style.left = lt.left + "%";
          el.style.top = lt.top + "%";
          els.pieces.appendChild(el);
        }
      });
    }

    (state.destinations || []).forEach(function (square) {
      const el = document.createElement("div");
      const capture = desired[square] && desired[square] !== ".";
      el.className = "dot" + (capture ? " capture" : "");
      const pos = parseSquare(square);
      const lt = pieceLeftTop(pos.file, pos.rank);
      el.style.left = lt.left + "%";
      el.style.top = lt.top + "%";
      els.pieces.appendChild(el);
    });
  }

  function renderBoard(animate) {
    renderPieces(animate);
    renderOverlays();
    renderArrows();
    const tag = $("side-tag");
    if (tag) {
      tag.textContent = (state.side === "w" ? "红方走棋" : "黑方走棋");
    }
  }

  /* ------------------------------------------------------------ 棋盘交互 */
  function canHumanMove() {
    // 引擎执红 / 执黑时，轮到引擎就不能替它走子；分析模式随时可走
    return state.canHumanMove !== false;
  }

  function doMove(from, to) {
    if (!canHumanMove()) { toast("等待引擎走棋…", "warn"); return; }
    api("move", { from: from, to: to });
  }

  function onPointerDown(ev) {
    if (ev.button !== 0) return;
    const square = squareFromPoint(ev.clientX, ev.clientY);
    if (!square) return;
    const piece = boardPieceAt(square);
    const own = piece !== "." && colorOf(piece) === state.side;
    if (state.selected && state.destinations.indexOf(square) >= 0) {
      doMove(state.selected, square);
      return;
    }
    if (own && canHumanMove()) {
      if (state.selected !== square) api("select", { square: square });
      const el = pieceEls[square];
      if (el) {
        state.dragging = { square: square, el: el, x: ev.clientX, y: ev.clientY, active: false };
        els.board.setPointerCapture && els.board.setPointerCapture(ev.pointerId);
      }
      return;
    }
    if (state.selected) api("select", { square: "" });
  }

  function onPointerMove(ev) {
    const drag = state.dragging;
    if (!drag) return;
    const dx = ev.clientX - drag.x;
    const dy = ev.clientY - drag.y;
    if (!drag.active && Math.abs(dx) + Math.abs(dy) < 7) return;
    if (!drag.active) {
      drag.active = true;
      drag.el.classList.add("dragging");
      if (state.selected !== drag.square) api("select", { square: drag.square });
    }
    const rect = els.board.getBoundingClientRect();
    drag.el.style.left = (((ev.clientX - rect.left) / rect.width) * 100) + "%";
    drag.el.style.top = (((ev.clientY - rect.top) / rect.height) * 100) + "%";
  }

  function onPointerUp(ev) {
    const drag = state.dragging;
    state.dragging = null;
    if (!drag) return;
    if (drag.el) drag.el.classList.remove("dragging");
    if (!drag.active) return;
    const target = squareFromPoint(ev.clientX, ev.clientY);
    if (target && target !== drag.square && state.destinations.indexOf(target) >= 0) {
      doMove(drag.square, target);
    } else {
      placePiece(drag.el, drag.square);
    }
  }

  /* ------------------------------------------------------------ 棋谱面板 */
  function nodeRows(root) {
    const rows = [];
    function walkVariation(node, indent) {
      let cur = node;
      let first = true;
      while (cur) {
        rows.push({ node: cur, indent: indent, first: first });
        first = false;
        const kids = cur.children || [];
        for (let i = 1; i < kids.length; i++) walkVariation(kids[i], indent + 1);
        cur = kids[0];
      }
    }
    function walk(node, indent) {
      const kids = node.children || [];
      if (!kids.length) return;
      const main = kids[0];
      rows.push({ node: main, indent: indent, first: true });
      for (let i = 1; i < kids.length; i++) walkVariation(kids[i], indent + 1);
      walk(main, indent);
    }
    walk(root, 0);
    return rows;
  }

  function renderMoveList() {
    const list = $("move-list");
    if (!state.tree) {
      list.innerHTML = '<div class="empty-hint">还没有棋谱<br>点击左上角「导入棋谱」或直接在棋盘上走子</div>';
      return;
    }
    const rows = nodeRows(state.tree.root);
    if (!rows.length) {
      list.innerHTML = '<div class="empty-hint">暂无着法，直接在棋盘上走子即可记录</div>';
      return;
    }
    const limit = 1500;
    const html = [];
    rows.slice(0, limit).forEach(function (row) {
      const node = row.node;
      const number = Math.floor((node.ply + 1) / 2);
      const isRed = node.mover === "w";
      const label = (state.settings.notation === "iccs") ? node.iccs
        : (state.settings.notation === "wxf") ? node.wxf
        : (state.settings.notation === "trad" ? (node.cn || "") : node.cn);
      const numberText = isRed ? (number + ".") : (row.first && node.ply > 1 ? number + "…" : "");
      const cls = "move-text" + (node.id === state.current ? " current" : "") +
        (node.mate ? " mate" : "");
      html.push(
        '<div class="move-row' + (row.indent ? " variation" : "") + '" style="margin-left:' +
        (row.indent * 14) + 'px">' +
        '<span class="move-number">' + numberText + "</span>" +
        '<span class="' + cls + '" data-id="' + node.id + '">' + label + "</span>" +
        (node.comment ? '<span class="move-caret">✎</span>' : "") +
        '<span class="move-tools">' +
        '<button data-promote="' + node.id + '">主线</button>' +
        '<button data-delete="' + node.id + '">删除</button>' +
        "</span></div>"
      );
    });
    if (rows.length > limit) {
      html.push('<div class="empty-hint">棋谱过大，只显示前 ' + limit + " 手</div>");
    }
    list.innerHTML = html.join("");
    const currentEl = list.querySelector(".move-text.current");
    if (currentEl) currentEl.scrollIntoView({ block: "nearest" });
  }

  function renderGameHead() {
    const meta = state.meta || {};
    const title = $("game-title");
    const info = $("game-meta");
    const red = meta.Red || "";
    const black = meta.Black || "";
    if (red || black) {
      title.textContent = (red || "红方") + " 对 " + (black || "黑方");
    } else if (meta.Event || meta.Title) {
      title.textContent = meta.Title || meta.Event;
    } else {
      title.textContent = state.tree && state.tree.nodes > 1 ? "当前棋谱" : "未导入棋谱";
    }
    const parts = [];
    if (meta.Event) parts.push("赛事：" + meta.Event);
    if (meta.Site) parts.push("地点：" + meta.Site);
    if (meta.Date) parts.push("日期：" + meta.Date);
    if (meta.Result && meta.Result !== "*") parts.push("结果：" + meta.Result);
    if (meta.Type) parts.push("类型：" + meta.Type);
    if (state.treeNodes) parts.push("节点：" + state.treeNodes);
    info.innerHTML = parts.length ? parts.join("<br>") : "　";
  }

  /* ------------------------------------------------------------ 分析面板 */
  function scoreClass(line) {
    if (line.scoreMate !== null && line.scoreMate !== undefined) return "pv-score mate";
    if (line.scoreCp === null || line.scoreCp === undefined) return "pv-score";
    return "pv-score " + (line.scoreCp >= 0 ? "plus" : "minus");
  }

  function renderAnalysis() {
    const analysis = state.analysis || {};
    const lines = analysis.lines || [];
    const best = lines[0] || null;
    $("stat-depth").textContent = analysis.depth ? analysis.depth : "-";
    $("stat-score").textContent = best ? best.scoreText : "-";
    $("stat-judge").textContent = best ? best.judgement : "-";
    $("stat-nodes").textContent = formatNodes(analysis.nodes);
    $("stat-nps").textContent = analysis.nps ? formatNodes(analysis.nps) + "/s" : "-";
    $("stat-time").textContent = analysis.time ? (analysis.time / 1000).toFixed(1) + "s" : "-";

    const wdl = best && best.wdl ? best.wdl : null;
    const bar = $("wdl-bar");
    if (wdl && bar.children.length >= 3) {
      bar.style.display = "flex";
      const total = Math.max(1, wdl[0] + wdl[1] + wdl[2]);
      bar.children[0].style.width = ((wdl[0] / total) * 100).toFixed(1) + "%";
      bar.children[1].style.width = ((wdl[1] / total) * 100).toFixed(1) + "%";
      bar.children[2].style.width = ((wdl[2] / total) * 100).toFixed(1) + "%";
      bar.title = "红胜 " + (wdl[0] / 10).toFixed(1) + "%　和棋 " + (wdl[1] / 10).toFixed(1) +
        "%　黑胜 " + (wdl[2] / 10).toFixed(1) + "%";
    } else {
      bar.style.display = "none";
    }

    const list = $("pv-list");
    if (!lines.length) {
      list.innerHTML = '<div class="empty-hint">' +
        (state.engine && state.engine.hasEngine ? "点击「开始分析」查看引擎推荐着法"
          : "尚未加载引擎，请在「引擎」标签中选择皮卡鱼引擎路径") + "</div>";
    } else {
      list.innerHTML = lines.map(function (line, index) {
        const head = line.pvIccs && line.pvIccs[0] ? line.pvIccs[0] : "";
        const from = head ? head.slice(0, 2) : "";
        const to = head ? head.slice(2, 4) : "";
        const moves = (line.pvCn || []).slice(0, 8).join("　");
        const sub = "深度 " + line.depth + "　" + formatNodes(line.nodes) + " 节点" +
          (line.bound ? "（" + (line.bound === "lowerbound" ? "至少" : "至多") + "）" : "");
        return '<div class="pv-row' + (index === state.pvIndex ? " active" : "") +
          '" data-index="' + index + '">' +
          '<span class="' + scoreClass(line) + '">' + line.scoreText + "</span>" +
          '<div class="pv-body"><div class="pv-moves">' + (moves || "-") +
          '</div><div class="pv-sub">' + sub + "</div></div>" +
          '<div class="pv-actions">' +
          (from ? '<button class="btn small" data-play="' + from + "," + to + '">走这步</button>' : "") +
          "</div></div>";
      }).join("");
    }
    renderScoreChart();
    renderArrows();
  }

  function renderScoreChart() {
    const canvas = $("score-chart");
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.clientWidth || 320;
    const height = 90;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const history = (state.analysis && state.analysis.history) || [];

    ctx.strokeStyle = "rgba(120,140,160,.35)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, height / 2);
    ctx.lineTo(width, height / 2);
    ctx.stroke();

    if (history.length < 2) {
      ctx.fillStyle = "rgba(154,167,182,.8)";
      ctx.font = "12px sans-serif";
      ctx.fillText("评分曲线（引擎搜索深度 - 评分）", 8, 18);
      return;
    }
    const values = history.map(function (item) { return item[1]; });
    let limit = 200;
    values.forEach(function (v) { limit = Math.max(limit, Math.abs(v)); });
    const stepX = width / Math.max(1, values.length - 1);
    ctx.beginPath();
    values.forEach(function (value, index) {
      const x = index * stepX;
      const y = height / 2 - (value / limit) * (height / 2 - 8);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = "#d8b25c";
    ctx.lineWidth = 1.8;
    ctx.stroke();

    ctx.fillStyle = "rgba(154,167,182,.75)";
    ctx.font = "11px sans-serif";
    ctx.fillText("+" + (limit / 100).toFixed(1), 4, 12);
    ctx.fillText("-" + (limit / 100).toFixed(1), 4, height - 4);
  }

  /* ------------------------------------------------------------ 引擎面板 */
  function renderEnginePanel() {
    const engine = state.engine || {};
    const settings = state.settings || {};
    $("engine-path").value = engine.path || settings.engine_path || "";
    $("engine-name").textContent = engine.name ? engine.name : "未加载引擎";
    const dot = $("engine-dot");
    dot.className = "dot";
    if (engine.state === "thinking") dot.classList.add("thinking");
    else if (engine.state === "ready") dot.classList.add("ready");
    else if (engine.state === "error") dot.classList.add("error");
    $("engine-hint").innerHTML = engine.error ? ('<span style="color:#ffb4aa">' + engine.error + "</span>")
      : (engine.hasEngine ? "引擎运行中，可直接分析或对战。" :
        "提示：选择 Pikafish 可执行文件（pikafish.exe），权重文件 pikafish.nnue 需与其放在同一目录。");
    $("btn-analyze-toggle").textContent = (state.analysis && state.analysis.running) ? "停止分析" : "开始分析";

    const modes = document.querySelectorAll("#modes .mode-btn");
    const currentMode = (state.modeInfo && state.modeInfo.mode) ||
      settings.engine_mode || "analyze";
    modes.forEach(function (btn) {
      btn.classList.toggle("active", btn.dataset.mode === currentMode);
    });
    $("difficulty").value = settings.difficulty || 6;
    $("difficulty-label").textContent = settings.difficulty || 6;
    const table = { 1: "随手棋", 2: "入门", 3: "初级", 4: "入门偏上", 5: "中级",
                    6: "中级偏上", 7: "中高级", 8: "高级", 9: "大师", 10: "满力（最强）" };
    $("difficulty-hint").textContent = "等级 " + (settings.difficulty || 6) + "：" +
      (table[settings.difficulty || 6] || "") + "，等级越低思考越短、选子越随机。";
    $("movetime").value = settings.movetime_ms || 1500;
    $("movetime-label").textContent = ((settings.movetime_ms || 1500) / 1000).toFixed(1);
    $("depth-limit").value = settings.depth_limit || 0;
    $("depth-label").textContent = (settings.depth_limit ? settings.depth_limit + " 层" : "不限");
    $("infinite").checked = !!settings.infinite;
    $("auto-analyze").checked = !!settings.auto_analyze;
    $("threads").value = settings.threads || 4;
    $("hash").value = settings.hash_mb || 128;
    $("multipv").value = settings.multi_pv || 3;
    $("show-wdl").checked = !!settings.show_wdl;
    renderEngineOptions();
  }

  function renderEngineOptions() {
    const box = $("engine-options");
    const options = (state.engine && state.engine.options) || [];
    const saved = (state.engine && state.engine.settings && state.engine.settings.engine_options) || {};
    if (!options.length) {
      box.innerHTML = '<div class="empty-hint">加载引擎后可在这里调整该引擎支持的全部 UCI 选项</div>';
      return;
    }
    box.innerHTML = options.map(function (option) {
      const value = (saved[option.name] !== undefined) ? saved[option.name] : option.default;
      const name = option.name;
      if (option.type === "check") {
        return '<label class="checkbox"><input type="checkbox" data-option="' + name +
          '"' + (String(value) === "true" ? " checked" : "") + "> " + name + "</label>";
      }
      if (option.type === "spin") {
        return '<div class="field"><label>' + name + "（" + option.min + " - " + option.max +
          '）</label><input type="number" data-option="' + name + '" data-type="spin" min="' +
          option.min + '" max="' + option.max + '" value="' + value + '"></div>';
      }
      if (option.type === "combo") {
        return '<div class="field"><label>' + name + '</label><select data-option="' + name + '">' +
          (option.values || []).map(function (item) {
            return '<option' + (item === value ? " selected" : "") + ">" + item + "</option>";
          }).join("") + "</select></div>";
      }
      return '<div class="field"><label>' + name + '</label><input type="text" data-option="' +
        name + '" value="' + (value === undefined || value === null ? "" : value) + '"></div>';
    }).join("");
  }

  /* ------------------------------------------------------------ 局面面板 */
  function renderPosition() {
    const meta = state.meta || {};
    $("pos-side").textContent = state.sideName || (state.side === "w" ? "红方" : "黑方");
    $("pos-ply").textContent = state.ply + " 手";
    $("pos-state").textContent = state.resultName || (state.check ? "被将军" : "对局进行中");
    $("pos-repeat").textContent = (state.repetition > 1 ? state.repetition + " 次" : "首次");
    const material = state.material || { red: 0, black: 0 };
    const pieces = state.piecesCount || { red: 0, black: 0 };
    $("pos-red").textContent = (material.red / 2).toFixed(0) + " 分 / " + pieces.red + " 子";
    $("pos-black").textContent = (material.black / 2).toFixed(0) + " 分 / " + pieces.black + " 子";
    const fenInput = $("fen-input");
    if (document.activeElement !== fenInput) fenInput.value = state.fen || "";

    const names = { Event: "赛事", Site: "地点", Date: "日期", Round: "轮次", Red: "红方",
                    Black: "黑方", Result: "结果", Title: "标题", Type: "类型", TimeRule: "用时",
                    RedTime: "红方用时", BlackTime: "黑方用时", Opening: "开局", ECCO: "分类",
                    Branches: "变例数", Moves: "着法数" };
    const items = [];
    Object.keys(meta).forEach(function (key) {
      if (!meta[key]) return;
      items.push('<dt>' + (names[key] || key) + "</dt><div>" + meta[key] + "</div>");
    });
    $("meta-list").innerHTML = items.length ? items.join("") : "<dt>信息</dt><div>暂无</div>";

    const warnings = state.warnings || [];
    if (warnings.length) {
      $("warning-field").style.display = "";
      $("warning-list").innerHTML = warnings.map(function (text) {
        return "<div>· " + text + "</div>";
      }).join("");
    } else {
      $("warning-field").style.display = "none";
    }
  }

  function renderStatus() {
    const engine = state.engine || {};
    const analysis = state.analysis || {};
    $("status-message").textContent = state.message || "就绪";
    $("status-engine").textContent = "引擎：" + (engine.hasEngine ? (engine.name || "已加载")
      : (engine.name ? engine.name + "（未运行）" : "未加载"));
    const best = (analysis.lines || [])[0];
    $("status-analysis").textContent = best
      ? ("分析：深 " + analysis.depth + "　" + best.scoreText + "　" + best.judgement)
      : "分析：-";
    $("move-counter").textContent = state.ply + " / " + Math.max(state.ply, treeLineLength()) + " 手";
    const commentInput = $("comment-input");
    if (document.activeElement !== commentInput) commentInput.value = state.comment || "";
    const notationSelect = $("notation");
    if (notationSelect && document.activeElement !== notationSelect) {
      notationSelect.value = state.settings.notation || "cn";
    }
  }

  function treeLineLength() {
    if (!state.tree) return state.ply;
    let length = 0;
    let node = state.tree.root;
    while (node && node.children && node.children.length) {
      node = node.children[0];
      length += 1;
    }
    return length;
  }

  /* ------------------------------------------------------------ 状态应用 */
  async function fetchTree() {
    try {
      const resp = await fetch("/api/tree");
      const data = await resp.json();
      if (data.ok) {
        state.tree = data.tree;
        renderMoveList();
        renderGameHead();
      }
    } catch (err) { /* 忽略网络抖动 */ }
  }

  function applyState(next) {
    if (!next) return;
    const animate = next.ply === state.ply + 1 && next.lastMove;
    const treeChanged = next.treeRev !== state.treeRev;
    Object.assign(state, {
      board: next.board || [], side: next.side, sideName: next.sideName, ply: next.ply,
      current: next.current, selected: next.selected, destinations: next.destinations || [],
      lastMove: next.lastMove, settings: next.settings || state.settings,
      analysis: next.analysis || state.analysis, engine: next.engine || state.engine,
      meta: next.meta || {}, warnings: next.warnings || [], treeRev: next.treeRev,
      treeNodes: next.treeNodes, check: next.check, resultName: next.resultName,
      repetition: next.repetition, material: next.material, piecesCount: next.piecesCount,
      comment: next.comment, message: next.message, fen: next.fen,
      flip: next.settings ? !!next.settings.flip : state.flip
    });
    state.settings.flip = state.flip;
    renderBoard(animate);
    renderMoveList();
    renderGameHead();
    renderAnalysis();
    renderEnginePanel();
    renderPosition();
    renderStatus();
    if (treeChanged) fetchTree();
  }

  function connectEvents() {
    const source = new EventSource("/api/events");
    source.onmessage = function (event) {
      let data;
      try { data = JSON.parse(event.data); } catch (err) { return; }
      if (data.type === "state") {
        applyState(data.state);
      } else if (data.type === "analysis") {
        state.analysis = data.analysis || state.analysis;
        renderAnalysis();
        renderStatus();
        renderEnginePanel();
      } else if (data.type === "engine") {
        state.engine = data.engine || state.engine;
        renderEnginePanel();
        renderStatus();
      } else if (data.type === "toast") {
        toast(data.text, data.level);
        if (data.text) $("status-message").textContent = data.text;
      }
    };
    source.onerror = function () { /* 浏览器会自动重连 */ };
  }

  /* ------------------------------------------------------------ 播放控制 */
  function stopAutoplay() {
    state.autoPlaying = false;
    if (state.autoTimer) clearInterval(state.autoTimer);
    state.autoTimer = null;
    $("btn-autoplay").textContent = "▶";
    $("btn-autoplay").classList.add("primary");
  }

  function startAutoplay() {
    state.autoPlaying = true;
    $("btn-autoplay").textContent = "⏸";
    $("btn-autoplay").classList.remove("primary");
    clearInterval(state.autoTimer);
    state.autoTimer = setInterval(function () {
      const before = state.ply;
      api("nav", { to: "next" }).then(function (data) {
        if (!data.state || data.state.ply === before) stopAutoplay();
      });
    }, state.playInterval * 1000);
  }

  /* ------------------------------------------------------------ 文件与弹窗 */
  function downloadText(text, filename) {
    const blob = new Blob(["\ufeff" + text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function copyText(text, okMessage) {
    const done = function () { toast(okMessage || "已复制到剪贴板"); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text, done); });
    } else {
      fallbackCopy(text, done);
    }
  }

  function fallbackCopy(text, done) {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    try { document.execCommand("copy"); done(); } catch (err) { toast("复制失败", "error"); }
    area.remove();
  }

  async function importFile(file) {
    const buffer = await file.arrayBuffer();
    let binary = "";
    const bytes = new Uint8Array(buffer);
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    const kind = /\.xqf$/i.test(file.name) ? "xqf" : "pgn";
    await api("import", { kind: kind, name: file.name, encoding: "base64", data: btoa(binary) });
  }

  let browsePath = "";
  function openBrowse(path) {
    api("browse", { path: path || browsePath }).then(function (data) {
      if (!data.listing) return;
      const listing = data.listing;
      browsePath = listing.path || "";
      $("modal-path").textContent = listing.path || "（选择磁盘）";
      const html = [];
      if (listing.parent) {
        html.push('<div class="modal-item dir" data-dir="' + listing.parent + '">.. 上级目录</div>');
      }
      (listing.entries || []).forEach(function (entry) {
        html.push('<div class="modal-item ' + (entry.kind === "dir" ? "dir" : "file") +
          '" data-dir="' + (entry.kind === "dir" ? entry.path : "") +
          '" data-file="' + (entry.kind === "file" ? entry.path : "") + '">' +
          entry.name + "</div>");
      });
      $("modal-list").innerHTML = html.join(""); 
      $("modal").classList.remove("hidden");
    });
  }

  /* ------------------------------------------------------------ 事件绑定 */
  function bindEvents() {
    document.querySelectorAll("#modes .mode-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        syncSettings({ engine_mode: btn.dataset.mode });
      });
    });
    $("notation").addEventListener("change", function (ev) {
      syncSettings({ notation: ev.target.value });
    });

    $("btn-new").addEventListener("click", function () { api("new"); });
    $("btn-import").addEventListener("click", function () { $("file-input").click(); });
    $("file-input").addEventListener("change", function (ev) {
      const file = ev.target.files && ev.target.files[0];
      if (file) importFile(file);
      ev.target.value = "";
    });
    $("btn-export").addEventListener("click", function () {
      api("export", {}).then(function (data) {
        if (data.ok && data.text) downloadText(data.text, data.filename || "棋谱.pgn");
      });
    });
    $("btn-copyfen").addEventListener("click", function () { copyText(state.fen || "", "FEN 已复制"); });
    $("btn-flip").addEventListener("click", function () { api("flip"); });

    $("btn-first").addEventListener("click", function () { api("nav", { to: "first" }); });
    $("btn-prev").addEventListener("click", function () { api("nav", { to: "prev" }); });
    $("btn-next").addEventListener("click", function () { api("nav", { to: "next" }); });
    $("btn-last").addEventListener("click", function () { api("nav", { to: "last" }); });
    $("btn-autoplay").addEventListener("click", function () {
      if (state.autoPlaying) stopAutoplay();
      else startAutoplay();
    });
    $("play-speed").addEventListener("input", function (ev) {
      state.playInterval = parseFloat(ev.target.value);
      $("play-speed-label").textContent = state.playInterval.toFixed(1) + "s";
      if (state.autoPlaying) startAutoplay();
    });
    $("btn-delete-node").addEventListener("click", function () {
      if (!state.current) return;
      api("delete", { id: state.current });
    });
    $("btn-promote-node").addEventListener("click", function () {
      if (!state.current) return;
      api("promote", { id: state.current });
    });

    $("move-list").addEventListener("click", function (ev) {
      const target = ev.target;
      if (target.dataset && target.dataset.id) {
        api("nav", { to: "node", id: parseInt(target.dataset.id, 10) });
        return;
      }
      if (target.dataset && target.dataset.delete) {
        api("delete", { id: parseInt(target.dataset.delete, 10) });
        return;
      }
      if (target.dataset && target.dataset.promote) {
        api("promote", { id: parseInt(target.dataset.promote, 10) });
      }
    });

    $("btn-save-comment").addEventListener("click", function () {
      api("comment", { id: state.current, text: $("comment-input").value });
      toast("评注已保存");
    });

    $("btn-analyze-toggle").addEventListener("click", function () {
      const running = state.analysis && state.analysis.running;
      api("analyze", { mode: running ? "stop" : "start" });
    });

    $("pv-list").addEventListener("click", function (ev) {
      const row = ev.target.closest(".pv-row");
      if (!row) return;
      if (ev.target.dataset && ev.target.dataset.play) {
        const parts = ev.target.dataset.play.split(",");
        api("move", { from: parts[0], to: parts[1] });
        return;
      }
      state.pvIndex = parseInt(row.dataset.index, 10) || 0;
      renderAnalysis();
    });

    /* 引擎面板 */
    $("btn-browse").addEventListener("click", function () { openBrowse(browsePath || ""); });
    $("btn-autodetect").addEventListener("click", function () {
      api("engine_autodetect", {}).then(function (data) {
        if (data.paths && data.paths.length) {
          $("engine-path").value = data.paths[0];
          toast("找到引擎：" + data.paths[0]);
        } else {
          toast("未在常见位置找到皮卡鱼，请手动选择", "warn");
        }
      });
    });
    $("btn-load-engine").addEventListener("click", function () {
      api("engine_load", { path: $("engine-path").value });
    });
    $("btn-unload-engine").addEventListener("click", function () { api("engine_unload"); });
    $("btn-engine-move").addEventListener("click", function () { api("engine_move"); });
    $("difficulty").addEventListener("input", function (ev) {
      $("difficulty-label").textContent = ev.target.value;
    });
    $("difficulty").addEventListener("change", function (ev) {
      syncSettings({ difficulty: parseInt(ev.target.value, 10) });
    });
    $("movetime").addEventListener("input", function (ev) {
      $("movetime-label").textContent = (parseInt(ev.target.value, 10) / 1000).toFixed(1);
    });
    $("movetime").addEventListener("change", function (ev) {
      syncSettings({ movetime_ms: parseInt(ev.target.value, 10) });
    });
    $("depth-limit").addEventListener("input", function (ev) {
      const value = parseInt(ev.target.value, 10);
      $("depth-label").textContent = value ? value + " 层" : "不限";
    });
    $("depth-limit").addEventListener("change", function (ev) {
      syncSettings({ depth_limit: parseInt(ev.target.value, 10) });
    });
    $("infinite").addEventListener("change", function (ev) {
      syncSettings({ infinite: ev.target.checked });
    });
    $("auto-analyze").addEventListener("change", function (ev) {
      syncSettings({ auto_analyze: ev.target.checked });
    });
    $("threads").addEventListener("change", function (ev) {
      syncSettings({ threads: parseInt(ev.target.value, 10) });
    });
    $("hash").addEventListener("change", function (ev) {
      syncSettings({ hash_mb: parseInt(ev.target.value, 10) });
    });
    $("multipv").addEventListener("change", function (ev) {
      syncSettings({ multi_pv: parseInt(ev.target.value, 10) });
    });
    $("show-wdl").addEventListener("change", function (ev) {
      syncSettings({ show_wdl: ev.target.checked });
    });
    $("engine-options").addEventListener("change", function (ev) {
      const name = ev.target.dataset.option;
      if (!name) return;
      const value = ev.target.type === "checkbox" ? ev.target.checked : ev.target.value;
      api("engine_option", { name: name, value: value });
    });

    /* 局面面板 */
    $("btn-apply-fen").addEventListener("click", function () {
      api("set_fen", { fen: $("fen-input").value.trim() });
    });
    $("btn-copy-fen").addEventListener("click", function () {
      copyText($("fen-input").value, "FEN 已复制");
    });
    $("btn-start-pos").addEventListener("click", function () { api("new"); });

    /* 弹窗 */
    $("modal-close").addEventListener("click", function () { $("modal").classList.add("hidden"); });
    $("modal").addEventListener("click", function (ev) {
      if (ev.target === $("modal")) $("modal").classList.add("hidden");
    });
    $("modal-list").addEventListener("click", function (ev) {
      const item = ev.target.closest(".modal-item");
      if (!item) return;
      if (item.dataset.dir) { openBrowse(item.dataset.dir); return; }
      if (item.dataset.file) {
        $("engine-path").value = item.dataset.file;
        $("modal").classList.add("hidden");
        api("engine_load", { path: item.dataset.file });
      }
    });

    /* 键盘快捷键 */
    document.addEventListener("keydown", function (ev) {
      if (ev.target.tagName === "INPUT" || ev.target.tagName === "TEXTAREA" ||
          ev.target.tagName === "SELECT") return;
      if (ev.key === "ArrowLeft") { api("nav", { to: "prev" }); ev.preventDefault(); }
      else if (ev.key === "ArrowRight") { api("nav", { to: "next" }); ev.preventDefault(); }
      else if (ev.key === "Home") { api("nav", { to: "first" }); ev.preventDefault(); }
      else if (ev.key === "End") { api("nav", { to: "last" }); ev.preventDefault(); }
      else if (ev.key === " ") {
        if (state.autoPlaying) stopAutoplay(); else startAutoplay();
        ev.preventDefault();
      } else if (ev.key === "Escape") { api("select", { square: "" }); }
    });

    /* 标签页 */
    document.querySelectorAll("#tabs .tab").forEach(function (tab) {
      tab.addEventListener("click", function () {
        document.querySelectorAll("#tabs .tab").forEach(function (item) {
          item.classList.toggle("active", item === tab);
        });
        document.querySelectorAll(".tab-panel").forEach(function (panel) {
          panel.classList.toggle("active", panel.id === "tab-" + tab.dataset.tab);
        });
        if (tab.dataset.tab === "analysis") renderScoreChart();
      });
    });

    els.board.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    els.board.addEventListener("contextmenu", function (ev) {
      ev.preventDefault();
      api("select", { square: "" });
    });
  }

  function resizePieces() {
    const rect = els.board.getBoundingClientRect();
    if (!rect.width) return;
    const cell = rect.width / 9;
    document.documentElement.style.setProperty("--piece-size", (cell * 0.86).toFixed(1) + "px");
    document.documentElement.style.setProperty("--piece-font", (cell * 0.56).toFixed(1) + "px");
  }

  function init() {
    els.board = $("board");
    els.svg = $("board-svg");
    els.pieces = $("pieces");
    buildBoard();
    resizePieces();
    bindEvents();
    connectEvents();
    window.addEventListener("resize", function () {
      resizePieces();
      renderBoard(false);
      renderScoreChart();
    });
    fetch("/api/state").then(function (resp) { return resp.json(); }).then(function (data) {
      if (data.ok) applyState(data.state);
      fetchTree();
    }).catch(function () { toast("无法连接本地服务，请确认程序仍在运行", "error"); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
