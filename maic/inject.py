from __future__ import annotations

import asyncio
import logging

from .config import Config
from .subprocess_env import child_env

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
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, b"", b"timed out"
    return proc.returncode or 0, out, err


async def _run_detached(*args: str, stdin: bytes | None = None) -> int:
    """Run a command that intentionally forks a background process.

    `wl-copy` forks a clipboard-owner process that inherits stdout/stderr.
    If we capture those pipes, `communicate()` waits for EOF and therefore
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
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124
    return proc.returncode or 0


def apply_replacements(text: str, cfg: Config) -> str:
    for source, target in cfg.replacements.items():
        text = text.replace(source, target)
    return text


def normalize_text(text: str, cfg: Config) -> str:
    text = apply_replacements(text, cfg)
    if cfg.multiline == "space":
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
    text = " ".join(text.split()) if cfg.multiline == "space" else text.strip()
    return text


async def copy_to_clipboard(text: str, cfg: Config) -> bool:
    data = text.encode("utf-8")
    ok = True
    if cfg.copy_to_clipboard:
        rc = await _run_detached("wl-copy", stdin=data)
        if rc != 0:
            log.warning("wl-copy failed (%s)", rc)
            ok = False
    if cfg.primary_selection:
        rc = await _run_detached("wl-copy", "--primary", stdin=data)
        if rc != 0:
            log.warning("wl-copy --primary failed (%s)", rc)
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
                code, _, err = await _run(
                    "wtype", "-M", "shift", "-k", "Return", "-m", "shift"
                )
                if code != 0:
                    log.warning("wtype newline failed (%s): %s", code, err.decode(errors="replace").strip())
                    return False
        return True
    code, _, err = await _wtype_text(text)
    if code != 0:
        log.warning("wtype failed (%s): %s", code, err.decode(errors="replace").strip())
    return code == 0
