"""Focus handling for tabbed screens."""

from __future__ import annotations

from typing import Any

from textual.screen import Screen
from textual.widgets import DataTable, TabbedContent


def focus_active_pane(screen: Screen[Any], tabs_id: str) -> None:
    """Focus the DataTable inside the currently active tab pane."""
    tabs = screen.query_one(tabs_id, TabbedContent)
    pane = tabs.get_pane(tabs.active) if tabs.active else None
    if pane is None:
        return
    tables = pane.query(DataTable)
    if tables:
        tables.first().focus()
