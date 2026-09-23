"""Reachability result for one host and the namespace picker."""

from __future__ import annotations

import time

from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from lirts.collectors.kube import KubeProvider
from lirts.collectors.reach import ReachResult, check_host
from lirts.constants import GLYPH_ERROR, GLYPH_OK


class ReachScreen(ModalScreen[None]):
    """DNS, ping and TCP connect for one remote host; opened with `R` (`R` in `K` for the API)."""

    BINDINGS = [Binding("escape,q,R", "close", "Close"), Binding("r", "again", "Check again")]

    def __init__(self, host: str, port: int | None, what: str) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.what = what

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(f"Reachability — {self.what}", classes="dialog-title")
            yield Static(Text(f"Checking {self.host}…", style="bold yellow"), id="reach-body")
            yield Static("Esc close · r check again", classes="dialog-footer")

    def on_mount(self) -> None:
        self.run_check()

    def action_again(self) -> None:
        """Run the checks once more (r)."""
        self.query_one("#reach-body", Static).update(
            Text(f"Checking {self.host}…", style="bold yellow")
        )
        self.run_check()

    @work(thread=True, exclusive=True, group="reach")
    def run_check(self) -> None:
        """Worker: resolve, ping and connect off the UI thread, then show the result."""
        result = check_host(self.host, self.port)
        self.app.call_from_thread(self._show, result)

    def _show(self, result: ReachResult) -> None:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold", width=10)
        grid.add_column()
        target = result.host + (f":{result.port}" if result.port else "")
        grid.add_row("Target", Text(target, style="bold"))
        for label, text, ok in result.lines():
            style = {True: "green", False: "bold red", None: "dim"}[ok]
            marker = {True: f"{GLYPH_OK} ", False: f"{GLYPH_ERROR} ", None: "  "}[ok]
            grid.add_row(label, Text(marker + text, style=style))
        verdict = (
            Text("reachable", style="bold green")
            if result.ok
            else Text("NOT reachable", style="bold red")
        )
        grid.add_row("Result", verdict)
        grid.add_row(
            "Checked",
            Text(time.strftime("%H:%M:%S", time.localtime(result.checked_at)), style="dim"),
        )
        self.query_one("#reach-body", Static).update(grid)

    def action_close(self) -> None:
        """Leave the result behind (Esc / q / R)."""
        self.dismiss(None)


class NamespaceScreen(ModalScreen[str | None]):
    """Pick a namespace of the current context; opened with `N` inside the Kubernetes screen.

    Dismisses with the chosen name, or ``"*"`` for all namespaces.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("enter", "choose", "Choose")]
    ALL = "*"

    def __init__(self, kube: KubeProvider, current: str | None, all_namespaces: bool) -> None:
        super().__init__()
        self.kube = kube
        self.current = current
        self.all_namespaces = all_namespaces

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("Namespace", classes="dialog-title")
            yield Static("Loading namespaces from the cluster…", id="namespace-status")
            yield OptionList(id="namespace-list")
            yield Static(
                "↑ ↓ select · Enter choose · Esc cancel   "
                "(only for this session; the kubeconfig is not changed)",
                classes="dialog-footer",
            )

    def on_mount(self) -> None:
        self.query_one("#namespace-list", OptionList).focus()
        self.load_namespaces()

    @work(thread=True, exclusive=True, group="namespaces")
    def load_namespaces(self) -> None:
        """Worker: ask the cluster for its namespaces off the UI thread."""
        names = list(self.kube.namespaces())
        self.app.call_from_thread(self._populate, names)

    def _populate(self, names: list[str]) -> None:
        options = self.query_one("#namespace-list", OptionList)
        status = self.query_one("#namespace-status", Static)
        options.clear_options()
        options.add_option(Option("all namespaces", id=self.ALL))
        for name in names:
            options.add_option(Option(name, id=name))
        if names:
            status.update(f"{len(names)} namespace(s) in the current context")
        else:
            status.update(
                Text(
                    "Could not list namespaces (no permission or cluster unreachable)",
                    style="yellow",
                )
            )
        wanted = self.ALL if self.all_namespaces else (self.current or "")
        for idx, name in enumerate([self.ALL, *names]):
            if name == wanted:
                options.highlighted = idx
                break
        else:
            options.highlighted = 0

    def action_cancel(self) -> None:
        """Keep the namespace that is already in use (Esc)."""
        self.dismiss(None)

    def action_choose(self) -> None:
        """Dismiss with the highlighted namespace (Enter)."""
        options = self.query_one("#namespace-list", OptionList)
        if options.highlighted is None:
            return
        option = options.get_option_at_index(options.highlighted)
        self.dismiss(str(option.id))

    @on(OptionList.OptionSelected, "#namespace-list")
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id))
