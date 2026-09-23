"""The star map: services, containers, tunnels, hosts and the cluster as a picture; opened with `M`."""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.color import Color, ColorParseError
from textual.containers import Horizontal
from textual.screen import Screen
from textual.theme import Theme
from textual.widgets import Static

from lirts.engine import Engine
from lirts.topology import MACHINE_ID, Graph, Node, NodeKind
from lirts.topology_text import describe_relation, kind_label, render_topology_text
from lirts.tui.screens.details import DetailsScreen
from lirts.tui.starmap_draw import DEFAULT_PALETTE, LEGEND, Palette, draw_graph, node_style
from lirts.tui.starmap_layout import Placement, cycle, nearest

PANEL_WIDTH = 34
# Below this the map is replaced by the text listing; between here and WIDE the panel is folded.
MIN_MAP_SIZE = (100, 30)
WIDE = 140
SCALE_STEP = 0.1
SCALE_RANGE = (0.5, 1.5)
DEFAULT_SCALE = 1.0
# How far `z` zooms into the selected star's wedge.
ZOOM_SCALE = 1.3
# How far the theme's text colour is pushed towards its background for the map's quiet colour.
MUTED_BLEND = 0.55


def theme_palette(variables: dict[str, str]) -> Palette:
    """The map's colours from a theme's CSS variables; anything unusable keeps the default."""
    return Palette(
        accent=_hex(variables.get("accent")) or DEFAULT_PALETTE.accent,
        primary=_hex(variables.get("primary")) or DEFAULT_PALETTE.primary,
        secondary=_hex(variables.get("secondary")) or DEFAULT_PALETTE.secondary,
        warning=_hex(variables.get("warning")) or DEFAULT_PALETTE.warning,
        error=_hex(variables.get("error")) or DEFAULT_PALETTE.error,
        success=_hex(variables.get("success")) or DEFAULT_PALETTE.success,
        muted=_muted(variables) or DEFAULT_PALETTE.muted,
        foreground=_hex(variables.get("foreground")) or DEFAULT_PALETTE.foreground,
    )


def _hex(value: str | None) -> str | None:
    """``#rrggbb`` for Rich, or None when the theme's value is not a plain colour."""
    try:
        text = Color.parse(value or "").hex
    except ColorParseError:
        return None
    return text if len(text) == 7 and text.startswith("#") else None


def _muted(variables: dict[str, str]) -> str | None:
    """Halfway between the theme's text and its background: readable but quiet."""
    foreground, background = _hex(variables.get("foreground")), _hex(variables.get("background"))
    if foreground is None or background is None:
        return None
    return Color.parse(foreground).blend(Color.parse(background), MUTED_BLEND).hex


class StarMapScreen(Screen[None]):
    """The constellation of this machine, drawn from the latest refresh; opened with `M`."""

    BINDINGS = [
        Binding("escape,q,M", "app.pop_screen", "Close"),
        Binding("right", "move('right')", "Right", show=False),
        Binding("left", "move('left')", "Left", show=False),
        Binding("up", "move('up')", "Up", show=False),
        Binding("down", "move('down')", "Down", show=False),
        Binding("tab", "cycle(False)", "Next", show=False),
        Binding("shift+tab", "cycle(True)", "Previous", show=False),
        Binding("enter", "jump", "Jump to row"),
        Binding("i", "details", "Details"),
        Binding("f", "focus_project", "Focus project"),
        Binding("h", "hide_idle", "Hide idle (learned)"),
        Binding("l", "labels", "Labels"),
        Binding("plus,equals_sign", "zoom(1)", "Wider", show=False),
        Binding("minus", "zoom(-1)", "Narrower", show=False),
        Binding("0", "reset_scale", "Reset rings", show=False),
        Binding("z", "zoom_star", "Zoom to star"),
        Binding("r", "redraw", "Redraw"),
    ]

    def __init__(self, engine: Engine, *, app_ref: Any = None) -> None:
        super().__init__()
        self.engine = engine
        self.lirts = app_ref
        self.graph: Graph = Graph()
        self.selected: str | None = None
        self.focus_on: str | None = None
        self.show_labels = True
        self.hide_idle = False
        self.scale = DEFAULT_SCALE
        self.zoomed = False
        self.placements: dict[str, Placement] = {}
        self.palette: Palette = DEFAULT_PALETTE
        self._scale_before_zoom = DEFAULT_SCALE
        self._cache_key: tuple[Any, ...] = ()

    def compose(self) -> ComposeResult:
        yield Static("Star map", classes="screen-title")
        with Horizontal(id="map-body"):
            yield Static(id="map-canvas")
            yield Static(id="map-panel")
        yield Static(classes="screen-footer", id="map-legend")

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Keys that need a selected star are dimmed until one is selected."""
        if action in ("jump", "details", "focus_project", "zoom_star") and self.selected is None:
            return None
        return True

    def on_mount(self) -> None:
        self.rebuild()
        if self.is_running:
            self.app.theme_changed_signal.subscribe(self, self.on_theme_changed)
        self.redraw()

    def on_unmount(self) -> None:
        self.app.theme_changed_signal.unsubscribe(self)

    def on_theme_changed(self, theme: Theme) -> None:
        """A new theme while the map is open: same stars, the theme's colours."""
        self.redraw()

    def on_resize(self) -> None:
        self.redraw()

    def on_refresh(self) -> None:
        """Called by the app after every refresh: new data, same positions."""
        self.rebuild()
        if self.selected and self.selected not in self.graph.nodes:
            self.selected = None
        self.redraw()

    # ----- actions --------------------------------------------------------------------------

    def action_move(self, direction: str) -> None:
        target = nearest(self.placements, self.selected, direction)
        if target is not None:
            self.selected = target
            self.redraw()

    def action_cycle(self, backwards: bool) -> None:
        target = cycle(self.placements, self.selected, backwards=backwards)
        if target is not None:
            self.selected = target
            self.redraw()

    def action_focus_project(self) -> None:
        """Only the selected star's project (or the cluster's services) fills the map; again for all."""
        if self.focus_on is not None:
            self.focus_on = None
        elif self.selected:
            self.focus_on = self._wedge_of(self.graph.nodes[self.selected])
        self.zoomed = False
        self.redraw()

    def _wedge_of(self, node: Node) -> str:
        """What ``f`` and ``z`` fill the map with: the cluster itself, else the project."""
        return node.id if node.kind == NodeKind.CLUSTER else (node.project or "")

    def action_hide_idle(self) -> None:
        self.hide_idle = not self.hide_idle
        self.redraw()

    def action_labels(self) -> None:
        self.show_labels = not self.show_labels
        self.redraw()

    def action_zoom(self, step: int) -> None:
        self.scale = min(
            SCALE_RANGE[1], max(SCALE_RANGE[0], round(self.scale + step * SCALE_STEP, 2))
        )
        self.redraw()

    def action_reset_scale(self) -> None:
        """Back to the normal ring spacing (0)."""
        self.scale = DEFAULT_SCALE
        self.redraw()

    def action_zoom_star(self) -> None:
        """The selected star's wedge alone, drawn larger (z); z again for the whole map."""
        if self.zoomed:
            self.focus_on, self.scale, self.zoomed = None, self._scale_before_zoom, False
        elif self.selected:
            self._scale_before_zoom = self.scale
            self.focus_on = self._wedge_of(self.graph.nodes[self.selected])
            self.scale, self.zoomed = ZOOM_SCALE, True
        self.redraw()

    def action_redraw(self) -> None:
        self.rebuild()
        self._cache_key = ()
        self.redraw()

    def action_jump(self) -> None:
        """Close the map and put the table cursor on the selected star's row (Enter)."""
        node = self.graph.nodes.get(self.selected or "")
        if node is None or not node.row_key or self.lirts is None:
            return
        self.app.pop_screen()
        if not self.lirts.select_row(node.row_key):
            self.lirts.notify(f"{node.label} is not in the table right now")

    def action_details(self) -> None:
        """Open the details of the selected star's row (i)."""
        node = self.graph.nodes.get(self.selected or "")
        if node is None or not node.row_key or self.lirts is None:
            return
        listener = self.lirts.snapshot.by_key(node.row_key)
        if listener is None:
            return

        self.app.push_screen(DetailsScreen(listener, self.lirts.snapshot, self.lirts.cfg))

    # ----- drawing --------------------------------------------------------------------------

    def rebuild(self) -> None:
        """Build the graph from the engine's latest snapshot."""
        self.graph = self.engine.topology.graph_for(hide_system=self.engine.hide_system)

    def redraw(self) -> None:
        """Draw the graph at the current size, or list it when the terminal is small."""
        body = self.query_one("#map-body", Horizontal)
        panel = self.query_one("#map-panel", Static)
        canvas = self.query_one("#map-canvas", Static)
        width, height = body.size.width, body.size.height
        if width == 0 or height == 0:
            return
        narrow = width < MIN_MAP_SIZE[0] or height < MIN_MAP_SIZE[1]
        panel.display = not narrow and width >= WIDE
        self.palette = theme_palette(self.app.get_css_variables())
        self.query_one("#map-legend", Static).update(
            self.footer_text(narrow, folded=not narrow and not panel.display)
        )
        if narrow:
            canvas.update(
                Text(
                    "The map needs about 100×30; this is the same information as a list.\n\n",
                    style="dim",
                )
                + Text(render_topology_text(self.graph, highlight=self.selected))
            )
            return
        map_width = width - (PANEL_WIDTH if panel.display else 0)
        cache_key = (map_width, height, self.focus_on, self.hide_idle, self.scale)
        cached = self.placements if cache_key == self._cache_key else None
        picture = draw_graph(
            self.graph,
            width=map_width,
            height=height,
            focus=self.focus_on,
            selected=self.selected,
            show_labels=self.show_labels,
            hide_idle=self.hide_idle,
            scale=self.scale,
            cached=cached,
            palette=self.palette,
        )
        self.placements = picture.placements
        self._cache_key = cache_key
        canvas.update(Group(*picture.rows()))
        panel.update(self.panel_text())

    def footer_text(self, narrow: bool, *, folded: bool = False) -> str:
        """Two lines under the map: the legend, then the keys — or the star while folded."""
        if narrow:
            return "Esc close · r redraw"
        node = self.graph.nodes.get(self.selected or "")
        if folded and node is not None:
            return f"{LEGEND}\n{self.star_line(node)}"
        state = []
        if self.focus_on is not None:
            state.append(f"focus {self.focus_on or '(no project)'}")
        if self.hide_idle:
            state.append("idle hidden")
        if not self.show_labels:
            state.append("labels off")
        if self.scale != DEFAULT_SCALE:
            state.append(f"rings ×{self.scale:.1f}")
        keys = "←↑→↓ Tab select · Enter row · i details · f focus · z zoom · h idle · l labels · + - 0 rings · r redraw · Esc"
        return f"{LEGEND}\n{keys}" + (f"   [{' · '.join(state)}]" if state else "")

    def star_line(self, node: Node) -> str:
        """The selected star on one line: what it is, where it listens, how connected it is."""
        parts = [node.label, kind_label(node)]
        if node.ports:
            parts.append(node.port_text)
        count = len(self.graph.relations_of(node.id))
        parts.append(f"{count} relation{'' if count == 1 else 's'}")
        return "  ·  ".join(parts)

    def panel_text(self) -> Text:
        """The right-hand panel: the selected star, or what is on the map."""
        node = self.graph.nodes.get(self.selected or "")
        return self.node_text(node) if node is not None else self.overview_text()

    def node_text(self, node: Node) -> Text:
        text = Text()
        text.append(f"{node.label}\n", style=f"bold {node_style(node, palette=self.palette)}")
        text.append(kind_label(node), style="dim")
        if node.source == "docker":
            text.append("  container", style="dim")
        text.append("\n")
        if node.ports:
            text.append(f"ports {node.port_text}\n")
        if node.project:
            text.append(f"project {node.project}\n")
        if node.aliases:
            text.append(f"names {', '.join(node.aliases)}\n")
        if node.status_rank:
            text.append(
                f"status {node.status}\n", style="red" if node.status_rank == 2 else "yellow"
            )
        if not node.live:
            text.append("not running now\n", style="dim")
        if node.days_seen:
            text.append(
                f"seen on {len(node.days_seen)} day{'s' if len(node.days_seen) != 1 else ''}"
                f", first {node.days_seen[0]}, last {node.days_seen[-1]}\n",
                style="dim",
            )
        relations = self.graph.relations_of(node.id)
        text.append(f"\n{len(relations)} relations\n", style="bold")
        for relation in relations:
            text.append(f"  {describe_relation(self.graph, relation, seen_from=node.id)}\n")
        if node.row_key:
            text.append("\nEnter: row in the table · i: details\n", style="dim")
        return text

    def overview_text(self) -> Text:
        nodes = [n for n in self.graph.nodes.values() if n.id != MACHINE_ID and n.parent is None]
        text = Text()
        text.append("This machine\n", style="bold")
        text.append(f"{len(nodes)} stars, {len(self.graph.relations)} lines\n\n", style="dim")
        counts: dict[str, int] = {}
        for node in nodes:
            counts[node.kind] = counts.get(node.kind, 0) + 1
        for kind in NodeKind:
            if counts.get(kind):
                text.append(f"  {counts[kind]:3d}  {kind}\n")
        projects = [p for p in self.graph.projects() if p]
        if projects:
            text.append("\nProjects\n", style="bold")
            for project in projects:
                members = sum(1 for n in nodes if n.project == project)
                text.append(f"  {project}  ")
                text.append(f"{members}\n", style="dim")
        problems = [n for n in nodes if n.status_rank > 0]
        if problems:
            text.append("\nProblems\n", style="bold")
            for node in sorted(problems, key=lambda n: -n.status_rank):
                text.append(
                    f"  {node.label} {node.port_text}\n",
                    style="red" if node.status_rank == 2 else "yellow",
                )
        text.append("\n←↑→↓ or Tab selects a star\n", style="dim")
        return text
