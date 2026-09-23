"""Shared status constants and the small value objects of the data model.

Split out of :mod:`lirts.models`, which re-exports every name here; import
from ``lirts.models``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from lirts.identity_rules import Role
from lirts.identity_rules_web import ProbeKind


class Activity(StrEnum):
    """How busy a listener is, from calm to busy."""

    IDLE = "idle"
    ACTIVE = "active"
    HOT = "hot"


class Status(StrEnum):
    """Overall status of a row (the worst insight wins)."""

    HEALTHY = "healthy"
    WARNING = "warning"
    ERROR = "error"
    UNKNOWN = "unknown"


class Level(StrEnum):
    """Severity of an insight or an event."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    @property
    def rank(self) -> int:
        return _LEVEL_RANK[self]


class Source(StrEnum):
    """Where a row comes from."""

    LOCAL = "local"
    DOCKER = "docker"
    SSH = "ssh"


class Protocol(StrEnum):
    """Transport protocol of a socket."""

    TCP = "TCP"
    UDP = "UDP"


class ContainerHealth(StrEnum):
    """Docker's own health verdict for a container, when it has a health check."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    STARTING = "starting"


class ListenerState(StrEnum):
    """What the socket behind a row is doing."""

    LISTEN = "LISTEN"
    BOUND = "BOUND"  # a UDP socket with no peer
    INTERNAL = "INTERNAL"  # a container port that is not published to the host
    STOPPED = "STOPPED"  # remembered for a while after the port went away
    SSH = "SSH"  # an outbound ssh session, not a listening port


class EventKind(StrEnum):
    """What an event or a remembered pattern is about."""

    STARTED = "started"
    BACK = "back"
    STOPPED = "stopped"
    RESTARTED = "restarted"
    REPLACED = "replaced"
    DEGRADED = "degraded"
    CHANGED = "changed"
    CONFLICT = "conflict"
    FAILING = "failing"
    RECOVERED = "recovered"
    OTHER = "other"
    # Only lirts itself produces these two, from an action the user took.
    KILLED = "killed"
    USER_RESTART = "user-restart"


ACTIVITY_IDLE = Activity.IDLE
ACTIVITY_ACTIVE = Activity.ACTIVE
ACTIVITY_HOT = Activity.HOT

STATUS_HEALTHY = Status.HEALTHY
STATUS_WARNING = Status.WARNING
STATUS_ERROR = Status.ERROR
STATUS_UNKNOWN = Status.UNKNOWN

LEVEL_INFO = Level.INFO
LEVEL_WARNING = Level.WARNING
LEVEL_ERROR = Level.ERROR

_LEVEL_RANK: dict[str, int] = {Level.INFO: 0, Level.WARNING: 1, Level.ERROR: 2}

_DOCKER_PROXY_PREFIXES = (
    "com.docker",
    "docker-proxy",
    "vpnkit",
    "rootlesskit",
    "slirp4netns",
    "orbstack",
    "limactl",
    "qemu-system",
)


def is_docker_proxy_name(name: str | None) -> bool:
    """True when the process only forwards traffic into a container."""
    if not name:
        return False
    low = name.lower()
    return any(low.startswith(p) for p in _DOCKER_PROXY_PREFIXES)


@dataclass
class ListenerProcess:
    """One OS process bound to a listening socket."""

    pid: int
    name: str = "?"
    cmdline: list[str] = field(default_factory=list)
    addresses: list[str] = field(default_factory=list)
    ppid: int | None = None
    create_time: float | None = None
    cwd: str | None = None
    exe: str | None = None
    user: str | None = None
    status: str | None = None
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    threads: int = 0
    inbound_connections: int = 0
    remote_conns: list[str] = field(default_factory=list)
    accessible: bool = True
    origin: Any = None  # lirts.collectors.origin.Origin

    @property
    def command(self) -> str:
        return " ".join(self.cmdline) if self.cmdline else self.name

    @property
    def uptime_seconds(self) -> float | None:
        if self.create_time is None:
            return None
        return max(0.0, time.time() - self.create_time)


@dataclass
class Edge:
    """A local process holding an established connection to a local listening port."""

    client_pid: int
    client_name: str
    dst_port: int
    count: int = 1
    client_cmd: str = ""
    client_project: str | None = None
    client_ports: list[int] = field(default_factory=list)  # the client's local ports
    bytes_in_rate: float | None = None  # towards the client, when bandwidth.connections is on
    bytes_out_rate: float | None = None  # from the client to the port
    bytes_in: int | None = None  # totals since the connection opened
    bytes_out: int | None = None
    rate_history: list[float] = field(default_factory=list)  # in + out, one per sample


@dataclass
class SshSession:
    """An outbound ssh client connection (interactive session or tunnel)."""

    pid: int
    host: str
    remote: str
    user: str | None = None
    tty: str | None = None
    forwards: list[str] = field(default_factory=list)
    create_time: float | None = None
    cmd: str = ""


@dataclass
class MountInfo:
    """A Docker mount (volume or bind)."""

    type: str
    source: str
    destination: str
    name: str | None = None
    rw: bool = True


@dataclass
class ContainerInfo:
    """Docker container details relevant to a listener."""

    id: str
    short_id: str
    name: str
    image: str
    status: str = "running"
    health: str | None = None
    stack: str | None = None
    service: str | None = None
    working_dir: str | None = None
    started_at: float | None = None
    restart_count: int = 0
    host_ports: list[int] = field(default_factory=list)
    port_map: dict[int, str] = field(default_factory=dict)
    internal_ports: list[int] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    mounts: list[MountInfo] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    # Live stats from the Docker stats API (None until sampled twice).
    cpu_percent: float | None = None
    memory_mb: float | None = None
    memory_limit_mb: float | None = None
    net_rx_rate: float | None = None
    net_tx_rate: float | None = None
    net_rx_bytes: int | None = None  # totals since the container started
    net_tx_bytes: int | None = None
    pids_current: int | None = None

    @property
    def has_persistence(self) -> bool:
        return any(m.type == "volume" or m.type == "bind" for m in self.mounts)

    @property
    def uptime_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        return max(0.0, time.time() - self.started_at)


@dataclass
class HttpProbe:
    """Result of a silent HTTP health probe."""

    attempted: bool = False
    ok: bool = False
    scheme: str | None = None
    status: int | None = None
    latency_ms: float | None = None
    server: str | None = None
    powered_by: str | None = None
    content_type: str | None = None
    error: str | None = None
    probed_at: float = 0.0
    kind: ProbeKind = ProbeKind.OTHER
    dev_server: str | None = None  # the framework the response gave away, if any

    @property
    def summary(self) -> str:
        if not self.attempted:
            return "not probed"
        if self.ok and self.status is not None:
            lat = f" ({self.latency_ms:.0f} ms)" if self.latency_ms is not None else ""
            return f"HTTP {self.status}{lat}"
        return f"unreachable ({self.error or 'no response'})"


@dataclass
class HealthResult:
    """Outcome of the universal health check (TCP connect + optional HTTP health path)."""

    checked: bool = False
    tcp_ok: bool = False
    tcp_latency_ms: float | None = None
    tcp_error: str | None = None
    http_path: str | None = None
    http_status: int | None = None
    http_latency_ms: float | None = None
    http_note: str | None = None  # why there is no endpoint result (not HTTP, none found, …)
    checked_at: float = 0.0

    @property
    def ok(self) -> bool:
        if not self.checked:
            return True
        if not self.tcp_ok:
            return False
        return self.http_status is None or self.http_status < 400

    @property
    def summary(self) -> str:
        if not self.checked:
            return "-"
        if not self.tcp_ok:
            return f"✗ {self.tcp_error or 'unreachable'}"
        parts = [
            f"tcp {self.tcp_latency_ms:.0f}ms" if self.tcp_latency_ms is not None else "tcp ok"
        ]
        if self.http_path:
            lat = f" {self.http_latency_ms:.0f}ms" if self.http_latency_ms is not None else ""
            parts.append(f"{self.http_status} {self.http_path}{lat}")
        elif self.http_note:
            parts.append(self.http_note)
        return " · ".join(parts)


@dataclass
class Identity:
    """Best-effort answer to "what is this service actually?"."""

    service: str = "Unknown"
    role: str = Role.UNKNOWN
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    project: str | None = None


@dataclass
class Insight:
    """A detected condition with an optional suggested action."""

    level: str
    code: str
    message: str
    suggestion: str | None = None
    action: str | None = None  # e.g. "kill:1234", "restart-container", "open"

    @property
    def rank(self) -> int:
        return _LEVEL_RANK.get(self.level, 0)
