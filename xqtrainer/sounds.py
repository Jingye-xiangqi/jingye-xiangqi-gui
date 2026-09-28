# -*- coding: utf-8 -*-
"""走子音效：程序自动合成 wav 文件（不依赖任何外部素材）。

三种音效：落子（清脆点击）、吃子（低沉一点）、将军（双音提示）。
Windows 下用系统自带的 winsound 异步播放；其它平台尝试用系统播放器，
都不可用时静默忽略（不影响任何功能）。
"""

from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import sys
import threading
import wave
from pathlib import Path
from typing import Dict, Optional

SAMPLE_RATE = 22050


def _envelope(index: int, total: int, attack: float = 0.02, decay: float = 0.9) -> float:
    """简单的起音/衰减包络。"""
    ratio = index / max(1, total)
    if ratio < attack:
        return ratio / attack
    return max(0.0, (1.0 - (ratio - attack) / max(1e-6, 1.0 - attack)) ** decay)


def _tone(freq: float, seconds: float, volume: float = 0.6, decay: float = 0.9,
          noise: float = 0.0, sweep: float = 0.0, seed: int = 1) -> bytes:
    total = int(SAMPLE_RATE * seconds)
    frames = bytearray()
    rng = seed
    for index in range(total):
        ratio = index / max(1, total)
        current = freq * (1.0 + sweep * ratio)
        value = math.sin(2 * math.pi * current * index / SAMPLE_RATE)
        if noise:
            rng = (1103515245 * rng + 12345) & 0x7FFFFFFF
            value += noise * ((rng / 0x3FFFFFFF) - 1.0)
        value *= _envelope(index, total, decay=decay) * volume
        value = max(-1.0, min(1.0, value))
        frames += struct.pack("<h", int(value * 32000))
    return bytes(frames)


def _silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(SAMPLE_RATE * seconds)


def _mix(*chunks: bytes) -> bytes:
    length = max(len(chunk) for chunk in chunks)
    total = bytearray(length)
    for chunk in chunks:
        for index in range(0, len(chunk), 2):
            base = struct.unpack_from("<h", chunk, index)[0]
            current = struct.unpack_from("<h", total, index)[0] if index + 2 <= len(total) else 0
            value = max(-32000, min(32000, base + current))
            struct.pack_into("<h", total, index, value)
    return bytes(total)


def _write_wav(path: Path, frames: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(frames)


def build_sound_files(folder: Path) -> Dict[str, Path]:
    """生成三个音效文件，返回名称到路径的映射。"""
    folder.mkdir(parents=True, exist_ok=True)
    files = {
        # 落子：短促木质敲击
        "move": _mix(_tone(880, 0.075, volume=0.55, decay=2.4, noise=0.25, seed=7),
                     _tone(1500, 0.045, volume=0.22, decay=3.0, noise=0.35, seed=11)),
        # 吃子：更低沉、更长
        "capture": _mix(_tone(520, 0.13, volume=0.65, decay=1.8, noise=0.3, seed=23),
                        _tone(240, 0.16, volume=0.35, decay=1.6, noise=0.2, seed=31)),
        # 将军：两声上行提示
        "check": _tone(1180, 0.10, volume=0.5, decay=1.6, sweep=0.15, seed=5)
        + _silence(0.03)
        + _tone(1560, 0.14, volume=0.5, decay=1.4, sweep=0.1, seed=13),
    }
    paths: Dict[str, Path] = {}
    for name, frames in files.items():
        target = folder / f"{name}.wav"
        if not target.exists() or target.stat().st_size < 500:
            try:
                _write_wav(target, frames)
            except OSError:
                continue
        paths[name] = target
    return paths


class SoundPlayer:
    """音效播放器（可关闭；失败时静默忽略）。"""

    def __init__(self, folder: Optional[Path] = None, enabled: bool = True):
        self.folder = folder
        self.enabled = enabled
        self.files: Dict[str, Path] = {}
        self._lock = threading.Lock()
        if enabled:
            self.prepare()

    def prepare(self) -> None:
        if self.files:
            return
        folder = self.folder
        if folder is None:
            folder = Path(os.environ.get("TEMP", ".")) / "jingye_xiangqi_sounds"
        try:
            self.files = build_sound_files(folder)
        except Exception:  # noqa: BLE001
            self.files = {}

    def play(self, kind: str = "move") -> None:
        if not self.enabled:
            return
        if not self.files:
            self.prepare()
        path = self.files.get(kind)
        if path is None or not path.exists():
            return
        threading.Thread(target=self._play_file, args=(str(path),), daemon=True).start()

    @staticmethod
    def _play_file(path: str) -> None:
        try:
            if sys.platform.startswith("win"):
                import winsound

                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC
                                   | winsound.SND_NODEFAULT)
                return
            for command in (["afplay", path], ["aplay", "-q", path],
                            ["paplay", path], ["ffplay", "-nodisp", "-autoexit", "-loglevel",
                                               "quiet", path]):
                if shutil.which(command[0]):
                    subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                    return
        except Exception:  # noqa: BLE001
            pass
