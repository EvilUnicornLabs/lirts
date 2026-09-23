"""Read-only commands that print what is running.

``list``, ``explain``, ``watch``, ``graph``, ``routes`` and ``doctor``.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from lirts.cli.common import (
    ConfigOpt,
    DemoOpt,
    DockerOpt,
    ProbeOpt,
    ReplayOpt,
    SystemOpt,
    UdpOpt,
    _build_config,
    _collect,
    _listener_dict,
    _render_table,
    console,
)
from lirts.collectors.proxies import discover_routes
from lirts.config_view import ConfigView
from lirts.constants import GLYPH_ERROR, GLYPH_OK, GLYPH_WARNING, MIN_REFRESH_INTERVAL
from lirts.doctor import FAIL, OK, WARN, as_dict, run_all
from lirts.engine import Engine
from lirts.insights import explain, render_graph_text
from lirts.models import Level, Snapshot
from lirts.notify import notify_desktop

# Colour per event severity in `lirts watch`; info events are printed plain.
EVENT_STYLES: dict[str, str] = {Level.ERROR: "bold red", Level.WARNING: "yellow"}


def list_command(
    config: ConfigOpt = None,
    udp: UdpOpt = None,
    docker: DockerOpt = True,
    probe: ProbeOpt = True,
    hide_system: SystemOpt = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable JSON output.")] = False,
    filter_text: Annotated[
        str | None,
        typer.Option("--filter", "-f", help="Only rows matching these words.", show_default=False),
    ] = None,
    columns: Annotated[
        str | None,
        typer.Option("--columns", help="Comma-separated column list.", show_default=False),
    ] = None,
    port: Annotated[
        int | None, typer.Option("--port", "-p", help="Only this port.", show_default=False)
    ] = None,
    demo: DemoOpt = False,
    replay: ReplayOpt = None,
) -> None:
    """Print the current listeners once (for scripts, or a quick look)."""
    cfg = _build_config(config, udp=udp, docker=docker, probe=probe, hide_system=hide_system)
    engine, snap = _collect(cfg, samples=2, demo=demo, replay=replay)
    try:
        listeners = snap.listeners
        if port is not None:
            listeners = [x for x in listeners if x.port == port]
        if filter_text:
            terms = filter_text.lower().split()
            listeners = [x for x in listeners if all(t in x.search_blob() for t in terms)]
        view = Snapshot(
            listeners=listeners,
            system=snap.system,
            events=snap.events,
            timestamp=snap.timestamp,
            docker_available=snap.docker_available,
            container_count=snap.container_count,
        )
        if as_json:
            payload = {
                "timestamp": view.timestamp,
                "docker_available": view.docker_available,
                "summary": view.summary,
                "listeners": [_listener_dict(x) for x in listeners],
            }
            console.print_json(json.dumps(payload, default=str))
        else:
            console.print(
                _render_table(
                    view,
                    cfg=ConfigView(cfg),
                    columns=columns.split(",") if columns else None,
                )
            )
            problems = [(x, i) for x in listeners for i in x.insights if i.level != Level.INFO]
            if problems:
                console.print()
                for x, i in problems:
                    failed = i.level == Level.ERROR
                    style = "red" if failed else "yellow"
                    glyph = GLYPH_ERROR if failed else GLYPH_WARNING
                    console.print(f"[{style}]{glyph} {x.key} {x.identity.service}: {i.message}[/]")
    finally:
        engine.close()


def explain_command(
    config: ConfigOpt = None,
    udp: UdpOpt = None,
    docker: DockerOpt = True,
    probe: ProbeOpt = True,
    demo: DemoOpt = False,
    replay: ReplayOpt = None,
) -> None:
    """Summarise what is running on this machine and what deserves attention."""
    cfg = _build_config(config, udp=udp, docker=docker, probe=probe, hide_system=None)
    engine, snap = _collect(cfg, samples=2, demo=demo, replay=replay)
    try:
        if engine.kube.enabled:  # the CLI has no background thread; fetch once, synchronously
            snap.kube = engine.kube.fetch()
        console.print(explain(snap, engine.last_session_diff), markup=False, highlight=False)
    finally:
        engine.close()


def watch_command(
    config: ConfigOpt = None,
    interval: Annotated[
        float, typer.Option("--interval", "-i", help="Seconds between checks.")
    ] = 5.0,
    notify: Annotated[
        bool, typer.Option("--notify", help="Also send desktop notifications.")
    ] = False,
    filter_text: Annotated[
        str | None,
        typer.Option("--filter", "-f", help="Only events for matching rows.", show_default=False),
    ] = None,
    docker: DockerOpt = True,
    probe: ProbeOpt = True,
    count: Annotated[
        int | None,
        typer.Option(
            "--count", help="Stop after this many checks (for scripts).", show_default=False
        ),
    ] = None,
) -> None:
    """Print start / stop / restart / health events as they happen (Ctrl-C to quit)."""
    cfg = _build_config(config, udp=None, docker=docker, probe=probe, hide_system=None)
    cfg.setdefault("bandwidth", {})["mode"] = "off"
    engine = Engine(cfg)
    terms = filter_text.lower().split() if filter_text else []
    seen_ts = 0.0
    checks = 0
    console.print(f"Watching every {interval:g}s; Ctrl-C to stop.", style="dim")
    try:
        while True:
            snap = engine.refresh_sync()
            checks += 1
            if checks == 1:
                seen_ts = max((e.timestamp for e in snap.events), default=0.0)
                console.print(
                    f"{time.strftime('%H:%M:%S')}  {len(snap.listeners)} listeners, watching...",
                    style="dim",
                )
            else:
                for ev in snap.events:
                    if ev.timestamp <= seen_ts:
                        continue
                    if terms:
                        row = snap.by_port(ev.port) if ev.port is not None else None
                        blob = row.search_blob() if row else ev.message.lower()
                        if not all(t in blob for t in terms):
                            continue
                    style = EVENT_STYLES.get(ev.level, "")
                    stamp = time.strftime("%H:%M:%S", time.localtime(ev.timestamp))
                    console.print(f"{stamp}  {escape(ev.message)}", style=style)
                    if notify and ev.level != Level.INFO:
                        notify_desktop("lirts", ev.message)
                if snap.events:
                    seen_ts = max(seen_ts, snap.events[-1].timestamp)
            if count is not None and checks >= count:
                break
            time.sleep(max(MIN_REFRESH_INTERVAL, interval))
    except KeyboardInterrupt:
        pass
    finally:
        engine.close()


def graph_command(
    config: ConfigOpt = None,
    docker: DockerOpt = True,
    hide_system: SystemOpt = None,
) -> None:
    """Who talks to whom: local clients per port, proxy chains, tunnels and ssh sessions."""
    cfg = _build_config(config, udp=None, docker=docker, probe=False, hide_system=hide_system)
    engine, snap = _collect(cfg)
    try:
        console.print(render_graph_text(snap), markup=False, highlight=False)
    finally:
        engine.close()


def routes_command(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Virtual hosts of local nginx / httpd and where they send requests (nginx -T, httpd -S)."""
    routes, notes = discover_routes()
    if as_json:
        console.print_json(json.dumps({"routes": [asdict(r) for r in routes], "notes": notes}))
        return
    if not routes:
        console.print("No routes found. " + "; ".join(notes))
        return
    table = Table(box=None, pad_edge=False, header_style="bold", show_edge=False)
    for col in ("PROXY", "PORT", "HOST NAMES", "PATH", "UPSTREAM", "FILES / ROOT"):
        table.add_column(col, no_wrap=True)
    for r in routes:
        table.add_row(
            r.source,
            str(r.listen_port),
            escape(r.label),
            r.path,
            escape(f"127.0.0.1:{r.upstream_port}" if r.upstream_port else (r.upstream or "-")),
            escape(r.doc_root or "-"),
        )
    console.print(table)
    console.print("[dim]" + "; ".join(notes) + "[/]")


def doctor_command(
    config: ConfigOpt = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Check the environment: Python, config, state dir, socket access, Docker, kubectl, extras."""
    checks = run_all(config)
    if as_json:
        console.print_json(json.dumps([as_dict(c) for c in checks]))
    else:
        marker = {
            OK: (GLYPH_OK, "green"),
            WARN: (GLYPH_WARNING, "yellow"),
            FAIL: (GLYPH_ERROR, "red"),
        }
        for c in checks:
            sym, style = marker[c.status]
            console.print(f"[{style}]{sym}[/] [bold]{escape(c.name):<18}[/] {escape(c.detail)}")
            if c.hint and c.status != OK:
                console.print(f"    [dim]→ {escape(c.hint)}[/]")
    failures = sum(1 for c in checks if c.status == FAIL)
    raise typer.Exit(code=1 if failures else 0)
