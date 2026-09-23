"""Extended, tabbed inspection of one listener."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static, TabbedContent, TabPane

from lirts.config_view import ConfigView
from lirts.models import Listener, Snapshot
from lirts.tui.screens.details_tabs import (
    connections_tab,
    docker_tab,
    history_tab,
    identity_tab,
    overview_tab,
    processes_tab,
)


class DetailsScreen(Screen[None]):
    """Everything known about one listener, in tabs; opened with `i` or Enter on a row."""

    BINDINGS = [
        Binding("escape,q,i", "app.pop_screen", "Close"),
        Binding("k", "app.kill", "Kill"),
        Binding("l", "app.docker_logs", "Logs"),
        Binding("o", "app.open_browser", "Open"),
        Binding("x", "app.docker_stop", "Stop"),
        Binding("t", "app.restart", "Restart"),
    ]

    def __init__(self, listener: Listener, snapshot: Snapshot, cfg: ConfigView) -> None:
        super().__init__()
        self.listener = listener
        self.snapshot = snapshot
        self.cfg = cfg

    def compose(self) -> ComposeResult:
        lst = self.listener
        yield Static(
            f"{lst.identity.service}  ·  {lst.key}  ·  {lst.name}",
            classes="screen-title",
            id="details-title",
        )
        with TabbedContent(id="details-tabs"):
            with TabPane("Overview", id="tab-overview"):
                yield VerticalScroll(Static(id="ov-body", classes="pane-body"))
            with TabPane("Processes", id="tab-processes"):
                yield VerticalScroll(Static(id="pr-body", classes="pane-body"))
            with TabPane("Docker", id="tab-docker"):
                yield VerticalScroll(Static(id="dk-body", classes="pane-body"))
            with TabPane("Connections", id="tab-conns"):
                yield VerticalScroll(Static(id="cn-body", classes="pane-body"))
            with TabPane("History", id="tab-history"):
                yield VerticalScroll(Static(id="hs-body", classes="pane-body"))
            with TabPane("Identity", id="tab-identity"):
                yield VerticalScroll(Static(id="id-body", classes="pane-body"))
        yield Static(
            "Esc close · Tab/←→ switch tabs · k kill · l logs · x stop · t restart · o open",
            classes="screen-footer",
        )

    def on_mount(self) -> None:
        self.render_all()
        self.query_one("#details-tabs", TabbedContent).focus()

    def update_listener(self, listener: Listener | None, snapshot: Snapshot) -> None:
        """Take the row from a newer snapshot; None means the port stopped listening."""
        if listener is None:
            self.query_one("#details-title", Static).update(
                f"{self.listener.key} — no longer listening"
            )
            return
        self.listener = listener
        self.snapshot = snapshot
        self.render_all()

    def render_all(self) -> None:
        """Redraw the title and every tab from the listener held right now."""
        lst = self.listener
        self.query_one("#ov-body", Static).update(overview_tab(lst))
        self.query_one("#pr-body", Static).update(processes_tab(lst))
        self.query_one("#dk-body", Static).update(docker_tab(lst))
        self.query_one("#cn-body", Static).update(connections_tab(lst, self.snapshot))
        self.query_one("#hs-body", Static).update(history_tab(lst, self.snapshot))
        self.query_one("#id-body", Static).update(identity_tab(lst))
        self.query_one("#details-title", Static).update(
            f"{lst.identity.service}  ·  {lst.key}  ·  {lst.name}"
        )
