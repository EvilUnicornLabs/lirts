"""The checklist modal that picks table columns and other multi-value settings."""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from lirts.constants import GLYPH_OK


class ColumnsScreen(ModalScreen[list[str] | None]):
    """Checklist of table columns; opened from the Settings screen on a list setting.

    Space toggles an entry, Enter accepts; dismisses with the chosen names in order.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("space", "flip", "Toggle"),
        Binding("enter", "accept", "Done"),
    ]

    def __init__(self, selected: list[str], available: list[str], title: str = "Columns") -> None:
        super().__init__()
        self.selected = list(selected)
        self.available = list(available)
        self.title_text = title

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(
                f"{self.title_text} (Space toggles, Enter accepts; order follows the list)",
                classes="dialog-title",
            )
            yield DataTable(id="columns-table", cursor_type="row")
            yield Static(
                "Selected columns appear in the order shown here.", classes="dialog-footer"
            )

    def on_mount(self) -> None:
        table = self.query_one("#columns-table", DataTable)
        table.add_column(" ", key="on", width=1)
        table.add_column("COLUMN", key="name")
        for name in [*self.selected, *[c for c in self.available if c not in self.selected]]:
            table.add_row(
                Text(GLYPH_OK, style="bold green") if name in self.selected else Text(""),
                name,
                key=name,
            )
        table.focus()

    def action_flip(self) -> None:
        """Add or remove the entry under the cursor (Space)."""
        table = self.query_one("#columns-table", DataTable)
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        name = str(row_key.value)
        if name in self.selected:
            self.selected.remove(name)
        else:
            self.selected.append(name)
        table.update_cell(
            name, "on", Text(GLYPH_OK, style="bold green") if name in self.selected else Text("")
        )

    def action_accept(self) -> None:
        """Dismiss with the selection, in the order it is shown (Enter)."""
        self.dismiss([c for c in self.selected if c in self.available] or None)

    def action_cancel(self) -> None:
        """Leave the setting unchanged (Esc)."""
        self.dismiss(None)

    @on(DataTable.RowSelected, "#columns-table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        # Enter reaches the table first; treat it as "done" (Space toggles).
        self.action_accept()
