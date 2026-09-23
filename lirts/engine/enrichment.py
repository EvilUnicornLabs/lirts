"""Enrichment steps: origins, client edges, containers, bandwidth and stopped rows.

Every method here takes the listeners collected in this cycle and attaches what
another collector knows about them.  They are called from the refresh pipeline.
"""

from __future__ import annotations

from collections import deque

from lirts.constants import EDGE_RATE_SAMPLES, SECONDS_PER_MINUTE
from lirts.engine.base import EngineBase
from lirts.identity import Role
from lirts.insights import format_duration
from lirts.models import (
    ContainerInfo,
    Edge,
    Identity,
    Insight,
    Level,
    Listener,
    ListenerState,
    Source,
)


class EnrichmentMixin(EngineBase):
    """Attaches what the other collectors know to the listeners of this cycle."""

    def _attach_origins(self, listeners: list[Listener]) -> None:
        live: set[int] = set()
        for lst in listeners:
            for proc in lst.processes:
                live.add(proc.pid)
                proc.origin = self.origins.resolve(proc.pid, proc.cwd)
        self.origins.forget(live)

    def _attach_clients(self, listeners: list[Listener]) -> None:
        """Attach who-talks-to-whom edges to the listeners they point at."""
        by_port = {lst.port: lst for lst in listeners if lst.protocol == "TCP"}
        for lst in listeners:
            lst.clients = []
        pid_to_listener = {p.pid: lst for lst in listeners for p in lst.processes}
        for edge in self.sampler.edges:
            target = by_port.get(edge.dst_port)
            if target is None:
                continue
            src = pid_to_listener.get(edge.client_pid)
            if src is not None:
                edge.client_project = src.identity.project
                if src.identity.service and src.identity.service != "Unknown":
                    edge.client_name = f"{edge.client_name} [{src.identity.service}]"
            target.clients.append(edge)
        self._attach_flows(self.sampler.edges)

    def _attach_flows(self, edges: list[Edge]) -> None:
        """Per-connection rates onto the edges (only when bandwidth.connections is on)."""
        if not self.bandwidth.connections:
            self._edge_history.clear()
            return
        flows = self.bandwidth.flows()
        seen: set[tuple[int, int]] = set()
        for edge in edges:
            key = (edge.client_pid, edge.dst_port)
            seen.add(key)
            total_in = total_out = 0.0
            sum_in = sum_out = 0
            found = False
            for lport in edge.client_ports:
                flow = flows.get((edge.client_pid, lport, edge.dst_port))
                if flow is None:
                    continue
                found = True
                total_in += flow[0]
                total_out += flow[1]
                sum_in += flow[2]
                sum_out += flow[3]
            if not found:
                continue
            edge.bytes_in_rate, edge.bytes_out_rate = total_in, total_out
            edge.bytes_in, edge.bytes_out = sum_in, sum_out
            hist = self._edge_history.setdefault(key, deque(maxlen=EDGE_RATE_SAMPLES))
            hist.append(total_in + total_out)
            edge.rate_history = list(hist)
        for key in list(self._edge_history):
            if key not in seen:
                del self._edge_history[key]

    def _check_tunnels(self, listeners: list[Listener]) -> None:
        """Warn about kubectl port-forwards whose target no longer exists in the cluster."""
        state = self.kube.snapshot() if self.kube.enabled else None
        if state is None or not state.available:
            return
        for lst in listeners:
            t = lst.tunnel
            if not t or t.get("type") != "kubectl":
                continue
            ns = t.get("namespace") or state.namespace
            if state.namespace and ns != state.namespace and not self.all_namespaces_active():
                continue  # we only know about the namespace we watch
            kind, name = t.get("kind"), t.get("name")
            exists = False
            if kind == "pod":
                exists = any(p.name == name and p.namespace == ns for p in state.pods)
            elif kind == "service":
                exists = any(s.name == name and s.namespace == ns for s in state.services)
            elif kind == "deployment":
                exists = any(d.name == name and d.namespace == ns for d in state.deployments)
            else:
                continue
            if not exists:
                lst.add_insight(
                    Insight(
                        Level.WARNING,
                        "tunnel-target-missing",
                        f"port-forward target {kind}/{name} not found in namespace {ns}",
                        "the forward will fail on first use; stop it (k) and start a new one from the Kubernetes screen (K)",
                        f"kill:{lst.pid}",
                    )
                )

    def _stopped_rows(self, listeners: list[Listener], now: float) -> list[Listener]:
        """Dimmed placeholder rows for ports that stopped listening recently."""
        minutes = self.cfg.insights.show_stopped_minutes
        if minutes <= 0:
            return []
        window = minutes * SECONDS_PER_MINUTE
        live = {x.key for x in listeners}
        ghosts: list[Listener] = []
        for entry in self.history.entries.values():
            if entry.key in live or (entry.protocol == "UDP" and not self.show_udp):
                continue
            age = now - entry.last_seen
            if age <= 0 or age > window:
                continue
            ghost = Listener(
                port=entry.port,
                protocol=entry.protocol,
                state=ListenerState.STOPPED,
                source=Source.LOCAL,
            )
            ghost.identity = Identity(
                service=entry.service or (entry.names[0] if entry.names else "?"),
                role=Role.UNKNOWN,
                confidence=0.0,
                reasons=["last known identity before the port stopped listening"],
            )
            was = f" (was PID {', '.join(str(p) for p in entry.pids)})" if entry.pids else ""
            ghost.insights = [
                Insight(
                    Level.WARNING,
                    "stopped",
                    f"Stopped {format_duration(age)} ago{was}",
                    "restart it if it should be running; this row disappears after "
                    f"{minutes:g} min",
                )
            ]
            ghost.cpu_history = list(entry.cpu)
            ghost.conn_history = list(entry.conns)
            ghost.first_seen = entry.first_seen
            ghosts.append(ghost)
        return ghosts

    def _attach_containers(
        self, listeners: list[Listener], containers: list[ContainerInfo]
    ) -> None:
        if not containers:
            return
        by_port: dict[int, ContainerInfo] = {}
        for c in containers:
            for port in c.host_ports:
                by_port.setdefault(port, c)
        present = {lst.port for lst in listeners if lst.protocol == "TCP"}
        for lst in listeners:
            owner = by_port.get(lst.port)
            if owner is None:
                continue
            mapped_proto = owner.port_map.get(lst.port, "/tcp").split("/")[-1].lower()
            if lst.protocol.lower() == mapped_proto:
                lst.container = owner
                lst.source = Source.DOCKER
        # Ports published by Docker but invisible to psutil (Linux iptables NAT).
        for c in containers:
            for port in c.host_ports:
                proto = c.port_map.get(port, "/tcp").split("/")[-1].upper()
                if proto == "TCP" and port not in present:
                    lst = Listener(
                        port=port,
                        protocol="TCP",
                        state=ListenerState.LISTEN,
                        source=Source.DOCKER,
                        container=c,
                    )
                    listeners.append(lst)
                    present.add(port)
                elif (
                    proto == "UDP"
                    and self.show_udp
                    and not any(x.port == port and x.protocol == "UDP" for x in listeners)
                ):
                    listeners.append(
                        Listener(
                            port=port,
                            protocol="UDP",
                            state=ListenerState.BOUND,
                            source=Source.DOCKER,
                            container=c,
                        )
                    )
            if self.cfg.docker.show_unpublished and not c.host_ports:
                for port in c.internal_ports:
                    listeners.append(
                        Listener(
                            port=port,
                            protocol="TCP",
                            state=ListenerState.INTERNAL,
                            source=Source.DOCKER,
                            container=c,
                        )
                    )
        listeners.sort(key=lambda x: (x.port, x.protocol, x.state))

    def _attach_bandwidth(self, listeners: list[Listener]) -> None:
        # Containers: the stats API gives exact per-container network counters.
        for lst in listeners:
            c = lst.container
            if c and lst.uses_container_stats and c.net_rx_rate is not None:
                lst.bytes_in_rate = c.net_rx_rate
                lst.bytes_out_rate = c.net_tx_rate
                lst.bandwidth_shared = len(c.host_ports) > 1
        if not self.bandwidth.available:
            return
        rates = self.bandwidth.rates()
        if not rates:
            return
        ports_per_pid: dict[int, int] = {}
        for lst in listeners:
            for pid in lst.pids:
                ports_per_pid[pid] = ports_per_pid.get(pid, 0) + 1
        for lst in listeners:
            if lst.bytes_in_rate is not None:
                continue  # already covered by container stats
            total_in = total_out = 0.0
            found = False
            shared = False
            for pid in lst.pids:
                r = rates.get(pid)
                if r is None:
                    continue
                found = True
                total_in += r[0]
                total_out += r[1]
                if ports_per_pid.get(pid, 1) > 1:
                    shared = True
            if found:
                lst.bytes_in_rate = total_in
                lst.bytes_out_rate = total_out
                lst.bandwidth_shared = shared
