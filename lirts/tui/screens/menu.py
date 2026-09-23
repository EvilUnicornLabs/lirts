"""The Esc menu: what to do when there is no filter and no mark left to clear."""

from __future__ import annotations

from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

MENU_ITEMS: list[tuple[str, str, str]] = [
    # (letter, label, app action; "cancel" closes the menu)
    ("s", "Settings", "settings"),
    ("h", "Help", "help"),
    ("m", "Star map", "starmap"),
    ("q", "Quit", "quit"),
    ("c", "Cancel", "cancel"),
]


class MenuScreen(ModalScreen[str | None]):
    """Settings, help, the star map or quit; opened with Esc when nothing is left to clear."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("j", "down", "Down", show=False),
        Binding("k", "up", "Up", show=False),
        *[Binding(letter, f"pick('{action}')", label) for letter, label, action in MENU_ITEMS],
    ]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog", id="menu-dialog"):
            yield Static("lirts", classes="dialog-title")
            yield OptionList(
                *[Option(f"{letter}  {label}", id=action) for letter, label, action in MENU_ITEMS],
                id="menu-options",
            )
            yield Static(
                "↑ ↓ / j k move · Enter choose · the letter picks directly · Esc cancel",
                classes="dialog-footer",
            )

    def on_mount(self) -> None:
        self.query_one("#menu-options", OptionList).focus()

    def action_down(self) -> None:
        """Move the highlight one option down (j)."""
        self.query_one("#menu-options", OptionList).action_cursor_down()

    def action_up(self) -> None:
        """Move the highlight one option up (k)."""
        self.query_one("#menu-options", OptionList).action_cursor_up()

    def action_pick(self, action: str) -> None:
        """Choose an entry by its letter."""
        self.dismiss(None if action == "cancel" else action)

    def action_cancel(self) -> None:
        """Close the menu and change nothing (Esc)."""
        self.dismiss(None)

    @on(OptionList.OptionSelected, "#menu-options")
    def _option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_pick(str(event.option.id))


def open_menu(lirts_app: Any) -> None:
    """Push the Esc menu and run the action the user picks (Esc and the palette share this)."""
    if isinstance(lirts_app.screen, MenuScreen):
        return

    def chosen(action: str | None) -> None:
        if action is None:
            return
        runner = {
            "settings": lirts_app.action_settings,
            "help": lirts_app.action_help,
            "starmap": lirts_app.action_starmap,
            "quit": lirts_app.exit,
        }.get(action)
        if runner is not None:
            runner()

    lirts_app.push_screen(MenuScreen(), chosen)
