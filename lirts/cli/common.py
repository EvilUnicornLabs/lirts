"""Shared plumbing for the CLI commands.

Consoles, the option types every command reuses, logging setup, engine
construction and the helpers that turn a snapshot into a table or into JSON.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from lirts.config import load_config, state_dir
from lirts.config_view import ConfigView
from lirts.constants import (
    CPU_SAMPLE_GAP_SECONDS,
    MIN_REFRESH_INTERVAL,
    PIPED_CONSOLE_WIDTH,
)
from lirts.demo import DemoEngine
from lirts.engine import Engine
from lirts.listener_json import listener_dict
from lirts.models import Snapshot
from lirts.replay import ReplayEngine
from lirts.tui.render import resolve_columns

console = Console()
err_console = Console(stderr=True)
# When piped, Rich assumes 80 columns; give scripts the full table instead.
if not console.is_terminal:
    console = Console(width=PIPED_CONSOLE_WIDTH)

ConfigOpt = Annotated[
    Path | None, typer.Option("--config", "-c", help="Path to config.yaml.", show_default=False)
]
UdpOpt = Annotated[
    bool | None, typer.Option("--udp/--no-udp", help="Show UDP sockets.", show_default=False)
]
DockerOpt = Annotated[bool, typer.Option("--docker/--no-docker", help="Enable Docker integration.")]
ProbeOpt = Annotated[bool, typer.Option("--probe/--no-probe", help="Enable HTTP health probing.")]
SystemOpt = Annotated[
    bool | None,
    typer.Option(
        "--hide-system/--show-system", help="Hide OS system services.", show_default=False
    ),
]
DebugOpt = Annotated[bool, typer.Option("--debug", help="Verbose logging to the state directory.")]


def _setup_logging(debug: bool) -> None:
    """Send the ``lirts`` logger to lirts.log in the state directory."""
    folder = state_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.FileHandler(folder / "lirts.log", encoding="utf-8")
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("lirts")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)


DemoOpt = Annotated[
    bool,
    typer.Option(
        "--demo", help="A synthetic machine instead of this one (for developers of lirts)."
    ),
]
ReplayOpt = Annotated[
    Path | None,
    typer.Option(
        "--replay", help="Play back a recording made with `lirts record`.", show_default=False
    ),
]


def _make_engine(cfg: dict[str, Any], *, demo: bool = False, replay: Path | None = None) -> Engine:
    """The live engine, or the demo / replay stand-ins."""
    if demo and replay:
        err_console.print("[red]--demo and --replay cannot be combined[/]")
        raise typer.Exit(code=2)
    if demo:
        return DemoEngine(cfg)
    if replay:
        try:
            return ReplayEngine(cfg, replay)
        except (OSError, ValueError) as exc:
            err_console.print(f"[red]Cannot replay {replay}: {exc}[/]")
            raise typer.Exit(code=2) from exc
    return Engine(cfg)


def apply_color_depth(config: dict[str, Any]) -> str:
    """Force the colour depth of the dashboard from ``ui.truecolor``; returns the mode applied.

    Call this before the TUI is imported: Textual reads ``TEXTUAL_COLOR_SYSTEM`` once, when
    ``textual.constants`` is imported, and Rich reads ``COLORTERM`` when a console is built.
    """
    mode = ConfigView(config).ui.truecolor
    if mode == "on":
        os.environ["COLORTERM"] = "truecolor"
        os.environ["TEXTUAL_COLOR_SYSTEM"] = "truecolor"
    elif mode == "off":
        os.environ.pop("COLORTERM", None)
        os.environ["TEXTUAL_COLOR_SYSTEM"] = "256"
    return mode


def apply_force_color(config: dict[str, Any]) -> bool:
    """Keep colours when the output is piped (``cli.force_color``); returns whether it is on.

    Rich decides the terminal flag and the colour system when a console is built, and these
    two consoles are module globals other command modules imported by name, so both are set
    in place instead of building new consoles.
    """
    if not ConfigView(config).cli.force_color:
        return False
    for con in (console, err_console):
        con._force_terminal = True
        con._color_system = con._detect_color_system()
    return True


def _build_config(
    config: Path | None,
    *,
    udp: bool | None,
    docker: bool,
    probe: bool,
    hide_system: bool | None,
    refresh: float | None = None,
) -> dict[str, Any]:
    """The configuration file with this run's command-line flags written over it."""
    cfg = load_config(config)
    view = ConfigView(cfg)
    if udp is not None:
        cfg["show_udp"] = udp
    if hide_system is not None:
        cfg["hide_system"] = hide_system
    if refresh is not None:
        cfg["refresh_interval"] = max(MIN_REFRESH_INTERVAL, refresh)
    cfg.setdefault("docker", {})["enabled"] = docker and view.docker.enabled
    cfg.setdefault("http_probe", {})["enabled"] = probe and view.http_probe.enabled
    apply_force_color(cfg)
    return cfg


def _collect(
    cfg: dict[str, Any],
    *,
    samples: int = 1,
    demo: bool = False,
    replay: Path | None = None,
) -> tuple[Engine, Snapshot]:
    """Run ``samples`` refreshes (two are needed for CPU deltas) and return the last."""
    cfg = dict(cfg)
    cfg.setdefault("bandwidth", {})["mode"] = "off"
    engine = _make_engine(cfg, demo=demo, replay=replay)
    snap = engine.refresh_sync()
    for _ in range(max(0, samples - 1)):
        time.sleep(CPU_SAMPLE_GAP_SECONDS)
        snap = engine.refresh_sync()
    return engine, snap


# The JSON shape of a row lives with the models; the CLI keeps its old name for it.
_listener_dict = listener_dict


def _render_table(snap: Snapshot, *, cfg: ConfigView, columns: list[str] | None = None) -> Table:
    """The snapshot as the plain table ``lirts list`` prints."""
    cols = resolve_columns(columns or cfg.columns)
    table = Table(box=None, pad_edge=False, header_style="bold", show_edge=False)
    for col in cols:
        table.add_column(col.label, no_wrap=True, overflow="ellipsis")
    for lst in snap.listeners:
        table.add_row(*[col.render(lst, cfg) for col in cols])
    return table
