"""One Rich cell each: how a single value of a listener is drawn.

The table, the CLI table output and the side panel all draw through these; the column
registry that puts them in order lives in :mod:`lirts.tui.render`.
"""

from __future__ import annotations

from rich.text import Text

from lirts.constants import (
    GLYPH_ERROR,
    GLYPH_INFO,
    GLYPH_RESTART,
    GLYPH_STATUS,
    GLYPH_STATUS_UNKNOWN,
    GLYPH_WARNING,
    SPARK_WIDTH_CELL,
)
from lirts.insights import format_duration, format_rate
from lirts.models import Activity, Level, Listener, Status
from lirts.tui.glyphs import active

# Sort order of the STATUS column: problems first.
STATUS_ORDER: dict[str, int] = {Status.ERROR: 0, Status.WARNING: 1, Status.HEALTHY: 2}

# Glyph and style per severity, shared by the side panel, the events screen and the explain tabs.
LEVEL_MARKERS: dict[str, tuple[str, str]] = {
    Level.ERROR: (GLYPH_ERROR, "bold red"),
    Level.WARNING: (GLYPH_WARNING, "yellow"),
    Level.INFO: (GLYPH_INFO, "dim"),
}

_STATUS_STYLES: dict[str, str] = {
    Status.ERROR: "bold red",
    Status.WARNING: "bold yellow",
    Status.HEALTHY: "green",
}
_STATUS_LABELS: dict[str, str] = {
    Status.ERROR: f"{GLYPH_STATUS} error",
    Status.WARNING: f"{GLYPH_STATUS} warn",
    Status.HEALTHY: f"{GLYPH_STATUS} ok",
}
_ACTIVITY_CELLS: dict[str, tuple[str, str]] = {
    Activity.HOT: ("▮▮▮ hot", "bold red"),
    Activity.ACTIVE: ("▮▮· active", "green"),
    Activity.IDLE: ("··· idle", "dim"),
}

# Longest cell before the text is cut with an ellipsis.
MAX_CELL = 20

# How sure identity has to be for the SERVICE cell to state the name (bold) rather than
# offer it (plain); below the lower bound the name is a guess and is marked with a `?`.
CONFIDENCE_SURE = 0.7
CONFIDENCE_GUESS = 0.5


def level_marker(level: str) -> tuple[str, str]:
    """Glyph and Rich style for an insight or event level."""
    return LEVEL_MARKERS.get(level, (GLYPH_INFO, ""))


def status_style(status: str) -> str:
    """Rich style for a listener status; empty for an unknown one."""
    return _STATUS_STYLES.get(status, "")


def sparkline(
    data: list[float] | list[int], *, width: int = 12, maximum: float | None = None
) -> str:
    """The last `width` samples as block glyphs, scaled to `maximum` or to the largest sample."""
    if not data:
        return ""
    ramp = active().spark
    values = [float(v) for v in data[-width:]]
    top = maximum if maximum is not None else max(values)
    if top <= 0:
        return ramp[1] * len(values)
    out = []
    for v in values:
        idx = round(v / top * (len(ramp) - 1))
        out.append(ramp[max(1, min(idx, len(ramp) - 1))])
    return "".join(out)


def bar(percent: float, width: int = 10) -> str:
    """A filled / empty block bar of `width` cells for a 0-100 percentage."""
    glyphs = active()
    percent = max(0.0, min(100.0, percent))
    filled = round(percent / 100 * width)
    return glyphs.bar_full * filled + glyphs.bar_empty * (width - filled)


def threshold_style(value: float, limits: tuple[float, float] | None) -> str:
    """Green / yellow / red for a value against its (yellow, red) limits."""
    if not limits:
        return ""
    yellow, red = limits
    if value >= red:
        return "bold red"
    if value >= yellow:
        return "yellow"
    return "green"


def cpu_text(pct: float, limits: tuple[float, float] | None = None) -> Text:
    """CPU percentage, coloured against its (yellow, red) limits."""
    return Text(f"{pct:.1f}%", style=threshold_style(pct, limits), justify="right")


def mem_text(mb: float, limits: tuple[float, float] | None = None) -> Text:
    """Memory in MB or GB, coloured against its (yellow, red) limits."""
    label = f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"
    return Text(label, style=threshold_style(mb, limits), justify="right")


def status_text(status: str, short: bool = False) -> Text:
    """The status dot, with its word unless `short`."""
    if status not in _STATUS_LABELS:
        return Text(GLYPH_STATUS_UNKNOWN if short else f"{GLYPH_STATUS_UNKNOWN} n/a", style="dim")
    label = GLYPH_STATUS if short else _STATUS_LABELS[status]
    return Text(label, style=_STATUS_STYLES[status])


def activity_text(listener: Listener) -> Text:
    """How busy the listener is, as a three-block gauge."""
    label, style = _ACTIVITY_CELLS.get(listener.activity, _ACTIVITY_CELLS[Activity.IDLE])
    return Text(label, style=style)


def bandwidth_text(listener: Listener) -> Text:
    """Current down / up rate; `~` when the rate belongs to a process serving several ports."""
    if listener.bytes_in_rate is None and listener.bytes_out_rate is None:
        return Text("-", style="dim", justify="right")
    down = format_rate(listener.bytes_in_rate or 0.0).replace(" ", "")
    up = format_rate(listener.bytes_out_rate or 0.0).replace(" ", "")
    prefix = "~" if listener.bandwidth_shared else ""
    txt = Text(justify="right")
    txt.append(f"{prefix}↓{down} ", style="cyan")
    txt.append(f"↑{up}", style="magenta")
    return txt


def clip(text: str, width: int = MAX_CELL) -> str:
    """`text` cut to `width` characters with a trailing ellipsis."""
    return text if len(text) <= width else text[: width - 1] + "…"


def service_text(listener: Listener, *, icons: bool = False) -> Text:
    """The inferred service name: bold when identity is sure, dim and marked `?` when it guesses."""
    ident = listener.identity
    sure = ident.confidence >= CONFIDENCE_SURE
    guess = ident.confidence < CONFIDENCE_GUESS
    style = "bold" if sure else ("dim italic" if guess else "")
    txt = Text(clip(ident.service), style=style)
    # An ssh row is already marked with the tunnel glyph by the table; one of them is enough.
    if icons and listener.state != "SSH":
        txt = Text(f"{active().role_icon(ident.role)} ") + txt
    if guess and ident.service != "Unknown" and listener.state != "STOPPED":
        txt.append("?", style="dim")
    return txt


def source_text(listener: Listener, *, icons: bool = False) -> Text:
    """Where the row came from: local, docker, ssh or kubernetes."""
    style = "magenta" if listener.source == "ssh" else ("cyan" if listener.is_docker else "green")
    label = listener.source
    if icons:
        label = f"{active().source_icon(listener.source)} {label}"
    return Text(label, style=style)


def pids_text(listener: Listener) -> Text:
    """The first PID, with `+n` when several processes share the port."""
    n = len(listener.processes)
    if n == 0:
        return Text("-", style="dim", justify="right")
    if n == 1:
        return Text(str(listener.pid), justify="right")
    return Text(f"{listener.pid} +{n - 1}", justify="right")


def uptime_text(listener: Listener) -> Text:
    """How long the listener has been up, with the restart count of the last hour."""
    up = listener.uptime_seconds
    txt = Text(format_duration(up), justify="right")
    if listener.restarts_last_hour:
        txt.append(f" {GLYPH_RESTART}{listener.restarts_last_hour}", style="yellow")
    return txt


def http_text(listener: Listener) -> Text:
    """The HTTP status code of the background probe, or why it failed."""
    p = listener.http
    if not p.attempted:
        return Text("-", style="dim")
    if p.ok and p.status is not None:
        style = "green" if p.status < 400 else ("yellow" if p.status < 500 else "red")
        return Text(str(p.status), style=style)
    return Text(
        GLYPH_ERROR,
        style=(
            "red" if p.error in ("timeout",) or (p.error or "").startswith("connection") else "dim"
        ),
    )


def latency_text(listener: Listener) -> Text:
    """Probe latency in milliseconds, coloured by how slow it is."""
    p = listener.http
    if not p.ok or p.latency_ms is None:
        return Text("-", style="dim", justify="right")
    style = "green" if p.latency_ms < 500 else ("yellow" if p.latency_ms < 1500 else "red")
    return Text(f"{p.latency_ms:.0f} ms", style=style, justify="right")


def origin_text(listener: Listener) -> Text:
    """The interactive shell or editor the process was started from, if known."""
    p = listener.primary
    if p is None or p.origin is None or not p.origin.interactive:
        return Text("-", style="dim")
    return Text(clip(p.origin.short, 24), style="dim")


def health_text(listener: Listener) -> Text:
    """The result of the last on-demand health check of this row."""
    h = listener.health
    if not h.checked:
        return Text("-", style="dim")
    if not h.tcp_ok:
        return Text(f"{GLYPH_ERROR} {h.tcp_error or 'down'}", style="bold red")
    if h.http_status is not None and h.http_status >= 400:
        return Text(f"{h.http_status} {h.http_path}", style="yellow")
    txt = Text(f"{h.tcp_latency_ms:.0f}ms" if h.tcp_latency_ms is not None else "ok", style="green")
    if h.http_path:
        txt.append(f" {h.http_path}", style="dim")
    return txt


def trend_text(listener: Listener) -> Text:
    """A small CPU sparkline for the TREND column."""
    return Text(sparkline(listener.cpu_history, width=SPARK_WIDTH_CELL) or "-", style="yellow")


def project_text(listener: Listener) -> Text:
    """The project the row belongs to, if identity found one."""
    return Text(listener.identity.project or "-", style="" if listener.identity.project else "dim")
