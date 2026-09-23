"""Registry of user-facing settings for the Settings screen and `lirts config set`."""

from __future__ import annotations

from lirts.config_defaults import MINIMUMS, SOURCE_NAMES
from lirts.settings_schema import (
    ALL_COLUMNS,
    SETTING_TABS,
    Setting,
    SettingKind,
    coerce,
    format_value,
    get_path,
    set_path,
    tab_for,
)

__all__ = [
    "ALL_COLUMNS",
    "SETTINGS",
    "SETTING_TABS",
    "STYLE_NAMES",
    "Setting",
    "SettingKind",
    "coerce",
    "format_value",
    "get_path",
    "set_path",
    "setting_for",
    "settings_on_tab",
    "tab_for",
]


STYLE_NAMES: tuple[str, ...] = ("classic", "compact", "tight", "cracktro", "phosphor")
LAYOUT_NAMES: tuple[str, ...] = ("default", "containers", "hosts")
GLYPH_SET_NAMES: tuple[str, ...] = ("block", "braille", "demoscene")
CLOCK_FORMATS: tuple[str, ...] = ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%Y-%m-%d %H:%M:%S")


SETTINGS: list[Setting] = [
    Setting(
        "theme",
        "Theme",
        SettingKind.CHOICE,
        "Colour theme; Enter opens the list and previews while you move",
        choices=(),
    ),
    Setting(
        "ui.style",
        "Style",
        SettingKind.CHOICE,
        "Borders, density, glyph set, scroller and panel set as one bundle (T cycles); colours stay the theme's",
        choices=STYLE_NAMES,
    ),
    Setting(
        "ui.layout",
        "Layout",
        SettingKind.CHOICE,
        "Columns, side panel sections, which panels and where they go: default, containers, hosts (U cycles)",
        choices=LAYOUT_NAMES,
    ),
    Setting(
        "ui.glyphs",
        "Glyph set",
        SettingKind.CHOICE,
        "Sparklines, bars and row icons: block, braille or demoscene shades",
        choices=GLYPH_SET_NAMES,
    ),
    Setting(
        "ui.rounded_corners", "Rounded corners", SettingKind.BOOL, "Round or square panel borders"
    ),
    Setting(
        "ui.row_icons",
        "Row icons",
        SettingKind.BOOL,
        "Role icon before the service name, source icon in SRC",
    ),
    Setting(
        "ui.history_panel",
        "History panel",
        SettingKind.BOOL,
        "Sparklines of the selected row under the table (Y)",
    ),
    Setting(
        "ui.clock",
        "Clock format",
        SettingKind.CHOICE,
        "Top bar clock and event times (strftime)",
        choices=CLOCK_FORMATS,
    ),
    Setting(
        "ui.truecolor",
        "Truecolor",
        SettingKind.CHOICE,
        "auto lets the terminal decide; on forces 24-bit colour, off 256 colours. Needs a restart",
        choices=("auto", "on", "off"),
        live=False,
    ),
    Setting(
        "cli.force_color",
        "Colour when piped",
        SettingKind.BOOL,
        "Keep colours when lirts list / explain output goes to a pipe",
        tab="General",
    ),
    Setting(
        "refresh_interval",
        "Refresh interval (s)",
        SettingKind.NUMBER,
        "Seconds between refreshes",
        minimum=MINIMUMS["refresh_interval"],
    ),
    Setting(
        "sources",
        "Sources",
        SettingKind.MULTI,
        "Which sources appear: local ports, Docker containers, ssh sessions, Kubernetes",
        choices=SOURCE_NAMES,
    ),
    Setting("columns", "Columns", SettingKind.COLUMNS, "Which columns the table shows, in order"),
    Setting(
        "editor",
        "Editor",
        SettingKind.TEXT,
        "Command that O opens the project folder with (cursor, code, idea…); empty = file manager",
    ),
    Setting(
        "group_by_stack",
        "Group by stack",
        SettingKind.BOOL,
        "Collapsible headers per compose stack / project (G)",
    ),
    Setting(
        "side_panel", "Side panel", SettingKind.BOOL, "Show the details panel on the right (p)"
    ),
    Setting(
        "net_panel",
        "Traffic panel",
        SettingKind.BOOL,
        "btop-style net box under the table: whole machine or the selected row (N); off = nothing sampled for it",
    ),
    Setting("show_udp", "Show UDP", SettingKind.BOOL, "Include bound UDP sockets (u)"),
    Setting(
        "hide_system",
        "Hide system services",
        SettingKind.BOOL,
        "Hide OS daemons such as mDNS or rapportd (h)",
    ),
    Setting(
        "insights.highlight_new_minutes",
        "Highlight new (min)",
        SettingKind.NUMBER,
        "Cyan ✚ for services started within this many minutes; 0 = off",
        minimum=MINIMUMS["insights.highlight_new_minutes"],
    ),
    Setting(
        "insights.show_stopped_minutes",
        "Keep stopped rows (min)",
        SettingKind.NUMBER,
        "Dimmed STOPPED rows stay this long; 0 = off",
        minimum=MINIMUMS["insights.show_stopped_minutes"],
    ),
    Setting(
        "insights.pattern_days",
        "Remember patterns (days)",
        SettingKind.NUMBER,
        "Recurring issues (conflicts, restarts, kills) are counted this long; 0 = off",
        minimum=MINIMUMS["insights.pattern_days"],
    ),
    Setting(
        "topology.days",
        "Star map memory (days)",
        SettingKind.NUMBER,
        "The map (M) keeps stars and lines it has seen this long",
        minimum=MINIMUMS["topology.days"],
    ),
    Setting(
        "topology.usual_days",
        "Usual relation after (days)",
        SettingKind.NUMBER,
        "A line seen on this many different days stays on the map while idle",
        minimum=MINIMUMS["topology.usual_days"],
    ),
    Setting(
        "insights.stale_after_hours",
        "Stale after (h)",
        SettingKind.NUMBER,
        "Idle dev server with no connections is stale after this",
        minimum=MINIMUMS["insights.stale_after_hours"],
    ),
    Setting(
        "insights.restart_warning",
        "Restart warning (per hour)",
        SettingKind.NUMBER,
        "Restarts within an hour that trigger a warning",
        minimum=MINIMUMS["insights.restart_warning"],
    ),
    Setting(
        "thresholds.cpu.yellow",
        "CPU yellow (%)",
        SettingKind.NUMBER,
        "CPU percentage coloured yellow",
        minimum=MINIMUMS["thresholds.cpu.yellow"],
    ),
    Setting(
        "thresholds.cpu.red",
        "CPU red (%)",
        SettingKind.NUMBER,
        "CPU percentage coloured red / warned about",
        minimum=MINIMUMS["thresholds.cpu.red"],
    ),
    Setting(
        "thresholds.memory_mb.yellow",
        "Memory yellow (MB)",
        SettingKind.NUMBER,
        "Memory coloured yellow",
        minimum=MINIMUMS["thresholds.memory_mb.yellow"],
    ),
    Setting(
        "thresholds.memory_mb.red",
        "Memory red (MB)",
        SettingKind.NUMBER,
        "Memory coloured red / warned about",
        minimum=MINIMUMS["thresholds.memory_mb.red"],
    ),
    Setting(
        "http_probe.enabled",
        "HTTP probes",
        SettingKind.BOOL,
        "Silent HEAD/GET probes for status, latency and headers",
    ),
    Setting(
        "http_probe.interval",
        "Probe interval (s)",
        SettingKind.NUMBER,
        "Seconds between probes of the same port",
        minimum=MINIMUMS["http_probe.interval"],
    ),
    Setting(
        "proxies.enabled",
        "Proxy configs",
        SettingKind.BOOL,
        "Read virtual hosts and upstreams from nginx -T / httpd -S at start",
    ),
    Setting("docker.enabled", "Docker", SettingKind.BOOL, "Docker integration"),
    Setting(
        "docker.show_unpublished",
        "Show unpublished containers",
        SettingKind.BOOL,
        "Rows for containers without published ports",
    ),
    Setting(
        "kubernetes.enabled",
        "Kubernetes",
        SettingKind.CHOICE,
        "auto = when kubectl and a context exist",
        choices=("auto", "true", "false"),
    ),
    Setting(
        "kubernetes.namespace",
        "Kubernetes namespace",
        SettingKind.TEXT,
        "Empty = the context's namespace",
    ),
    Setting(
        "kubernetes.all_namespaces",
        "All namespaces",
        SettingKind.BOOL,
        "Watch every namespace instead of one",
    ),
    Setting(
        "bandwidth.mode",
        "Per-process bandwidth",
        SettingKind.CHOICE,
        "macOS nettop sampling (needs restart)",
        choices=("auto", "off"),
        live=False,
    ),
    Setting(
        "bandwidth.connections",
        "Per-connection traffic",
        SettingKind.BOOL,
        "Rates per client → port link on the w map; one more command per sample (off by default)",
    ),
    Setting(
        "history.persist",
        "Persist history",
        SettingKind.BOOL,
        "Keep history and events across runs (needs restart)",
        live=False,
    ),
]


def setting_for(path: str) -> Setting | None:
    """The registry entry for a dotted key, or None when the key is not a setting."""
    return next((s for s in SETTINGS if s.path == path), None)


def settings_on_tab(tab: str) -> list[Setting]:
    """The settings shown on a Settings screen tab, in registry order."""
    return [s for s in SETTINGS if tab_for(s) == tab]
