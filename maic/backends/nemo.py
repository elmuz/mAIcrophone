"""nemo-speech realtime WebSocket ASR provider."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
from typing import Callable

import websockets

from ..config import Config
from .protocols import AsrCapabilities

log = logging.getLogger("maic.asr")

# A delta that shares at least this many leading characters with the currently
# displayed partial is treated as a revision (a full replacement), not as a
# new suffix. Very short matches are ambiguous and append like normal suffixes.
_MIN_REVISION_PREFIX = 3


def longest_common_prefix(a: str, b: str) -> int:
    """Return the length of the common leading substring of *a* and *b*."""
    limit = min(len(a), len(b))
    i = 0
    while i < limit and a[i] == b[i]:
        i += 1
    return i


def reconcile_partial(previous: str, delta: str) -> str:
    """Merge a streaming ASR delta into the currently displayed partial.

    The server emits ``delta`` in two shapes:

    * a suffix of newly recognised words (the common case), or
    * a complete replacement partial when earlier words are revised.

    Blindly appending would corrupt the overlay after a revision. A replacement
    usually starts with the old partial (an extension) or keeps a substantial
    leading prefix of it. Otherwise the delta is appended as a new suffix.
    """
    if not delta:
        return previous
    if not previous:
        return delta
    # New partial extends what is already displayed: it is authoritative.
    if delta.startswith(previous):
        return delta
    common = longest_common_prefix(previous, delta)
    # A revision keeps a meaningful prefix of the displayed text. Compare
    # against the delta length so a long, unrelated suffix is still appended.
    if common >= max(_MIN_REVISION_PREFIX, len(delta) // 2):
        return delta
    return previous + delta


def _check_ready_sync(url: str, timeout: float) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if body.get("ready") is True or body.get("status") == "ok":
            return True, "ready"
        return False, f"server not ready: {body}"
    except Exception as exc:
        return False, f"server offline: {exc}"


async def check_ready(cfg: Config, timeout: float = 0.4) -> tuple[bool, str]:
    return await asyncio.to_thread(_check_ready_sync, cfg.ready_url, timeout)


class NemoRealtimeSession:
    def __init__(
        self,
        cfg: Config,
        on_partial: Callable[[str], None],
        on_final: Callable[[str], None],
        on_error: Callable[[str], None],
    ):
        self.cfg = cfg
        self.on_partial = on_partial
        self.on_final = on_final
        self.on_error = on_error
        self.capabilities = NemoSpeechProvider.capabilities
        self.ws = None
        self.reader: asyncio.Task | None = None
        self.partial = ""
        self.final_text: str | None = None
        self.final_event = asyncio.Event()
        self.committed_event = asyncio.Event()

    async def connect(self) -> None:
        self.ws = await websockets.connect(self.cfg.server_url, max_size=None)
        await self.ws.recv()  # session.created
        await self.ws.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "sample_rate": self.cfg.sample_rate,
                        "language": self.cfg.language,
                        "automatic_punctuation": self.cfg.punctuation,
                    },
                }
            )
        )
        while True:
            raw = json.loads(await self.ws.recv())
            if raw.get("type") == "session.updated":
                break
            if raw.get("type") == "error":
                raise RuntimeError(raw.get("error", {}).get("message", "ASR error"))
        self.reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        assert self.ws is not None
        try:
            async for raw in self.ws:
                event = json.loads(raw)
                kind = event.get("type", "")
                if kind.endswith("transcription.delta"):
                    delta = event.get("delta") or ""
                    if not delta:
                        continue
                    # Best-effort overlay rendering. The final transcript is
                    # authoritative. Deltas are usually suffixes, but the
                    # server may send a full replacement when it revises
                    # earlier words, so reconcile instead of blind append.
                    self.partial = reconcile_partial(self.partial, delta)
                    self.on_partial(self.partial)
                elif kind.endswith("transcription.completed"):
                    self.final_text = event.get("transcript") or ""
                    self.partial = self.final_text
                    self.on_final(self.final_text)
                    self.final_event.set()
                elif kind == "input_audio_buffer.committed":
                    self.committed_event.set()
                elif kind == "error":
                    msg = event.get("error", {}).get("message", "ASR error")
                    self.on_error(msg)
                    self.final_event.set()
                    self.committed_event.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.on_error(str(exc))
            self.final_event.set()
            self.committed_event.set()

    async def send_audio(self, data: bytes) -> None:
        if self.ws is not None and data:
            await self.ws.send(data)

    async def commit(self, timeout: float = 6.0) -> str | None:
        if self.ws is None:
            return None
        started = time.monotonic()
        log.info("sending commit")
        await self.ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        try:
            await asyncio.wait_for(self.final_event.wait(), timeout=timeout)
            log.info("final received after %.2fs", time.monotonic() - started)
            await asyncio.wait_for(self.committed_event.wait(), timeout=timeout)
            log.info("committed received after %.2fs", time.monotonic() - started)
        except asyncio.TimeoutError:
            self.on_error("timed out waiting for final transcript")
            return None
        return self.final_text

    async def cancel(self) -> None:
        if self.ws is None:
            return
        try:
            await self.ws.send(json.dumps({"type": "input_audio_buffer.clear"}))
        except Exception:
            pass

    async def close(self) -> None:
        if self.reader:
            self.reader.cancel()
            try:
                await self.reader
            except (asyncio.CancelledError, Exception):
                pass
            self.reader = None
        if self.ws is not None:
            await self.ws.close()
            self.ws = None


class NemoSpeechProvider:
    capabilities = AsrCapabilities(
        supports_streaming=True,
        supports_partials=True,
        supports_commit=True,
        supports_cancel=True,
        supports_punctuation=True,
        supports_itn=True,
        languages=("auto", "en-US", "it-IT"),
    )

    def __init__(self, cfg: Config):
        self.cfg = cfg

    async def check_ready(self, timeout: float = 0.4) -> tuple[bool, str]:
        return await check_ready(self.cfg, timeout)

    def create_session(
        self,
        *,
        on_partial: Callable[[str], None],
        on_final: Callable[[str], None],
        on_error: Callable[[str], None],
    ) -> NemoRealtimeSession:
        return NemoRealtimeSession(self.cfg, on_partial, on_final, on_error)
