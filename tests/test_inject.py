from __future__ import annotations

import unittest

from maic.config import Config
from maic.inject import apply_replacements, normalize_text
from maic.marks import Mark, apply_marks, mark_char


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


class ApplyMarksTests(unittest.TestCase):
    def test_mark_is_attached_to_visible_sentence(self):
        self.assertEqual(
            apply_marks("Hello world how are you", [Mark("Hello world", ".")]),
            "Hello world. how are you",
        )

    def test_trailing_space_does_not_push_the_mark_away(self):
        self.assertEqual(
            apply_marks("Hello world and more", [Mark("Hello world ", ".")]),
            "Hello world. and more",
        )

    def test_multiple_marks_keep_their_order_and_positions(self):
        marks = [Mark("Hello world", "."), Mark("Hello world how are you", "?")]
        self.assertEqual(
            apply_marks("Hello world how are you", marks),
            "Hello world. how are you?",
        )

    def test_revised_anchor_falls_back_to_a_word_boundary(self):
        # ASR changed "world" after the mark was pressed.
        self.assertEqual(
            apply_marks("Hello word how are you", [Mark("Hello world", ".")]),
            "Hello. word how are you",
        )

    def test_unrelated_anchor_appends_at_the_end(self):
        self.assertEqual(
            apply_marks("completely different", [Mark("Hello world", "?")]),
            "completely different?",
        )

    def test_existing_punctuation_is_not_duplicated(self):
        self.assertEqual(apply_marks("Hello world.", [Mark("Hello world.", ".")]), "Hello world.")

    def test_mark_names_accept_shortcuts(self):
        self.assertEqual(mark_char("period"), ".")
        self.assertEqual(mark_char("."), ".")
        self.assertEqual(mark_char("QUESTION"), "?")
        self.assertIsNone(mark_char("comma"))


if __name__ == "__main__":
    unittest.main()
