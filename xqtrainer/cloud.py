# -*- coding: utf-8 -*-
"""中国象棋云库（在线查询）客户端。

接口来自象棋云库官方文档（http://www.chessdb.cn/cloudbook_api.html）：

    http://www.chessdb.cn/chessdb.php?action=queryall&board=<FEN>&showall=1&learn=0

返回以 ``|`` 分隔的着法信息，每项形如::

    move:c0e2,score:2,rank:2,note:! (44-05),winrate:50.15

其中 ``move`` 是 ICCS 着法，``score`` 是分值（``??`` 表示云库尚未收录），
``rank`` 是排序，``winrate`` 是胜率，``note`` 是备注。

其它可用动作：``querybest``（最佳着法）、``queryscore``（评估分值）、
``querypv``（思考细节）、``queryrule``（棋规裁定）。

为了让程序保持"不打扰服务器"：默认 ``learn=0``（不自动学习、不提交数据）、
结果带内存+磁盘缓存、并对请求做最小间隔限制。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_ENDPOINT = "http://www.chessdb.cn/chessdb.php"
USER_AGENT = "JingyeXiangqi/2.1 (+local desktop app)"

#: 云库返回的状态
STATUS_OK = "ok"
STATUS_UNKNOWN = "unknown"
STATUS_INVALID = "invalid"
STATUS_CHECKMATE = "checkmate"
STATUS_STALEMATE = "stalemate"
STATUS_NOBEST = "nobestmove"
STATUS_BUSY = "busy"
STATUS_ERROR = "error"
STATUS_DISABLED = "disabled"

STATUS_TEXT = {
    STATUS_OK: "查询成功",
    STATUS_UNKNOWN: "云库没有收录该局面",
    STATUS_INVALID: "局面代码无效",
    STATUS_CHECKMATE: "被将死",
    STATUS_STALEMATE: "被困毙",
    STATUS_NOBEST: "云库没有可推荐的着法",
    STATUS_BUSY: "查询过于频繁，请稍后再试",
    STATUS_ERROR: "网络错误",
    STATUS_DISABLED: "云库未启用",
}


@dataclass
class CloudHit:
    """云库返回的一条着法。"""

    move: str
    score: Optional[int] = None
    winrate: Optional[float] = None
    rank: Optional[int] = None
    note: str = ""
    unknown: bool = False

    def to_dict(self, cn: str = "") -> Dict[str, object]:
        return {
            "move": self.move,
            "cn": cn,
            "score": self.score,
            "winrate": self.winrate,
            "rank": self.rank,
            "note": self.note,
            "unknown": self.unknown,
            "source": "云库",
        }


@dataclass
class CloudResult:
    status: str = STATUS_ERROR
    hits: List[CloudHit] = None          # type: ignore[assignment]
    message: str = ""
    raw: str = ""
    cached: bool = False
    elapsed: float = 0.0

    def __post_init__(self) -> None:
        if self.hits is None:
            self.hits = []

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def to_dict(self) -> Dict[str, object]:
        return {
            "status": self.status,
            "statusText": STATUS_TEXT.get(self.status, self.status),
            "message": self.message,
            "cached": self.cached,
            "elapsed": round(self.elapsed, 2),
            "hits": [hit.to_dict() for hit in self.hits],
        }


def board_to_cloud_fen(fen: str) -> str:
    """云库只需要"盘面 + 行棋方"两段。"""
    parts = (fen or "").split()
    if len(parts) < 2:
        return (fen or "").strip()
    return parts[0] + " " + parts[1]


def parse_queryall(text: str) -> List[CloudHit]:
    """解析 ``queryall`` 的返回内容。"""
    text = (text or "").strip().strip("\x00").strip()
    if not text or text in ("unknown", "invalid board", "checkmate", "stalemate",
                            "nobestmove") or "查询过于频繁" in text:
        return []
    hits: List[CloudHit] = []
    for chunk in text.split("|"):
        chunk = chunk.strip()
        if not chunk:
            continue
        fields: Dict[str, str] = {}
        for item in chunk.split(","):
            if ":" not in item:
                continue
            key, _, value = item.partition(":")
            fields[key.strip().lower()] = value.strip()
        move = fields.get("move", "")
        if len(move) != 4:
            continue
        score_text = fields.get("score", "")
        unknown = score_text in ("??", "", "?")
        score: Optional[int]
        try:
            score = None if unknown else int(float(score_text))
        except ValueError:
            score, unknown = None, True
        try:
            winrate = float(fields.get("winrate", ""))
        except ValueError:
            winrate = None
        try:
            rank = int(fields.get("rank", ""))
        except ValueError:
            rank = None
        hits.append(CloudHit(move=move, score=score, winrate=winrate, rank=rank,
                             note=fields.get("note", ""), unknown=unknown))
    unknown_hits = [hit for hit in hits if hit.unknown]
    real_hits = [hit for hit in hits if not hit.unknown]
    # ★ 两种极端都要照顾：
    #   · 局面**完全没收录**（一条真实数据都没有）→ 返回空，不要拿占位行冒充招法；
    #   · 局面**有真实数据**（例如初始局面）→ 把"未收录"的着法也一并列出来
    #     （界面上标"未收录"、排在有数据的后面），这样开局的全部招法都能看到。
    #   · score:0 是正常分值，永远保留。
    if not real_hits:
        return []
    hits = real_hits + unknown_hits
    hits.sort(key=lambda hit: (hit.unknown, -(hit.rank or 0),
                               -(hit.score if hit.score is not None else -999)))
    return hits


class CloudBook:
    """云库客户端（带缓存与限流）。"""

    def __init__(self, endpoint: str = DEFAULT_ENDPOINT, timeout: float = 8.0,
                 cache_ttl: float = 600.0, min_interval: float = 0.35,
                 cache_path: Optional[Path] = None):
        self.endpoint = endpoint or DEFAULT_ENDPOINT
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.min_interval = min_interval
        self.cache_path = cache_path
        self._cache: Dict[str, Tuple[float, CloudResult]] = {}
        self._lock = threading.Lock()
        self._last_request = 0.0
        self.last_error = ""
        self.last_query_time = 0.0
        #: 自动学习：查询时带 learn=1（云库会开放全部招法/分值/胜率，并把局面提交给它分析）
        self.learn = False
        self.load_cache()

    # ------------------------------------------------------------ 缓存
    def load_cache(self) -> None:
        if self.cache_path is None or not Path(self.cache_path).exists():
            return
        try:
            data = json.loads(Path(self.cache_path).read_text(encoding="utf-8"))
            for key, value in data.get("entries", {}).items():
                hits = [CloudHit(**item) for item in value.get("hits", [])]
                self._cache[key] = (float(value.get("time", 0)),
                                    CloudResult(status=value.get("status", STATUS_OK),
                                                hits=hits, raw=value.get("raw", "")))
        except (OSError, ValueError, TypeError):
            self._cache = {}

    def save_cache(self) -> None:
        if self.cache_path is None:
            return
        try:
            entries = {}
            for key, (stamp, result) in list(self._cache.items())[-400:]:
                entries[key] = {"time": stamp, "status": result.status, "raw": result.raw[:4000],
                                "hits": [vars(hit) for hit in result.hits]}
            Path(self.cache_path).parent.mkdir(parents=True, exist_ok=True)
            Path(self.cache_path).write_text(
                json.dumps({"endpoint": self.endpoint, "entries": entries}, ensure_ascii=False),
                encoding="utf-8")
        except OSError:
            pass

    def cached(self, fen: str) -> Optional[CloudResult]:
        key = board_to_cloud_fen(fen)
        with self._lock:
            item = self._cache.get(key)
        if item is None:
            return None
        stamp, result = item
        if time.time() - stamp > self.cache_ttl:
            return None
        clone = CloudResult(status=result.status, hits=list(result.hits), raw=result.raw,
                            cached=True)
        return clone

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    # ------------------------------------------------------------ 请求
    def _request(self, params: Dict[str, str], retries: int = 2) -> str:
        query = urllib.parse.urlencode(params)
        url = f"{self.endpoint}?{query}"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        last_exc: Optional[Exception] = None
        for attempt in range(retries + 1):
            wait = self.min_interval - (time.time() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    self._last_request = time.time()
                    body = response.read().decode("utf-8", errors="replace")
                    return body.replace("\x00", "").strip()
            except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as exc:
                last_exc = exc
                self._last_request = time.time()
                time.sleep(0.4 * (attempt + 1))
        raise RuntimeError(str(last_exc) if last_exc else "云库请求失败")

    def query_all(self, fen: str, show_all: bool = True, learn: bool = False,
                  use_cache: bool = True) -> CloudResult:
        """查询该局面的全部云库着法。"""
        key = board_to_cloud_fen(fen)
        if use_cache:
            cached = self.cached(key)
            if cached is not None:
                return cached
        start = time.time()
        if self.learn:
            learn = True                     # 自动学习：每次都带 learn=1
        params = {"action": "queryall", "board": key, "showall": "1" if show_all else "0",
                  "learn": "1" if learn else "0"}
        try:
            text = self._request(params)
        except RuntimeError as exc:
            self.last_error = str(exc)
            return CloudResult(status=STATUS_ERROR, message=str(exc),
                               elapsed=time.time() - start)
        result = self._interpret(text, start)
        with self._lock:
            self._cache[key] = (time.time(), result)
        self.last_query_time = time.time()
        return result

    def _interpret(self, text: str, start: float) -> CloudResult:
        text = (text or "").strip()
        elapsed = time.time() - start
        if text.startswith("invalid board"):
            return CloudResult(status=STATUS_INVALID, message=text, raw=text, elapsed=elapsed)
        if text == "unknown":
            return CloudResult(status=STATUS_UNKNOWN, raw=text, elapsed=elapsed)
        if text == "checkmate":
            return CloudResult(status=STATUS_CHECKMATE, raw=text, elapsed=elapsed)
        if text == "stalemate":
            return CloudResult(status=STATUS_STALEMATE, raw=text, elapsed=elapsed)
        if text == "nobestmove":
            return CloudResult(status=STATUS_NOBEST, raw=text, elapsed=elapsed)
        if "查询过于频繁" in text or "too frequent" in text.lower():
            return CloudResult(status=STATUS_BUSY, message=text, raw=text, elapsed=elapsed)
        hits = parse_queryall(text)
        if not hits:
            return CloudResult(status=STATUS_UNKNOWN, raw=text, elapsed=elapsed)
        return CloudResult(status=STATUS_OK, hits=hits, raw=text, elapsed=elapsed)

    def query_best(self, fen: str, use_cache: bool = False) -> Tuple[Optional[str], str]:
        """查询云库最佳着法，返回 ``(着法或 None, 状态)``。"""
        key = board_to_cloud_fen(fen)
        params = {"action": "querybest", "board": key, "learn": "0"}
        try:
            text = self._request(params)
        except RuntimeError as exc:
            self.last_error = str(exc)
            return None, STATUS_ERROR
        text = text.strip().strip("\x00").strip()
        if text.startswith("move:"):
            return text.split(":", 1)[1].strip(), STATUS_OK
        if text.startswith("egtb:"):
            return text.split(":", 1)[1].strip(), STATUS_OK
        if text == "nobestmove":
            return None, STATUS_NOBEST
        if text.startswith("invalid board"):
            return None, STATUS_INVALID
        return None, STATUS_UNKNOWN

    def query_pv(self, fen: str) -> Tuple[Optional[int], Optional[int], List[str]]:
        """查询云库的思考细节：返回 ``(分值, 深度, PV)``。"""
        key = board_to_cloud_fen(fen)
        params = {"action": "querypv", "board": key, "learn": "0"}
        try:
            text = self._request(params).strip()
        except RuntimeError:
            return None, None, []
        if not text.startswith("score:"):
            return None, None, []
        score: Optional[int] = None
        depth: Optional[int] = None
        pv: List[str] = []
        for item in text.split(","):
            name, _, value = item.partition(":")
            name = name.strip()
            if name == "score":
                try:
                    score = int(float(value))
                except ValueError:
                    score = None
            elif name == "depth":
                try:
                    depth = int(value)
                except ValueError:
                    depth = None
            elif name == "pv":
                pv = [move for move in value.split("|") if move]
        return score, depth, pv
