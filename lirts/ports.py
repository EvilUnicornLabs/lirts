"""Port memory: what usually runs on a port, and whether its holder looks left over.

Nothing new is collected for this.  The answer is folded out of what lirts already keeps:
the learned star map (:mod:`lirts.topology_store`), which knows on how many different days
it saw a service and which ports that service had, and the rolling history
(:mod:`lirts.history_store`), whose entries count the services seen on each port refresh by
refresh.  A :class:`PortMemory` is built once per refresh and answers "what usually holds
this port" for the insights, the side panel and ``lirts who``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from typing import Any

import psutil

from lirts.constants import (
    PORT_USUAL_MIN_DAYS,
    PORT_USUAL_MIN_REFRESHES,
    SECONDS_PER_DAY,
)
from lirts.history_entry import HistoryEntry
from lirts.models import ContainerInfo, Listener, ListenerProcess
from lirts.topology import Graph, NodeKind

# Stars that are not a service running on a port of this machine.
_NOT_A_HOLDER = {NodeKind.MACHINE, NodeKind.HOST, NodeKind.CLUSTER, NodeKind.TUNNEL}
# The ppids a process has once the process that started it is gone.
_REPARENTED_PPIDS = (0, 1)


def holder_label(service: str, project: str | None) -> str:
    """``project/service`` when the project is known, the service name alone otherwise."""
    return f"{project}/{service}" if project else service


def split_label(label: str) -> tuple[str, str | None]:
    """A :func:`holder_label` back into ``(service, project)``."""
    if "/" in label:
        project, service = label.split("/", 1)
        return service, project or None
    return label, None


def listener_holder(listener: Listener) -> tuple[str, str | None]:
    """The ``(service, project)`` a row is remembered under; the compose stack wins."""
    service = listener.identity.service or listener.name
    project = listener.identity.project
    if listener.container is not None and listener.container.stack:
        project = listener.container.stack
    return service, project


def last_seen_text(last_seen: float, now: float) -> str:
    """How long ago something was seen, in the words the panel and the CLI use."""
    if last_seen <= 0:
        return "unknown"
    days = _day_number(now) - _day_number(last_seen)
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def _day_number(ts: float) -> int:
    """The local calendar day of a timestamp, as a whole number of days."""
    local = time.localtime(ts)
    return int(time.mktime((*local[:3], 0, 0, 0, 0, 0, -1)) // SECONDS_PER_DAY)


@dataclass(slots=True)
class UsualHolder:
    """A service lirts has seen holding a port, and how well it knows it."""

    port: int
    service: str
    project: str | None = None
    days: int = 0  # different calendar days the star map saw it
    refreshes: int = 0  # refreshes the history counted it on this port
    last_seen: float = 0.0

    @property
    def label(self) -> str:
        return holder_label(self.service, self.project)

    @property
    def seen_text(self) -> str:
        """How well it is known: ``3 days`` once the map has more than one, else refreshes."""
        if self.days >= PORT_USUAL_MIN_DAYS:
            return f"{self.days} days"
        return f"{self.refreshes} refreshes" if self.refreshes else "1 day"

    @property
    def strength(self) -> tuple[int, int, float]:
        """Sort key: the most days wins, then the most refreshes, then the newest."""
        return (self.days, self.refreshes, self.last_seen)


class PortMemory:
    """What lirts remembers about the ports of this machine, built fresh every refresh."""

    def __init__(
        self,
        holders: Iterable[UsualHolder] = (),
        *,
        young: bool = True,
        stacks: dict[str, int] | None = None,
    ) -> None:
        self.young = young
        self._stacks = dict(stacks or {})
        self._by_port: dict[int, list[UsualHolder]] = {}
        for holder in holders:
            self._by_port.setdefault(holder.port, []).append(holder)
        for group in self._by_port.values():
            group.sort(key=lambda h: h.strength, reverse=True)

    def __bool__(self) -> bool:
        """False while lirts has not seen anything yet: the panel then says nothing."""
        return bool(self._by_port)

    def to_dict(self) -> dict[str, Any]:
        """The memory as plain JSON values, for a daemon frame."""
        return {
            "young": self.young,
            "stacks": dict(self._stacks),
            "holders": [asdict(h) for group in self._by_port.values() for h in group],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PortMemory:
        """Rebuild what :meth:`to_dict` produced."""
        return cls(
            (UsualHolder(**h) for h in data.get("holders", [])),
            young=bool(data.get("young", True)),
            stacks=data.get("stacks") or {},
        )

    @classmethod
    def build(
        cls,
        *,
        graph: Graph,
        entries: Iterable[HistoryEntry] = (),
        containers: Iterable[ContainerInfo] = (),
        now: float | None = None,
    ) -> PortMemory:
        """Fold the learned map and the history into one answer per port."""
        now = now or time.time()
        found: dict[tuple[int, str, str | None], UsualHolder] = {}
        first_seen: list[float] = []
        for node in graph.nodes.values():
            if node.kind in _NOT_A_HOLDER or not node.ports:
                continue
            if node.first_seen:
                first_seen.append(node.first_seen)
            for port in node.ports:
                holder = found.setdefault(
                    (port, node.label, node.project),
                    UsualHolder(port, node.label, node.project),
                )
                holder.days = max(holder.days, len(node.days_seen))
                holder.last_seen = max(holder.last_seen, node.last_seen)
        for entry in entries:
            if entry.first_seen:
                first_seen.append(entry.first_seen)
            for label, count in entry.services_seen.items():
                service, project = split_label(label)
                holder = found.setdefault(
                    (entry.port, service, project),
                    UsualHolder(entry.port, service, project),
                )
                holder.refreshes += count
                holder.last_seen = max(holder.last_seen, entry.last_seen)
        stacks: dict[str, int] = {}
        for container in containers:
            if container.stack:
                stacks[container.stack] = stacks.get(container.stack, 0) + 1
        earliest = min(first_seen, default=now)
        return cls(found.values(), young=(now - earliest) < SECONDS_PER_DAY, stacks=stacks)

    # ----- questions the rest of lirts asks -------------------------------------------

    def is_usual(self, holder: UsualHolder) -> bool:
        """True when this is part of the usual picture, not something seen once."""
        if holder.days >= PORT_USUAL_MIN_DAYS:
            return True
        return self.young and holder.refreshes > PORT_USUAL_MIN_REFRESHES

    def holders(self, port: int) -> list[UsualHolder]:
        """Everything remembered on ``port``, the best known first."""
        return list(self._by_port.get(port, ()))

    def usual(self, port: int) -> UsualHolder | None:
        """What usually holds ``port``, or None when lirts does not know it well enough."""
        for holder in self._by_port.get(port, ()):
            if self.is_usual(holder):
                return holder
        return None

    def also_used_by(self, port: int) -> list[UsualHolder]:
        """Other projects that usually use ``port`` besides its usual holder."""
        top = self.usual(port)
        if top is None:
            return []
        return [
            h
            for h in self._by_port[port]
            if h is not top and h.project != top.project and self.is_usual(h)
        ]

    def stack_size(self, stack: str) -> int:
        """How many containers of this compose project are running right now."""
        return self._stacks.get(stack, 0)


def parent_is_gone(
    process: ListenerProcess, *, alive: Callable[[int], bool] = psutil.pid_exists
) -> bool:
    """True when whatever started this process is gone and the OS re-parented it.

    ``alive`` is the liveness test; tests pass a fake process table instead of the real one.
    """
    ppid = process.ppid
    if ppid is None:
        return False
    if ppid in _REPARENTED_PPIDS:
        return True
    return not alive(ppid)


__all__ = [
    "PortMemory",
    "UsualHolder",
    "holder_label",
    "last_seen_text",
    "listener_holder",
    "parent_is_gone",
    "split_label",
]
