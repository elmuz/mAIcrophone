"""Clipboard providers.

The bundled daemon uses the native Wayland data-control provider by default.
``Gdk.Clipboard`` remains available for GTK callers, and ``wl-copy`` covers
compositors without the native protocol. All implement
:class:`maic.backends.protocols.Clipboard`.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from ..subprocess_env import child_env

log = logging.getLogger("maic")


async def _run_detached(*args: str, stdin: bytes | None = None) -> int:
    """Run a command that intentionally forks a background process.

    ``wl-copy`` forks a clipboard-owner process that inherits stdout/stderr.
    If we capture those pipes, ``communicate()`` waits for EOF and therefore
    blocks until the clipboard is replaced. Redirect them to /dev/null.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        env=child_env(),
    )
    try:
        await asyncio.wait_for(proc.communicate(stdin), timeout=5)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124
    return proc.returncode or 0


class SubprocessClipboard:
    """Clipboard provider backed by ``wl-copy``."""

    async def copy(self, text: str, *, primary: bool = False) -> bool:
        args = ["wl-copy"]
        if primary:
            args.append("--primary")
        rc = await _run_detached(*args, stdin=text.encode("utf-8"))
        if rc != 0:
            log.warning("%s failed (%s)", " ".join(args), rc)
            return False
        return True


class GtkClipboard:
    """Optional clipboard provider backed by ``Gdk.Clipboard``.

    Gdk calls must run on the GTK main thread. The daemon's session loop lives
    in an asyncio thread, so copy requests are marshalled with
    ``GLib.idle_add`` and a bounded wait. If GTK is unavailable, the main loop
    is not running, or the copy times out, the optional fallback is used.
    """

    def __init__(
        self,
        fallback: SubprocessClipboard | None = None,
        timeout: float = 2.0,
    ):
        self.fallback = fallback
        self.timeout = timeout

    async def copy(self, text: str, *, primary: bool = False) -> bool:
        if await asyncio.to_thread(self._copy_on_main_loop, text, primary):
            return True
        if self.fallback is None:
            return False
        log.warning(
            "Gdk.Clipboard failed for %s; falling back to wl-copy",
            "primary selection" if primary else "clipboard",
        )
        return await self.fallback.copy(text, primary=primary)

    def _copy_on_main_loop(self, text: str, primary: bool) -> bool:
        try:
            import gi

            gi.require_version("Gdk", "4.0")
            from gi.repository import Gdk, GLib
        except (ImportError, ValueError) as exc:
            log.warning("Gdk.Clipboard unavailable: %s", exc)
            return False

        done = threading.Event()
        result = {"ok": False}

        def apply() -> bool:
            try:
                display = Gdk.Display.get_default()
                if display is None:
                    raise RuntimeError("no GDK display")
                clipboard = display.get_primary_clipboard() if primary else display.get_clipboard()
                provider = Gdk.ContentProvider.new_for_bytes(
                    "text/plain;charset=utf-8",
                    GLib.Bytes.new(text.encode("utf-8")),
                )
                # ``Gdk.Clipboard.set`` is not the content-setter in PyGObject;
                # it resolves to the generic GObject property helper and is a
                # silent no-op here. ``set_content`` takes ownership of the
                # provider and correctly claims both CLIPBOARD and PRIMARY.
                clipboard.set_content(provider)
                result["ok"] = True
            except Exception:
                log.exception("Gdk.Clipboard copy failed")
            finally:
                done.set()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(apply)
        if not done.wait(self.timeout):
            log.warning("Gdk.Clipboard timed out waiting for the GTK main loop")
            return False
        return result["ok"]
