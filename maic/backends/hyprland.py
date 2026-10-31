"""Hyprland compositor provider using the native IPC command socket.

``hyprctl`` is only a client of Hyprland's Unix sockets. This module sends the
same commands directly, so the daemon does not need ``hyprctl`` on ``PATH``.

The command socket is::

    $XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket.sock

Commands prefixed with ``j/`` return JSON (for example ``j/activewindow``).
Dispatchers such as ``dispatch focuswindow address:0x...`` return ``ok``.

The event socket (``.socket2.sock``) is not used yet. The daemon queries focus
at session start and immediately before injection, which is enough for the
current focus guard.
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
_MAX_RESPONSE_BYTES = 64 * 1024 * 1024


def _socket_path() -> Path | None:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or os.environ.get("HIS")
    if runtime and signature:
        path = Path(runtime) / "hypr" / signature / ".socket.sock"
        if path.exists():
            return path

    if not runtime:
        return None
    candidates = []
    for path in Path(runtime).glob("hypr/*/.socket.sock"):
        try:
            candidates.append((path.stat().st_mtime, path))
        except FileNotFoundError:
            continue
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


async def _request_text(command: str) -> str:
    path = _socket_path()
    if path is None:
        raise RuntimeError(
            "HYPRLAND_INSTANCE_SIGNATURE is not set and no Hyprland IPC socket was found"
        )

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(path), timeout=_REQUEST_TIMEOUT
        )
    except TimeoutError as exc:
        raise RuntimeError("timed out connecting to the Hyprland IPC socket") from exc

    try:
        writer.write(command.encode("utf-8"))
        await writer.drain()
        # Hyprland's command socket processes one request and closes its side
        # after replying. Half-closing our side makes the request boundary
        # explicit and lets servers that read until EOF answer immediately.
        try:
            writer.write_eof()
        except (AttributeError, OSError):
            pass
        chunks: list[bytes] = []
        total = 0
        while True:
            # Some Hyprland versions close the command socket after replying;
            # others leave it open. Wait for the first byte normally, then use
            # a short idle timeout so an open connection cannot stall a query.
            timeout = _REQUEST_TIMEOUT if not chunks else min(0.05, _REQUEST_TIMEOUT)
            try:
                chunk = await asyncio.wait_for(reader.read(65536), timeout=timeout)
            except TimeoutError:
                if chunks:
                    break
                raise
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_RESPONSE_BYTES:
                raise RuntimeError("Hyprland IPC response is too large")
            chunks.append(chunk)
    except TimeoutError as exc:
        raise RuntimeError("timed out waiting for the Hyprland IPC response") from exc
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass

    return b"".join(chunks).decode("utf-8", errors="replace")


async def _request_json(command: str) -> object:
    response = await _request_text(command)
    try:
        return json.loads(response)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from Hyprland IPC: {exc.msg}") from exc


def _target_from_json(data: object) -> TargetWindow | None:
    if not isinstance(data, dict):
        return None
    address = data.get("address")
    if not isinstance(address, str) or not address:
        return None
    try:
        window_id = int(address, 16)
    except (TypeError, ValueError):
        return None
    # Hyprland uses 0x0 when no window is active.
    if window_id <= 0:
        return None
    try:
        return TargetWindow(
            id=window_id,
            pid=int(data.get("pid", 0)),
            app_id=str(data.get("class", "")),
            title=str(data.get("title", "")),
        )
    except (TypeError, ValueError):
        return None


def _output_from_json(data: object) -> OutputInfo | None:
    if not isinstance(data, dict):
        return None
    try:
        return OutputInfo(
            name=str(data.get("name", "")),
            width=int(data.get("width", 0)),
            height=int(data.get("height", 0)),
        )
    except (TypeError, ValueError):
        return None


def _focused_output_from_monitors(monitors: object) -> OutputInfo | None:
    if not isinstance(monitors, list):
        return None
    for monitor in monitors:
        if isinstance(monitor, dict) and monitor.get("focused") is True:
            return _output_from_json(monitor)
    return None


async def focused_window() -> TargetWindow | None:
    try:
        data = await _request_json("j/activewindow")
    except Exception:
        return None
    return _target_from_json(data)


async def focused_output() -> OutputInfo | None:
    try:
        monitors = await _request_json("j/monitors")
    except Exception:
        return None
    return _focused_output_from_monitors(monitors)


async def window_exists(window_id: int) -> bool:
    try:
        clients = await _request_json("j/clients")
    except Exception:
        return False
    if not isinstance(clients, list):
        return False
    for client in clients:
        if not isinstance(client, dict):
            continue
        address = client.get("address")
        if not isinstance(address, str):
            continue
        try:
            if int(address, 16) == window_id:
                return True
        except (TypeError, ValueError):
            continue
    return False


async def current_focused_id() -> int | None:
    target = await focused_window()
    return target.id if target else None


async def _dispatch(command: str) -> bool:
    try:
        response = await _request_text(f"dispatch {command}")
    except Exception:
        return False
    return response.strip().lower() == "ok"


async def focus_window(window_id: int) -> bool:
    # Hyprland 0.57 moved the default config provider to Lua and expects
    # dispatchers such as ``hl.dsp.focus(...)``. Older releases use the
    # legacy ``focuswindow address:...`` syntax. Try the current form first
    # and fall back without needing the hyprctl binary or a version parser.
    if await _dispatch(f'hl.dsp.focus({{ window = "address:0x{window_id:x}" }})'):
        return True
    return await _dispatch(f"focuswindow address:0x{window_id:x}")


class HyprlandCompositor:
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
