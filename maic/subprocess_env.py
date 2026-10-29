from __future__ import annotations

import os


def child_env() -> dict[str, str]:
    """Environment for helper processes.

    The daemon itself needs LD_PRELOAD for gtk4-layer-shell, but children such
    as wtype, pw-record and wl-copy must not inherit it. Loading the GTK
    layer-shell library into unrelated Wayland clients can make them misbehave
    or hang.
    """
    env = os.environ.copy()
    env.pop("LD_PRELOAD", None)
    return env
