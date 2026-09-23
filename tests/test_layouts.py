"""Layouts (U), where they put the panels, the history box (Y) and the side panel sections."""

from __future__ import annotations

import io
from dataclasses import replace

import pytest
from rich.console import Console, RenderableType

from lirts.collectors.kube_models import KubePod, KubeState
from lirts.config import load_config
from lirts.models import HealthResult, Identity, SshSession
from lirts.tui.app import LirtsApp
from lirts.tui.histpanel import HistoryPanel
from lirts.tui.layout import LAYOUTS, SidePosition
from lirts.tui.netpanel import NetPanel
from lirts.tui.widgets import Section, SidePanel, render_details
from tests.conftest import FakeEngine, make_container, make_listener, wait_rows

SPARK_GLYPHS = "▁▂▃▄▅▆▇█"


def render_text(renderable: RenderableType, width: int = 120) -> str:
    """A Rich renderable as plain text, the way the panel draws it."""
    console = Console(width=width, file=io.StringIO(), record=True, legacy_windows=False)
    console.print(renderable)
    return console.export_text()


def fill_history(app: LirtsApp) -> None:
    """Give the first fixture row a measured history to draw."""
    row = app.engine.listeners[0]  # type: ignore[attr-defined]  # FakeEngine test double
    row.cpu_history = [1.0, 5.0, 12.0, 3.0]
    row.conn_history = [1, 2, 3, 2]
    row.latency_history = [10.0, 25.0, 12.0]
    row.bytes_in_history = [100.0, 4000.0, 250.0]
    row.bytes_out_history = [50.0, 80.0, 900.0]
    row.activity_history = ["idle", "active", "hot"]
    row.bytes_in_rate = 250.0
    row.bytes_out_rate = 900.0


def boxes_under_the_table(app: LirtsApp) -> list[str]:
    """The ids of the widgets under the table, in the order they are drawn."""
    children = [child.id for child in app.query_one("#left").children]
    return children[children.index("table") + 1 :]


async def test_layout_changes_the_columns_and_the_panels_and_back(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        saved_columns = list(app.config["columns"])

        app.apply_layout("hosts")
        await pilot.pause()
        assert [c.key for c in app.columns] == [
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
        ]
        assert not app.query_one("#net", NetPanel).has_class("hidden")
        assert not app.query_one("#side", SidePanel).has_class("hidden")
        assert app.query_one("#side", SidePanel).sections == (
            Section.HOSTS,
            Section.IDENTITY,
            Section.RUNTIME,
            Section.INSIGHTS,
        )
        assert app.config["columns"] == saved_columns  # a layout never rewrites the setting

        app.apply_layout("containers")
        await pilot.pause()
        assert [c.key for c in app.columns][:5] == ["port", "state", "src", "stack", "container"]
        assert not app.query_one("#net", NetPanel).has_class("hidden")
        assert not app.query_one("#history", HistoryPanel).has_class("hidden")

        app.apply_layout("default")
        await pilot.pause()
        assert [c.key for c in app.columns] == saved_columns
        assert not app.query_one("#net", NetPanel).has_class("hidden")
        assert app.query_one("#history", HistoryPanel).has_class("hidden")
        assert app.query_one("#side", SidePanel).sections == LAYOUTS["default"].side_sections


async def test_each_layout_puts_the_side_panel_where_it_says_and_back(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        side = app.query_one("#side", SidePanel)
        assert side.parent is not None and side.parent.id == "main"
        assert boxes_under_the_table(app) == ["net", "history"]

        app.apply_layout("containers")
        await pilot.pause()
        assert side.parent is not None and side.parent.id == "main"
        assert boxes_under_the_table(app) == ["history", "net"]

        app.apply_layout("hosts")
        await pilot.pause()
        assert side.parent is not None and side.parent.id == "bottom"
        assert side.region.width == 160 and side.region.height == 12
        assert boxes_under_the_table(app) == ["net", "history"]

        app.apply_layout("default")
        await pilot.pause()
        assert side.parent is not None and side.parent.id == "main"
        assert side.region.width == 44
        assert boxes_under_the_table(app) == ["net", "history"]


async def test_a_layout_can_put_the_side_panel_on_the_left(app: LirtsApp) -> None:
    on_the_left = replace(LAYOUTS["default"], side_position=SidePosition.LEFT)
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        side = app.query_one("#side", SidePanel)

        app._place_side_panel(on_the_left)
        await pilot.pause()

        assert side.parent is not None and side.parent.id == "side-left"
        assert side.region.x == 0 and side.region.width == 44


async def test_u_cycles_the_layouts_and_writes_only_the_layout_setting(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        assert app.layout_name == "default"
        before = {
            "side_panel": app.config["side_panel"],
            "net_panel": app.config["net_panel"],
            "history_panel": app.config["ui"]["history_panel"],
            "columns": list(app.config["columns"]),
        }

        await pilot.press("U")
        await pilot.pause()
        assert app.layout_name == "containers"
        assert app.config["ui"]["layout"] == "containers"

        await pilot.press("U")
        await pilot.pause()
        assert app.layout_name == "hosts" and app.config["ui"]["layout"] == "hosts"
        assert app.sort_key in [c.key for c in app.columns]

        await pilot.press("U")
        await pilot.pause()
        assert app.layout_name == "default" and app.config["ui"]["layout"] == "default"
        assert app.config["side_panel"] == before["side_panel"]
        assert app.config["net_panel"] == before["net_panel"]
        assert app.config["ui"]["history_panel"] == before["history_panel"]
        assert app.config["columns"] == before["columns"]


@pytest.mark.parametrize("layout", ["default", "containers", "hosts"])
async def test_toggling_the_history_box_never_hides_the_traffic_box(layout: str) -> None:
    config = load_config()
    config["refresh_interval"] = 60.0
    config["ui"]["layout"] = layout
    app = LirtsApp(FakeEngine(), config)  # type: ignore[arg-type]  # FakeEngine stands in

    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        net = app.query_one("#net", NetPanel)
        assert not net.has_class("hidden")

        await pilot.press("Y")
        await pilot.pause()
        assert not net.has_class("hidden") and net.region.height > 0

        await pilot.press("Y")
        await pilot.pause()
        assert not net.has_class("hidden") and net.region.height > 0


async def test_y_toggles_the_history_box_and_it_draws_the_selected_row(app: LirtsApp) -> None:
    fill_history(app)
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        assert app._selected_key == "5173/TCP"
        panel = app.query_one("#history", HistoryPanel)
        assert panel.has_class("hidden")  # composed with the rest, hidden until Y

        await pilot.press("Y")
        await pilot.pause()
        assert not panel.has_class("hidden")
        assert app.config["ui"]["history_panel"] is True
        out = render_text(panel.render())
        assert "CPU" in out and "Conns" in out and "Latency" in out and "Activity" in out
        assert any(glyph in out for glyph in SPARK_GLYPHS)
        assert "250 B/s" in out and "900 B/s" in out and "4 samples" in out

        await pilot.press("Y")
        await pilot.pause()
        assert panel.has_class("hidden")
        assert app.config["ui"]["history_panel"] is False


async def test_history_box_follows_the_cursor(app: LirtsApp) -> None:
    fill_history(app)
    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)
        await pilot.press("Y")
        await pilot.pause()
        panel = app.query_one("#history", HistoryPanel)
        assert panel.listener is not None and panel.listener.port == 5173

        table.move_cursor(row=app._row_order.index("6379/TCP"))
        await pilot.pause()
        assert panel.listener is not None and panel.listener.port == 6379
        assert "Redis" in str(panel.border_title)


async def test_the_saved_layout_and_history_setting_are_applied_at_start_up() -> None:
    cfg = load_config()
    cfg["refresh_interval"] = 60.0
    cfg["ui"]["layout"] = "hosts"
    cfg["ui"]["history_panel"] = True
    app = LirtsApp(FakeEngine(), cfg)  # type: ignore[arg-type]  # FakeEngine stands in for Engine

    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        await pilot.pause()
        assert app.layout_name == "hosts"
        assert "project" not in [c.key for c in app.columns]
        side = app.query_one("#side", SidePanel)
        assert side.parent is not None and side.parent.id == "bottom"
        assert not app.query_one("#net", NetPanel).has_class("hidden")
        assert not app.query_one("#history", HistoryPanel).has_class("hidden")
        # Nothing of the user's was rewritten on the way in.
        assert cfg["ui"]["layout"] == "hosts" and cfg["ui"]["history_panel"] is True
        assert cfg["side_panel"] is True and cfg["net_panel"] is True

        app.apply_layout_setting("ui.history_panel", False)
        await pilot.pause()
        assert app.query_one("#history", HistoryPanel).has_class("hidden")
        assert not app.query_one("#net", NetPanel).has_class("hidden")

        app.apply_layout_setting("ui.layout", "containers")
        await pilot.pause()
        assert app.layout_name == "containers"
        assert not app.query_one("#net", NetPanel).has_class("hidden")


def test_cluster_section_shows_the_context_and_the_pods_of_a_container_row() -> None:
    listener = make_listener(
        port=6379,
        container=make_container(name="app-redis-1", image="redis:7", host_ports=[6379]),
        identity=Identity("Redis", "cache", 0.95),
    )
    kube = KubeState(
        available=True,
        context="kind-dev",
        namespace="shop",
        pods=[
            KubePod(
                name="redis-0",
                namespace="shop",
                phase="Running",
                ready=1,
                total=1,
                restarts=0,
                created=None,
                node="node-1",
                owner_kind="StatefulSet",
                owner="redis",
            ),
            KubePod(
                name="api-7d",
                namespace="shop",
                phase="Pending",
                ready=0,
                total=1,
                restarts=3,
                created=None,
                node=None,
                owner_kind="Deployment",
                owner="api",
                reason="CrashLoopBackOff",
            ),
        ],
    )

    out = render_text(
        render_details(
            listener,
            sections=LAYOUTS["containers"].side_sections,
            kube=kube,
        )
    )

    assert "app-redis-1" in out and "redis:7" in out
    assert "kind-dev" in out and "shop" in out
    assert "2 pods" in out and "1 unhealthy" in out


def test_hosts_section_shows_the_ssh_session_the_tunnel_and_the_domains() -> None:
    listener = make_listener(
        port=8080,
        state="SSH",
        row_id="ssh:4242",
        ssh=SshSession(
            pid=4242,
            host="build01",
            remote="10.0.0.5:22",
            user="timo",
            tty="ttys004",
            forwards=["8080:localhost:80"],
        ),
        tunnel={"type": "ssh", "host": "build01", "forwards": [(8080, "localhost", 80)]},
        hosts=["api.test"],
        proxy_targets=[3000],
        health=HealthResult(checked=True, tcp_ok=True, tcp_latency_ms=12.0),
    )

    out = render_text(render_details(listener, sections=LAYOUTS["hosts"].side_sections))

    assert "timo@build01" in out and "10.0.0.5:22" in out and "ttys004" in out
    assert "8080:localhost:80" in out
    assert "build01 → localhost:80" in out
    assert "tcp 12ms" in out
    assert "3000" in out and "api.test" in out
