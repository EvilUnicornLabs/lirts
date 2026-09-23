"""Rolling history engine with optional persistence.

For every ``port/PROTO`` key we keep first/last seen timestamps, the set of
PIDs (to detect restarts), and rolling windows of CPU, connection count,
latency and activity samples.  :meth:`HistoryStore.update` also emits
:class:`Event` objects (started / stopped / restarted / conflict) which feed
the event stream in the UI.

``save_last_snapshot`` / ``load_last_snapshot`` keep the single file the engine
writes on exit, so the next run can say what changed since the last session;
there is deliberately no restore.

The implementation lives in :mod:`lirts.history_entry`, :mod:`lirts.history_store`
and :mod:`lirts.history_snapshots`; this module re-exports it so ``lirts.history``
stays the single import path.
"""

from __future__ import annotations

from lirts.history_entry import HistoryEntry
from lirts.history_snapshots import (
    last_snapshot_path,
    load_last_snapshot,
    save_last_snapshot,
)
from lirts.history_store import HistoryStore

__all__ = [
    "HistoryEntry",
    "HistoryStore",
    "last_snapshot_path",
    "load_last_snapshot",
    "save_last_snapshot",
]
