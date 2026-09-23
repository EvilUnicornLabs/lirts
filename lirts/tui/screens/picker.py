"""One modal for choosing the value of a choice setting, with an optional live preview.

The Settings screen never edits a choice in place: Enter on a choice row opens
:class:`PickerScreen`, which looks and behaves like the column checklist next to it.  A caller
that can show the effect of a value while the cursor moves passes ``preview``; one that can draw
what a value looks like passes ``describe`` and gets a preview line beside every option.
"""

from __future__ import annotations

from collections.abc import Callable

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from lirts.identity_rules import Role
from lirts.settings import Setting
from lirts.tui.glyphs import GLYPH_SETS

# Width the option name is padded to when a describe() preview follows it on the same line.
CHOICE_LABEL_WIDTH = 14

# One fixed curve and one fixed bar, drawn with each set's own ramp, so that the glyph sets can
# be compared against each other rather than against whatever the machine happens to be doing.
GLYPH_PREVIEW_SAMPLE: tuple[float, ...] = (0.0, 0.2, 0.45, 0.7, 1.0, 0.8, 0.5, 0.25)
GLYPH_PREVIEW_ROLES: tuple[str, ...] = (Role.FRONTEND, Role.BACKEND, Role.DB)
GLYPH_PREVIEW_BAR_FULL = 6
GLYPH_PREVIEW_BAR_EMPTY = 3


def glyph_preview(name: str) -> Text:
    """A sparkline, a bar and three role icons drawn with one glyph set (``ui.glyphs``)."""
    glyphs = GLYPH_SETS.get(name)
    if glyphs is None:
        return Text("")
    top = len(glyphs.spark) - 1
    spark = "".join(glyphs.spark[round(level * top)] for level in GLYPH_PREVIEW_SAMPLE)
    bar = glyphs.bar_full * GLYPH_PREVIEW_BAR_FULL + glyphs.bar_empty * GLYPH_PREVIEW_BAR_EMPTY
    icons = " ".join(glyphs.role_icon(role) for role in GLYPH_PREVIEW_ROLES)
    return Text(f"{spark}  {bar}  {icons}", style="cyan")


class PickerScreen(ModalScreen[str | None]):
    """The value list of one choice setting; dismisses with the chosen value or None.

    ↑ ↓ (or j / k) move, Enter picks, Esc leaves the setting untouched.  The value the setting
    holds right now is highlighted when the modal opens.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("j", "move(1)", "Down", show=False),
        Binding("k", "move(-1)", "Up", show=False),
    ]

    def __init__(
        self,
        setting: Setting,
        *,
        choices: list[str],
        current: str,
        preview: Callable[[str], None] | None = None,
        describe: Callable[[str], Text] | None = None,
    ) -> None:
        super().__init__()
        self.setting = setting
        self.choices = list(choices)
        self.current = current
        self.preview = preview
        self.describe = describe
        # Nothing is previewed for the value that is already live when the modal opens.
        self._previewed: str | None = current if current in self.choices else None

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.setting.label, classes="dialog-title")
            yield Static(Text(self.setting.description, style="dim"), id="picker-description")
            yield OptionList(*self._options(), id="picker-options")
            yield Static(
                "↑ ↓ / j k move · Enter picks · Esc keeps the current value",
                classes="dialog-footer",
            )

    def _options(self) -> list[Option]:
        options: list[Option] = []
        for choice in self.choices:
            if self.describe is None:
                options.append(Option(Text(choice, style="bold")))
                continue
            prompt = Text(choice.ljust(CHOICE_LABEL_WIDTH), style="bold")
            prompt.append_text(self.describe(choice))
            options.append(Option(prompt))
        return options

    def on_mount(self) -> None:
        options = self.query_one("#picker-options", OptionList)
        if self.choices:
            known = self._previewed is not None
            options.highlighted = self.choices.index(self.current) if known else 0
            self._previewed = self.current if known else self.choices[0]
        options.focus()

    def action_move(self, step: int) -> None:
        """j and k move the cursor the way ↓ and ↑ do."""
        options = self.query_one("#picker-options", OptionList)
        if step > 0:
            options.action_cursor_down()
        else:
            options.action_cursor_up()

    def action_cancel(self) -> None:
        """Leave the setting unchanged (Esc)."""
        self.dismiss(None)

    @on(OptionList.OptionHighlighted, "#picker-options")
    def _highlighted(self, event: OptionList.OptionHighlighted) -> None:
        choice = self.choices[event.option_index]
        if self.preview is None or choice == self._previewed:
            return
        self._previewed = choice
        self.preview(choice)

    @on(OptionList.OptionSelected, "#picker-options")
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.choices[event.option_index])
