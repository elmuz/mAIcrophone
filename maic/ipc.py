"""Versioned JSON protocol for the local daemon socket.

The socket protocol is intentionally small: one UTF-8 JSON object per line.
This module owns the wire constants and validation rules so the daemon and CLI
cannot drift apart. See ``docs/ipc.md`` for the published contract.
"""

from __future__ import annotations

from typing import Any

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 64 * 1024

ERROR_CODES = frozenset(
    {
        "bad_request",
        "protocol_mismatch",
        "unknown_command",
        "invalid_argument",
        "injection_failed",
        "internal_error",
    }
)


class IpcProtocolError(Exception):
    """A request that cannot be dispatched, with a stable error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def make_request(command: str, **fields: Any) -> dict[str, Any]:
    """Build a request object at the current protocol version."""
    return {"protocol": PROTOCOL_VERSION, "command": command, **fields}


def ok(**fields: Any) -> dict[str, Any]:
    """Build a successful response envelope."""
    return {"protocol": PROTOCOL_VERSION, "ok": True, **fields}


def error(message: str, code: str = "internal_error") -> dict[str, Any]:
    """Build a failed response envelope."""
    if code not in ERROR_CODES:
        code = "internal_error"
    return {
        "protocol": PROTOCOL_VERSION,
        "ok": False,
        "code": code,
        "error": message,
    }


def validate_request(payload: Any) -> dict[str, Any]:
    """Validate the common request envelope and return the object.

    A missing ``protocol`` field is treated as version 1 so very old local
    clients fail only when they use a command that actually changed. New
    clients always send the field explicitly.
    """
    if not isinstance(payload, dict):
        raise IpcProtocolError("bad_request", "request must be a JSON object")

    protocol = payload.get("protocol", PROTOCOL_VERSION)
    if isinstance(protocol, bool) or not isinstance(protocol, int):
        raise IpcProtocolError("bad_request", "protocol must be an integer")
    if protocol != PROTOCOL_VERSION:
        raise IpcProtocolError(
            "protocol_mismatch",
            f"unsupported protocol {protocol}; this daemon speaks {PROTOCOL_VERSION}",
        )

    command = payload.get("command")
    if not isinstance(command, str) or not command:
        raise IpcProtocolError("bad_request", "command must be a non-empty string")

    return payload


def validate_response(payload: Any) -> dict[str, Any]:
    """Validate a response envelope from the daemon."""
    if not isinstance(payload, dict):
        raise IpcProtocolError("bad_request", "response must be a JSON object")
    protocol = payload.get("protocol")
    if protocol != PROTOCOL_VERSION:
        raise IpcProtocolError(
            "protocol_mismatch",
            f"daemon uses protocol {protocol}; client requires {PROTOCOL_VERSION}",
        )
    if not isinstance(payload.get("ok"), bool):
        raise IpcProtocolError("bad_request", "response is missing a boolean ok field")
    return payload
