"""``lirts daemon``: run the machine's one engine, and start it at login.

``lirts daemon`` (or ``lirts -d``) runs it in the foreground; ``install`` / ``uninstall``
make the service manager start it at login; ``status``, ``start`` and ``stop`` speak to it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.markup import escape

from lirts import daemon_install
from lirts.cli.common import (
    ConfigOpt,
    DebugOpt,
    DemoOpt,
    DockerOpt,
    ProbeOpt,
    _build_config,
    _setup_logging,
    console,
    err_console,
)
from lirts.config_view import ConfigView
from lirts.constants import DAEMON_START_WAIT
from lirts.daemon import DaemonServer
from lirts.daemon_client import DaemonClient, DaemonError, ping, socket_path
from lirts.demo import DemoEngine
from lirts.engine import Engine
from lirts.insights import format_duration
from lirts.mcp_server import http_url

daemon_app = typer.Typer(
    help="One engine for the whole machine, shared by dashboards, the CLI and lirts mcp.",
    invoke_without_command=True,
)

McpPortOpt = Annotated[
    int | None,
    typer.Option(
        "--mcp-port",
        help="Serve MCP to coding agents at http://127.0.0.1:<port>/mcp (daemon.mcp_port); 0 = off.",
        show_default=False,
    ),
]
AllowActionsOpt = Annotated[
    bool,
    typer.Option(
        "--allow-actions",
        help="The MCP endpoint also offers free_port, fix and restart; without it every tool is read-only.",
    ),
]
IdleOpt = Annotated[
    float | None,
    typer.Option(
        "--idle",
        help="Seconds between refreshes while no dashboard is attached (daemon.idle_interval).",
        show_default=False,
    ),
]


def strip_debug_env() -> None:
    """Drop macOS malloc debugging variables so every spawned command stays quiet.

    A launch agent can inherit ``MallocStackLogging`` and friends; each ``nettop`` or
    ``docker`` child then prints a notice to the daemon's log, thousands of times a day.
    """
    for key in [k for k in os.environ if k.startswith("Malloc")]:
        del os.environ[key]


def run_daemon(
    config: Path | None,
    *,
    docker: bool = True,
    probe: bool = True,
    idle: float | None = None,
    mcp_port: int | None = None,
    allow_actions: bool = False,
    demo: bool = False,
    debug: bool = False,
) -> None:
    """Run the daemon in the foreground until it is stopped (also ``lirts -d``)."""
    _setup_logging(debug)
    # The mcp SDK echoes the root logger to stderr (launchd's daemon.log); lirts's own log
    # already goes to the state directory, so its records must not propagate there.
    logging.getLogger("lirts").propagate = False
    strip_debug_env()
    cfg = _build_config(config, udp=True, docker=docker, probe=probe, hide_system=False)
    view = ConfigView(cfg)
    interval = idle if idle is not None else view.daemon.idle_interval
    port = mcp_port if mcp_port is not None else view.daemon.mcp_port
    engine: Engine = DemoEngine(cfg) if demo else Engine(cfg)
    server = DaemonServer(
        engine,
        socket_path=socket_path(),
        idle_interval=interval,
        mcp_port=port,
        allow_actions=allow_actions,
    )
    err_console.print(
        f"lirts daemon on {escape(str(server.socket_path))}, idle refresh every {interval:g}s"
        + (f", MCP at {http_url(port)}" if port else "")
        + (" with actions" if allow_actions and port else "")
        + (" (demo machine)" if demo else "")
        + "; Ctrl-C stops"
    )
    try:
        asyncio.run(server.serve())
    except RuntimeError as exc:
        err_console.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(code=1) from exc


@daemon_app.callback()
def daemon_main(
    ctx: typer.Context,
    config: ConfigOpt = None,
    docker: DockerOpt = True,
    probe: ProbeOpt = True,
    idle: IdleOpt = None,
    mcp_port: McpPortOpt = None,
    allow_actions: AllowActionsOpt = False,
    debug: DebugOpt = False,
    demo: DemoOpt = False,
) -> None:
    """Run the daemon in the foreground (no sub-command), or manage the installed one."""
    if ctx.invoked_subcommand is not None:
        return
    run_daemon(
        config,
        docker=docker,
        probe=probe,
        idle=idle,
        mcp_port=mcp_port,
        allow_actions=allow_actions,
        demo=demo,
        debug=debug,
    )


def _print_status(info: dict[str, Any]) -> None:
    up = format_duration(time.time() - float(info.get("started", time.time())))
    seen = info.get("last_client") or 0.0
    client = format_duration(time.time() - seen) + " ago" if seen else "never"
    console.print(
        f"running  PID {info.get('pid')}  lirts {info.get('version')}  up {up}  "
        f"{info.get('refreshes')} refreshes  idle every {info.get('idle_interval')}s  "
        f"last client {client}" + ("  DEMO" if info.get("demo") else "")
    )
    url = info.get("mcp_url")
    console.print(
        f"MCP: {escape(str(url))}" + ("  (actions allowed)" if info.get("allow_actions") else "")
        if url
        else "MCP: off (daemon.mcp_port = 0, or the mcp SDK is missing)"
    )


BREW_HINT = (
    "this lirts comes from Homebrew; its daemon is a brew service: "
    "brew services start lirts (stop | restart likewise)"
)


@daemon_app.command("status")
def daemon_status() -> None:
    """Is the daemon running, and is it installed to start at login? Exit 1 when not running."""
    mgr = daemon_install.manager()
    if daemon_install.from_homebrew():
        brew_file = daemon_install.brew_service_file()
        if brew_file is not None:
            console.print(f"login start: brew services ({brew_file.stem}, started at login)")
        else:
            console.print("login start: not started: brew services start lirts")
        if mgr is not None and mgr.file.exists():
            console.print(
                f"[yellow]a lirts daemon install agent also exists ({escape(str(mgr.file))}); "
                "two login items start two daemons: run `lirts daemon uninstall` so only the "
                "brew service remains[/]"
            )
    elif mgr is not None:
        state = "installed" if mgr.file.exists() else "not installed"
        console.print(f"login start: {state} ({mgr.kind}, {escape(str(mgr.file))})")
    info = ping(socket_path())
    if info is None:
        console.print(f"not running (socket {escape(str(socket_path()))})")
        raise typer.Exit(code=1)
    _print_status(info)


@daemon_app.command("stop")
def daemon_stop() -> None:
    """Ask the running daemon to stop (an installed one comes back at the next login)."""
    info = ping(socket_path())
    if info is None:
        console.print("not running")
        return
    try:
        DaemonClient(socket_path()).call_sync("stop")
    except DaemonError as exc:
        err_console.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(code=1) from exc
    console.print(f"stopped PID {info.get('pid')}")


def _manager_or_exit() -> daemon_install.Manager:
    mgr = daemon_install.manager()
    if mgr is None:
        err_console.print("[red]login start is supported on macOS (launchd) and Linux (systemd)[/]")
        raise typer.Exit(code=1)
    return mgr


def _report(ok: bool, message: str) -> None:
    console.print(escape(message) if ok else f"[red]{escape(message)}[/]")
    if not ok:
        raise typer.Exit(code=1)


@daemon_app.command("install")
def daemon_install_command() -> None:
    """Start the daemon at every login (launchd agent on macOS, systemd user unit on Linux)."""
    mgr = _manager_or_exit()
    exe = daemon_install.executable()
    if exe is None:
        err_console.print("[red]cannot find the lirts command to register[/]")
        raise typer.Exit(code=1)
    if daemon_install.from_homebrew(exe):
        console.print(BREW_HINT)
        return
    _report(*daemon_install.install(mgr, exe=exe))
    console.print(f"registered {escape(exe)}")
    info = wait_for_daemon()
    if info is None:
        console.print("[yellow]not answering yet; check `lirts daemon status` in a moment[/]")
        return
    _print_status(info)


def wait_for_daemon(
    timeout: float = DAEMON_START_WAIT, pause: float = 0.25
) -> dict[str, Any] | None:
    """Poll the socket until the freshly started daemon answers, or give up."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = ping(socket_path())
        if info is not None:
            return info
        time.sleep(pause)
    return None


@daemon_app.command("uninstall")
def daemon_uninstall_command() -> None:
    """Stop starting the daemon at login and remove the service file."""
    mgr = _manager_or_exit()
    if not mgr.file.exists() and daemon_install.from_homebrew():
        console.print(BREW_HINT.replace("start lirts", "stop lirts"))
        return
    _report(*daemon_install.uninstall(mgr))


@daemon_app.command("start")
def daemon_start_command() -> None:
    """Start the installed daemon now."""
    if daemon_install.from_homebrew():
        console.print(BREW_HINT)
        return
    _report(*daemon_install.start(_manager_or_exit()))
