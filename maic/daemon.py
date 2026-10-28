from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .asr import RealtimeAsr, check_ready
from .audio import AudioCapture
from .config import Config
from .inject import copy_to_clipboard, normalize_text, type_text
from .subprocess_env import child_env
from .niri import (
    OutputInfo,
    TargetWindow,
    current_focused_id,
    focus_window,
    focused_output,
    focused_window,
    window_exists,
)

log = logging.getLogger("maic")


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "maic.sock"
    return Path(f"/tmp/maic-{os.getuid()}.sock")


@dataclass
class Session:
    target: TargetWindow
    output: OutputInfo
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    error_event: asyncio.Event = field(default_factory=asyncio.Event)
    error_message: str = ""


class Daemon:
    def __init__(self, cfg: Config, overlay):
        self.cfg = cfg
        self.overlay = overlay
        self.state = "idle"
        self.session: Session | None = None
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, name="maic-asyncio", daemon=True)
        self.ipc_server: asyncio.AbstractServer | None = None
        self._session_task: asyncio.Task | None = None
        self._keep_error = False
        self._started = threading.Event()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def start(self) -> None:
        self.thread.start()
        future = asyncio.run_coroutine_threadsafe(self._start_ipc(), self.loop)
        future.result(timeout=3)

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
        path.parent.mkdir(parents=True, exist_ok=True)
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
            request = json.loads(line)
            response = await self.dispatch(request)
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        try:
            writer.write((json.dumps(response) + "\n").encode("utf-8"))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def dispatch(self, request: dict) -> dict:
        command = str(request.get("command", ""))
        if command == "status":
            return {"ok": True, "state": self.state, "language": self.cfg.language}
        if command == "language":
            code = str(request.get("language", "")).strip()
            if not code:
                return {"ok": True, "language": self.cfg.language}
            self.cfg.language = code
            self.overlay.set_language(code)
            log.info("language set to %s", code)
            return {"ok": True, "language": code}
        if command == "toggle":
            if self.state == "idle":
                self._start_session(copy=bool(request.get("clipboard", False)))
                return {"ok": True, "state": self.state}
            if self.state in {"recording", "starting"}:
                self._request_stop()
                return {"ok": True, "state": self.state}
            return {"ok": True, "state": self.state}
        if command == "stop":
            self._request_stop()
            return {"ok": True, "state": self.state}
        if command == "cancel":
            self._request_cancel()
            return {"ok": True, "state": self.state}
        if command == "type":
            text = normalize_text(str(request.get("text", "")), self.cfg)
            ok = await type_text(text, self.cfg)
            return {"ok": ok, "state": self.state}
        if command == "quit":
            self._request_cancel()
            return {"ok": True, "state": self.state}
        return {"ok": False, "error": f"unknown command: {command}"}

    def _start_session(self, copy: bool) -> None:
        if self.state != "idle":
            return
        self.state = "starting"
        self._session_task = self.loop.create_task(self._run_session(copy))

    def _request_stop(self) -> None:
        if self.session is None:
            return
        if self.state == "recording":
            log.info("stop requested")
            self.state = "stopping"
            self.overlay.set_state("STOP")
            self.session.stop_event.set()
        elif self.state == "starting":
            self._request_cancel()

    def _request_cancel(self) -> None:
        if self.session is None:
            return
        self.state = "cancelling"
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
        asr: RealtimeAsr | None = None
        audio: AudioCapture | None = None
        audio_task: asyncio.Task | None = None
        try:
            output = await focused_output()
            if output is None:
                await self._fail("cannot determine focused output")
                return
            self.overlay.start(output)
            self.overlay.set_state("CHECK")

            ready, message = await check_ready(self.cfg)
            if not ready:
                await self._fail(message)
                return

            target = await focused_window()
            if target is None:
                await self._fail("cannot determine focused window")
                return

            log.info("recording target window %s (%s)", target.id, target.app_id)
            session = Session(target=target, output=output)
            self.session = session
            self.overlay.set_state("CONNECT")

            asr = RealtimeAsr(
                self.cfg,
                on_partial=self.overlay.set_partial,
                on_final=self.overlay.set_partial,
                on_error=lambda msg: self._on_asr_error(session, msg),
            )
            await asr.connect()

            audio = AudioCapture(self.cfg)
            await audio.start()
            self.state = "recording"
            self.overlay.set_state("REC")

            audio_task = asyncio.create_task(self._audio_loop(audio, asr, session))
            stop_task = asyncio.create_task(session.stop_event.wait())
            cancel_task = asyncio.create_task(session.cancel_event.wait())
            error_task = asyncio.create_task(session.error_event.wait())

            done, pending = await asyncio.wait(
                {stop_task, cancel_task, error_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

            if session.cancel_event.is_set():
                self.state = "cancelling"
                self.overlay.set_state("CANCEL")
                await asr.cancel()
                return

            if session.error_event.is_set():
                raise RuntimeError(session.error_message or "ASR error")

            self.state = "stopping"
            self.overlay.set_state("STOP")
            stop_started = time.monotonic()
            await audio.stop()
            log.info("audio stopped after %.2fs", time.monotonic() - stop_started)
            if audio_task is not None:
                try:
                    await asyncio.wait_for(audio_task, timeout=2)
                    log.info("audio task finished after %.2fs", time.monotonic() - stop_started)
                except (asyncio.TimeoutError, asyncio.CancelledError):
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
            await copy_to_clipboard(text, self.cfg)
            log.info("clipboard updated after %.2fs", time.monotonic() - stop_started)

            if self.cfg.return_to_target:
                exists = await window_exists(target.id)
                log.info("target existence checked after %.2fs", time.monotonic() - stop_started)
                if not exists:
                    await self._fail(
                        "target window disappeared; transcript copied to clipboard",
                        session,
                    )
                    return
                focused = await current_focused_id()
                log.info("focus before injection: %s (target %s)", focused, target.id)
                if focused != target.id:
                    log.info("returning focus to target window %s", target.id)
                    await focus_window(target.id)
                    await asyncio.sleep(0.15)

            self.state = "injecting"
            self.overlay.set_state("TYPE")
            log.info("typing %d characters into window %s", len(text), target.id)
            ok = await type_text(text, self.cfg)
            log.info("type_text returned after %.2fs", time.monotonic() - stop_started)
            if not ok:
                await self._fail(
                    "wtype failed; transcript copied to clipboard",
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
            self.state = "idle"
            self.session = None
            self._session_task = None

    def _on_asr_error(self, session: Session, message: str) -> None:
        session.error_message = message
        session.error_event.set()

    async def _audio_loop(self, audio: AudioCapture, asr: RealtimeAsr, session: Session) -> None:
        try:
            while not session.stop_event.is_set() and not session.cancel_event.is_set():
                data = await audio.read_chunk()
                if not data:
                    break
                stats = AudioCapture.stats(data)
                self.overlay.set_level(stats.rms)
                await asr.send_audio(data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            session.error_message = f"audio capture failed: {exc}"
            session.error_event.set()
