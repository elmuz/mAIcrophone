#!/usr/bin/env python3
"""Live compositor smoke tests for mAIcrophone.

This script starts real headless/nested compositor sessions, creates two GTK
windows, and exercises the same compositor backend methods the daemon uses:
focused window/output, window existence, current focus, and refocus.

It is intentionally separate from the fast unit-test suite. Run it on a machine
with Sway, Hyprland, Niri, GTK4, and PyGObject installed:

    python tests/integration/compositor_smoke.py sway
    python tests/integration/compositor_smoke.py niri
    python tests/integration/compositor_smoke.py all
    python tests/integration/compositor_smoke.py hyprland

Sway runs directly with its headless backend. Niri is started as a nested
compositor inside a headless Sway session. ``all`` runs Sway and Niri.

The Hyprland test runs inside an existing real Hyprland session: Hyprland 0.57
requires newer Wayland protocol versions than the headless Sway/Niri parents
used here advertise, so it cannot be nested in the same way without a DRM
renderer or a self-hosted desktop runner.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maic.backends import hyprland, niri, sway  # noqa: E402

WINDOW_SCRIPT = """\
import sys
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


class TestWindow(Gtk.Application):
    def __init__(self, title):
        super().__init__(application_id=f"test.maic.{title}")
        self.title = title

    def do_activate(self):
        window = Gtk.ApplicationWindow(application=self, title=self.title)
        window.set_default_size(400, 300)
        window.present()


TestWindow(sys.argv[1]).run()
"""


class SmokeError(RuntimeError):
    pass


class ManagedProcess:
    def __init__(self, name: str, argv: list[str], *, env: dict[str, str], log_path: Path):
        self.name = name
        self.log_path = log_path
        self._log = log_path.open("wb")
        self.proc = subprocess.Popen(
            argv,
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def stop(self) -> None:
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.proc.wait(timeout=5)
        self._log.close()

    def has_exited(self) -> bool:
        return self.proc.poll() is not None

    def tail(self, lines: int = 120) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ""
        return "\n".join(text.splitlines()[-lines:])


class Runtime:
    def __init__(self):
        # Hyprland embeds a long instance signature in its socket path. Keep
        # the runtime directory itself short and directly under /tmp.
        self.root = Path(tempfile.mkdtemp(prefix="maic-"))
        self.home = self.root / "home"
        self.runtime_dir = self.root
        self.logs = self.root / "logs"
        self.home.mkdir()
        self.logs.mkdir()
        for name in (".config", ".cache", ".local/share", ".local/state"):
            (self.home / name).mkdir(parents=True, exist_ok=True)

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def base_env(self) -> dict[str, str]:
        env = os.environ.copy()
        for key in (
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "SWAYSOCK",
            "NIRI_SOCKET",
            "HYPRLAND_INSTANCE_SIGNATURE",
            "HIS",
            "XDG_CURRENT_DESKTOP",
            "XDG_SESSION_TYPE",
            "LD_PRELOAD",
        ):
            env.pop(key, None)
        env.update(
            {
                "HOME": str(self.home),
                "XDG_RUNTIME_DIR": str(self.runtime_dir),
                "XDG_CONFIG_HOME": str(self.home / ".config"),
                "XDG_CACHE_HOME": str(self.home / ".cache"),
                "XDG_DATA_HOME": str(self.home / ".local/share"),
                "XDG_STATE_HOME": str(self.home / ".local/state"),
                "XDG_SESSION_TYPE": "wayland",
                "LIBGL_ALWAYS_SOFTWARE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "NO_AT_BRIDGE": "1",
                "GTK_A11Y": "none",
            }
        )
        return env

    def log(self, name: str) -> Path:
        return self.logs / f"{name}.log"


def _is_socket(path: Path) -> bool:
    try:
        return stat.S_ISSOCK(path.stat().st_mode)
    except FileNotFoundError:
        return False


def _find_socket(directory: Path, pattern: str, *, exclude: set[Path] | None = None) -> Path | None:
    exclude = exclude or set()
    candidates = sorted(
        (path for path in directory.glob(pattern) if path not in exclude and _is_socket(path)),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _wait_for_path(
    process: ManagedProcess,
    path_getter,
    *,
    description: str,
    timeout: float = 15.0,
) -> Path:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        path = path_getter()
        if path is not None:
            return path
        if process.has_exited():
            raise SmokeError(
                f"{process.name} exited while waiting for {description}\n{process.tail()}"
            )
        time.sleep(0.05)
    raise SmokeError(f"timed out waiting for {description}\n{process.tail()}")


def _wait_for_wayland_socket(
    runtime: Runtime,
    process: ManagedProcess,
    *,
    exclude: set[Path],
    description: str,
) -> Path:
    return _wait_for_path(
        process,
        lambda: _find_socket(runtime.runtime_dir, "wayland-*", exclude=exclude),
        description=description,
    )


def _sway_config(runtime: Runtime) -> Path:
    path = runtime.root / "sway.conf"
    path.write_text(
        "output HEADLESS-1 resolution 1920x1080\nxwayland disable\n",
        encoding="utf-8",
    )
    return path


def _start_sway(runtime: Runtime) -> tuple[ManagedProcess, Path, dict[str, str]]:
    env = runtime.base_env()
    env.update(
        {
            "WLR_BACKENDS": "headless",
            "WLR_HEADLESS_OUTPUTS": "1",
            "WLR_LIBINPUT_NO_DEVICES": "1",
            "WLR_RENDERER": "pixman",
            "XDG_CURRENT_DESKTOP": "sway",
        }
    )
    process = ManagedProcess(
        "sway",
        ["sway", "-c", str(_sway_config(runtime))],
        env=env,
        log_path=runtime.log("sway"),
    )
    try:
        socket = _wait_for_path(
            process,
            lambda: _find_socket(runtime.runtime_dir, "sway-ipc.*.sock"),
            description="Sway IPC socket",
        )
        display = _wait_for_wayland_socket(
            runtime,
            process,
            exclude=set(),
            description="Sway Wayland socket",
        )
    except Exception:
        process.stop()
        raise
    env["SWAYSOCK"] = str(socket)
    env["WAYLAND_DISPLAY"] = display.name
    return process, display, env


def _start_niri(
    runtime: Runtime, parent_display: Path, parent_env: dict[str, str]
) -> tuple[ManagedProcess, Path, dict[str, str]]:
    env = parent_env.copy()
    env.pop("SWAYSOCK", None)
    env.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
    env.update(
        {
            "WAYLAND_DISPLAY": parent_display.name,
            "XDG_CURRENT_DESKTOP": "niri",
        }
    )
    config = runtime.root / "niri.kdl"
    config.write_text("", encoding="utf-8")
    process = ManagedProcess(
        "niri",
        ["niri", "-c", str(config)],
        env=env,
        log_path=runtime.log("niri"),
    )
    try:
        socket = _wait_for_path(
            process,
            lambda: _find_socket(runtime.runtime_dir, "niri*.sock"),
            description="Niri IPC socket",
        )
        display = _wait_for_wayland_socket(
            runtime,
            process,
            exclude={parent_display},
            description="Niri Wayland socket",
        )
    except Exception:
        process.stop()
        raise
    env["NIRI_SOCKET"] = str(socket)
    env.pop("SWAYSOCK", None)
    env["WAYLAND_DISPLAY"] = display.name
    return process, display, env


def _start_windows(runtime: Runtime, env: dict[str, str]) -> list[ManagedProcess]:
    script = runtime.root / "window.py"
    script.write_text(WINDOW_SCRIPT, encoding="utf-8")
    window_env = env.copy()
    window_env.update({"GDK_BACKEND": "wayland", "GSK_RENDERER": "cairo"})
    windows = []
    for title in ("smoke-one", "smoke-two"):
        windows.append(
            ManagedProcess(
                title,
                [sys.executable, str(script), title],
                env=window_env,
                log_path=runtime.log(title),
            )
        )
    return windows


def _set_backend_env(env: dict[str, str]) -> None:
    for key in ("SWAYSOCK", "NIRI_SOCKET", "HYPRLAND_INSTANCE_SIGNATURE", "HIS"):
        os.environ.pop(key, None)
    for key in (
        "SWAYSOCK",
        "NIRI_SOCKET",
        "HYPRLAND_INSTANCE_SIGNATURE",
        "XDG_RUNTIME_DIR",
        "WAYLAND_DISPLAY",
    ):
        if key in env:
            os.environ[key] = env[key]


async def _sway_ids() -> list[int]:
    tree = await sway._request(sway.GET_TREE)
    return [
        int(node["id"])
        for node in sway._walk(tree)
        if node.get("type") == "con" and node.get("id") is not None
    ]


async def _hyprland_ids() -> list[int]:
    clients = await hyprland._request_json("j/clients")
    if not isinstance(clients, list):
        return []
    ids = []
    for client in clients:
        if not isinstance(client, dict):
            continue
        try:
            ids.append(int(client.get("address", "0x0"), 16))
        except (TypeError, ValueError):
            continue
    return ids


async def _niri_ids() -> list[int]:
    windows = await niri._request("Windows")
    if not isinstance(windows, list):
        return []
    ids = []
    for window in windows:
        if not isinstance(window, dict):
            continue
        try:
            ids.append(int(window["id"]))
        except (KeyError, TypeError, ValueError):
            continue
    return ids


async def _wait_async(predicate, *, description: str, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await predicate()
        if last:
            return last
        await asyncio.sleep(0.05)
    raise SmokeError(f"timed out waiting for {description}; last value: {last!r}")


async def _check_compositor(
    name: str, module, list_ids, *, candidates: set[int] | None = None
) -> None:
    async def candidate_ids() -> list[int]:
        ids = await list_ids()
        if candidates is not None:
            ids = [window_id for window_id in ids if window_id in candidates]
        return ids

    ids = await _wait_async(
        lambda: _nonempty_ids(candidate_ids, minimum=2),
        description=f"{name} windows",
    )

    async def focused_candidate():
        focused = await module.focused_window()
        return focused if focused is not None and focused.id in ids else None

    focused = await _wait_async(
        focused_candidate,
        description=f"{name} focused candidate window",
    )
    output = await module.focused_output()
    if output is None or output.width <= 0 or output.height <= 0:
        raise SmokeError(f"{name}: invalid focused output: {output!r}")

    target = next(window_id for window_id in ids if window_id != focused.id)
    exists = await _wait_async(
        lambda: _is_true(module.window_exists(target)),
        description=f"{name} target window existence",
    )
    if not exists:
        raise SmokeError(f"{name}: target window {target} does not exist")

    if not await module.focus_window(target):
        raise SmokeError(f"{name}: refocus request failed for {target}")
    now = await _wait_async(
        lambda: _equal_id(module.current_focused_id(), target),
        description=f"{name} focus confirmation",
    )
    if now != target:
        raise SmokeError(f"{name}: expected focus {target}, got {now}")

    print(f"[{name}] windows: {ids}")
    print(f"[{name}] focused: {focused}")
    print(f"[{name}] output: {output}")
    print(f"[{name}] refocus: ok ({target})")


async def _nonempty_ids(list_ids, *, minimum: int) -> list[int]:
    ids = await list_ids()
    return ids if len(ids) >= minimum else []


async def _is_true(value):
    return bool(await value)


async def _equal_id(value, expected: int):
    result = await value
    return result if result == expected else None


async def _new_hyprland_ids(before: set[int]) -> list[int]:
    return [window_id for window_id in await _hyprland_ids() if window_id not in before]


def _run_existing_hyprland() -> None:
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        raise SmokeError(
            "the hyprland smoke test must run inside a real Hyprland session "
            "(HYPRLAND_INSTANCE_SIGNATURE is not set)"
        )
    if not os.environ.get("WAYLAND_DISPLAY"):
        raise SmokeError("the hyprland smoke test requires WAYLAND_DISPLAY")

    runtime = Runtime()
    windows: list[ManagedProcess] = []
    try:
        before = set(asyncio.run(_hyprland_ids()))
        windows = _start_windows(runtime, os.environ.copy())
        candidates = set(
            asyncio.run(
                _wait_async(
                    lambda: _two_new_ids(before),
                    description="two new Hyprland windows",
                )
            )
        )
        asyncio.run(
            _check_compositor(
                "hyprland",
                hyprland,
                _hyprland_ids,
                candidates=candidates,
            )
        )
    except Exception:
        print("\n[hyprland] smoke test failed", file=sys.stderr)
        for process in windows:
            tail = process.tail()
            if tail:
                print(f"\n--- {process.name} log ---\n{tail}", file=sys.stderr)
        raise
    finally:
        for window in windows:
            window.stop()
        runtime.cleanup()


async def _two_new_ids(before: set[int]) -> list[int]:
    ids = await _new_hyprland_ids(before)
    return ids if len(ids) >= 2 else []


def _run_compositor(name: str) -> None:
    if name == "hyprland":
        _run_existing_hyprland()
        return

    runtime = Runtime()
    parent: ManagedProcess | None = None
    target: ManagedProcess | None = None
    windows: list[ManagedProcess] = []
    try:
        if name == "sway":
            target, _display, env = _start_sway(runtime)
            module = sway
            list_ids = _sway_ids
        elif name == "niri":
            parent, parent_display, parent_env = _start_sway(runtime)
            target, _display, env = _start_niri(runtime, parent_display, parent_env)
            module = niri
            list_ids = _niri_ids
        else:
            raise SmokeError(f"unknown compositor: {name}")

        windows = _start_windows(runtime, env)
        _set_backend_env(env)
        asyncio.run(_check_compositor(name, module, list_ids))
    except Exception:
        print(f"\n[{name}] smoke test failed", file=sys.stderr)
        for process in (target, parent, *windows):
            if process is not None:
                tail = process.tail()
                if tail:
                    print(f"\n--- {process.name} log ---\n{tail}", file=sys.stderr)
        raise
    finally:
        for window in windows:
            window.stop()
        if target is not None:
            target.stop()
        if parent is not None:
            parent.stop()
        runtime.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "compositors",
        nargs="+",
        choices=("sway", "hyprland", "niri", "all"),
    )
    args = parser.parse_args()
    names: list[str] = []
    for requested in args.compositors:
        if requested == "all":
            names.extend(("sway", "niri"))
        elif requested not in names:
            names.append(requested)

    required = {
        "sway": ("sway",),
        "niri": ("sway", "niri"),
        "hyprland": ("Hyprland",),
    }
    missing = sorted(
        {binary for name in names for binary in required[name] if shutil.which(binary) is None}
    )
    if missing:
        print(f"missing compositor binaries: {', '.join(missing)}", file=sys.stderr)
        return 1

    for name in names:
        print(f"\n=== {name} ===", flush=True)
        _run_compositor(name)
    print("\ncompositor smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
