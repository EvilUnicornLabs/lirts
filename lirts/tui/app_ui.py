"""Looks: styles, glyph sets, borders, clock format, colour depth.

The mixin applies the ``ui.*`` settings to the running app; :mod:`lirts.tui.styles` and
:mod:`lirts.tui.glyphs` hold the bundles and the glyph tables.  ``apply_looks`` is the start-up
entry point; ``T`` cycles the styles.  Colours belong to the ``theme`` setting alone: a style
never touches ``app.theme``.
"""

from __future__ import annotations

from typing import Any

from textual.widgets import DataTable

from lirts.constants import TOAST_LONG, TOAST_SHORT
from lirts.settings import set_path
from lirts.tui.app_base import LirtsAppBase
from lirts.tui.glyphs import set_active
from lirts.tui.render import set_clock_format
from lirts.tui.styles import (
    Density,
    Style,
    effective_border,
    next_style,
    register_style_themes,
    style_for,
)
from lirts.tui.topbar import TopBar

# Cells of padding each side of a table column, per density.
CELL_PADDING: dict[str, int] = {Density.NORMAL: 1, Density.DENSE: 0, Density.TIGHT: 0}

# The app classes a style owns; they are replaced together, everything else is left alone.
LOOK_CLASS_PREFIXES = ("style-", "density-", "borders-", "corners-")


class UiMixin(LirtsAppBase):
    """Styles, glyphs, borders and the clock; ``T`` cycles the styles."""

    def apply_looks(self) -> None:
        """Apply style, glyphs, corners, theme and clock from the config; call from ``on_mount``.

        The two themes :mod:`lirts.tui.styles` brings are registered here, so the ``theme``
        setting is applied afterwards: until now ``cracktro`` and ``phosphor`` were unknown names.
        """
        register_style_themes(self)
        if self.cfg.theme in self.available_themes:
            self.theme = self.cfg.theme
        # At start-up the saved panel settings win; a style changes boxes only when chosen (T).
        self.apply_style(self.cfg.ui.style, set_panels=False)
        # An explicit glyph choice wins over the one the style would draw with.
        set_active(self.cfg.ui.glyphs)
        self._apply_clock()
        self._redraw_looks()

    def apply_style(self, name: str, *, set_panels: bool = True) -> None:
        """Switch the look to a style: classes, glyph set and (when the user chose it) the boxes."""
        style = style_for(name)
        set_active(style.glyphs)
        self._apply_look_classes(style)
        if set_panels:
            self._apply_style_panels(style)
        self._redraw_looks(style)

    def action_cycle_style(self) -> None:
        """Switch to the next style and remember it (T)."""
        style = next_style(self.cfg.ui.style)
        self.apply_style(style.name)
        set_path(self.config, path="ui.style", value=style.name)
        set_path(self.config, path="ui.glyphs", value=style.glyphs)
        self._remember("ui", self.config["ui"])
        self.notify(f"Style: {style.name}", timeout=TOAST_SHORT)

    def apply_ui_setting(self, path: str, value: Any) -> None:
        """Make a changed ``ui.*`` setting take effect."""
        if path in ("ui.layout", "ui.history_panel"):
            self.apply_layout_setting(path, value)
            return
        if path == "ui.style":
            self.apply_style(str(value))
            return
        if path == "ui.glyphs":
            set_active(str(value))
            self._redraw_looks()
            return
        if path == "ui.rounded_corners":
            self._apply_look_classes(style_for(self.cfg.ui.style))
            return
        if path == "ui.row_icons":
            self._redraw_looks()
            return
        if path == "ui.clock":
            self._apply_clock()
            return
        if path == "ui.truecolor":
            self.notify(f"Colour depth is {value} from the next start of lirts", timeout=TOAST_LONG)
            return
        self.notify(f"{path} takes effect on the next start")

    # ----- pieces of a look --------------------------------------------------------

    def _apply_look_classes(self, style: Style) -> None:
        """Replace the CSS classes that drive density, borders and corners."""
        for name in [c for c in self.classes if c.startswith(LOOK_CLASS_PREFIXES)]:
            self.remove_class(name)
        rounded = self.cfg.ui.rounded_corners
        self.add_class(
            f"style-{style.name}",
            f"density-{style.density}",
            f"borders-{effective_border(style, rounded_corners=rounded)}",
            "corners-round" if rounded else "corners-square",
        )

    def _apply_style_panels(self, style: Style) -> None:
        """Show or hide the side, traffic and history boxes the way the style wants them.

        Nothing is written to the config: the saved ``side_panel``, ``net_panel`` and
        ``ui.history_panel`` are the user's and come back on the next start.
        """
        if not self._looks_ready():
            return
        self._side_wanted = style.side_panel
        self._net_wanted = style.net_panel
        self.history_wanted = style.history_panel
        self._apply_side_visibility()
        self._apply_net_visibility()
        self._refresh_net_panel()
        self._apply_history_visibility()

    def _apply_clock(self) -> None:
        """Take ``ui.clock`` into the top bar and into every time lirts prints."""
        set_clock_format(self.cfg.ui.clock)
        if self._looks_ready():
            self.query_one("#top", TopBar).set_clock_format(self.cfg.ui.clock)

    def _redraw_looks(self, style: Style | None = None) -> None:
        """Repaint everything that caches a glyph: the table cells, the top bar, the panels."""
        if not self._looks_ready():
            return
        style = style or style_for(self.cfg.ui.style)
        table = self.query_one("#table", DataTable)
        table.cell_padding = CELL_PADDING.get(style.density, 1)
        top = self.query_one("#top", TopBar)
        top.set_density(str(style.density))
        top.set_scroller(style.scroller)
        self._cell_cache.clear()
        if self._first_refresh_done:
            self.update_table()
        self._sync_side_panel()
        self.refresh(layout=True)

    def _looks_ready(self) -> bool:
        """True once the dashboard's own widgets are mounted and can be told about a look."""
        return self.is_running and bool(self.query("#table"))
