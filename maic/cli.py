from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from . import __version__
from .backends import build_backends
from .config import load_config
from .daemon import Daemon, socket_path
from .overlay_null import NullOverlay

log = logging.getLogger("maic")


def _maybe_reexec_with_layer_shell() -> None:
    """gtk4-layer-shell must be linked before libwayland-client.

    The Arch package is linked after GTK's libwayland, so LD_PRELOAD is the
    documented workaround. Re-exec before importing gi/Gtk.
    """
    lib = os.environ.get(
        "MAIC_LAYER_SHELL_PRELOAD", "/usr/lib/libgtk4-layer-shell.so"
    )
    if not os.path.exists(lib):
        return
    preload = os.environ.get("LD_PRELOAD", "")
    if lib in preload.split():
        return
    env = os.environ.copy()
    env["LD_PRELOAD"] = f"{lib} {preload}".strip()
    os.execvpe(sys.executable, [sys.executable, "-m", "maic", *sys.argv[1:]], env)


def _request(command: str, **extra) -> dict:
    async def run() -> dict:
        reader, writer = await asyncio.open_unix_connection(socket_path())
        try:
            payload = {"command": command, **extra}
            writer.write((json.dumps(payload) + "\n").encode("utf-8"))
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=3)
            if not line:
                raise RuntimeError("empty response from daemon")
            return json.loads(line)
        finally:
            writer.close()
            await writer.wait_closed()

    return asyncio.run(run())


def _print_response(response: dict) -> int:
    if response.get("ok"):
        if "state" in response:
            print(response["state"])
        elif "language" in response:
            print(response["language"])
        else:
            print("ok")
        return 0
    print(f"maic: {response.get('error', 'unknown error')}", file=sys.stderr)
    return 1


def _load_gtk():
    """Import GTK only for the overlay path.

    Importing ``gi`` loads GTK and, on Arch, triggers the layer-shell
    ``LD_PRELOAD`` workaround. Headless commands must not pay that cost or
    require the GUI stack to be installed.
    """
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk  # noqa: E402

    return GLib, Gtk


def _build_overlay(cfg):
    if not cfg.overlay_enabled or cfg.backend_overlay == "null":
        return NullOverlay()

    # Re-exec before importing gi/Gtk so gtk4-layer-shell is preloaded.
    _maybe_reexec_with_layer_shell()
    _GLib, Gtk = _load_gtk()
    Gtk.init()

    from .overlay import Overlay

    return Overlay(cfg)


def _run_headless_loop(daemon: Daemon) -> None:
    """Block until SIGINT/SIGTERM without requiring GLib/GTK."""
    stop = threading.Event()

    def request_stop(*_args) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    log.info("mAIcrophone %s daemon ready (headless)", __version__)
    try:
        while not stop.wait(timeout=1.0):
            pass
    finally:
        daemon.stop()


def _run_gtk_loop(daemon: Daemon, overlay) -> None:
    GLib, _Gtk = _load_gtk()
    loop = GLib.MainLoop()

    def quit_loop(*_args) -> bool:
        loop.quit()
        return GLib.SOURCE_REMOVE

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, quit_loop)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, quit_loop)

    log.info("mAIcrophone %s daemon ready", __version__)
    try:
        loop.run()
    finally:
        daemon.stop()
        overlay.destroy()


def _run_daemon(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config).expanduser() if args.config else None)
    if args.no_overlay:
        cfg.overlay_enabled = False

    try:
        overlay = _build_overlay(cfg)
    except Exception as exc:
        print(f"maic: {exc}", file=sys.stderr)
        return 1

    try:
        backends = build_backends(cfg)
    except Exception as exc:
        print(f"maic: {exc}", file=sys.stderr)
        overlay.destroy()
        return 1

    daemon = Daemon(cfg, backends, overlay)
    try:
        daemon.start()
    except Exception as exc:
        print(f"maic: failed to start daemon: {exc}", file=sys.stderr)
        overlay.destroy()
        return 1

    if cfg.overlay_enabled:
        _run_gtk_loop(daemon, overlay)
    else:
        _run_headless_loop(daemon)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="maic", description="mAIcrophone dictation")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    daemon = sub.add_parser("daemon", help="run the dictation daemon")
    daemon.add_argument("--config", help="config file path")
    daemon.add_argument("--no-overlay", action="store_true", help="disable the overlay")

    toggle = sub.add_parser("toggle", help="start or stop recording")
    toggle.add_argument("--clipboard", action="store_true", help="initially copy mode")
    sub.add_parser("stop", help="stop recording and commit")
    sub.add_parser("cancel", help="discard the current recording")
    sub.add_parser("status", help="print daemon state")
    language_cmd = sub.add_parser("language", help="get or set the ASR language")
    language_cmd.add_argument("code", nargs="?", help="e.g. en-US, it-IT, auto")
    type_cmd = sub.add_parser("type", help="type text via the daemon (diagnostic)")
    type_cmd.add_argument("text")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("MAIC_LOG", "INFO").upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.command == "daemon":
        return _run_daemon(args)

    try:
        if args.command == "toggle":
            response = _request("toggle", clipboard=args.clipboard)
        elif args.command == "language":
            response = _request("language", language=args.code or "")
        elif args.command == "type":
            response = _request("type", text=args.text)
        else:
            response = _request(args.command)
    except FileNotFoundError:
        print("maic: daemon is not running", file=sys.stderr)
        return 1
    except ConnectionRefusedError:
        print("maic: daemon is not running", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"maic: {exc}", file=sys.stderr)
        return 1
    return _print_response(response)


if __name__ == "__main__":
    raise SystemExit(main())
