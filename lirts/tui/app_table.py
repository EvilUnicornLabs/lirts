"""Row building, grouping and the table refresh of the dashboard."""

from __future__ import annotations

import time

from rich.text import Text
from textual.widgets import DataTable

from lirts.constants import (
    GLYPH_COLLAPSED,
    GLYPH_ERROR,
    GLYPH_EXPANDED,
    GLYPH_NEW,
    GLYPH_SSH,
    GLYPH_STATUS,
    GLYPH_WARNING,
    SECONDS_PER_MINUTE,
    TOAST_NORMAL,
    TOAST_SHORT,
)
from lirts.filtering import Term, parse_filter, term_matches
from lirts.models import Listener, Status
from lirts.tui.app_base import LirtsAppBase
from lirts.tui.histpanel import feed_history_panel
from lirts.tui.render import status_style
from lirts.tui.widgets import SidePanel


class TableMixin(LirtsAppBase):
    """Turns the snapshot into table rows and keeps the DataTable in step with it."""

    def visible_listeners(self) -> list[Listener]:
        """The rows the table shows right now: filtered, optionally problems only, sorted."""
        items = self.snapshot.listeners
        if self.problems_only:
            items = [x for x in items if x.status in (Status.ERROR, Status.WARNING)]
        if self.filter_text:
            terms = parse_filter(self.filter_text)
            items = [x for x in items if self._matches(x, terms)]
        col = next((c for c in self.columns if c.key == self.sort_key), None)
        if col is not None:
            reverse = self.sort_reverse != col.reverse_default
            # Port as a tiebreak keeps equal values (e.g. 0.0 % CPU) from shuffling on refresh.
            items = sorted(items, key=lambda x: (col.sort_key(x), x.port), reverse=reverse)
        return items

    def _matches(self, lst: Listener, terms: list[Term]) -> bool:
        for term in terms:
            if term.key == "new":
                hit = (
                    self.is_new(lst) if term.value in ("yes", "true", "1") else not self.is_new(lst)
                )
                if term.negate:
                    hit = not hit
                if not hit:
                    return False
            elif not term_matches(lst, term):
                return False
        return True

    GROUP_PREFIX = "group:"

    @staticmethod
    def group_name(lst: Listener) -> str:
        """The stack, project or source a row is grouped under."""
        if lst.source == "ssh":
            return "ssh"
        if lst.container:
            return lst.container.stack or "docker"
        return lst.identity.project or "local"

    def _grouped_rows(
        self, listeners: list[Listener]
    ) -> list[tuple[str, Listener | None, list[Listener]]]:
        """``(row_key, listener or None for a header, group members)`` in display order."""
        groups: dict[str, list[Listener]] = {}
        for lst in listeners:
            groups.setdefault(self.group_name(lst), []).append(lst)
        rows: list[tuple[str, Listener | None, list[Listener]]] = []
        for name in sorted(groups, key=lambda n: (n in ("local", "docker", "ssh"), n.lower())):
            members = groups[name]
            rows.append((self.GROUP_PREFIX + name, None, members))
            if name in self._collapsed:
                continue
            rows.extend((m.key, m, members) for m in members)
        return rows

    def _header_cells(self, name: str, members: list[Listener]) -> list[Text]:
        """The group header row: the worst status of the group and its totals."""
        rank: dict[str, int] = {Status.ERROR: 2, Status.WARNING: 1}
        worst = max((m.status for m in members), key=lambda st: rank.get(st, 0))
        style = status_style(worst) if worst in (Status.ERROR, Status.WARNING) else "bold"
        collapsed = name in self._collapsed
        docker = sum(1 for m in members if m.container)
        detail = Text(f"{len(members)} service{'s' if len(members) != 1 else ''}", style="dim")
        if docker:
            detail.append(f", {docker} container{'s' if docker != 1 else ''}", style="dim")
        cpu = sum(m.cpu_percent for m in members)
        mem = sum(m.memory_mb for m in members)
        has_project = any(c.key == "project" for c in self.columns)
        cells: list[Text] = []
        for col in self.columns:
            chevron = GLYPH_COLLAPSED if collapsed else GLYPH_EXPANDED
            if col.key == "project":
                cells.append(Text(f"{chevron} {name}", style=style))
            elif col.key == "service":
                cells.append(Text(f"{chevron} {name}", style=style) if not has_project else detail)
            elif col.key == "process":
                cells.append(detail if not has_project else Text(""))
            elif col.key == "cpu":
                cells.append(Text(f"{cpu:.1f}%", style="dim", justify="right"))
            elif col.key == "mem":
                cells.append(Text(f"{mem:.0f} MB", style="dim", justify="right"))
            elif col.key == "status":
                labels: dict[str, str] = {
                    Status.ERROR: f"{GLYPH_STATUS} error",
                    Status.WARNING: f"{GLYPH_STATUS} warn",
                }
                cells.append(Text(labels.get(worst, f"{GLYPH_STATUS} ok"), style=style))
            else:
                cells.append(Text(""))
        return cells

    def update_table(self) -> None:
        """Rebuild the rows when their order changed, otherwise update the cells in place."""
        table = self.query_one("#table", DataTable)
        listeners = self.visible_listeners()
        rows: list[tuple[str, Listener | None, list[Listener]]]
        if self.grouped:
            rows = self._grouped_rows(listeners)
        else:
            rows = [(x.key, x, []) for x in listeners]
        self._group_of = {}
        for key, lst, members in rows:
            if lst is None:
                for m in members:
                    self._group_of[m.key] = key[len(self.GROUP_PREFIX) :]
        order = [key for key, _, _ in rows]
        selected = self._selected_key
        if order != self._row_order:
            saved_scroll = table.scroll_y
            table.clear()
            self._cell_cache.clear()
            for key, lst, members in rows:
                cells = self._row_cells(key, lst=lst, members=members)
                mark = self._mark_cell(key) if lst else Text("")
                table.add_row(mark, *cells, key=key)
                for col, cell in zip(self.columns, cells, strict=True):
                    self._cell_cache[(key, col.key)] = cell.plain
            self._row_order = order
            if selected in order:
                table.move_cursor(row=order.index(selected), animate=False, scroll=False)
            elif order:
                table.move_cursor(row=0, animate=False, scroll=False)
            if saved_scroll:
                self.call_after_refresh(table.scroll_to, y=saved_scroll, animate=False, force=True)
        else:
            for key, lst, members in rows:
                row_cells = self._row_cells(key, lst=lst, members=members)
                for col, cell in zip(self.columns, row_cells, strict=True):
                    cache_key = (key, col.key)
                    if self._cell_cache.get(cache_key) != cell.plain:
                        table.update_cell(key, col.key, cell, update_width=True)
                        self._cell_cache[cache_key] = cell.plain
        if not order:
            self._selected_key = None
            self.query_one("#side", SidePanel).show(None)
            feed_history_panel(self, None)
        else:
            self._sync_side_panel()
        live = {x.key for x in self.snapshot.listeners}
        if self._marked - live:
            self._marked &= live
            self._update_mark_subtitle()

    def _row_cells(self, key: str, *, lst: Listener | None, members: list[Listener]) -> list[Text]:
        if lst is None:
            return self._header_cells(key[len(self.GROUP_PREFIX) :], members)
        branch = ""
        if self.grouped and members:
            branch = "└─" if lst is members[-1] else "├─"
        return self._render_cells(lst, branch)

    def _render_cells(self, lst: Listener, branch: str = "") -> list[Text]:
        """Render a row and emphasise it according to its status.

        ``branch`` is the tree connector for grouped rows: it replaces the PROJECT
        cell (the header already names the group) or prefixes the service name.
        """
        cells = [col.render(lst, self.cfg) for col in self.columns]
        has_project = any(c.key == "project" for c in self.columns)
        indent = ""
        if branch:
            if has_project:
                for col, cell in zip(self.columns, cells, strict=True):
                    if col.key == "project":
                        cell.plain = f" {branch}"
                        cell.stylize("dim")
            else:
                indent = f" {branch} "
        if lst.state == "STOPPED":
            for col, cell in zip(self.columns, cells, strict=True):
                if col.key == "service":
                    cell.plain = indent + cell.plain
                cell.stylize("dim strike")
            return cells
        if lst.state == "SSH":
            for col, cell in zip(self.columns, cells, strict=True):
                if col.key == "service":
                    cell.plain = f"{indent}{GLYPH_SSH} " + cell.plain
                    cell.stylize("bold magenta")
                elif col.key == "port":
                    cell.plain = f"→{lst.port}"
            return cells
        if lst.status == Status.ERROR:
            marker, style = f"{GLYPH_ERROR} ", "bold red"
        elif lst.status == Status.WARNING:
            marker, style = f"{GLYPH_WARNING} ", "bold yellow"
        elif self.is_new(lst):
            marker, style = f"{GLYPH_NEW} ", "bold cyan"
        else:
            marker, style = "", ""
        for col, cell in zip(self.columns, cells, strict=True):
            if col.key == "service":
                cell.plain = indent + marker + cell.plain
            if style and col.key in ("port", "service", "process", "status"):
                cell.stylize(style)
        return cells

    def is_new(self, lst: Listener) -> bool:
        """True for services that started within `insights.highlight_new_minutes`."""
        minutes = self.cfg.insights.highlight_new_minutes
        if minutes <= 0:
            return False
        started = lst.uptime_seconds
        if started is None and lst.first_seen:
            started = time.time() - lst.first_seen
        return started is not None and started <= minutes * SECONDS_PER_MINUTE

    def action_toggle_grouping(self) -> None:
        """Group the rows under stack / project headers, or go back to the flat list (G)."""
        self.grouped = not self.grouped
        self._remember("group_by_stack", self.grouped)
        self._row_order = []  # force a rebuild
        self.update_table()
        self.apply_subtitle()
        self.notify(
            (
                "Grouped by stack / project (Space or Enter on a header collapses it)"
                if self.grouped
                else "Flat list"
            ),
            timeout=TOAST_NORMAL,
        )

    def selected_group(self) -> tuple[str, list[Listener]] | None:
        """The group header under the cursor, if any."""
        key = self._selected_key
        if not key or not key.startswith(self.GROUP_PREFIX):
            return None
        name = key[len(self.GROUP_PREFIX) :]
        members = [x for x in self.visible_listeners() if self.group_name(x) == name]
        return name, members

    def select_row(self, key: str) -> bool:
        """Move the cursor to the row with ``key``; False when it is not in the table right now."""
        if key not in self._row_order:
            return False
        table = self.query_one("#table", DataTable)
        table.move_cursor(row=self._row_order.index(key), animate=False)
        self._selected_key = key
        return True

    def toggle_collapse(self, name: str) -> None:
        """Fold or unfold one group header."""
        if name in self._collapsed:
            self._collapsed.discard(name)
        else:
            self._collapsed.add(name)
        self._row_order = []
        self.update_table()

    def action_toggle_problems(self) -> None:
        """Show only the rows with a warning or an error, or all of them again (!)."""
        self.problems_only = not self.problems_only
        self.update_table()
        self.apply_subtitle()
        self.notify(
            (
                "Showing problems only (press ! again for all rows)"
                if self.problems_only
                else "Showing all rows"
            ),
            timeout=TOAST_SHORT,
        )
