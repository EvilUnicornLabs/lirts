"""``lirts daemon``: run the machine's one engine, and start it at login.

``lirts daemon`` (or ``lirts -d``) runs it in the foreground; ``install`` / ``uninstall``
make the service manager start it at login; ``status``, ``start`` and ``stop`` speak to it.
"""

from __future__ import annotations

import asyncio
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
from lirts.daemon import DaemonServer
from lirts.daemon_client import DaemonClient, DaemonError, ping, socket_path
from lirts.demo import DemoEngine
from lirts.engine import Engine
from lirts.insights import format_duration

daemon_app = typer.Typer(
    help="One engine for the whole machine, shared by dashboards, the CLI and lirts mcp.",
    invoke_without_command=True,
)

IdleOpt = Annotated[
    float | None,
    typer.Option(
        "--idle",
        help="Seconds between refreshes while no dashboard is attached (daemon.idle_interval).",
        show_default=False,
    ),
]


def run_daemon(
    config: Path | None,
    *,
    docker: bool = True,
    probe: bool = True,
    idle: float | None = None,
    demo: bool = False,
    debug: bool = False,
) -> None:
    """Run the daemon in the foreground until it is stopped (also ``lirts -d``)."""
    _setup_logging(debug)
    cfg = _build_config(config, udp=True, docker=docker, probe=probe, hide_system=False)
    view = ConfigView(cfg)
    interval = idle if idle is not None else view.daemon.idle_interval
    engine: Engine = DemoEngine(cfg) if demo else Engine(cfg)
    server = DaemonServer(engine, socket_path=socket_path(), idle_interval=interval)
    err_console.print(
        f"lirts daemon on {escape(str(server.socket_path))}, idle refresh every {interval:g}s"
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
    debug: DebugOpt = False,
    demo: DemoOpt = False,
) -> None:
    """Run the daemon in the foreground (no sub-command), or manage the installed one."""
    if ctx.invoked_subcommand is not None:
        return
    run_daemon(config, docker=docker, probe=probe, idle=idle, demo=demo, debug=debug)


def _print_status(info: dict[str, Any]) -> None:
    up = format_duration(time.time() - float(info.get("started", time.time())))
    seen = info.get("last_client") or 0.0
    client = format_duration(time.time() - seen) + " ago" if seen else "never"
    console.print(
        f"running  PID {info.get('pid')}  lirts {info.get('version')}  up {up}  "
        f"{info.get('refreshes')} refreshes  idle every {info.get('idle_interval')}s  "
        f"last client {client}" + ("  DEMO" if info.get("demo") else "")
    )


@daemon_app.command("status")
def daemon_status() -> None:
    """Is the daemon running, and is it installed to start at login? Exit 1 when not running."""
    mgr = daemon_install.manager()
    if mgr is not None:
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
        err_console.print("[red]lirts is not on PATH; install it with pipx first[/]")
        raise typer.Exit(code=1)
    _report(*daemon_install.install(mgr, exe=exe))


@daemon_app.command("uninstall")
def daemon_uninstall_command() -> None:
    """Stop starting the daemon at login and remove the service file."""
    _report(*daemon_install.uninstall(_manager_or_exit()))


@daemon_app.command("start")
def daemon_start_command() -> None:
    """Start the installed daemon now."""
    _report(*daemon_install.start(_manager_or_exit()))
