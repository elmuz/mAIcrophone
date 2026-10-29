"""Compatibility re-exports for the PipeWire audio provider.

New code should use :class:`maic.backends.pipewire.PwRecordAudioCapture`.
"""

from __future__ import annotations

from .backends.pipewire import PwRecordAudioCapture as AudioCapture
from .backends.protocols import AudioStats

__all__ = ["AudioCapture", "AudioStats"]
