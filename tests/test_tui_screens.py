"""Opening the secondary screens: details, help, explain, logs, health, net panel."""

from __future__ import annotations

import asyncio
import io
from typing import Any

from textual.widgets import DataTable, Static

from lirts.config import load_config
from lirts.tui.app import LirtsApp
from lirts.tui.screens import DetailsScreen, ExplainScreen, HelpScreen
from tests.conftest import wait_rows


async def test_screens(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        await pilot.press("i")
        await pilot.pause()
        assert isinstance(app.screen, DetailsScreen)
        assert app.screen.listener.port == 5173
        # live update keeps the details screen in sync
        app.refresh_data()
        await pilot.pause()
        await asyncio.sleep(0.1)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, DetailsScreen)

        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("E")
        await pilot.pause()
        assert isinstance(app.screen, ExplainScreen)
        await pilot.press("escape")
        await pilot.pause()

        # docker actions on the redis row
        table = app.query_one("#table", DataTable)
        table.move_cursor(row=app._row_order.index("6379/TCP"))
        await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        await asyncio.sleep(0.2)
        await pilot.pause()
        assert app.screen.__class__.__name__ == "LogScreen"
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        await asyncio.sleep(0.2)
        await pilot.press("x")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "ConfirmScreen"
        await pilot.press("y")
        await pilot.pause()
        await asyncio.sleep(0.2)
        await pilot.pause()
        # docker actions on a local row are refused politely
        table.move_cursor(row=app._row_order.index("8000/TCP"))
        await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        assert app.screen.__class__.__name__ != "LogScreen"


def test_help_lists_every_key_of_the_dashboard() -> None:
    from lirts.tui.screens import KEY_HELP

    keys = {key for key, _ in KEY_HELP}
    assert "/ / Ctrl+F" in keys
    assert "T" in keys and "U" in keys and "Y" in keys and "F" in keys
    meanings = dict(KEY_HELP)
    assert "style" in meanings["T"]
    assert "layout" in meanings["U"]
    assert "history panel" in meanings["Y"]
    assert meanings["Esc"] == (
        "clear the filter, then the marks; with nothing to clear, open the menu"
    )


async def test_health_screen_shows_results(app: LirtsApp) -> None:
    from lirts.tui.screens import HealthScreen

    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        await pilot.press("H")
        await pilot.pause()
        await asyncio.sleep(0.1)
        await pilot.pause()
        assert isinstance(app.screen, HealthScreen)
        screen = app.screen
        table = screen.query_one("#health-table", DataTable)
        assert table.row_count == 3
        # failing rows first
        assert screen._rows[0].port == 8000
        verdicts = {x.port: screen.verdict(x) for x in screen._rows}
        assert verdicts[8000] == (False, "unreachable: refused")
        assert verdicts[5173] == (True, "ok")
        assert verdicts[6379] == (True, "ok (TCP only)")
        from rich.console import Console

        console = Console(record=True, width=160, force_terminal=False, file=io.StringIO())
        console.print(screen.query_one("#health-summary", Static).visual._renderable)  # type: ignore[union-attr]  # the rendered Static has a visual
        summary = console.export_text()
        assert "1 failing" in summary and "8000 FastAPI: unreachable: refused" in summary
        await pilot.press("f")
        await pilot.pause()
        assert table.row_count == 1
        await pilot.press("enter")  # jump to 8000
        await pilot.pause()
        assert app.filter_text == "8000"
        assert not isinstance(app.screen, HealthScreen)
        row = next(x for x in app.snapshot.listeners if x.port == 8000)
        assert row.health.checked and not row.health.ok


async def test_explain_screen_tabs(app: LirtsApp) -> None:
    from textual.widgets import TabbedContent

    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        await pilot.press("E")
        await pilot.pause()
        assert isinstance(app.screen, ExplainScreen)
        tabs = app.screen.query_one("#explain-tabs", TabbedContent)
        assert tabs.active == "explain-summary"
        await pilot.press("right")
        await pilot.pause()
        assert tabs.active != "explain-summary"
        await pilot.press("left")
        await pilot.pause()
        assert tabs.active == "explain-summary"
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ExplainScreen)


async def test_net_panel_scopes_and_toggle(app: LirtsApp) -> None:
    from lirts.tui.netpanel import NetPanel

    async with app.run_test(size=(160, 50)) as pilot:
        await wait_rows(app, pilot)
        panel = app.query_one("#net", NetPanel)
        assert panel.display and "· whole machine" in str(panel.border_title)
        await pilot.press("N")
        await pilot.pause()
        assert "· whole machine" not in str(panel.border_title)
        assert ":5173" in str(panel.border_title) or ":8000" in str(panel.border_title)
        await pilot.press("N")
        await pilot.pause()
        assert "· whole machine" in str(panel.border_title)
        app.config["net_panel"] = False
        app.apply_setting("net_panel", False)
        await pilot.pause()
        assert not panel.display


async def test_dashboard_runs_on_the_demo_engine(tmp_path: Any) -> None:
    from lirts.demo import DemoEngine
    from lirts.tui.screens import (
        DetailsScreen,
        ExplainScreen,
        GraphScreen,
        HealthScreen,
        KubeScreen,
        StackScreen,
    )

    cfg = load_config(tmp_path / "c.yaml")
    cfg["refresh_interval"] = 60.0
    engine = DemoEngine(cfg)
    app = LirtsApp(engine, cfg)
    async with app.run_test(size=(170, 50)) as pilot:
        table = await wait_rows(app, pilot)
        assert table.row_count >= 15 and "DEMO" in app.sub_title
        for key, screen in (
            ("i", DetailsScreen),
            ("g", StackScreen),
            ("w", GraphScreen),
            ("E", ExplainScreen),
            ("K", KubeScreen),
        ):
            await pilot.press(key)
            for _ in range(20):
                await pilot.pause()
                await asyncio.sleep(0.05)
                if isinstance(app.screen, screen):
                    break
            assert isinstance(app.screen, screen), key
            await pilot.press("escape")
            await pilot.pause()
        await pilot.press("H")
        for _ in range(30):
            await pilot.pause()
            await asyncio.sleep(0.05)
            if isinstance(app.screen, HealthScreen) and app.screen.results:
                break
        assert isinstance(app.screen, HealthScreen) and app.screen.results
        await pilot.press("escape")
        await pilot.pause()
    engine.close()


async def test_details_tabs_all_render_for_a_container_row(app: LirtsApp) -> None:
    from textual.widgets import TabbedContent

    redis = next(x for x in app.engine.listeners if x.container)  # type: ignore[attr-defined]  # FakeEngine test double
    redis.cpu_history = [1.0, 2.0, 3.0]
    redis.conn_history = [1, 2, 3]
    assert redis.container is not None
    redis.container.cpu_percent, redis.container.memory_mb = 12.5, 64.0
    redis.container.net_rx_rate, redis.container.net_tx_rate = 2048.0, 512.0

    async with app.run_test(size=(170, 50)) as pilot:
        table = await wait_rows(app, pilot)
        table.move_cursor(row=app._row_order.index("6379/TCP"))
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause()

        assert isinstance(app.screen, DetailsScreen)
        tabs = app.screen.query_one("#details-tabs", TabbedContent)
        panes = [str(pane.id) for pane in tabs.query("TabPane")]
        assert panes == [
            "tab-overview",
            "tab-processes",
            "tab-docker",
            "tab-conns",
            "tab-history",
            "tab-identity",
        ]
        for body_id in ("#ov-body", "#pr-body", "#dk-body", "#cn-body", "#hs-body", "#id-body"):
            assert app.screen.query_one(body_id, Static).visual is not None


async def test_bindings_are_dimmed_when_the_row_cannot_use_them(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)

        # 5173 is a local TCP process: no container, so the Docker keys are unavailable.
        assert app.check_action("docker_logs", ()) is None
        assert app.check_action("docker_stop", ()) is None
        assert app.check_action("kill", ()) is True
        assert app.check_action("open_browser", ()) is True

        table.move_cursor(row=app._row_order.index("6379/TCP"))
        await pilot.pause()
        assert app.check_action("docker_logs", ()) is True
        assert app.check_action("docker_stop", ()) is True
