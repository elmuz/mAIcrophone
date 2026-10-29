"""Compatibility re-exports for text injection.

New code should use :class:`maic.backends.wtype.WtypeInjector`; normalization
lives in :mod:`maic.text`.
"""

from __future__ import annotations

from .backends.wtype import WtypeInjector, copy_to_clipboard, type_text
from .text import apply_replacements, normalize_text

__all__ = [
    "WtypeInjector",
    "apply_replacements",
    "copy_to_clipboard",
    "normalize_text",
    "type_text",
]
