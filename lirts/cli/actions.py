"""Commands that change what is running: ``kill`` and ``restart``."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.markup import escape

from lirts.cli.common import ConfigOpt, _build_config, _collect, console, err_console
from lirts.constants import GLYPH_WARNING
from lirts.insights import blast_radius


def kill_command(
    port: Annotated[int, typer.Argument(help="Port whose process(es) should be killed.")],
    config: ConfigOpt = None,
    force: Annotated[
        bool, typer.Option("--force", "-9", help="Send SIGKILL instead of SIGTERM.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
    protocol: Annotated[str, typer.Option("--protocol", help="TCP or UDP.")] = "TCP",
) -> None:
    """Kill whatever is listening on PORT, after showing the blast radius."""
    cfg = _build_config(
        config, udp=protocol.upper() == "UDP", docker=True, probe=False, hide_system=None
    )
    engine, snap = _collect(cfg)
    try:
        lst = snap.by_port(port, protocol.upper())
        if lst is None:
            err_console.print(f"[red]Nothing is listening on {port}/{protocol.upper()}[/]")
            raise typer.Exit(code=1)
        if not lst.processes:
            err_console.print(
                f"[yellow]Port {port} is published by container {lst.container.name if lst.container else '?'} without a host process; use docker stop.[/]"
            )
            raise typer.Exit(code=2)
        console.print(
            f"[bold]{lst.identity.service}[/] on {lst.key}: "
            + ", ".join(f"{p.name} (PID {p.pid})" for p in lst.processes)
        )
        for line in blast_radius(lst, snap):
            console.print(f"  [yellow]{GLYPH_WARNING} {line}[/]")
        if not yes and not typer.confirm(
            f"Send {'SIGKILL' if force else 'SIGTERM'} to {len(lst.pids)} process(es)?",
            default=False,
        ):
            raise typer.Exit(code=0)
        results = engine.kill_and_wait(lst.pids, force=force)
        failures = 0
        for pid, ok, msg in results:
            console.print(f"  PID {pid}: [{'green' if ok else 'red'}]{msg}[/]")
            failures += 0 if ok else 1
        if failures:
            raise typer.Exit(code=1)
    finally:
        engine.close()


def restart_command(
    port: Annotated[int, typer.Argument(help="Port whose service should be restarted.")],
    config: ConfigOpt = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
    force: Annotated[bool, typer.Option("--force", "-9", help="SIGKILL the old process.")] = False,
) -> None:
    """Restart the service on PORT: containers via Docker, local processes by re-running their command."""
    cfg = _build_config(config, udp=None, docker=True, probe=False, hide_system=None)
    engine, snap = _collect(cfg)
    try:
        lst = snap.by_port(port)
        if lst is None:
            err_console.print(f"[red]Nothing is listening on {port}/TCP[/]")
            raise typer.Exit(code=1)
        if lst.container:
            if not yes and not typer.confirm(
                f"Restart container {lst.container.name}?", default=True
            ):
                raise typer.Exit(code=0)
            ok, msg = engine.docker.restart(lst.container.id)
            console.print(f"[{'green' if ok else 'red'}]{escape(msg)}[/]")
            raise typer.Exit(code=0 if ok else 1)
        plan = engine.restart_plan(lst)
        if plan is None:
            err_console.print(
                "[red]No command line recorded for this process (permission denied?)[/]"
            )
            raise typer.Exit(code=2)
        cmdline, cwd = plan
        console.print(f"[bold]{escape(lst.identity.service)}[/] on {lst.key}")
        console.print(f"  command: {escape(' '.join(cmdline))}")
        console.print(f"  cwd:     {escape(cwd or '(unknown)')}")
        for line in blast_radius(lst, snap):
            console.print(f"  [yellow]{GLYPH_WARNING} {escape(line)}[/]")
        if not yes and not typer.confirm("Kill and re-run this command?", default=False):
            raise typer.Exit(code=0)
        ok, msg = engine.restart_process(lst, force=force)
        console.print(f"[{'green' if ok else 'red'}]{escape(msg)}[/]")
        if not ok:
            raise typer.Exit(code=1)
    finally:
        engine.close()
