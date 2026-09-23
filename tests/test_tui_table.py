"""The main table: filtering, sorting, grouping, emphasis, the command palette."""

from __future__ import annotations

import asyncio
import time

from textual.widgets import DataTable, Input, Static

from lirts.models import Identity
from lirts.tui.app import LirtsApp
from lirts.tui.widgets import SidePanel
from tests.conftest import FakeEngine, make_listener, make_process, wait_rows


async def test_table_filter_sort_and_side_panel(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)
        assert table.row_count == 3
        assert app._selected_key == "5173/TCP"
        assert "3 listeners" in app.sub_title
        side = app.query_one("#side", SidePanel)
        details = side.query_one("#side-details", Static)
        rendered = "".join(
            strip.text
            for strip in details.visual.to_strips(
                details, details.visual, 60, 40, details.visual_style
            )
        )
        assert "Vite dev server" in rendered

        await pilot.press("slash")
        assert isinstance(app.focused, Input)
        await pilot.press(*"redis")
        await pilot.pause()
        assert table.row_count == 1 and app._row_order == ["6379/TCP"]
        await pilot.press("escape")
        await pilot.pause()
        assert table.row_count == 3 and app.filter_text == ""
        assert isinstance(app.focused, DataTable)

        await pilot.press("s")  # -> state
        await pilot.press("s")  # -> src
        await pilot.pause()
        assert app.sort_key == "src"
        assert app._row_order[0] == "6379/TCP"  # docker sorts before local
        await pilot.press("S")
        await pilot.pause()
        assert app.sort_reverse and app._row_order[-1] == "6379/TCP"

        await pilot.press("p")
        await pilot.pause()
        assert side.has_class("hidden")
        await pilot.press("p")
        await pilot.pause()
        assert not side.has_class("hidden")


async def test_table_updates_in_place(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)
        engine: FakeEngine = app.engine  # type: ignore[assignment]  # the fixture installs a FakeEngine
        engine.listeners[1].processes[0].cpu_percent = 99.0
        app.refresh_data()
        await pilot.pause()
        await asyncio.sleep(0.1)
        await pilot.pause()
        cell = table.get_cell("8000/TCP", "cpu")
        assert cell.plain == "99.0%"
        # a row disappearing triggers a rebuild that keeps the selection
        table.move_cursor(row=app._row_order.index("6379/TCP"))
        await pilot.pause()
        engine.listeners.pop(0)
        app.refresh_data()
        await pilot.pause()
        await asyncio.sleep(0.1)
        await pilot.pause()
        assert table.row_count == 2
        assert app._selected_key == "6379/TCP"


async def test_emphasis_and_problems_toggle(app: LirtsApp) -> None:
    from lirts.models import Insight

    engine = app.engine
    engine.listeners[1].insights.append(Insight("error", "port-conflict", "Port conflict"))  # 8000
    engine.listeners[2].insights.append(Insight("warning", "cpu-high", "High CPU"))  # 6379
    stopped = make_listener(
        port=9000, processes=[], state="STOPPED", identity=Identity("Old thing", "unknown", 0.0)
    )
    stopped.insights.append(Insight("warning", "stopped", "Stopped 1m ago"))
    engine.listeners.append(stopped)
    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)
        assert table.row_count == 4
        assert table.get_cell("8000/TCP", "service").plain.startswith("✖ ")
        assert table.get_cell("6379/TCP", "service").plain.startswith("⚠ ")
        assert "strike" in str(table.get_cell("9000/TCP", "port").spans[-1].style)
        assert table.get_cell("5173/TCP", "service").plain == "○ Vite dev server"
        await pilot.press("exclamation_mark")
        await pilot.pause()
        assert app.problems_only and table.row_count == 3
        assert "problems only" in app.sub_title
        await pilot.press("exclamation_mark")
        await pilot.pause()
        assert table.row_count == 4


async def test_grouped_view(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)
        await pilot.press("G")
        await pilot.pause()
        assert app.grouped
        assert "grouped" in app.sub_title
        # groups: "shop" (vite, project shop), "app" (redis container stack), "local" (python)
        assert app._row_order[0] == "group:app"
        assert app._row_order == [
            "group:app",
            "6379/TCP",
            "group:shop",
            "5173/TCP",
            "group:local",
            "8000/TCP",
        ]
        assert table.get_cell("group:app", "project").plain == "▾ app"
        assert table.get_cell("group:app", "service").plain == "1 service, 1 container"
        assert table.get_cell("6379/TCP", "project").plain == " └─"
        assert table.get_cell("6379/TCP", "service").plain == "◆ Redis"
        assert table.row_count == 6
        # collapse the first group with Space (marks nothing)
        table.move_cursor(row=0)
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert app._marked == set()
        assert (
            "6379/TCP" not in app._row_order
            and table.get_cell("group:app", "project").plain == "▸ app"
        )
        await pilot.press("enter")
        await pilot.pause()
        assert "6379/TCP" in app._row_order
        # group header actions: stop acts on every container of the group
        await pilot.press("x")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "ConfirmScreen"
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert app._marked == {"6379/TCP", "5173/TCP", "8000/TCP"}
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("G")
        await pilot.pause()
        assert not app.grouped and table.row_count == 3


async def test_sort_headers_and_new_highlight(app: LirtsApp) -> None:
    engine = app.engine
    app.config["insights"]["highlight_new_minutes"] = 3
    engine.listeners[0].processes[0].create_time = time.time() - 30  # started 30 s ago
    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)
        port_key = next(k for k in table.columns if k.value == "port")
        assert table.columns[port_key].label.plain == "PORT ▲"
        assert table.get_cell("5173/TCP", "service").plain.startswith("✚ ")
        await pilot.press("s")  # -> state
        await pilot.pause()
        labels = {k.value: table.columns[k].label.plain for k in table.columns}
        assert labels["state"] == "STATE ▲" and labels["port"] == "PORT"
        await pilot.press("S")
        await pilot.pause()
        labels = {k.value: table.columns[k].label.plain for k in table.columns}
        assert labels["state"] == "STATE ▼"
        assert table.row_count == 3
        await pilot.press("p")
        await pilot.pause()
        assert app.query_one("#side").has_class("hidden")


async def test_structured_filter_palette_and_persistence(app: LirtsApp, tmp_path) -> None:
    from lirts.tui.app import LirtsCommands

    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)
        app.set_filter("src:docker")
        await pilot.pause()
        assert app._row_order == ["6379/TCP"]
        app.set_filter("-src:docker port:5000-6000")
        await pilot.pause()
        assert app._row_order == ["5173/TCP"]
        app.set_filter("")
        await pilot.pause()
        assert table.row_count == 3

        provider = LirtsCommands(app.screen)
        hits = [h async for h in provider.search("kube")]
        assert any("Kubernetes" in str(h.match_display) for h in hits)
        names = [str(h.display) for h in [h async for h in provider.discover()]]
        assert "Settings" in names and "Quit" in names
        field_hits = [h async for h in provider.search("stat")]
        assert any("filter status" in str(h.match_display) for h in field_hits)

        # sort persistence only touches an existing config file
        assert not app.config.get("_exists")
        await pilot.press("s")
        await pilot.pause()
        assert app.config["sort_by"] == "state"
        cfg_path = tmp_path / "persist.yaml"
        cfg_path.write_text("theme: nord\n")
        app.config["_path"] = str(cfg_path)
        app.config["_exists"] = True
        await pilot.press("S")
        await pilot.pause()
        import yaml

        saved = yaml.safe_load(cfg_path.read_text())
        assert saved["sort_by"] == "state" and saved["sort_desc"] is True


async def test_ssh_row_renders_and_kube_binding_disabled_without_kubectl(app: LirtsApp) -> None:
    from lirts.models import SshSession

    engine = app.engine
    row = make_listener(port=22, name="ssh", processes=[make_process(pid=77, name="ssh")])
    row.state, row.source, row.row_id = "SSH", "ssh", "ssh:77"
    row.ssh = SshSession(pid=77, host="box", remote="10.0.0.5:22", user="me", cmd="ssh me@box")
    row.identity = Identity("ssh me@box", "ssh", 1.0)
    engine.listeners.append(row)  # type: ignore[attr-defined]  # FakeEngine keeps its rows in a list
    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)
        assert table.row_count == 4
        cells = app._render_cells(row, "")
        by_key = dict(zip([c.key for c in app.columns], cells, strict=True))
        assert by_key["port"].plain == "→22" and by_key["service"].plain == "⇄ ssh me@box"
        assert by_key["state"].plain == "SSH" and by_key["src"].plain == "⇄ ssh"
        assert app.check_action("kubernetes", ()) is None  # FakeEngine has no kubectl
        await pilot.press("K")
        await pilot.pause()
        assert app.screen is app.screen_stack[0]  # nothing opened
        app.set_filter("src:ssh")
        await pilot.pause()
        assert [x.port for x in app.visible_listeners()] == [22]
