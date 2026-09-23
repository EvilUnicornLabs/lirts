"""Who talks to whom: local clients, proxy chains, tunnels, ssh sessions."""

from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from lirts.constants import TOAST_SHORT
from lirts.engine import Engine
from lirts.insights import render_graph_text


class GraphScreen(Screen[None]):
    """Who talks to whom: local clients, proxy chains, tunnels, ssh sessions; opened with `w`."""

    BINDINGS = [Binding("escape,q,w", "app.pop_screen", "Close"), Binding("r", "reload", "Reload")]

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine

    def compose(self) -> ComposeResult:
        yield Static("Who talks to whom", classes="screen-title")
        yield VerticalScroll(Static(id="graph-content"))
        yield Static(
            "Esc close · r re-ask nginx / httpd for routes and redraw   (host view: traffic inside containers or the cluster is not visible)",
            classes="screen-footer",
        )

    def on_mount(self) -> None:
        self.render_graph()

    def render_graph(self) -> None:
        """Draw the graph from the engine's latest snapshot, with the route notes underneath."""
        text = render_graph_text(self.engine.snapshot)
        styled = Text()
        for line in text.splitlines():
            if line and not line.startswith(" "):
                styled.append(line + "\n", style="bold underline")
            elif "◀──" in line:
                styled.append(line + "\n", style="cyan")
            elif line.strip().startswith(":"):
                styled.append(line + "\n", style="bold")
            elif "→" in line and ("nginx" in line or "httpd" in line):
                styled.append(
                    line + "\n", style="magenta" if "nothing listening" not in line else "dim"
                )
            else:
                styled.append(line + "\n", style="dim" if "none right now" in line else "")
        notes = getattr(self.engine, "route_notes", None)
        if notes:
            styled.append("\n" + "; ".join(notes) + "\n", style="dim")
        self.query_one("#graph-content", Static).update(styled)

    def action_reload(self) -> None:
        """Re-ask nginx / httpd for their routes, then redraw."""
        if hasattr(self.engine, "refresh_routes"):
            self.notify("Asking nginx / httpd for their routes…", timeout=TOAST_SHORT)
            self.reload_routes()
        else:
            self.render_graph()

    @work(thread=True, exclusive=True, group="routes")
    def reload_routes(self) -> None:
        """Worker: ask the proxies again off the UI thread, then redraw."""
        self.engine.refresh_routes()
        self.app.call_from_thread(self.render_graph)
