"""Typed access to the loaded configuration.

The configuration is one nested dict (loaded by :mod:`lirts.config`, edited by the
Settings screen, saved by ``save_config``).  Code that only reads it goes through
:class:`ConfigView`, so a key name is typed once, defaults come from
:data:`lirts.config_defaults.DEFAULT_CONFIG` and the call site gets the right type.
The view wraps the live dict without copying, so changes made through Settings are
visible immediately.
"""

from __future__ import annotations

from typing import Any

from lirts.config_defaults import DEFAULT_CONFIG


class _Section:
    """Typed reads of one top-level section of the config."""

    name: str = ""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def _get(self, key: str) -> Any:
        section = self._data.get(self.name) or {}
        value = section.get(key)
        return DEFAULT_CONFIG[self.name][key] if value is None else value

    def _float(self, key: str) -> float:
        return float(self._get(key))

    def _int(self, key: str) -> int:
        return int(self._get(key))

    def _bool(self, key: str) -> bool:
        return bool(self._get(key))

    def _str(self, key: str) -> str:
        return str(self._get(key))


class DockerSection(_Section):
    name = "docker"

    @property
    def enabled(self) -> bool:
        return self._bool("enabled")

    @property
    def show_unpublished(self) -> bool:
        return self._bool("show_unpublished")

    @property
    def inspect_interval(self) -> float:
        return self._float("inspect_interval")

    @property
    def stats(self) -> bool:
        return self._bool("stats")


class HttpProbeSection(_Section):
    name = "http_probe"

    @property
    def enabled(self) -> bool:
        return self._bool("enabled")

    @property
    def interval(self) -> float:
        return self._float("interval")

    @property
    def timeout(self) -> float:
        return self._float("timeout")

    @property
    def skip_ports(self) -> list[int]:
        return [int(p) for p in self._get("skip_ports")]

    @property
    def force_ports(self) -> list[int]:
        return [int(p) for p in self._get("force_ports")]


class HealthSection(_Section):
    name = "health"

    @property
    def timeout(self) -> float:
        return self._float("timeout")

    @property
    def paths(self) -> list[str]:
        return [str(p) for p in self._get("paths")]

    @property
    def overrides(self) -> dict[int, str]:
        return {int(k): str(v) for k, v in dict(self._get("overrides")).items()}


class ProxiesSection(_Section):
    name = "proxies"

    @property
    def enabled(self) -> bool:
        return self._bool("enabled")


class KubernetesSection(_Section):
    name = "kubernetes"

    @property
    def enabled(self) -> str | bool:
        """``"auto"``, ``"off"``, ``True`` or ``False`` as written by the user."""
        value = self._get("enabled")
        return value if isinstance(value, bool) else str(value)

    @property
    def context(self) -> str | None:
        value = self._data.get(self.name, {}).get("context")
        return str(value) if value else None

    @property
    def namespace(self) -> str | None:
        value = self._data.get(self.name, {}).get("namespace")
        return str(value) if value else None

    @property
    def all_namespaces(self) -> bool:
        return self._bool("all_namespaces")

    @property
    def interval(self) -> float:
        return self._float("interval")

    @property
    def timeout(self) -> float:
        return self._float("timeout")


class BandwidthSection(_Section):
    name = "bandwidth"

    @property
    def mode(self) -> str:
        return self._str("mode")

    @property
    def interval(self) -> float:
        return self._float("interval")

    @property
    def connections(self) -> bool:
        return self._bool("connections")


class HistorySection(_Section):
    name = "history"

    @property
    def persist(self) -> bool:
        return self._bool("persist")

    @property
    def window(self) -> int:
        return self._int("window")

    @property
    def retention_hours(self) -> float:
        return self._float("retention_hours")


class InsightsSection(_Section):
    name = "insights"

    @property
    def stale_after_hours(self) -> float:
        return self._float("stale_after_hours")

    @property
    def restart_warning(self) -> int:
        return self._int("restart_warning")

    @property
    def show_stopped_minutes(self) -> float:
        return float(self._get("show_stopped_minutes") or 0)

    @property
    def highlight_new_minutes(self) -> float:
        return float(self._get("highlight_new_minutes") or 0)

    @property
    def pattern_days(self) -> int:
        return int(self._get("pattern_days") or 0)

    @property
    def pattern_min(self) -> int:
        return self._int("pattern_min")


class UiSection(_Section):
    name = "ui"

    @property
    def style(self) -> str:
        return self._str("style")

    @property
    def layout(self) -> str:
        return self._str("layout")

    @property
    def glyphs(self) -> str:
        return self._str("glyphs")

    @property
    def rounded_corners(self) -> bool:
        return self._bool("rounded_corners")

    @property
    def truecolor(self) -> str:
        """``auto``, ``on`` or ``off`` (booleans in the file count as on / off)."""
        value = self._get("truecolor")
        if isinstance(value, bool):
            return "on" if value else "off"
        return str(value)

    @property
    def clock(self) -> str:
        return self._str("clock")

    @property
    def history_panel(self) -> bool:
        return self._bool("history_panel")

    @property
    def row_icons(self) -> bool:
        return self._bool("row_icons")


class CliSection(_Section):
    name = "cli"

    @property
    def force_color(self) -> bool:
        return self._bool("force_color")


class TopologySection(_Section):
    name = "topology"

    @property
    def days(self) -> int:
        return self._int("days")

    @property
    def usual_days(self) -> int:
        return self._int("usual_days")


class ThresholdsSection(_Section):
    name = "thresholds"

    def _pair(self, key: str) -> tuple[float, float]:
        value = self._get(key)
        return float(value.get("yellow", 0)), float(value.get("red", 0))

    @property
    def cpu(self) -> tuple[float, float]:
        """(yellow, red) CPU percent."""
        return self._pair("cpu")

    @property
    def memory_mb(self) -> tuple[float, float]:
        return self._pair("memory_mb")

    @property
    def latency_ms(self) -> tuple[float, float]:
        return self._pair("latency_ms")


class ConfigView:
    """Typed reads of the live configuration dict."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self.docker = DockerSection(data)
        self.http_probe = HttpProbeSection(data)
        self.health = HealthSection(data)
        self.proxies = ProxiesSection(data)
        self.kubernetes = KubernetesSection(data)
        self.bandwidth = BandwidthSection(data)
        self.history = HistorySection(data)
        self.insights = InsightsSection(data)
        self.topology = TopologySection(data)
        self.ui = UiSection(data)
        self.cli = CliSection(data)
        self.thresholds = ThresholdsSection(data)

    @property
    def data(self) -> dict[str, Any]:
        """The underlying dict, for the code that saves or edits the config."""
        return self._data

    def _get(self, key: str) -> Any:
        value = self._data.get(key)
        return DEFAULT_CONFIG[key] if value is None else value

    # ----- top-level keys ------------------------------------------------------------------

    @property
    def refresh_interval(self) -> float:
        return float(self._get("refresh_interval"))

    @property
    def theme(self) -> str:
        return str(self._get("theme"))

    @property
    def show_udp(self) -> bool:
        return bool(self._get("show_udp"))

    @property
    def sources(self) -> list[str]:
        return [str(s) for s in self._get("sources")]

    @property
    def hide_system(self) -> bool:
        return bool(self._get("hide_system"))

    @property
    def side_panel(self) -> bool:
        return bool(self._get("side_panel"))

    @property
    def net_panel(self) -> bool:
        return bool(self._get("net_panel"))

    @property
    def group_by_stack(self) -> bool:
        return bool(self._get("group_by_stack"))

    @property
    def sort_by(self) -> str:
        return str(self._get("sort_by"))

    @property
    def sort_desc(self) -> bool:
        return bool(self._get("sort_desc"))

    @property
    def columns(self) -> list[str]:
        return [str(c) for c in self._get("columns")]

    @property
    def aliases(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in dict(self._get("aliases")).items()}

    @property
    def editor(self) -> str:
        return str(self._get("editor")).strip()

    @property
    def mask_env_vars(self) -> list[str]:
        return [str(m) for m in self._get("mask_env_vars")]

    # ----- metadata set by load_config, not part of the schema -----------------------------

    @property
    def path(self) -> str | None:
        value = self._data.get("_path")
        return str(value) if value else None

    @property
    def exists(self) -> bool:
        return bool(self._data.get("_exists"))

    @property
    def file_version(self) -> int | None:
        value = self._data.get("_file_version")
        return int(value) if value is not None else None

    @property
    def obsolete_keys(self) -> list[str]:
        return [str(k) for k in self._data.get("_obsolete_keys") or []]

    @property
    def is_demo(self) -> bool:
        return bool(self._data.get("_demo"))

    @property
    def is_replay(self) -> bool:
        return bool(self._data.get("_replay"))
