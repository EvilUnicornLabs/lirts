"""``lirts mcp``: serve the engine to a coding agent over the Model Context Protocol."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

import typer
from rich.markup import escape

from lirts.cli.common import (
    ConfigOpt,
    DebugOpt,
    DemoOpt,
    DockerOpt,
    ProbeOpt,
    ReplayOpt,
    _build_config,
    _make_engine,
    _setup_logging,
    err_console,
)
from lirts.constants import MIN_REFRESH_INTERVAL
from lirts.mcp_server import build_server, mcp_available, serve_stdio

INSTALL_HINT = (
    "the MCP server needs the mcp SDK: pip install 'lirts[mcp]'  (or pipx inject lirts mcp)"
)


def mcp_command(
    config: ConfigOpt = None,
    docker: DockerOpt = True,
    probe: ProbeOpt = True,
    refresh: Annotated[
        float | None,
        typer.Option("--refresh", "-r", help="Refresh interval in seconds.", show_default=False),
    ] = None,
    allow_actions: Annotated[
        bool,
        typer.Option(
            "--allow-actions",
            help="Also offer free_port, fix and restart; without it every tool is read-only.",
        ),
    ] = False,
    debug: DebugOpt = False,
    demo: DemoOpt = False,
    replay: ReplayOpt = None,
) -> None:
    """Serve lirts to a coding agent over MCP (stdio): who, list_listeners, explain, topology…"""
    if not mcp_available():
        err_console.print(f"[red]{escape(INSTALL_HINT)}[/]")
        raise typer.Exit(code=2)
    _setup_logging(debug)
    # The SDK echoes the root logger to stderr; lirts's log already goes to the state directory.
    logging.getLogger("lirts").propagate = False
    cfg = _build_config(
        config, udp=None, docker=docker, probe=probe, hide_system=None, refresh=refresh
    )
    # Per-process bandwidth sampling is the dashboard's business, not an agent's.
    cfg.setdefault("bandwidth", {})["mode"] = "off"
    interval = max(MIN_REFRESH_INTERVAL, float(cfg["refresh_interval"]))
    engine = _make_engine(cfg, demo=demo, replay=replay)
    server = build_server(engine, refresh_interval=interval, allow_actions=allow_actions)
    asyncio.run(serve_stdio(server))
