"""``lirts daemon``: one engine for the whole machine, serving every dashboard and agent.

The daemon owns the collectors and the state files.  It refreshes on its own every
``daemon.idle_interval`` seconds so history, patterns and the star map keep learning, and an
attached dashboard drives faster refreshes by asking for a snapshot no older than its own
refresh interval.  Dashboards, CLI commands and ``lirts mcp`` become clients
(:class:`lirts.remote.RemoteEngine`) when the socket answers.

The protocol is one JSON line per request on the Unix socket in the state directory; see
:mod:`lirts.daemon_client`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from lirts import __version__
from lirts.daemon_client import ping
from lirts.engine import Engine
from lirts.models import Level
from lirts.replay import snapshot_to_dict, to_jsonable

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Awaitable[Any]]


class DaemonServer:
    """The socket server around one engine."""

    def __init__(self, engine: Engine, *, socket_path: Path, idle_interval: float) -> None:
        self.engine = engine
        self.socket_path = socket_path
        self.idle_interval = idle_interval
        self.started = time.time()
        self.refreshes = 0
        self.last_client = 0.0
        self._stop = asyncio.Event()
        self._server: asyncio.AbstractServer | None = None
        self._methods: dict[str, Handler] = {
            "hello": self.hello,
            "snapshot": self.snapshot,
            "events": self.events,
            "clear_events": self.clear_events,
            "note": self.note,
            "health": self.health,
            "reload_routes": self.reload_routes,
            "stop": self.stop,
        }

    # ----- lifecycle -----------------------------------------------------------------

    async def serve(self) -> None:
        """Run until ``stop`` is called or SIGTERM / SIGINT arrives."""
        other = ping(self.socket_path)
        if other is not None:
            raise RuntimeError(
                f"another lirts daemon is running (PID {other.get('pid')}); stop it first"
            )
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            self.socket_path.unlink()  # a stale socket of a daemon that died
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, self._stop.set)
        self.engine.start()
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.socket_path))
        os.chmod(self.socket_path, 0o600)  # the socket can kill processes: owner only
        log.info("daemon listening on %s (PID %s)", self.socket_path, os.getpid())
        refresher = asyncio.create_task(self._loop())
        try:
            await self._stop.wait()
        finally:
            refresher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await refresher
            self._server.close()
            await self._server.wait_closed()
            with contextlib.suppress(FileNotFoundError):
                self.socket_path.unlink()
            self.engine.close()
            log.info("daemon stopped")

    async def _loop(self) -> None:
        """Refresh on the idle cadence; attached clients ask for fresher frames themselves."""
        while True:
            await self._refresh()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.idle_interval)

    async def _refresh(self) -> None:
        try:
            await self.engine.refresh()
            self.refreshes += 1
        except Exception:  # one bad cycle must not stop the daemon
            log.exception("refresh failed")

    # ----- protocol ------------------------------------------------------------------

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            reply = await self.dispatch(line)
            writer.write((json.dumps(reply, default=str) + "\n").encode("utf-8"))
            await writer.drain()
        except (OSError, asyncio.IncompleteReadError) as exc:
            log.debug("client went away: %s", exc)
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def dispatch(self, line: bytes) -> dict[str, Any]:
        """One request line → one reply dict (also the seam the tests use)."""
        try:
            request = json.loads(line or b"{}")
            method = str(request.get("method", ""))
            params = request.get("params") or {}
        except (ValueError, AttributeError) as exc:
            return {"ok": False, "error": f"bad request: {exc}"}
        handler = self._methods.get(method)
        if handler is None:
            return {"ok": False, "error": f"unknown method '{method}'"}
        try:
            return {"ok": True, "result": await handler(params)}
        except Exception as exc:  # the reply carries the failure; the daemon lives on
            log.exception("%s failed", method)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # ----- methods -------------------------------------------------------------------

    def info(self) -> dict[str, Any]:
        return {
            "pid": os.getpid(),
            "version": __version__,
            "started": self.started,
            "refreshes": self.refreshes,
            "idle_interval": self.idle_interval,
            "last_client": self.last_client,
            "demo": bool(getattr(self.engine, "demo", False)),
        }

    async def hello(self, _params: dict[str, Any]) -> dict[str, Any]:
        return self.info()

    def frame(self, *, with_events: bool = False) -> dict[str, Any]:
        """Everything a client needs to look exactly like the daemon's own dashboard."""
        engine = self.engine
        data: dict[str, Any] = {
            "snapshot": snapshot_to_dict(engine.snapshot),
            "topology": engine.topology.graph.to_dict(),
            "port_memory": engine.port_memory.to_dict(),
            "patterns": [to_jsonable(p) for p in engine.patterns.patterns.values()],
            "last_session_diff": engine.last_session_diff,
            "route_notes": list(engine.route_notes),
            "daemon": self.info(),
        }
        if with_events:
            data["events"] = [asdict(e) for e in engine.history.all_events()]
        return data

    async def snapshot(self, params: dict[str, Any]) -> dict[str, Any]:
        """The latest frame, refreshed first when it is older than ``max_age`` seconds."""
        self.last_client = time.time()
        max_age = float(params.get("max_age", 0.0))
        if not self.refreshes or time.time() - self.engine.snapshot.timestamp > max_age:
            await self._refresh()
        return self.frame(with_events=bool(params.get("with_events")))

    async def events(self, _params: dict[str, Any]) -> list[dict[str, Any]]:
        return [asdict(e) for e in self.engine.history.all_events()]

    async def clear_events(self, _params: dict[str, Any]) -> bool:
        self.engine.history.clear_events()
        return True

    async def note(self, params: dict[str, Any]) -> dict[str, Any]:
        event = self.engine.history.note(
            str(params.get("message", "")),
            port=params.get("port"),
            level=str(params.get("level") or Level.INFO),
        )
        return asdict(event)

    async def health(self, params: dict[str, Any]) -> dict[str, Any]:
        """Check the named rows now; the results stay attached to later frames."""
        keys = set(params.get("keys") or [])
        targets = [x for x in self.engine.snapshot.listeners if not keys or x.key in keys]
        await self.engine.check_health_now(targets)
        return {
            x.key: {"health": to_jsonable(x.health), "insights": [asdict(i) for i in x.insights]}
            for x in targets
        }

    async def reload_routes(self, _params: dict[str, Any]) -> bool:
        self.engine.reload_routes_on_next_refresh()
        return True

    async def stop(self, _params: dict[str, Any]) -> bool:
        self._stop.set()
        return True
