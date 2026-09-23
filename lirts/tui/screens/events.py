"""Notification centre: the full event log with a legend."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Static

from lirts.constants import GLYPH_OK, GLYPH_STATUS, TOAST_NORMAL
from lirts.engine import Engine
from lirts.insights import event_insights
from lirts.models import Event, EventKind, Level, Snapshot
from lirts.tui.render import format_time, level_marker
from lirts.tui.screens.help_confirm import ConfirmScreen

# Ongoing / recurring findings shown above the log before the list is cut.
MAX_SUMMARY_ITEMS = 8

_LEVEL_STYLES: dict[str, str] = {Level.ERROR: "bold red", Level.WARNING: "yellow"}

EVENT_LEGEND: list[tuple[str, str, str]] = [
    ("[+]", "started", "a port began listening (new service, or one that came back)"),
    ("[-]", "stopped", "a port stopped listening since the previous refresh"),
    ("[~]", "restarted / degraded", "same port, new PID; or a warning-level insight appeared"),
    (
        "[!]",
        "conflict / failing",
        "two unrelated processes share a port, or an error-level insight appeared",
    ),
    (f"[{GLYPH_OK}]", "recovered", "a port that was degraded or failing is healthy again"),
]


class EventsScreen(Screen[int | None]):
    """Notification centre: every recorded event with a legend; opened with `n`.

    Dismisses with the port of the selected event, so the dashboard can filter by it.
    """

    BINDINGS = [
        Binding("escape,q,n", "close", "Close"),
        Binding("enter", "jump", "Jump to port", show=False),
        Binding("c", "clear", "Clear log"),
        Binding("r", "reload", "Reload"),
    ]

    def __init__(self, engine: Engine, unread_since: float) -> None:
        super().__init__()
        self.engine = engine
        self.unread_since = unread_since
        self._events: list[Event] = []

    def compose(self) -> ComposeResult:
        legend = Table.grid(padding=(0, 2))
        legend.add_column(style="bold", width=4)
        legend.add_column(style="bold cyan", width=22)
        legend.add_column()
        for marker, name, meaning in EVENT_LEGEND:
            legend.add_row(marker, name, Text(meaning, style="dim"))
        yield Static("Events", classes="screen-title")
        yield Static(legend, id="events-legend")
        yield Static(id="events-summary")
        yield DataTable(id="events-table", cursor_type="row", zebra_stripes=True)
        yield Static(
            "Esc close · Enter jump to the port · c clear the log · r reload   "
            "(events are kept across runs; older than the history retention are dropped)",
            classes="screen-footer",
        )

    def on_mount(self) -> None:
        table = self.query_one("#events-table", DataTable)
        for label, key in (
            ("TIME", "time"),
            ("", "new"),
            ("LEVEL", "level"),
            ("STATE", "state"),
            ("EVENT", "event"),
        ):
            table.add_column(label, key=key)
        table.focus()
        self.action_reload()

    def action_reload(self) -> None:
        """Read the event log again and redraw the table and the summary (r)."""
        table = self.query_one("#events-table", DataTable)
        table.clear()
        events = list(reversed(self.engine.history.all_events()))
        snapshot = self.engine.snapshot
        if not events:
            self.notify("No events recorded yet", timeout=TOAST_NORMAL)
        for idx, ev in enumerate(events):
            style = _LEVEL_STYLES.get(ev.level, "")
            when = format_time(ev.timestamp, with_date=True)
            new = (
                Text(GLYPH_STATUS, style="bold yellow")
                if ev.timestamp > self.unread_since
                else Text("")
            )
            table.add_row(
                Text(when, style="dim"),
                new,
                Text(ev.level, style=style),
                self._state_cell(ev, snapshot),
                Text(ev.message, style=style),
                key=str(idx),
            )
        self._events = events
        self._render_summary(events, snapshot)

    @staticmethod
    def _state_cell(ev: Event, snapshot: Snapshot) -> Text:
        """ongoing / resolved for conditions, back / still gone for stopped ports."""
        key = ev.key
        row = snapshot.by_key(key) if key else None
        live = row is not None and row.state != "STOPPED"
        if ev.kind in (EventKind.CONFLICT, EventKind.FAILING, EventKind.DEGRADED):
            if live and row is not None and any(i.level != Level.INFO for i in row.insights):
                return Text("ongoing", style="bold red")
            return Text("resolved", style="green")
        if ev.kind == EventKind.STOPPED:
            return Text("back", style="green") if live else Text("still gone", style="yellow")
        if ev.kind in (EventKind.STARTED, EventKind.BACK):
            return Text("running", style="green") if live else Text("gone", style="dim")
        return Text("")

    def _render_summary(self, events: list[Event], snapshot: Snapshot) -> None:
        items = event_insights(list(reversed(events)), snapshot=snapshot)
        block = Table.grid(padding=(0, 1))
        block.add_column(width=2)
        block.add_column(overflow="fold")
        if not items:
            block.add_row("", Text("Nothing ongoing or recurring.", style="green"))
        for level, message, suggestion in items[:MAX_SUMMARY_ITEMS]:
            marker, style = level_marker(level)
            block.add_row(Text(marker, style=style), Text(message, style=style))
            if suggestion:
                block.add_row("", Text(f"→ {suggestion}", style="italic"))
        if len(items) > MAX_SUMMARY_ITEMS:
            extra = len(items) - MAX_SUMMARY_ITEMS
            block.add_row("", Text(f"… and {extra} more", style="dim"))
        self.query_one("#events-summary", Static).update(block)

    def action_close(self) -> None:
        """Leave the screen without filtering the dashboard (Esc / q / n)."""
        self.dismiss(None)

    def action_jump(self) -> None:
        """Close the screen and filter the dashboard by the selected event's port (Enter)."""
        table = self.query_one("#events-table", DataTable)
        if table.row_count == 0:
            return
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        ev = self._events[int(str(row_key.value))]
        self.dismiss(ev.port)

    @on(DataTable.RowSelected, "#events-table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_jump()

    def action_clear(self) -> None:
        """Forget every recorded event, after a confirmation (c)."""

        def done(ok: bool | None) -> None:
            if ok:
                self.engine.history.clear_events()
                self.action_reload()

        self.app.push_screen(
            ConfirmScreen("Clear the event log?", Text("All recorded events will be forgotten.")),
            done,
        )
