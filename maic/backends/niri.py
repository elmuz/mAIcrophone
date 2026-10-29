"""Niri compositor provider using the native IPC socket.

Niri exposes a private, line-delimited JSON protocol on ``$NIRI_SOCKET``. The
provider talks to that socket directly instead of spawning ``niri msg`` for
every focus query. Requests and responses look like::

    "FocusedWindow"
    {"Ok": {"FocusedWindow": {"id": 3, ...}}}

    {"Action": {"FocusWindow": {"id": 3}}}
    {"Ok": "Handled"}

The socket path is inherited from the Niri session when available. Services
started outside Niri's environment (for example a systemd user unit) may not
have ``NIRI_SOCKET`` set, so fall back to the newest Niri socket in
``$XDG_RUNTIME_DIR``.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from .protocols import (
    CompositorCapabilities,
    OutputInfo,
    TargetWindow,
)

_REQUEST_TIMEOUT = 2.0


def _socket_path() -> Path | None:
    env_path = os.environ.get("NIRI_SOCKET")
    if env_path:
        path = Path(env_path)
        if path.exists():
            return path

    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        return None
    candidates = []
    for path in Path(runtime).glob("niri*.sock"):
        try:
            candidates.append((path.stat().st_mtime, path))
        except FileNotFoundError:
            # The socket disappeared between glob() and stat().
            continue
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


async def _request(payload: dict | str) -> object:
    path = _socket_path()
    if path is None:
        raise RuntimeError("NIRI_SOCKET is not set and no niri socket was found")

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(path), timeout=_REQUEST_TIMEOUT
        )
    except TimeoutError as exc:
        raise RuntimeError("timed out connecting to the niri IPC socket") from exc
    try:
        writer.write((json.dumps(payload) + "\n").encode("utf-8"))
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=_REQUEST_TIMEOUT)
    except TimeoutError as exc:
        raise RuntimeError("timed out waiting for the niri IPC response") from exc
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass

    if not line:
        raise RuntimeError("niri closed the IPC connection without a response")
    response = json.loads(line)
    if "Err" in response:
        raise RuntimeError(str(response["Err"]))
    if "Ok" not in response:
        raise RuntimeError(f"unexpected niri response: {response!r}")
    result = response["Ok"]
    # Reply data is wrapped with the request name, e.g.
    # {"Ok": {"FocusedWindow": {...}}}. Actions reply with a plain string.
    if isinstance(result, dict):
        request_name = next(iter(payload)) if isinstance(payload, dict) else payload
        if request_name in result:
            return result[request_name]
    return result


def _target_from_json(data: object) -> TargetWindow | None:
    if not isinstance(data, dict) or not data.get("is_focused", True):
        return None
    try:
        return TargetWindow(
            id=int(data["id"]),
            pid=int(data.get("pid", 0)),
            app_id=str(data.get("app_id", "")),
            title=str(data.get("title", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _output_from_json(data: object) -> OutputInfo | None:
    if not isinstance(data, dict):
        return None
    logical = data.get("logical")
    if not isinstance(logical, dict):
        logical = {}
    try:
        return OutputInfo(
            name=str(data.get("name", "")),
            width=int(logical.get("width", 0)),
            height=int(logical.get("height", 0)),
        )
    except (TypeError, ValueError):
        return None


async def focused_window() -> TargetWindow | None:
    try:
        return _target_from_json(await _request("FocusedWindow"))
    except Exception:
        return None


async def focused_output() -> OutputInfo | None:
    try:
        return _output_from_json(await _request("FocusedOutput"))
    except Exception:
        return None


async def window_exists(window_id: int) -> bool:
    try:
        windows = await _request("Windows")
    except Exception:
        return False
    if not isinstance(windows, list):
        return False
    for window in windows:
        if not isinstance(window, dict):
            continue
        try:
            if int(window.get("id", -1)) == window_id:
                return True
        except (TypeError, ValueError):
            continue
    return False


async def current_focused_id() -> int | None:
    target = await focused_window()
    return target.id if target else None


async def focus_window(window_id: int) -> bool:
    try:
        await _request({"Action": {"FocusWindow": {"id": window_id}}})
    except Exception:
        return False
    return True


class NiriCompositor:
    capabilities = CompositorCapabilities(
        can_refocus=True,
        can_check_windows=True,
        can_query_output=True,
    )

    async def focused_window(self) -> TargetWindow | None:
        return await focused_window()

    async def focused_output(self) -> OutputInfo | None:
        return await focused_output()

    async def window_exists(self, window_id: int) -> bool:
        return await window_exists(window_id)

    async def current_focused_id(self) -> int | None:
        return await current_focused_id()

    async def focus_window(self, window_id: int) -> bool:
        return await focus_window(window_id)
