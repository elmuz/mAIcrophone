from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from maic.backends import hyprland


class HyprlandJsonTests(unittest.TestCase):
    def test_focused_window_is_parsed(self):
        window = hyprland._target_from_json(
            {
                "address": "0x55d1234",
                "pid": 7,
                "class": "kitty",
                "title": "editor",
            }
        )
        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual(window.id, 0x55D1234)
        self.assertEqual(window.app_id, "kitty")

    def test_no_active_window_is_ignored(self):
        self.assertIsNone(hyprland._target_from_json({"address": "0x0"}))

    def test_monitor_geometry_is_parsed(self):
        output = hyprland._output_from_json(
            {"name": "DP-1", "width": 2560, "height": 1440, "focused": True}
        )
        self.assertIsNotNone(output)
        assert output is not None
        self.assertEqual((output.width, output.height), (2560, 1440))

    def test_focused_output_prefers_focused_monitor(self):
        output = hyprland._focused_output_from_monitors(
            [
                {"name": "HDMI-A-1", "width": 1920, "height": 1080, "focused": False},
                {"name": "DP-1", "width": 2560, "height": 1440, "focused": True},
            ]
        )
        self.assertIsNotNone(output)
        assert output is not None
        self.assertEqual(output.name, "DP-1")


class HyprlandSocketDiscoveryTests(unittest.TestCase):
    def test_environment_signature_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hypr" / "instance" / ".socket.sock"
            path.parent.mkdir(parents=True)
            path.touch()
            with mock.patch.dict(
                os.environ,
                {
                    "XDG_RUNTIME_DIR": tmp,
                    "HYPRLAND_INSTANCE_SIGNATURE": "instance",
                },
                clear=False,
            ):
                self.assertEqual(hyprland._socket_path(), path)

    def test_newest_runtime_socket_is_used_as_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "hypr" / "old" / ".socket.sock"
            new = Path(tmp) / "hypr" / "new" / ".socket.sock"
            old.parent.mkdir(parents=True)
            new.parent.mkdir(parents=True)
            old.touch()
            new.touch()
            os.utime(old, (1, 1))
            os.utime(new, (2, 2))
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}, clear=False):
                os.environ.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
                os.environ.pop("HIS", None)
                self.assertEqual(hyprland._socket_path(), new)


class HyprlandIpcTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "hypr.sock"
        self.requests: list[str] = []
        self.responses: list[str] = []
        self.server = await asyncio.start_unix_server(self._handle, path=str(self.path))
        self.patch = mock.patch.object(hyprland, "_socket_path", return_value=self.path)
        self.patch.start()

    async def asyncTearDown(self):
        self.patch.stop()
        self.server.close()
        await self.server.wait_closed()
        self._tmp.cleanup()

    async def _handle(self, reader, writer):
        try:
            command = await reader.read()
            self.requests.append(command.decode("utf-8"))
            writer.write(self.responses.pop(0).encode("utf-8"))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_focused_window_uses_json_activewindow(self):
        self.responses.append(
            json.dumps(
                {
                    "address": "0x55d1234",
                    "pid": 7,
                    "class": "kitty",
                    "title": "editor",
                }
            )
        )
        window = await hyprland.focused_window()
        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual(window.id, 0x55D1234)
        self.assertEqual(self.requests, ["j/activewindow"])

    async def test_focused_output_uses_json_monitors(self):
        self.responses.append(
            json.dumps(
                [
                    {"name": "HDMI-A-1", "width": 1920, "height": 1080, "focused": False},
                    {"name": "DP-1", "width": 2560, "height": 1440, "focused": True},
                ]
            )
        )
        output = await hyprland.focused_output()
        self.assertIsNotNone(output)
        assert output is not None
        self.assertEqual(output.name, "DP-1")
        self.assertEqual(self.requests, ["j/monitors"])

    async def test_focus_action_uses_lua_dispatch(self):
        self.responses.append("ok")
        self.assertTrue(await hyprland.focus_window(0x55D1234))
        self.assertEqual(
            self.requests,
            ['dispatch hl.dsp.focus({ window = "address:0x55d1234" })'],
        )

    async def test_focus_action_falls_back_to_legacy_dispatch(self):
        self.responses.extend(["error: unknown hl", "ok"])
        self.assertTrue(await hyprland.focus_window(0x55D1234))
        self.assertEqual(
            self.requests,
            [
                'dispatch hl.dsp.focus({ window = "address:0x55d1234" })',
                "dispatch focuswindow address:0x55d1234",
            ],
        )

    async def test_window_exists_compares_parsed_addresses(self):
        self.responses.append(
            json.dumps(
                [
                    {"address": "0x1", "class": "a"},
                    {"address": "0x55d1234", "class": "kitty"},
                ]
            )
        )
        self.assertTrue(await hyprland.window_exists(0x55D1234))
        self.assertEqual(self.requests, ["j/clients"])


if __name__ == "__main__":
    unittest.main()
