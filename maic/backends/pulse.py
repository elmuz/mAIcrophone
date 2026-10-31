"""Audio capture through the PulseAudio simple API.

PipeWire's ``pipewire-pulse`` server provides a compatible implementation, so
this provider works without the ``pw-record`` command. The simple API is a
small, stable C interface that is easy to bind with :mod:`ctypes`; it blocks in
a dedicated reader thread while the asyncio side consumes fixed-size chunks.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import math
import queue
import struct
import threading

from ..config import Config
from .protocols import AudioCapabilities, AudioStats

log = logging.getLogger("maic")

_PA_STREAM_RECORD = 2
_PA_SAMPLE_S16LE = 3
_PA_SAMPLE_S16NE = _PA_SAMPLE_S16LE  # supported targets are little-endian


class _PaSampleSpec(ctypes.Structure):
    _fields_ = [
        ("format", ctypes.c_int),
        ("rate", ctypes.c_uint32),
        ("channels", ctypes.c_uint8),
    ]


class _PaBufferAttr(ctypes.Structure):
    _fields_ = [
        ("maxlength", ctypes.c_uint32),
        ("tlength", ctypes.c_uint32),
        ("prebuf", ctypes.c_uint32),
        ("minreq", ctypes.c_uint32),
        ("fragsize", ctypes.c_uint32),
    ]


class _PulseSimple:
    def __init__(self) -> None:
        simple = ctypes.CDLL("libpulse-simple.so.0")
        pulse = ctypes.CDLL("libpulse.so.0")

        simple.pa_simple_new.argtypes = [
            ctypes.c_char_p,  # server
            ctypes.c_char_p,  # application name
            ctypes.c_int,  # direction
            ctypes.c_char_p,  # device/source
            ctypes.c_char_p,  # stream name
            ctypes.POINTER(_PaSampleSpec),
            ctypes.c_void_p,  # channel map
            ctypes.POINTER(_PaBufferAttr),  # buffer attributes
            ctypes.POINTER(ctypes.c_int),  # error
        ]
        simple.pa_simple_new.restype = ctypes.c_void_p
        simple.pa_simple_read.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_int),
        ]
        simple.pa_simple_read.restype = ctypes.c_int
        simple.pa_simple_free.argtypes = [ctypes.c_void_p]
        simple.pa_simple_free.restype = None

        pulse.pa_strerror.argtypes = [ctypes.c_int]
        pulse.pa_strerror.restype = ctypes.c_char_p

        self.simple = simple
        self.pulse = pulse

    def error_text(self, code: int) -> str:
        text = self.pulse.pa_strerror(code)
        return text.decode("utf-8", errors="replace") if text else f"error {code}"


class PulseAudioCapture:
    capabilities = AudioCapabilities(sample_rate=16000, channels=1)

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.capabilities = AudioCapabilities(sample_rate=cfg.sample_rate, channels=1)
        self._api: _PulseSimple | None = None
        self._stream: int | None = None
        self._queue: queue.Queue[bytes | None] | None = None
        self._pending = bytearray()
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None
        self._error: str | None = None

    async def start(self) -> None:
        if self._reader is not None and self._reader.is_alive():
            raise RuntimeError("previous PulseAudio capture thread is still running")
        await asyncio.to_thread(self._start_sync)
        self._stop.clear()
        self._error = None
        self._reader = threading.Thread(
            target=self._read_loop, name="maic-pulse-audio", daemon=True
        )
        self._reader.start()

    def _start_sync(self) -> None:
        api = _PulseSimple()
        spec = _PaSampleSpec(
            format=_PA_SAMPLE_S16NE,
            rate=self.cfg.sample_rate,
            channels=1,
        )
        # The simple API's default recording buffer can be several seconds on
        # PipeWire. Request a short fragment so pa_simple_read() returns in
        # ~20 ms steps instead of dumping multi-second bursts.
        fragsize = max(2, int(self.cfg.sample_rate * 2 * 0.02))
        attr = _PaBufferAttr(0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, fragsize)
        error = ctypes.c_int(0)
        stream = api.simple.pa_simple_new(
            None,
            b"maic",
            _PA_STREAM_RECORD,
            self.cfg.audio_source.encode("utf-8") if self.cfg.audio_source else None,
            b"dictation",
            ctypes.byref(spec),
            None,
            ctypes.byref(attr),
            ctypes.byref(error),
        )
        if not stream:
            raise RuntimeError(f"cannot open PulseAudio source: {api.error_text(error.value)}")
        self._api = api
        self._stream = stream
        self._pending = bytearray()
        # Keep the same worst-case buffering as the old 64 x chunk_ms queue:
        # the reader now queues 10 ms frames instead of aggregated chunks.
        queue_size = max(64, int(64 * self.cfg.chunk_ms / 10))
        self._queue = queue.Queue(maxsize=queue_size)

    def _read_loop(self) -> None:
        assert self._api is not None
        assert self._stream is not None
        assert self._queue is not None
        # Read in short frames so stop() is not stuck behind a large blocking
        # pa_simple_read(). read_chunk() aggregates them for the ASR stream and
        # also lets the overlay consume fresh meter samples at a higher rate.
        read_bytes = max(2, int(self.cfg.sample_rate * 2 * 0.01))
        error = ctypes.c_int(0)
        while not self._stop.is_set():
            buffer = ctypes.create_string_buffer(read_bytes)
            rc = self._api.simple.pa_simple_read(
                self._stream, buffer, read_bytes, ctypes.byref(error)
            )
            if rc < 0:
                self._error = self._api.error_text(error.value)
                break
            self._put(bytes(buffer.raw))
        self._put(None)

    def _put(self, data: bytes | None) -> None:
        assert self._queue is not None
        try:
            self._queue.put_nowait(data)
        except queue.Full:
            # Prefer recent audio over stale audio when the ASR link stalls.
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(data)
            except queue.Full:
                pass

    async def read_chunk(self, max_bytes: int | None = None) -> bytes:
        if self._queue is None:
            return b""
        limit = self.cfg.chunk_bytes if max_bytes is None else max(2, max_bytes)
        loop = asyncio.get_running_loop()
        data = bytearray()
        while len(data) < limit:
            if not self._pending:
                incoming = await loop.run_in_executor(None, self._queue.get)
                if incoming is None:
                    if self._error:
                        raise RuntimeError(self._error)
                    break
                self._pending.extend(incoming)
            take = min(limit - len(data), len(self._pending))
            data.extend(self._pending[:take])
            del self._pending[:take]
        return bytes(data)

    async def stop(self) -> None:
        self._stop.set()
        await asyncio.to_thread(self._stop_sync)

    def _stop_sync(self) -> None:
        if self._reader is not None:
            self._reader.join(timeout=2.0)
            if self._reader.is_alive():
                # Freeing the stream while pa_simple_read() is still inside it
                # aborts libpulse. Leaking one stream is preferable to taking
                # down the whole daemon during shutdown.
                log.error("PulseAudio reader did not stop; leaving stream open")
                return
            self._reader = None
        if self._api is not None and self._stream is not None:
            self._api.simple.pa_simple_free(self._stream)
        self._stream = None
        self._api = None
        self._queue = None

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
