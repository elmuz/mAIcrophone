from __future__ import annotations

import asyncio
import json
import unittest

from maic.backends.protocols import (
    AsrCapabilities,
    AudioCapabilities,
    Backends,
    CompositorCapabilities,
    InjectionCapabilities,
)
from maic.config import Config
from maic.daemon import Daemon
from maic.ipc import (
    PROTOCOL_VERSION,
    IpcProtocolError,
    error,
    make_request,
    ok,
    validate_request,
    validate_response,
)


class FakeCompositor:
    capabilities = CompositorCapabilities()

    async def focused_window(self):
        return None

    async def focused_output(self):
        return None

    async def window_exists(self, window_id: int) -> bool:
        return False

    async def current_focused_id(self) -> int | None:
        return None

    async def focus_window(self, window_id: int) -> bool:
        return False


class FakeAudio:
    capabilities = AudioCapabilities()

    async def start(self) -> None:
        pass

    async def read_chunk(self, max_bytes: int | None = None) -> bytes:
        return b""

    async def stop(self) -> None:
        pass

    def level(self, data: bytes) -> float:
        return 0.0


class FakeAsr:
    capabilities = AsrCapabilities()

    async def check_ready(self, timeout: float = 0.4) -> tuple[bool, str]:
        return True, "ready"

    def create_session(self, *, on_event):
        raise AssertionError("not used")


class FakeInjector:
    capabilities = InjectionCapabilities()

    def __init__(self):
        self.typed: list[str] = []

    async def type_text(self, text: str) -> bool:
        self.typed.append(text)
        return True

    async def copy_to_clipboard(self, text: str) -> bool:
        return True


class FakeOverlay:
    def __init__(self):
        self.languages: list[str] = []

    def start(self, output):
        pass

    def set_partial(self, text):
        pass

    def set_level(self, rms):
        pass

    def set_state(self, state):
        pass

    def set_language(self, code):
        self.languages.append(code)

    def set_server_status(self, status):
        pass

    def show_error(self, message, timeout_ms=5000):
        pass

    def hide(self):
        pass

    def destroy(self):
        pass


class CapturingWriter:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data += data

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


def make_daemon():
    injection = FakeInjector()
    overlay = FakeOverlay()
    backends = Backends(
        compositor=FakeCompositor(),
        audio=FakeAudio(),
        injection=injection,
        asr=FakeAsr(),
    )
    return Daemon(Config(), backends, overlay), injection, overlay


class EnvelopeTests(unittest.TestCase):
    def test_request_has_version_and_command(self):
        self.assertEqual(
            make_request("toggle", clipboard=True),
            {"protocol": PROTOCOL_VERSION, "command": "toggle", "clipboard": True},
        )

    def test_missing_protocol_is_treated_as_v1(self):
        request = validate_request({"command": "status"})
        self.assertEqual(request["command"], "status")

    def test_protocol_mismatch_is_rejected(self):
        with self.assertRaises(IpcProtocolError) as raised:
            validate_request({"protocol": 99, "command": "status"})
        self.assertEqual(raised.exception.code, "protocol_mismatch")

    def test_error_envelope_uses_stable_code(self):
        response = error("no such command", "unknown_command")
        self.assertEqual(response["protocol"], PROTOCOL_VERSION)
        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "unknown_command")

    def test_unknown_error_code_is_normalized(self):
        self.assertEqual(error("boom", "made-up")["code"], "internal_error")

    def test_response_validation_rejects_wrong_protocol(self):
        with self.assertRaises(IpcProtocolError):
            validate_response({"protocol": 2, "ok": True})
        self.assertTrue(ok(state="idle")["ok"])


class DaemonDispatchTests(unittest.TestCase):
    def test_status_snapshot(self):
        daemon, _, _ = make_daemon()
        daemon.server_ready = True
        response = asyncio.run(daemon.dispatch(make_request("status")))
        self.assertTrue(response["ok"])
        self.assertEqual(response["protocol"], PROTOCOL_VERSION)
        self.assertEqual(response["state"], "idle")
        self.assertEqual(response["language"], "en-US")
        self.assertTrue(response["server_ready"])
        self.assertEqual(response["server_status"], "online")
        self.assertIsNone(response["session"])
        self.assertIn("version", response)

    def test_unknown_command(self):
        daemon, _, _ = make_daemon()
        response = asyncio.run(daemon.dispatch(make_request("frobnicate")))
        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "unknown_command")

    def test_invalid_language_type(self):
        daemon, _, _ = make_daemon()
        response = asyncio.run(daemon.dispatch(make_request("language", language=12)))
        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "invalid_argument")

    def test_language_is_set_and_reflected_in_status(self):
        daemon, _, overlay = make_daemon()
        response = asyncio.run(daemon.dispatch(make_request("language", language="it-IT")))
        self.assertEqual(response, ok(language="it-IT"))
        self.assertEqual(daemon.cfg.language, "it-IT")
        self.assertEqual(overlay.languages, ["it-IT"])

    def test_type_requires_a_string(self):
        daemon, injection, _ = make_daemon()
        response = asyncio.run(daemon.dispatch(make_request("type", text=1)))
        self.assertEqual(response["code"], "invalid_argument")
        self.assertEqual(injection.typed, [])

    def test_type_injects_normalized_text(self):
        daemon, injection, _ = make_daemon()
        response = asyncio.run(daemon.dispatch(make_request("type", text="  hello\nworld  ")))
        self.assertTrue(response["ok"])
        self.assertEqual(injection.typed, ["hello world"])

    def test_ipc_handler_reports_invalid_json(self):
        async def scenario():
            daemon, _, _ = make_daemon()
            reader = asyncio.StreamReader()
            reader.feed_data(b"{not json}\n")
            reader.feed_eof()
            writer = CapturingWriter()
            await daemon._handle_ipc(reader, writer)
            return json.loads(writer.data), writer

        response, writer = asyncio.run(scenario())
        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "bad_request")
        self.assertTrue(writer.closed)


if __name__ == "__main__":
    unittest.main()
