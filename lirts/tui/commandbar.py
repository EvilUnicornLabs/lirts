"""The command dropdown that the filter box turns into when the user types a second ``/``.

The commands themselves come from :data:`lirts.tui.app_palette.PALETTE_COMMANDS`; the key that
runs each one is read from the app's ``BINDINGS`` at runtime, so the key table is never copied.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from textual.widgets import OptionList
from textual.widgets.option_list import Option

from lirts.tui.app_palette import PALETTE_COMMANDS, with_key

COMMAND_BAR_ID = "commandbar"
COMMAND_PREFIX = "/"
DISABLED_NOTE = "(not for this row)"
MAX_VISIBLE_COMMANDS = 12


@dataclass(frozen=True, slots=True)
class CommandEntry:
    """One command offered in the dropdown."""

    name: str
    description: str
    key: str
    action: str
    enabled: bool

    @property
    def label(self) -> str:
        """The line shown in the dropdown: ``name  description  (key)``."""
        note = "" if self.enabled else f"  {DISABLED_NOTE}"
        return f"{self.name}  {with_key(self.description, self.key)}{note}"


def matching_commands(
    query: str, *, keys: dict[str, str], allowed: Callable[[str], bool | None]
) -> list[CommandEntry]:
    """The commands ``query`` can mean: names starting with it first, descriptions after.

    ``allowed`` is the app's ``check_action``: ``False`` leaves the command out altogether,
    ``None`` keeps it in the list but disabled, because the current row cannot take it.
    """
    wanted = query.strip().lower()
    by_name: list[CommandEntry] = []
    by_description: list[CommandEntry] = []
    for name, description, action in PALETTE_COMMANDS:
        state = allowed(action)
        if state is False:
            continue
        entry = CommandEntry(name, description, keys.get(action, ""), action, state is True)
        if not wanted or name.lower().startswith(wanted):
            by_name.append(entry)
        elif any(word.lower().startswith(wanted) for word in description.split()):
            by_description.append(entry)
    return by_name + by_description


class CommandBar(OptionList):
    """The dropdown under the filter box, listing the commands that match what is typed."""

    DEFAULT_CSS = f"""
    CommandBar {{
        height: auto;
        max-height: {MAX_VISIBLE_COMMANDS};
    }}
    """

    can_focus = False

    def __init__(self, *, id: str = COMMAND_BAR_ID) -> None:
        super().__init__(id=id)
        self.entries: list[CommandEntry] = []

    def show_matches(self, entries: list[CommandEntry]) -> None:
        """Replace the list with ``entries`` and highlight the first one that can be run."""
        self.entries = entries
        self.clear_options()
        self.add_options(
            [Option(e.label, id=e.action, disabled=not e.enabled) for e in entries],
        )
        self.display = bool(entries)
        self.highlighted = next(
            (index for index, entry in enumerate(entries) if entry.enabled), None
        )

    @property
    def highlighted_entry(self) -> CommandEntry | None:
        """The command Enter would run, or None when nothing runnable is highlighted."""
        index = self.highlighted
        if index is None or not 0 <= index < len(self.entries):
            return None
        entry = self.entries[index]
        return entry if entry.enabled else None
