"""Results of an on-demand health check."""

from __future__ import annotations

import time

from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Static

from lirts.constants import GLYPH_ERROR, GLYPH_OK
from lirts.engine import Engine
from lirts.models import Listener

# Failing rows named in the summary block before it is cut with a "… and N more" line.
MAX_FAILING_LISTED = 6


class HealthScreen(Screen[int | None]):
    """Results of an on-demand health check, failing rows first; opened with `H`."""

    BINDINGS = [
        Binding("escape,q", "close", "Close"),
        Binding("enter", "jump", "Jump to port", show=False),
        Binding("r,H", "rerun", "Check again"),
        Binding("f", "toggle_failing", "Failing only"),
    ]

    def __init__(self, engine: Engine, targets: list[Listener], scope: str = "") -> None:
        super().__init__()
        self.engine = engine
        self.targets = targets
        self.scope = scope or f"{len(targets)} row(s)"
        self.results: list[Listener] = []
        self.failing_only = False
        self.checking = False
        self._rows: list[Listener] = []

    def compose(self) -> ComposeResult:
        yield Static(
            f"Health check — {self.scope} (space marks rows to limit the next check)",
            classes="screen-title",
        )
        yield Static(id="health-summary")
        yield DataTable(id="health-table", cursor_type="row", zebra_stripes=True)
        yield Static(
            "Esc close · Enter filter the dashboard by this port · r check again · f failing only",
            classes="screen-footer",
        )

    def on_mount(self) -> None:
        table = self.query_one("#health-table", DataTable)
        for label, key in (
            ("PORT", "port"),
            ("SERVICE", "service"),
            ("PROJECT", "project"),
            ("SRC", "src"),
            ("TCP", "tcp"),
            ("HEALTH ENDPOINT", "endpoint"),
            ("DOCKER", "docker"),
            ("RESULT", "result"),
            ("CHECKED", "checked"),
        ):
            table.add_column(label, key=key)
        table.focus()
        self.action_rerun()

    def action_rerun(self) -> None:
        """Check every target again, unless a check is already running (r / H)."""
        if self.checking:
            return
        self.checking = True
        self.query_one("#health-summary", Static).update(
            Text(f"Checking {len(self.targets)} listener(s)…", style="bold yellow")
        )
        self.run_check()

    @work(exclusive=True, group="health-screen")
    async def run_check(self) -> None:
        """Worker: run the TCP and endpoint checks, then redraw the table."""
        try:
            self.results = list(await self.engine.check_health_now(self.targets))
        finally:
            self.checking = False
        self.render_results()

    def action_toggle_failing(self) -> None:
        """Show only the rows that failed, or all of them again (f)."""
        self.failing_only = not self.failing_only
        self.render_results()

    @staticmethod
    def verdict(lst: Listener) -> tuple[bool | None, str]:
        """(ok, label): None = not checked."""
        h = lst.health
        if not h.checked:
            return None, "skipped (UDP)" if lst.protocol == "UDP" else "skipped (not listening)"
        if not h.tcp_ok:
            return False, f"unreachable: {h.tcp_error or 'no connection'}"
        if h.http_status is not None and h.http_status >= 400:
            return False, f"endpoint {h.http_status}"
        if lst.container and lst.container.health == "unhealthy":
            return False, "docker unhealthy"
        return True, "ok" if h.http_path else "ok (TCP only)"

    def render_results(self) -> None:
        """Redraw the result table from the last check, worst rows first."""
        table = self.query_one("#health-table", DataTable)
        table.clear()
        rows = list(self.results)
        rows.sort(key=lambda x: ({False: 0, None: 2, True: 1}[self.verdict(x)[0]], x.port))
        if self.failing_only:
            rows = [x for x in rows if self.verdict(x)[0] is False]
        self._rows = rows
        for idx, lst in enumerate(rows):
            h = lst.health
            ok, label = self.verdict(lst)
            if ok is None:
                tcp: Text = Text("-", style="dim")
                endpoint: Text = Text("-", style="dim")
            else:
                tcp = (
                    Text(f"{h.tcp_latency_ms:.0f} ms", style="green")
                    if h.tcp_ok and h.tcp_latency_ms is not None
                    else Text(h.tcp_error or "unreachable", style="bold red")
                )
                if h.http_path:
                    lat = f" {h.http_latency_ms:.0f} ms" if h.http_latency_ms is not None else ""
                    endpoint = Text(
                        f"{h.http_status} {h.http_path}{lat}",
                        style="green" if (h.http_status or 0) < 400 else "bold red",
                    )
                elif h.http_note:
                    endpoint = Text(h.http_note, style="dim")
                else:
                    endpoint = Text("-", style="dim")
            c = lst.container
            docker = Text(
                (c.health or c.status or "running") if c else "-",
                style="bold red" if c and c.health == "unhealthy" else ("" if c else "dim"),
            )
            result = {
                True: Text(f"{GLYPH_OK} {label}", style="green"),
                False: Text(f"{GLYPH_ERROR} {label}", style="bold red"),
                None: Text(label, style="dim"),
            }[ok]
            table.add_row(
                Text(str(lst.port)),
                Text(lst.identity.service),
                Text(lst.identity.project or "-", style="" if lst.identity.project else "dim"),
                Text(lst.source),
                tcp,
                endpoint,
                docker,
                result,
                Text(
                    time.strftime("%H:%M:%S", time.localtime(h.checked_at)) if h.checked else "-",
                    style="dim",
                ),
                key=str(idx),
            )
        self._render_summary()

    def _render_summary(self) -> None:
        verdicts = [self.verdict(x) for x in self.results]
        ok = sum(1 for v, _ in verdicts if v is True)
        failing = [x for x in self.results if self.verdict(x)[0] is False]
        skipped = sum(1 for v, _ in verdicts if v is None)
        with_endpoint = sum(1 for x in self.results if x.health.checked and x.health.http_path)
        checked = len(self.results) - skipped
        when = time.strftime("%H:%M:%S")
        line = Text()
        line.append(f"{checked} checked at {when}: ", style="bold")
        line.append(f"{ok} ok", style="green")
        line.append(" · ")
        line.append(f"{len(failing)} failing", style="bold red" if failing else "dim")
        line.append(
            f" · health endpoint found on {with_endpoint}, TCP only on {checked - with_endpoint}"
            + (f" · {skipped} skipped" if skipped else ""),
            style="dim",
        )
        block = Table.grid(padding=(0, 1))
        block.add_column(width=2)
        block.add_column(overflow="fold")
        block.add_row("", line)
        for lst in failing[:MAX_FAILING_LISTED]:
            _, label = self.verdict(lst)
            block.add_row(
                Text(GLYPH_ERROR, style="bold red"),
                Text(f"{lst.port} {lst.identity.service}: {label}", style="bold red"),
            )
        if len(failing) > MAX_FAILING_LISTED:
            extra = len(failing) - MAX_FAILING_LISTED
            block.add_row("", Text(f"… and {extra} more (f shows failing only)", style="dim"))
        if self.failing_only:
            block.add_row("", Text("Showing failing rows only (f toggles).", style="italic"))
        self.query_one("#health-summary", Static).update(block)

    def action_close(self) -> None:
        """Leave the screen without filtering the dashboard (Esc / q)."""
        self.dismiss(None)

    def action_jump(self) -> None:
        """Close the screen and filter the dashboard by the selected port (Enter)."""
        table = self.query_one("#health-table", DataTable)
        if table.row_count == 0:
            return
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        self.dismiss(self._rows[int(str(row_key.value))].port)

    @on(DataTable.RowSelected, "#health-table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_jump()
