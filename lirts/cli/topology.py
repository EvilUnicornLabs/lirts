"""``lirts topology``: the star map as text."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from lirts.cli.common import (
    ConfigOpt,
    DockerOpt,
    SystemOpt,
    _build_config,
    _collect,
    _make_engine,
    console,
)
from lirts.topology_text import render_topology_text


def topology_command(
    config: ConfigOpt = None,
    docker: DockerOpt = True,
    hide_system: SystemOpt = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    demo: Annotated[bool, typer.Option("--demo", help="Use the built-in demo machine.")] = False,
    clear: Annotated[bool, typer.Option("--clear", help="Forget the learned map.")] = False,
) -> None:
    """The star map as text: what runs now and what lirts has seen on this machine lately."""
    cfg = _build_config(config, udp=None, docker=docker, probe=False, hide_system=hide_system)
    if clear:
        engine = _make_engine(cfg, demo=demo)
        had_file = engine.topology.clear()
        engine.close()
        console.print("Forgot the learned map." if had_file else "Nothing learned yet.")
        return
    engine, _snap = _collect(cfg, demo=demo)
    try:
        graph = engine.topology.graph_for(hide_system=engine.hide_system)
    finally:
        engine.close()
    if as_json:
        console.print_json(json.dumps(graph.to_dict()))
        return
    console.print(render_topology_text(graph), markup=False, highlight=False)
