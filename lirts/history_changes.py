"""Which events one refresh of a port produces.

Pure comparisons between a :class:`~lirts.history_entry.HistoryEntry` as it was
and the :class:`~lirts.models.Listener` just collected.  The functions here
decide what happened and update the entry's restart counters;
:mod:`lirts.history_store` records the events and keeps the rolling samples.
"""

from __future__ import annotations

from lirts.history_entry import HistoryEntry
from lirts.models import Event, Level, Listener, Status


def pid_change_event(
    listener: Listener,
    *,
    entry: HistoryEntry,
    now: float,
    retention: float,
    was_absent: bool,
) -> Event | None:
    """The event for a port that came back or changed processes, or None when nothing did.

    Also records the restart in ``entry.pid_changes`` and resets ``entry.uptime_start``,
    because "was this a restart?" and "count it" are the same decision.
    """
    if was_absent:
        entry.uptime_start = now
        if now - entry.last_seen >= retention:
            return None
        entry.pid_changes.append(now)
        return Event(
            now,
            Level.INFO,
            f"[+] {listener.identity.service} back on {listener.key}",
            listener.port,
        )
    pids = sorted(listener.pids)
    if not pids or not entry.pids or pids == entry.pids:
        return None
    old, new = set(entry.pids), set(pids)
    if old & new:
        # One of the previous processes is still there: helpers came or went (an updater,
        # a child that inherited the socket).  Not a restart.
        joined = sorted(new - old)
        left = sorted(old - new)
        what = (f"helper PID {joined} joined" if joined else "") + (
            (", " if joined and left else "") + (f"PID {left} left" if left else "")
        )
        return Event(
            now,
            Level.INFO,
            f"[~] {listener.identity.service} on {listener.key} changed: {what}; "
            f"main PID {sorted(old & new)} still running",
            listener.port,
        )
    entry.pid_changes.append(now)
    entry.uptime_start = now
    names = sorted({p.name for p in listener.processes})
    same_names = set(names) & set(entry.names)
    label = "restarted" if same_names or not entry.names else "replaced"
    level = Level.INFO if label == "restarted" else Level.WARNING
    return Event(
        now,
        level,
        f"[~] {listener.identity.service} on {listener.key} {label} (PID {entry.pids} → {pids})",
        listener.port,
    )


def status_change_event(
    listener: Listener,
    *,
    entry: HistoryEntry,
    status: str,
    conflict: bool,
    now: float,
) -> Event | None:
    """The event for a port whose overall status changed, or None when it did not.

    A port conflict is reported separately, so a failure caused by one is not repeated here.
    """
    if not entry.health or status == entry.health:
        return None
    worst = max(listener.insights, key=lambda i: i.rank) if listener.insights else None
    detail = f": {worst.message}" if worst else ""
    if status == Status.ERROR and not conflict:
        return Event(
            now,
            Level.ERROR,
            f"[!] {listener.identity.service} on {listener.key} is failing{detail}",
            listener.port,
        )
    if status == Status.WARNING and entry.health == Status.HEALTHY:
        return Event(
            now,
            Level.WARNING,
            f"[~] {listener.identity.service} on {listener.key} degraded{detail}",
            listener.port,
        )
    if status == Status.HEALTHY and entry.health in (Status.ERROR, Status.WARNING):
        return Event(
            now,
            Level.INFO,
            f"[✓] {listener.identity.service} on {listener.key} recovered",
            listener.port,
        )
    return None
