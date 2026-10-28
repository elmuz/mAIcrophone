from __future__ import annotations

import unittest

from maic.config import Config
from maic.inject import apply_replacements, normalize_text


class NormalizeTextTests(unittest.TestCase):
    def test_replacements_are_applied(self):
        cfg = Config(replacements={"Pipewire": "PipeWire", "System D": "systemd"})
        self.assertEqual(
            apply_replacements("Pipewire and System D", cfg),
            "PipeWire and systemd",
        )

    def test_space_mode_flattens_newlines_and_whitespace(self):
        cfg = Config(multiline="space")
        self.assertEqual(
            normalize_text("  first\nsecond\r\n\tthird  ", cfg),
            "first second third",
        )

    def test_space_mode_still_applies_replacements(self):
        cfg = Config(multiline="space", replacements={"Enemmotron": "Nemotron"})
        self.assertEqual(normalize_text("Enemmotron\nworks", cfg), "Nemotron works")

    def test_shift_enter_mode_preserves_newlines(self):
        cfg = Config(multiline="shift-enter")
        self.assertEqual(
            normalize_text("  first\nsecond\n", cfg),
            "first\nsecond",
        )


if __name__ == "__main__":
    unittest.main()
