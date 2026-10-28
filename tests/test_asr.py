from __future__ import annotations

import unittest

from maic.asr import longest_common_prefix, reconcile_partial


class LongestCommonPrefixTests(unittest.TestCase):
    def test_common_prefix(self):
        self.assertEqual(longest_common_prefix("hello world", "hello word"), 9)

    def test_no_common_prefix(self):
        self.assertEqual(longest_common_prefix("alpha", "beta"), 0)

    def test_one_is_prefix_of_other(self):
        self.assertEqual(longest_common_prefix("hello", "hello there"), 5)
        self.assertEqual(longest_common_prefix("hello there", "hello"), 5)

    def test_empty(self):
        self.assertEqual(longest_common_prefix("", "hello"), 0)
        self.assertEqual(longest_common_prefix("hello", ""), 0)


class ReconcilePartialTests(unittest.TestCase):
    def test_empty_previous_starts_with_delta(self):
        self.assertEqual(reconcile_partial("", "Hello"), "Hello")

    def test_empty_delta_is_noop(self):
        self.assertEqual(reconcile_partial("Hello", ""), "Hello")

    def test_suffix_chunks_are_appended(self):
        text = ""
        for delta in ("Hello", ", my", " name", " is", " Ada"):
            text = reconcile_partial(text, delta)
        self.assertEqual(text, "Hello, my name is Ada")

    def test_unrelated_suffix_is_appended(self):
        self.assertEqual(reconcile_partial("Hello", " and then"), "Hello and then")

    def test_full_replacement_that_extends_previous(self):
        self.assertEqual(
            reconcile_partial("Hello wo", "Hello world"),
            "Hello world",
        )

    def test_revision_in_the_middle_replaces_previous(self):
        self.assertEqual(
            reconcile_partial("Hello world", "Hello word"),
            "Hello word",
        )

    def test_shortening_revision_replaces_previous(self):
        self.assertEqual(reconcile_partial("Hello world", "Hello"), "Hello")

    def test_earlier_word_revision_with_preserved_prefix(self):
        self.assertEqual(
            reconcile_partial("I want to go", "I wanna go"),
            "I wanna go",
        )


if __name__ == "__main__":
    unittest.main()
