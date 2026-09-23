"""Blast radius of killing a listener and event-log analysis."""

from __future__ import annotations

import time

from lirts.constants import (
    BLAST_CLIENTS_SHOWN,
    BLAST_PORTS_SHOWN,
    EVENT_WINDOW_HOURS,
    RECURRING_MIN_EVENTS,
    SECONDS_PER_HOUR,
)
from lirts.identity import Role, role_label
from lirts.insights_explain import format_duration
from lirts.models import (
    Event,
    EventKind,
    Level,
    Listener,
    ListenerState,
    Snapshot,
)

# ----- blast radius ----------------------------------------------------------------


def blast_radius(listener: Listener, snapshot: Snapshot) -> list[str]:
    """Human-readable consequences of killing everything behind ``listener``."""
    impacts: list[str] = []
    pids = set(listener.pids)
    if not pids:
        if listener.container:
            impacts.append(f"no host process; use stop (x) for container {listener.container.name}")
        return impacts
    if listener.connections:
        impacts.append(f"{listener.connections} active connection(s) will be dropped")
    if listener.clients:
        shown = listener.clients[:BLAST_CLIENTS_SHOWN]
        names = ", ".join(f"{c.client_name} (PID {c.client_pid})" for c in shown)
        extra = len(listener.clients) - BLAST_CLIENTS_SHOWN
        more = f" +{extra}" if extra > 0 else ""
        impacts.append(
            f"{len(listener.clients)} local client process(es) will lose their connection: {names}{more}"
        )
    if len(listener.processes) > 1:
        impacts.append(
            f"all {len(listener.processes)} processes on port {listener.port} will be terminated"
        )
    # Other ports served by the same process(es).
    others = [
        other
        for other in snapshot.listeners
        if other.key != listener.key and pids & set(other.pids)
    ]
    if others:
        ports = ", ".join(str(o.port) for o in others[:BLAST_PORTS_SHOWN])
        hidden = len(others) - BLAST_PORTS_SHOWN
        more = f" (+{hidden} more)" if hidden > 0 else ""
        impacts.append(f"the same process also serves port(s) {ports}{more}")
    if listener.identity.role == Role.DOCKER or (
        listener.primary and listener.primary.name.startswith("com.docker")
    ):
        impacts.append("this is the Docker port proxy: every published container port will go down")
    # Dependents: proxies that forward to this port.
    for other in snapshot.listeners:
        if listener.port in other.proxy_targets:
            impacts.append(
                f"{other.identity.service} on {other.port} proxies to this port and will fail"
            )
    # Targets: backends that lose their front door.
    if listener.proxy_targets:
        targets = ", ".join(str(p) for p in listener.proxy_targets)
        impacts.append(f"backends on {targets} will no longer be reachable through this proxy")
    if listener.identity.role in {Role.DB, Role.CACHE, Role.QUEUE}:
        impacts.append(
            f"a {role_label(listener.identity.role)} is a shared dependency; other services may break"
        )
    if listener.identity.role == Role.SYSTEM:
        impacts.append("system service: the OS may respawn it or lose functionality")
    if listener.container:
        impacts.append(f"container {listener.container.name} may restart it; prefer stop (x)")
    return impacts


# ----- event log analysis -----------------------------------------------------------

# Event kinds that are worth reporting when they happen again and again.
_RECURRING_LABELS: dict[EventKind, str] = {
    EventKind.RESTARTED: "restarted",
    EventKind.REPLACED: "replaced by another process",
    EventKind.STOPPED: "stopped",
    EventKind.CONFLICT: "hit a port conflict",
    EventKind.FAILING: "failed",
    EventKind.DEGRADED: "degraded",
    EventKind.BACK: "came back",
}
_RECURRING_KINDS = frozenset(_RECURRING_LABELS)


def event_insights(
    events: list[Event],
    *,
    snapshot: Snapshot,
    now: float | None = None,
    window_hours: float = EVENT_WINDOW_HOURS,
) -> list[tuple[str, str, str | None]]:
    """Summarise the event log: ``(level, message, suggestion)`` for ongoing and recurring issues."""
    now = now or time.time()
    window = window_hours * SECONDS_PER_HOUR
    out: list[tuple[str, str, str | None]] = []
    recent = [e for e in events if now - e.timestamp <= window]

    # Ongoing: an error/warning-level condition that the current snapshot still shows.
    for lst in snapshot.listeners:
        worst = max(lst.insights, key=lambda i: i.rank) if lst.insights else None
        if worst is None or worst.level == Level.INFO:
            continue
        related = [
            e
            for e in recent
            if e.key == lst.key
            and e.kind in (EventKind.CONFLICT, EventKind.FAILING, EventKind.DEGRADED)
        ]
        since = min((e.timestamp for e in related), default=None)
        since_text = f" for {format_duration(now - since)}" if since else ""
        out.append(
            (
                worst.level,
                f"ongoing{since_text}: {lst.identity.service} on {lst.key}: {worst.message}",
                worst.suggestion,
            )
        )

    # Recurring: the same kind of event on the same port several times in the window.
    counter: dict[tuple[str, EventKind], list[Event]] = {}
    for e in recent:
        if e.kind in _RECURRING_KINDS:
            counter.setdefault((e.key or "?", e.kind), []).append(e)
    live_keys = {x.key for x in snapshot.listeners if x.state != ListenerState.STOPPED}
    for (key, kind), items in sorted(counter.items()):
        if len(items) < RECURRING_MIN_EVENTS:
            continue
        last = max(e.timestamp for e in items)
        span = (
            "the last hour"
            if all(now - e.timestamp <= SECONDS_PER_HOUR for e in items)
            else f"{window_hours:g}h"
        )
        label = _RECURRING_LABELS[kind]
        service = next((x.identity.service for x in snapshot.listeners if x.key == key), key)
        message = f"recurring: {service} on {key} {label} {len(items)}× in {span} (last {time.strftime('%H:%M', time.localtime(last))})"
        suggestion: str | None
        if kind in (EventKind.RESTARTED, EventKind.REPLACED, EventKind.FAILING):
            suggestion = "looks like a crash loop or a supervisor restarting it; check its logs (l) and the History tab (i)"
        elif kind in (EventKind.STOPPED, EventKind.BACK):
            suggestion = "the port flaps; if it is a dev server, it may be reloading or being restarted by a watcher"
        elif kind == EventKind.CONFLICT:
            suggestion = "two things keep grabbing the same port; pin one of them to another port"
        else:
            suggestion = None
        level = (
            Level.ERROR
            if kind in (EventKind.FAILING, EventKind.CONFLICT) and key in live_keys
            else Level.WARNING
        )
        out.append((level, message, suggestion))
    # Remembered across runs: the long-term tally.
    out.extend(snapshot.patterns)
    return out
