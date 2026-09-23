"""The refresh cycle: collect → enrich → identify → history → insights.

The small steps (``_collect``, ``_probe``, ``_sample_system`` …) are the seams
the demo and replay engines override; ``refresh`` itself is shared by all three.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from lirts.collectors.ports import collect_listeners
from lirts.constants import (
    HOST_NAME_PORTS,
    MAX_ACCUMULATION_GAP,
    SNAPSHOT_EVENTS,
    SSH_DEFAULT_PORT,
)
from lirts.engine.enrichment import EnrichmentMixin
from lirts.engine.routes import RoutesMixin
from lirts.history import HistoryStore, load_last_snapshot
from lirts.identity import Role, identify
from lirts.insights import analyse, classify_activity, detect_proxies
from lirts.models import (
    Activity,
    ContainerInfo,
    Identity,
    Listener,
    ListenerProcess,
    ListenerState,
    Snapshot,
    Source,
    SshSession,
    SystemStats,
)
from lirts.ports import PortMemory
from lirts.topology_build import build_graph

log = logging.getLogger(__name__)


class RefreshPipelineMixin(EnrichmentMixin, RoutesMixin):
    """The refresh cycle shared by the live, demo and replay engines."""

    async def refresh(self) -> Snapshot:
        """Run a full collection cycle.  Concurrent calls wait for the running one."""
        if self._lock.locked():
            async with self._lock:
                return self.snapshot
        async with self._lock:
            started = time.perf_counter()
            listeners, containers = await self._collect()
            self.containers = containers
            self._attach_containers(listeners, containers)
            await self._enrich_origins(listeners)
            self._attach_clients(listeners)
            self.hosts.refresh()
            loopback_names = self.hosts.loopback_names()
            aliases = self.cfg.aliases
            for lst in listeners:
                lst.identity = identify(lst, aliases=aliases)
                if lst.port in HOST_NAME_PORTS or lst.identity.role == Role.PROXY:
                    lst.hosts = loopback_names
            await self._probe(listeners)
            # Never checks by itself: only re-attaches the results of the last manual check.
            self._health_attach(listeners)
            # Headers may sharpen the identity.
            for lst in listeners:
                if lst.http.ok:
                    lst.identity = identify(lst, aliases=aliases)
            self._attach_bandwidth(listeners)
            for lst in listeners:
                lst.activity, lst.activity_score = classify_activity(lst)
            detect_proxies(listeners)
            if self.proxies_enabled and not self._routes_loaded:
                await self._load_routes()
            self._apply_routes(listeners)
            now = time.time()
            self.port_memory = PortMemory.build(
                graph=self.topology.graph,
                entries=self.history.entries.values(),
                containers=containers,
                now=now,
            )
            for lst in listeners:
                analyse(lst, cfg=self.cfg, now=now, memory=self.port_memory, alive=self.pid_alive)
            protocols = {"TCP", "UDP"} if self.show_udp else {"TCP"}
            events = self.history.update(listeners, now=now, protocols=protocols)
            if self.patterns.days and self.cfg.insights.pattern_days > 0:
                self.patterns.ingest(events, listeners=listeners, now=now)
                self.patterns.save()
                for lst in listeners:
                    lst.patterns = [
                        self.patterns.describe(p, now)
                        for p in self.patterns.for_key(lst.key)
                        if p.count >= self.patterns.min_count
                    ]
            # Stale detection needs history (last_active); run once more cheaply.
            for lst in listeners:
                analyse(lst, cfg=self.cfg, now=now, memory=self.port_memory, alive=self.pid_alive)
            self.history.save()
            self._check_tunnels(listeners)
            listeners.extend(self._stopped_rows(listeners, now))
            if "ssh" in self.sources:
                listeners.extend(self._ssh_rows(self.sampler.ssh_sessions))
            if self.hide_system:
                listeners = [x for x in listeners if x.identity.role != Role.SYSTEM]
            if "local" not in self.sources:
                listeners = [x for x in listeners if x.source != Source.LOCAL]
            system = await self._sample_system()
            self._accumulate_totals(listeners, now)
            self.snapshot = Snapshot(
                listeners=listeners,
                system=system,
                events=self.history.recent_events(SNAPSHOT_EVENTS),
                timestamp=now,
                docker_available=self.docker.available,
                container_count=len(containers),
                refresh_ms=(time.perf_counter() - started) * 1000,
                kube=self._kube_state(),
                edges=list(self.sampler.edges),
                routes=list(self.routes) if self.proxies_enabled else [],
                patterns=self.patterns.summarise(now),
                hosts_names=loopback_names,
                ssh_sessions=list(self.sampler.ssh_sessions),
            )
            self.topology.observe(build_graph(self.snapshot, now=now), now=now)
            self.topology.save()
            if self.last_session_diff is None:
                self._compute_session_diff()
            if events:
                for ev in events:
                    log.info("event: %s", ev.message)
            return self.snapshot

    def refresh_sync(self) -> Snapshot:
        """Run :meth:`refresh` in a fresh event loop, for the CLI and recordings."""
        return asyncio.run(self.refresh())

    # ----- collection steps (the demo and replay engines override these) --------------

    async def _collect(self) -> tuple[list[Listener], list[ContainerInfo]]:
        listeners, containers = await asyncio.gather(
            asyncio.to_thread(collect_listeners, self.sampler, self.show_udp),
            asyncio.to_thread(self.docker.list_containers),
        )
        return listeners, containers

    async def _enrich_origins(self, listeners: list[Listener]) -> None:
        await asyncio.to_thread(self._attach_origins, listeners)

    async def _probe(self, listeners: list[Listener]) -> None:
        await self.prober.enrich(listeners)

    def _health_attach(self, listeners: list[Listener]) -> None:
        self.health.attach_cached(listeners)

    async def _load_routes(self) -> None:
        await asyncio.to_thread(self.refresh_routes)

    async def _sample_system(self) -> SystemStats:
        return await asyncio.to_thread(self.system.sample)

    def _kube_state(self) -> Any:
        return self.kube.snapshot() if self.kube.enabled else None

    # ----- rows and totals the cycle derives ------------------------------------------

    def _accumulate_totals(self, listeners: list[Listener], now: float) -> None:
        """Bytes per row since lirts started (only while the traffic panel is on)."""
        if not self.cfg.net_panel:
            self._row_totals.clear()
            self._last_refresh = now
            return
        gap = max(now - self._last_refresh, 0.0)
        dt = min(gap, MAX_ACCUMULATION_GAP) if self._last_refresh else 0.0
        self._last_refresh = now
        live: set[str] = set()
        for lst in listeners:
            live.add(lst.key)
            total_in, total_out = self._row_totals.get(lst.key, (0.0, 0.0))
            total_in += (lst.bytes_in_rate or 0.0) * dt
            total_out += (lst.bytes_out_rate or 0.0) * dt
            self._row_totals[lst.key] = (total_in, total_out)
            lst.bytes_in_total, lst.bytes_out_total = total_in, total_out
        for key in list(self._row_totals):
            if key not in live:
                del self._row_totals[key]

    def _ssh_rows(self, sessions: list[SshSession]) -> list[Listener]:
        """One row per outbound ssh session (state SSH, source ssh); killing it closes the session."""
        rows: list[Listener] = []
        for s in sessions:
            try:
                rport = int(s.remote.rsplit(":", 1)[1])
            except (IndexError, ValueError):
                rport = SSH_DEFAULT_PORT
            snap = self.sampler.describe(s.pid)
            proc = ListenerProcess(
                pid=s.pid,
                name=snap.name if snap.name != "?" else "ssh",
                cmdline=list(snap.cmdline or []) or s.cmd.split(),
                ppid=snap.ppid,
                create_time=snap.create_time or s.create_time,
                cwd=snap.cwd,
                exe=snap.exe,
                user=snap.user,
                status=snap.status,
                cpu_percent=snap.cpu_percent,
                memory_mb=snap.memory_mb,
                threads=snap.threads,
                inbound_connections=1,
                remote_conns=[s.remote],
                accessible=snap.accessible,
            )
            target = f"{s.user + '@' if s.user else ''}{s.host}"
            kind = "tunnel" if s.forwards else "session"
            row = Listener(
                port=rport,
                protocol="TCP",
                processes=[proc],
                state=ListenerState.SSH,
                source=Source.SSH,
                row_id=f"ssh:{s.pid}",
                ssh=s,
            )
            row.identity = Identity(
                service=f"ssh {target}" + (f" ({', '.join(s.forwards)})" if s.forwards else ""),
                role=Role.TUNNEL if s.forwards else Role.SSH,
                confidence=1.0,
                reasons=[
                    f"outbound ssh {kind} to {s.remote}" + (f" from {s.tty}" if s.tty else ""),
                    s.cmd,
                ],
            )
            row.activity = Activity.ACTIVE
            row.first_seen = s.create_time
            rows.append(row)
        return rows

    def _compute_session_diff(self) -> None:
        self.last_session_diff = {}
        old = load_last_snapshot(self.state_root)
        if old:
            new = HistoryStore.snapshot_payload(self.snapshot.listeners)
            self.last_session_diff = HistoryStore.diff_snapshots(old, new)
