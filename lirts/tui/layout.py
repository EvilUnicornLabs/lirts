"""Layouts: the columns, the side panel sections, which boxes are shown and where they sit.

``default`` keeps the user's own columns with the side panel on the right, ``containers`` puts
the cluster and Docker facts first and the history box above the traffic box, ``hosts`` puts the
ssh, tunnel and proxy facts first and moves the side panel under the table, full width.  ``U``
cycles them and the choice is stored as the ``ui.layout`` setting; a layout only overrides what
is displayed, never the saved ``columns``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from lirts.config_defaults import DEFAULT_CONFIG
from lirts.settings import LAYOUT_NAMES
from lirts.tui.widgets import DEFAULT_SECTIONS, Section

# The first name in the settings choices is the layout everything falls back to.
DEFAULT_LAYOUT: str = LAYOUT_NAMES[0]


class Panel(enum.StrEnum):
    """A box a layout can ask for around the table."""

    SIDE = "side"
    NET = "net"
    HISTORY = "history"


class SidePosition(enum.StrEnum):
    """Where a layout puts the side panel: beside the table, or full width under it."""

    RIGHT = "right"
    LEFT = "left"
    BOTTOM = "bottom"


@dataclass(frozen=True, slots=True)
class Layout:
    """One arrangement: the table columns, the side panel sections, the boxes and their places."""

    name: str
    columns: tuple[str, ...]
    side_sections: tuple[Section, ...]
    panels: frozenset[Panel]
    side_position: SidePosition
    bottom_order: tuple[Panel, ...]


LAYOUTS: dict[str, Layout] = {
    "default": Layout(
        name="default",
        columns=tuple(DEFAULT_CONFIG["columns"]),
        side_sections=DEFAULT_SECTIONS,
        panels=frozenset({Panel.SIDE, Panel.NET}),
        side_position=SidePosition.RIGHT,
        bottom_order=(Panel.NET, Panel.HISTORY),
    ),
    "containers": Layout(
        name="containers",
        columns=(
            "port",
            "state",
            "src",
            "stack",
            "container",
            "service",
            "project",
            "cpu",
            "mem",
            "conns",
            "activity",
            "bandwidth",
            "health",
            "status",
        ),
        side_sections=(
            Section.CLUSTER,
            Section.CONTAINER,
            Section.IDENTITY,
            Section.RUNTIME,
            Section.INSIGHTS,
        ),
        panels=frozenset({Panel.SIDE, Panel.NET, Panel.HISTORY}),
        side_position=SidePosition.RIGHT,
        bottom_order=(Panel.HISTORY, Panel.NET),
    ),
    "hosts": Layout(
        name="hosts",
        columns=(
            "port",
            "state",
            "src",
            "service",
            "identity",
            "process",
            "pids",
            "conns",
            "activity",
            "uptime",
            "latency",
            "status",
        ),
        side_sections=(Section.HOSTS, Section.IDENTITY, Section.RUNTIME, Section.INSIGHTS),
        panels=frozenset({Panel.SIDE, Panel.NET}),
        side_position=SidePosition.BOTTOM,
        bottom_order=(Panel.NET,),
    ),
}


def layout_for(name: str) -> Layout:
    """The named layout, or the default one when the name is not one of ours."""
    return LAYOUTS.get(name, LAYOUTS[DEFAULT_LAYOUT])
