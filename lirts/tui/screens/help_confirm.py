"""The keyboard help screen and the generic yes / no dialog."""

from __future__ import annotations

from typing import Any

from rich.table import Table
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

KEY_HELP: list[tuple[str, str]] = [
    ("↑ ↓ / PgUp PgDn", "navigate the table"),
    ("Enter / i", "open the extended details view"),
    (
        "/ / Ctrl+F",
        "filter: words or key:value (status:error project:x src:docker -system); Esc clears",
    ),
    ("Ctrl+P", "command palette: every action and filter field, searchable"),
    ("s / S", "cycle sort column / reverse sort (or click a header)"),
    ("r", "refresh now"),
    ("Space / a", "mark the row / mark all visible rows (Esc clears marks)"),
    ("k", "kill the marked or current process(es), with blast-radius preview"),
    ("l", "show container logs"),
    ("e", "open an interactive shell in the container"),
    ("x", "stop the marked or current container(s)"),
    ("t", "restart: containers via Docker, local processes by re-running their command"),
    ("F", "fix: run what the row's worst insight proposes, after seeing exactly what will run"),
    ("g", "compose stacks: restart / stop / logs / open folder for a whole project"),
    ("K", "Kubernetes: pods, services, port-forwards (← → tabs, N namespace list, l e t f)"),
    ("w", "who talks to whom: local clients per port, proxy chains, tunnels, ssh sessions"),
    ("R", "reachability of the ssh host under the cursor (DNS, ping, TCP); in K: the API server"),
    ("H", "check health now: all visible rows, or only the marked ones (space); results screen"),
    ("G", "group rows under stack / project headers (Space or Enter on a header collapses it)"),
    ("o / O", "open the service in the browser / open the project folder"),
    ("c", "copy the URL or the docker exec command to the clipboard"),
    ("E", "explain the whole machine in tabs: summary, stack, network, warnings, patterns, events"),
    ("n", "notification centre: every event with time, level and meaning"),
    ("!", "toggle problems only (rows with warnings or errors)"),
    ("u", "toggle UDP sockets"),
    ("h", "toggle hiding of system services"),
    ("p", "toggle the side panel"),
    (
        "N",
        "traffic panel: whole machine ↔ selected row (download up, upload down; Settings turns it off)",
    ),
    ("T", "cycle the style: borders, density, glyph set, scroller and panels (never colours)"),
    ("U", "cycle the layout: columns, side panel sections, panels and where they sit"),
    ("Y", "toggle the history panel with the selected row's sparklines"),
    (",", "settings: version, config file, and every option editable in place"),
    ("W", "setup wizard: every setting step by step (runs by itself on the first start)"),
    ("? / F1", "this help"),
    ("q", "quit"),
    ("Esc", "clear the filter, then the marks; with nothing to clear, open the menu"),
]


class HelpScreen(ModalScreen[None]):
    """Every key lirts listens for, with what it does; opened with `?` or F1."""

    BINDINGS = [Binding("escape,q,question_mark,f1", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold cyan", width=18)
        table.add_column()
        for key, desc in KEY_HELP:
            table.add_row(key, desc)
        with Vertical(classes="dialog"):
            yield Static("lirts — keyboard shortcuts", classes="dialog-title")
            yield Static(table, classes="dialog-body")
            yield Static("Esc to close", classes="dialog-footer")


class ConfirmScreen(ModalScreen[bool]):
    """Generic yes / no dialog, pushed by the action that needs the confirmation."""

    BINDINGS = [
        Binding("escape,n", "cancel", "No"),
        Binding("y,enter", "confirm", "Yes"),
    ]

    def __init__(self, title: str, body: Any) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self._title, classes="dialog-title")
            yield Static(self._body, classes="dialog-body")
            yield Static("y / Enter = yes    n / Esc = no", classes="dialog-footer")

    def action_cancel(self) -> None:
        """Answer no (n / Esc)."""
        self.dismiss(False)

    def action_confirm(self) -> None:
        """Answer yes (y / Enter)."""
        self.dismiss(True)
