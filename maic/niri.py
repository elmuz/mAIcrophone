"""Compatibility re-exports for the Niri compositor provider.

New code should use :class:`maic.backends.niri.NiriCompositor` and the
protocol dataclasses in :mod:`maic.backends.protocols`.
"""

from __future__ import annotations

from .backends.niri import (
    NiriCompositor,
    current_focused_id,
    focus_window,
    focused_output,
    focused_window,
    window_exists,
)
from .backends.protocols import OutputInfo, TargetWindow

__all__ = [
    "NiriCompositor",
    "OutputInfo",
    "TargetWindow",
    "current_focused_id",
    "focus_window",
    "focused_output",
    "focused_window",
    "window_exists",
]
