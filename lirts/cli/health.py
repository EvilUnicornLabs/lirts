"""Checking commands: ``health``, ``patterns`` and ``reach``."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from lirts.cli.common import ConfigOpt, DockerOpt, SystemOpt, _build_config, _collect, console
from lirts.collectors.reach import check_host
from lirts.config import load_config, state_dir
from lirts.config_view import ConfigView
from lirts.constants import GLYPH_ERROR, GLYPH_OK, PATTERN_SERVICES_SHOWN, PING_TIMEOUT
from lirts.filtering import apply_filter
from lirts.patterns import PatternStore


def health_command(
    config: ConfigOpt = None,
    docker: DockerOpt = True,
    hide_system: SystemOpt = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    filter_text: Annotated[
        str | None,
        typer.Option("--filter", "-f", help="Only rows matching this filter.", show_default=False),
    ] = None,
) -> None:
    """Check every listener: TCP connect latency and, for HTTP services, a health endpoint. Exit 1 if any fail."""
    cfg = _build_config(config, udp=None, docker=docker, probe=True, hide_system=hide_system)
    engine, snap = _collect(cfg)
    try:
        listeners = apply_filter(snap.listeners, filter_text) if filter_text else snap.listeners
        listeners = [x for x in listeners if x.state == "LISTEN"]
        if not any(x.health.checked for x in listeners):
            asyncio.run(engine.check_health_now(listeners))
        if as_json:
            payload = [
                {
                    "port": x.port,
                    "service": x.identity.service,
                    "project": x.identity.project,
                    "ok": x.health.ok,
                    "tcp_ok": x.health.tcp_ok,
                    "tcp_latency_ms": x.health.tcp_latency_ms,
                    "tcp_error": x.health.tcp_error,
                    "http_path": x.health.http_path,
                    "http_status": x.health.http_status,
                    "http_latency_ms": x.health.http_latency_ms,
                    "docker_health": x.container.health if x.container else None,
                }
                for x in listeners
            ]
            console.print_json(json.dumps(payload))
        else:
            table = Table(box=None, pad_edge=False, header_style="bold", show_edge=False)
            for col in ("PORT", "SERVICE", "PROJECT", "TCP", "HTTP", "DOCKER", "STATUS"):
                table.add_column(col, no_wrap=True)
            for x in listeners:
                h = x.health
                tcp = (
                    f"{h.tcp_latency_ms:.0f} ms"
                    if h.tcp_ok and h.tcp_latency_ms is not None
                    else (h.tcp_error or "-")
                )
                http = (
                    f"{h.http_status} {h.http_path} ({h.http_latency_ms:.0f} ms)"
                    if h.http_path and h.http_latency_ms is not None
                    else (h.http_note or "-")
                )
                dock = (
                    x.container.health or ("running" if x.container else "-")
                    if x.container
                    else "-"
                )
                ok = h.ok and (not x.container or x.container.health != "unhealthy")
                table.add_row(
                    str(x.port),
                    x.identity.service,
                    x.identity.project or "-",
                    tcp,
                    http,
                    dock,
                    f"[green]{GLYPH_OK} ok[/]" if ok else f"[bold red]{GLYPH_ERROR} failing[/]",
                )
            console.print(table)
        failing = [
            x
            for x in listeners
            if not x.health.ok or (x.container and x.container.health == "unhealthy")
        ]
        if failing:
            raise typer.Exit(code=1)
    finally:
        engine.close()


def patterns_command(
    config: ConfigOpt = None,
    min_count: Annotated[int, typer.Option("--min", help="Report from this many occurrences.")] = 0,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    clear: Annotated[
        bool, typer.Option("--clear", help="Forget every remembered pattern.")
    ] = False,
) -> None:
    """Recurring issues remembered across runs: conflicts, restarts, kills per port."""
    insights = ConfigView(load_config(config)).insights
    store = PatternStore(
        path=state_dir() / "patterns.json",
        persist=True,
        days=insights.pattern_days,
        min_count=insights.pattern_min,
    )
    store.load()
    if clear:
        store.clear()
        console.print("Forgot every remembered pattern.")
        return
    items = store.recurring(min_count or None)
    if as_json:
        console.print_json(json.dumps([p.to_dict() | {"count": p.count} for p in items]))
        return
    if not items:
        console.print(
            f"Nothing recurring in the last {store.days} days "
            f"(threshold {min_count or store.min_count}; lirts learns while it runs)."
        )
        return
    table = Table(box=None, pad_edge=False, header_style="bold", show_edge=False)
    for col in ("PORT", "WHAT", "TIMES", "DAYS", "LAST", "INVOLVED"):
        table.add_column(col, no_wrap=True)
    for p in items:
        table.add_row(
            p.key,
            p.label,
            str(p.count),
            str(p.active_days),
            time.strftime("%m-%d %H:%M", time.localtime(p.last)),
            escape(", ".join(p.services[:PATTERN_SERVICES_SHOWN]) or "-"),
        )
    console.print(table)
    console.print(f"[dim]window {store.days} days · state {state_dir() / 'patterns.json'}[/]")


def reach_command(
    host: Annotated[str, typer.Argument(help="Host name or address.")],
    port: Annotated[int | None, typer.Argument(help="TCP port to connect to.")] = None,
    timeout: Annotated[float, typer.Option("--timeout", help="Seconds per step.")] = PING_TIMEOUT,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """DNS, one ping and a TCP connect to a remote host. Exit 1 when not reachable."""
    result = check_host(host, port=port, timeout=timeout)
    if as_json:
        console.print_json(json.dumps(asdict(result)))
    else:
        for label, text, ok in result.lines():
            marker = {
                True: f"[green]{GLYPH_OK}[/]",
                False: f"[bold red]{GLYPH_ERROR}[/]",
                None: " ",
            }[ok]
            console.print(f"{marker} {label:<10} {escape(text)}")
        console.print("[green]reachable[/]" if result.ok else "[bold red]not reachable[/]")
    if not result.ok:
        raise typer.Exit(code=1)
