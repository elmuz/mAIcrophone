"""Compatibility re-exports for the nemo-speech ASR provider.

New code should use :class:`maic.backends.nemo.NemoSpeechProvider`.
"""

from __future__ import annotations

from .backends.nemo import (
    NemoRealtimeSession,
    NemoSpeechProvider,
    check_ready,
    longest_common_prefix,
    reconcile_partial,
)

# Historical name for the concrete nemo-speech session.
RealtimeAsr = NemoRealtimeSession

__all__ = [
    "NemoRealtimeSession",
    "NemoSpeechProvider",
    "RealtimeAsr",
    "check_ready",
    "longest_common_prefix",
    "reconcile_partial",
]
