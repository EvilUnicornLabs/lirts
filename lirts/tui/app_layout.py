"""Layouts (``U``) and the history box (``Y``).

A layout picks the columns, the side panel sections, which boxes are shown and where they sit
(default, containers, hosts).  The boxes themselves are composed once by
:meth:`lirts.tui.app.LirtsApp.compose`; a layout only moves them and shows or hides them, and it
never writes anything into the config.  ``Y`` toggles the history box and nothing else.
"""

from __future__ import annotations

from typing import Any

from textual.widget import Widget

from lirts.constants import TOAST_NORMAL, TOAST_SHORT
from lirts.settings import LAYOUT_NAMES
from lirts.tui.app_base import LirtsAppBase
from lirts.tui.histpanel import HistoryPanel
from lirts.tui.layout import DEFAULT_LAYOUT, Layout, Panel, SidePosition, layout_for
from lirts.tui.render import resolve_columns
from lirts.tui.widgets import SidePanel

# The container each side panel position mounts the panel into; the slots are composed empty.
SIDE_SLOTS: dict[SidePosition, str] = {
    SidePosition.RIGHT: "#main",
    SidePosition.LEFT: "#side-left",
    SidePosition.BOTTOM: "#bottom",
}

# The boxes under the table, by the panel a layout names in its ``bottom_order``.
BOTTOM_BOXES: tuple[tuple[Panel, str], ...] = ((Panel.NET, "#net"), (Panel.HISTORY, "#history"))


class LayoutMixin(LirtsAppBase):
    """Layouts (``U``), where the panels sit, and the history box (``Y``)."""

    def apply_layout_from_config(self) -> None:
        """Put the saved ``ui.layout`` in force; called once at start-up."""
        self.apply_layout(self.cfg.ui.layout)

    def apply_layout(self, name: str) -> None:
        """Show one layout's columns, sections, boxes and placement; saves nothing."""
        layout = layout_for(name)
        self.layout_name = layout.name
        # A layout overrides what is displayed; the user's own `columns` setting stays untouched.
        names = self.cfg.columns if layout.name == DEFAULT_LAYOUT else list(layout.columns)
        self.columns = resolve_columns(names)
        if self.sort_key not in [c.key for c in self.columns]:
            self.sort_key = self.columns[0].key
        self._build_columns()
        self.query_one("#side", SidePanel).set_sections(layout.side_sections)
        # A layout can ask for the history box; the user's own `ui.history_panel` still wins.
        self.history_wanted = self.cfg.ui.history_panel or Panel.HISTORY in layout.panels
        self._order_bottom_boxes(layout)
        self._place_side_panel(layout)
        self._apply_side_visibility()
        self._apply_net_visibility()
        self._apply_history_visibility()
        self.update_table()

    def apply_layout_setting(self, path: str, value: Any) -> None:
        """Make a changed ``ui.layout`` / ``ui.history_panel`` setting take effect."""
        if path == "ui.layout":
            self.apply_layout(str(value))
        elif path == "ui.history_panel":
            self.history_wanted = bool(value)
            self._apply_history_visibility()

    def action_cycle_layout(self) -> None:
        """Switch to the next layout and remember it (U)."""
        current = LAYOUT_NAMES.index(self.layout_name) if self.layout_name in LAYOUT_NAMES else -1
        name = LAYOUT_NAMES[(current + 1) % len(LAYOUT_NAMES)]
        self.apply_layout(name)
        self._remember("ui.layout", name)
        self.notify(f"Layout: {name}", timeout=TOAST_NORMAL)

    def action_toggle_history(self) -> None:
        """Show or hide the history box of the selected row (Y)."""
        self.history_wanted = not self.history_wanted
        self._apply_history_visibility()
        self._remember("ui.history_panel", self.history_wanted)
        self.notify(
            "History panel " + ("shown" if self.history_wanted else "hidden"), timeout=TOAST_SHORT
        )

    # ----- placement ---------------------------------------------------------------

    def _order_bottom_boxes(self, layout: Layout) -> None:
        """Put the boxes under the table in the order this layout names, the rest after them."""
        by_panel = dict(BOTTOM_BOXES)
        wanted = [by_panel[panel] for panel in layout.bottom_order if panel in by_panel]
        wanted += [box for panel, box in BOTTOM_BOXES if panel not in layout.bottom_order]
        left = self.query_one("#left")
        anchor: Widget = self.query_one("#table")
        for box in wanted:
            widget = self.query_one(box)
            left.move_child(widget, after=anchor)
            anchor = widget

    def _place_side_panel(self, layout: Layout) -> None:
        """Move the side panel into the slot this layout asks for, once the screen can take it."""
        target = self.query_one(SIDE_SLOTS[layout.side_position])
        if self.query_one("#side", SidePanel).parent is target:
            return
        self.call_next(self._remount_side_panel, target)

    async def _remount_side_panel(self, target: Widget) -> None:
        """Re-parent the side panel; Textual needs the removal to finish before the mount."""
        side = self.query_one("#side", SidePanel)
        if side.parent is target:
            return
        await side.remove()
        await target.mount(side)
        self._apply_side_visibility()
        self._sync_side_panel()

    def _apply_history_visibility(self) -> None:
        """Show the box with the selected row's history, or hide it."""
        panel = self._history_panel()
        panel.set_class(not self.history_wanted, "hidden")
        if self.history_wanted:
            panel.show(self.selected())

    def _history_panel(self) -> HistoryPanel:
        """The history box; it is composed with the rest of the dashboard and only hides."""
        return self.query_one("#history", HistoryPanel)
