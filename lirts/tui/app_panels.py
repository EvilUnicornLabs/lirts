"""The side panel and the traffic panel: what they show and when they are visible."""

from __future__ import annotations

from textual.widgets import DataTable

from lirts.constants import SIDE_PANEL_MIN_TERMINAL_WIDTH, TOAST_NORMAL
from lirts.models import Listener
from lirts.tui.app_base import LirtsAppBase
from lirts.tui.histpanel import feed_history_panel
from lirts.tui.layout import Panel, layout_for
from lirts.tui.netpanel import NetPanel
from lirts.tui.widgets import SidePanel


class PanelsMixin(LirtsAppBase):
    """Feeds the side panel, the traffic box and the history box, and hides them."""

    def _sync_side_panel(self) -> None:
        table = self.query_one("#table", DataTable)
        row, column = table.cursor_coordinate
        # While the rows are rebuilt the cursor can point past the end for one frame.
        if not (0 <= row < table.row_count and 0 <= column < len(table.columns)):
            return
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        key = str(row_key.value)
        self._selected_key = key
        self._show_side_for(key)

    def _show_side_for(self, key: str) -> None:
        self._refresh_net_panel()
        side = self.query_one("#side", SidePanel)
        if key.startswith(self.GROUP_PREFIX):
            group = self.selected_group()
            if group:
                side.show_group(group[0], members=group[1])
            feed_history_panel(self, None)
            return
        listener = self.snapshot.by_key(key)
        side.show(listener, kube=self.snapshot.kube)
        feed_history_panel(self, listener)

    def action_toggle_side(self) -> None:
        """Show or hide the side panel and remember the choice (p)."""
        self._side_wanted = not self._side_wanted
        self.config["side_panel"] = self._side_wanted
        self._apply_side_visibility()
        # Hiding a sibling leaves stale cells on some terminals: relayout and repaint everything.
        self.query_one("#main").refresh(layout=True)
        self.query_one("#table", DataTable).refresh(layout=True)
        self.screen.refresh(repaint=True, layout=True)
        self.call_after_refresh(self.refresh, repaint=True)

    def _refresh_net_panel(self) -> None:
        """Feed the traffic box: the whole machine, or the row under the cursor."""
        if not self._net_wanted:
            return
        panel = self.query_one("#net", NetPanel)
        lst = self.selected() if self._net_scope == "row" else None
        group = self.selected_group() if self._net_scope == "row" and lst is None else None
        if group is not None and group[1]:
            self._show_group_traffic(panel, name=group[0], members=group[1])
            return
        if lst is None:
            self._show_machine_traffic(panel)
            return
        self._show_row_traffic(panel, lst)

    def _show_machine_traffic(self, panel: NetPanel) -> None:
        """Whole-machine throughput, the default scope of the traffic panel."""
        sysinfo = self.snapshot.system
        where = (
            f"{sysinfo.address} ({sysinfo.interface})" if sysinfo.address != "-" else "this machine"
        )
        panel.show(
            f"net · {where} · whole machine  (N: selected row)",
            down=sysinfo.net_down_history,
            up=sysinfo.net_up_history,
            down_now=sysinfo.net_down_bps,
            up_now=sysinfo.net_up_bps,
            down_total=float(sysinfo.net_bytes_recv),
            up_total=float(sysinfo.net_bytes_sent),
            total_note="totals since boot",
        )

    def _show_group_traffic(self, panel: NetPanel, *, name: str, members: list[Listener]) -> None:
        """The traffic of every service in a stack / project group, summed sample by sample."""
        width = max((len(x.bytes_in_history) for x in members), default=0)

        def summed(attr: str) -> list[float]:
            out = [0.0] * width
            for x in members:
                hist = getattr(x, attr)
                for i, v in enumerate(reversed(hist)):
                    if i < width:
                        out[width - 1 - i] += float(v)
            return out

        panel.show(
            f"net · {name} · {len(members)} services summed  (N: whole machine)",
            down=summed("bytes_in_history"),
            up=summed("bytes_out_history"),
            down_now=sum(x.bytes_in_rate or 0.0 for x in members),
            up_now=sum(x.bytes_out_rate or 0.0 for x in members),
            down_total=sum(x.bytes_in_total for x in members),
            up_total=sum(x.bytes_out_total for x in members),
            total_note="totals since lirts started",
        )

    def _show_row_traffic(self, panel: NetPanel, lst: Listener) -> None:
        """The traffic of the selected row, with a note when the rate is not per port."""
        what = (
            f"container {lst.container.name}"
            if lst.container
            else (f"PID {lst.pid}" if lst.pid else "")
        )
        note = ""
        if lst.bytes_in_rate is None and lst.bytes_out_rate is None:
            note = "no per-process rates here (bandwidth.mode off or unsupported)"
        elif lst.bandwidth_shared:
            note = "process serves several ports: rate is per process"
        panel.show(
            f"net · {lst.identity.service} :{lst.port} {what}  (N: whole machine)".rstrip(),
            down=lst.bytes_in_history,
            up=lst.bytes_out_history,
            down_now=lst.bytes_in_rate or 0.0,
            up_now=lst.bytes_out_rate or 0.0,
            down_total=lst.bytes_in_total,
            up_total=lst.bytes_out_total,
            total_note="totals since lirts started",
            note=note,
        )

    def action_net_scope(self) -> None:
        """Switch the traffic panel between the whole machine and the selected row (N)."""
        if not self._net_wanted:
            self.notify("Traffic panel is off (Settings: Traffic panel)", timeout=TOAST_NORMAL)
            return
        self._net_scope = "machine" if self._net_scope == "row" else "row"
        self._refresh_net_panel()

    def _apply_net_visibility(self) -> None:
        """Hide the traffic box when the setting is off or the layout leaves it out."""
        off = Panel.NET not in layout_for(self.layout_name).panels
        self.query_one("#net", NetPanel).set_class(not self._net_wanted or off, "hidden")

    def _apply_side_visibility(self) -> None:
        """Hide the side panel when the user asked for it or the terminal is too narrow."""
        side = self.query_one("#side", SidePanel)
        narrow = self.size.width < SIDE_PANEL_MIN_TERMINAL_WIDTH
        off = Panel.SIDE not in layout_for(self.layout_name).panels
        side.set_class(not self._side_wanted or narrow or off, "hidden")

    def on_resize(self) -> None:
        self._apply_side_visibility()
