"""The history panel: the rolling sparklines of the selected row, under the table (``Y``).

The same series the details screen's History tab draws, in three lines: CPU, connections and
latency on the left, traffic and activity on the right, each with its current value.
"""

from __future__ import annotations

from typing import Any

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text
from textual.app import App
from textual.widget import Widget

from lirts.insights import format_rate
from lirts.models import Activity, Listener
from lirts.tui.render import sparkline

# Samples drawn per series; the panel is three lines high and sits beside the traffic panel.
SPARK_WIDTH_HISTORY = 24
# Activity levels as numbers so the shared sparkline can draw them; the top level fixes the scale.
ACTIVITY_LEVELS: dict[str, float] = {Activity.HOT: 2.0, Activity.ACTIVE: 1.0, Activity.IDLE: 0.0}
ACTIVITY_TOP = 2.0

_ACTIVITY_STYLES: dict[str, str] = {Activity.HOT: "bold red", Activity.ACTIVE: "green"}


def _series(history: list[Any], *, current: str, style: str, maximum: float | None = None) -> Text:
    """One sparkline with the current value behind it, or a dash when nothing was sampled."""
    line = Text(sparkline(history, width=SPARK_WIDTH_HISTORY, maximum=maximum) or "-", style=style)
    line.append(f"  {current}", style="dim")
    return line


def _activity_series(listener: Listener) -> Text:
    """The activity history as a strip, with the level of the last refresh spelled out."""
    levels = [ACTIVITY_LEVELS.get(level, 0.0) for level in listener.activity_history]
    line = Text(
        sparkline(levels, width=SPARK_WIDTH_HISTORY, maximum=ACTIVITY_TOP) or "-", style="green"
    )
    line.append(f"  {listener.activity}", style=_ACTIVITY_STYLES.get(listener.activity, "dim"))
    return line


def history_lines(listener: Listener) -> Table:
    """CPU, connections and latency next to traffic and activity, as one grid."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(width=8, style="bold cyan")
    grid.add_column(width=SPARK_WIDTH_HISTORY + 12)
    grid.add_column(width=8, style="bold cyan")
    grid.add_column(overflow="fold")
    latency = listener.http.latency_ms
    grid.add_row(
        "CPU",
        _series(listener.cpu_history, current=f"{listener.cpu_percent:.1f}%", style="yellow"),
        "↓",
        _series(
            listener.bytes_in_history,
            current=format_rate(listener.bytes_in_rate or 0.0),
            style="cyan",
        ),
    )
    grid.add_row(
        "Conns",
        _series(listener.conn_history, current=str(listener.connections), style="blue"),
        "↑",
        _series(
            listener.bytes_out_history,
            current=format_rate(listener.bytes_out_rate or 0.0),
            style="magenta",
        ),
    )
    grid.add_row(
        "Latency",
        _series(
            listener.latency_history,
            current=f"{latency:.0f} ms" if latency is not None else "-",
            style="magenta",
        ),
        "Activity",
        _activity_series(listener),
    )
    return grid


class HistoryPanel(Widget):
    """The selected row's sparklines under the table; ``Y`` shows and hides it."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.listener: Listener | None = None
        self.border_title = "history"

    def show(self, listener: Listener | None) -> None:
        """Draw the history of this row, or the hint when no row is selected."""
        self.listener = listener
        self.border_title = (
            f"history · {listener.identity.service} :{listener.port}  (Y: hide)"
            if listener is not None
            else "history  (Y: hide)"
        )
        self.refresh()

    def render(self) -> RenderableType:
        if self.listener is None:
            return Text("Select a row to see its history", style="dim")
        samples = len(self.listener.cpu_history)
        note = Text(f"{samples} samples, one per refresh · i opens the History tab", style="dim")
        return Group(history_lines(self.listener), note)


def feed_history_panel(app: App[None], listener: Listener | None) -> None:
    """Show this row in the history panel, when the app has built one (``Y``)."""
    for panel in app.query("#history").results(HistoryPanel):
        panel.show(listener)
