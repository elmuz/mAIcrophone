from __future__ import annotations

import asyncio
import unittest

from maic.backends.wtype import WtypeInjector, copy_to_clipboard
from maic.config import Config


class FakeClipboard:
    def __init__(self, results: list[bool] | None = None):
        self.results = list(results or [True])
        self.calls: list[tuple[str, bool]] = []

    async def copy(self, text: str, *, primary: bool = False) -> bool:
        self.calls.append((text, primary))
        if not self.results:
            return True
        return self.results.pop(0)


class ClipboardSelectionTests(unittest.TestCase):
    def test_both_selections_are_written(self):
        preferred = FakeClipboard()
        cfg = Config(copy_to_clipboard=True, primary_selection=True)
        ok = asyncio.run(copy_to_clipboard("hello", cfg, preferred))
        self.assertTrue(ok)
        self.assertEqual(preferred.calls, [("hello", False), ("hello", True)])

    def test_disabled_selections_do_not_call_the_provider(self):
        preferred = FakeClipboard()
        cfg = Config(copy_to_clipboard=False, primary_selection=False)
        ok = asyncio.run(copy_to_clipboard("hello", cfg, preferred))
        self.assertTrue(ok)
        self.assertEqual(preferred.calls, [])

    def test_failed_preferred_write_falls_back_per_selection(self):
        preferred = FakeClipboard([False, False])
        fallback = FakeClipboard()
        cfg = Config(copy_to_clipboard=True, primary_selection=True)
        ok = asyncio.run(
            copy_to_clipboard("hello", cfg, preferred, fallback=fallback)
        )
        self.assertTrue(ok)
        self.assertEqual(preferred.calls, [("hello", False), ("hello", True)])
        self.assertEqual(fallback.calls, [("hello", False), ("hello", True)])

    def test_failure_without_fallback_is_reported(self):
        preferred = FakeClipboard([False])
        cfg = Config(copy_to_clipboard=True, primary_selection=False)
        self.assertFalse(asyncio.run(copy_to_clipboard("hello", cfg, preferred)))


class InjectorClipboardTests(unittest.TestCase):
    def test_injector_uses_the_preferred_provider(self):
        preferred = FakeClipboard()
        cfg = Config(copy_to_clipboard=True, primary_selection=False)
        injector = WtypeInjector(cfg, clipboard=preferred)
        self.assertTrue(asyncio.run(injector.copy_to_clipboard("hello")))
        self.assertEqual(preferred.calls, [("hello", False)])

    def test_injector_defaults_to_the_subprocess_provider(self):
        injector = WtypeInjector(Config())
        self.assertEqual(type(injector.clipboard).__name__, "SubprocessClipboard")
        self.assertIsNone(injector.clipboard_fallback)


if __name__ == "__main__":
    unittest.main()
