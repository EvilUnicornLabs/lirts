"""The single "what was running last session" file the engine writes on exit.

It holds what identified each port at the end of the previous run, so the next
run can say what is missing, new or different.  There is one file, it is
overwritten every time, and there is deliberately no restore.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lirts.history_store import HistoryStore
from lirts.models import Listener


def last_snapshot_path(state_root: Path) -> Path:
    """The file the last session's services are stored in."""
    return state_root / "last.json"


def save_last_snapshot(state_root: Path, listeners: list[Listener]) -> Path:
    """Write what is running now as the session the next run compares against."""
    target = last_snapshot_path(state_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(HistoryStore.snapshot_payload(listeners), indent=1), encoding="utf-8"
    )
    return target


def load_last_snapshot(state_root: Path) -> dict[str, Any] | None:
    """The previous session's services, or ``None`` when there is no readable file."""
    target = last_snapshot_path(state_root)
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
