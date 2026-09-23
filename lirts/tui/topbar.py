"""The top bar: title, system gauges, counts, the latest event, the clock and the scroller."""

from __future__ import annotations

import math
import time
from typing import Any

from rich.table import Table
from rich.text import Text
from textual.timer import Timer
from textual.widgets import Static

from lirts.constants import SPARK_WIDTH_TOPBAR
from lirts.insights import format_rate
from lirts.models import Level, Snapshot
from lirts.tui.render import bar, format_time, set_clock_format, sparkline, threshold_style
from lirts.tui.styles import Density

# The machine's own CPU and memory gauges are fixed; the config thresholds are per service.
SYSTEM_CPU_LIMITS = (50.0, 85.0)
SYSTEM_MEM_LIMITS = (70.0, 90.0)

# Width of the bar next to the top-bar sparklines.
TOPBAR_BAR_WIDTH = 12
# Newest events shown in the top bar; the rest wait in the notification centre.
TOPBAR_EVENTS = 2
# Cells the scroller shows at once, and how many of them one wave of the sine takes.
SCROLLER_WIDTH = 26
SCROLLER_WAVELENGTH = 3.5
# Seconds between two steps of the scroller; nothing in the bar ticks faster.
SCROLLER_INTERVAL = 1.0

_EVENT_STYLES: dict[str, str] = {Level.ERROR: "red", Level.WARNING: "yellow"}
# Colours the sine walks through, dark to bright and back.
_SCROLLER_STYLES = ("magenta", "bold magenta", "bold cyan", "cyan")


def sine_scroller(text: str, *, width: int, offset: int) -> Text:
    """`width` cells of `text` starting at `offset`, coloured along a sine wave.

    The text wraps around, so the offset is simply how many cells it has travelled.
    """
    if not text:
        return Text("")
    window = Text(no_wrap=True, overflow="crop")
    for cell in range(width):
        position = offset + cell
        wave = math.sin(position / SCROLLER_WAVELENGTH)
        step = round((wave + 1) / 2 * (len(_SCROLLER_STYLES) - 1))
        window.append(text[position % len(text)], style=_SCROLLER_STYLES[step])
    return window


class TopBar(Static):
    """CPU / memory / network graphs, service summary, the event stream and the clock."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._snapshot = Snapshot()
        self._collecting = False
        self._unread = 0
        self._density: str = Density.NORMAL
        self._scroller = ""
        self._scroller_offset = 0
        self._scroller_timer: Timer | None = None

    # ----- what the app sets -------------------------------------------------------

    def set_clock_format(self, fmt: str) -> None:
        """Set the strftime format of the clock and of the event times (``ui.clock``)."""
        set_clock_format(fmt)
        self._redraw()

    def set_density(self, density: str) -> None:
        """``dense`` draws the whole bar on one line; ``tight`` drops the load row."""
        self._density = density
        self._redraw()

    def set_scroller(self, text: str) -> None:
        """Run a sine scroller on the title line, or stop it with an empty text."""
        if text == self._scroller:
            return
        self._scroller = text
        self._scroller_offset = 0
        if text and self.is_mounted:
            self._start_scroller()
        elif not text and self._scroller_timer is not None:
            self._scroller_timer.stop()
            self._scroller_timer = None
        self._redraw()

    def advance_scroller(self) -> None:
        """Move the scroller on by one cell; the one-second timer calls this."""
        if not self._scroller:
            return
        self._scroller_offset = (self._scroller_offset + 1) % len(self._scroller)
        self._redraw()

    def on_mount(self) -> None:
        if self._scroller:
            self._start_scroller()

    def _start_scroller(self) -> None:
        """One timer, one step per second; nothing else in the bar is on a clock of its own."""
        if self._scroller_timer is None:
            self._scroller_timer = self.set_interval(SCROLLER_INTERVAL, self.advance_scroller)

    def update_snapshot(
        self,
        snapshot: Snapshot,
        *,
        collecting: bool = False,
        unread: int = 0,
    ) -> None:
        """Redraw the bar for a new snapshot; `unread` is the event count behind the `n` key."""
        self._snapshot = snapshot
        self._collecting = collecting
        self._unread = unread
        self._redraw()

    # ----- drawing -----------------------------------------------------------------

    def _redraw(self) -> None:
        """Paint the bar again from what it was last told, in the current density."""
        if self._density == Density.DENSE:
            self.update(self._one_line())
            return
        outer = Table.grid(padding=(0, 3), expand=True)
        outer.add_column(width=56)
        outer.add_column(ratio=1)
        outer.add_row(self._system_gauges(), self._summary_block())
        self.update(outer)

    def _clock(self) -> Text:
        return Text(format_time(time.time()), style="bold")

    def _title_line(self, summary: Text) -> Table:
        """The first line: the counts, the scroller when one runs, and the clock on the right."""
        line = Table.grid(padding=(0, 1), expand=True)
        line.add_column(ratio=1, overflow="ellipsis", no_wrap=True)
        cells = [summary]
        if self._scroller:
            line.add_column(width=SCROLLER_WIDTH)
            cells.append(
                sine_scroller(self._scroller, width=SCROLLER_WIDTH, offset=self._scroller_offset)
            )
        line.add_column(justify="right")
        cells.append(self._clock())
        line.add_row(*cells)
        return line

    def _system_gauges(self) -> Table:
        """The CPU / memory / network sparklines and bars on the left."""
        sysinfo = self._snapshot.system
        cpu_style = threshold_style(sysinfo.cpu_percent, SYSTEM_CPU_LIMITS)
        mem_style = threshold_style(sysinfo.mem_percent, SYSTEM_MEM_LIMITS)
        left = Table.grid(padding=(0, 1))
        left.add_column(width=4)
        left.add_column(width=14)
        left.add_column(width=12)
        left.add_column()
        left.add_row(
            Text("CPU", style="bold"),
            Text(
                sparkline(sysinfo.cpu_history, width=SPARK_WIDTH_TOPBAR, maximum=100),
                style=cpu_style,
            ),
            Text(bar(sysinfo.cpu_percent, TOPBAR_BAR_WIDTH), style=cpu_style),
            Text(f"{sysinfo.cpu_percent:4.0f}%", style=cpu_style),
        )
        left.add_row(
            Text("MEM", style="bold"),
            Text(
                sparkline(sysinfo.mem_history, width=SPARK_WIDTH_TOPBAR, maximum=100),
                style=mem_style,
            ),
            Text(bar(sysinfo.mem_percent, TOPBAR_BAR_WIDTH), style=mem_style),
            Text(
                f"{sysinfo.mem_percent:4.0f}%  {sysinfo.mem_used_gb:.1f}/{sysinfo.mem_total_gb:.0f} GB",
                style=mem_style,
            ),
        )
        left.add_row(
            Text("NET", style="bold"),
            Text(sparkline(self._net_history(), width=SPARK_WIDTH_TOPBAR), style="cyan"),
            "",
            self._net_text(),
        )
        left.add_row(Text(""), "", "", Text(self._load_text(), style="dim"))
        return left

    def _net_history(self) -> list[float]:
        sysinfo = self._snapshot.system
        return [
            up + down
            for up, down in zip(sysinfo.net_up_history, sysinfo.net_down_history, strict=False)
        ]

    def _net_text(self) -> Text:
        sysinfo = self._snapshot.system
        net = Text()
        net.append(f"↓{format_rate(sysinfo.net_down_bps).replace(' ', '')}", style="cyan")
        net.append(f" ↑{format_rate(sysinfo.net_up_bps).replace(' ', '')}", style="magenta")
        return net

    def _load_text(self) -> str:
        sysinfo = self._snapshot.system
        if not sysinfo.load_avg:
            return ""
        return "load " + " ".join(f"{x:.2f}" for x in sysinfo.load_avg)

    def _counts_text(self) -> Text:
        """The listener / container counts and how long the last refresh took."""
        snapshot = self._snapshot
        summary = Text()
        summary.append(f" {snapshot.summary['total']} listeners", style="bold")
        if snapshot.docker_available:
            summary.append(
                f"  {snapshot.container_count} containers ({snapshot.summary['docker']} published)",
                style="cyan",
            )
        else:
            summary.append("  docker: off", style="dim")
        if self._collecting:
            summary.append("  collecting…", style="yellow")
        else:
            summary.append(f"  {snapshot.refresh_ms:.0f} ms", style="dim")
        return summary

    def _states_text(self) -> Text:
        """Hot / active / idle, the problem counts and the unread event count."""
        s = self._snapshot.summary
        states = Text()
        states.append(f" hot {s['hot']}", style="bold red")
        states.append(f"  active {s['active']}", style="green")
        states.append(f"  idle {s['idle']}", style="dim")
        states.append("   ")
        states.append_text(self._problems_text())
        return states

    def _problems_text(self) -> Text:
        """The warning / error counts and the unread events, the part the dense bar keeps."""
        s = self._snapshot.summary
        problems = Text()
        problems.append(
            f"warnings {s['warnings']}", style="bold yellow" if s["warnings"] else "dim"
        )
        problems.append(f"  errors {s['errors']}", style="bold red" if s["errors"] else "dim")
        problems.append("   ")
        problems.append(
            f"events {self._unread} new (n)" if self._unread else "events (n)",
            style="bold yellow" if self._unread else "dim",
        )
        return problems

    def _summary_block(self) -> Table:
        """The counts, the state line and the two newest events on the right."""
        events = Table.grid(padding=(0, 1))
        events.add_column(style="dim", no_wrap=True)
        events.add_column()
        recent = self._snapshot.events[-TOPBAR_EVENTS:]
        if not recent:
            events.add_row("", Text("no events yet", style="dim"))
        for ev in recent:
            events.add_row(
                format_time(ev.timestamp),
                Text(
                    ev.message,
                    style=_EVENT_STYLES.get(ev.level, ""),
                    overflow="ellipsis",
                    no_wrap=True,
                ),
            )

        right = Table.grid(expand=True)
        right.add_column(ratio=1)
        right.add_row(self._title_line(self._counts_text()))
        right.add_row(self._states_text())
        right.add_row(events)
        return right

    def _one_line(self) -> Table:
        """The whole bar on a single row, for the dense presets."""
        sysinfo = self._snapshot.system
        gauges = Text()
        gauges.append(
            f"CPU {sysinfo.cpu_percent:.0f}%",
            style=threshold_style(sysinfo.cpu_percent, SYSTEM_CPU_LIMITS),
        )
        gauges.append("  ")
        gauges.append(
            f"MEM {sysinfo.mem_percent:.0f}%",
            style=threshold_style(sysinfo.mem_percent, SYSTEM_MEM_LIMITS),
        )
        gauges.append("  ")
        gauges.append_text(self._net_text())
        summary = self._counts_text()
        summary.append("   ")
        summary.append_text(self._problems_text())
        line = Table.grid(padding=(0, 2), expand=True)
        line.add_column(width=44)
        line.add_column(ratio=1)
        line.add_row(gauges, self._title_line(summary))
        return line
