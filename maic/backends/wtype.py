"""Text injection provider backed by ``wtype`` and a clipboard provider."""

from __future__ import annotations

import asyncio
import logging

from ..config import Config
from ..subprocess_env import child_env
from .clipboard import SubprocessClipboard
from .protocols import Clipboard, InjectionCapabilities

log = logging.getLogger("maic")


async def _run(*args: str, stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=child_env(),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=15)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, b"", b"timed out"
    return proc.returncode or 0, out, err


async def copy_to_clipboard(
    text: str,
    cfg: Config,
    clipboard: Clipboard | None = None,
    fallback: Clipboard | None = None,
) -> bool:
    """Copy to the selections requested by the config.

    ``clipboard`` is the preferred provider; ``fallback`` is tried once per
    selection if it returns false. Callers normally pass the native Wayland
    provider and keep the ``wl-copy`` subprocess only as a compatibility
    fallback.
    """
    preferred = clipboard or SubprocessClipboard()
    ok = True
    selections = []
    if cfg.copy_to_clipboard:
        selections.append(("clipboard", False))
    if cfg.primary_selection:
        selections.append(("primary selection", True))

    for label, primary in selections:
        copied = await preferred.copy(text, primary=primary)
        if not copied and fallback is not None:
            log.warning("%s copy failed; falling back to wl-copy", label)
            copied = await fallback.copy(text, primary=primary)
        if not copied:
            log.warning("%s copy failed", label)
            ok = False
    return ok


async def _wtype_text(text: str) -> tuple[int, bytes, bytes]:
    args = ["wtype"]
    if text.startswith("-"):
        args.append("--")
    args.append(text)
    return await _run(*args)


async def type_text(text: str, cfg: Config) -> bool:
    if not text:
        return True
    if cfg.multiline == "shift-enter":
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line:
                code, _, err = await _wtype_text(line)
                if code != 0:
                    log.warning("wtype failed (%s): %s", code, err.decode(errors="replace").strip())
                    return False
            if i < len(lines) - 1:
                code, _, err = await _run("wtype", "-M", "shift", "-k", "Return", "-m", "shift")
                if code != 0:
                    log.warning(
                        "wtype newline failed (%s): %s", code, err.decode(errors="replace").strip()
                    )
                    return False
        return True
    code, _, err = await _wtype_text(text)
    if code != 0:
        log.warning("wtype failed (%s): %s", code, err.decode(errors="replace").strip())
    return code == 0


class WtypeInjector:
    capabilities = InjectionCapabilities(
        supports_unicode=True,
        supports_clipboard=True,
        supports_primary_selection=True,
    )

    def __init__(self, cfg: Config, clipboard: Clipboard | None = None):
        self.cfg = cfg
        # Keep libwayland optional at import time: headless installs that use
        # the GTK provider should not fail just because the native client
        # cannot be loaded. ``wl-copy`` remains the final compatibility path.
        self.clipboard = clipboard
        if self.clipboard is None:
            try:
                from .wayland_clipboard import WaylandClipboard

                self.clipboard = WaylandClipboard()
            except Exception as exc:
                log.warning("native Wayland clipboard unavailable (%s); using wl-copy", exc)
                self.clipboard = SubprocessClipboard()
                self.clipboard_fallback = None
                return
        self.clipboard_fallback = SubprocessClipboard()

    async def type_text(self, text: str) -> bool:
        return await type_text(text, self.cfg)

    async def copy_to_clipboard(self, text: str) -> bool:
        return await copy_to_clipboard(
            text,
            self.cfg,
            clipboard=self.clipboard,
            fallback=self.clipboard_fallback,
        )
