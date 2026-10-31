from __future__ import annotations

import asyncio
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from maic.backends import sway

MAGIC = b"i3-ipc"
HEADER = struct.Struct("=II")


class SwayJsonTests(unittest.TestCase):
    def test_focused_window_is_parsed(self):
        window = sway._target_from_json(
            {
                "id": 42,
                "pid": 7,
                "app_id": "kitty",
                "name": "editor",
                "type": "con",
                "focused": True,
            }
        )
        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual(window.id, 42)
        self.assertEqual(window.app_id, "kitty")
        self.assertEqual(window.title, "editor")

    def test_x11_window_properties_are_used(self):
        window = sway._target_from_json(
            {
                "id": 43,
                "pid": 8,
                "name": "xterm",
                "type": "con",
                "focused": True,
                "window_properties": {"class": "XTerm", "title": "xterm"},
            }
        )
        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual(window.app_id, "XTerm")

    def test_workspace_is_not_a_target(self):
        self.assertIsNone(
            sway._target_from_json({"id": 1, "name": "1", "type": "workspace", "focused": True})
        )

    def test_output_rect_is_parsed(self):
        output = sway._output_from_json(
            {
                "name": "DP-1",
                "active": True,
                "focused": True,
                "rect": {"width": 2560, "height": 1440},
            }
        )
        self.assertIsNotNone(output)
        assert output is not None
        self.assertEqual((output.width, output.height), (2560, 1440))

    def test_focused_output_prefers_focused_flag(self):
        output = sway._focused_output_from_outputs(
            [
                {"name": "HDMI-A-1", "active": True, "rect": {"width": 1920, "height": 1080}},
                {
                    "name": "DP-1",
                    "active": True,
                    "focused": True,
                    "rect": {"width": 2560, "height": 1440},
                },
            ]
        )
        self.assertIsNotNone(output)
        assert output is not None
        self.assertEqual(output.name, "DP-1")


class SwaySocketDiscoveryTests(unittest.TestCase):
    def test_swaysock_environment_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "explicit.sock"
            path.touch()
            with mock.patch.dict(os.environ, {"SWAYSOCK": str(path)}):
                self.assertEqual(sway._socket_path(), path)

    def test_newest_runtime_socket_is_used_as_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "sway-ipc.1000.1.sock"
            new = Path(tmp) / "sway-ipc.1000.2.sock"
            old.touch()
            new.touch()
            os.utime(old, (1, 1))
            os.utime(new, (2, 2))
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}, clear=False):
                os.environ.pop("SWAYSOCK", None)
                self.assertEqual(sway._socket_path(), new)


class SwayIpcTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "sway.sock"
        self.requests: list[tuple[int, bytes]] = []
        self.responses: list[object] = []
        self.server = await asyncio.start_unix_server(self._handle, path=str(self.path))
        self.patch = mock.patch.object(sway, "_socket_path", return_value=self.path)
        self.patch.start()

    async def asyncTearDown(self):
        self.patch.stop()
        self.server.close()
        await self.server.wait_closed()
        self._tmp.cleanup()

    async def _handle(self, reader, writer):
        try:
            header = await reader.readexactly(len(MAGIC) + HEADER.size)
            self.assertEqual(header[: len(MAGIC)], MAGIC)
            length, message_type = HEADER.unpack(header[len(MAGIC) :])
            body = await reader.readexactly(length)
            self.requests.append((message_type, body))
            response = json.dumps(self.responses.pop(0)).encode("utf-8")
            writer.write(MAGIC + HEADER.pack(len(response), message_type) + response)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_focused_window_uses_get_tree(self):
        self.responses.append(
            {
                "id": 1,
                "type": "root",
                "nodes": [
                    {
                        "id": 2,
                        "type": "output",
                        "nodes": [
                            {
                                "id": 42,
                                "pid": 7,
                                "app_id": "kitty",
                                "name": "editor",
                                "type": "con",
                                "focused": True,
                            }
                        ],
                    }
                ],
            }
        )
        window = await sway.focused_window()
        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual(window.id, 42)
        self.assertEqual(self.requests, [(sway.GET_TREE, b"")])

    async def test_focused_output_uses_get_outputs(self):
        self.responses.append(
            [
                {
                    "name": "DP-1",
                    "active": True,
                    "focused": True,
                    "rect": {"width": 2560, "height": 1440},
                }
            ]
        )
        output = await sway.focused_output()
        self.assertIsNotNone(output)
        assert output is not None
        self.assertEqual(output.name, "DP-1")
        self.assertEqual(self.requests, [(sway.GET_OUTPUTS, b"")])

    async def test_focused_output_falls_back_to_focused_workspace(self):
        self.responses.extend(
            [
                [
                    {
                        "name": "HDMI-A-1",
                        "active": True,
                        "rect": {"width": 1920, "height": 1080},
                    },
                    {
                        "name": "DP-1",
                        "active": True,
                        "rect": {"width": 2560, "height": 1440},
                    },
                ],
                [
                    {"name": "1", "output": "HDMI-A-1", "focused": False},
                    {"name": "2", "output": "DP-1", "focused": True},
                ],
            ]
        )
        output = await sway.focused_output()
        self.assertIsNotNone(output)
        assert output is not None
        self.assertEqual(output.name, "DP-1")
        self.assertEqual(
            self.requests,
            [(sway.GET_OUTPUTS, b""), (sway.GET_WORKSPACES, b"")],
        )

    async def test_focus_action_uses_con_id(self):
        self.responses.append([{"success": True}])
        self.assertTrue(await sway.focus_window(42))
        self.assertEqual(self.requests, [(sway.RUN_COMMAND, b"[con_id=42] focus")])

    async def test_window_exists_walks_the_tree(self):
        self.responses.append(
            {
                "id": 1,
                "type": "root",
                "nodes": [{"id": 42, "type": "con", "name": "editor"}],
            }
        )
        self.assertTrue(await sway.window_exists(42))
        self.assertEqual(self.requests, [(sway.GET_TREE, b"")])


if __name__ == "__main__":
    unittest.main()
