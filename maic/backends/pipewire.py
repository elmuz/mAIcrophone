"""PipeWire audio capture provider backed by ``pw-record``."""

from __future__ import annotations

import asyncio
import math
import shutil
import struct

from ..config import Config
from ..subprocess_env import child_env
from .protocols import AudioCapabilities, AudioStats


class PwRecordAudioCapture:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.proc: asyncio.subprocess.Process | None = None
        self._pending = bytearray()

    @property
    def capabilities(self) -> AudioCapabilities:
        return AudioCapabilities(sample_rate=self.cfg.sample_rate, channels=1)

    async def start(self) -> None:
        args = [
            "pw-record",
            "--raw",
            "--format",
            "s16",
            "--rate",
            str(self.cfg.sample_rate),
            "--channels",
            "1",
        ]
        if self.cfg.audio_source:
            args += ["--target", self.cfg.audio_source]
        args.append("-")
        self._pending.clear()
        # pw-record writes stdout through C stdio. On a pipe it buffers in
        # 4096-byte blocks (128 ms at 16 kHz mono s16), which makes the level
        # meter arrive in big, irregular bursts. Force unbuffered writes so
        # callers get the PipeWire quantum (about 5-20 ms) instead.
        if shutil.which("stdbuf"):
            args = ["stdbuf", "-o0", *args]
        self.proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=child_env(),
        )

    async def read_chunk(self, max_bytes: int | None = None) -> bytes:
        if not self.proc or not self.proc.stdout:
            return b""
        limit = self.cfg.chunk_bytes if max_bytes is None else max(2, max_bytes)
        while len(self._pending) < limit:
            data = await self.proc.stdout.read(limit - len(self._pending))
            if not data:
                break
            self._pending.extend(data)
        data = bytes(self._pending[:limit])
        del self._pending[:limit]
        return data

    async def stop(self) -> None:
        if not self.proc:
            return
        if self.proc.returncode is None:
            self.proc.terminate()
        try:
            await asyncio.wait_for(self.proc.wait(), timeout=2.0)
        except TimeoutError:
            self.proc.kill()
            await self.proc.wait()
        self.proc = None

    def level(self, data: bytes) -> float:
        return float(self.stats(data).peak)

    @staticmethod
    def stats(data: bytes) -> AudioStats:
        if len(data) < 2:
            return AudioStats()
        n = len(data) // 2
        samples = struct.unpack(f"<{n}h", data[: n * 2])
        if not samples:
            return AudioStats()
        rms = math.sqrt(sum(x * x for x in samples) / n)
        peak = max(abs(x) for x in samples)
        return AudioStats(rms=rms, peak=peak)
