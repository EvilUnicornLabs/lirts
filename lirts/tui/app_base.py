"""Typing base shared by the ``LirtsApp`` mixins.

Every mixin is composed into ``LirtsApp`` and reaches the app's own state and the methods that
live in the other mixins. At runtime the base is plain ``object``; only the type checker sees
the declarations below, so the mixin modules stay independent and no import cycle appears.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

    from rich.text import Text
    from textual.app import App
    from textual.timer import Timer

    from lirts.config_view import ConfigView
    from lirts.engine import Engine
    from lirts.models import Listener, Snapshot
    from lirts.tui.render import Column

    class LirtsAppBase(App[None]):
        """The state and the cross-mixin methods of ``LirtsApp``, for the type checker only."""

        GROUP_PREFIX: str
        cfg: ConfigView
        columns: list[Column]
        config: dict[str, Any]
        engine: Engine
        filter_text: str
        grouped: bool
        open_setup: bool
        problems_only: bool
        snapshot: Snapshot
        sort_key: str
        sort_reverse: bool
        _cell_cache: dict[tuple[str, str], str]
        _collapsed: set[str]
        _config_mtime: float | None
        _events_seen_ts: float
        _first_refresh_done: bool
        _group_of: dict[str, str]
        _last_event_ts: float
        _marked: set[str]
        _net_scope: str
        _net_wanted: bool
        _refreshing: bool
        _row_order: list[str]
        _selected_key: str | None
        _side_wanted: bool
        _timer: Timer
        history_wanted: bool
        layout_name: str

        def apply_snapshot(self, snapshot: Snapshot) -> None: ...

        def apply_subtitle(self) -> None: ...

        def apply_ui_setting(self, path: str, value: Any) -> None: ...

        def apply_looks(self) -> None: ...

        def apply_layout_from_config(self) -> None: ...

        def apply_layout_setting(self, path: str, value: Any) -> None: ...

        def clear_marks(self) -> bool: ...

        def refresh_data(self) -> Any: ...

        def selected(self) -> Listener | None: ...

        def selected_group(self) -> tuple[str, list[Listener]] | None: ...

        def set_filter(self, text: str) -> None: ...

        def targets(self) -> list[Listener]: ...

        def toggle_collapse(self, name: str) -> None: ...

        def update_table(self) -> None: ...

        def visible_listeners(self) -> list[Listener]: ...

        def _apply_history_visibility(self) -> None: ...

        def _apply_net_visibility(self) -> None: ...

        def _apply_side_visibility(self) -> None: ...

        def _build_columns(self) -> None: ...

        def _mark_cell(self, key: str) -> Text: ...

        def _refresh_net_panel(self) -> None: ...

        def _remember(self, key: str, value: Any) -> None: ...

        def _sync_side_panel(self) -> None: ...

        def _update_mark_subtitle(self) -> None: ...

else:
    LirtsAppBase = object
