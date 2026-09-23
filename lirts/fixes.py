"""Smart fix: the one action an insight proposes, run with one key after a confirmation.

An insight may carry an ``action`` string (``kill:<pid>``, ``restart-container``,
``stop-container``, ``restart-process``, ``logs``, ``stop-forward``).  :func:`fix_for` turns
the worst such insight of a row into a :class:`Fix` that says exactly what will happen; the
dashboard shows it and runs it only when the user confirms.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from lirts.models import Insight, Listener


class FixKind(StrEnum):
    """What a fix does."""

    KILL = "kill"
    RESTART_CONTAINER = "restart-container"
    STOP_CONTAINER = "stop-container"
    RESTART_PROCESS = "restart-process"
    LOGS = "logs"
    STOP_FORWARD = "stop-forward"


@dataclass(frozen=True)
class Fix:
    """A concrete, described fix for one row."""

    kind: FixKind
    listener: Listener
    insight: Insight
    pid: int | None = None

    @property
    def command(self) -> str:
        """What will run, in words a confirmation can show."""
        c = self.listener.container
        if self.kind == FixKind.KILL:
            name = next((p.name for p in self.listener.processes if p.pid == self.pid), "process")
            return f"send SIGTERM to {name} (PID {self.pid})"
        if self.kind == FixKind.RESTART_CONTAINER and c is not None:
            return f"docker restart {c.name}"
        if self.kind == FixKind.STOP_CONTAINER and c is not None:
            return f"docker stop {c.name}"
        if self.kind == FixKind.RESTART_PROCESS:
            return f"terminate PID {self.listener.pid} and run its command again in its directory"
        if self.kind == FixKind.LOGS and c is not None:
            return f"show the logs of {c.name}"
        if self.kind == FixKind.STOP_FORWARD:
            return f"stop the port-forward on :{self.listener.port}"
        return self.kind

    @property
    def title(self) -> str:
        return f"Fix for {self.listener.identity.service} on {self.listener.key}"


def parse_action(action: str, listener: Listener) -> Fix | None:
    """The fix an ``action`` string means for ``listener``; None when it cannot apply."""
    insight = next((i for i in listener.insights if i.action == action), None)
    if insight is None:
        return None
    if action.startswith("kill:"):
        try:
            pid = int(action.split(":", 1)[1])
        except ValueError:
            return None
        if pid not in listener.pids:
            return None
        return Fix(FixKind.KILL, listener, insight, pid=pid)
    if action == FixKind.RESTART_CONTAINER and listener.container is not None:
        return Fix(FixKind.RESTART_CONTAINER, listener, insight)
    if action == FixKind.STOP_CONTAINER and listener.container is not None:
        return Fix(FixKind.STOP_CONTAINER, listener, insight)
    if action == FixKind.RESTART_PROCESS and listener.pid and not listener.container:
        return Fix(FixKind.RESTART_PROCESS, listener, insight, pid=listener.pid)
    if action == FixKind.LOGS and listener.container is not None:
        return Fix(FixKind.LOGS, listener, insight)
    if action == FixKind.STOP_FORWARD and listener.tunnel:
        return Fix(FixKind.STOP_FORWARD, listener, insight)
    return None


def fix_for(listener: Listener) -> Fix | None:
    """The fix of the row's worst insight that proposes one, or None."""
    for insight in sorted(listener.insights, key=lambda i: -i.rank):
        if insight.action:
            fix = parse_action(insight.action, listener)
            if fix is not None:
                return fix
    return None
