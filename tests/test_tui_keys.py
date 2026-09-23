"""The keys that had no pilot test yet: q, r, c, e, w, R and the command palette."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest

from lirts.models import Identity, SshSession
from lirts.tui.app import LirtsApp
from lirts.tui.screens import GraphScreen, ReachScreen
from tests.conftest import make_listener, make_process, wait_rows


def _spy_on_toasts(app: LirtsApp) -> list[str]:
    """Collect every notification the app raises, while still showing it."""
    toasts: list[str] = []
    original = app.notify

    def spy(message: Any, **kw: Any) -> None:
        toasts.append(str(message))
        original(message, **kw)

    app.notify = spy  # type: ignore[method-assign]  # test spy
    return toasts


async def test_q_quits_the_app(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)

        await pilot.press("q")
        await pilot.pause()

        assert not app.is_running or app._exit


async def test_r_collects_again(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        before = app.engine.refreshes  # type: ignore[attr-defined]  # FakeEngine test double

        await pilot.press("r")
        await pilot.pause()
        await asyncio.sleep(0.1)
        await pilot.pause()

        assert app.engine.refreshes > before  # type: ignore[attr-defined]  # FakeEngine test double


async def test_c_copies_the_url_of_the_current_row(
    app: LirtsApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without pyperclip the terminal clipboard is used, so no test touches the real one.
    monkeypatch.setitem(sys.modules, "pyperclip", None)
    toasts = _spy_on_toasts(app)

    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)

        await pilot.press("c")
        await pilot.pause()

    assert any("URL copied" in t and "http://localhost:5173/" in t for t in toasts)


async def test_c_copies_the_exec_command_of_a_container_row(
    app: LirtsApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "pyperclip", None)
    toasts = _spy_on_toasts(app)

    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)
        table.move_cursor(row=app._row_order.index("6379/TCP"))
        await pilot.pause()

        await pilot.press("c")
        await pilot.pause()

    assert any("docker exec command copied" in t for t in toasts)
    assert any("docker exec -it" in t for t in toasts)


async def test_e_is_offered_on_a_container_row_only(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)

        assert app.check_action("docker_exec", ()) is None  # 5173 is a local process

        table.move_cursor(row=app._row_order.index("6379/TCP"))
        await pilot.pause()
        assert app.check_action("docker_exec", ()) is True


async def test_e_copies_the_command_when_the_terminal_cannot_be_suspended(
    app: LirtsApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "pyperclip", None)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "lirts.tui.app_actions.subprocess.call", lambda cmd, *a, **kw: calls.append(cmd) or 0
    )
    toasts = _spy_on_toasts(app)

    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)
        table.move_cursor(row=app._row_order.index("6379/TCP"))
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()

    # The headless driver cannot suspend, so the command is handed over instead of run.
    assert calls == []
    assert any("Cannot open a shell here" in t and "docker exec -it" in t for t in toasts)


async def test_w_opens_the_who_talks_to_whom_screen(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)

        await pilot.press("w")
        await pilot.pause()

        assert isinstance(app.screen, GraphScreen)
        body = app.screen.query_one("#graph-content")
        assert body is not None
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, GraphScreen)


async def test_r_is_refused_on_a_row_that_is_not_an_ssh_session(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)

        assert app.check_action("reach", ()) is None

        await pilot.press("R")
        await pilot.pause()
        assert not isinstance(app.screen, ReachScreen)


async def test_r_checks_the_host_of_an_ssh_row(
    app: LirtsApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lirts.collectors.reach import ReachResult

    asked: list[tuple[str, int | None]] = []
    monkeypatch.setattr(
        "lirts.tui.screens.reach_namespace.check_host",
        lambda host, port=None: (
            asked.append((host, port))
            or ReachResult(host=host, port=port, ip="10.0.0.5", tcp_ms=1.5)
        ),
    )
    row = make_listener(port=22, name="ssh", processes=[make_process(pid=77, name="ssh")])
    row.state, row.source, row.row_id = "SSH", "ssh", "ssh:77"
    row.ssh = SshSession(pid=77, host="box", remote="10.0.0.5:22", user="me", cmd="ssh me@box")
    row.identity = Identity("ssh me@box", "ssh", 1.0)
    app.engine.listeners.append(row)  # type: ignore[attr-defined]  # FakeEngine test double

    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)
        table.move_cursor(row=app._row_order.index(row.key))
        await pilot.pause()
        assert app.check_action("reach", ()) is True

        await pilot.press("R")
        for _ in range(20):
            await pilot.pause()
            await asyncio.sleep(0.05)
            if asked:
                break

        assert isinstance(app.screen, ReachScreen)
        assert asked == [("10.0.0.5", 22)]
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ReachScreen)


async def test_ctrl_p_opens_the_command_palette(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)

        await pilot.press("ctrl+p")
        await pilot.pause()

        assert app.screen.__class__.__name__ == "CommandPalette"
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.__class__.__name__ != "CommandPalette"
