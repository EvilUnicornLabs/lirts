"""The main Textual application."""

from __future__ import annotations

import logging
from typing import Any

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input

from lirts import __version__
from lirts.config import state_dir
from lirts.config_view import ConfigView
from lirts.constants import CONFIG_WATCH_INTERVAL, TOAST_LONG, TOAST_SHORT
from lirts.engine import Engine
from lirts.fixes import fix_for
from lirts.models import Level, Snapshot
from lirts.tui.app_actions import ActionsMixin
from lirts.tui.app_filter import FilterMixin
from lirts.tui.app_layout import LayoutMixin
from lirts.tui.app_marks import MarksMixin
from lirts.tui.app_palette import LirtsCommands
from lirts.tui.app_panels import PanelsMixin
from lirts.tui.app_screens import ScreensMixin
from lirts.tui.app_settings import SettingsMixin
from lirts.tui.app_table import TableMixin
from lirts.tui.app_ui import UiMixin
from lirts.tui.histpanel import HistoryPanel
from lirts.tui.layout import DEFAULT_LAYOUT
from lirts.tui.netpanel import NetPanel
from lirts.tui.render import Column, resolve_columns
from lirts.tui.screens import DetailsScreen, StarMapScreen
from lirts.tui.widgets import SidePanel, TopBar

log = logging.getLogger(__name__)


class LirtsApp(
    TableMixin,
    MarksMixin,
    FilterMixin,
    PanelsMixin,
    ActionsMixin,
    ScreensMixin,
    SettingsMixin,
    UiMixin,
    LayoutMixin,
    App[None],
):
    """A btop-style dashboard for ports, processes, containers and services."""

    TITLE = f"lirts {__version__}"
    COMMANDS = App.COMMANDS | {LirtsCommands}
    CSS_PATH = ["styles.tcss", "looks.tcss"]

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("slash", "focus_filter", "Filter"),
        Binding("escape", "escape", "Clear / quit", show=False),
        Binding("enter", "show_details", "Details", show=False, priority=False),
        Binding("i", "show_details", "Details"),
        Binding("space", "mark", "Mark", show=False),
        Binding("a", "mark_all", "Mark all", show=False),
        Binding("k", "kill", "Kill"),
        Binding("l", "docker_logs", "Logs"),
        Binding("e", "docker_exec", "Exec"),
        Binding("x", "docker_stop", "Stop"),
        Binding("t", "restart", "Restart"),
        Binding("F", "fix", "Fix"),
        Binding("g", "stacks", "Stacks"),
        Binding("G", "toggle_grouping", "Group"),
        Binding("K", "kubernetes", "Kube"),
        Binding("w", "graph", "Who", show=False),
        Binding("M", "starmap", "Map", show=False),
        Binding("R", "reach", "Reach", show=False),
        Binding("H", "check_health", "Health"),
        Binding("o", "open_browser", "Open"),
        Binding("O", "open_project", "Folder", show=False),
        Binding("c", "copy", "Copy", show=False),
        Binding("s", "sort", "Sort"),
        Binding("S", "sort_reverse", "Reverse", show=False),
        Binding("E", "explain", "Explain"),
        Binding("n", "events", "Events"),
        Binding("exclamation_mark", "toggle_problems", "Problems", show=False),
        Binding("u", "toggle_udp", "UDP", show=False),
        Binding("h", "toggle_system", "System", show=False),
        Binding("p", "toggle_side", "Panel", show=False),
        Binding("N", "net_scope", "Net", show=False),
        Binding("T", "cycle_style", "Style", show=False),
        Binding("U", "cycle_layout", "Layout", show=False),
        Binding("Y", "toggle_history", "History", show=False),
        Binding("ctrl+f", "focus_filter", "Filter", show=False),
        Binding("comma", "settings", "Settings"),
        Binding("W", "setup", "Setup", show=False),
        Binding("question_mark,f1", "help", "Help"),
    ]

    def __init__(self, engine: Engine, config: dict[str, Any], open_setup: bool = False) -> None:
        super().__init__()
        self.engine = engine
        # The dict stays: Settings, the setup wizard and save_config write to it; cfg reads it.
        self.config = config
        self.cfg = ConfigView(config)
        self.open_setup = open_setup
        self._net_scope = "machine"
        self._refreshing = False
        self.snapshot: Snapshot = Snapshot()
        self.columns: list[Column] = resolve_columns(self.cfg.columns)
        sortable = [c.key for c in self.columns]
        wanted = self.cfg.sort_by or "port"
        self.sort_key: str = (
            wanted if wanted in sortable else ("port" if "port" in sortable else sortable[0])
        )
        self.sort_reverse: bool = self.cfg.sort_desc
        self.filter_text: str = ""
        self._row_order: list[str] = []
        self._cell_cache: dict[tuple[str, str], str] = {}
        self._selected_key: str | None = None
        self._last_event_ts: float = 0.0  # newest event already toasted
        self._events_seen_ts: float = 0.0  # newest event seen in the notification centre
        self._first_refresh_done = False
        self._side_wanted: bool = self.cfg.side_panel
        self._net_wanted: bool = self.cfg.net_panel
        self.layout_name: str = DEFAULT_LAYOUT
        self.history_wanted: bool = self.cfg.ui.history_panel
        self._marked: set[str] = set()
        self.problems_only: bool = False
        self.grouped: bool = self.cfg.group_by_stack
        self._collapsed: set[str] = set()
        self._group_of: dict[str, str] = {}

    # ----- layout ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield TopBar(id="top")
        with Horizontal(id="main"):
            # Empty slots: apply_layout moves the side panel into the one its position names.
            yield Container(id="side-left")
            with Vertical(id="left"):
                yield Input(
                    placeholder="Filter: words or key:value, e.g. status:error project:shop src:docker -system  (/ focus, Esc clear)",
                    id="filter",
                )
                yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
                yield NetPanel(id="net")
                yield HistoryPanel(id="history", classes="hidden")
            yield SidePanel(id="side")
        yield Container(id="bottom")
        yield Footer()

    def on_mount(self) -> None:
        theme = self.cfg.theme
        if theme in self.available_themes:
            self.theme = theme
        table = self.query_one("#table", DataTable)
        self._build_columns()
        self.apply_looks()
        table.focus()
        side = self.query_one("#side", SidePanel)
        side.show(None)
        self._apply_side_visibility()
        self.apply_layout_from_config()
        self.query_one("#top", TopBar).update_snapshot(self.snapshot, collecting=True)
        self.sub_title = "starting: collecting ports, containers and probing services…"
        table.loading = True
        self.engine.start()
        self.refresh_data()
        self._timer = self.set_interval(self.cfg.refresh_interval, self.refresh_data)
        self._apply_net_visibility()
        self._first_run_hint()
        if self.open_setup:
            self.action_setup()
        self._config_outdated_hint()
        self._config_mtime = self._config_stat()
        self.set_interval(CONFIG_WATCH_INTERVAL, self._check_config_file)

    async def action_quit(self) -> None:
        """Leave lirts (q)."""
        self.exit()

    # ----- data --------------------------------------------------------------------

    @work(group="refresh")
    async def refresh_data(self) -> None:
        """Worker: collect one snapshot and apply it, unless a collection is already running."""
        # Never cancel a running refresh (an exclusive worker would): when the interval is
        # shorter than one collection cycle, the empty first snapshot would be applied forever.
        if self._refreshing:
            return
        self._refreshing = True
        try:
            snapshot = await self.engine.refresh()
        except Exception as exc:  # keep the UI alive whatever the collectors do
            log.exception("refresh failed")
            self.notify(f"Refresh failed: {exc}", severity="error", timeout=TOAST_LONG)
            return
        finally:
            self._refreshing = False
        self.apply_snapshot(snapshot)

    def apply_snapshot(self, snapshot: Snapshot) -> None:
        """Take a fresh snapshot into the table, the panels, the subtitle and the toasts."""
        self.snapshot = snapshot
        if not self._first_refresh_done:
            # Everything recorded before this session started is history, not news.
            newest = max((e.timestamp for e in self.engine.history.all_events()), default=0.0)
            self._last_event_ts = max(self._last_event_ts, newest)
            self._events_seen_ts = max(self._events_seen_ts, newest)
        if not self._first_refresh_done:
            table = self.query_one("#table", DataTable)
            table.loading = False
            # The loading overlay steals focus; give it back unless the user started typing.
            if not self.query_one("#filter", Input).value:
                table.focus()
        self._first_refresh_done = True
        self.query_one("#top", TopBar).update_snapshot(snapshot, unread=self.unread_events())
        s = snapshot.summary
        docker = f" · {snapshot.container_count} containers" if snapshot.docker_available else ""
        flags = []
        if self.engine.show_udp:
            flags.append("udp")
        if self.engine.hide_system:
            flags.append("no-system")
        if self.problems_only:
            flags.append("problems only")
        if self.grouped:
            flags.append("grouped")
        flag_text = f" · {' '.join(flags)}" if flags else ""
        marked = f" · {len(self._marked)} marked" if self._marked else ""
        mode = ""
        if getattr(self.engine, "demo", False):
            mode = " · DEMO (synthetic machine)"
        elif getattr(self.engine, "replay", False) or getattr(self.engine, "remote", False):
            # Only the replay and remote engines have a position; the protocol does not declare one.
            mode = f" · {getattr(self.engine, 'position', '')}"
        self.sub_title = f"{s['total']} listeners{docker} · sort {self.sort_key}{' ↓' if self.sort_reverse else ''}{flag_text}{marked}{mode}"
        self.update_table()
        self._refresh_net_panel()
        self._notify_events(snapshot)
        screen = self.screen
        if isinstance(screen, DetailsScreen):
            screen.update_listener(snapshot.by_key(screen.listener.key), snapshot)
        elif isinstance(screen, StarMapScreen):
            screen.on_refresh()

    def unread_events(self) -> int:
        """Events recorded since the notification centre was last opened."""
        return sum(
            1 for e in self.engine.history.all_events() if e.timestamp > self._events_seen_ts
        )

    def _notify_events(self, snapshot: Snapshot) -> None:
        """Toast only error-level events that happened during this session; the rest wait in `n`."""
        for ev in snapshot.events:
            if ev.timestamp <= self._last_event_ts:
                continue
            if ev.level == Level.ERROR:
                self.notify(
                    f"{ev.message}  ·  press n for details", severity="error", timeout=TOAST_LONG
                )
        if snapshot.events:
            self._last_event_ts = max(self._last_event_ts, snapshot.events[-1].timestamp)

    # ----- widget events -----------------------------------------------------------

    @on(DataTable.RowHighlighted, "#table")
    def _row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is not None and event.row_key.value is not None:
            self._selected_key = str(event.row_key.value)
            self._show_side_for(self._selected_key)

    @on(DataTable.RowSelected, "#table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_show_details()

    @on(DataTable.HeaderSelected, "#table")
    def _header_selected(self, event: DataTable.HeaderSelected) -> None:
        key = str(event.column_key.value)
        if key == "mark":
            return
        if key == self.sort_key:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_key = key
            self.sort_reverse = False
        self._apply_sort()

    @on(Input.Changed, "#filter")
    def _filter_changed(self, event: Input.Changed) -> None:
        self.filter_text = event.value.strip().lower()
        self.update_table()

    @on(Input.Submitted, "#filter")
    def _filter_submitted(self, event: Input.Submitted) -> None:
        self.query_one("#table", DataTable).focus()

    # ----- toggles -----------------------------------------------------------------

    def action_toggle_udp(self) -> None:
        """Show or hide UDP sockets (u)."""
        self.engine.show_udp = not self.engine.show_udp
        self.notify(
            "UDP sockets " + ("shown" if self.engine.show_udp else "hidden"), timeout=TOAST_SHORT
        )
        self.refresh_data()

    def action_toggle_system(self) -> None:
        """Show or hide OS daemons (h)."""
        self.engine.hide_system = not self.engine.hide_system
        self.notify(
            "System services " + ("hidden" if self.engine.hide_system else "shown"),
            timeout=TOAST_SHORT,
        )
        self.refresh_data()

    def action_refresh(self) -> None:
        """Collect everything again now (r)."""
        self.refresh_data()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """None shows a binding disabled (dimmed) in the footer instead of hiding it."""
        if action == "kubernetes":
            return True if self.engine.kube.kubectl else None
        row = self.selected()
        if row is None:
            # A group header or an empty table: the action itself explains what it needs.
            return True
        if action in ("docker_logs", "docker_exec", "docker_stop"):
            return True if row.container else None
        if action == "kill":
            return True if row.processes else None
        if action == "fix":
            # Hidden, not dimmed: the key only exists when the row has something to fix.
            return fix_for(row) is not None
        if action == "open_browser":
            return True if row.protocol == "TCP" else None
        if action == "reach":
            return True if row.ssh else None
        return True


def run_app(engine: Engine, *, config: dict[str, Any], setup: bool = False) -> None:
    """Run the dashboard until the user quits, then close the engine."""
    # First start on this machine with no config file: walk through the settings once.
    first_start = not ConfigView(config).exists and not (state_dir() / ".welcomed").exists()
    app = LirtsApp(engine, config, open_setup=setup or first_start)
    try:
        app.run()
    finally:
        engine.close()
