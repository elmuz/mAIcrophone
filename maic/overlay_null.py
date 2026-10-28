"""Overlay implementations that do not require GTK.

Kept separate from :mod:`maic.overlay` so the daemon and CLI can run without
PyGObject/GTK (for example ``maic daemon --no-overlay`` or ``maic status``).
"""

from __future__ import annotations


class NullOverlay:
    def start(self, output): pass
    def set_partial(self, text): pass
    def set_level(self, rms): pass
    def set_state(self, state): pass
    def set_language(self, code): pass
    def set_server_status(self, status): pass
    def show_error(self, message, timeout_ms=5000): pass
    def hide(self): pass
    def destroy(self): pass
