"""The compose stack screen and the local restart it can trigger."""

from __future__ import annotations

import asyncio

from textual.widgets import DataTable

from lirts.tui.app import LirtsApp
from tests.conftest import make_container, wait_rows


async def test_stack_restart_and_project_folder(app: LirtsApp) -> None:
    from lirts.tui.screens import RestartScreen, StackScreen

    engine = app.engine
    web = make_container(
        name="app-redis-1",
        image="redis:7",
        host_ports=[6379],
        stack="app",
        service="redis",
        working_dir="/tmp/app",
    )
    engine.containers = [web]  # type: ignore[attr-defined]  # FakeEngine test double
    engine.stacks = lambda: {"app": [web]}  # type: ignore[attr-defined]  # FakeEngine test double
    stack_calls: list = []
    engine.stack_action = lambda project, action: (
        stack_calls.append((project, action)) or [("app-redis-1", True, action)]
    )  # type: ignore[attr-defined]  # FakeEngine test double
    engine.stack_logs = lambda project, tail=100: "redis | ready"  # type: ignore[attr-defined]  # FakeEngine test double
    opened: list[str] = []
    engine.open_path = lambda path: opened.append(path) or (True, path)  # type: ignore[attr-defined]  # FakeEngine test double
    engine.project_dir = lambda lst: "/tmp/app" if lst.container else None  # type: ignore[attr-defined]  # FakeEngine test double
    engine.restart_plan = lambda lst: (
        (["python", "app.py"], "/tmp/x") if not lst.container else None
    )  # type: ignore[attr-defined]  # FakeEngine test double
    restarts: list = []
    engine.restart_process = lambda lst, force=False: (
        restarts.append((lst.port, force)) or (True, "started PID 1")
    )  # type: ignore[attr-defined]  # FakeEngine test double
    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)
        await pilot.press("g")
        await pilot.pause()
        assert isinstance(app.screen, StackScreen)
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        await asyncio.sleep(0.2)
        await pilot.pause()
        assert stack_calls == [("app", "restart")]
        await pilot.press("l")
        await pilot.pause()
        await asyncio.sleep(0.2)
        assert app.screen.__class__.__name__ == "LogScreen"
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()
        assert opened == ["/tmp/app"]
        await pilot.press("enter")  # filter by stack
        await pilot.pause()
        assert app.filter_text == "app"
        await pilot.press("escape")
        await pilot.pause()

        # local restart on the python row
        table.move_cursor(row=app._row_order.index("8000/TCP"))
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        assert isinstance(app.screen, RestartScreen)
        await pilot.press("enter")
        await pilot.pause()
        await asyncio.sleep(0.2)
        assert restarts == [(8000, False)]

        # open project folder for the docker row
        table.move_cursor(row=app._row_order.index("6379/TCP"))
        await pilot.pause()
        await pilot.press("O")
        await pilot.pause()
        assert opened[-1] == "/tmp/app"


async def test_stack_screen_traffic_column(app: LirtsApp) -> None:
    from lirts.tui.screens import StackScreen

    engine = app.engine
    redis = next(x for x in engine.listeners if x.container)  # type: ignore[attr-defined]  # FakeEngine test double
    assert redis.container is not None
    redis.container.net_rx_rate, redis.container.net_tx_rate = 2048.0, 512.0
    redis.container.net_rx_bytes, redis.container.net_tx_bytes = 10_000, 5_000
    engine.containers = [redis.container]  # type: ignore[attr-defined]  # FakeEngine test double
    engine.stacks = lambda: {"app": [redis.container]}  # type: ignore[attr-defined]  # FakeEngine test double
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        await pilot.press("g")
        await pilot.pause()
        assert isinstance(app.screen, StackScreen)
        table = app.screen.query_one("#stack-table", DataTable)
        assert [str(c.label) for c in table.columns.values()][4] == "TRAFFIC"
        cell = table.get_cell("app", "net")
        assert "↓2.0KB/s" in cell.plain and "↑512B/s" in cell.plain
