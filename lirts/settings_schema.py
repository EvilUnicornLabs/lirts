"""What a setting is: its kind, its tab, and how its value is read, shown and parsed."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from lirts.config_defaults import INTEGER_KEYS
from lirts.tui.render import COLUMNS

ALL_COLUMNS = list(COLUMNS)


class SettingKind(StrEnum):
    """What a setting holds, and so how it is displayed and parsed."""

    BOOL = "bool"
    NUMBER = "number"
    CHOICE = "choice"
    TEXT = "text"
    COLUMNS = "columns"
    MULTI = "multi"


@dataclass(frozen=True)
class Setting:
    """One user-facing setting: where it lives in the config and how it is edited."""

    path: str  # dotted key into the config
    label: str
    kind: SettingKind
    description: str
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    live: bool = True  # applied immediately; False = takes effect on next start
    tab: str = ""  # Settings screen tab; empty = decided by tab_for()


SETTING_TABS: tuple[str, ...] = ("General", "Layout", "Collection", "Memory", "Insights")
_TAB_BY_PREFIX: dict[str, str] = {
    "ui.": "Layout",
    "theme": "Layout",
    "columns": "Layout",
    "group_by_stack": "Layout",
    "side_panel": "Layout",
    "net_panel": "Layout",
    "docker.": "Collection",
    "kubernetes.": "Collection",
    "bandwidth.": "Collection",
    "http_probe.": "Collection",
    "proxies.": "Collection",
    "sources": "Collection",
    "history.": "Memory",
    "topology.": "Memory",
    "daemon.": "Collection",
    "insights.pattern": "Memory",
    "insights.": "Insights",
    "thresholds.": "Insights",
}


def tab_for(setting: Setting) -> str:
    """The Settings tab a setting lives on: its own ``tab`` or one decided by its path."""
    if setting.tab:
        return setting.tab
    for prefix, tab in _TAB_BY_PREFIX.items():
        if setting.path.startswith(prefix):
            return tab
    return "General"


def get_path(config: dict[str, Any], path: str) -> Any:
    """The value at a dotted key, or None when any step of the path is missing."""
    node: Any = config
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def set_path(config: dict[str, Any], *, path: str, value: Any) -> None:
    """Write ``value`` at a dotted key, creating the sections on the way."""
    parts = path.split(".")
    node: Any = config
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def format_value(setting: Setting, value: Any) -> str:
    """A setting's value as the Settings screen shows it."""
    if setting.kind == SettingKind.BOOL:
        return "on" if value else "off"
    if setting.kind in (SettingKind.COLUMNS, SettingKind.MULTI):
        return ", ".join(value or [])
    if value is None or value == "":
        return "(default)" if setting.kind == SettingKind.TEXT else "-"
    if setting.kind == SettingKind.CHOICE and isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def coerce(setting: Setting, raw: str) -> Any:
    """Turn user text into the setting's type; raises ValueError."""
    if setting.kind == SettingKind.BOOL:
        low = raw.strip().lower()
        if low in ("1", "true", "on", "yes", "y"):
            return True
        if low in ("0", "false", "off", "no", "n"):
            return False
        raise ValueError("expected on / off")
    if setting.kind == SettingKind.NUMBER:
        value = float(raw)
        if setting.minimum is not None and value < setting.minimum:
            raise ValueError(f"minimum is {setting.minimum:g}")
        return int(value) if value.is_integer() and setting.path in INTEGER_KEYS else value
    if setting.kind == SettingKind.CHOICE:
        low = raw.strip().lower()
        # Choices match case-insensitively but keep their own spelling (strftime formats).
        matched = next((c for c in setting.choices if c.lower() == low), None)
        if setting.choices and matched is None:
            raise ValueError("one of " + ", ".join(setting.choices))
        if setting.path == "kubernetes.enabled":
            return {"true": True, "false": False}.get(low, low)
        return matched if matched is not None else low
    if setting.kind == SettingKind.MULTI:
        items = [c.strip().lower() for c in raw.split(",") if c.strip()]
        unknown = [c for c in items if c not in setting.choices]
        if unknown:
            raise ValueError(
                "unknown: " + ", ".join(unknown) + "; one of " + ", ".join(setting.choices)
            )
        if not items:
            raise ValueError("pick at least one of " + ", ".join(setting.choices))
        return [c for c in setting.choices if c in items]
    if setting.kind == SettingKind.COLUMNS:
        cols = [c.strip().lower() for c in raw.split(",") if c.strip()]
        unknown = [c for c in cols if c not in ALL_COLUMNS]
        if unknown:
            raise ValueError("unknown column(s): " + ", ".join(unknown))
        if not cols:
            raise ValueError("at least one column")
        return cols
    return raw.strip() or None
