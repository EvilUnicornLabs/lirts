"""Container and pod logs with reload and follow."""

from __future__ import annotations

from collections.abc import Callable

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import RichLog, Static

from lirts.constants import LOG_FOLLOW_INTERVAL, LOG_TAIL_CONTAINER
from lirts.engine import Engine


class LogScreen(Screen[None]):
    """Container, stack or pod logs with reload and follow; opened with `l`."""

    BINDINGS = [
        Binding("escape,q", "app.pop_screen", "Close"),
        Binding("r", "reload", "Reload"),
        Binding("f", "toggle_follow", "Follow"),
        Binding("end", "scroll_end", "End", show=False),
    ]

    def __init__(
        self,
        engine: Engine,
        container_id: str,
        container_name: str,
        tail: int = LOG_TAIL_CONTAINER,
        fetch: Callable[[int], str] | None = None,
    ) -> None:
        super().__init__()
        self.engine = engine
        self.container_id = container_id
        self.container_name = container_name
        self.tail = tail
        self.follow = False
        self._timer: Timer | None = None
        self._fetch = fetch

    def compose(self) -> ComposeResult:
        yield Static(
            f"Logs — {self.container_name}  (last {self.tail} lines)",
            classes="screen-title",
            id="log-title",
        )
        yield RichLog(highlight=True, markup=False, wrap=True, id="log-content")
        yield Static("Esc close · r reload · f follow · End jump to end", classes="screen-footer")

    def on_mount(self) -> None:
        self.query_one("#log-content", RichLog).focus()
        self.action_reload()

    def action_reload(self) -> None:
        """Fetch the last `tail` lines again (r)."""
        self.load_logs()

    @work(thread=True, exclusive=True, group="logs")
    def load_logs(self) -> None:
        """Worker: fetch the log text off the UI thread and hand it to the widget."""
        if self._fetch is not None:
            text = self._fetch(self.tail)
        else:
            text = self.engine.docker.logs(self.container_id, tail=self.tail)
        self.app.call_from_thread(self._show, text)

    def _show(self, text: str) -> None:
        log = self.query_one("#log-content", RichLog)
        log.clear()
        for line in text.splitlines() or ["(no output)"]:
            log.write(line)
        log.scroll_end(animate=False)

    def action_toggle_follow(self) -> None:
        """Reload every few seconds, or stop doing so (f)."""
        self.follow = not self.follow
        title = self.query_one("#log-title", Static)
        if self.follow:
            self._timer = self.set_interval(LOG_FOLLOW_INTERVAL, self.action_reload)
            title.update(f"Logs — {self.container_name}  (following)")
        else:
            if self._timer:
                self._timer.stop()
                self._timer = None
            title.update(f"Logs — {self.container_name}  (last {self.tail} lines)")

    def action_scroll_end(self) -> None:
        """Jump to the newest line (End)."""
        self.query_one("#log-content", RichLog).scroll_end(animate=False)
