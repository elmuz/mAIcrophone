from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from maic.backends import build_backends
from maic.backends.protocols import (
    AsrCapabilities,
    AsrEvent,
    AudioCapabilities,
    Backends,
    CompositorCapabilities,
    InjectionCapabilities,
    OutputInfo,
    TargetWindow,
)
from maic.config import Config, load_config
from maic.daemon import Daemon


class FakeCompositor:
    capabilities = CompositorCapabilities(
        can_refocus=True,
        can_check_windows=True,
        can_query_output=True,
    )

    def __init__(self):
        self.refocused: list[int] = []

    async def focused_window(self):
        return TargetWindow(id=42, pid=7, app_id="test", title="Editor")

    async def focused_output(self):
        return OutputInfo(name="TEST-1", width=1920, height=1080)

    async def window_exists(self, window_id: int) -> bool:
        return window_id == 42

    async def current_focused_id(self) -> int | None:
        return 42

    async def focus_window(self, window_id: int) -> bool:
        self.refocused.append(window_id)
        return True


class FakeAudio:
    capabilities = AudioCapabilities()

    def __init__(self):
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def read_chunk(self) -> bytes:
        await asyncio.sleep(0.01)
        return b"\x00\x00"

    async def stop(self) -> None:
        self.stopped = True

    def level(self, data: bytes) -> float:
        return 0.0


class FakeAsrSession:
    def __init__(self, capabilities, on_event):
        self.capabilities = capabilities
        self.on_event = on_event
        self.connected = False
        self.audio: list[bytes] = []
        self.cancelled = False
        self.closed = False

    async def connect(self) -> None:
        self.connected = True

    async def send_audio(self, data: bytes) -> None:
        self.audio.append(data)

    async def commit(self, timeout: float = 6.0) -> str | None:
        final = "hello world"
        self.on_event(AsrEvent.final(final))
        self.on_event(AsrEvent.committed())
        return final

    async def cancel(self) -> None:
        self.cancelled = True

    async def close(self) -> None:
        self.closed = True


class FakeAsrProvider:
    def __init__(self, capabilities=None):
        self.capabilities = capabilities or AsrCapabilities(
            supports_streaming=True,
            supports_partials=True,
            supports_commit=True,
            languages=("en-US",),
        )
        self.session: FakeAsrSession | None = None

    async def check_ready(self, timeout: float = 0.4) -> tuple[bool, str]:
        return True, "ready"

    def create_session(self, *, on_event):
        self.session = FakeAsrSession(self.capabilities, on_event)
        return self.session


class FakeInjector:
    capabilities = InjectionCapabilities(
        supports_unicode=True,
        supports_clipboard=True,
        supports_primary_selection=True,
    )

    def __init__(self):
        self.typed: list[str] = []
        self.copied: list[str] = []

    async def type_text(self, text: str) -> bool:
        self.typed.append(text)
        return True

    async def copy_to_clipboard(self, text: str) -> bool:
        self.copied.append(text)
        return True


class FakeOverlay:
    def __init__(self):
        self.started = False
        self.partials: list[str] = []
        self.states: list[str] = []
        self.hidden = False

    def start(self, output):
        self.started = True

    def set_partial(self, text):
        self.partials.append(text)

    def set_level(self, rms):
        pass

    def set_state(self, state):
        self.states.append(state)

    def set_language(self, code):
        pass

    def set_server_status(self, status):
        pass

    def show_error(self, message, timeout_ms=5000):
        pass

    def hide(self):
        self.hidden = True

    def destroy(self):
        pass


class BackendFactoryTests(unittest.TestCase):
    def test_default_backends_are_built_from_config(self):
        backends = build_backends(Config())
        self.assertEqual(type(backends.compositor).__name__, "NiriCompositor")
        self.assertEqual(type(backends.audio).__name__, "PulseAudioCapture")
        self.assertEqual(type(backends.injection).__name__, "WtypeInjector")
        self.assertEqual(type(backends.asr).__name__, "NemoSpeechProvider")
        self.assertTrue(backends.compositor.capabilities.can_refocus)
        self.assertTrue(backends.injection.capabilities.supports_unicode)
        self.assertTrue(backends.asr.capabilities.supports_streaming)


class BackendConfigTests(unittest.TestCase):
    def test_backend_section_is_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                """
[backend]
compositor = "niri"
audio = "pulse"
injection = "wtype"
asr = "nemo-speech"
overlay = "null"
""",
                encoding="utf-8",
            )
            cfg = load_config(path)

        self.assertEqual(cfg.backend_compositor, "niri")
        self.assertEqual(cfg.backend_audio, "pulse")
        self.assertEqual(cfg.backend_injection, "wtype")
        self.assertEqual(cfg.backend_asr, "nemo-speech")
        self.assertEqual(cfg.backend_overlay, "null")

    def test_unknown_backend_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                '[backend]\nasr = "does-not-exist"\n',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_config(path)


class DaemonFakeBackendTests(unittest.TestCase):
    def test_session_loop_runs_with_fake_providers(self):
        async def scenario(asr_capabilities=None):
            cfg = Config()
            compositor = FakeCompositor()
            audio = FakeAudio()
            injection = FakeInjector()
            asr = FakeAsrProvider(asr_capabilities)
            overlay = FakeOverlay()
            backends = Backends(
                compositor=compositor,
                audio=audio,
                injection=injection,
                asr=asr,
            )
            daemon = Daemon(cfg, backends, overlay)
            task = asyncio.create_task(daemon._run_session(False))
            for _ in range(200):
                if daemon.state == "recording":
                    break
                await asyncio.sleep(0.01)
            else:
                task.cancel()
                raise AssertionError("session never reached recording state")
            daemon._request_stop()
            await asyncio.wait_for(task, timeout=2)
            return daemon, compositor, audio, injection, asr, overlay

        daemon, compositor, audio, injection, asr, overlay = asyncio.run(scenario())

        self.assertEqual(daemon.state, "idle")
        self.assertTrue(audio.started)
        self.assertTrue(audio.stopped)
        self.assertIsNotNone(asr.session)
        assert asr.session is not None
        self.assertTrue(asr.session.connected)
        self.assertTrue(asr.session.closed)
        self.assertEqual(injection.copied, ["hello world"])
        self.assertEqual(injection.typed, ["hello world"])
        self.assertEqual(compositor.refocused, [])
        self.assertTrue(overlay.started)
        self.assertTrue(overlay.hidden)

    def test_session_loop_accepts_non_streaming_asr(self):
        caps = AsrCapabilities(
            supports_streaming=False,
            supports_partials=False,
            supports_commit=True,
            supports_cancel=True,
            languages=(),
        )

        async def scenario():
            cfg = Config()
            compositor = FakeCompositor()
            audio = FakeAudio()
            injection = FakeInjector()
            asr = FakeAsrProvider(caps)
            overlay = FakeOverlay()
            backends = Backends(
                compositor=compositor,
                audio=audio,
                injection=injection,
                asr=asr,
            )
            daemon = Daemon(cfg, backends, overlay)
            task = asyncio.create_task(daemon._run_session(False))
            for _ in range(200):
                if daemon.state == "recording":
                    break
                await asyncio.sleep(0.01)
            else:
                task.cancel()
                raise AssertionError("session never reached recording state")
            daemon._request_stop()
            await asyncio.wait_for(task, timeout=2)
            return injection, overlay

        injection, overlay = asyncio.run(scenario())
        self.assertEqual(injection.typed, ["hello world"])
        # Partials are skipped for a batch engine, but the final still reaches
        # the overlay through the same event contract.
        self.assertEqual(overlay.partials, ["hello world"])


if __name__ == "__main__":
    unittest.main()
