"""The learned star map: what lirts has seen on this machine over the last days.

Every refresh's graph is merged into a store kept in ``topology.json`` next to the other
state files: a star or a line seen again keeps its id and first-seen time and gains a day;
one seen on ``usual_days`` different days is a "usual" relation and stays on the map (dim)
while idle; anything not seen for ``days`` is forgotten.  Only what lirts observed while
running is learned; the store never reads anything else.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from lirts.constants import STATE_SAVE_INTERVAL
from lirts.topology import Graph, NodeKind

log = logging.getLogger(__name__)

STORE_VERSION = 1
# Days kept per star or line, enough to tell "usual" from "once" for any usual_days setting.
DAYS_KEPT = 60


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


class TopologyStore:
    """Merges the graph of every refresh into the learned graph and persists it."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        persist: bool = False,
        days: int = 14,
        usual_days: int = 3,
    ) -> None:
        self.path = Path(path) if path else None
        self.persist = persist and self.path is not None
        self.days = max(1, int(days))
        self.usual_days = max(1, int(usual_days))
        self.graph = Graph()
        self._dirty = False
        self._last_save = 0.0

    # ----- persistence --------------------------------------------------------------------

    def load(self) -> None:
        """Read topology.json (when persisting) and forget what is older than ``days``."""
        if not self.persist or not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.graph = Graph.from_dict(data.get("graph", {}))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            log.warning("Could not read the learned topology %s: %s", self.path, exc)
            self.graph = Graph()
            return
        for node in self.graph.nodes.values():
            node.live = False
        for relation in self.graph.relations.values():
            relation.live = False
        self.prune(time.time())

    def save(self, force: bool = False) -> bool:
        """Write topology.json; throttled unless ``force``.  True when it was written."""
        if not self.persist or not self.path:
            return False
        now = time.time()
        if not force and (not self._dirty or now - self._last_save < STATE_SAVE_INTERVAL):
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {
                "version": STORE_VERSION,
                "saved_at": now,
                "days": self.days,
                "graph": self.graph.to_dict(),
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("Could not save the learned topology to %s: %s", self.path, exc)
            return False
        self._last_save = now
        self._dirty = False
        return True

    def clear(self) -> bool:
        """Forget everything learned and delete the file.  True when there was a file."""
        self.graph = Graph()
        self._dirty = False
        if self.path and self.path.exists():
            self.path.unlink()
            return True
        return False

    # ----- learning -----------------------------------------------------------------------

    def observe(self, seen: Graph, *, now: float | None = None) -> Graph:
        """Merge one refresh's graph in: everything in it is live now, the rest goes idle."""
        now = now or seen.built_at or time.time()
        today = _day(now)
        for node in self.graph.nodes.values():
            node.live = False
        for relation in self.graph.relations.values():
            relation.live = False
        for node in seen.nodes.values():
            known = self.graph.nodes.get(node.id)
            if known is None:
                known = replace(node)
                known.first_seen = node.first_seen or now
                self.graph.nodes[node.id] = known
            else:
                known.label = node.label
                known.kind = node.kind
                known.project = node.project
                known.ports = list(node.ports)
                known.source = node.source
                known.row_key = node.row_key
                known.parent = node.parent
                known.aliases = list(node.aliases)
            known.status = node.status
            known.live = node.live
            known.last_seen = now
            _mark_day(known.days_seen, today)
            known.usual = len(known.days_seen) >= self.usual_days
        for relation in seen.relations.values():
            known_relation = self.graph.relations.get(relation.key)
            if known_relation is None:
                known_relation = replace(relation)
                known_relation.first_seen = relation.first_seen or now
                self.graph.relations[relation.key] = known_relation
            else:
                known_relation.count = relation.count
                known_relation.label = relation.label
                known_relation.rate_in = relation.rate_in
                known_relation.rate_out = relation.rate_out
            known_relation.live = True
            known_relation.last_seen = now
            _mark_day(known_relation.days_seen, today)
            known_relation.usual = len(known_relation.days_seen) >= self.usual_days
        self.graph.built_at = now
        self._dirty = True
        self.prune(now)
        return self.graph

    def prune(self, now: float) -> None:
        """Drop stars and lines not seen for ``days``, and lines whose ends are gone."""
        limit = now - self.days * 86400
        for node_id in [n.id for n in self.graph.nodes.values() if n.last_seen < limit]:
            del self.graph.nodes[node_id]
            self._dirty = True
        for key in [
            r.key
            for r in self.graph.relations.values()
            if r.last_seen < limit or r.src not in self.graph.nodes or r.dst not in self.graph.nodes
        ]:
            del self.graph.relations[key]
            self._dirty = True

    def graph_for(self, *, hide_system: bool = False) -> Graph:
        """The learned graph as the map shows it (system daemons dropped when the table hides them)."""
        if not hide_system:
            return self.graph
        view = Graph(built_at=self.graph.built_at)
        for node in self.graph.nodes.values():
            if node.kind != NodeKind.SYSTEM:
                view.nodes[node.id] = node
        for relation in self.graph.relations.values():
            if relation.src in view.nodes and relation.dst in view.nodes:
                view.relations[relation.key] = relation
        return view


def _mark_day(days: list[str], today: str) -> None:
    if today not in days:
        days.append(today)
        days.sort()
        del days[:-DAYS_KEPT]
