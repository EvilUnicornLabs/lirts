"""Watching the config file and applying a changed setting to the running app."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lirts.config import load_config, outdated_reason, save_config, state_dir
from lirts.constants import MIN_REFRESH_INTERVAL, TOAST_DETAIL, TOAST_LONG, TOAST_ONCE, TOAST_SHORT
from lirts.settings import SETTINGS, get_path, set_path
from lirts.tui.app_base import LirtsAppBase
from lirts.tui.render import resolve_columns


class SettingsMixin(LirtsAppBase):
    """Reloads the config file when it changes and makes a setting take effect."""

    def _config_stat(self) -> float | None:
        """The config file's modification time, or None when there is no readable file."""
        try:
            return Path(str(self.cfg.path)).stat().st_mtime
        except OSError:  # no file yet, or it was removed between two checks
            return None

    def _check_config_file(self) -> None:
        """Reload the config file when it changed on disk and apply the differences."""
        current = self._config_stat()
        if current == self._config_mtime:
            return
        self._config_mtime = current
        if current is None:
            return
        fresh = load_config(self.cfg.path)
        changed = [s for s in SETTINGS if get_path(fresh, s.path) != get_path(self.config, s.path)]
        # Keep the same dict object: the engine holds a reference to it.
        self.config.clear()
        self.config.update(fresh)
        for setting in changed:
            self.apply_setting(setting.path, get_path(fresh, setting.path))
        self.notify(
            "Config reloaded" + (f": {', '.join(s.label for s in changed[:4])}" if changed else ""),
            timeout=TOAST_DETAIL,
        )
        if self._first_refresh_done:
            self.refresh_data()

    def _first_run_hint(self) -> None:
        """Once per machine: point at the help and the settings."""
        marker = state_dir() / ".welcomed"
        if marker.exists():
            return
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("1")
        except OSError:  # a read-only or missing state directory: the hint is not worth failing on
            return
        self.notify(
            "Welcome to lirts. ? shows every key, , opens settings (theme, columns, behaviour).",
            timeout=TOAST_ONCE,
        )

    def _config_outdated_hint(self) -> None:
        """The config file was written for another schema: say what is ignored and how to fix it."""
        reason = outdated_reason(self.config)
        if reason:
            self.notify(
                f"Config file outdated ({reason}). Nothing in it is migrated automatically: "
                "open Settings (,) and press s, or run `lirts config upgrade`.",
                severity="warning",
                timeout=TOAST_ONCE,
            )

    def _remember(self, key: str, value: Any) -> None:
        """Persist a view preference (``key`` may be dotted) when a config file already exists.

        Never creates a file: the first write is always the user's (Settings ``s`` or the wizard).
        """
        set_path(self.config, path=key, value=value)
        if self.cfg.exists:
            save_config(self.config)
            self._config_mtime = self._config_stat()

    def action_cycle_theme(self) -> None:
        """Move to the next colour theme and save it to the config (T)."""
        names = sorted(self.available_themes)
        idx = names.index(self.theme) if self.theme in names else -1
        self.theme = names[(idx + 1) % len(names)]
        self.config["theme"] = self.theme
        save_config(self.config)
        self.notify(f"Theme: {self.theme}", timeout=TOAST_SHORT)

    def apply_setting(self, path: str, value: Any) -> None:
        """Make a changed setting take effect immediately where possible."""
        if path == "theme" and value in self.available_themes:
            self.theme = str(value)
        elif path.startswith("ui."):
            self.apply_ui_setting(path, value)
        elif path == "refresh_interval":
            self._timer.stop()
            self._timer = self.set_interval(
                max(MIN_REFRESH_INTERVAL, float(value)), self.refresh_data
            )
        elif path == "columns":
            self.columns = resolve_columns(list(value))
            if self.sort_key not in [c.key for c in self.columns]:
                self.sort_key = self.columns[0].key
            self._build_columns()
            self.update_table()
        elif path == "group_by_stack":
            self.grouped = bool(value)
            self._row_order = []
            self.update_table()
        elif path == "side_panel":
            self._side_wanted = bool(value)
            self._apply_side_visibility()
        elif path == "net_panel":
            self._net_wanted = bool(value)
            self._apply_net_visibility()
            self._refresh_net_panel()
        elif path == "show_udp":
            self.engine.show_udp = bool(value)
            self.refresh_data()
        elif path == "sources":
            self.engine.set_sources(list(value))
            self.refresh_data()
        elif path == "hide_system":
            self.engine.hide_system = bool(value)
            self.refresh_data()
        elif path == "http_probe.enabled":
            self.engine.prober.enabled = bool(value)
        elif path == "bandwidth.connections":
            self.engine.bandwidth.connections = bool(value)
            if value and not self.engine.bandwidth.available:
                self.notify(
                    "Per-connection traffic needs bandwidth.mode auto and nettop (macOS) or ss (Linux)",
                    severity="warning",
                    timeout=TOAST_LONG,
                )
        elif path == "proxies.enabled":
            self.engine.proxies_enabled = bool(value)
            self.engine.reload_routes_on_next_refresh()
            self.refresh_data()
        elif path == "http_probe.interval":
            self.engine.prober.interval = float(value)
        elif path == "docker.enabled":
            self.engine.docker.enabled = bool(value) and "docker" in self.engine.sources
        elif path == "kubernetes.enabled":
            self.engine.kube.mode = value
            if self.engine.kube.enabled:
                self.engine.kube.start()
        elif path == "kubernetes.namespace":
            self.engine.kube.namespace = value or None
            self.engine.kube.refresh_soon()
        elif path == "kubernetes.all_namespaces":
            self.engine.kube.all_namespaces = bool(value)
            self.engine.kube.refresh_soon()
        if self._first_refresh_done:
            self.apply_snapshot(self.snapshot)
