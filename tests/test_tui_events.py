"""The notification centre: toasts, unread counts and the events screen."""

from __future__ import annotations

import asyncio
import time

from lirts.tui.app import LirtsApp
from tests.conftest import wait_rows


async def test_old_events_are_not_toasted_and_events_screen(app: LirtsApp) -> None:
    from lirts.models import Event
    from lirts.tui.screens import EventsScreen

    toasts: list[str] = []
    original = app.notify

    def spy(message, **kw):
        toasts.append(str(message))
        return original(message, **kw)

    app.notify = spy  # type: ignore[method-assign]  # test spy
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        assert not any("old" in t for t in toasts)
        assert app.unread_events() == 0
        # a new error during the session is toasted and counted
        app.engine.history.events.append(
            Event(time.time() + 5, "error", "[!] port conflict on 8000/TCP", 8000)
        )  # type: ignore[attr-defined]  # FakeEngine test double
        app.refresh_data()
        await pilot.pause()
        await asyncio.sleep(0.1)
        await pilot.pause()
        assert app.unread_events() == 1
        assert any("8000/TCP" in t and "press n" in t for t in toasts)
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, EventsScreen)
        assert app.screen.query_one("#events-table").row_count == 2
        await pilot.press("enter")  # jump to 8000
        await pilot.pause()
        assert app.filter_text == "8000"
        assert app.unread_events() == 0


async def test_events_screen_summary(app: LirtsApp) -> None:
    from lirts.models import Event, Insight
    from lirts.tui.screens import EventsScreen

    engine = app.engine
    engine.listeners[1].insights.append(
        Insight("error", "port-conflict", "Port conflict", "stop one")
    )
    engine.history.events.append(
        Event(time.time() - 600, "error", "[!] port conflict on 8000/TCP", 8000)
    )  # type: ignore[attr-defined]  # FakeEngine test double
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, EventsScreen)
        table = app.screen.query_one("#events-table")
        states = [table.get_cell(str(i), "state").plain for i in range(table.row_count)]
        assert "ongoing" in states
