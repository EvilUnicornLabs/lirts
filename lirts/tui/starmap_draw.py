"""Drawing the star map: lines, glyphs and colours onto a braille canvas.

Colours are never hard-coded here: a star's kind and a line's state pick a :class:`Tone`, and
the :class:`Palette` handed in says what that tone looks like.  The screen builds one from the
running Textual theme, so every theme recolours the map; :data:`DEFAULT_PALETTE` keeps the
original Rich colour names for the tests and for any caller that has no theme.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from rich.text import Text

from lirts.topology import NODE_GLYPHS, Graph, Node, NodeKind, Relation, Status
from lirts.tui.starmap_canvas import BrailleCanvas
from lirts.tui.starmap_labels import put_label
from lirts.tui.starmap_layout import Placement, layout


class Tone(StrEnum):
    """A named colour of the theme, as the map asks for it."""

    ACCENT = "accent"
    PRIMARY = "primary"
    SECONDARY = "secondary"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"
    MUTED = "muted"
    FOREGROUND = "foreground"


@dataclass(frozen=True, slots=True)
class Palette:
    """The eight colours the map draws with, as Rich colour names or ``#rrggbb`` strings."""

    accent: str = "cyan"
    primary: str = "cyan"
    secondary: str = "magenta"
    warning: str = "yellow"
    error: str = "red"
    success: str = "green"
    muted: str = "bright_black"
    foreground: str = "white"

    def tone(self, tone: Tone) -> str:
        return str(getattr(self, tone.value))


DEFAULT_PALETTE = Palette()

# What a star is, as a tone; a problem overrides it (see ``node_style``).
KIND_TONES: dict[str, Tone] = {
    NodeKind.DB: Tone.SECONDARY,
    NodeKind.CACHE: Tone.SECONDARY,
    NodeKind.QUEUE: Tone.SECONDARY,
    NodeKind.BACKEND: Tone.SUCCESS,
    NodeKind.FRONTEND: Tone.PRIMARY,
    NodeKind.PROXY: Tone.WARNING,
    NodeKind.TOOL: Tone.FOREGROUND,
    NodeKind.SYSTEM: Tone.MUTED,
    NodeKind.TUNNEL: Tone.ACCENT,
    NodeKind.HOST: Tone.MUTED,
    NodeKind.CLUSTER: Tone.MUTED,
    NodeKind.SERVICE: Tone.MUTED,
}
STATUS_TONES: dict[str, Tone] = {Status.ERROR: Tone.ERROR, Status.WARNING: Tone.WARNING}
# The legend under the map.
LEGEND = "◆ db/cache/queue  ● backend  ○ frontend  ⬢ proxy  ▫ tool  ⇄ tunnel  ⌂ host  ☁ cluster  · system  — line: who talks to whom"


def node_style(node: Node, *, selected: bool = False, palette: Palette = DEFAULT_PALETTE) -> str:
    """Colour of a star: its kind, overridden by a problem, inverted when selected."""
    status_tone = STATUS_TONES.get(node.status)
    if status_tone is not None:
        style = f"bold {palette.tone(status_tone)}"
    else:
        style = palette.tone(KIND_TONES.get(node.kind, Tone.FOREGROUND))
    if not node.live:
        style = "dim"
    return f"{style} reverse" if selected else style


def line_style(relation: Relation, *, selected: str | None, palette: Palette) -> str:
    """Colour of a line: the selected star's own lines stand out, the rest go quiet."""
    if selected in (relation.src, relation.dst):
        return f"bold {palette.primary}"
    if selected or not relation.live:
        return palette.muted
    return palette.accent


@dataclass
class Picture:
    """A drawn map: the canvas plus where every star ended up."""

    canvas: BrailleCanvas
    placements: dict[str, Placement]
    width: int
    height: int

    def rows(self) -> list[Text]:
        return self.canvas.render()


def draw_graph(
    graph: Graph,
    *,
    width: int,
    height: int,
    focus: str | None = None,
    selected: str | None = None,
    show_labels: bool = True,
    hide_idle: bool = False,
    scale: float = 1.0,
    cached: dict[str, Placement] | None = None,
    palette: Palette = DEFAULT_PALETTE,
) -> Picture:
    """Lay the graph out and draw it: lines first, then glyphs and labels over them."""
    canvas = BrailleCanvas(width, height)
    placements = layout(
        graph,
        width=width,
        height=height,
        focus=focus,
        hide_idle=hide_idle,
        scale=scale,
        cached=cached,
    )
    neighbours = graph.neighbours(selected) if selected else set()
    for relation in graph.relations.values():
        a, b = placements.get(relation.src), placements.get(relation.dst)
        if a is None or b is None or (not relation.live and not relation.usual):
            continue
        canvas.line(
            a.x, a.y, b.x, b.y, style=line_style(relation, selected=selected, palette=palette)
        )
    centre_x = (width - 1) / 2
    styles: dict[str, str] = {}
    for node_id, place in placements.items():
        node = graph.nodes[node_id]
        dimmed = bool(selected) and node_id != selected and node_id not in neighbours
        styles[node_id] = (
            "dim" if dimmed else node_style(node, selected=node_id == selected, palette=palette)
        )
        canvas.put(place.x, place.y, NODE_GLYPHS.get(node.kind, "·"), style=styles[node_id])
    if show_labels:
        # Glyphs first, labels second, so a label never lands on top of another star.
        for node_id, place in placements.items():
            node = graph.nodes[node_id]
            put_label(canvas, place, node, style=styles[node_id], left=place.x < centre_x)
    return Picture(canvas, placements, width, height)
