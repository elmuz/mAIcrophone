from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from .subprocess_env import child_env


@dataclass
class TargetWindow:
    id: int
    pid: int
    app_id: str
    title: str


@dataclass
class OutputInfo:
    name: str
    width: int
    height: int


async def _run_json(*args: str) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "niri",
        "msg",
        "--json",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=child_env(),
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(err.decode(errors="replace").strip() or "niri msg failed")
    return json.loads(out)


async def focused_window() -> TargetWindow | None:
    try:
        data = await _run_json("focused-window")
    except Exception:
        return None
    if not data or not data.get("is_focused", True):
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


async def focused_output() -> OutputInfo | None:
    try:
        data = await _run_json("focused-output")
    except Exception:
        return None
    logical = data.get("logical") or {}
    try:
        return OutputInfo(
            name=str(data.get("name", "")),
            width=int(logical.get("width", 0)),
            height=int(logical.get("height", 0)),
        )
    except (TypeError, ValueError):
        return None


async def window_exists(window_id: int) -> bool:
    try:
        windows = await _run_json("windows")
    except Exception:
        return False
    return any(int(w.get("id", -1)) == window_id for w in windows)


async def current_focused_id() -> int | None:
    target = await focused_window()
    return target.id if target else None


async def focus_window(window_id: int) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "niri",
        "msg",
        "action",
        "focus-window",
        "--id",
        str(window_id),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        env=child_env(),
    )
    await proc.wait()
    return proc.returncode == 0
