"""The ``lirts stack`` sub-app: docker compose projects."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

from lirts.cli.common import ConfigOpt, _build_config, _collect, console, err_console
from lirts.constants import LOG_TAIL_STACK
from lirts.insights import format_rate

stack_app = typer.Typer(help="Docker compose stacks: list, logs, restart, stop.")


@stack_app.command("list")
def stack_list(config: ConfigOpt = None) -> None:
    """List compose projects with their containers and published ports."""
    cfg = _build_config(config, udp=None, docker=True, probe=False, hide_system=None)
    engine, snap = _collect(cfg)
    try:
        stacks = engine.stacks()
        if not stacks:
            console.print(
                "No running containers." if snap.docker_available else "Docker is not available."
            )
            return
        for project, containers in stacks.items():
            console.print(f"[bold]{escape(project)}[/]  ({len(containers)} containers)")
            if containers[0].working_dir:
                console.print(f"    [dim]{escape(containers[0].working_dir)}[/]")
            for c in containers:
                ports = ", ".join(str(p) for p in c.host_ports) or "-"
                health = f" ({c.health})" if c.health else ""
                if c.net_rx_rate is not None:
                    health += f"  ↓{format_rate(c.net_rx_rate)} ↑{format_rate(c.net_tx_rate)}"
                console.print(
                    f"    {escape(c.service or c.name):<20} {escape(c.status)}{health:<12} ports {ports}"
                )
    finally:
        engine.close()


def _stack_action(project: str, *, action: str, config: Path | None, yes: bool) -> None:
    """Confirm and run ``action`` on every container of a compose project."""
    cfg = _build_config(config, udp=None, docker=True, probe=False, hide_system=None)
    engine, _ = _collect(cfg)
    try:
        containers = engine.stacks().get(project)
        if not containers:
            err_console.print(
                f"[red]No running containers for project '{project}'. Known: {', '.join(engine.stacks()) or 'none'}[/]"
            )
            raise typer.Exit(code=1)
        names = ", ".join(c.name for c in containers)
        if not yes and not typer.confirm(
            f"{action.capitalize()} {len(containers)} container(s): {names}?", default=False
        ):
            raise typer.Exit(code=0)
        failures = 0
        for name, ok, msg in engine.stack_action(project, action):
            console.print(f"  {escape(name)}: [{'green' if ok else 'red'}]{escape(msg)}[/]")
            failures += 0 if ok else 1
        if failures:
            raise typer.Exit(code=1)
    finally:
        engine.close()


@stack_app.command("restart")
def stack_restart(
    project: Annotated[str, typer.Argument(help="Compose project name.")],
    config: ConfigOpt = None,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    """Restart every container of a compose project."""
    _stack_action(project, action="restart", config=config, yes=yes)


@stack_app.command("stop")
def stack_stop(
    project: Annotated[str, typer.Argument(help="Compose project name.")],
    config: ConfigOpt = None,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    """Stop every container of a compose project."""
    _stack_action(project, action="stop", config=config, yes=yes)


@stack_app.command("logs")
def stack_logs(
    project: Annotated[str, typer.Argument(help="Compose project name.")],
    config: ConfigOpt = None,
    tail: Annotated[
        int, typer.Option("--tail", "-n", help="Lines per container.")
    ] = LOG_TAIL_STACK,
) -> None:
    """Interleaved logs of every container in a compose project."""
    cfg = _build_config(config, udp=None, docker=True, probe=False, hide_system=None)
    engine, _ = _collect(cfg)
    try:
        if project not in engine.stacks():
            err_console.print(f"[red]No running containers for project '{project}'[/]")
            raise typer.Exit(code=1)
        console.print(engine.stack_logs(project, tail), markup=False, highlight=False)
    finally:
        engine.close()
