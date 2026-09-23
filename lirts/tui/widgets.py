"""Top bar and side panel widgets."""

from __future__ import annotations

import enum
import time
from collections.abc import Callable
from typing import Any

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from lirts.collectors.kube_models import KubeState
from lirts.constants import GLYPH_OK, GLYPH_RESTART
from lirts.identity import role_label
from lirts.insights import format_duration, format_rate
from lirts.models import Activity, Level, Listener
from lirts.ports import PortMemory, last_seen_text
from lirts.tui.render import level_marker, status_text
from lirts.tui.topbar import TopBar

_ACTIVITY_STYLES: dict[str, str] = {Activity.HOT: "bold red", Activity.ACTIVE: "green"}

# Local domain names listed in the side panel before the rest is left out.
MAX_DOMAINS = 4


class Section(enum.StrEnum):
    """One block of the side panel; a usage profile picks the blocks and their order."""

    IDENTITY = "identity"
    RUNTIME = "runtime"
    PROCESS = "process"
    CONTAINER = "container"
    CLUSTER = "cluster"
    HOSTS = "hosts"
    INSIGHTS = "insights"


DEFAULT_SECTIONS: tuple[Section, ...] = (
    Section.IDENTITY,
    Section.RUNTIME,
    Section.PROCESS,
    Section.CONTAINER,
    Section.INSIGHTS,
)


class SidePanel(VerticalScroll):
    """Details and insights for the selected row, next to the table (`p` hides it)."""

    sections: tuple[Section, ...] = DEFAULT_SECTIONS

    def compose(self) -> ComposeResult:
        yield Static(id="side-details")
        yield Static(id="side-insights")

    def show_group(self, name: str, *, members: list[Listener]) -> None:
        """Show the totals and the problems of a whole stack / project group."""
        details = self.query_one("#side-details", Static)
        insights = self.query_one("#side-insights", Static)
        title = Text(name, style="bold")
        title.append(f"  {len(members)} services", style="dim")
        grid = Table.grid(padding=(0, 1))
        grid.add_column(width=11)
        grid.add_column(overflow="fold")
        containers = [m.container for m in members if m.container]
        if containers and containers[0].working_dir:
            _kv(grid, key="Folder", value=Text(containers[0].working_dir, style="dim"))
        cpu = sum(m.cpu_percent for m in members)
        mem = sum(m.memory_mb for m in members)
        _kv(grid, key="CPU / MEM", value=f"{cpu:.1f}%  /  {mem:.0f} MB")
        _kv(grid, key="Conns", value=f"{sum(m.connections for m in members)} established")
        rows = Table.grid(padding=(0, 1))
        rows.add_column(width=6, justify="right")
        rows.add_column(overflow="ellipsis", no_wrap=True)
        rows.add_column(width=8)
        for m in sorted(members, key=lambda x: x.port):
            rows.add_row(
                Text(str(m.port), style="bold"), Text(m.identity.service), status_text(m.status)
            )
        details.update(Group(title, Text(""), grid, Text(""), rows))
        insights.update(Group(*_group_problems(members)))

    def set_sections(self, sections: tuple[Section, ...]) -> None:
        """Choose which blocks the panel shows, in which order (the usage profile decides)."""
        self.sections = sections

    def show(self, listener: Listener | None, *, kube: KubeState | None = None) -> None:
        """Show one row's details and insights, or the hint when nothing is selected."""
        details = self.query_one("#side-details", Static)
        insights = self.query_one("#side-insights", Static)
        if listener is None:
            details.update(Text("Select a row to see details", style="dim"))
            insights.update("")
            return
        memory = getattr(getattr(self.app, "engine", None), "port_memory", None)
        details.update(render_details(listener, sections=self.sections, kube=kube, memory=memory))
        insights.update(
            render_insights(listener) if Section.INSIGHTS in self.sections else Text("")
        )


def _group_problems(members: list[Listener]) -> list[Any]:
    """The warning and error insights of every member, or a clean bill of health."""
    parts: list[Any] = [Text("Insights", style="bold underline")]
    found = False
    for m in members:
        for ins in m.insights:
            if ins.level == Level.INFO:
                continue
            found = True
            marker, style = level_marker(ins.level)
            parts.append(Text(f"{marker} {m.port}: {ins.message}", style=style))
    if not found:
        parts.append(Text(f"{GLYPH_OK} nothing unusual in this group", style="green"))
    parts.append(Text(""))
    parts.append(
        Text(
            "Space/Enter collapse · x stop · t restart · l logs (whole stack)",
            style="dim italic",
        )
    )
    return parts


def _kv(table: Table, *, key: str, value: Any) -> None:
    """Add a bold key and its value as one row of a two-column grid."""
    table.add_row(
        Text(key, style="bold cyan"), value if isinstance(value, Text) else Text(str(value))
    )


def _identity_rows(grid: Table, listener: Listener) -> None:
    """Service, role, confidence, project and where the row came from."""
    ident = listener.identity
    _kv(grid, key="Status", value=status_text(listener.status))
    _kv(grid, key="Role", value=role_label(ident.role))
    conf = Text(
        f"{ident.confidence:.0%}",
        style=(
            "green" if ident.confidence >= 0.7 else ("yellow" if ident.confidence >= 0.4 else "red")
        ),
    )
    conf.append(f"  {ident.reasons[0]}" if ident.reasons else "", style="dim")
    _kv(grid, key="Confidence", value=conf)
    if ident.project:
        _kv(grid, key="Project", value=ident.project)
    _kv(
        grid,
        key="Source",
        value=Text(listener.source, style="cyan" if listener.is_docker else "green"),
    )
    _kv(grid, key="Bind", value=", ".join(listener.addresses) or "-")


def _usually_value(listener: Listener, memory: PortMemory) -> Text:
    """What the port memory knows about this port, as the side panel prints it."""
    usual = memory.usual(listener.port)
    if usual is None:
        return Text("this is the first time", style="dim")
    return Text(
        f"{usual.label} · {usual.seen_text} · last {last_seen_text(usual.last_seen, time.time())}"
    )


def _runtime_rows(grid: Table, listener: Listener, *, memory: PortMemory | None = None) -> None:
    """Uptime, activity, connections, resource use, the probe results and the port memory."""
    restarts = (
        f"  {GLYPH_RESTART} {listener.restarts_last_hour} restarts/h"
        if listener.restarts_last_hour
        else ""
    )
    _kv(grid, key="Uptime", value=format_duration(listener.uptime_seconds) + restarts)
    act = Text(listener.activity, style=_ACTIVITY_STYLES.get(listener.activity, "dim"))
    if listener.bytes_in_rate is not None:
        act.append(
            f"  ↓{format_rate(listener.bytes_in_rate)} ↑{format_rate(listener.bytes_out_rate)}",
            style="dim",
        )
    _kv(grid, key="Activity", value=act)
    _kv(grid, key="Conns", value=f"{listener.connections} established")
    _kv(
        grid,
        key="CPU / MEM",
        value=f"{listener.cpu_percent:.1f}%  /  {listener.memory_mb:.0f} MB",
    )
    if listener.http.attempted:
        server = f"  {listener.http.server}" if listener.http.server else ""
        _kv(grid, key="HTTP", value=listener.http.summary + server)
    if listener.health.checked:
        _kv(
            grid,
            key="Health",
            value=Text(
                listener.health.summary, style="green" if listener.health.ok else "bold red"
            ),
        )
    if listener.proxy_chain:
        _kv(grid, key="Proxy", value=listener.proxy_chain)
    if memory:
        _kv(grid, key="Usually", value=_usually_value(listener, memory))


def _process_rows(grid: Table, listener: Listener) -> None:
    """The process that holds the port, its command line and its working directory."""
    procs = listener.processes
    if not procs:
        return
    head = procs[0]
    _kv(
        grid,
        key="Process",
        value=f"{head.name}  PID {head.pid}" + (f"  ({head.user})" if head.user else ""),
    )
    _kv(grid, key="Command", value=Text(head.command[:300], style="dim"))
    if len(procs) > 1:
        _kv(
            grid,
            key="Also",
            value=", ".join(f"{p.name} {p.pid}" for p in procs[1:6])
            + (" …" if len(procs) > 6 else ""),
        )
    if head.cwd:
        _kv(grid, key="CWD", value=Text(head.cwd, style="dim"))


def _container_rows(grid: Table, listener: Listener) -> None:
    """Image, compose stack, state, published ports and whether data survives a restart."""
    c = listener.container
    if not c:
        return
    _kv(grid, key="Container", value=f"{c.name}  ({c.short_id})")
    _kv(grid, key="Image", value=c.image)
    if c.stack:
        _kv(grid, key="Stack", value=f"{c.stack}" + (f" / {c.service}" if c.service else ""))
    _kv(
        grid,
        key="State",
        value=f"{c.status}"
        + (f" ({c.health})" if c.health else "")
        + (f", {c.restart_count} restarts" if c.restart_count else ""),
    )
    if c.port_map:
        _kv(
            grid,
            key="Ports",
            value=", ".join(f"{h}→{i}" for h, i in sorted(c.port_map.items())),
        )
    vols = (
        [m.name or m.source for m in c.mounts if m.type in ("volume", "bind")] if c.mounts else []
    )
    if vols:
        _kv(grid, key="Persist", value="yes: " + ", ".join(vols[:3]))
    else:
        _kv(grid, key="Persist", value=Text("no persistent volume", style="dim"))


def _cluster_rows(grid: Table, listener: Listener, *, kube: KubeState | None) -> None:
    """The Kubernetes context this machine talks to, and the forward this row is, if any."""
    if kube is not None and kube.available:
        if kube.context:
            _kv(grid, key="Context", value=kube.context)
        _kv(grid, key="Namespace", value=kube.namespace or "(all)")
        pods = Text(f"{len(kube.pods)} pods")
        unhealthy = len(kube.unhealthy_pods)
        if unhealthy:
            pods.append(f"  {unhealthy} unhealthy", style="bold red")
        _kv(grid, key="Pods", value=pods)
    tunnel = listener.tunnel or {}
    if tunnel.get("type") != "kubectl":
        return
    target = f"{tunnel.get('kind') or 'pod'}/{tunnel.get('name') or '?'}"
    _kv(
        grid, key="Forward", value=f"{target}:{tunnel.get('remote_port') or '?'} → :{listener.port}"
    )


def _hosts_rows(grid: Table, listener: Listener) -> None:
    """The remote side of a row: ssh session, tunnel target, reachability, proxy and domains."""
    ssh = listener.ssh
    if ssh is not None:
        _kv(
            grid,
            key="SSH",
            value=Text(f"{ssh.user}@{ssh.host}" if ssh.user else ssh.host, style="magenta"),
        )
        _kv(grid, key="Remote", value=ssh.remote or "-")
        if ssh.tty:
            _kv(grid, key="TTY", value=ssh.tty)
        if ssh.forwards:
            _kv(grid, key="Forwards", value=", ".join(ssh.forwards))
        if listener.health.checked:
            # The R screen keeps nothing; the stored result is the last H / reach check.
            _kv(
                grid,
                key="Reach",
                value=Text(
                    listener.health.summary,
                    style="green" if listener.health.ok else "bold red",
                ),
            )
    tunnel = listener.tunnel or {}
    if tunnel and tunnel.get("type") != "kubectl":
        hop = next(
            (
                f"{host}:{remote}"
                for local, host, remote in tunnel.get("forwards", ())
                if local == listener.port
            ),
            None,
        )
        _kv(grid, key="Tunnel", value=f"{tunnel.get('host') or 'ssh'} → {hop or '?'}")
    if listener.proxy_targets:
        _kv(grid, key="Forwards to", value=", ".join(str(p) for p in listener.proxy_targets))
    if listener.hosts:
        _kv(grid, key="Domains", value=", ".join(listener.hosts[:MAX_DOMAINS]))


_SECTION_ROWS: dict[Section, Callable[[Table, Listener], None]] = {
    Section.IDENTITY: _identity_rows,
    Section.PROCESS: _process_rows,
    Section.CONTAINER: _container_rows,
    Section.HOSTS: _hosts_rows,
}


def render_details(
    listener: Listener,
    *,
    sections: tuple[Section, ...] = DEFAULT_SECTIONS,
    kube: KubeState | None = None,
    memory: PortMemory | None = None,
) -> Group:
    """The side panel's detail block for one listener, one block per section in order."""
    title = Text()
    title.append(f"{listener.identity.service}", style="bold")
    title.append(f"  {listener.key}", style="dim")
    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=11)
    grid.add_column(overflow="fold")
    for section in sections:
        if section == Section.CLUSTER:
            _cluster_rows(grid, listener, kube=kube)
        elif section == Section.RUNTIME:
            _runtime_rows(grid, listener, memory=memory)
        elif section in _SECTION_ROWS:
            _SECTION_ROWS[section](grid, listener)
    if listener.hosts and Section.HOSTS not in sections:
        _kv(grid, key="Domains", value=", ".join(listener.hosts[:MAX_DOMAINS]))
    return Group(title, Text(""), grid)


def render_insights(listener: Listener) -> Group:
    """One line per insight, worst first, with its suggestion underneath."""
    parts: list[Any] = [Text("Insights", style="bold underline")]
    if not listener.insights:
        parts.append(Text(f"{GLYPH_OK} nothing unusual", style="green"))
    for ins in sorted(listener.insights, key=lambda i: -i.rank):
        marker, style = level_marker(ins.level)
        t = Text()
        t.append(f"{marker} ", style=style)
        t.append(ins.message, style=style if ins.level != Level.INFO else "")
        parts.append(t)
        if ins.suggestion:
            parts.append(Text(f"   → {ins.suggestion}", style="italic"))
    return Group(*parts)


__all__ = [
    "DEFAULT_SECTIONS",
    "Section",
    "SidePanel",
    "TopBar",
    "render_details",
    "render_insights",
]
