"""Row actions: kill, marking, the toggles and what Esc clears."""

from __future__ import annotations

import asyncio

from textual.widgets import DataTable

from lirts.tui.app import LirtsApp
from lirts.tui.screens import KillScreen
from tests.conftest import wait_rows


async def test_kill_flow(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)
        table.move_cursor(row=app._row_order.index("8000/TCP"))
        await pilot.pause()
        await pilot.press("k")
        await pilot.pause()
        assert isinstance(app.screen, KillScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert app.engine.killed == []  # type: ignore[attr-defined]  # FakeEngine test double
        await pilot.press("k")
        await pilot.pause()
        await pilot.press("k")  # kill -9
        await pilot.pause()
        await asyncio.sleep(0.1)
        assert app.engine.killed == [([42], True)]  # type: ignore[attr-defined]  # FakeEngine test double


async def test_toggles_and_theme(app: LirtsApp, tmp_path) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        before = app.theme
        app.action_cycle_theme()  # T cycles styles; the theme cycles from the palette
        await pilot.pause()
        assert app.theme != before
        await pilot.press("u")
        await pilot.pause()
        assert app.engine.show_udp is True
        await pilot.press("h")
        await pilot.pause()
        assert app.engine.hide_system is True
        await pilot.press("o")
        await pilot.pause()


async def test_marking_and_bulk_actions(app: LirtsApp) -> None:
    from lirts.tui.screens import KillScreen as _Kill

    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)
        await pilot.press("space")  # marks 5173 and moves down
        await pilot.press("space")  # marks 6379 (rows are sorted by port)
        await pilot.pause()
        assert app._marked == {"5173/TCP", "6379/TCP"}
        assert "2 marked" in app.sub_title
        assert table.get_cell("5173/TCP", "mark").plain == "✓"
        await pilot.press("k")
        await pilot.pause()
        assert isinstance(app.screen, _Kill)
        assert len(app.screen.others) == 1
        await pilot.press("t")
        await pilot.pause()
        await asyncio.sleep(0.1)
        killed = app.engine.killed  # type: ignore[attr-defined]  # FakeEngine test double
        assert killed and sorted(killed[0][0]) == sorted([15173, 7])
        assert app._marked == set()
        await pilot.press("a")
        await pilot.pause()
        assert len(app._marked) == 3
        await pilot.press("a")
        await pilot.pause()
        assert app._marked == set()
        await pilot.press("space")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app._marked == set()


async def test_loading_indicator_and_escape_clears_then_opens_the_menu(app: LirtsApp) -> None:
    from lirts.tui.screens import MenuScreen

    async with app.run_test(size=(160, 45)) as pilot:
        table = app.query_one("#table", DataTable)
        await wait_rows(app, pilot)
        assert table.loading is False
        await pilot.press("slash", *"x", "escape")  # clears the filter first
        await pilot.pause()
        assert app.filter_text == "" and app.is_running
        await pilot.press("space", "escape")  # clears marks next
        await pilot.pause()
        assert app._marked == set() and app.is_running
        await pilot.press("escape")  # nothing left to clear: the menu
        await pilot.pause()
        assert isinstance(app.screen, MenuScreen) and app.is_running
        await pilot.press("q")  # Quit is one of its entries
        await pilot.pause()
        assert not app.is_running or app._exit
