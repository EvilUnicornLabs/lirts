"""Typer command-line interface.

``lirts`` alone launches the TUI.  Sub-commands provide scriptable output
(``list --json``), one-shot actions (``kill``, ``explain``) and
configuration helpers.

The command modules hold the functions; this module assembles them into the
Typer app.  The order of the registrations below is the order ``lirts --help``
prints the commands in, so it is kept deliberately rather than left to the
order the modules happen to be imported in.
"""

from __future__ import annotations

import typer

from lirts.cli.actions import kill_command, restart_command
from lirts.cli.common import (
    _build_config,
    _collect,
    _listener_dict,
    _make_engine,
    _render_table,
    _setup_logging,
    console,
    err_console,
)
from lirts.cli.config_cmd import config_app
from lirts.cli.daemon import daemon_app
from lirts.cli.health import health_command, patterns_command, reach_command
from lirts.cli.kube import _kube, kube_app
from lirts.cli.listing import (
    doctor_command,
    explain_command,
    graph_command,
    list_command,
    routes_command,
    watch_command,
)
from lirts.cli.mcp import mcp_command
from lirts.cli.ports import free_command, who_command
from lirts.cli.root import main, record_command, setup_command, tui_command
from lirts.cli.stack import stack_app
from lirts.cli.topology import topology_command

app = typer.Typer(
    help="A btop-style dashboard for ports, processes, Docker containers and the services behind them.",
    no_args_is_help=False,
    add_completion=True,
    rich_markup_mode="rich",
)

app.callback(invoke_without_command=True)(main)
app.command("tui")(tui_command)
app.command("record")(record_command)
app.command("setup")(setup_command)
app.command("list")(list_command)
app.command("kill")(kill_command)
app.command("explain")(explain_command)
app.command("restart")(restart_command)
app.command("watch")(watch_command)
app.command("health")(health_command)
app.command("patterns")(patterns_command)
app.command("reach")(reach_command)
app.command("graph")(graph_command)
app.command("who")(who_command)
app.command("free")(free_command)
app.command("topology")(topology_command)
app.command("routes")(routes_command)
app.command("doctor")(doctor_command)
app.command("mcp")(mcp_command)

app.add_typer(config_app, name="config")
app.add_typer(daemon_app, name="daemon")
app.add_typer(stack_app, name="stack")
app.add_typer(kube_app, name="kube")


def _entry() -> None:  # pragma: no cover - console script shim
    """The ``lirts`` console script."""
    app()


__all__ = [
    "_build_config",
    "_collect",
    "_entry",
    "_kube",
    "_listener_dict",
    "_make_engine",
    "_render_table",
    "_setup_logging",
    "app",
    "config_app",
    "console",
    "daemon_app",
    "doctor_command",
    "err_console",
    "explain_command",
    "free_command",
    "graph_command",
    "health_command",
    "kill_command",
    "kube_app",
    "list_command",
    "main",
    "mcp_command",
    "patterns_command",
    "reach_command",
    "record_command",
    "restart_command",
    "routes_command",
    "setup_command",
    "stack_app",
    "topology_command",
    "tui_command",
    "watch_command",
    "who_command",
]
