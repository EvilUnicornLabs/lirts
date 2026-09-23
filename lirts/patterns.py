"""Long-term memory of recurring issues, learned from the event log.

The event log keeps a day; this keeps a per-port, per-kind tally for ``days``
days (default 30) in lirts' own state directory: how often a port conflicted,
stopped, was restarted or killed, which services were involved, when it last
happened.  Nothing is scanned; it only counts what lirts observed while running.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lirts.constants import (
    PATTERN_SAMPLES_KEPT,
    PATTERN_SEEN_KEEP,
    PATTERN_SEEN_LIMIT,
    PATTERN_SERVICES_KEPT,
    PATTERN_SERVICES_SHOWN,
    PERSISTENT_PATTERN_FACTOR,
    SECONDS_PER_DAY,
    STATE_SAVE_INTERVAL,
)
from lirts.models import Event, EventKind, Level, Listener

log = logging.getLogger(__name__)

KINDS: dict[str, str] = {
    EventKind.CONFLICT: "port conflict",
    EventKind.STOPPED: "stopped",
    EventKind.BACK: "came back",
    EventKind.RESTARTED: "restarted",
    EventKind.REPLACED: "replaced by another process",
    EventKind.FAILING: "failed",
    EventKind.DEGRADED: "degraded",
    EventKind.KILLED: "killed from lirts",
    EventKind.USER_RESTART: "restarted from lirts",
}


def classify(ev: Event) -> EventKind | None:
    """The pattern kind of an event, or None when it is not worth counting."""
    if "by you (k)" in ev.message:
        return EventKind.KILLED
    if "by you (t)" in ev.message:
        return EventKind.USER_RESTART
    kind = ev.kind
    return kind if kind in KINDS else None


@dataclass
class Pattern:
    """How often one kind of thing happened on one port, and when."""

    key: str
    kind: str
    first: float = 0.0
    last: float = 0.0
    days: dict[str, int] = field(default_factory=dict)  # "YYYY-MM-DD" -> count
    services: list[str] = field(default_factory=list)
    samples: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return sum(self.days.values())

    @property
    def active_days(self) -> int:
        return len(self.days)

    @property
    def label(self) -> str:
        """Human wording of the kind, e.g. ``conflict`` → ``port conflict``."""
        return KINDS.get(self.kind, self.kind)

    def to_dict(self) -> dict[str, Any]:
        """The pattern as plain JSON values, with only the newest samples kept."""
        return {
            "key": self.key,
            "kind": self.kind,
            "first": self.first,
            "last": self.last,
            "days": self.days,
            "services": self.services,
            "samples": self.samples[-PATTERN_SAMPLES_KEPT:],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Pattern:
        """Rebuild a pattern from :meth:`to_dict`."""
        return cls(
            key=str(data["key"]),
            kind=str(data["kind"]),
            first=float(data.get("first", 0.0)),
            last=float(data.get("last", 0.0)),
            days={str(k): int(v) for k, v in (data.get("days") or {}).items()},
            services=[str(s) for s in data.get("services") or []],
            samples=[str(s) for s in data.get("samples") or []],
        )


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


class PatternStore:
    """Long-term tally of what keeps happening, learned from the event log."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        persist: bool = False,
        days: int = 30,
        min_count: int = 3,
    ) -> None:
        self.path = Path(path) if path else None
        self.persist = persist and self.path is not None
        self.days = max(1, int(days))
        self.min_count = max(2, int(min_count))
        self.patterns: dict[tuple[str, str], Pattern] = {}
        self._dirty = False
        self._last_save = 0.0
        self._seen: set[tuple[float, str]] = set()

    # ----- persistence --------------------------------------------------------------

    def load(self) -> None:
        """Read patterns.json and drop everything older than ``days``."""
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read patterns %s: %s", self.path, exc)
            return
        for raw in data.get("patterns", []):
            try:
                p = Pattern.from_dict(raw)
            except (KeyError, TypeError, ValueError):
                continue
            self.patterns[(p.key, p.kind)] = p
        self.prune(time.time())

    def save(self, force: bool = False) -> bool:
        """Write patterns.json; throttled unless ``force``.  True when it was written."""
        if not self.persist or not self.path:
            return False
        now = time.time()
        if not force and (not self._dirty or now - self._last_save < STATE_SAVE_INTERVAL):
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "saved_at": now,
                "days": self.days,
                "patterns": [p.to_dict() for p in self.patterns.values()],
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("Could not save patterns to %s: %s", self.path, exc)
            return False
        self._last_save = now
        self._dirty = False
        return True

    def clear(self) -> None:
        """Forget everything remembered so far and write the empty file."""
        self.patterns.clear()
        self._dirty = True
        self.save(force=True)

    # ----- learning -------------------------------------------------------------------

    def prune(self, now: float) -> None:
        """Drop the day tallies that fell out of the ``days`` window."""
        cutoff = now - self.days * SECONDS_PER_DAY
        for key in list(self.patterns):
            p = self.patterns[key]
            p.days = {d: n for d, n in p.days.items() if _day_ts(d) >= cutoff}
            if not p.days:
                del self.patterns[key]
                self._dirty = True

    def ingest(
        self, events: list[Event], *, listeners: list[Listener], now: float | None = None
    ) -> None:
        """Count new events; remember which services were involved."""
        now = now or time.time()
        by_key = {x.key: x for x in listeners}
        for ev in events:
            kind = classify(ev)
            if kind is None or not ev.key:
                continue
            stamp = (round(ev.timestamp, 3), ev.message)
            if stamp in self._seen:
                continue
            self._seen.add(stamp)
            p = self.patterns.get((ev.key, kind))
            if p is None:
                p = Pattern(key=ev.key, kind=kind, first=ev.timestamp)
                self.patterns[(ev.key, kind)] = p
            p.last = max(p.last, ev.timestamp)
            p.first = min(p.first or ev.timestamp, ev.timestamp)
            day = _day(ev.timestamp)
            p.days[day] = p.days.get(day, 0) + 1
            for name in _participants(ev, by_key.get(ev.key)):
                if name not in p.services:
                    p.services.append(name)
            p.services = p.services[-PATTERN_SERVICES_KEPT:]
            p.samples = [*p.samples, ev.message][-PATTERN_SAMPLES_KEPT:]
            self._dirty = True
        if len(self._seen) > PATTERN_SEEN_LIMIT:
            self._seen = set(sorted(self._seen)[-PATTERN_SEEN_KEEP:])
        self.prune(now)

    # ----- reading --------------------------------------------------------------------

    def recurring(self, min_count: int | None = None) -> list[Pattern]:
        """Patterns seen at least ``min_count`` times, most frequent first."""
        floor = self.min_count if min_count is None else min_count
        items = [p for p in self.patterns.values() if p.count >= floor]
        return sorted(items, key=lambda p: (-p.count, p.key, p.kind))

    def for_key(self, key: str) -> list[Pattern]:
        """Every pattern remembered for one ``port/PROTO``, most frequent first."""
        return sorted(
            (p for (k, _), p in self.patterns.items() if k == key), key=lambda p: -p.count
        )

    def describe(self, p: Pattern, now: float | None = None) -> str:
        """One line naming the port, the services, how often and when it last happened."""
        now = now or time.time()
        who = f" ({', '.join(p.services[:PATTERN_SERVICES_SHOWN])})" if p.services else ""
        last = time.strftime("%m-%d %H:%M", time.localtime(p.last))
        per_day = f" on {p.active_days} day(s)" if p.active_days > 1 else ""
        return f"{p.key}{who}: {p.label} {p.count}× in {self.days} days{per_day}, last {last}"

    def summarise(self, now: float | None = None) -> list[tuple[str, str, str | None]]:
        """``(level, message, suggestion)`` for every recurring pattern (Explain, Events)."""
        out: list[tuple[str, str, str | None]] = []
        for p in self.recurring():
            suggestion: str | None
            if p.kind == EventKind.CONFLICT:
                suggestion = (
                    "the same things keep grabbing this port; pin one of them to another port"
                )
            elif p.kind in (EventKind.RESTARTED, EventKind.REPLACED, EventKind.FAILING):
                suggestion = "keeps breaking; check its logs and what restarts it"
            elif p.kind in (EventKind.KILLED, EventKind.USER_RESTART):
                suggestion = "you keep doing this by hand; a stack action (g) may save time"
            elif p.kind in (EventKind.STOPPED, EventKind.BACK):
                suggestion = "the port flaps; a watcher or a reload loop may be behind it"
            else:
                suggestion = None
            level = (
                Level.ERROR
                if p.kind in (EventKind.CONFLICT, EventKind.FAILING)
                and p.count >= PERSISTENT_PATTERN_FACTOR * self.min_count
                else (Level.WARNING if p.kind != EventKind.BACK else Level.INFO)
            )
            out.append((level, "pattern: " + self.describe(p, now), suggestion))
        return out


_DAY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _day_ts(day: str) -> float:
    m = _DAY_RE.match(day)
    if not m:
        return 0.0
    try:
        return time.mktime((int(m.group(1)), int(m.group(2)), int(m.group(3)), 0, 0, 0, 0, 0, -1))
    except (OverflowError, ValueError):
        return 0.0


def _participants(ev: Event, lst: Listener | None) -> list[str]:
    names: list[str] = []
    if lst is not None:
        if lst.identity.service and lst.identity.service != "Unknown":
            names.append(lst.identity.service)
        for p in lst.processes:
            if p.name and p.name not in names:
                names.append(p.name)
        if lst.container and lst.container.name not in names:
            names.append(lst.container.name)
    else:
        # "[~] Redis on 6379/TCP restarted ..." -> the service name before " on "
        m = re.match(r"^\[.\]\s+(.+?) on \d+/(?:TCP|UDP)", ev.message)
        if m:
            names.append(m.group(1))
    return names[:PATTERN_SERVICES_KEPT]
