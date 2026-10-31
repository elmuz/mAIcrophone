"""Compositor auto-detection for ``backend.compositor = "auto"``.

Detection uses the same public environment variables and runtime socket paths
as the individual providers. It never launches ``niri``, ``swaymsg``, or
``hyprctl``.
"""

from __future__ import annotations

import os
from pathlib import Path


def _runtime_candidates(runtime: str) -> list[tuple[float, str]]:
    patterns = (
        ("niri*.sock", "niri"),
        ("sway-ipc.*.sock", "sway"),
        ("hypr/*/.socket.sock", "hyprland"),
    )
    candidates: list[tuple[float, str]] = []
    base = Path(runtime)
    for pattern, name in patterns:
        for path in base.glob(pattern):
            try:
                candidates.append((path.stat().st_mtime, name))
            except FileNotFoundError:
                continue
    return candidates


def detect_compositor() -> str:
    """Return the compositor backend matching the current session.

    Explicit environment variables win because they identify the session even
    when several stale sockets exist in the runtime directory. Otherwise the
    newest known compositor socket is selected.
    """
    if os.environ.get("NIRI_SOCKET"):
        return "niri"
    if os.environ.get("SWAYSOCK"):
        return "sway"
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or os.environ.get("HIS"):
        return "hyprland"

    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        candidates = _runtime_candidates(runtime)
        if candidates:
            return max(candidates, key=lambda item: item[0])[1]

    raise RuntimeError(
        "cannot detect the compositor; set backend.compositor to niri, sway, or hyprland explicitly"
    )
