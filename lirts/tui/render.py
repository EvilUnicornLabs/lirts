"""The table's column registry and the clock format shared by everything lirts prints.

One :class:`Column` per table column: its header, the cell renderer from
:mod:`lirts.tui.cells` and how it sorts.  The cell renderers themselves are re-exported here,
because the TUI, the CLI and the panels have always imported them from this module.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rich.text import Text

from lirts.config_defaults import DEFAULT_CONFIG
from lirts.config_view import ConfigView
from lirts.identity import role_label
from lirts.models import Listener
from lirts.tui.cells import (
    LEVEL_MARKERS,
    MAX_CELL,
    STATUS_ORDER,
    activity_text,
    bandwidth_text,
    bar,
    clip,
    cpu_text,
    health_text,
    http_text,
    latency_text,
    level_marker,
    mem_text,
    origin_text,
    pids_text,
    project_text,
    service_text,
    source_text,
    sparkline,
    status_style,
    status_text,
    threshold_style,
    trend_text,
    uptime_text,
)

__all__ = [
    "COLUMNS",
    "COLUMN_ALIASES",
    "DEFAULT_CLOCK_FORMAT",
    "LEVEL_MARKERS",
    "MAX_CELL",
    "STATUS_ORDER",
    "Column",
    "activity_text",
    "bandwidth_text",
    "bar",
    "clip",
    "clock_format",
    "cpu_text",
    "format_time",
    "health_text",
    "http_text",
    "latency_text",
    "level_marker",
    "mem_text",
    "origin_text",
    "pids_text",
    "project_text",
    "resolve_columns",
    "service_text",
    "set_clock_format",
    "source_text",
    "sparkline",
    "status_style",
    "status_text",
    "threshold_style",
    "trend_text",
    "uptime_text",
]

# Fallback clock format, used until the app applies the one from ``ui.clock``.
DEFAULT_CLOCK_FORMAT = "%H:%M:%S"

_clock_format = DEFAULT_CLOCK_FORMAT


def set_clock_format(fmt: str) -> str:
    """Set the strftime format of every time lirts prints (``ui.clock``).

    Like the active glyph set this is module state on purpose: it is the one place the app
    sets it, so the renderers deep inside a screen need no extra argument.
    """
    global _clock_format
    _clock_format = fmt or DEFAULT_CLOCK_FORMAT
    return _clock_format


def clock_format() -> str:
    """The strftime format times are drawn with right now."""
    return _clock_format


def format_time(timestamp: float, fmt: str | None = None, *, with_date: bool = False) -> str:
    """A unix timestamp as local time, in ``fmt`` or in the configured clock format.

    ``with_date`` prefixes the day when the format does not already show one (event logs).
    """
    fmt = fmt or _clock_format
    if with_date and "%Y" not in fmt and "%d" not in fmt:
        fmt = "%Y-%m-%d " + fmt
    return time.strftime(fmt, time.localtime(timestamp))


@dataclass
class Column:
    """One table column: its header, how a row renders into it and how it sorts."""

    key: str
    label: str
    render: Callable[[Listener, ConfigView], Text]
    sort_key: Callable[[Listener], Any]
    reverse_default: bool = False


COLUMNS: dict[str, Column] = {
    "port": Column(
        "port",
        "PORT",
        lambda x, c: Text(str(x.port), style="bold", justify="right"),
        lambda x: (x.port, x.protocol),
    ),
    "proto": Column(
        "proto",
        "PROTO",
        lambda x, c: Text(x.protocol, style="" if x.protocol == "TCP" else "magenta"),
        lambda x: x.protocol,
    ),
    "state": Column(
        "state",
        "STATE",
        lambda x, c: Text(
            x.state, style="magenta" if x.state == "SSH" else ("dim" if x.state != "LISTEN" else "")
        ),
        lambda x: x.state,
    ),
    "src": Column(
        "src", "SRC", lambda x, c: source_text(x, icons=c.ui.row_icons), lambda x: x.source
    ),
    "service": Column(
        "service",
        "SERVICE",
        lambda x, c: service_text(x, icons=c.ui.row_icons),
        lambda x: x.identity.service.lower(),
    ),
    "identity": Column(
        "identity",
        "ROLE",
        lambda x, c: Text(role_label(x.identity.role), style="dim"),
        lambda x: x.identity.role,
    ),
    "process": Column(
        "process", "PROCESS", lambda x, c: Text(clip(x.name)), lambda x: x.name.lower()
    ),
    "pids": Column("pids", "PID", lambda x, c: pids_text(x), lambda x: x.pid or 0),
    "cpu": Column(
        "cpu",
        "CPU",
        lambda x, c: cpu_text(x.cpu_percent, c.thresholds.cpu),
        lambda x: x.cpu_percent,
        True,
    ),
    "mem": Column(
        "mem",
        "MEM",
        lambda x, c: mem_text(x.memory_mb, c.thresholds.memory_mb),
        lambda x: x.memory_mb,
        True,
    ),
    "conns": Column(
        "conns",
        "CONN",
        lambda x, c: Text(str(x.connections), justify="right"),
        lambda x: x.connections,
        True,
    ),
    "activity": Column(
        "activity", "ACTIVITY", lambda x, c: activity_text(x), lambda x: x.activity_score, True
    ),
    "bandwidth": Column(
        "bandwidth",
        "BANDWIDTH",
        lambda x, c: bandwidth_text(x),
        lambda x: (x.bytes_in_rate or 0) + (x.bytes_out_rate or 0),
        True,
    ),
    "uptime": Column(
        "uptime", "UPTIME", lambda x, c: uptime_text(x), lambda x: x.uptime_seconds or 0, True
    ),
    "project": Column(
        "project",
        "PROJECT",
        lambda x, c: project_text(x),
        lambda x: (x.identity.project or "~").lower(),
    ),
    "container": Column(
        "container",
        "CONTAINER",
        lambda x, c: Text(
            clip(x.container.name) if x.container else "-", style="" if x.container else "dim"
        ),
        lambda x: (x.container.name if x.container else "~").lower(),
    ),
    "stack": Column(
        "stack",
        "STACK",
        lambda x, c: Text(
            (x.container.stack if x.container and x.container.stack else "-"),
            style="" if x.container and x.container.stack else "dim",
        ),
        lambda x: ((x.container.stack or "~") if x.container else "~").lower(),
    ),
    "http": Column("http", "HTTP", lambda x, c: http_text(x), lambda x: x.http.status or 0),
    "latency": Column(
        "latency", "LATENCY", lambda x, c: latency_text(x), lambda x: x.http.latency_ms or 0, True
    ),
    "origin": Column(
        "origin",
        "STARTED FROM",
        lambda x, c: origin_text(x),
        lambda x: (x.primary.origin.short if x.primary and x.primary.origin else "~").lower(),
    ),
    "health": Column(
        "health",
        "HEALTH",
        lambda x, c: health_text(x),
        lambda x: (0 if not x.health.ok else 1, x.health.tcp_latency_ms or 0),
    ),
    "status": Column(
        "status",
        "STATUS",
        lambda x, c: status_text(x.status),
        lambda x: STATUS_ORDER.get(x.status, 3),
    ),
    "trend": Column(
        "trend",
        "TREND",
        lambda x, c: trend_text(x),
        lambda x: x.cpu_history[-1] if x.cpu_history else 0,
        True,
    ),
}


# Names accepted in the config for a column that is registered under another key.
COLUMN_ALIASES: dict[str, str] = {"role": "identity"}


def resolve_columns(names: list[str]) -> list[Column]:
    """The named columns in order, falling back to the configured defaults when none are known."""
    wanted = [COLUMN_ALIASES.get(n, n) for n in names]
    cols = [COLUMNS[n] for n in wanted if n in COLUMNS]
    return cols or [COLUMNS[n] for n in DEFAULT_CONFIG["columns"] if n in COLUMNS]
