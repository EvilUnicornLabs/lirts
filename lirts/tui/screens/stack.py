"""Compose projects with whole-stack actions."""

from __future__ import annotations

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Static

from lirts.constants import GLYPH_STATUS, LOG_TAIL_STACK, TOAST_LONG, TOAST_NORMAL
from lirts.engine import Engine
from lirts.insights import format_rate
from lirts.models import ContainerInfo
from lirts.tui.screens.help_confirm import ConfirmScreen
from lirts.tui.screens.log import LogScreen

# Service names listed in the SERVICES cell before the rest become an ellipsis.
MAX_SERVICES_LISTED = 6


class StackScreen(Screen[str | None]):
    """Compose projects with whole-stack actions; opened with `g`.

    Dismisses with the project name the dashboard should filter by.
    """

    BINDINGS = [
        Binding("escape,q,g", "close", "Close"),
        Binding("enter", "filter_by", "Filter table", show=False),
        Binding("t", "restart_stack", "Restart"),
        Binding("x", "stop_stack", "Stop"),
        Binding("l", "stack_logs", "Logs"),
        Binding("o", "open_dir", "Open folder"),
        Binding("r", "reload", "Reload"),
    ]

    def __init__(self, engine: Engine, preselect: str | None = None) -> None:
        super().__init__()
        self.engine = engine
        self.preselect = preselect
        self.stacks: dict[str, list[ContainerInfo]] = {}

    def compose(self) -> ComposeResult:
        yield Static("Compose stacks", classes="screen-title")
        yield DataTable(id="stack-table", cursor_type="row", zebra_stripes=True)
        yield Static(
            "Esc close · Enter filter table by stack · t restart · x stop · l logs · o open folder",
            classes="screen-footer",
        )

    def on_mount(self) -> None:
        table = self.query_one("#stack-table", DataTable)
        for label, key in (
            ("PROJECT", "project"),
            ("CONTAINERS", "n"),
            ("SERVICES", "services"),
            ("PORTS", "ports"),
            ("TRAFFIC", "net"),
            ("STATUS", "status"),
            ("FOLDER", "dir"),
        ):
            table.add_column(label, key=key)
        table.focus()
        self.action_reload()

    def action_reload(self) -> None:
        """Ask Docker for the running compose projects again and redraw (r)."""
        table = self.query_one("#stack-table", DataTable)
        self.stacks = self.engine.stacks()
        table.clear()
        for project, containers in self.stacks.items():
            services = ", ".join(
                (c.service or c.name) for c in containers[:MAX_SERVICES_LISTED]
            ) + (" …" if len(containers) > MAX_SERVICES_LISTED else "")
            ports = ", ".join(str(p) for c in containers for p in c.host_ports) or "-"
            unhealthy = [
                c
                for c in containers
                if c.health == "unhealthy" or c.status not in ("running", "healthy")
            ]
            status = (
                Text(f"{GLYPH_STATUS} ok", style="green")
                if not unhealthy
                else Text(f"{GLYPH_STATUS} {len(unhealthy)} unhealthy", style="bold red")
            )
            folder = containers[0].working_dir or "-"
            sampled = [c for c in containers if c.net_rx_rate is not None]
            if sampled:
                rx = sum(c.net_rx_rate or 0.0 for c in sampled)
                tx = sum(c.net_tx_rate or 0.0 for c in sampled)
                traffic = Text(f"↓{format_rate(rx).replace(' ', '')}", style="cyan")
                traffic.append(f" ↑{format_rate(tx).replace(' ', '')}", style="magenta")
                chatty = max(sampled, key=lambda c: (c.net_rx_rate or 0) + (c.net_tx_rate or 0))
                if len(sampled) > 1 and (chatty.net_rx_rate or 0) + (chatty.net_tx_rate or 0) > 0:
                    traffic.append(f"  most: {chatty.service or chatty.name}", style="dim")
            else:
                traffic = Text("-", style="dim")
            table.add_row(
                Text(project, style="bold"),
                str(len(containers)),
                services,
                ports,
                traffic,
                status,
                Text(folder, style="dim"),
                key=project,
            )
        if self.preselect in self.stacks:
            table.move_cursor(row=list(self.stacks).index(self.preselect), animate=False)
        if not self.stacks:
            self.notify("No running compose stacks", severity="warning", timeout=TOAST_NORMAL)

    def current(self) -> str | None:
        """The compose project under the cursor, if the table has any rows."""
        table = self.query_one("#stack-table", DataTable)
        if table.row_count == 0:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        return str(row_key.value)

    def action_close(self) -> None:
        """Leave the screen without filtering the dashboard (Esc / q / g)."""
        self.dismiss(None)

    def action_filter_by(self) -> None:
        """Close the screen and filter the dashboard by this project (Enter)."""
        self.dismiss(self.current())

    @on(DataTable.RowSelected, "#stack-table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_filter_by()

    def _confirm_and_run(self, action: str) -> None:
        project = self.current()
        if not project:
            return
        names = ", ".join(c.name for c in self.stacks[project])

        def done(ok: bool | None) -> None:
            if ok:
                self.run_stack_action(project, action)

        self.app.push_screen(
            ConfirmScreen(
                f"{action.capitalize()} stack {project}?",
                Text(f"{action.capitalize()} {len(self.stacks[project])} container(s): {names}"),
            ),
            done,
        )

    def action_restart_stack(self) -> None:
        """Restart every container of the selected project, after a confirmation (t)."""
        self._confirm_and_run("restart")

    def action_stop_stack(self) -> None:
        """Stop every container of the selected project, after a confirmation (x)."""
        self._confirm_and_run("stop")

    @work(thread=True, group="stack-action")
    def run_stack_action(self, project: str, action: str) -> None:
        """Worker: run the Docker action on the whole project, then report and redraw."""
        results = self.engine.stack_action(project, action)
        failed = [f"{name}: {msg}" for name, ok, msg in results if not ok]
        if failed:
            self.app.call_from_thread(
                self.notify,
                f"{action} failed: " + "; ".join(failed),
                severity="error",
                timeout=TOAST_LONG,
            )
        else:
            self.app.call_from_thread(
                self.notify, f"{action.capitalize()}ed {len(results)} container(s) of {project}"
            )
        self.app.call_from_thread(self.action_reload)

    def action_stack_logs(self) -> None:
        """Show the interleaved logs of the whole project (l)."""
        project = self.current()
        if not project:
            return
        engine = self.engine
        self.app.push_screen(
            LogScreen(
                engine,
                project,
                f"stack {project}",
                tail=LOG_TAIL_STACK,
                fetch=lambda tail: engine.stack_logs(project, tail),
            )
        )

    def action_open_dir(self) -> None:
        """Open the project's folder in the configured editor (o)."""
        project = self.current()
        if not project:
            return
        folder = self.stacks[project][0].working_dir
        if not folder:
            self.notify(
                "No project folder recorded for this stack",
                severity="warning",
                timeout=TOAST_NORMAL,
            )
            return
        ok, msg = self.engine.open_path(folder)
        self.notify(
            f"Opening {folder}" if ok else f"Could not open folder: {msg}",
            severity="information" if ok else "error",
        )
