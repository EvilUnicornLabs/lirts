"""Styles: borders, density, glyph set, scroller and panel set as one named bundle.

``T`` cycles them and the ``ui.style`` setting picks one.  A style is data only; applying it is
:meth:`lirts.tui.app_ui.UiMixin.apply_style`.  Colours are never a style's business: the two
themes this module brings (cracktro, phosphor) are registered on the app at start-up as plain
themes the user picks with the ``theme`` setting.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from textual.app import App
from textual.theme import Theme

from lirts.settings import STYLE_NAMES


class Border(StrEnum):
    """The frame a style draws around the panels."""

    ROUND = "round"
    SOLID = "solid"
    NONE = "none"
    DOUBLE = "double"
    ASCII = "ascii"


class Density(StrEnum):
    """How much room the table, the top bar and the panels take."""

    NORMAL = "normal"
    DENSE = "dense"
    TIGHT = "tight"


# Borders whose corners the ``ui.rounded_corners`` setting decides; the others are the point
# of the style and stay as they are.
ROUNDABLE_BORDERS = (Border.ROUND, Border.SOLID)


@dataclass(frozen=True, slots=True)
class Style:
    """One look: the frame, the glyphs, the density, the scroller and which boxes are shown."""

    name: str
    border: Border
    glyphs: str
    density: Density
    side_panel: bool
    net_panel: bool
    history_panel: bool
    scroller: str = ""


CRACKTRO_THEME = Theme(
    name="cracktro",
    primary="#ff2bd6",
    secondary="#00e5ff",
    accent="#00e5ff",
    warning="#ffe600",
    error="#ff2b5e",
    success="#00ff9c",
    foreground="#f2e8ff",
    background="#000000",
    surface="#0a0012",
    panel="#16001f",
    dark=True,
)

PHOSPHOR_THEME = Theme(
    name="phosphor",
    primary="#33ff66",
    secondary="#1f9e44",
    accent="#7cff9e",
    warning="#c8ff33",
    error="#ff6b6b",
    success="#33ff66",
    foreground="#33ff66",
    background="#000000",
    surface="#001005",
    panel="#002a10",
    dark=True,
)

# Registered at start-up so the theme picker offers them; no style ever selects a theme.
STYLE_THEMES = (CRACKTRO_THEME, PHOSPHOR_THEME)

# The cracktro scroller; one cell of it moves per second across the top bar's first line.
CRACKTRO_SCROLLER = "*** lirts ***  every port on this machine, greeted  ***  "

STYLES: dict[str, Style] = {
    "classic": Style(
        name="classic",
        border=Border.ROUND,
        glyphs="block",
        density=Density.NORMAL,
        side_panel=True,
        net_panel=True,
        history_panel=False,
    ),
    "compact": Style(
        name="compact",
        border=Border.NONE,
        glyphs="block",
        density=Density.DENSE,
        side_panel=False,
        net_panel=True,
        history_panel=False,
    ),
    "tight": Style(
        name="tight",
        border=Border.ROUND,
        glyphs="block",
        density=Density.TIGHT,
        side_panel=True,
        net_panel=True,
        history_panel=False,
    ),
    "cracktro": Style(
        name="cracktro",
        border=Border.DOUBLE,
        glyphs="demoscene",
        density=Density.NORMAL,
        side_panel=True,
        net_panel=True,
        history_panel=False,
        scroller=CRACKTRO_SCROLLER,
    ),
    "phosphor": Style(
        name="phosphor",
        border=Border.ASCII,
        glyphs="block",
        density=Density.NORMAL,
        side_panel=True,
        net_panel=True,
        history_panel=False,
    ),
}

DEFAULT_STYLE = STYLE_NAMES[0]


def style_for(name: str) -> Style:
    """The named style, falling back to ``classic`` for an unknown name."""
    return STYLES.get(name, STYLES[DEFAULT_STYLE])


def next_style(name: str) -> Style:
    """The style after `name` in the settings order; ``T`` walks the same ring."""
    names = list(STYLE_NAMES)
    index = names.index(name) if name in names else -1
    return style_for(names[(index + 1) % len(names)])


def effective_border(style: Style, *, rounded_corners: bool) -> str:
    """The border a style really draws: ``ui.rounded_corners`` decides round vs square.

    Only the plain frames follow the setting; a style whose frame is the point of it
    (no border, double lines, ASCII) keeps its own.
    """
    if style.border not in ROUNDABLE_BORDERS:
        return str(style.border)
    return str(Border.ROUND if rounded_corners else Border.SOLID)


def register_style_themes(app: App[Any]) -> None:
    """Add the themes this module brings to the app, once; registering twice is harmless."""
    for theme in STYLE_THEMES:
        if theme.name not in app.available_themes:
            app.register_theme(theme)
