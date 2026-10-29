from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from maic.backends import niri
from maic.backends.pulse import PulseAudioCapture
from maic.config import Config
from maic.backends.protocols import AudioStats


class NiriJsonTests(unittest.TestCase):
    def test_focused_window_is_parsed(self):
        window = niri._target_from_json(
            {
                "id": 42,
                "pid": 7,
                "app_id": "kitty",
                "title": "editor",
                "is_focused": True,
            }
        )
        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual(window.id, 42)
        self.assertEqual(window.app_id, "kitty")

    def test_unfocused_window_is_ignored(self):
        self.assertIsNone(
            niri._target_from_json({"id": 42, "is_focused": False})
        )

    def test_output_logical_size_is_parsed(self):
        output = niri._output_from_json(
            {"name": "DP-1", "logical": {"width": 1920, "height": 1080}}
        )
        self.assertIsNotNone(output)
        assert output is not None
        self.assertEqual((output.width, output.height), (1920, 1080))


class NiriSocketDiscoveryTests(unittest.TestCase):
    def test_niri_socket_environment_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "explicit.sock"
            path.touch()
            with mock.patch.dict(os.environ, {"NIRI_SOCKET": str(path)}):
                self.assertEqual(niri._socket_path(), path)

    def test_newest_runtime_socket_is_used_as_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "niri.wayland-1.1.sock"
            new = Path(tmp) / "niri.wayland-1.2.sock"
            old.touch()
            new.touch()
            os.utime(old, (1, 1))
            os.utime(new, (2, 2))
            env = {"XDG_RUNTIME_DIR": tmp}
            with mock.patch.dict(os.environ, env, clear=False):
                os.environ.pop("NIRI_SOCKET", None)
                self.assertEqual(niri._socket_path(), new)


class PulseStatsTests(unittest.TestCase):
    def test_stats_are_computed_from_pcm16(self):
        data = (100).to_bytes(2, "little", signed=True) * 4
        stats = PulseAudioCapture.stats(data)
        self.assertAlmostEqual(stats.rms, 100.0)
        self.assertEqual(stats.peak, 100)

    def test_empty_input_has_zero_stats(self):
        self.assertEqual(PulseAudioCapture.stats(b""), AudioStats())


if __name__ == "__main__":
    unittest.main()


class NiriIpcTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "niri.sock"
        self.requests: list[object] = []
        self.responses: list[object] = []
        self.server = await asyncio.start_unix_server(
            self._handle, path=str(self.path)
        )
        self.patch = mock.patch.object(niri, "_socket_path", return_value=self.path)
        self.patch.start()

    async def asyncTearDown(self):
        self.patch.stop()
        self.server.close()
        await self.server.wait_closed()
        self._tmp.cleanup()

    async def _handle(self, reader, writer):
        try:
            line = await reader.readline()
            self.requests.append(json.loads(line))
            response = self.responses.pop(0)
            writer.write((json.dumps(response) + "\n").encode("utf-8"))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_unit_query_uses_canonical_request_and_unwraps_reply(self):
        self.responses.append(
            {
                "Ok": {
                    "FocusedWindow": {
                        "id": 42,
                        "pid": 7,
                        "app_id": "kitty",
                        "title": "editor",
                        "is_focused": True,
                    }
                }
            }
        )
        window = await niri.focused_window()
        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual(window.id, 42)
        self.assertEqual(self.requests, ["FocusedWindow"])

    async def test_focus_action_sends_window_id(self):
        self.responses.append({"Ok": "Handled"})
        self.assertTrue(await niri.focus_window(42))
        self.assertEqual(
            self.requests, [{"Action": {"FocusWindow": {"id": 42}}}]
        )

    async def test_window_exists_tolerates_malformed_entries(self):
        self.responses.append(
            {
                "Ok": {
                    "Windows": [
                        {"id": "not-an-id"},
                        "not-a-dict",
                        {"id": 42},
                    ]
                }
            }
        )
        self.assertTrue(await niri.window_exists(42))
        self.assertEqual(self.requests, ["Windows"])

    async def test_error_reply_is_raised_by_request(self):
        self.responses.append({"Err": "no such window"})
        with self.assertRaises(RuntimeError):
            await niri._request("FocusedWindow")


class _FakePulseSimple:
    def __init__(self, fail_after: int | None = None):
        self.fail_after = fail_after
        self.reads = 0
        self.freed: list[int] = []
        self.simple = self
        self.pulse = self

    def pa_simple_new(self, *args):
        return 123

    def pa_simple_read(self, stream, buffer, size, error):
        self.reads += 1
        if self.fail_after is not None and self.reads > self.fail_after:
            error._obj.value = -1
            return -1
        data = (b"\x01\x00" * ((size + 1) // 2))[:size]
        import ctypes

        ctypes.memmove(buffer, data, size)
        return 0

    def pa_simple_free(self, stream):
        self.freed.append(stream)

    def pa_strerror(self, code):
        return b"fake pulse error"

    def error_text(self, code):
        return "fake pulse error"


class PulseCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_delivers_chunks_and_frees_the_stream(self):
        fake = _FakePulseSimple()
        with mock.patch("maic.backends.pulse._PulseSimple", return_value=fake):
            capture = PulseAudioCapture(Config())
            await capture.start()
            chunk = await asyncio.wait_for(capture.read_chunk(), timeout=2)
            await capture.stop()

        self.assertEqual(len(chunk), Config().chunk_bytes)
        self.assertEqual(fake.freed, [123])
        self.assertIsNone(capture._reader)

    async def test_reader_error_is_reported_to_the_consumer(self):
        fake = _FakePulseSimple(fail_after=0)
        with mock.patch("maic.backends.pulse._PulseSimple", return_value=fake):
            capture = PulseAudioCapture(Config())
            await capture.start()
            with self.assertRaises(RuntimeError):
                await asyncio.wait_for(capture.read_chunk(), timeout=2)
            await capture.stop()

        self.assertEqual(fake.freed, [123])
