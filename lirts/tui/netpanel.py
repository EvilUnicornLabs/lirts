"""The traffic panel: the btop-style net box and its byte / rate formatting."""

from __future__ import annotations

from typing import Any

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text
from textual.widget import Widget

from lirts.insights import format_rate
from lirts.tui.glyphs import active


def _short_bytes(value: float) -> str:
    """One-character-unit size for the scale label of the graph (e.g. ``2.3M``)."""
    if value < 1024:
        return f"{value:.0f}B"
    if value < 1024**2:
        return f"{value / 1024:.0f}K"
    if value < 1024**3:
        return f"{value / 1024**2:.1f}M"
    return f"{value / 1024**3:.2f}G"


def format_bytes(value: float) -> str:
    """A byte total in binary units, e.g. ``2.30 GiB``."""
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} TiB"


def format_rate_bits(bps: float) -> str:
    """A byte-per-second rate as bits per second, the unit link speeds are quoted in."""
    bits = bps * 8
    for unit in ("bps", "Kibps", "Mibps", "Gibps"):
        if bits < 1024 or unit == "Gibps":
            return f"{bits:.0f} {unit}" if unit == "bps" else f"{bits:.1f} {unit}"
        bits /= 1024
    return f"{bits:.1f} Gibps"


def render_net_graph(
    down: list[float], *, up: list[float], width: int, height: int
) -> tuple[list[Text], float]:
    """btop-style mirrored graph: download grows up from the midline, upload grows down.

    Returns the lines (top to bottom) and the scale (B/s at full height of one half).
    """
    width = max(4, width)
    half = max(1, height // 2)
    d = [float(v) for v in down[-width:]]
    u = [float(v) for v in up[-width:]]
    d = [0.0] * (width - len(d)) + d
    u = [0.0] * (width - len(u)) + u
    scale = max(max(d, default=0.0), max(u, default=0.0), 1.0)
    glyphs = active()
    blocks = glyphs.partial_blocks
    top_block = len(blocks) - 1

    def cell(value: float, row: int, mirrored: bool) -> tuple[str, str]:
        level = value / scale * half
        if level >= row:
            return glyphs.bar_full, ""
        if level <= row - 1:
            return " ", ""
        frac = level - (row - 1)
        if not mirrored:
            idx = min(top_block, max(1, round(frac * len(blocks))))
            return blocks[idx], ""
        idx = round((1 - frac) * len(blocks))
        if idx <= 0:
            return glyphs.bar_full, ""
        return blocks[min(top_block, idx)], "reverse"

    lines: list[Text] = []
    for row in range(half, 0, -1):
        line = Text()
        for v in d:
            ch, extra = cell(v, row, False)
            line.append(ch, style=("cyan " + extra).strip())
        lines.append(line)
    for row in range(1, half + 1):
        line = Text()
        for v in u:
            ch, extra = cell(v, row, True)
            line.append(ch, style=("magenta " + extra).strip())
        lines.append(line)
    label = _short_bytes(scale)
    for idx in (0, len(lines) - 1):
        lines[idx] = Text(label, style="dim") + Text(" ") + lines[idx][len(label) + 1 :]
    return lines, scale


class NetPanel(Widget):
    """Machine-wide or per-row traffic: mirrored history graph plus current / top / total."""

    STATS_WIDTH = 34

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.title = "net"
        self.down: list[float] = []
        self.up: list[float] = []
        self.down_now = 0.0
        self.up_now = 0.0
        self.down_total = 0.0
        self.up_total = 0.0
        self.total_note = ""
        self.note = ""

    def show(
        self,
        title: str,
        *,
        down: list[float],
        up: list[float],
        down_now: float,
        up_now: float,
        down_total: float,
        up_total: float,
        total_note: str = "",
        note: str = "",
    ) -> None:
        """Replace what the panel shows and repaint it."""
        self.border_title = title
        self.down, self.up = list(down), list(up)
        self.down_now, self.up_now = down_now, up_now
        self.down_total, self.up_total = down_total, up_total
        self.total_note = total_note
        self.note = note
        self.refresh()

    def render(self) -> RenderableType:
        width = max(10, self.size.width - self.STATS_WIDTH - 2)
        height = max(2, self.size.height)
        lines, _ = render_net_graph(self.down, up=self.up, width=width, height=height)
        graph = Group(*lines)
        stats = Table.grid(padding=(0, 1))
        stats.add_column(width=1)
        stats.add_column(min_width=8)
        stats.add_column(justify="right")
        top_down = max(self.down, default=0.0)
        top_up = max(self.up, default=0.0)
        stats.add_row(Text("▼", style="cyan"), Text("download", style="bold cyan"), "")
        stats.add_row(
            "",
            Text(format_rate(self.down_now)),
            Text(f"({format_rate_bits(self.down_now)})", style="dim"),
        )
        stats.add_row("", Text("Top", style="dim"), Text(format_rate(top_down)))
        stats.add_row("", Text("Total", style="dim"), Text(format_bytes(self.down_total)))
        stats.add_row(Text("▲", style="magenta"), Text("upload", style="bold magenta"), "")
        stats.add_row(
            "",
            Text(format_rate(self.up_now)),
            Text(f"({format_rate_bits(self.up_now)})", style="dim"),
        )
        stats.add_row("", Text("Top", style="dim"), Text(format_rate(top_up)))
        stats.add_row("", Text("Total", style="dim"), Text(format_bytes(self.up_total)))
        if self.total_note:
            stats.add_row("", Text(self.total_note, style="dim"), "")
        if self.note:
            stats.add_row("", Text(self.note, style="italic yellow"), "")
        layout = Table.grid(expand=True, padding=(0, 1))
        layout.add_column(ratio=1)
        layout.add_column(width=self.STATS_WIDTH)
        layout.add_row(graph, stats)
        return layout
