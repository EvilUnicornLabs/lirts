"""Talking to a running ``lirts daemon``: one JSON line in, one JSON line out.

The protocol is deliberately small.  A client opens the Unix socket in the state directory,
writes one request line ``{"method": ..., "params": {...}}``, reads one response line
``{"ok": true, "result": ...}`` or ``{"ok": false, "error": "..."}`` and closes.  Every call
is its own connection, so the client needs no session state and works from synchronous code
(the dashboard's key handlers) as well as from the refresh loop.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import Any

from lirts.config import state_dir
from lirts.constants import DAEMON_CALL_TIMEOUT, DAEMON_PING_TIMEOUT

SOCKET_NAME = "daemon.sock"


class DaemonError(Exception):
    """The daemon answered with an error, or could not be reached."""


def socket_path(state_root: Path | None = None) -> Path:
    """Where the daemon listens: ``daemon.sock`` in the state directory."""
    return (state_root or state_dir()) / SOCKET_NAME


def _exchange(path: Path, request: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    """Send one request line and read the one response line, on a fresh connection."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(path))
        sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)
        with sock.makefile("rb") as reader:
            line = reader.readline()
    if not line:
        raise DaemonError("the daemon closed the connection without answering")
    reply: dict[str, Any] = json.loads(line)
    return reply


class DaemonClient:
    """A handle on the daemon behind ``path``; every call is one request."""

    def __init__(self, path: Path, *, timeout: float = DAEMON_CALL_TIMEOUT) -> None:
        self.path = path
        self.timeout = timeout

    def call_sync(self, method: str, **params: Any) -> Any:
        """Call ``method`` from synchronous code; raises :class:`DaemonError` on failure."""
        try:
            reply = _exchange(self.path, {"method": method, "params": params}, timeout=self.timeout)
        except (OSError, ValueError) as exc:
            raise DaemonError(f"cannot reach the lirts daemon at {self.path}: {exc}") from exc
        if not reply.get("ok"):
            raise DaemonError(str(reply.get("error") or "unknown error"))
        return reply.get("result")

    async def call(self, method: str, **params: Any) -> Any:
        """Call ``method`` without blocking the event loop."""
        return await asyncio.to_thread(self.call_sync, method, **params)


def ping(
    path: Path | None = None, *, timeout: float = DAEMON_PING_TIMEOUT
) -> dict[str, Any] | None:
    """The daemon's ``hello`` answer (pid, version, uptime…), or None when nothing listens."""
    target = path or socket_path()
    if not target.exists():
        return None
    try:
        reply = _exchange(target, {"method": "hello", "params": {}}, timeout=timeout)
    except (OSError, ValueError):
        return None
    if not reply.get("ok"):
        return None
    result: dict[str, Any] = reply.get("result") or {}
    return result
