"""Insights about who holds a port: the usual holder, leftovers and shared ports.

These three rules are the ones that answer "why can I not start my server on this port?".
They all read :class:`lirts.ports.PortMemory`, which is folded out of what lirts has already
seen; when the memory is empty (a first run) none of them say anything.  Called from
:func:`lirts.insights_analyse.analyse`.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import psutil

from lirts.constants import PORT_ALSO_USED_SHOWN
from lirts.fixes import FixKind
from lirts.identity import Role
from lirts.models import Insight, Level, Listener, ListenerState, is_docker_proxy_name
from lirts.ports import PortMemory, listener_holder, parent_is_gone

# Roles whose process is a development server someone started, not a system service.
LEFTOVER_ROLES = {Role.FRONTEND, Role.BACKEND, Role.TOOL}
# Compose calls containers without a project "-"; those are not a stack that can be gone.
UNNAMED_STACK = "-"


def port_memory_insights(
    listener: Listener,
    *,
    memory: PortMemory,
    now: float,
    alive: Callable[[int], bool] = psutil.pid_exists,
) -> None:
    """Add the squatter, orphaned and shared-project insights to ``listener``.

    ``alive`` is the process-liveness test the orphan rule uses; tests pass a fake
    process table instead of the real one.
    """
    if listener.state == ListenerState.STOPPED:
        return
    _squatter(listener, memory=memory, now=now)
    _orphaned(listener, memory=memory, alive=alive)
    _shared_projects(listener, memory=memory)


def _holder_action(listener: Listener) -> str | None:
    """How the user would hand the port back: kill the process, or stop the container."""
    local = next((p for p in listener.processes if not is_docker_proxy_name(p.name)), None)
    if local is not None:
        return f"kill:{local.pid}"
    if listener.container is not None:
        return FixKind.STOP_CONTAINER
    return None


def _since_text(listener: Listener, now: float) -> str:
    """The ``since 09:12`` tail of a message, or an empty string when the start is unknown."""
    uptime = listener.uptime_seconds
    started = now - uptime if uptime is not None else listener.first_seen
    if not started:
        return ""
    return f" since {time.strftime('%H:%M', time.localtime(started))}"


def _squatter(listener: Listener, *, memory: PortMemory, now: float) -> None:
    """Somebody else is on the port that another project usually has."""
    usual = memory.usual(listener.port)
    if usual is None:
        return
    service, project = listener_holder(listener)
    if not service or service == "Unknown":
        return
    if usual.service == service and usual.project == project:
        return
    here = f"{service} ({project})" if project else service
    listener.add_insight(
        Insight(
            Level.WARNING,
            "squatter",
            f"{listener.port} is usually {usual.label}; now {here}{_since_text(listener, now)}",
            f"check whether {usual.label} failed to start",
            _holder_action(listener),
        )
    )


def leftover_reason(
    listener: Listener,
    *,
    memory: PortMemory,
    alive: Callable[[int], bool] = psutil.pid_exists,
) -> str | None:
    """Why the holder looks left over, in a few words, or None when it does not.

    A container is left over when it is the last one of its compose project; a local
    process is left over when whatever started it is gone and nothing supervises it.
    """
    container = listener.container
    if container is not None:
        stack = container.stack
        if not stack or stack == UNNAMED_STACK or memory.stack_size(stack) > 1:
            return None
        return f"the only container of stack {stack} still running"
    if listener.identity.role not in LEFTOVER_ROLES:
        return None
    process = listener.primary
    if process is None or is_docker_proxy_name(process.name):
        return None
    if getattr(process.origin, "supervisor", None):
        return None  # launchd / systemd started it on purpose; ppid 1 means nothing here
    if not parent_is_gone(process, alive=alive):
        return None
    return f"parent gone ({process.name}, PID {process.pid})"


def _orphaned(listener: Listener, *, memory: PortMemory, alive: Callable[[int], bool]) -> None:
    """The holder looks left over: nothing supervises it, or its stack is gone."""
    reason = leftover_reason(listener, memory=memory, alive=alive)
    if reason is None:
        return
    container = listener.container
    if container is not None:
        suggestion: str = f"stop it (x) if {container.stack} is no longer in use"
        action: str = FixKind.STOP_CONTAINER
    else:
        suggestion = "nothing supervises it any more; kill it (k) if it is not needed"
        action = f"kill:{listener.pid}"
    listener.add_insight(
        Insight(Level.WARNING, "orphaned", f"Left over: {reason}", suggestion, action)
    )


def _shared_projects(listener: Listener, *, memory: PortMemory) -> None:
    """Another project is used to having this port too, so the clash will come back."""
    _service, project = listener_holder(listener)
    others = [h for h in memory.also_used_by(listener.port) if h.project != project]
    if not others:
        return
    shown = others[:PORT_ALSO_USED_SHOWN]
    text = ", ".join(f"{h.label} (seen {h.seen_text})" for h in shown)
    more = len(others) - len(shown)
    listener.add_insight(
        Insight(
            Level.INFO,
            "port-shared-projects",
            f"{listener.port} is also used by {text}" + (f" and {more} more" if more else ""),
        )
    )


__all__ = ["LEFTOVER_ROLES", "leftover_reason", "port_memory_insights"]
