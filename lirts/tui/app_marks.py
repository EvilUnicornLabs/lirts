"""Marking rows (multi-select) and the row under the cursor."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import DataTable

from lirts.constants import GLYPH_OK
from lirts.models import Listener
from lirts.tui.app_base import LirtsAppBase


class MarksMixin(LirtsAppBase):
    """Marks, the mark column and the listener under the cursor."""

    def _mark_cell(self, key: str) -> Text:
        return Text(GLYPH_OK, style="bold green") if key in self._marked else Text("")

    def _refresh_mark(self, key: str) -> None:
        table = self.query_one("#table", DataTable)
        if key in self._row_order:
            table.update_cell(key, "mark", self._mark_cell(key))

    def _update_mark_subtitle(self) -> None:
        if self._first_refresh_done:
            self.apply_snapshot(self.snapshot)

    def action_mark(self) -> None:
        """Mark or unmark the row under the cursor, or collapse a group header (Space)."""
        key = self._selected_key
        if key is None:
            return
        if key.startswith(self.GROUP_PREFIX):
            self.toggle_collapse(key[len(self.GROUP_PREFIX) :])
            return
        if key in self._marked:
            self._marked.discard(key)
        else:
            self._marked.add(key)
        self._refresh_mark(key)
        self._update_mark_subtitle()
        table = self.query_one("#table", DataTable)
        if table.cursor_row < table.row_count - 1:
            table.move_cursor(row=table.cursor_row + 1, animate=False)

    def action_mark_all(self) -> None:
        """Mark every visible row, or clear the marks when they are all marked already (a)."""
        visible = {k for k in self._row_order if not k.startswith(self.GROUP_PREFIX)}
        if visible and visible <= self._marked:
            self._marked -= visible
        else:
            self._marked |= visible
        for key in self._row_order:
            self._refresh_mark(key)
        self._update_mark_subtitle()

    def clear_marks(self) -> bool:
        """Drop every mark; False when there was nothing marked."""
        if not self._marked:
            return False
        keys = list(self._marked)
        self._marked.clear()
        for key in keys:
            self._refresh_mark(key)
        self._update_mark_subtitle()
        return True

    def targets(self) -> list[Listener]:
        """Marked rows if any are marked, otherwise the row under the cursor."""
        if self._marked:
            return [x for x in self.visible_listeners() if x.key in self._marked]
        lst = self.selected()
        return [lst] if lst else []

    def selected(self) -> Listener | None:
        """The listener under the cursor, or None on a group header or an empty table."""
        if self._selected_key is None:
            return None
        return self.snapshot.by_key(self._selected_key)
