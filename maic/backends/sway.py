"""Sway compositor provider using the native i3-compatible IPC socket.

Sway's IPC is the protocol spoken by ``swaymsg``. This module talks to the
Unix socket directly, so the daemon does not need the ``swaymsg`` executable
or any other Sway helper on ``PATH``.

Protocol framing is a 14-byte header followed by a JSON payload::

    b"i3-ipc" | uint32 payload length | uint32 message type | payload

The socket path comes from ``$SWAYSOCK``. Services started outside Sway's
environment may not inherit it, so the newest ``sway-ipc.*.sock`` in
``$XDG_RUNTIME_DIR`` is used as a fallback.
"""

from __future__ import annotations

import asyncio
import json
import os
import struct
from pathlib import Path

from .protocols import (
    CompositorCapabilities,
    OutputInfo,
    TargetWindow,
)

_REQUEST_TIMEOUT = 2.0
_MAX_RESPONSE_BYTES = 64 * 1024 * 1024

_MAGIC = b"i3-ipc"
_HEADER = struct.Struct("=II")

RUN_COMMAND = 0
GET_WORKSPACES = 1
GET_OUTPUTS = 3
GET_TREE = 4


def _socket_path() -> Path | None:
    env_path = os.environ.get("SWAYSOCK")
    if env_path:
        path = Path(env_path)
        if path.exists():
            return path

    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        return None
    candidates = []
    for path in Path(runtime).glob("sway-ipc.*.sock"):
        try:
            candidates.append((path.stat().st_mtime, path))
        except FileNotFoundError:
            continue
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


async def _request(message_type: int, payload: object | bytes | None = None) -> object:
    path = _socket_path()
    if path is None:
        raise RuntimeError("SWAYSOCK is not set and no sway IPC socket was found")

    if payload is None:
        body = b""
    elif isinstance(payload, bytes):
        body = payload
    else:
        body = json.dumps(payload).encode("utf-8")

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(path), timeout=_REQUEST_TIMEOUT
        )
    except TimeoutError as exc:
        raise RuntimeError("timed out connecting to the sway IPC socket") from exc

    try:
        writer.write(_MAGIC + _HEADER.pack(len(body), message_type) + body)
        await writer.drain()
        header = await asyncio.wait_for(
            reader.readexactly(len(_MAGIC) + _HEADER.size), timeout=_REQUEST_TIMEOUT
        )
        if not header.startswith(_MAGIC):
            raise RuntimeError("invalid sway IPC response header")
        length, _reply_type = _HEADER.unpack(header[len(_MAGIC) :])
        if length > _MAX_RESPONSE_BYTES:
            raise RuntimeError(f"sway IPC response is too large ({length} bytes)")
        response = await asyncio.wait_for(reader.readexactly(length), timeout=_REQUEST_TIMEOUT)
    except TimeoutError as exc:
        raise RuntimeError("timed out waiting for the sway IPC response") from exc
    except asyncio.IncompleteReadError as exc:
        raise RuntimeError("sway closed the IPC connection without a complete response") from exc
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass

    try:
        return json.loads(response)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from sway IPC: {exc.msg}") from exc


def _walk(node: object):
    if not isinstance(node, dict):
        return
    yield node
    for key in ("nodes", "floating_nodes"):
        children = node.get(key)
        if not isinstance(children, list):
            continue
        for child in children:
            yield from _walk(child)


def _find_focused(node: object) -> dict | None:
    for candidate in _walk(node):
        if candidate.get("focused") is True:
            return candidate
    return None


def _find_window(node: object, window_id: int) -> dict | None:
    for candidate in _walk(node):
        try:
            if int(candidate.get("id", -1)) == window_id:
                if candidate.get("type") in {"con", "floating_con"}:
                    return candidate
        except (TypeError, ValueError):
            continue
    return None


def _target_from_json(data: object) -> TargetWindow | None:
    if not isinstance(data, dict):
        return None
    if data.get("focused") is False:
        return None
    node_type = data.get("type")
    if node_type is not None and node_type not in {"con", "floating_con"}:
        return None
    properties = data.get("window_properties")
    if not isinstance(properties, dict):
        properties = {}
    try:
        return TargetWindow(
            id=int(data["id"]),
            pid=int(data.get("pid") or properties.get("pid") or 0),
            app_id=str(data.get("app_id") or properties.get("class") or ""),
            title=str(data.get("name") or properties.get("title") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _output_from_json(data: object) -> OutputInfo | None:
    if not isinstance(data, dict):
        return None
    rect = data.get("rect")
    if not isinstance(rect, dict):
        rect = {}
    mode = data.get("current_mode")
    if not isinstance(mode, dict):
        mode = {}
    try:
        return OutputInfo(
            name=str(data.get("name", "")),
            width=int(rect.get("width") or mode.get("width") or 0),
            height=int(rect.get("height") or mode.get("height") or 0),
        )
    except (TypeError, ValueError):
        return None


def _focused_output_from_outputs(outputs: object) -> OutputInfo | None:
    if not isinstance(outputs, list):
        return None
    active: list[OutputInfo] = []
    for output in outputs:
        if not isinstance(output, dict):
            continue
        if output.get("focused") is True:
            parsed = _output_from_json(output)
            if parsed is not None:
                return parsed
        if output.get("active", True):
            parsed = _output_from_json(output)
            if parsed is not None:
                active.append(parsed)
    # When Sway does not mark a focused output, a single active output is
    # unambiguous. Multi-output setups use the focused workspace below.
    if len(active) == 1:
        return active[0]
    return None


async def focused_window() -> TargetWindow | None:
    try:
        tree = await _request(GET_TREE)
    except Exception:
        return None
    return _target_from_json(_find_focused(tree))


async def focused_output() -> OutputInfo | None:
    try:
        outputs = await _request(GET_OUTPUTS)
    except Exception:
        return None
    output = _focused_output_from_outputs(outputs)
    if output is not None:
        return output

    # Fall back to the focused workspace's output. This also handles Sway
    # versions that do not include a "focused" flag in GET_OUTPUTS.
    try:
        workspaces = await _request(GET_WORKSPACES)
    except Exception:
        return None
    if not isinstance(workspaces, list):
        return None
    focused_workspace = next(
        (
            workspace
            for workspace in workspaces
            if isinstance(workspace, dict) and workspace.get("focused") is True
        ),
        None,
    )
    if focused_workspace is None:
        return None
    output_name = str(focused_workspace.get("output", ""))
    if not isinstance(outputs, list):
        return None
    for candidate in outputs:
        if isinstance(candidate, dict) and str(candidate.get("name", "")) == output_name:
            return _output_from_json(candidate)
    return None


async def window_exists(window_id: int) -> bool:
    try:
        tree = await _request(GET_TREE)
    except Exception:
        return False
    return _find_window(tree, window_id) is not None


async def current_focused_id() -> int | None:
    target = await focused_window()
    return target.id if target else None


async def focus_window(window_id: int) -> bool:
    try:
        # RUN_COMMAND payloads are raw command bytes, not a JSON string.
        response = await _request(RUN_COMMAND, f"[con_id={window_id}] focus".encode())
    except Exception:
        return False
    if isinstance(response, list):
        return any(isinstance(item, dict) and item.get("success") is True for item in response)
    if isinstance(response, dict):
        return response.get("success") is True
    return False


class SwayCompositor:
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
