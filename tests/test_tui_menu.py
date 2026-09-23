"""The Esc menu: settings, help, star map or quit when there is nothing left to clear."""

from __future__ import annotations

from textual.widgets import OptionList

from lirts.tui.app import LirtsApp
from lirts.tui.screens import MENU_ITEMS, HelpScreen, MenuScreen, SettingsScreen, StarMapScreen
from tests.conftest import wait_rows


def test_menu_items_are_the_five_documented_entries() -> None:
    assert MENU_ITEMS == [
        ("s", "Settings", "settings"),
        ("h", "Help", "help"),
        ("m", "Star map", "starmap"),
        ("q", "Quit", "quit"),
        ("c", "Cancel", "cancel"),
    ]


async def test_escape_clears_first_and_only_then_opens_the_menu(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        await pilot.press("slash", *"x", "escape")  # the filter goes first
        await pilot.pause()
        assert app.filter_text == "" and not isinstance(app.screen, MenuScreen)
        await pilot.press("space", "escape")  # then the marks
        await pilot.pause()
        assert app._marked == set() and not isinstance(app.screen, MenuScreen)
        await pilot.press("escape")  # nothing left: the menu
        await pilot.pause()
        assert isinstance(app.screen, MenuScreen)
        options = app.screen.query_one("#menu-options", OptionList)
        ids = [str(options.get_option_at_index(i).id) for i in range(options.option_count)]
        assert ids == [
            "settings",
            "help",
            "starmap",
            "quit",
            "cancel",
        ]
        await pilot.press("escape")  # Esc cancels the menu itself
        await pilot.pause()
        assert not isinstance(app.screen, MenuScreen) and app.is_running


async def test_menu_enter_on_settings_opens_the_settings_screen(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, MenuScreen)
        await pilot.press("enter")  # Settings is highlighted first
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)


async def test_menu_arrows_and_letters_choose_help_and_the_star_map(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("j", "enter")  # down to Help
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, MenuScreen)
        await pilot.press("m")  # the letter picks the star map directly
        await pilot.pause()
        assert isinstance(app.screen, StarMapScreen)


async def test_menu_quit_exits(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, MenuScreen)
        await pilot.press("q")
        await pilot.pause()
        assert not app.is_running or app._exit


async def test_menu_cancel_entry_leaves_everything_alone(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert not isinstance(app.screen, MenuScreen)
        assert app.is_running and table.row_count >= 3
