from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from maic.backends import _build_compositor
from maic.backends.auto import detect_compositor


class AutoCompositorTests(unittest.TestCase):
    def test_explicit_environment_wins(self):
        with mock.patch.dict(
            os.environ,
            {
                "NIRI_SOCKET": "/tmp/niri.sock",
                "SWAYSOCK": "/tmp/sway.sock",
                "HYPRLAND_INSTANCE_SIGNATURE": "hypr",
            },
            clear=True,
        ):
            self.assertEqual(detect_compositor(), "niri")

    def test_sway_environment_is_detected(self):
        with mock.patch.dict(
            os.environ,
            {"SWAYSOCK": "/tmp/sway.sock"},
            clear=True,
        ):
            self.assertEqual(detect_compositor(), "sway")

    def test_hyprland_environment_is_detected(self):
        with mock.patch.dict(
            os.environ,
            {"HYPRLAND_INSTANCE_SIGNATURE": "hypr"},
            clear=True,
        ):
            self.assertEqual(detect_compositor(), "hyprland")

    def test_newest_runtime_socket_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            sway_socket = Path(tmp) / "sway-ipc.1000.1.sock"
            hypr_socket = Path(tmp) / "hypr" / "instance" / ".socket.sock"
            hypr_socket.parent.mkdir(parents=True)
            sway_socket.touch()
            hypr_socket.touch()
            os.utime(sway_socket, (1, 1))
            os.utime(hypr_socket, (2, 2))
            with mock.patch.dict(
                os.environ,
                {"XDG_RUNTIME_DIR": tmp},
                clear=True,
            ):
                self.assertEqual(detect_compositor(), "hyprland")

    def test_factory_builds_the_detected_provider(self):
        with mock.patch.dict(
            os.environ,
            {"SWAYSOCK": "/tmp/sway.sock"},
            clear=True,
        ):
            compositor = _build_compositor("auto")
        self.assertEqual(type(compositor).__name__, "SwayCompositor")

    def test_missing_compositor_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ,
                {"XDG_RUNTIME_DIR": tmp},
                clear=True,
            ):
                with self.assertRaises(RuntimeError):
                    detect_compositor()


if __name__ == "__main__":
    unittest.main()
