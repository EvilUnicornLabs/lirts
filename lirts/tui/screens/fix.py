"""The smart-fix confirmation: what the insight says and exactly what will run; opened with `F`."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from lirts.fixes import Fix
from lirts.tui.render import level_marker


class FixScreen(ModalScreen[bool]):
    """Shows the proposed fix and asks for a yes; dismisses with True to run it."""

    BINDINGS = [
        Binding("escape,n", "cancel", "No"),
        Binding("y,enter,F", "confirm", "Yes"),
    ]

    def __init__(self, fix: Fix) -> None:
        super().__init__()
        self.fix = fix

    def compose(self) -> ComposeResult:
        fix = self.fix
        body = Text()
        glyph, style = level_marker(fix.insight.level)
        body.append(f"{glyph} ", style=style)
        body.append(f"{fix.insight.message}\n")
        if fix.insight.suggestion:
            body.append(f"{fix.insight.suggestion}\n", style="dim")
        body.append("\nWill run: ", style="bold")
        body.append(fix.command, style="bold yellow")
        body.append("\n")
        with Vertical(classes="dialog"):
            yield Static(fix.title, classes="dialog-title")
            yield Static(body, classes="dialog-body")
            yield Static("y / Enter / F = run it    n / Esc = no", classes="dialog-footer")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)
