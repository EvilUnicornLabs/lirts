"""The star map picture: ring layout, braille canvas, drawing and the M screen."""

from __future__ import annotations

from typing import Any

from textual.widgets import Static

from lirts.config import DEFAULT_CONFIG, validate
from lirts.demo import DemoEngine
from lirts.identity import ROLE_BACKEND, ROLE_DB, ROLE_PROXY
from lirts.models import Edge, Identity, Snapshot
from lirts.topology import EdgeKind, Graph, Node, NodeKind, Relation
from lirts.topology_build import build_graph
from lirts.tui.app import LirtsApp
from lirts.tui.screens import StarMapScreen
from lirts.tui.screens.starmap import theme_palette
from lirts.tui.starmap_canvas import BRAILLE_BASE, BrailleCanvas
from lirts.tui.starmap_draw import DEFAULT_PALETTE, Palette, draw_graph, line_style, node_style
from lirts.tui.starmap_labels import label_candidates, put_label
from lirts.tui.starmap_layout import RING_OF_KIND, Placement, layout, visible_nodes, wedges
from tests.conftest import make_listener, make_process, wait_rows


def _graph() -> Graph:
    nginx = make_listener(port=80, name="nginx", identity=Identity("Nginx", ROLE_PROXY, 0.9))
    api = make_listener(
        port=8001,
        processes=[make_process(pid=8001, name="python")],
        identity=Identity("API", ROLE_BACKEND, 0.9, project="shop"),
    )
    db = make_listener(port=5432, identity=Identity("PostgreSQL", ROLE_DB, 0.9, project="shop"))
    snap = Snapshot(listeners=[nginx, api, db], edges=[Edge(8001, "python", 5432, count=2)])
    return build_graph(snap, now=1.0)


def test_rings_follow_the_kind_and_wedges_follow_the_project() -> None:
    graph = _graph()
    places = layout(graph, width=100, height=30)
    assert places["svc:shop/PostgreSQL"].ring == 0
    assert places["svc:shop/API"].ring == 1
    assert places["proc:Nginx:80"].ring == 2
    assert places["svc:shop/PostgreSQL"].project == "shop"
    assert places["proc:Nginx:80"].project == ""
    assert [w[0] for w in wedges(visible_nodes(graph))] == ["shop", ""]
    assert RING_OF_KIND[NodeKind.CLUSTER] == 3


def test_layout_is_deterministic_and_inside_the_map() -> None:
    graph = _graph()
    first = layout(graph, width=100, height=30)
    again = layout(graph, width=100, height=30)
    assert first == again
    assert all(0 <= p.x < 100 and 0 <= p.y < 30 for p in first.values())
    assert len({(p.x, p.y) for p in first.values()}) == len(first)
    assert layout(graph, width=3, height=3) == {}


def test_cluster_children_are_hidden_unless_the_cluster_is_focused() -> None:
    cfg: dict[str, Any] = validate(dict(DEFAULT_CONFIG))
    engine = DemoEngine(cfg)
    try:
        graph = build_graph(engine.refresh_sync())
    finally:
        engine.close()
    overview = {n.id for n in visible_nodes(graph)}
    assert "k8s:demo-cluster" in overview
    assert "k8s:demo-cluster/shop/api" not in overview
    focused = {n.id for n in visible_nodes(graph, focus="k8s:demo-cluster")}
    assert focused == {
        "k8s:demo-cluster",
        "k8s:demo-cluster/shop/api",
        "k8s:demo-cluster/shop/postgres",
    }
    assert {n.id for n in visible_nodes(graph, focus="shop")} >= {"ctr:shop/db", "ctr:shop/web"}


def test_braille_canvas_draws_lines_and_text_wins_over_dots() -> None:
    canvas = BrailleCanvas(10, 3)
    canvas.line(0, 0, 9, 2, style="cyan")
    rows = canvas.render()
    first = rows[0].plain
    assert ord(first[0]) > BRAILLE_BASE
    assert ord(rows[2].plain[9]) > BRAILLE_BASE
    canvas.put(0, 0, "AB", style="bold")
    assert canvas.render()[0].plain.startswith("AB")
    assert not canvas.fits(0, 0, 2)
    assert canvas.fits(2, 1, 3)
    assert not canvas.fits(8, 1, 5)
    empty = BrailleCanvas(0, 0)
    assert empty.render() == []


def test_draw_graph_places_glyphs_labels_and_colours() -> None:
    graph = _graph()
    picture = draw_graph(graph, width=100, height=30)
    text = "\n".join(row.plain for row in picture.rows())
    assert "◆ " in text or " ◆" in text
    assert "PostgreSQL :5432" in text
    assert "Nginx :80" in text
    assert node_style(graph.nodes["svc:shop/PostgreSQL"]) == "magenta"
    graph.nodes["svc:shop/PostgreSQL"].status = "error"
    assert node_style(graph.nodes["svc:shop/PostgreSQL"]) == "bold red"
    assert node_style(graph.nodes["svc:shop/API"], selected=True).endswith("reverse")
    graph.nodes["svc:shop/API"].live = False
    assert node_style(graph.nodes["svc:shop/API"]) == "dim"
    no_labels = draw_graph(graph, width=100, height=30, show_labels=False)
    assert "PostgreSQL" not in "\n".join(row.plain for row in no_labels.rows())


async def test_m_opens_the_star_map_with_panel_and_legend(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        await pilot.press("M")
        await pilot.pause()
        assert isinstance(app.screen, StarMapScreen)
        screen = app.screen
        assert screen.graph.nodes
        assert screen.query_one("#map-panel").display is True
        assert "stars" in screen.panel_text().plain
        await pilot.press("r")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, StarMapScreen)


async def test_narrow_terminal_shows_the_listing_instead_of_the_map(app: LirtsApp) -> None:
    async with app.run_test(size=(80, 24)) as pilot:
        await wait_rows(app, pilot)
        await pilot.press("M")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, StarMapScreen)
        assert screen.query_one("#map-panel").display is False
        await pilot.press("escape")


def test_nearest_and_cycle_move_between_stars() -> None:
    from lirts.tui.starmap_layout import Placement, cycle, nearest

    places = {
        "a": Placement(10, 10, 0, "", 0.0),
        "b": Placement(30, 10, 1, "", 1.0),
        "c": Placement(10, 20, 2, "", 2.0),
        "d": Placement(30, 20, 3, "", 3.0),
    }
    assert nearest(places, "a", "right") == "b"
    assert nearest(places, "a", "down") == "c"
    assert nearest(places, "d", "left") == "c"
    assert nearest(places, "d", "up") == "b"
    assert nearest(places, "a", "left") is None
    assert nearest(places, None, "right") == "a"
    assert nearest({}, None, "right") is None
    assert cycle(places, None) == "a"
    assert cycle(places, "d") == "a"
    assert cycle(places, "a", backwards=True) == "d"
    assert cycle({}, None) is None


def test_cached_positions_survive_a_refresh_and_hide_idle_drops_stopped_rows() -> None:
    graph = _graph()
    first = layout(graph, width=100, height=30)
    graph.nodes["proc:Nginx:80"].live = False
    again = layout(graph, width=100, height=30, cached=first)
    assert again == first
    hidden = layout(graph, width=100, height=30, hide_idle=True)
    assert "proc:Nginx:80" not in hidden
    wide = layout(graph, width=100, height=30, scale=0.5)
    assert wide != first


async def test_map_keys_select_focus_toggle_jump_and_open_details(app: LirtsApp) -> None:
    from lirts.tui.screens import DetailsScreen

    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        await pilot.press("M")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, StarMapScreen)
        assert screen.selected is None
        await pilot.press("tab")
        await pilot.pause()
        assert screen.selected in screen.placements
        first = screen.selected
        assert first is not None
        assert screen.graph.nodes[first].label in screen.panel_text().plain
        for key in ("right", "down", "left", "up", "shift+tab"):
            await pilot.press(key)
        await pilot.pause()
        assert screen.selected in screen.placements

        await pilot.press("f")
        await pilot.pause()
        assert screen.focus_on is not None
        await pilot.press("f")
        await pilot.pause()
        assert screen.focus_on is None

        await pilot.press("h", "l", "plus", "minus", "minus")
        await pilot.pause()
        assert screen.hide_idle and not screen.show_labels and screen.scale == 0.9
        assert "idle hidden" in screen.footer_text(False)
        screen.on_refresh()

        await pilot.press("i")
        await pilot.pause()
        assert isinstance(app.screen, DetailsScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, StarMapScreen)

        node = screen.graph.nodes[screen.selected or ""]
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, StarMapScreen)
        assert app._selected_key == node.row_key


def test_a_crowded_label_is_shortened_instead_of_dropped() -> None:
    alpha = Node("alpha", NodeKind.BACKEND, "Alpha-service", ports=[8000])
    beta = Node("beta", NodeKind.BACKEND, "Beta-service", ports=[9000])
    canvas = BrailleCanvas(30, 1)
    canvas.put(2, 0, "●")
    canvas.put(18, 0, "●")

    first = put_label(canvas, Placement(2, 0, 1, "", 0.0), alpha, style="", left=False)
    second = put_label(canvas, Placement(18, 0, 1, "", 0.0), beta, style="", left=False)

    assert first == "Alpha-service"
    assert second == "Beta-serv…"
    row = canvas.render()[0].plain
    assert "Alpha-service" in row and "Beta-serv…" in row

    cramped = BrailleCanvas(8, 1)
    cramped.put(7, 0, "◆")
    star = Placement(7, 0, 0, "", 0.0)
    assert put_label(cramped, star, Node("c", NodeKind.DB, "db"), style="", left=False) is None
    assert cramped.render()[0].plain == "       ◆"


def test_label_candidates_drop_the_port_then_shorten_but_never_below_six_characters() -> None:
    node = Node("a", NodeKind.BACKEND, "Payments-worker", ports=[8080, 8081])

    assert label_candidates(node, free=40) == ["Payments-worker :8080", "Payments-worker"]
    assert label_candidates(node, free=9) == [
        "Payments-worker :8080",
        "Payments-worker",
        "Payments…",
    ]
    assert label_candidates(node, free=5) == ["Payments-worker :8080", "Payments-worker"]
    assert label_candidates(Node("b", NodeKind.TOOL, "kubectl"), free=3) == ["kubectl"]


def test_the_palette_colours_every_kind_line_and_status() -> None:
    palette = Palette(
        accent="#111111",
        primary="#222222",
        secondary="#333333",
        warning="#444444",
        error="#555555",
        success="#666666",
        muted="#777777",
        foreground="#888888",
    )

    assert node_style(Node("a", NodeKind.DB, "db"), palette=palette) == "#333333"
    assert node_style(Node("a", NodeKind.CACHE, "c"), palette=palette) == "#333333"
    assert node_style(Node("a", NodeKind.BACKEND, "b"), palette=palette) == "#666666"
    assert node_style(Node("a", NodeKind.FRONTEND, "f"), palette=palette) == "#222222"
    assert node_style(Node("a", NodeKind.PROXY, "p"), palette=palette) == "#444444"
    assert node_style(Node("a", NodeKind.TOOL, "t"), palette=palette) == "#888888"
    assert node_style(Node("a", NodeKind.TUNNEL, "t"), palette=palette) == "#111111"
    assert node_style(Node("a", NodeKind.HOST, "h"), palette=palette) == "#777777"
    assert node_style(Node("a", NodeKind.CLUSTER, "k"), palette=palette) == "#777777"
    assert node_style(Node("a", NodeKind.SYSTEM, "s"), palette=palette) == "#777777"
    assert (
        node_style(Node("a", NodeKind.DB, "db", status="error"), palette=palette) == "bold #555555"
    )
    assert (
        node_style(Node("a", NodeKind.DB, "db", status="warning"), palette=palette)
        == "bold #444444"
    )

    live = Relation("a", "b", EdgeKind.CONNECTS)
    idle = Relation("a", "b", EdgeKind.CONNECTS, live=False)
    assert line_style(live, selected=None, palette=palette) == "#111111"
    assert line_style(idle, selected=None, palette=palette) == "#777777"
    assert line_style(live, selected="a", palette=palette) == "bold #222222"
    assert line_style(live, selected="elsewhere", palette=palette) == "#777777"


def test_theme_palette_takes_the_theme_colours_and_keeps_defaults_for_the_rest() -> None:
    palette = theme_palette(
        {
            "accent": "#ffa62b",
            "primary": "#0178d4",
            "success": "#4ebf71",
            "foreground": "#e0e0e0",
            "background": "#121212",
            "secondary": "auto 60%",
        }
    )

    assert palette.accent == "#FFA62B"
    assert palette.primary == "#0178D4"
    assert palette.success == "#4EBF71"
    assert palette.foreground == "#E0E0E0"
    assert palette.muted == "#6E6E6E"
    assert palette.secondary == DEFAULT_PALETTE.secondary
    assert theme_palette({}) == DEFAULT_PALETTE


async def test_zero_resets_the_rings_and_z_zooms_into_the_selected_star(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        await pilot.press("M")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, StarMapScreen)

        await pilot.press("plus", "plus")
        await pilot.pause()
        assert screen.scale == 1.2
        assert "rings ×1.2" in screen.footer_text(False)
        await pilot.press("0")
        await pilot.pause()
        assert screen.scale == 1.0
        assert "rings ×" not in screen.footer_text(False)
        assert "z zoom" in screen.footer_text(False)

        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("z")
        await pilot.pause()
        assert screen.zoomed and screen.scale == 1.3 and screen.focus_on is not None
        await pilot.press("z")
        await pilot.pause()
        assert not screen.zoomed and screen.scale == 1.0 and screen.focus_on is None


async def test_a_folded_panel_puts_the_selected_star_in_the_footer(app: LirtsApp) -> None:
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_rows(app, pilot)
        await pilot.press("M")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, StarMapScreen)
        assert screen.query_one("#map-panel").display is False
        assert "Tab select" in str(screen.query_one("#map-legend", Static).content)

        await pilot.press("tab")
        await pilot.pause()
        node = screen.graph.nodes[screen.selected or ""]
        line = str(screen.query_one("#map-legend", Static).content).splitlines()[1]
        assert node.label in line
        assert "relation" in line
        assert "Tab select" not in line


async def test_the_narrow_listing_marks_the_selected_star(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        await pilot.press("M")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, StarMapScreen)
        await pilot.press("tab")
        await pilot.pause()
        label = screen.graph.nodes[screen.selected or ""].label

        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        listing = screen.query_one("#map-canvas", Static).content.plain
        assert "▶ " in listing
        assert label in listing


async def test_the_map_is_redrawn_in_the_colours_of_a_new_theme(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        await pilot.press("M")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, StarMapScreen)
        before = screen.palette

        app.theme = "gruvbox"
        for _ in range(4):
            await pilot.pause()

        assert screen.palette != before
        assert screen.palette == theme_palette(app.get_css_variables())
        await pilot.press("escape")
        await pilot.pause()
        assert app.theme_changed_signal._subscriptions.get(screen) is None
