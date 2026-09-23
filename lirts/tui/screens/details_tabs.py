"""The body of each tab of the details screen, rendered from one listener."""

from __future__ import annotations

import time
from typing import Any

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from lirts.constants import (
    GLYPH_RECURRING,
    SPARK_WIDTH_SCREEN,
)
from lirts.identity import role_label
from lirts.insights import format_duration, format_rate
from lirts.models import Activity, Listener, Snapshot
from lirts.tui.netpanel import format_bytes
from lirts.tui.render import format_time, sparkline, status_text
from lirts.tui.widgets import render_insights

# Rows shown before the list is cut with a "… and N more" line.
MAX_PROCESS_COMMANDS = 6
MAX_REMOTE_CONNECTIONS = 60
MAX_PORT_EVENTS = 15
# Samples of the activity history drawn as a coloured block strip.
ACTIVITY_STRIP_SAMPLES = 60
# Width of the traffic sparklines on the Docker tab and per client on Connections.
DOCKER_SPARK_WIDTH = 40
EDGE_SPARK_WIDTH = 20

_ACTIVITY_BLOCKS: dict[str, tuple[str, str]] = {
    Activity.HOT: ("█", "red"),
    Activity.ACTIVE: ("▄", "green"),
    Activity.IDLE: ("▁", "dim"),
}


def detail_grid() -> Table:
    """A two-column key / value grid with the label column styled."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(width=16, style="bold cyan")
    grid.add_column(overflow="fold")
    return grid


def overview_tab(listener: Listener) -> RenderableType:
    """Identity, port, uptime, load and probe results, followed by the insights."""
    g = detail_grid()
    ident = listener.identity
    g.add_row("Status", status_text(listener.status))
    g.add_row(
        "Service",
        f"{ident.service}  ({role_label(ident.role)}, {ident.confidence:.0%} confidence)",
    )
    if ident.project:
        g.add_row("Project", ident.project)
    g.add_row(
        "Port",
        f"{listener.port}/{listener.protocol}  state {listener.state}  "
        f"bound on {', '.join(listener.addresses) or '-'}",
    )
    container = f"  (container {listener.container.name})" if listener.container else ""
    g.add_row("Source", listener.source + container)
    first_seen = f"  first seen {format_time(listener.first_seen)}" if listener.first_seen else ""
    g.add_row("Uptime", format_duration(listener.uptime_seconds) + first_seen)
    g.add_row("Restarts", f"{listener.restarts_last_hour} in the last hour")
    rates = (
        f"  ↓{format_rate(listener.bytes_in_rate)} ↑{format_rate(listener.bytes_out_rate)}"
        if listener.bytes_in_rate is not None
        else ""
    )
    g.add_row("Activity", f"{listener.activity} (score {listener.activity_score:.2f})" + rates)
    g.add_row("Connections", f"{listener.connections} established inbound")
    g.add_row("CPU / Memory", f"{listener.cpu_percent:.1f}%  /  {listener.memory_mb:.0f} MB")
    if listener.http.attempted:
        http = listener.http.summary
        if listener.http.server:
            http += f"  Server: {listener.http.server}"
        if listener.http.powered_by:
            http += f"  X-Powered-By: {listener.http.powered_by}"
        g.add_row("HTTP", http)
    if listener.health.checked:
        g.add_row("Health", listener.health.summary)
    if listener.proxy_chain:
        g.add_row("Proxy chain", listener.proxy_chain)
    if listener.hosts:
        g.add_row("Local domains", ", ".join(listener.hosts))
    if listener.shared_reason:
        g.add_row("Shared port", listener.shared_reason)
    return Group(g, Text(""), render_insights(listener))


def processes_tab(listener: Listener) -> RenderableType:
    """Every process bound to the port, with its command line, directory and origin."""
    if not listener.processes:
        return Text(
            "No host process (port published by Docker without a userland proxy).", style="dim"
        )
    t = Table(box=None, pad_edge=False, expand=False, show_header=True, header_style="bold")
    for col in ("PID", "NAME", "USER", "STATUS", "CPU", "MEM", "THREADS", "UPTIME", "BIND"):
        t.add_column(col)
    for p in listener.processes:
        t.add_row(
            str(p.pid),
            p.name,
            p.user or "-",
            p.status or "-",
            f"{p.cpu_percent:.1f}%",
            f"{p.memory_mb:.0f} MB",
            str(p.threads),
            format_duration(p.uptime_seconds),
            ", ".join(p.addresses),
        )
    parts: list[Any] = [t, Text("")]
    for p in listener.processes[:MAX_PROCESS_COMMANDS]:
        parts.append(Text(f"PID {p.pid}  {p.command}", style="dim", overflow="fold"))
        if p.cwd:
            parts.append(Text(f"    cwd {p.cwd}", style="dim"))
        if p.exe:
            parts.append(Text(f"    exe {p.exe}", style="dim"))
        if p.origin is not None and p.origin.interactive:
            parts.append(Text(f"    started from {p.origin.summary}", style="cyan"))
    return Group(*parts)


def _docker_stats_rows(grid: Table, listener: Listener) -> None:
    """CPU, memory and network of the container, with the traffic history underneath."""
    c = listener.container
    if c is None or c.cpu_percent is None:
        return
    mem = f"{c.memory_mb:.0f} MB" if c.memory_mb is not None else "-"
    if c.memory_limit_mb:
        mem += f" / {c.memory_limit_mb:.0f} MB"
    grid.add_row("Stats", f"CPU {c.cpu_percent:.1f}%  MEM {mem}  PIDs {c.pids_current or '-'}")
    if c.net_rx_rate is None:
        return
    net = Text(f"↓ {format_rate(c.net_rx_rate)}", style="cyan")
    net.append(f"  ↑ {format_rate(c.net_tx_rate)}", style="magenta")
    if c.net_rx_bytes is not None and c.net_tx_bytes is not None:
        net.append(
            f"   total ↓ {format_bytes(c.net_rx_bytes)} ↑ {format_bytes(c.net_tx_bytes)}"
            " since the container started",
            style="dim",
        )
    grid.add_row("Network", net)
    if not (any(listener.bytes_in_history) or any(listener.bytes_out_history)):
        return
    grid.add_row(
        "",
        Text(sparkline(listener.bytes_in_history, width=DOCKER_SPARK_WIDTH), style="cyan")
        + Text("  ↓ history", style="dim"),
    )
    grid.add_row(
        "",
        Text(sparkline(listener.bytes_out_history, width=DOCKER_SPARK_WIDTH), style="magenta")
        + Text("  ↑ history", style="dim"),
    )


def docker_tab(listener: Listener) -> RenderableType:
    """The container behind the port: image, compose project, stats, volumes, environment."""
    c = listener.container
    if not c:
        return Text("Not a Docker container.", style="dim")
    g = detail_grid()
    g.add_row("Container", f"{c.name}  ({c.short_id})")
    g.add_row("Image", c.image)
    g.add_row("State", f"{c.status}" + (f" / health {c.health}" if c.health else ""))
    g.add_row(
        "Started",
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(c.started_at)) if c.started_at else "-",
    )
    g.add_row("Restarts", str(c.restart_count))
    if c.stack:
        g.add_row("Compose", f"project {c.stack}" + (f", service {c.service}" if c.service else ""))
    if c.working_dir:
        g.add_row("Project dir", c.working_dir)
    _docker_stats_rows(g, listener)
    if c.port_map:
        g.add_row("Ports", ", ".join(f"{h} → {i}" for h, i in sorted(c.port_map.items())))
    if c.internal_ports:
        g.add_row("Unpublished", ", ".join(str(p) for p in c.internal_ports))
    parts: list[Any] = [g, Text(""), Text("Persistence", style="bold underline")]
    if c.mounts:
        for m in c.mounts:
            label = m.name or m.source
            parts.append(Text(f"  {m.type:<7} {label} → {m.destination}{'' if m.rw else ' (ro)'}"))
    else:
        parts.append(Text("  no volumes or bind mounts (data is ephemeral)", style="dim"))
    parts.append(Text(""))
    parts.append(Text("Environment", style="bold underline"))
    if c.env:
        for k in sorted(c.env):
            v = c.env[k]
            parts.append(
                Text(f"  {k}=", style="cyan") + Text(v, style="dim" if v == "********" else "")
            )
    else:
        parts.append(Text("  (not available)", style="dim"))
    return Group(*parts)


def connections_tab(listener: Listener, snapshot: Snapshot) -> RenderableType:
    """Who connects to this port, where the process connects to, and the proxies around it."""
    parts: list[Any] = [
        Text(
            f"{listener.connections} established inbound connection(s) on port {listener.port}",
            style="bold",
        )
    ]
    if listener.clients:
        parts.append(Text(""))
        parts.append(Text("Local clients (who talks to this port)", style="bold"))
        for edge in sorted(listener.clients, key=lambda e: -e.count):
            proj = f" [{edge.client_project}]" if edge.client_project else ""
            line = Text(f"  ◀── {edge.client_name}{proj} (PID {edge.client_pid}) ×{edge.count}")
            if edge.bytes_in_rate is not None or edge.bytes_out_rate is not None:
                line.append(f"  ↓{format_rate(edge.bytes_in_rate)}", style="cyan")
                line.append(f" ↑{format_rate(edge.bytes_out_rate)}", style="magenta")
                if len(edge.rate_history) > 1:
                    line.append(
                        "  " + sparkline(edge.rate_history, width=EDGE_SPARK_WIDTH), style="dim"
                    )
            parts.append(line)
            if edge.client_cmd:
                parts.append(Text(f"      {edge.client_cmd}", style="dim"))
    remotes = listener.remote_conns
    parts.append(Text(""))
    parts.append(Text(f"Outbound connections from the process(es): {len(remotes)}", style="bold"))
    for rc in remotes[:MAX_REMOTE_CONNECTIONS]:
        parts.append(Text(f"  → {rc}"))
    if len(remotes) > MAX_REMOTE_CONNECTIONS:
        parts.append(Text(f"  … and {len(remotes) - MAX_REMOTE_CONNECTIONS} more", style="dim"))
    dependents = [o for o in snapshot.listeners if listener.port in o.proxy_targets]
    if dependents:
        parts.append(Text(""))
        parts.append(Text("Proxied by", style="bold"))
        for d in dependents:
            parts.append(Text(f"  {d.identity.service} on {d.port}"))
    if listener.proxy_targets:
        parts.append(Text(""))
        parts.append(Text("Forwards to", style="bold"))
        for port in listener.proxy_targets:
            target = snapshot.by_port(port)
            parts.append(Text(f"  {port}  {target.identity.service if target else ''}"))
    return Group(*parts)


def _history_grid(listener: Listener) -> Table:
    """One sparkline row per measured series, plus the activity strip."""
    g = detail_grid()
    g.add_row(
        "CPU",
        Text(sparkline(listener.cpu_history, width=SPARK_WIDTH_SCREEN) or "-", style="yellow")
        + Text(
            f"  max {max(listener.cpu_history) if listener.cpu_history else 0:.1f}%", style="dim"
        ),
    )
    g.add_row(
        "Connections",
        Text(sparkline(listener.conn_history, width=SPARK_WIDTH_SCREEN) or "-", style="blue")
        + Text(f"  max {max(listener.conn_history) if listener.conn_history else 0}", style="dim"),
    )
    if listener.latency_history:
        average = sum(listener.latency_history) / len(listener.latency_history)
        g.add_row(
            "Latency",
            Text(sparkline(listener.latency_history, width=SPARK_WIDTH_SCREEN), style="magenta")
            + Text(
                f"  avg {average:.0f} ms, peak {max(listener.latency_history):.0f} ms", style="dim"
            ),
        )
    if any(listener.bytes_in_history) or any(listener.bytes_out_history):
        g.add_row(
            "Traffic ↓",
            Text(
                sparkline(listener.bytes_in_history, width=SPARK_WIDTH_SCREEN) or "-", style="cyan"
            )
            + Text(f"  peak {format_rate(max(listener.bytes_in_history or [0]))}", style="dim"),
        )
        g.add_row(
            "Traffic ↑",
            Text(
                sparkline(listener.bytes_out_history, width=SPARK_WIDTH_SCREEN) or "-",
                style="magenta",
            )
            + Text(f"  peak {format_rate(max(listener.bytes_out_history or [0]))}", style="dim"),
        )
    act = Text()
    for level in listener.activity_history[-ACTIVITY_STRIP_SAMPLES:]:
        glyph, style = _ACTIVITY_BLOCKS.get(level, _ACTIVITY_BLOCKS[Activity.IDLE])
        act.append(glyph, style=style)
    g.add_row("Activity", act or Text("-"))
    g.add_row("Samples", f"{len(listener.cpu_history)} (one per refresh)")
    return g


def history_tab(listener: Listener, snapshot: Snapshot) -> RenderableType:
    """The rolling sparklines of this port, its recurring patterns and its events."""
    parts: list[Any] = [_history_grid(listener), Text("")]
    if listener.patterns:
        parts.append(
            Text("Recurring on this port (remembered across runs)", style="bold underline")
        )
        for line in listener.patterns:
            parts.append(Text(f"  {GLYPH_RECURRING} {line}", style="yellow"))
        parts.append(Text(""))
    parts.append(Text("Events for this port", style="bold underline"))
    events = [e for e in snapshot.events if e.port == listener.port]
    if not events:
        parts.append(Text("  none this session", style="dim"))
    for ev in events[-MAX_PORT_EVENTS:]:
        when = format_time(ev.timestamp)
        parts.append(Text(f"  {when}  {ev.message}"))
    return Group(*parts)


def identity_tab(listener: Listener) -> RenderableType:
    """Why lirts thinks this is the service it named, reason by reason."""
    ident = listener.identity
    parts: list[Any] = [
        Text(f"{ident.service}", style="bold"),
        Text(f"role: {role_label(ident.role)}   confidence: {ident.confidence:.0%}"),
        Text(""),
        Text("Reasoning", style="bold underline"),
    ]
    for i, reason in enumerate(ident.reasons):
        parts.append(Text(f"  {'★' if i == 0 else '•'} {reason}", style="" if i == 0 else "dim"))
    parts.append(Text(""))
    parts.append(
        Text(
            "Override the name in the config under `aliases` (port number or substring).",
            style="dim italic",
        )
    )
    return Group(*parts)
