"""The configuration schema: the default values and the current schema version."""

from __future__ import annotations

from typing import Any

from lirts.constants import MIN_REFRESH_INTERVAL

# Bumped whenever keys are removed or renamed (added keys just take their defaults).  A file
# with an older schema still
# loads: keys that no longer exist are ignored (never applied) and reported; the file itself
# is only rewritten when the user asks (Settings `s`, `lirts config upgrade`).
CONFIG_VERSION = 3

# The sources a row can come from: the only values `sources` accepts.
SOURCE_NAMES: tuple[str, ...] = ("local", "docker", "ssh", "kubernetes")

DEFAULT_CONFIG: dict[str, Any] = {
    # Seconds between refreshes of the main table.
    "refresh_interval": 2.0,
    # Textual theme name (see `lirts config themes`).
    "theme": "dracula",
    # Show UDP sockets that are bound but have no peer (DNS, mDNS, ...).
    "show_udp": False,
    # Which sources appear in the table: local, docker, ssh, kubernetes (W runs the setup wizard).
    "sources": list(SOURCE_NAMES),
    # Hide well-known macOS/Linux system daemons (rapportd, mDNSResponder, ...).
    "hide_system": False,
    # Show the side panel on start.
    "side_panel": True,
    # Traffic panel under the table (own address, download / upload history, N switches scope).
    "net_panel": True,
    # Group rows under collapsible compose-stack / project headers (toggle with G).
    "group_by_stack": False,
    # Last chosen sort column and direction (remembered when the config file exists).
    "sort_by": "port",
    "sort_desc": False,
    # Columns of the main table, in order. Available: port, proto, state, src, service,
    # identity, process, pids, cpu, mem, conns, activity, bandwidth, uptime, project,
    # container, stack, http, latency, health, origin, status, trend.
    "columns": [
        "port",
        "state",
        "src",
        "project",
        "service",
        "process",
        "pids",
        "cpu",
        "mem",
        "conns",
        "activity",
        "uptime",
        "status",
    ],
    # Custom service names. Keys may be a port number or a substring matched
    # against the process name, command line, container name or image.
    "aliases": {},
    # Editor command for O (open the project folder), e.g. "cursor", "code", "idea".
    # Empty = the desktop file manager (open / xdg-open).
    "editor": "",
    # Resource thresholds used for colouring and warnings.
    "thresholds": {
        "cpu": {"yellow": 40.0, "red": 80.0},
        "memory_mb": {"yellow": 500.0, "red": 1000.0},
        "latency_ms": {"yellow": 500.0, "red": 1500.0},
    },
    # Environment variable names (substring, case-insensitive) masked in the UI.
    "mask_env_vars": ["SECRET", "PASSWORD", "PASSWD", "TOKEN", "API_KEY", "PRIVATE", "CREDENTIAL"],
    "http_probe": {
        "enabled": True,
        # Seconds between probes of the same port.
        "interval": 10.0,
        "timeout": 1.0,
        # Ports never probed (well-known non-HTTP services).
        "skip_ports": [
            22,
            25,
            53,
            110,
            143,
            465,
            587,
            993,
            995,
            1883,
            3306,
            5432,
            5433,
            6379,
            6380,
            27017,
            11211,
            9092,
            2181,
            5672,
            1433,
            1521,
            3389,
            5900,
            5353,
            631,
            445,
            139,
            1025,
            4317,
        ],
        # Extra ports to probe even if they are in skip_ports.
        "force_ports": [],
    },
    "health": {
        # Health checks run only on demand (H in the dashboard, `lirts health`); there is no
        # background checking.  TCP connect + learned HTTP health endpoint.
        "timeout": 1.5,
        # Conventional health paths tried once per HTTP service; the first 2xx/3xx is remembered.
        "paths": [
            "/health",
            "/healthz",
            "/readyz",
            "/livez",
            "/api/health",
            "/api/healthz",
            "/-/health",
            "/-/ready",
            "/actuator/health",
            "/status",
            "/ping",
            "/up",
        ],
        # Per-port health path, e.g. {8080: "/internal/status"}.
        "overrides": {},
    },
    "proxies": {
        # Ask local nginx / httpd for their virtual hosts (nginx -T, httpd -S) at start.
        "enabled": True,
    },
    "docker": {
        "enabled": True,
        # Also list containers whose ports are not published to the host.
        "show_unpublished": False,
        # Seconds between full `docker inspect` refreshes per container.
        "inspect_interval": 15.0,
        # Sample per-container CPU / memory / network via the stats API.
        "stats": True,
    },
    "kubernetes": {
        # "auto" enables when kubectl and a current context exist; true / false force it.
        "enabled": "auto",
        # Context and namespace to use (null = the kubeconfig's current ones).
        "context": None,
        "namespace": None,
        "all_namespaces": False,
        # Seconds between background refreshes of pods / deployments / services.
        "interval": 30.0,
        # Seconds before a kubectl call is abandoned.
        "timeout": 10.0,
    },
    "bandwidth": {
        # "auto" uses macOS `nettop` when available; "off" disables per-process rates.
        "mode": "auto",
        # Seconds between samples (nettop itself needs ~5s per run).
        "interval": 5.0,
        # Per-connection traffic on the who-talks-to-whom map (w): one more command per sample
        # (nettop -t loopback on macOS, ss -ti on Linux). Off by default.
        "connections": False,
    },
    "history": {
        # Persist history and the last snapshot across runs.
        "persist": True,
        # Number of samples kept per port (one per refresh).
        "window": 120,
        # Entries not seen for this many hours are dropped on load.
        "retention_hours": 24,
    },
    "insights": {
        # A process idle this long with no connections is considered stale.
        "stale_after_hours": 6.0,
        # Restarts within the last hour that trigger a warning.
        "restart_warning": 3,
        # Keep a dimmed "STOPPED" row for this many minutes after a port disappears (0 = off).
        "show_stopped_minutes": 5,
        # Highlight services that started within this many minutes (0 = off).
        "highlight_new_minutes": 3,
        # Remember recurring issues (conflicts, restarts, kills) for this many days (0 = off).
        "pattern_days": 30,
        # A pattern is reported once the same thing happened this many times.
        "pattern_min": 3,
    },
    "ui": {
        # Style: classic, compact, tight, cracktro, phosphor (T cycles them): borders, density,
        # glyphs, scroller and panel set. Colours are the theme's business, not the style's.
        "style": "classic",
        # Layout: default, containers, hosts (U cycles them): columns, side panel sections,
        # which panels and where they go.
        "layout": "default",
        # Glyphs for sparklines, bars and row icons: block, braille, demoscene.
        "glyphs": "block",
        # Round or square panel corners.
        "rounded_corners": True,
        # Colour depth: auto (the terminal decides), on (force 24-bit), off (256 colours). Needs a restart.
        "truecolor": "auto",
        # strftime format of the clock in the top bar and of event / history times.
        "clock": "%H:%M:%S",
        # History panel under the table (sparklines of the selected row); Y toggles it.
        "history_panel": False,
        # Icons before the service name and in the SRC column.
        "row_icons": True,
    },
    "cli": {
        # Keep colours when the output of lirts list / explain / ... is piped.
        "force_color": False,
    },
    "topology": {
        # The star map (M) remembers stars and lines it has seen for this many days.
        "days": 14,
        # A relation seen on this many different days is "usual" and stays on the map while idle.
        "usual_days": 3,
    },
}


# The lowest value each tunable number may take.  Loading pulls a smaller value in the file
# back up to it and the Settings screen refuses one below it, so the bound is written once.
MINIMUMS: dict[str, float] = {
    "refresh_interval": MIN_REFRESH_INTERVAL,
    "http_probe.interval": 1.0,
    "http_probe.timeout": 0.1,
    "history.window": 10,
    "history.retention_hours": 0,
    "insights.stale_after_hours": 0.1,
    "insights.restart_warning": 1,
    "insights.show_stopped_minutes": 0,
    "insights.highlight_new_minutes": 0,
    "insights.pattern_days": 0,
    "insights.pattern_min": 2,
    "topology.days": 1,
    "topology.usual_days": 1,
    "thresholds.cpu.yellow": 0,
    "thresholds.cpu.red": 0,
    "thresholds.memory_mb.yellow": 0,
    "thresholds.memory_mb.red": 0,
}

# Keys whose value is a whole number, wherever it is read or written.
INTEGER_KEYS: frozenset[str] = frozenset(
    {
        "history.window",
        "insights.restart_warning",
        "insights.pattern_days",
        "insights.pattern_min",
        "topology.days",
        "topology.usual_days",
    }
)


# Default column lists of earlier releases; configs still carrying one are upgraded.
_LEGACY_COLUMN_SETS: list[list[str]] = [
    [
        "port",
        "state",
        "src",
        "service",
        "process",
        "pids",
        "cpu",
        "mem",
        "conns",
        "activity",
        "uptime",
        "status",
    ],
]


# Sections whose keys are user-defined rather than part of the schema.
_FREE_FORM_SECTIONS = {"aliases", "health.overrides"}
