"""One port's rolling history record, with its JSON round-trip."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from lirts.constants import (
    ENTRY_ERRORS_SAVED,
    ENTRY_RESTARTS_SAVED,
    ENTRY_SERVICES_KEPT,
    HISTORY_WINDOW,
)

# The rolling sample windows of an entry, all kept to the same length.
SAMPLE_FIELDS = ("cpu", "conns", "latency", "activity", "bytes_in", "bytes_out")


@dataclass
class HistoryEntry:
    """Everything lirts remembers about one ``port/PROTO`` while it runs."""

    key: str
    port: int
    protocol: str
    first_seen: float
    last_seen: float
    pids: list[int] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    service: str = ""
    pid_changes: list[float] = field(default_factory=list)
    uptime_start: float = 0.0
    cpu: deque[float] = field(default_factory=lambda: deque(maxlen=HISTORY_WINDOW))
    conns: deque[int] = field(default_factory=lambda: deque(maxlen=HISTORY_WINDOW))
    latency: deque[float] = field(default_factory=lambda: deque(maxlen=HISTORY_WINDOW))
    activity: deque[str] = field(default_factory=lambda: deque(maxlen=HISTORY_WINDOW))
    bytes_in: deque[float] = field(default_factory=lambda: deque(maxlen=HISTORY_WINDOW))
    bytes_out: deque[float] = field(default_factory=lambda: deque(maxlen=HISTORY_WINDOW))
    last_active: float | None = None
    errors: list[tuple[float, str]] = field(default_factory=list)
    conflict: bool = False
    health: str = ""
    # How many refreshes each "project/service" was seen holding this port.
    services_seen: dict[str, int] = field(default_factory=dict)

    def note_service(self, label: str) -> None:
        """Count one more refresh for ``label``, forgetting the rarest name past the cap."""
        if not label:
            return
        self.services_seen[label] = self.services_seen.get(label, 0) + 1
        while len(self.services_seen) > ENTRY_SERVICES_KEPT:
            rarest = min(self.services_seen, key=lambda name: self.services_seen[name])
            del self.services_seen[rarest]

    def restarts_within(self, seconds: float, now: float) -> int:
        """Number of recorded PID changes in the last ``seconds``."""
        return sum(1 for t in self.pid_changes if now - t <= seconds)

    def to_dict(self) -> dict[str, Any]:
        """The entry as plain JSON values, with the long lists trimmed."""
        return {
            "key": self.key,
            "port": self.port,
            "protocol": self.protocol,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "pids": self.pids,
            "names": self.names,
            "service": self.service,
            "pid_changes": self.pid_changes[-ENTRY_RESTARTS_SAVED:],
            "uptime_start": self.uptime_start,
            "cpu": list(self.cpu),
            "conns": list(self.conns),
            "latency": list(self.latency),
            "activity": list(self.activity),
            "bytes_in": list(self.bytes_in),
            "bytes_out": list(self.bytes_out),
            "last_active": self.last_active,
            "errors": self.errors[-ENTRY_ERRORS_SAVED:],
            "conflict": self.conflict,
            "health": self.health,
            "services_seen": self.services_seen,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], window: int) -> HistoryEntry:
        """Rebuild an entry from :meth:`to_dict`, resizing the sample windows to ``window``."""
        entry = cls(
            key=data["key"],
            port=int(data["port"]),
            protocol=str(data.get("protocol", "TCP")),
            first_seen=float(data.get("first_seen", 0.0)),
            last_seen=float(data.get("last_seen", 0.0)),
            pids=[int(p) for p in data.get("pids", [])],
            names=[str(n) for n in data.get("names", [])],
            service=str(data.get("service", "")),
            pid_changes=[float(t) for t in data.get("pid_changes", [])],
            uptime_start=float(data.get("uptime_start", 0.0)),
            last_active=data.get("last_active"),
            errors=[(float(when), str(message)) for when, message in data.get("errors", [])],
            conflict=bool(data.get("conflict", False)),
            health=str(data.get("health", "")),
            services_seen={
                str(name): int(count) for name, count in (data.get("services_seen") or {}).items()
            },
        )
        for name in SAMPLE_FIELDS:
            dq: deque[Any] = deque(maxlen=window)
            dq.extend(data.get(name, []))
            setattr(entry, name, dq)
        return entry
