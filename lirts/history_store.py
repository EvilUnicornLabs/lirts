"""The rolling history engine: folds refreshes into entries and emits events."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Any

from lirts.constants import (
    EVENTS_RELOADED,
    EVENTS_SAVED,
    HISTORY_WINDOW,
    MAX_EVENTS,
    RESTART_MATCH_TOLERANCE,
    RESTART_WINDOW_SECONDS,
    SECONDS_PER_HOUR,
    STATE_SAVE_INTERVAL,
)
from lirts.history_changes import pid_change_event, status_change_event
from lirts.history_entry import SAMPLE_FIELDS, HistoryEntry
from lirts.models import (
    Activity,
    Event,
    Level,
    Listener,
)
from lirts.ports import holder_label, listener_holder

log = logging.getLogger(__name__)


class HistoryStore:
    """Folds every refresh into per-port entries and emits the events that follow."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        persist: bool = False,
        window: int = HISTORY_WINDOW,
        retention_hours: float = 24.0,
    ) -> None:
        self.path = Path(path) if path else None
        self.persist = persist and self.path is not None
        self.window = window
        self.retention = retention_hours * SECONDS_PER_HOUR
        self.entries: dict[str, HistoryEntry] = {}
        self.events: deque[Event] = deque(maxlen=MAX_EVENTS)
        self._last_keys: set[str] | None = None
        self._last_save = 0.0
        self._dirty = False
        if self.persist:
            self.load()

    # ----- persistence -------------------------------------------------------------

    def load(self) -> None:
        """Read history.json, dropping entries and events older than the retention window."""
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read history %s: %s", self.path, exc)
            return
        now = time.time()
        for raw in data.get("entries", []):
            try:
                entry = HistoryEntry.from_dict(raw, self.window)
            except (KeyError, TypeError, ValueError):
                continue
            if self.retention and now - entry.last_seen > self.retention:
                continue
            self.entries[entry.key] = entry
        # Loaded entries came from a previous run; they are not "currently seen".
        self._last_keys = None
        for raw in data.get("events", [])[-EVENTS_RELOADED:]:
            try:
                if self.retention and now - float(raw["timestamp"]) > self.retention:
                    continue
                self.events.append(
                    Event(
                        float(raw["timestamp"]),
                        raw.get("level", Level.INFO),
                        str(raw.get("message", "")),
                        raw.get("port"),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        self._drop_false_restarts(data.get("events", []))

    _RESTART_PIDS = re.compile(r" restarted \(PID \[([\d, ]*)\] → \[([\d, ]*)\]\)")

    def _drop_false_restarts(self, raw_events: list[dict[str, Any]]) -> None:
        """Older versions counted a helper PID joining or leaving as a restart; undo that.

        A recorded "restarted (PID [a] → [a, b])" where a PID survived was not a restart.
        Its timestamp is removed from the entry's restart counter.
        """
        for raw in raw_events:
            message = str(raw.get("message", ""))
            found = self._RESTART_PIDS.search(message)
            if not found:
                continue
            old = {int(p) for p in found.group(1).split(",") if p.strip()}
            new = {int(p) for p in found.group(2).split(",") if p.strip()}
            if not (old & new):
                continue
            ev = Event(float(raw.get("timestamp", 0)), Level.INFO, message, raw.get("port"))
            entry = self.entries.get(ev.key or "")
            if entry is None:
                continue
            before = len(entry.pid_changes)
            entry.pid_changes = [
                t for t in entry.pid_changes if abs(t - ev.timestamp) > RESTART_MATCH_TOLERANCE
            ]
            if len(entry.pid_changes) != before:
                self._dirty = True

    def save(self, force: bool = False) -> bool:
        """Write history.json; throttled unless ``force``.  True when it was written."""
        if not self.persist or not self.path:
            return False
        now = time.time()
        if not force and (not self._dirty or now - self._last_save < STATE_SAVE_INTERVAL):
            return False
        payload = {
            "version": 1,
            "saved_at": now,
            "entries": [e.to_dict() for e in self.entries.values()],
            "events": [
                {"timestamp": e.timestamp, "level": e.level, "message": e.message, "port": e.port}
                for e in list(self.events)[-EVENTS_SAVED:]
            ],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("Could not save history to %s: %s", self.path, exc)
            return False
        self._last_save = now
        self._dirty = False
        return True

    # ----- updating ----------------------------------------------------------------

    def note(self, message: str, *, port: int | None = None, level: str = Level.INFO) -> Event:
        """Record an action the user took in lirts (kill, restart) so the log explains what followed."""
        self._dirty = True
        return self._emit(level, message=message, port=port, now=time.time())

    def _emit(self, level: str, *, message: str, port: int | None, now: float) -> Event:
        return self._record(Event(now, level, message, port))

    def _record(self, event: Event) -> Event:
        self.events.append(event)
        return event

    def update(
        self,
        listeners: list[Listener],
        *,
        now: float | None = None,
        protocols: set[str] | None = None,
    ) -> list[Event]:
        """Fold a new set of listeners into the history and return new events.

        ``protocols`` names the protocols collected this cycle; keys of other
        protocols are neither reported as stopped nor forgotten.
        """
        now = now or time.time()
        protocols = protocols or {"TCP", "UDP"}
        new_events: list[Event] = []
        current_keys: set[str] = set()
        for lst in listeners:
            current_keys.add(lst.key)
            entry = self.entries.get(lst.key)
            pids = sorted(lst.pids)
            names = sorted({p.name for p in lst.processes})
            if entry is None:
                entry = HistoryEntry(
                    key=lst.key,
                    port=lst.port,
                    protocol=lst.protocol,
                    first_seen=now,
                    last_seen=now,
                    pids=pids,
                    names=names,
                    service=lst.identity.service,
                    uptime_start=now,
                )
                for name in SAMPLE_FIELDS:
                    setattr(entry, name, deque(maxlen=self.window))
                self.entries[lst.key] = entry
                if self._last_keys is not None:
                    new_events.append(
                        self._emit(
                            Level.INFO,
                            message=f"[+] {lst.identity.service} started on {lst.key}",
                            port=lst.port,
                            now=now,
                        )
                    )
            else:
                if self._last_keys is not None:
                    change = pid_change_event(
                        lst,
                        entry=entry,
                        now=now,
                        retention=self.retention,
                        was_absent=lst.key not in self._last_keys,
                    )
                    if change is not None:
                        new_events.append(self._record(change))
                entry.pids = pids
                entry.names = names
                entry.service = lst.identity.service or entry.service
                entry.last_seen = now
            entry.note_service(holder_label(*listener_holder(lst)))
            entry.cpu.append(round(lst.cpu_percent, 1))
            entry.conns.append(lst.connections)
            entry.bytes_in.append(round(lst.bytes_in_rate or 0.0))
            entry.bytes_out.append(round(lst.bytes_out_rate or 0.0))
            entry.activity.append(lst.activity)
            if lst.http.ok and lst.http.latency_ms is not None:
                entry.latency.append(round(lst.http.latency_ms, 1))
            if lst.activity != Activity.IDLE:
                entry.last_active = now
            elif entry.last_active is None:
                entry.last_active = None
            conflict = any(i.code == "port-conflict" for i in lst.insights)
            if conflict and not entry.conflict:
                new_events.append(
                    self._emit(
                        Level.ERROR,
                        message=f"[!] port conflict on {lst.key}",
                        port=lst.port,
                        now=now,
                    )
                )
            entry.conflict = conflict
            status = lst.status
            if self._last_keys is not None:
                changed = status_change_event(
                    lst, entry=entry, status=status, conflict=conflict, now=now
                )
                if changed is not None:
                    new_events.append(self._record(changed))
            entry.health = status
            for ins in lst.insights:
                is_new_error = ins.level == Level.ERROR and ins.code != "port-conflict"
                if is_new_error and (not entry.errors or entry.errors[-1][1] != ins.message):
                    entry.errors.append((now, ins.message))
            # Feed the listener so the UI does not need to consult the store.
            lst.cpu_history = list(entry.cpu)
            lst.conn_history = list(entry.conns)
            lst.latency_history = list(entry.latency)
            lst.activity_history = list(entry.activity)
            lst.bytes_in_history = list(entry.bytes_in)
            lst.bytes_out_history = list(entry.bytes_out)
            lst.first_seen = entry.first_seen
            lst.restarts_last_hour = entry.restarts_within(RESTART_WINDOW_SECONDS, now)
            lst.last_active = entry.last_active
        if self._last_keys is not None:
            for key in sorted(self._last_keys - current_keys):
                if key.rsplit("/", 1)[-1] not in protocols:
                    current_keys.add(key)  # not collected this cycle; still considered seen
                    continue
                entry = self.entries.get(key)
                label = entry.service if entry and entry.service else key
                new_events.append(
                    self._emit(
                        Level.WARNING,
                        message=f"[-] {label} stopped on {key}",
                        port=entry.port if entry else None,
                        now=now,
                    )
                )
        self._last_keys = current_keys
        self._dirty = True
        # Prune very old entries.
        if self.retention:
            for key in list(self.entries):
                if key not in current_keys and now - self.entries[key].last_seen > self.retention:
                    del self.entries[key]
        return new_events

    def recent_events(self, limit: int = 10) -> list[Event]:
        """The newest ``limit`` events, oldest first."""
        return list(self.events)[-limit:]

    def all_events(self) -> list[Event]:
        """Every event still in memory, oldest first."""
        return list(self.events)

    def clear_events(self) -> None:
        """Forget the event log (the per-port entries are kept)."""
        self.events.clear()
        self._dirty = True

    # ----- snapshots ---------------------------------------------------------------

    @staticmethod
    def snapshot_payload(listeners: list[Listener]) -> dict[str, Any]:
        """What identified each port, for the "changes since last session" comparison."""
        return {
            "saved_at": time.time(),
            "services": {
                lst.key: {
                    "service": lst.identity.service,
                    "role": lst.identity.role,
                    "name": lst.name,
                    "source": lst.source,
                    "container": lst.container.name if lst.container else None,
                    "project": lst.identity.project,
                }
                for lst in listeners
            },
        }

    @staticmethod
    def diff_snapshots(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[str]]:
        """``{"missing": [...], "added": [...], "changed": [...]}`` between two payloads."""
        old_s = old.get("services", {})
        new_s = new.get("services", {})
        missing = [f"{v.get('service', '?')} on {k}" for k, v in old_s.items() if k not in new_s]
        added = [f"{v.get('service', '?')} on {k}" for k, v in new_s.items() if k not in old_s]
        changed = []
        for k, v in new_s.items():
            o = old_s.get(k)
            if not o:
                continue
            if o.get("service") != v.get("service") or o.get("source") != v.get("source"):
                changed.append(
                    f"{k}: {o.get('service')} ({o.get('source')}) → {v.get('service')} ({v.get('source')})"
                )
        return {"missing": sorted(missing), "added": sorted(added), "changed": sorted(changed)}
