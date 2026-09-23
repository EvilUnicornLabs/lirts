"""``lirts who`` and ``lirts free``: who holds a port, and getting it back.

``who`` answers the question that costs the most time when a server refuses to start:
what is on this port, is it a leftover, and what usually lives here.  ``free`` shows the
same and then hands the port back, after a confirmation that says exactly what will run.
"""

from __future__ import annotations

import time
from typing import Annotated

import typer
from rich.markup import escape

from lirts.cli.common import (
    ConfigOpt,
    DemoOpt,
    DockerOpt,
    ReplayOpt,
    _build_config,
    _collect,
    console,
    err_console,
)
from lirts.constants import GLYPH_WARNING
from lirts.engine import Engine
from lirts.identity import role_label
from lirts.insights import format_duration
from lirts.insights_ports import leftover_reason
from lirts.models import Level, Listener, ListenerProcess, is_docker_proxy_name
from lirts.ports import PortMemory, last_seen_text, listener_holder
from lirts.tui.render import level_marker

PortArg = Annotated[int, typer.Argument(help="The port to look at.")]
ProtocolOpt = Annotated[str, typer.Option("--protocol", help="TCP or UDP.")]
YesOpt = Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")]
ForceOpt = Annotated[bool, typer.Option("--force", "-9", help="Send SIGKILL instead of SIGTERM.")]

# Width of the label column of the printed block.
_LABEL_WIDTH = 11


def _row(label: str, value: str, *, style: str = "") -> None:
    """One ``Label  value`` line of the block; ``value`` is already escaped."""
    text = f"[{style}]{value}[/]" if style else value
    console.print(f"  [bold cyan]{label:<{_LABEL_WIDTH}}[/]{text}")


def _holder_process(listener: Listener) -> ListenerProcess | None:
    """The process that really serves the port, not Docker's forwarder in front of it."""
    return listener.real_processes[0] if listener.real_processes else listener.primary


def _holder_line(listener: Listener) -> str:
    """Process, PID, user and uptime of whatever holds the port."""
    proc = _holder_process(listener)
    container = listener.container
    if proc is None or (is_docker_proxy_name(proc.name) and container is not None):
        if container is None:
            return "no host process"
        via = f" via {proc.name} (PID {proc.pid})" if proc is not None else ""
        return f"container {container.name}{via}"
    parts = [proc.name, f"PID {proc.pid}"]
    if proc.user:
        parts.append(proc.user)
    uptime = listener.uptime_seconds
    if uptime is not None:
        parts.append(f"up {format_duration(uptime)}")
    return "  ".join(parts)


def _print_holder(listener: Listener, *, memory: PortMemory, now: float) -> None:
    """The whole block ``who`` prints, and ``free`` shows before it asks."""
    service, project = listener_holder(listener)
    head = f"{listener.key}  {service}" + (f"  ({project})" if project else "")
    console.print(f"[bold]{escape(head)}[/]")
    _row("Holder", escape(_holder_line(listener)))
    _row("Role", escape(f"{role_label(listener.identity.role)} · {listener.source}"))
    proc = _holder_process(listener)
    if proc is not None and not is_docker_proxy_name(proc.name):
        _row("Command", escape(proc.command[:200]), style="dim")
    container = listener.container
    if container is not None:
        stack = f" (stack {container.stack})" if container.stack else ""
        _row("Container", escape(f"{container.name}{stack} · {container.status}"))
    reason = leftover_reason(listener, memory=memory)
    if reason is not None:
        _row("Left over", escape(reason), style="yellow")
    usual = memory.usual(listener.port)
    if usual is not None:
        _row(
            "Usually",
            escape(
                f"{usual.label} · {usual.seen_text} · last {last_seen_text(usual.last_seen, now)}"
            ),
        )
    elif memory:
        _row("Usually", "this is the first time lirts sees this port", style="dim")
    others = memory.also_used_by(listener.port)
    if others:
        _row("Also used", escape(", ".join(f"{h.label} (seen {h.seen_text})" for h in others)))
    for insight in sorted(listener.insights, key=lambda i: -i.rank):
        marker, style = level_marker(insight.level)
        body = escape(insight.message)
        console.print(f"  [{style}]{marker}[/] {body}" if style else f"  {marker} {body}")
        if insight.suggestion:
            console.print(f"     [italic]→ {escape(insight.suggestion)}[/]")


def who_command(
    port: PortArg,
    config: ConfigOpt = None,
    protocol: ProtocolOpt = "TCP",
    docker: DockerOpt = True,
    demo: DemoOpt = False,
    replay: ReplayOpt = None,
) -> None:
    """Who holds PORT, what usually holds it, and whether it looks left over. Exit 1 if free."""
    proto = protocol.upper()
    cfg = _build_config(config, udp=proto == "UDP", docker=docker, probe=False, hide_system=False)
    engine, snap = _collect(cfg, samples=2, demo=demo, replay=replay)
    try:
        listener = snap.by_port(port, proto)
        now = time.time()
        if listener is None:
            usual = engine.port_memory.usual(port)
            tail = f"; usually {usual.label}" if usual else ""
            err_console.print(f"[yellow]nothing on {port}{escape(tail)}[/]")
            raise typer.Exit(code=1)
        _print_holder(listener, memory=engine.port_memory, now=now)
    finally:
        engine.close()


def _free_plan(listener: Listener) -> tuple[list[int], list[str]]:
    """The PIDs to terminate and the words describing everything that will happen."""
    pids = [p.pid for p in listener.real_processes]
    steps = [f"terminate {p.name} (PID {p.pid})" for p in listener.real_processes]
    if listener.container is not None:
        steps.append(f"docker stop {listener.container.name}")
    return pids, steps


def free_command(
    port: PortArg,
    config: ConfigOpt = None,
    protocol: ProtocolOpt = "TCP",
    docker: DockerOpt = True,
    yes: YesOpt = False,
    force: ForceOpt = False,
    demo: DemoOpt = False,
    replay: ReplayOpt = None,
) -> None:
    """Hand PORT back: terminate the process holding it and stop its container."""
    proto = protocol.upper()
    cfg = _build_config(config, udp=proto == "UDP", docker=docker, probe=False, hide_system=False)
    engine, snap = _collect(cfg, samples=2, demo=demo, replay=replay)
    try:
        listener = snap.by_port(port, proto)
        if listener is None:
            console.print(f"{port}/{proto} is already free")
            return
        _print_holder(listener, memory=engine.port_memory, now=time.time())
        pids, steps = _free_plan(listener)
        if not steps:
            err_console.print(f"[yellow]nothing on {port} that lirts can stop[/]")
            raise typer.Exit(code=2)
        for step in steps:
            console.print(f"  [yellow]{GLYPH_WARNING} {escape(step)}[/]")
        if demo or replay:
            mode = "replay" if replay else "demo"
            err_console.print(f"[yellow]{mode} mode: actions are disabled[/]")
            raise typer.Exit(code=2)
        if not yes and not typer.confirm(f"Free port {port}?", default=False):
            raise typer.Exit(code=0)
        failures = _run_free(engine, listener, pids=pids, force=force)
        if failures:
            raise typer.Exit(code=1)
    finally:
        engine.close()


def _run_free(engine: Engine, listener: Listener, *, pids: list[int], force: bool) -> int:
    """Carry out the plan through the engine's own kill and Docker paths; count failures."""
    failures = 0
    if pids:
        for pid, ok, msg in engine.kill_and_wait(pids, force=force):
            console.print(f"  PID {pid}: [{'green' if ok else 'red'}]{escape(msg)}[/]")
            failures += 0 if ok else 1
    container = listener.container
    if container is not None:
        ok, msg = engine.docker.stop(container.id)
        console.print(f"  {escape(container.name)}: [{'green' if ok else 'red'}]{escape(msg)}[/]")
        failures += 0 if ok else 1
    engine.history.note(
        f"[-] {listener.identity.service} on {listener.key}: freed by you (lirts free)",
        port=listener.port,
        level=Level.INFO if not failures else Level.WARNING,
    )
    return failures


__all__ = ["free_command", "who_command"]
