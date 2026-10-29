"""Batch (non-streaming) HTTP ASR provider.

This adapter buffers raw PCM16 for the duration of a dictation and uploads a
WAV file when the session is committed. It works with OpenAI-compatible
transcription endpoints (faster-whisper-server, whisper.cpp's OpenAI server)
and the native whisper.cpp ``/inference`` endpoint.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave

from ..config import Config
from .protocols import AsrCapabilities, AsrEvent, AsrEventListener

log = logging.getLogger("maic.asr")


def pcm_to_wav(
    pcm: bytes,
    sample_rate: int,
    channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    """Wrap raw little-endian PCM in a minimal WAV container."""
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sample_width)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return out.getvalue()


def _multipart(
    fields: list[tuple[str, str]],
    *,
    file_field: str,
    filename: str,
    content: bytes,
    content_type: str = "audio/wav",
) -> tuple[bytes, str]:
    boundary = f"----maic-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields:
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _extract_text(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("text", "transcript", "result"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
    return ""


def _check_ready_sync(url: str, timeout: float) -> tuple[bool, str]:
    if not url:
        return False, "server offline: no batch transcription URL configured"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            resp.read(1)
        return True, "ready"
    except urllib.error.HTTPError as exc:
        # A 4xx response still proves that the HTTP server is reachable. Some
        # servers do not expose a GET endpoint for the transcription route.
        if exc.code < 500:
            return True, f"server responded HTTP {exc.code}"
        return False, f"server error: HTTP {exc.code}"
    except Exception as exc:
        return False, f"server offline: {exc}"


class BatchHttpSession:
    capabilities = AsrCapabilities(
        supports_streaming=False,
        supports_partials=False,
        supports_commit=True,
        supports_cancel=True,
        supports_punctuation=True,
        supports_itn=False,
        supports_endpointing=False,
    )

    def __init__(self, provider: "BatchHttpProvider", on_event: AsrEventListener):
        self.provider = provider
        self.cfg = provider.cfg
        self.on_event = on_event
        self.capabilities = provider.capabilities
        self._pcm = bytearray()
        self._connected = False
        self._closed = False

    async def connect(self) -> None:
        self._pcm.clear()
        self._connected = True
        self._closed = False

    async def send_audio(self, data: bytes) -> None:
        if not self._connected or self._closed:
            return
        if data:
            self._pcm.extend(data)

    async def commit(self, timeout: float | None = None) -> str | None:
        if not self._connected:
            return None
        timeout = timeout or self.cfg.batch_timeout
        pcm = bytes(self._pcm)
        self._pcm.clear()
        if not pcm:
            final = ""
            self.on_event(AsrEvent.final(final))
            self.on_event(AsrEvent.committed())
            return final
        try:
            final = await self.provider.transcribe_pcm(pcm, timeout=timeout)
        except Exception as exc:
            log.exception("batch transcription failed")
            self.on_event(AsrEvent.error(str(exc)))
            return None
        self.on_event(AsrEvent.final(final))
        self.on_event(AsrEvent.committed())
        return final

    async def cancel(self) -> None:
        self._pcm.clear()

    async def close(self) -> None:
        self._pcm.clear()
        self._closed = True
        self._connected = False


class BatchHttpProvider:
    """Transcribe buffered audio with a whisper.cpp/faster-whisper server."""

    capabilities = BatchHttpSession.capabilities

    def __init__(self, cfg: Config, protocol: str | None = None):
        self.cfg = cfg
        self.protocol = protocol or cfg.batch_protocol

    @property
    def endpoint(self) -> str:
        if self.cfg.batch_url:
            return self.cfg.batch_url
        if self.cfg.server_url.startswith(("http://", "https://")):
            return self.cfg.server_url
        if self.protocol == "whisper-cpp":
            return "http://127.0.0.1:8080/inference"
        return "http://127.0.0.1:8081/v1/audio/transcriptions"

    @property
    def ready_url(self) -> str:
        if self.cfg.batch_ready_url:
            return self.cfg.batch_ready_url
        parsed = urllib.parse.urlsplit(self.endpoint)
        if not parsed.scheme or not parsed.netloc:
            return self.endpoint
        # Probe the base URL rather than assuming a particular /health route.
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))

    async def check_ready(self, timeout: float = 0.4) -> tuple[bool, str]:
        return await asyncio.to_thread(_check_ready_sync, self.ready_url, timeout)

    def create_session(self, *, on_event: AsrEventListener) -> BatchHttpSession:
        return BatchHttpSession(self, on_event)

    async def transcribe_pcm(self, pcm: bytes, timeout: float) -> str:
        return await asyncio.to_thread(self._transcribe_sync, pcm, timeout)

    def _transcribe_sync(self, pcm: bytes, timeout: float) -> str:
        wav = pcm_to_wav(pcm, self.cfg.sample_rate)
        fields: list[tuple[str, str]] = [("response_format", "json")]
        if self.protocol == "whisper-cpp":
            file_field = "file"
            if self.cfg.language and self.cfg.language != "auto":
                fields.append(("language", self.cfg.language))
            fields.append(("temperature", "0.0"))
        else:
            file_field = "file"
            fields.append(("model", self.cfg.batch_model))
            if self.cfg.language and self.cfg.language != "auto":
                fields.append(("language", self.cfg.language))

        body, content_type = _multipart(
            fields,
            file_field=file_field,
            filename="maic.wav",
            content=wav,
        )
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": content_type,
                "Content-Length": str(len(body)),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                response_body = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"ASR server returned HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"ASR request failed: {exc}") from exc
        return _extract_text(response_body)
