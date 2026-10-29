from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from maic.config import Config, load_config


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = Config()
        self.assertEqual(cfg.language, "en-US")
        self.assertEqual(cfg.chunk_bytes, 3200)
        self.assertEqual(
            cfg.ready_url,
            "http://127.0.0.1:8080/ready",
        )

    def test_ready_url_for_wss(self):
        cfg = Config(server_url="wss://example.test/v1/audio/transcriptions/realtime")
        self.assertEqual(cfg.ready_url, "https://example.test/ready")

    def test_file_overrides_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                """
[server]
url = "ws://127.0.0.1:9999/v1/audio/transcriptions/realtime"
poll_seconds = 2

[audio]
sample_rate = 16000
chunk_ms = 100

[asr]
language = "it-IT"

[injection]
multiline = "shift-enter"

[replacements]
"Pipewire" = "PipeWire"
""",
                encoding="utf-8",
            )
            cfg = load_config(path)

        self.assertEqual(cfg.server_url, "ws://127.0.0.1:9999/v1/audio/transcriptions/realtime")
        self.assertEqual(cfg.ready_url, "http://127.0.0.1:9999/ready")
        self.assertEqual(cfg.server_poll_seconds, 2)
        self.assertEqual(cfg.language, "it-IT")
        self.assertEqual(cfg.multiline, "shift-enter")
        self.assertEqual(cfg.replacements, {"Pipewire": "PipeWire"})

    def test_overlay_colors_are_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                """
[overlay.colors]
background = "#1d2021"
border = "#7c6f64"
text = "#ebdbb2"
partial = "#a89984"
accent = "#d79921"
divider = "#3c3836"
success = "#b8bb26"
warning = "#fabd2f"
error = "#fb4934"
cancel = "#fe8019"
""",
                encoding="utf-8",
            )
            cfg = load_config(path)

        self.assertEqual(cfg.overlay_background_color, "#1d2021")
        self.assertEqual(cfg.overlay_border_color, "#7c6f64")
        self.assertEqual(cfg.overlay_text_color, "#ebdbb2")
        self.assertEqual(cfg.overlay_accent_color, "#d79921")

    def test_invalid_overlay_color_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                '[overlay.colors]\nborder = "not-a-color"\n',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_config(path)

    def test_invalid_multiline_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                '[injection]\nmultiline = "carrier-pigeon"\n',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_config(path)

    def test_environment_overrides_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                '[server]\nurl = "ws://127.0.0.1:1111/v1/audio/x"\n',
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "MAIC_SERVER_URL": "ws://127.0.0.1:2222/v1/audio/x",
                    "MAIC_LANGUAGE": "auto",
                },
                clear=False,
            ):
                cfg = load_config(path)

        self.assertEqual(cfg.server_url, "ws://127.0.0.1:2222/v1/audio/x")
        self.assertEqual(cfg.language, "auto")


if __name__ == "__main__":
    unittest.main()
