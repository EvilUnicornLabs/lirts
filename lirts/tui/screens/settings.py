"""The Settings screen: every user-facing setting in tabs, changed through modals."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Static, TabbedContent, TabPane

from lirts import __version__
from lirts.config import CONFIG_VERSION, DEFAULT_CONFIG, outdated_reason, save_config
from lirts.constants import TOAST_DETAIL, TOAST_NORMAL
from lirts.settings import (
    ALL_COLUMNS,
    SETTING_TABS,
    SETTINGS,
    Setting,
    SettingKind,
    coerce,
    format_value,
    get_path,
    set_path,
    settings_on_tab,
    tab_for,
)
from lirts.tui.screens.columns import ColumnsScreen
from lirts.tui.screens.help_confirm import ConfirmScreen
from lirts.tui.screens.pane_focus import focus_active_pane
from lirts.tui.screens.picker import PickerScreen, glyph_preview
from lirts.tui.screens.prompt import PromptScreen

# The VALUE column is fixed so that DESCRIPTION always fits; the full value is in the picker.
VALUE_COLUMN_WIDTH = 28
RESTART_SUFFIX = "  (restart)"

THEME_PATH = "theme"
GLYPHS_PATH = "ui.glyphs"


def tab_pane_id(tab: str) -> str:
    """The id of the tab pane holding a settings tab."""
    return f"settings-tab-{tab.lower()}"


def tab_table_id(tab: str) -> str:
    """The id of the settings table on a tab."""
    return f"settings-{tab.lower()}"


class SettingsScreen(Screen[None]):
    """Every user-facing setting, in tabs, editable in place and saved with `s`; opened with `,`."""

    BINDINGS = [
        Binding("escape,q,comma", "close", "Close"),
        Binding("enter,space", "edit", "Change"),
        Binding("s", "save", "Save"),
        Binding("R", "reset", "Defaults"),
        Binding("W", "wizard", "Wizard"),
        Binding("right", "next_tab", "Next tab", show=False, priority=True),
        Binding("left", "prev_tab", "Previous tab", show=False, priority=True),
        Binding("tab", "next_tab", "Next tab", show=False),
        Binding("shift+tab", "prev_tab", "Previous tab", show=False),
    ]

    def __init__(self, app_ref: Any) -> None:
        super().__init__()
        self.lirts = app_ref
        self.dirty = False
        self._theme_at_open = str(app_ref.theme)
        self._theme_config_at_open = get_path(app_ref.config, THEME_PATH)

    def action_wizard(self) -> None:
        """Close the screen and start the step-by-step setup wizard instead (W)."""
        self.dismiss(None)
        self.lirts.action_setup()

    # ----- layout -------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("Settings", classes="screen-title")
        with TabbedContent(id="settings-tabs"):
            for tab in SETTING_TABS:
                with TabPane(tab, id=tab_pane_id(tab)):
                    if tab == SETTING_TABS[0]:
                        yield Static(id="settings-info")
                    yield DataTable(id=tab_table_id(tab), cursor_type="row", zebra_stripes=True)
        yield Static(
            "Enter/Space change · ← → Tab tabs · s save to the config file · R reset to "
            "defaults · W step-by-step wizard · Esc close (changes apply immediately)",
            classes="screen-footer",
        )

    def on_mount(self) -> None:
        for tab in SETTING_TABS:
            table = self.query_one(f"#{tab_table_id(tab)}", DataTable)
            table.add_column("SETTING", key="label")
            table.add_column("VALUE", key="value", width=VALUE_COLUMN_WIDTH)
            table.add_column("DESCRIPTION", key="desc")
            for setting in settings_on_tab(tab):
                table.add_row(
                    Text(setting.label, style="bold"),
                    self._value_cell(setting),
                    Text(setting.description, style="dim"),
                    key=setting.path,
                )
        self.query_one(f"#{tab_table_id(SETTING_TABS[0])}", DataTable).focus()
        self.render_info()

    def _table_for(self, setting: Setting) -> DataTable[Any]:
        return self.query_one(f"#{tab_table_id(tab_for(setting))}", DataTable)

    def _value_cell(self, setting: Setting) -> Text:
        """The VALUE cell: the value cut to the fixed column width, with the restart marker."""
        value = get_path(self.lirts.config, setting.path)
        style = {SettingKind.BOOL: "green" if value else "dim"}.get(setting.kind, "cyan")
        cell = Text(format_value(setting, value), style=style)
        room = VALUE_COLUMN_WIDTH - (0 if setting.live else len(RESTART_SUFFIX))
        cell.truncate(room, overflow="ellipsis")
        if not setting.live:
            cell.append(RESTART_SUFFIX, style="dim")
        return cell

    def render_info(self) -> None:
        """The version, the config file and whether it matches the current schema."""
        cfg = self.lirts.config
        view = self.lirts.cfg
        info = Text()
        info.append(f"lirts {__version__}", style="bold")
        info.append(f"   config: {view.path}", style="")
        if not view.exists:
            info.append("  (no file yet: defaults in use; press s to create it)", style="yellow")
        elif outdated_reason(cfg):
            info.append(
                f"  OUTDATED ({outdated_reason(cfg)}): press s to rewrite it for schema"
                f" {CONFIG_VERSION}; nothing is changed until you do",
                style="bold yellow",
            )
        else:
            info.append(f"  (schema {CONFIG_VERSION}, up to date)", style="green")
        if self.dirty:
            info.append(
                "\nUnsaved changes: press s to keep them for next time.", style="bold yellow"
            )
        else:
            info.append(
                "\nChanges take effect immediately; s writes them to the file.", style="dim"
            )
        self.query_one("#settings-info", Static).update(info)

    # ----- tabs ---------------------------------------------------------------------

    def _cycle_tab(self, step: int) -> None:
        tabs = self.query_one("#settings-tabs", TabbedContent)
        ids = [tab_pane_id(t) for t in SETTING_TABS]
        idx = ids.index(str(tabs.active)) if str(tabs.active) in ids else 0
        tabs.active = ids[(idx + step) % len(ids)]

    def action_next_tab(self) -> None:
        """Move one tab right; the list never edits with the arrows (→ / Tab)."""
        self._cycle_tab(1)

    def action_prev_tab(self) -> None:
        """Move one tab left; the list never edits with the arrows (← / Shift+Tab)."""
        self._cycle_tab(-1)

    @on(TabbedContent.TabActivated, "#settings-tabs")
    def _tab_activated(self, event: TabbedContent.TabActivated) -> None:
        focus_active_pane(self, "#settings-tabs")

    # ----- editing ------------------------------------------------------------------

    def _current(self) -> Setting | None:
        tabs = self.query_one("#settings-tabs", TabbedContent)
        ids = {tab_pane_id(t): t for t in SETTING_TABS}
        tab = ids.get(str(tabs.active), SETTING_TABS[0])
        table = self.query_one(f"#{tab_table_id(tab)}", DataTable)
        if table.row_count == 0:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        return next((s for s in SETTINGS if s.path == str(row_key.value)), None)

    def _apply(self, setting: Setting, value: Any) -> None:
        set_path(self.lirts.config, path=setting.path, value=value)
        self.lirts.apply_setting(setting.path, value)
        self.dirty = True
        self._table_for(setting).update_cell(setting.path, "value", self._value_cell(setting))
        self.render_info()

    def _open_picker(self, setting: Setting, value: Any) -> None:
        """Open the value list of a choice setting; Enter applies it, Esc changes nothing."""
        choices = list(setting.choices) or sorted(self.lirts.available_themes)
        is_theme = setting.path == THEME_PATH
        theme_before = str(self.lirts.theme)

        def preview_theme(choice: str) -> None:
            self.lirts.theme = choice

        def done(choice: str | None) -> None:
            if choice is None:
                if is_theme:
                    self.lirts.theme = theme_before
                return
            self._apply(setting, choice if is_theme else coerce(setting, choice))

        self.app.push_screen(
            PickerScreen(
                setting,
                choices=choices,
                current=format_value(setting, value),
                preview=preview_theme if is_theme else None,
                describe=glyph_preview if setting.path == GLYPHS_PATH else None,
            ),
            done,
        )

    def _open_checklist(self, setting: Setting, value: Any) -> None:
        """Open the checklist of a columns / multi setting."""

        def done(chosen: list[str] | None) -> None:
            if chosen:
                self._apply(setting, chosen)

        available = ALL_COLUMNS if setting.kind == SettingKind.COLUMNS else list(setting.choices)
        self.app.push_screen(ColumnsScreen(list(value or []), available, setting.label), done)

    def _open_prompt(self, setting: Setting, value: Any) -> None:
        """Open the text prompt of a number / text setting."""
        shown = format_value(setting, value)

        def done(raw: str | None) -> None:
            if raw is None:
                return
            try:
                self._apply(setting, coerce(setting, raw))
            except ValueError as exc:
                self.notify(f"{setting.label}: {exc}", severity="error", timeout=TOAST_DETAIL)

        self.app.push_screen(
            PromptScreen(
                setting.label,
                placeholder=shown,
                default=shown if value not in (None, "") else "",
                hint=setting.description,
            ),
            done,
        )

    def action_edit(self) -> None:
        """Change the setting under the cursor: toggle it or open its modal (Enter / Space)."""
        setting = self._current()
        if setting is None:
            return
        value = get_path(self.lirts.config, setting.path)
        if setting.kind == SettingKind.BOOL:
            self._apply(setting, not value)
        elif setting.kind == SettingKind.CHOICE:
            self._open_picker(setting, value)
        elif setting.kind in (SettingKind.COLUMNS, SettingKind.MULTI):
            self._open_checklist(setting, value)
        else:
            self._open_prompt(setting, value)

    @on(DataTable.RowSelected)
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_edit()

    # ----- saving and leaving ---------------------------------------------------------

    def action_save(self) -> None:
        """Write every current value to the config file (s)."""
        if not save_config(self.lirts.config):
            self.notify("Could not write the config file", severity="error", timeout=TOAST_DETAIL)
            return
        self.dirty = False
        self._theme_at_open = str(self.lirts.theme)
        self._theme_config_at_open = get_path(self.lirts.config, THEME_PATH)
        self.lirts.config["_exists"] = True
        self.lirts.config["_file_version"] = CONFIG_VERSION
        self.lirts.config["_obsolete_keys"] = []
        self.render_info()
        self.notify(f"Saved {self.lirts.cfg.path}", timeout=TOAST_NORMAL)

    def action_reset(self) -> None:
        """Put every setting back to its default, after a confirmation (R)."""

        def done(ok: bool | None) -> None:
            if not ok:
                return
            for setting in SETTINGS:
                default = get_path(DEFAULT_CONFIG, setting.path)
                self._apply(setting, default)
            self.notify("Defaults restored (press s to save)", timeout=TOAST_NORMAL)

        self.app.push_screen(
            ConfirmScreen(
                "Reset all settings to defaults?",
                Text("Aliases and skip lists are kept."),
            ),
            done,
        )

    def _restore_theme(self) -> None:
        """Put the theme that was live when the screen opened back; unsaved is not chosen."""
        if str(self.lirts.theme) == self._theme_at_open:
            return
        set_path(self.lirts.config, path=THEME_PATH, value=self._theme_config_at_open)
        self.lirts.apply_setting(THEME_PATH, self._theme_at_open)

    def action_close(self) -> None:
        """Leave the screen, warning when the changes were not saved (Esc / q / ,)."""
        self._restore_theme()
        if self.dirty:
            self.notify(
                "Settings changed but not saved (s); they last until you quit",
                severity="warning",
                timeout=TOAST_DETAIL,
            )
        self.dismiss(None)
