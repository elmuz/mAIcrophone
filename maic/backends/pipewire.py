"""PipeWire audio capture provider backed by ``pw-record``."""

from __future__ import annotations

import asyncio
import math
import struct

from ..config import Config
from ..subprocess_env import child_env
from .protocols import AudioCapabilities, AudioStats


class PwRecordAudioCapture:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.proc: asyncio.subprocess.Process | None = None

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
        self.proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=child_env(),
        )

    async def read_chunk(self) -> bytes:
        if not self.proc or not self.proc.stdout:
            return b""
        return await self.proc.stdout.read(self.cfg.chunk_bytes)

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
