"""The filter box, its command mode (a second `/`) and the sort order of the table."""

from __future__ import annotations

from textual.widgets import DataTable, Input

from lirts.constants import TOAST_NORMAL, TOAST_SHORT
from lirts.tui.app_base import LirtsAppBase
from lirts.tui.app_palette import key_hints
from lirts.tui.commandbar import COMMAND_BAR_ID, COMMAND_PREFIX, CommandBar, matching_commands
from lirts.tui.screens.menu import open_menu

# Bound on the filter box itself while command mode is on: the box is first in the binding
# chain, so these win over the Input's own keys and over the screen's tab focus binding.
COMMAND_MODE_KEYS = {
    "down": "app.command_next",
    "up": "app.command_prev",
    "tab": "app.command_complete",
}


class FilterMixin(LirtsAppBase):
    """Filtering the rows, running a command typed after `/`, and cycling the sort column."""

    command_mode: bool = False

    def action_focus_filter(self) -> None:
        """Put the cursor in the filter box (/ or ctrl+f)."""
        self.query_one("#filter", Input).focus()

    def action_escape(self) -> None:
        """Esc clears the filter, then the marks; with nothing left to clear it opens the menu."""
        box = self.query_one("#filter", Input)
        if self.command_mode:
            self.leave_command_mode()
            self._clear_filter_box()
            return
        if box.has_focus or box.value:
            self._clear_filter_box()
        elif self.clear_marks():
            self.notify("Marks cleared", timeout=TOAST_SHORT)
        else:
            open_menu(self)
            return
        self.query_one("#table", DataTable).focus()

    def action_clear_filter(self) -> None:
        """Empty the filter box and show every row again."""
        self.leave_command_mode()
        self._clear_filter_box()
        self.query_one("#table", DataTable).focus()

    def set_filter(self, text: str) -> None:
        """Type `text` into the filter box and apply it; a trailing `:` keeps the box focused."""
        self.leave_command_mode()
        box = self.query_one("#filter", Input)
        box.value = text
        self.filter_text = text.strip().lower()
        self.update_table()
        if text.endswith(":"):
            box.focus()  # a palette-chosen field: let the user type the value
        else:
            self.query_one("#table", DataTable).focus()

    # ----- command mode ------------------------------------------------------------

    async def on_input_changed(self, event: Input.Changed) -> None:
        """A lone `/` in the filter box starts command mode; deleting that `/` ends it."""
        if event.input.id != "filter":
            return
        if not self.command_mode:
            if event.value == COMMAND_PREFIX:
                await self.enter_command_mode()
            return
        if not event.value.startswith(COMMAND_PREFIX):
            self.leave_command_mode()
            return
        self._drop_command_text_from_filter()
        self._refresh_command_bar(event.value[len(COMMAND_PREFIX) :])

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter runs the highlighted command and hands the focus back to the table."""
        if event.input.id != "filter" or not self.command_mode:
            return
        entry = self.command_bar().highlighted_entry
        self.leave_command_mode()
        self._clear_filter_box()
        self.query_one("#table", DataTable).focus()
        if entry is not None:
            await self.run_action(entry.action)

    async def enter_command_mode(self) -> None:
        """Turn the filter box into a command line and open the dropdown under it."""
        self.command_mode = True
        box = self.query_one("#filter", Input)
        box.add_class("command")
        for key, action in COMMAND_MODE_KEYS.items():
            box._bindings.bind(key, action, show=False)
        if not self.query(CommandBar):
            await self.query_one("#left").mount(CommandBar(), after=box)
        self._drop_command_text_from_filter()
        self._refresh_command_bar("")

    def leave_command_mode(self) -> None:
        """Back to the plain filter: the dropdown closes and the extra keys go away."""
        if not self.command_mode:
            return
        self.command_mode = False
        box = self.query_one("#filter", Input)
        box.remove_class("command")
        for key in COMMAND_MODE_KEYS:
            box._bindings.key_to_bindings.pop(key, None)
        self.command_bar().display = False

    def command_bar(self) -> CommandBar:
        """The dropdown; it exists from the first time command mode was entered."""
        return self.query_one(f"#{COMMAND_BAR_ID}", CommandBar)

    def action_command_next(self) -> None:
        """Highlight the next command in the dropdown (↓)."""
        if self.command_mode:
            self.command_bar().action_cursor_down()

    def action_command_prev(self) -> None:
        """Highlight the previous command in the dropdown (↑)."""
        if self.command_mode:
            self.command_bar().action_cursor_up()

    def action_command_complete(self) -> None:
        """Write the highlighted command's name into the box (Tab)."""
        if not self.command_mode:
            return
        entry = self.command_bar().highlighted_entry
        if entry is None:
            return
        box = self.query_one("#filter", Input)
        box.value = COMMAND_PREFIX + entry.name
        box.cursor_position = len(box.value)

    def _refresh_command_bar(self, query: str) -> None:
        entries = matching_commands(
            query,
            keys=key_hints(self.BINDINGS),
            allowed=lambda action: self.check_action(action, ()),
        )
        self.command_bar().show_matches(entries)

    def _drop_command_text_from_filter(self) -> None:
        """What is typed after `/` names a command, so it must not filter the rows as well."""
        if self.filter_text:
            self.filter_text = ""
            self.update_table()

    def _clear_filter_box(self) -> None:
        self.query_one("#filter", Input).value = ""
        self.filter_text = ""
        self.update_table()

    # ----- sorting -----------------------------------------------------------------

    def _build_columns(self) -> None:
        """(Re)create the table columns; the sort column carries a direction arrow."""
        table = self.query_one("#table", DataTable)
        table.clear(columns=True)
        table.add_column(" ", key="mark", width=1)
        for col in self.columns:
            label = col.label
            if col.key == self.sort_key:
                label += " ▼" if self.sort_descending() else " ▲"
            table.add_column(label, key=col.key)
        self._row_order = []
        self._cell_cache.clear()

    def sort_descending(self) -> bool:
        """True when the current sort column is showing its largest / newest values first."""
        col = next((c for c in self.columns if c.key == self.sort_key), None)
        return bool(col and (self.sort_reverse != col.reverse_default))

    def _announce_sort(self) -> None:
        direction = "descending, largest / newest first" if self.sort_descending() else "ascending"
        if self.sort_key == "uptime":
            direction = (
                "longest running first" if self.sort_descending() else "most recently started first"
            )
        self.notify(f"Sorted by {self.sort_key} ({direction})", timeout=TOAST_NORMAL)

    def _apply_sort(self) -> None:
        self._build_columns()
        self.update_table()
        self.apply_subtitle()
        self._announce_sort()
        self._remember("sort_by", self.sort_key)
        self._remember("sort_desc", self.sort_reverse)

    def action_sort(self) -> None:
        """Move the sort to the next column (s)."""
        keys = [c.key for c in self.columns]
        idx = keys.index(self.sort_key) if self.sort_key in keys else -1
        self.sort_key = keys[(idx + 1) % len(keys)]
        self.sort_reverse = False
        self._apply_sort()

    def action_sort_reverse(self) -> None:
        """Flip the sort direction of the current column (S)."""
        self.sort_reverse = not self.sort_reverse
        self._apply_sort()

    def apply_subtitle(self) -> None:
        """Redraw the header subtitle from the snapshot already on screen."""
        if self._first_refresh_done:
            self.apply_snapshot(self.snapshot)
