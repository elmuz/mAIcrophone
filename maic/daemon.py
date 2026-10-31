from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from . import __version__
from .backends.protocols import (
    AsrEvent,
    AsrSession,
    AudioCapture,
    Backends,
    OutputInfo,
    TargetWindow,
)
from .config import Config
from .ipc import (
    MAX_MESSAGE_BYTES,
    IpcProtocolError,
    error,
    ok,
    validate_request,
)
from .subprocess_env import child_env
from .text import normalize_text

log = logging.getLogger("maic")


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "maic.sock"
    # /tmp is world-writable and makes the socket vulnerable to symlink and
    # pre-creation games. Prefer the per-user state directory when the session
    # runtime directory is unavailable (e.g. launched from a bare shell).
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    path = state / "maic" / "maic.sock"
    # AF_UNIX paths are limited to roughly 100 bytes. A very long $HOME must
    # not turn the fallback into a startup failure.
    if len(os.fsencode(path)) < 100:
        return path
    return Path(f"/tmp/maic-{os.getuid()}.sock")


class State(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"
    CANCELLING = "cancelling"
    INJECTING = "injecting"


@dataclass
class Session:
    target: TargetWindow
    output: OutputInfo
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    error_event: asyncio.Event = field(default_factory=asyncio.Event)
    committed_event: asyncio.Event = field(default_factory=asyncio.Event)
    error_message: str = ""


class Daemon:
    def __init__(self, cfg: Config, backends: Backends, overlay):
        self.cfg = cfg
        self.backends = backends
        self.overlay = overlay
        self.state = State.IDLE
        self.session: Session | None = None
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, name="maic-asyncio", daemon=True)
        self.ipc_server: asyncio.AbstractServer | None = None
        self._session_task: asyncio.Task | None = None
        self._server_task: asyncio.Task | None = None
        self.server_ready: bool | None = None
        self._keep_error = False
        self._started = threading.Event()
        self._commands: dict[str, Callable[[dict], Awaitable[dict]]] = {
            "status": self._cmd_status,
            "language": self._cmd_language,
            "toggle": self._cmd_toggle,
            "stop": self._cmd_stop,
            "cancel": self._cmd_cancel,
            "type": self._cmd_type,
        }

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def start(self) -> None:
        self.thread.start()
        future = asyncio.run_coroutine_threadsafe(self._start_ipc(), self.loop)
        future.result(timeout=3)
        self.loop.call_soon_threadsafe(self._start_polling)

    def _start_polling(self) -> None:
        self._server_task = self.loop.create_task(self._poll_server())

    async def _poll_server(self) -> None:
        while True:
            ready, _ = await self.backends.asr.check_ready()
            self._set_server_ready(ready)
            await asyncio.sleep(max(1, self.cfg.server_poll_seconds))

    def _set_server_ready(self, ready: bool) -> None:
        status = "online" if ready else "offline"
        self.overlay.set_server_status(status)
        if self.server_ready != ready:
            self.server_ready = ready
            log.info("ASR server %s", status)

    def stop(self) -> None:
        if self.loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop)
            try:
                future.result(timeout=3)
            except Exception:
                log.exception("shutdown failed")
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3)

    async def _shutdown(self) -> None:
        if self.session is not None:
            self.session.cancel_event.set()
        if self._session_task is not None:
            try:
                await asyncio.wait_for(self._session_task, timeout=3)
            except Exception:
                self._session_task.cancel()
        if self._server_task is not None:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
            self._server_task = None
        if self.ipc_server is not None:
            self.ipc_server.close()
            await self.ipc_server.wait_closed()
            self.ipc_server = None
        path = socket_path()
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    async def _start_ipc(self) -> None:
        path = socket_path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.exists():
            try:
                _, writer = await asyncio.open_unix_connection(path)
                writer.close()
                await writer.wait_closed()
                raise RuntimeError(f"another maic daemon is already listening on {path}")
            except (ConnectionRefusedError, FileNotFoundError, OSError):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        self.ipc_server = await asyncio.start_unix_server(self._handle_ipc, path=str(path))
        os.chmod(path, 0o600)
        log.info("listening on %s", path)

    async def _handle_ipc(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            if not line:
                return
            if len(line) > MAX_MESSAGE_BYTES:
                raise IpcProtocolError("bad_request", "request is too large")
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise IpcProtocolError("bad_request", f"invalid JSON: {exc.msg}") from exc
            request = validate_request(payload)
            response = await self.dispatch(request)
        except IpcProtocolError as exc:
            response = error(exc.message, exc.code)
        except Exception as exc:
            log.exception("IPC request failed")
            response = error(str(exc), "internal_error")
        try:
            writer.write((json.dumps(response) + "\n").encode("utf-8"))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def _status_snapshot(self) -> dict:
        if self.server_ready is True:
            server_status = "online"
        elif self.server_ready is False:
            server_status = "offline"
        else:
            server_status = "checking"

        session = None
        if self.session is not None:
            target = self.session.target
            session = {
                "target": {
                    "id": target.id,
                    "pid": target.pid,
                    "app_id": target.app_id,
                    "title": target.title,
                }
            }

        return {
            "state": self.state,
            "language": self.cfg.language,
            "server_ready": self.server_ready,
            "server_status": server_status,
            "overlay": bool(self.cfg.overlay_enabled and self.cfg.backend_overlay != "null"),
            "version": __version__,
            "session": session,
        }

    async def dispatch(self, request: dict) -> dict:
        command = str(request.get("command", ""))
        handler = self._commands.get(command)
        if handler is None:
            return error(f"unknown command: {command}", "unknown_command")
        return await handler(request)

    async def _cmd_status(self, request: dict) -> dict:
        return ok(**self._status_snapshot())

    async def _cmd_language(self, request: dict) -> dict:
        value = request.get("language", "")
        if not isinstance(value, str):
            return error("language must be a string", "invalid_argument")
        code = value.strip()
        if not code:
            return ok(language=self.cfg.language)
        self.cfg.language = code
        self.overlay.set_language(code)
        log.info("language set to %s", code)
        return ok(language=code)

    async def _cmd_toggle(self, request: dict) -> dict:
        clipboard = request.get("clipboard", False)
        if not isinstance(clipboard, bool):
            return error("clipboard must be a boolean", "invalid_argument")
        if self.state == State.IDLE:
            self._start_session(copy=clipboard)
        elif self.state in {State.RECORDING, State.STARTING}:
            self._request_stop()
        return ok(state=self.state)

    async def _cmd_stop(self, request: dict) -> dict:
        self._request_stop()
        return ok(state=self.state)

    async def _cmd_cancel(self, request: dict) -> dict:
        self._request_cancel()
        return ok(state=self.state)

    async def _cmd_type(self, request: dict) -> dict:
        value = request.get("text")
        if not isinstance(value, str):
            return error("text must be a string", "invalid_argument")
        text = normalize_text(value, self.cfg)
        injected = await self.backends.injection.type_text(text)
        if not injected:
            return error("text injection failed", "injection_failed")
        return ok(state=self.state)

    def _start_session(self, copy: bool) -> None:
        if self.state != State.IDLE:
            return
        self.state = State.STARTING
        self._session_task = self.loop.create_task(self._run_session(copy))

    def _request_stop(self) -> None:
        if self.session is None:
            return
        if self.state == State.RECORDING:
            log.info("stop requested")
            self.state = State.STOPPING
            self.overlay.set_state("STOP")
            self.session.stop_event.set()
        elif self.state == State.STARTING:
            self._request_cancel()

    def _request_cancel(self) -> None:
        if self.session is None:
            return
        self.state = State.CANCELLING
        self.overlay.set_state("CANCEL")
        self.session.cancel_event.set()

    async def _notify(self, title: str, body: str) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "notify-send",
                "--app-name=maic",
                "--expire-time=4000",
                title,
                body,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=child_env(),
            )
            await proc.wait()
        except Exception:
            pass

    async def _fail(self, message: str, session: Session | None = None) -> None:
        log.error("%s", message)
        self._keep_error = True
        self.overlay.show_error(message)
        await self._notify("mAIcrophone", message)
        if session is not None:
            session.error_message = message
            session.error_event.set()

    async def _run_session(self, copy: bool) -> None:
        session: Session | None = None
        asr: AsrSession | None = None
        audio: AudioCapture | None = None
        audio_task: asyncio.Task | None = None
        try:
            output = await self.backends.compositor.focused_output()
            if output is None:
                await self._fail("cannot determine focused output")
                return
            self.overlay.start(output)
            self.overlay.set_state("CHECK")
            self.overlay.set_server_status("checking")

            ready, message = await self.backends.asr.check_ready()
            self._set_server_ready(ready)
            if not ready:
                await self._fail(message)
                return

            target = await self.backends.compositor.focused_window()
            if target is None:
                await self._fail("cannot determine focused window")
                return

            log.info("recording target window %s (%s)", target.id, target.app_id)
            session = Session(target=target, output=output)
            self.session = session
            self.overlay.set_state("CONNECT")

            asr = self.backends.asr.create_session(
                on_event=lambda event: self._on_asr_event(session, event),
            )
            await asr.connect()

            audio = self.backends.audio
            await audio.start()
            self.state = State.RECORDING
            self.overlay.set_state("REC")

            audio_task = asyncio.create_task(self._audio_loop(audio, asr, session))
            stop_task = asyncio.create_task(session.stop_event.wait())
            cancel_task = asyncio.create_task(session.cancel_event.wait())
            error_task = asyncio.create_task(session.error_event.wait())

            _done, pending = await asyncio.wait(
                {stop_task, cancel_task, error_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

            if session.cancel_event.is_set():
                self.state = State.CANCELLING
                self.overlay.set_state("CANCEL")
                await asr.cancel()
                return

            if session.error_event.is_set():
                raise RuntimeError(session.error_message or "ASR error")

            self.state = State.STOPPING
            self.overlay.set_state("STOP")
            stop_started = time.monotonic()
            await audio.stop()
            log.info("audio stopped after %.2fs", time.monotonic() - stop_started)
            if audio_task is not None:
                try:
                    await asyncio.wait_for(audio_task, timeout=2)
                    log.info("audio task finished after %.2fs", time.monotonic() - stop_started)
                except (TimeoutError, asyncio.CancelledError):
                    audio_task.cancel()
                    log.info("audio task cancelled after %.2fs", time.monotonic() - stop_started)
            final = await asr.commit()
            log.info("commit returned after %.2fs", time.monotonic() - stop_started)
            if final is None:
                raise RuntimeError("ASR did not return a final transcript")
            log.debug("final transcript: %r", final)

            text = normalize_text(final, self.cfg)
            if not text:
                self.overlay.show_error("empty transcript")
                return

            # Always keep the transcript if the user asked for it.
            await self.backends.injection.copy_to_clipboard(text)
            log.info("clipboard updated after %.2fs", time.monotonic() - stop_started)

            compositor_caps = self.backends.compositor.capabilities
            if self.cfg.return_to_target and compositor_caps.can_refocus:
                if compositor_caps.can_check_windows:
                    exists = await self.backends.compositor.window_exists(target.id)
                    log.info(
                        "target existence checked after %.2fs",
                        time.monotonic() - stop_started,
                    )
                    if not exists:
                        await self._fail(
                            "target window disappeared; transcript copied to clipboard",
                            session,
                        )
                        return
                focused = await self.backends.compositor.current_focused_id()
                log.info("focus before injection: %s (target %s)", focused, target.id)
                if not await self._ensure_target_focus(target.id):
                    await self._fail(
                        "could not refocus target; transcript copied to clipboard",
                        session,
                    )
                    return
                log.info("focus confirmed after %.2fs", time.monotonic() - stop_started)

            injector_caps = self.backends.injection.capabilities
            if not injector_caps.supports_unicode and not text.isascii():
                await self._fail(
                    "injection backend does not support unicode; transcript copied to clipboard",
                    session,
                )
                return

            self.state = State.INJECTING
            self.overlay.set_state("TYPE")
            log.info("typing %d characters into window %s", len(text), target.id)
            ok = await self.backends.injection.type_text(text)
            log.info("type_text returned after %.2fs", time.monotonic() - stop_started)
            if not ok:
                await self._fail(
                    "text injection failed; transcript copied to clipboard",
                    session,
                )
                return
            log.info("injection complete after %.2fs", time.monotonic() - stop_started)
            await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._fail(str(exc), session)
        finally:
            if audio_task is not None:
                audio_task.cancel()
            if audio is not None:
                try:
                    await audio.stop()
                except Exception:
                    pass
            if asr is not None:
                try:
                    await asr.close()
                except Exception:
                    pass
            if not self._keep_error:
                self.overlay.hide()
            self._keep_error = False
            self.state = State.IDLE
            self.session = None
            self._session_task = None

    async def _ensure_target_focus(self, window_id: int, timeout: float = 1.0) -> bool:
        if await self.backends.compositor.current_focused_id() == window_id:
            return True
        if not await self.backends.compositor.focus_window(window_id):
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.backends.compositor.current_focused_id() == window_id:
                return True
            await asyncio.sleep(0.05)
        return False

    def _on_asr_event(self, session: Session, event: AsrEvent) -> None:
        if event.kind == "partial":
            # Partial support is optional; batch engines go straight to final.
            if self.backends.asr.capabilities.supports_partials:
                self.overlay.set_partial(event.text)
        elif event.kind == "final":
            set_final = getattr(self.overlay, "set_final", None)
            if callable(set_final):
                set_final(event.text)
            else:
                # Backwards compatibility with overlay implementations that
                # predate the final/partial distinction.
                self.overlay.set_partial(event.text)
        elif event.kind == "committed":
            session.committed_event.set()
        elif event.kind == "error":
            session.error_message = event.text
            session.error_event.set()

    @staticmethod
    def _meter_levels(
        audio: AudioCapture, data: bytes, target_ms: float = 1000.0 / 60.0
    ) -> tuple[list[float], int]:
        """Split captured audio into meter samples paced at about 60 Hz."""
        if len(data) < 2:
            return [], 0
        channels = max(1, audio.capabilities.channels)
        frame_bytes = 2 * channels
        frames = len(data) // frame_bytes
        if frames == 0:
            return [], 0
        duration_ms = frames / audio.capabilities.sample_rate * 1000.0
        count = max(1, min(frames, round(duration_ms / target_ms)))
        levels = []
        for i in range(count):
            start = round(i * frames / count) * frame_bytes
            end = round((i + 1) * frames / count) * frame_bytes
            levels.append(audio.level(data[start:end]))
        interval = max(1, round(duration_ms / count))
        return levels, interval

    async def _audio_loop(self, audio: AudioCapture, asr: AsrSession, session: Session) -> None:
        bytes_per_second = audio.capabilities.sample_rate * 2 * max(1, audio.capabilities.channels)
        # Capture in small blocks so the waveform gets fresh samples at roughly
        # frame rate. Accumulate them into the configured ASR chunk size below.
        meter_bytes = max(2, int(bytes_per_second * 0.02))
        chunk_bytes = max(2, int(bytes_per_second * self.cfg.chunk_ms / 1000))
        pending = bytearray()
        try:
            while not session.stop_event.is_set() and not session.cancel_event.is_set():
                data = await audio.read_chunk(meter_bytes)
                if not data:
                    break
                pending.extend(data)
                levels, interval = self._meter_levels(audio, data)
                set_levels = getattr(self.overlay, "set_levels", None)
                if callable(set_levels) and levels:
                    set_levels(levels, interval)
                elif levels:
                    self.overlay.set_level(max(levels))
                while len(pending) >= chunk_bytes:
                    await asr.send_audio(bytes(pending[:chunk_bytes]))
                    del pending[:chunk_bytes]
            if pending and not session.cancel_event.is_set():
                await asr.send_audio(bytes(pending))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            session.error_message = f"audio capture failed: {exc}"
            session.error_event.set()
