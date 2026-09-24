"""The dashboard, the CLI and ``lirts mcp`` as clients of a running ``lirts daemon``.

:class:`RemoteEngine` looks like :class:`lirts.engine.Engine` to everything above it, the
way the replay engine does, but its frames come from the daemon instead of a recording and
its actions are real.  Local things stay local: killing a process, restarting it, Docker
logs and exec, opening a browser or the editor.  Things that must land in the daemon's memory
go over the socket: history notes, clearing events, health checks (their results are cached
there and ride along in later frames) and a routes reload.
"""

from __future__ import annotations

import asyncio
import copy
import shutil
import tempfile
from pathlib import Path
from typing import Any

from lirts.daemon_client import DaemonClient
from lirts.engine import Engine
from lirts.history import HistoryStore
from lirts.identity import Role
from lirts.models import ContainerInfo, Event, HealthResult, Insight, Listener, Protocol, Snapshot
from lirts.patterns import Pattern
from lirts.ports import PortMemory
from lirts.replay import from_dict, snapshot_from_dict
from lirts.topology import Graph


class RemoteHistory(HistoryStore):
    """The daemon's history as seen from a client: notes and clears go over the socket."""

    def __init__(self, client: DaemonClient) -> None:
        super().__init__(persist=False)
        self.client = client

    def note(self, message: str, *, port: int | None = None, level: str = "info") -> Event:
        data = self.client.call_sync("note", message=message, port=port, level=str(level))
        event = Event(data["timestamp"], data["level"], data["message"], data.get("port"))
        self.events.append(event)
        return event

    def clear_events(self) -> None:
        self.client.call_sync("clear_events")
        super().clear_events()


class RemoteEngine(Engine):
    """An engine whose frames come from the daemon; filters and local actions stay here."""

    def __init__(self, config: dict[str, Any], client: DaemonClient, info: dict[str, Any]) -> None:
        self._tmp = tempfile.mkdtemp(prefix="lirts-remote-")
        cfg = copy.deepcopy(config)
        # The daemon collects; this side only filters, so nothing here may sample or persist.
        cfg.setdefault("bandwidth", {})["mode"] = "off"
        cfg.setdefault("http_probe", {})["enabled"] = False
        cfg.setdefault("proxies", {})["enabled"] = False
        cfg.setdefault("history", {})["persist"] = False
        cfg["_remote"] = True
        super().__init__(cfg, state_root=Path(self._tmp))
        self.client = client
        self.daemon_info = info
        self.remote = True
        self.history = RemoteHistory(client)
        self._events_loaded = False

    @property
    def position(self) -> str:
        """``via daemon (PID n)`` for the title bar."""
        return f"via daemon (PID {self.daemon_info.get('pid', '?')})"

    def close(self) -> None:
        """Nothing to persist here: the daemon owns the state files."""
        self.kube.close()
        self.docker.close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ----- frames --------------------------------------------------------------------

    async def refresh(self) -> Snapshot:
        """Ask for a frame no older than this side's refresh interval and apply it."""
        frame = await self.client.call(
            "snapshot", max_age=self.cfg.refresh_interval, with_events=not self._events_loaded
        )
        return self.apply_frame(frame)

    def refresh_sync(self) -> Snapshot:
        return asyncio.run(self.refresh())

    def apply_frame(self, frame: dict[str, Any]) -> Snapshot:
        """Turn a daemon frame into this side's snapshot, filtered as this side wants it."""
        snap = snapshot_from_dict(frame["snapshot"])
        snap.listeners = [x for x in snap.listeners if self._wanted(x)]
        if "events" in frame:
            self.history.events.clear()
            for e in frame["events"]:
                self.history.events.append(
                    Event(e["timestamp"], e["level"], e["message"], e.get("port"))
                )
            self._events_loaded = True
        else:
            seen = {(e.timestamp, e.message) for e in self.history.events}
            for ev in snap.events:
                if (ev.timestamp, ev.message) not in seen:
                    self.history.events.append(Event(ev.timestamp, ev.level, ev.message, ev.port))
        containers: list[ContainerInfo] = []
        seen_ids: set[str] = set()
        for x in snap.listeners:
            if x.container is not None and x.container.id not in seen_ids:
                seen_ids.add(x.container.id)
                containers.append(x.container)
        self.containers = containers
        self.routes = list(snap.routes)
        self.route_notes = list(frame.get("route_notes") or [])
        self.topology.graph = Graph.from_dict(frame.get("topology") or {})
        self.port_memory = PortMemory.from_dict(frame.get("port_memory") or {})
        self.patterns.patterns = {
            (p["key"], p["kind"]): from_dict(Pattern, p) for p in frame.get("patterns") or []
        }
        self.last_session_diff = frame.get("last_session_diff") or {}
        self.daemon_info = frame.get("daemon") or self.daemon_info
        self.snapshot = snap
        return snap

    def _wanted(self, lst: Listener) -> bool:
        """This side's filters: system rows, UDP and the sources setting."""
        if self.hide_system and lst.identity.role == Role.SYSTEM:
            return False
        if not self.show_udp and lst.protocol == Protocol.UDP:
            return False
        return lst.source in self.sources

    # ----- what goes over the socket --------------------------------------------------

    async def check_health_now(self, listeners: list[Listener] | None = None) -> list[Listener]:
        """The daemon runs the check and keeps the results; this side shows them at once."""
        targets = listeners if listeners is not None else list(self.snapshot.listeners)
        results = await self.client.call("health", keys=[x.key for x in targets])
        for lst in targets:
            got = results.get(lst.key)
            if got is None:
                continue
            lst.health = from_dict(HealthResult, got["health"])
            lst.insights = [from_dict(Insight, i) for i in got["insights"]]
        return targets

    def reload_routes_on_next_refresh(self) -> None:
        self.client.call_sync("reload_routes")

    def refresh_routes(self) -> None:
        self.client.call_sync("reload_routes")
