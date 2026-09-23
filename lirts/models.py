"""Data model shared by collectors, the identity engine, insights and the UI.

The central object is :class:`Listener`: one row in the UI, representing a
``(port, protocol)`` pair together with every process bound to it, the Docker
container behind it (if any), its HTTP health, identity and derived insights.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from lirts.identity_rules import Role
from lirts.models_parts import (
    ACTIVITY_ACTIVE,
    ACTIVITY_HOT,
    ACTIVITY_IDLE,
    LEVEL_ERROR,
    LEVEL_INFO,
    LEVEL_WARNING,
    STATUS_ERROR,
    STATUS_HEALTHY,
    STATUS_UNKNOWN,
    STATUS_WARNING,
    Activity,
    ContainerHealth,
    ContainerInfo,
    Edge,
    EventKind,
    HealthResult,
    HttpProbe,
    Identity,
    Insight,
    Level,
    ListenerProcess,
    ListenerState,
    MountInfo,
    Protocol,
    Source,
    SshSession,
    Status,
    is_docker_proxy_name,
)

__all__ = [
    "ACTIVITY_ACTIVE",
    "ACTIVITY_HOT",
    "ACTIVITY_IDLE",
    "LEVEL_ERROR",
    "LEVEL_INFO",
    "LEVEL_WARNING",
    "STATUS_ERROR",
    "STATUS_HEALTHY",
    "STATUS_UNKNOWN",
    "STATUS_WARNING",
    "Activity",
    "ContainerHealth",
    "ContainerInfo",
    "Edge",
    "Event",
    "EventKind",
    "HealthResult",
    "HttpProbe",
    "Identity",
    "Insight",
    "Level",
    "Listener",
    "ListenerProcess",
    "ListenerState",
    "MountInfo",
    "Protocol",
    "Snapshot",
    "Source",
    "SshSession",
    "Status",
    "SystemStats",
    "is_docker_proxy_name",
]


@dataclass
class Listener:
    """A unified row: a port plus everything we know about what serves it."""

    port: int
    protocol: str  # "TCP" or "UDP"
    processes: list[ListenerProcess] = field(default_factory=list)
    state: str = ListenerState.LISTEN
    source: str = Source.LOCAL
    container: ContainerInfo | None = None
    identity: Identity = field(default_factory=Identity)
    http: HttpProbe = field(default_factory=HttpProbe)
    health: HealthResult = field(default_factory=HealthResult)
    hosts: list[str] = field(default_factory=list)
    activity: str = Activity.IDLE
    activity_score: float = 0.0
    bytes_in_rate: float | None = None  # bytes per second, best effort
    bytes_out_rate: float | None = None
    bandwidth_shared: bool = False  # process serves several ports; rate is per process
    proxy_targets: list[int] = field(default_factory=list)
    proxy_chain: str | None = None
    insights: list[Insight] = field(default_factory=list)
    shared_reason: str | None = None
    cpu_history: list[float] = field(default_factory=list)
    conn_history: list[int] = field(default_factory=list)
    latency_history: list[float] = field(default_factory=list)
    activity_history: list[str] = field(default_factory=list)
    bytes_in_history: list[float] = field(default_factory=list)  # B/s per refresh
    bytes_out_history: list[float] = field(default_factory=list)
    bytes_in_total: float = 0.0  # bytes observed since lirts started (traffic panel)
    bytes_out_total: float = 0.0
    first_seen: float | None = None
    restarts_last_hour: int = 0
    last_active: float | None = None
    tunnel: dict[str, Any] | None = None  # parsed kubectl port-forward / ssh -L details
    clients: list[Edge] = field(default_factory=list)  # local processes connected to this port
    row_id: str | None = None  # explicit key for rows that are not a listening port (ssh)
    ssh: SshSession | None = None  # set for outbound ssh session rows (state "SSH")
    patterns: list[str] = field(default_factory=list)  # recurring issues on this port (memory)

    # ----- convenience accessors -------------------------------------------------

    @property
    def key(self) -> str:
        return self.row_id or f"{self.port}/{self.protocol}"

    @property
    def primary(self) -> ListenerProcess | None:
        """The process that best represents the port (the oldest one)."""
        if not self.processes:
            return None
        return min(self.processes, key=lambda p: (p.create_time or float("inf"), p.pid))

    @property
    def pid(self) -> int | None:
        p = self.primary
        return p.pid if p else None

    @property
    def pids(self) -> list[int]:
        return [p.pid for p in self.processes]

    @property
    def name(self) -> str:
        p = self.primary
        if p:
            return p.name
        if self.container:
            return self.container.name
        return "?"

    @property
    def addresses(self) -> list[str]:
        seen: list[str] = []
        for p in self.processes:
            for a in p.addresses:
                if a not in seen:
                    seen.append(a)
        return seen

    @property
    def uses_container_stats(self) -> bool:
        """True when container stats stand in for the Docker proxy process."""
        if not self.container or self.container.cpu_percent is None:
            return False
        return not self.processes or any(is_docker_proxy_name(p.name) for p in self.processes)

    @property
    def real_processes(self) -> list[ListenerProcess]:
        """Processes excluding Docker's port-forwarding proxies."""
        return [p for p in self.processes if not is_docker_proxy_name(p.name)]

    @property
    def cpu_percent(self) -> float:
        total = sum(p.cpu_percent for p in self.real_processes)
        if self.uses_container_stats and self.container and self.container.cpu_percent is not None:
            total += self.container.cpu_percent
        elif len(self.real_processes) != len(self.processes) and not self.container:
            total += sum(p.cpu_percent for p in self.processes if is_docker_proxy_name(p.name))
        return total

    @property
    def memory_mb(self) -> float:
        total = sum(p.memory_mb for p in self.real_processes)
        if self.uses_container_stats and self.container and self.container.memory_mb is not None:
            total += self.container.memory_mb
        elif len(self.real_processes) != len(self.processes) and not self.container:
            total += sum(p.memory_mb for p in self.processes if is_docker_proxy_name(p.name))
        return total

    @property
    def connections(self) -> int:
        return sum(p.inbound_connections for p in self.processes)

    @property
    def remote_conns(self) -> list[str]:
        out: list[str] = []
        for p in self.processes:
            for rc in p.remote_conns:
                if rc not in out:
                    out.append(rc)
        return out

    @property
    def uptime_seconds(self) -> float | None:
        if self.container and self.container.started_at:
            return self.container.uptime_seconds
        p = self.primary
        return p.uptime_seconds if p else None

    @property
    def status(self) -> str:
        """Overall status of the row: the level of its worst insight."""
        if not self.insights:
            if not self.processes and not self.container:
                return Status.UNKNOWN
            return Status.HEALTHY
        worst = max(self.insights, key=lambda i: i.rank)
        if worst.level == Level.ERROR:
            return Status.ERROR
        if worst.level == Level.WARNING:
            return Status.WARNING
        return Status.HEALTHY

    @property
    def is_docker(self) -> bool:
        return self.source == Source.DOCKER

    @property
    def is_web(self) -> bool:
        return self.http.ok or self.identity.role in {
            Role.FRONTEND,
            Role.BACKEND,
            Role.PROXY,
            Role.TOOL,
        }

    def add_insight(self, insight: Insight) -> None:
        """Add ``insight`` unless the same code and message is already there."""
        if not any(i.code == insight.code and i.message == insight.message for i in self.insights):
            self.insights.append(insight)

    def search_blob(self) -> str:
        """Lower-cased text used by the filter box."""
        parts = [
            str(self.port),
            self.protocol,
            self.state,
            self.source,
            self.identity.service,
            self.identity.role,
            self.identity.project or "",
            self.activity,
            self.status,
            self.name,
            self.proxy_chain or "",
            *[p.command for p in self.processes],
            *[str(p.pid) for p in self.processes],
            *self.hosts,
            *([self.ssh.host, self.ssh.remote, *self.ssh.forwards] if self.ssh else []),
        ]
        if self.container:
            parts.extend([self.container.name, self.container.image, self.container.stack or ""])
        return " ".join(parts).lower()


@dataclass
class SystemStats:
    """Machine-wide numbers for the top bar."""

    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    mem_used_gb: float = 0.0
    mem_total_gb: float = 0.0
    net_up_bps: float = 0.0
    net_down_bps: float = 0.0
    cpu_history: list[float] = field(default_factory=list)
    mem_history: list[float] = field(default_factory=list)
    net_up_history: list[float] = field(default_factory=list)
    net_down_history: list[float] = field(default_factory=list)
    load_avg: tuple[float, float, float] | None = None
    net_bytes_sent: int = 0  # since boot
    net_bytes_recv: int = 0
    address: str = "-"  # primary interface address, for the traffic panel title
    interface: str = "-"


@dataclass
class Event:
    """Something that changed between two refreshes."""

    timestamp: float
    level: str
    message: str
    port: int | None = None

    @property
    def kind(self) -> EventKind:
        """What the event is about, derived from the marker and wording of its message."""
        m = self.message
        if m.startswith("[+]"):
            return EventKind.BACK if " back on " in m else EventKind.STARTED
        if m.startswith("[-]"):
            return EventKind.STOPPED
        if m.startswith("[~]"):
            for word in (EventKind.RESTARTED, EventKind.REPLACED, EventKind.DEGRADED):
                if f" {word}" in m:
                    return word
            return EventKind.CHANGED
        if m.startswith("[!]"):
            return EventKind.CONFLICT if "port conflict" in m else EventKind.FAILING
        if m.startswith("[✓]"):
            return EventKind.RECOVERED
        return EventKind.OTHER

    @property
    def key(self) -> str | None:
        """The ``port/PROTO`` the message is about, when it names one."""
        found = re.search(r"\b(\d+/(?:TCP|UDP))\b", self.message)
        return found.group(1) if found else None


@dataclass
class Snapshot:
    """The complete state produced by one refresh."""

    listeners: list[Listener] = field(default_factory=list)
    system: SystemStats = field(default_factory=SystemStats)
    events: list[Event] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    docker_available: bool = False
    container_count: int = 0
    refresh_ms: float = 0.0
    kube: Any = None  # lirts.collectors.kube.KubeState when Kubernetes is enabled
    edges: list[Edge] = field(default_factory=list)
    ssh_sessions: list[SshSession] = field(default_factory=list)
    routes: list[Any] = field(default_factory=list)  # lirts.collectors.proxies.Route
    hosts_names: list[str] = field(default_factory=list)  # /etc/hosts names on loopback
    patterns: list[tuple[str, str, str | None]] = field(default_factory=list)  # (level, msg, hint)

    def by_key(self, key: str) -> Listener | None:
        """The listener with this ``port/PROTO`` (or ssh row id), if it is in the snapshot."""
        for lst in self.listeners:
            if lst.key == key:
                return lst
        return None

    def by_port(self, port: int, protocol: str = "TCP") -> Listener | None:
        """The listener on ``port`` for this protocol, if it is in the snapshot."""
        return self.by_key(f"{port}/{protocol}")

    @property
    def summary(self) -> dict[str, int]:
        """Row counts by activity, status and source, for the top bar."""
        out = {
            "total": len(self.listeners),
            "active": 0,
            "idle": 0,
            "hot": 0,
            "warnings": 0,
            "errors": 0,
            "docker": 0,
        }
        for lst in self.listeners:
            out[lst.activity] = out.get(lst.activity, 0) + 1
            if lst.status == Status.WARNING:
                out["warnings"] += 1
            elif lst.status == Status.ERROR:
                out["errors"] += 1
            if lst.is_docker:
                out["docker"] += 1
        return out
