"""The command palette: every lirts action, searchable from Textual's palette."""

from __future__ import annotations

from collections.abc import Iterable
from functools import partial
from typing import TYPE_CHECKING, cast

from textual.binding import Binding, BindingType
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.keys import key_to_character

from lirts.filtering import FIELDS

if TYPE_CHECKING:
    from lirts.tui.app import LirtsApp


PALETTE_COMMANDS: list[tuple[str, str, str]] = [
    # (name, help, action); the key that runs the action is read from BINDINGS, never repeated here
    ("Details", "Open the extended details of the current row", "show_details"),
    (
        "Kill process",
        "Kill the marked or current process(es) with a blast-radius preview",
        "kill",
    ),
    ("Restart", "Restart the container, or re-run a local process", "restart"),
    ("Stop container", "Stop the marked or current container(s)", "docker_stop"),
    ("Container logs", "Show the container's logs", "docker_logs"),
    ("Container shell", "Open an interactive shell in the container", "docker_exec"),
    ("Open in browser", "Open the service URL", "open_browser"),
    ("Open project folder", "Open the project's folder", "open_project"),
    ("Copy URL / exec command", "Copy to the clipboard", "copy"),
    ("Fix", "Run the fix the current row's insight proposes", "fix"),
    ("Stacks", "Compose stacks: restart, stop, logs, folder", "stacks"),
    ("Group by stack", "Toggle the tree view by stack / project", "toggle_grouping"),
    ("Kubernetes", "Pods, services and port-forwards", "kubernetes"),
    ("Events", "Notification centre with ongoing / recurring issues", "events"),
    (
        "Who talks to whom",
        "Local clients per port, proxy chains, tunnels, ssh sessions",
        "graph",
    ),
    ("Reachability", "DNS, ping and TCP connect for the ssh host under the cursor", "reach"),
    ("Setup wizard", "Walk through every setting step by step and save", "setup"),
    (
        "Check health now",
        "One-off TCP + health-endpoint check of the visible rows, with a results screen",
        "check_health",
    ),
    ("Explain my machine", "Summary, inferred stack, warnings", "explain"),
    ("Problems only", "Show only rows with warnings or errors", "toggle_problems"),
    ("Mark all", "Mark every visible row", "mark_all"),
    ("Sort", "Cycle the sort column", "sort"),
    ("Reverse sort", "Reverse the sort direction", "sort_reverse"),
    ("Toggle UDP", "Show or hide UDP sockets", "toggle_udp"),
    ("Toggle system services", "Show or hide OS daemons", "toggle_system"),
    ("Toggle side panel", "Show or hide the details panel", "toggle_side"),
    ("Traffic panel scope", "Whole machine or the selected row in the net box", "net_scope"),
    ("Theme", "Cycle the colour theme", "cycle_theme"),
    ("Style", "Cycle the style: borders, density, glyph set, scroller and panels", "cycle_style"),
    (
        "Layout",
        "Cycle the layout: columns, side panel sections, panels and where they sit",
        "cycle_layout",
    ),
    ("History panel", "Sparklines of the selected row under the table", "toggle_history"),
    ("Menu", "Settings, help, star map or quit; Esc with nothing to clear", "menu"),
    ("Settings", "Version, config file and every option", "settings"),
    ("Refresh", "Collect everything again now", "refresh"),
    ("Help", "Keyboard shortcuts", "help"),
    ("Quit", "Exit lirts", "quit"),
]


def _key_display(keys: str) -> str:
    """The first of a binding's keys, written the way the user presses it."""
    first = keys.split(",")[0]
    character = key_to_character(first)
    if character and character.isprintable() and not character.isspace():
        return character
    return first


def key_hints(bindings: Iterable[BindingType]) -> dict[str, str]:
    """Map every action in `bindings` to the key that runs it, as the footer would show it."""
    hints: dict[str, str] = {}
    shown: set[str] = set()
    for binding in bindings:
        if isinstance(binding, Binding):
            keys, action, visible = binding.key, binding.action, binding.show
        else:
            keys, action, visible = binding[0], binding[1], True
        if not action or (action in hints and (action in shown or not visible)):
            continue
        hints[action] = _key_display(keys)
        if visible:
            shown.add(action)
    return hints


def with_key(description: str, key: str) -> str:
    """The command's help with the key that runs it, when there is one."""
    return f"{description} ({key})" if key else description


class LirtsCommands(Provider):
    """Every lirts action, searchable from Textual's command palette (Ctrl+P)."""

    async def discover(self) -> Hits:
        """Every lirts action, listed before the user types anything."""
        app = self.app
        keys = key_hints(app.BINDINGS)
        for name, help_text, action in PALETTE_COMMANDS:
            yield DiscoveryHit(
                name,
                partial(app.run_action, action),
                help=with_key(help_text, keys.get(action, "")),
            )

    async def search(self, query: str) -> Hits:
        """Actions matching `query`, followed by the filter fields it could mean."""
        matcher = self.matcher(query)
        app = cast("LirtsApp", self.app)
        keys = key_hints(app.BINDINGS)
        for name, help_text, action in PALETTE_COMMANDS:
            score = matcher.match(f"{name} {help_text}")
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(name),
                    partial(app.run_action, action),
                    help=with_key(help_text, keys.get(action, "")),
                )
        low = query.lower().strip()
        for field, meaning in FIELDS.items():
            if low and (field.startswith(low) or low in meaning):
                yield Hit(
                    0.5,
                    f"filter {field}:…",
                    partial(app.set_filter, f"{field}:"),
                    help=f"Filter rows by {field}: {meaning}",
                )
