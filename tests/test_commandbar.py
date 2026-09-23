"""Command mode: a second `/` in the filter box turns it into a command line with a dropdown."""

from __future__ import annotations

from textual.widgets import Input

from lirts.models import Insight, Level
from lirts.tui.app import LirtsApp
from lirts.tui.commandbar import CommandBar
from lirts.tui.screens import HelpScreen
from tests.conftest import wait_rows


async def _open_command_mode(app: LirtsApp, pilot) -> CommandBar:
    """Press `/` twice from the table and return the dropdown that appears."""
    await pilot.press("slash")
    await pilot.pause()
    await pilot.press("slash")
    await pilot.pause()
    await pilot.pause()
    return app.query_one("#commandbar", CommandBar)


async def test_slash_focuses_the_filter_box_without_entering_command_mode(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)

        await pilot.press("slash")
        await pilot.pause()

        assert app.query_one("#filter", Input).has_focus
        assert app.command_mode is False
        assert not app.query(CommandBar)


async def test_ctrl_f_focuses_the_filter_box(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)

        await pilot.press("ctrl+f")
        await pilot.pause()

        assert app.query_one("#filter", Input).has_focus
        assert app.command_mode is False


async def test_a_second_slash_opens_the_command_dropdown(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)

        bar = await _open_command_mode(app, pilot)

        assert app.command_mode is True
        assert bar.display is True
        assert app.query_one("#filter", Input).has_class("command")
        assert app.filter_text == ""
        names = [entry.name for entry in bar.entries]
        assert "Help" in names and "Quit" in names
        assert bar.highlighted_entry is not None


async def test_the_dropdown_shows_the_key_of_each_command(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)

        bar = await _open_command_mode(app, pilot)

        labels = {entry.name: entry.label for entry in bar.entries}
        assert labels["Quit"] == "Quit  Exit lirts (q)"
        assert labels["Help"] == "Help  Keyboard shortcuts (?)"
        assert labels["Details"] == "Details  Open the extended details of the current row (i)"


async def test_a_command_the_current_row_cannot_take_is_dimmed(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)

        bar = await _open_command_mode(app, pilot)

        logs = next(entry for entry in bar.entries if entry.name == "Container logs")
        assert logs.enabled is False
        assert logs.label.endswith("(not for this row)")


async def test_typing_f_narrows_the_list_and_offers_fix_when_the_row_has_one(
    app: LirtsApp,
) -> None:
    app.engine.listeners[0].insights = [  # type: ignore[attr-defined]  # FakeEngine test double
        Insight(Level.ERROR, "unreachable-tcp", "hung", "restart it", "restart-process")
    ]

    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        bar = await _open_command_mode(app, pilot)

        await pilot.press("f")
        await pilot.pause()

        names = [entry.name for entry in bar.entries]
        assert names[0] == "Fix"
        assert "Quit" not in names
        assert app.filter_text == ""


async def test_fix_is_left_out_when_the_current_row_has_nothing_to_fix(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        bar = await _open_command_mode(app, pilot)

        await pilot.press("f")
        await pilot.pause()

        names = [entry.name for entry in bar.entries]
        assert "Fix" not in names
        assert "Open project folder" in names


async def test_down_then_enter_runs_the_highlighted_command(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        bar = await _open_command_mode(app, pilot)

        await pilot.press("h")
        await pilot.pause()
        assert [entry.name for entry in bar.entries][:2] == ["History panel", "Help"]

        await pilot.press("down")
        await pilot.pause()
        assert bar.highlighted_entry is not None
        assert bar.highlighted_entry.name == "Help"

        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, HelpScreen)
        assert app.command_mode is False
        assert app.query_one("#filter", Input).value == ""
        assert bar.display is False


async def test_tab_completes_the_name_of_the_highlighted_command(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        bar = await _open_command_mode(app, pilot)

        await pilot.press("h")
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()

        assert app.query_one("#filter", Input).value == "/Help"
        assert app.query_one("#filter", Input).has_focus
        assert bar.entries[0].name == "Help"
        assert bar.highlighted_entry is not None
        assert bar.highlighted_entry.name == "Help"


async def test_escape_goes_back_to_the_plain_filter(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        bar = await _open_command_mode(app, pilot)
        await pilot.press("f")
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        assert app.command_mode is False
        assert bar.display is False
        box = app.query_one("#filter", Input)
        assert box.value == ""
        assert not box.has_class("command")
        assert box.has_focus


async def test_backspace_over_the_slash_leaves_command_mode(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        bar = await _open_command_mode(app, pilot)

        await pilot.press("backspace")
        await pilot.pause()

        assert app.command_mode is False
        assert bar.display is False
        assert app.query_one("#filter", Input).value == ""
        assert not app.query_one("#filter", Input).has_class("command")


async def test_the_plain_filter_still_filters_rows_after_command_mode(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)
        await _open_command_mode(app, pilot)
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("r", "e", "d", "i", "s")
        await pilot.pause()

        assert app.filter_text == "redis"
        assert app._row_order == ["6379/TCP"]
        assert table.row_count == 1
