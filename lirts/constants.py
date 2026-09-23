"""Fixed values that are used in more than one place.

Everything the user may tune lives in the config (see :mod:`lirts.config_defaults`); this
module holds what is fixed by design: timeouts of the commands lirts runs, history sizes,
notification durations, glyphs.  One definition each, imported where needed.
"""

from __future__ import annotations

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
SECONDS_PER_DAY = 86400

# ----- engine, history, insights ------------------------------------------------------------

# Samples kept per port in the rolling history (one per refresh).
HISTORY_WINDOW = 120
# Events kept in memory and in history.json.
MAX_EVENTS = 300
# Restarts are counted over this window for the "restarted N times" insight.
RESTART_WINDOW_SECONDS = SECONDS_PER_HOUR
# The refresh interval never goes below this, whatever the config or flags say.
MIN_REFRESH_INTERVAL = 0.5
# Machine-wide CPU / memory / throughput samples kept for the top-bar sparklines.
SYSTEM_HISTORY_SAMPLES = 60
# Per-connection rate samples kept for an edge's traffic sparkline.
EDGE_RATE_SAMPLES = 60
# Longest gap counted when adding observed bytes to a row's running total.
MAX_ACCUMULATION_GAP = 60.0
# Seconds between throttled writes of history.json and patterns.json.
STATE_SAVE_INTERVAL = 30.0
# Events written to history.json, and how many of them are read back on start.
EVENTS_SAVED = 100
EVENTS_RELOADED = 50
# Restart timestamps and error messages kept per history entry when saving.
ENTRY_RESTARTS_SAVED = 50
ENTRY_ERRORS_SAVED = 20
# Service names a history entry remembers per port; the rarest is forgotten past this.
ENTRY_SERVICES_KEPT = 8
# What "usually runs on this port" means: seen on this many different days, or — while the
# learned store is still younger than a day — seen in more than this many refreshes.
PORT_USUAL_MIN_DAYS = 2
PORT_USUAL_MIN_REFRESHES = 5
# Other projects named in the "also used by" insight before it says "and N more".
PORT_ALSO_USED_SHOWN = 1
# Events carried in a Snapshot for the event stream in the UI.
SNAPSHOT_EVENTS = 12
# How far back the event-log analysis looks by default.
EVENT_WINDOW_HOURS = 24.0
# De-duplication set of ingested events: trimmed to KEEP once it passes LIMIT.
PATTERN_SEEN_LIMIT = 5000
PATTERN_SEEN_KEEP = 2000
# Service names and sample messages remembered per pattern.
PATTERN_SERVICES_KEPT = 6
PATTERN_SAMPLES_KEPT = 3
# A conflict or failure counted this many times over the reporting minimum is an error,
# not a warning.
PERSISTENT_PATTERN_FACTOR = 2
# How long a kill waits for the processes to disappear before reporting them alive.
KILL_WAIT_SECONDS = 3.0
# A restart may wait longer, because the process is started again afterwards.
RESTART_KILL_WAIT_SECONDS = 5.0
# The primary interface address is looked up again at most this often.
ADDRESS_CACHE_SECONDS = 60
# Ports whose rows also show the hosts-file names that resolve to this machine.
HOST_NAME_PORTS = (80, 443, 8080, 8443)
# Port assumed for an ssh session whose remote address carries no port.
SSH_DEFAULT_PORT = 22
# Ports tried as HTTPS before plain HTTP, and the ports a URL leaves out.
TLS_PORTS = (443, 8443, 4443)
STANDARD_WEB_PORTS = (80, 443)
# Heat score (0..1) at which a listener counts as hot, and as active.
ACTIVITY_HOT_SCORE = 0.6
ACTIVITY_ACTIVE_SCORE = 0.2
# Processes named in a port-conflict message before the list is cut short.
CONFLICT_PROCESSES_SHOWN = 4
# Links followed when a proxy chain is rendered.
PROXY_CHAIN_DEPTH = 4
# Identity confidence from which a failed HTTP probe is reported as a problem.
UNREACHABLE_MIN_CONFIDENCE = 0.5
# Client processes and shared ports named in a blast-radius line before it is cut short.
BLAST_CLIENTS_SHOWN = 4
BLAST_PORTS_SHOWN = 8
# Times the same thing must happen in the window before it is reported as recurring.
RECURRING_MIN_EVENTS = 2
# Services per role and proxy routes listed in the machine summary before "and N more".
EXPLAIN_SERVICES_PER_ROLE = 5
EXPLAIN_ROUTES_SHOWN = 12
# Processes of one port the identity engine matches against its rules.
IDENTITY_PROCESSES_CHECKED = 3
# How close a recorded restart timestamp must be to an event to be the same thing.
RESTART_MATCH_TOLERANCE = 2.0
# Shortest pause between two recorded frames, however short the interval is.
RECORD_MIN_SLEEP = 0.2

# ----- commands lirts runs (seconds before giving up) ---------------------------------------

DOCKER_CLIENT_TIMEOUT = 3
DOCKER_CLI_TIMEOUT = 5
DOCKER_STOP_TIMEOUT = 10
DOCKER_ACTION_TIMEOUT = 30
# The Docker socket is not retried more often than this after a failed connect.
DOCKER_CLIENT_RETRY_SECONDS = 10
# Containers whose stats are sampled in parallel.
DOCKER_STATS_WORKERS = 8
KUBECTL_CONFIG_TIMEOUT = 5
KUBECTL_READY_TIMEOUT = 8
KUBECTL_SETTLE_SECONDS = 0.8
# nginx -T / httpd -S when the proxy routes are discovered.
PROXY_COMMAND_TIMEOUT = 5.0
# Grace added to the sampling interval before nettop / ss is given up on.
BANDWIDTH_COMMAND_GRACE = 10
REACH_TIMEOUT = 3
PING_TIMEOUT = 2.0
ORIGIN_TIMEOUT = 2
# A working directory's git branch is re-read at most this often.
ORIGIN_BRANCH_CACHE_SECONDS = 30
NOTIFY_TIMEOUT = 5

# ----- TUI ------------------------------------------------------------------------------------

# Toast durations in seconds, from a confirmation the user expected to a notice shown once.
TOAST_SHORT = 2
TOAST_NORMAL = 3
TOAST_DETAIL = 5
TOAST_LONG = 8
TOAST_ONCE = 20

# Block glyphs from empty to full; the sparkline uses the whole ramp.
SPARK_CHARS = " ▁▂▃▄▅▆▇█"
# The same ramp without the full block: the traffic graph draws full cells itself.
PARTIAL_BLOCKS = SPARK_CHARS[:-1]
BAR_FULL = "█"
BAR_EMPTY = "░"

# Glyphs shown in the table, the panels and the screens.
GLYPH_ERROR = "✖"
GLYPH_WARNING = "⚠"
GLYPH_OK = "✓"
GLYPH_INFO = "•"
GLYPH_STATUS = "●"
GLYPH_STATUS_UNKNOWN = "○"
GLYPH_NEW = "✚"
GLYPH_SSH = "⇄"
GLYPH_RESTART = "↻"
GLYPH_RECURRING = "⟳"
GLYPH_COLLAPSED = "▸"
GLYPH_EXPANDED = "▾"

# Sparkline widths: one table cell, the top bar, and a full-width row on a screen.
SPARK_WIDTH_CELL = 10
SPARK_WIDTH_TOPBAR = 14
SPARK_WIDTH_SCREEN = 60

# The side panel hides itself below this terminal width, whatever the setting says.
SIDE_PANEL_MIN_TERMINAL_WIDTH = 140
# Seconds between checks of the config file's mtime.
CONFIG_WATCH_INTERVAL = 3.0
# Seconds between reloads while a log screen is following.
LOG_FOLLOW_INTERVAL = 2.0
# Log lines a screen asks for: a container, a whole compose stack, a pod.
LOG_TAIL_CONTAINER = 300
LOG_TAIL_STACK = 100
LOG_TAIL_POD = 200

# ----- CLI -----------------------------------------------------------------------------------

# Width Rich is given when the output is piped, so scripts see the full table.
PIPED_CONSOLE_WIDTH = 220
# Pause between the two samples a CPU percentage needs.
CPU_SAMPLE_GAP_SECONDS = 0.6
# Service names listed per remembered pattern in `lirts patterns`.
PATTERN_SERVICES_SHOWN = 4
# Pod names printed when a name prefix matches more than one pod.
AMBIGUOUS_POD_NAMES_SHOWN = 6
