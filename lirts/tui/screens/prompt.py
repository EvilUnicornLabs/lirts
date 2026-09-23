"""One-line text prompt dialog."""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class PromptScreen(ModalScreen[str | None]):
    """Ask for one line of text (a port-forward, a column set); pushed by the action."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self, title: str, placeholder: str = "", default: str = "", hint: str = ""
    ) -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder
        self._default = default
        self._hint = hint

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self._title, classes="dialog-title")
            if self._hint:
                yield Static(Text(self._hint, style="dim"), classes="dialog-body")
            yield Input(value=self._default, placeholder=self._placeholder, id="prompt-input")
            yield Static("Enter = ok    Esc = cancel", classes="dialog-footer")

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    @on(Input.Submitted, "#prompt-input")
    def _submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value or None)

    def action_cancel(self) -> None:
        """Leave without a value (Esc)."""
        self.dismiss(None)
