"""Commands that open the dashboard or record what it sees.

The root callback (``lirts`` with no sub-command), ``tui``, ``record`` and ``setup``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

import lirts
from lirts import __version__
from lirts.cli.common import (
    ConfigOpt,
    DebugOpt,
    DemoOpt,
    DockerOpt,
    ProbeOpt,
    ReplayOpt,
    StandaloneOpt,
    SystemOpt,
    UdpOpt,
    _build_config,
    _make_engine,
    _setup_logging,
    apply_color_depth,
    console,
    err_console,
)
from lirts.cli.daemon import run_daemon
from lirts.engine import Engine
from lirts.models import Snapshot
from lirts.replay import parse_duration, record


def main(
    ctx: typer.Context,
    config: ConfigOpt = None,
    udp: UdpOpt = None,
    docker: DockerOpt = True,
    probe: ProbeOpt = True,
    hide_system: SystemOpt = None,
    refresh: Annotated[
        float | None,
        typer.Option("--refresh", "-r", help="Refresh interval in seconds.", show_default=False),
    ] = None,
    theme: Annotated[
        str | None,
        typer.Option("--theme", help="Textual theme name for this run.", show_default=False),
    ] = None,
    debug: DebugOpt = False,
    version: Annotated[
        bool, typer.Option("--version", "-V", help="Print the version and exit.")
    ] = False,
    demo: DemoOpt = False,
    replay: ReplayOpt = None,
    standalone: StandaloneOpt = False,
    daemon: Annotated[
        bool,
        typer.Option("--daemon", "-d", help="Run the daemon in the foreground (lirts daemon)."),
    ] = False,
) -> None:
    """Launch the interactive dashboard (default) or run a sub-command."""
    if version:
        console.print(f"lirts {__version__}  ({Path(lirts.__file__).parent})")
        raise typer.Exit()
    if ctx.invoked_subcommand is not None:
        return
    if daemon:
        run_daemon(config, docker=docker, probe=probe, demo=demo, debug=debug)
        return
    _setup_logging(debug)
    cfg = _build_config(
        config, udp=udp, docker=docker, probe=probe, hide_system=hide_system, refresh=refresh
    )
    if theme:
        cfg["theme"] = theme
    apply_color_depth(cfg)
    # Textual is a heavy import: the sub-commands that only print text must not pay for it.
    from lirts.tui.app import run_app

    run_app(_make_engine(cfg, demo=demo, replay=replay, standalone=standalone), config=cfg)


def tui_command(
    config: ConfigOpt = None,
    udp: UdpOpt = None,
    docker: DockerOpt = True,
    probe: ProbeOpt = True,
    hide_system: SystemOpt = None,
    refresh: Annotated[float | None, typer.Option("--refresh", "-r", show_default=False)] = None,
    debug: DebugOpt = False,
    demo: DemoOpt = False,
    replay: ReplayOpt = None,
    standalone: StandaloneOpt = False,
) -> None:
    """Launch the interactive dashboard (same as running lirts with no command)."""
    _setup_logging(debug)
    cfg = _build_config(
        config, udp=udp, docker=docker, probe=probe, hide_system=hide_system, refresh=refresh
    )
    apply_color_depth(cfg)
    # Textual is a heavy import: the sub-commands that only print text must not pay for it.
    from lirts.tui.app import run_app

    run_app(_make_engine(cfg, demo=demo, replay=replay, standalone=standalone), config=cfg)


def record_command(
    duration: Annotated[str, typer.Argument(help="How long, e.g. 30s, 5m, 1h.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Recording file (JSON lines).")] = Path(
        "lirts-recording.jsonl"
    ),
    interval: Annotated[float, typer.Option("--interval", help="Seconds between frames.")] = 2.0,
    config: ConfigOpt = None,
    docker: DockerOpt = True,
    probe: ProbeOpt = True,
    demo: DemoOpt = False,
) -> None:
    """Record what lirts sees, one frame per refresh, for `lirts --replay FILE` anywhere."""
    try:
        seconds = parse_duration(duration)
    except ValueError as exc:
        err_console.print(f"[red]bad duration '{duration}' (use 30s, 5m, 1h)[/]")
        raise typer.Exit(code=2) from exc
    cfg = _build_config(config, udp=None, docker=docker, probe=probe, hide_system=None)
    cfg.setdefault("bandwidth", {})["mode"] = "off"
    engine = _make_engine(cfg, demo=demo)
    engine.start()
    try:
        console.print(f"Recording {seconds:g}s every {interval:g}s to {out} (Ctrl-C stops early)")

        def progress(n: int, snap: Snapshot) -> None:
            console.print(f"  frame {n}: {len(snap.listeners)} rows", end="\r")

        try:
            frames = record(engine, seconds=seconds, interval=interval, out=out, progress=progress)
        except KeyboardInterrupt:
            frames = -1
        console.print()
        console.print(
            f"Wrote {out}" + (f" ({frames} frames)" if frames >= 0 else " (stopped early)")
        )
        console.print(f"Play it back with: lirts --replay {out}")
    finally:
        engine.close()


def setup_command(config: ConfigOpt = None, debug: DebugOpt = False) -> None:
    """Open the dashboard with the step-by-step setup wizard (rerunnable; W inside lirts)."""
    _setup_logging(debug)
    cfg = _build_config(config, udp=None, docker=True, probe=True, hide_system=None)
    apply_color_depth(cfg)
    # Textual is a heavy import: the sub-commands that only print text must not pay for it.
    from lirts.tui.app import run_app

    run_app(Engine(cfg), config=cfg, setup=True)
