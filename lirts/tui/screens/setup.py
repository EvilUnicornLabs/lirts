"""Step-by-step walk through the settings."""

from __future__ import annotations

from typing import Any

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Input, OptionList, SelectionList, Static
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from lirts.config import CONFIG_VERSION, save_config
from lirts.constants import TOAST_DETAIL
from lirts.settings import ALL_COLUMNS, SETTINGS, Setting, coerce, format_value, get_path, set_path

SETUP_STEPS = [
    "theme",
    "ui.style",
    "ui.layout",
    "ui.glyphs",
    "ui.rounded_corners",
    "ui.row_icons",
    "ui.history_panel",
    "ui.clock",
    "sources",
    "columns",
    "refresh_interval",
    "group_by_stack",
    "side_panel",
    "net_panel",
    "hide_system",
    "show_udp",
    "http_probe.enabled",
    "proxies.enabled",
    "insights.highlight_new_minutes",
    "insights.show_stopped_minutes",
    "kubernetes.enabled",
    "bandwidth.mode",
    "history.persist",
]


class SetupScreen(Screen[bool | None]):
    """Step-by-step walk through the settings; opened with `W` and on the very first start.

    Nothing is applied or saved before the summary step is confirmed.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter,right", "next", "Next", priority=True),
        Binding("left", "previous", "Back", priority=True),
    ]

    def __init__(self, app_ref: Any) -> None:
        super().__init__()
        self.lirts = app_ref
        self.steps: list[Setting] = [s for p in SETUP_STEPS for s in SETTINGS if s.path == p]
        self.values: dict[str, Any] = {
            s.path: get_path(self.lirts.config, s.path) for s in self.steps
        }
        self.index = 0

    # ----- layout -------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("Setup", classes="screen-title", id="setup-title")
        yield Static(id="setup-question")
        yield OptionList(id="setup-options")
        yield SelectionList[str](id="setup-multi")
        yield Input(id="setup-input")
        yield Static(id="setup-summary")
        yield Static(
            "Enter / → next · ← back · Space toggles a checklist item · Esc cancel without saving",
            classes="screen-footer",
        )

    def on_mount(self) -> None:
        self.show_step()

    @property
    def total(self) -> int:
        """Number of steps, the summary included."""
        return len(self.steps) + 1  # + summary

    def _choices(self, setting: Setting) -> list[str]:
        if setting.kind == "bool":
            return ["on", "off"]
        if setting.path == "theme":
            return sorted(self.lirts.available_themes)
        return list(setting.choices)

    def show_step(self) -> None:
        """Draw the widget the current step needs, or the summary after the last one."""
        options = self.query_one("#setup-options", OptionList)
        multi = self.query_one("#setup-multi", SelectionList)
        field = self.query_one("#setup-input", Input)
        summary = self.query_one("#setup-summary", Static)
        for w in (options, multi, field, summary):
            w.display = False
        title = self.query_one("#setup-title", Static)
        question = self.query_one("#setup-question", Static)
        if self.index >= len(self.steps):
            title.update(f"Setup — step {self.total}/{self.total}: summary")
            question.update(
                Text("Enter saves these to ", style="bold")
                + Text(str(self.lirts.cfg.path), style="cyan")
                + Text(" and applies them; ← goes back; Esc discards.", style="bold")
            )
            grid = Table.grid(padding=(0, 2))
            grid.add_column(style="bold", width=28)
            grid.add_column(style="cyan")
            for s in self.steps:
                grid.add_row(s.label, format_value(s, self.values[s.path]))
            summary.update(grid)
            summary.display = True
            self.set_focus(None)
            return
        setting = self.steps[self.index]
        title.update(f"Setup — step {self.index + 1}/{self.total}: {setting.label}")
        question.update(
            Text(setting.description, style="bold")
            + (
                Text("  (takes effect after a restart)", style="dim")
                if not setting.live
                else Text("")
            )
        )
        value = self.values[setting.path]
        if setting.kind in ("bool", "choice"):
            options.clear_options()
            choices = self._choices(setting)
            for c in choices:
                options.add_option(Option(c, id=c))
            current = format_value(setting, value)
            options.highlighted = choices.index(current) if current in choices else 0
            options.display = True
            options.focus()
        elif setting.kind in ("columns", "multi"):
            multi.clear_options()
            available = ALL_COLUMNS if setting.kind == "columns" else list(setting.choices)
            chosen = list(value or [])
            for name in [*chosen, *[c for c in available if c not in chosen]]:
                multi.add_option(Selection(name, name, name in chosen))
            multi.display = True
            multi.focus()
        else:
            field.value = "" if value in (None, "") else format_value(setting, value)
            field.placeholder = setting.description
            field.display = True
            field.focus()

    # ----- navigation -----------------------------------------------------------------

    def _read_current(self) -> bool:
        """Store the widget's value for the current step; False when it is invalid."""
        if self.index >= len(self.steps):
            return True
        setting = self.steps[self.index]
        try:
            if setting.kind in ("bool", "choice"):
                options = self.query_one("#setup-options", OptionList)
                idx = options.highlighted if options.highlighted is not None else 0
                self.values[setting.path] = coerce(
                    setting, str(options.get_option_at_index(idx).id)
                )
            elif setting.kind in ("columns", "multi"):
                multi = self.query_one("#setup-multi", SelectionList)
                chosen = [str(v) for v in multi.selected]
                if not chosen:
                    raise ValueError("pick at least one")
                order = [str(multi.get_option_at_index(i).value) for i in range(multi.option_count)]
                self.values[setting.path] = [c for c in order if c in chosen]
            else:
                raw = self.query_one("#setup-input", Input).value
                self.values[setting.path] = (
                    coerce(setting, raw)
                    if raw.strip()
                    else (None if setting.kind == "text" else self.values[setting.path])
                )
        except ValueError as exc:
            self.notify(f"{setting.label}: {exc}", severity="error", timeout=TOAST_DETAIL)
            return False
        return True

    def action_next(self) -> None:
        """Store this step's answer and move on, or save on the summary (Enter / →)."""
        if not self._read_current():
            return
        if self.index >= len(self.steps):
            self._save()
            return
        self.index += 1
        self.show_step()

    def action_previous(self) -> None:
        """Go back one step, keeping the answer already given (←)."""
        if self.index == 0:
            return
        self._read_current()
        self.index -= 1
        self.show_step()

    def action_cancel(self) -> None:
        """Leave without applying or saving anything (Esc)."""
        self.dismiss(None)

    def _save(self) -> None:
        for s in self.steps:
            value = self.values[s.path]
            if get_path(self.lirts.config, s.path) != value:
                set_path(self.lirts.config, path=s.path, value=value)
                self.lirts.apply_setting(s.path, value)
        if save_config(self.lirts.config):
            self.lirts.config["_exists"] = True
            self.lirts.config["_file_version"] = CONFIG_VERSION
            self.lirts.config["_obsolete_keys"] = []
            self.dismiss(True)
        else:
            self.notify("Could not write the config file", severity="error", timeout=TOAST_DETAIL)
