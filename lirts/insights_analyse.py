"""Per-listener analysis: activity level, proxy chains and the insight rules."""

from __future__ import annotations

import time
from collections.abc import Callable

import psutil

from lirts.config_view import ConfigView
from lirts.constants import (
    ACTIVITY_ACTIVE_SCORE,
    ACTIVITY_HOT_SCORE,
    CONFLICT_PROCESSES_SHOWN,
    PROXY_CHAIN_DEPTH,
    SECONDS_PER_HOUR,
    UNREACHABLE_MIN_CONFIDENCE,
)
from lirts.identity import Role
from lirts.insights_explain import format_duration
from lirts.insights_ports import port_memory_insights
from lirts.models import (
    Activity,
    ContainerHealth,
    Insight,
    Level,
    Listener,
    ListenerState,
    Protocol,
    is_docker_proxy_name,
)
from lirts.ports import PortMemory

PROXY_ROLES = {Role.PROXY}
WEB_ROLES = {Role.FRONTEND, Role.BACKEND, Role.PROXY, Role.TOOL}
_LOOPBACK_PREFIXES = ("127.", "[::1]", "localhost")
# Process names that behave like a reverse proxy even when identity says otherwise.
_PROXY_PROCESS_NAMES = ("nginx", "caddy", "traefik", "haproxy", "envoy", "httpd", "apache")
# Process states the OS reports for something that is no longer running.
_DEAD_PROCESS_STATES = ("zombie", "dead")


def _remote_port(remote: str) -> int | None:
    try:
        return int(remote.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _is_loopback(remote: str) -> bool:
    return remote.startswith(_LOOPBACK_PREFIXES)


# ----- activity --------------------------------------------------------------------


def classify_activity(listener: Listener) -> tuple[str, float]:
    """Return ``(level, score)``; score is a 0..1 heat used for the heat bar."""
    rate = None
    if listener.bytes_in_rate is not None or listener.bytes_out_rate is not None:
        rate = (listener.bytes_in_rate or 0.0) + (listener.bytes_out_rate or 0.0)
    conns = listener.connections
    cpu = listener.cpu_percent
    score = 0.0
    if rate is not None and rate >= 1024:
        score = max(score, min(1.0, 0.3 + rate / (2 * 1024**2)))
    if conns:
        score = max(score, min(1.0, 0.25 + conns / 40))
    if cpu >= 1.0:
        score = max(score, min(1.0, 0.2 + cpu / 100))
    if score >= ACTIVITY_HOT_SCORE:
        return Activity.HOT, score
    if score >= ACTIVITY_ACTIVE_SCORE:
        return Activity.ACTIVE, score
    return Activity.IDLE, score


# ----- proxy mapping ---------------------------------------------------------------


def detect_proxies(listeners: list[Listener]) -> None:
    """Fill ``proxy_targets`` and ``proxy_chain`` for proxy-like listeners."""
    by_port: dict[int, Listener] = {}
    for lst in listeners:
        if lst.protocol == Protocol.TCP:
            by_port.setdefault(lst.port, lst)
    for lst in listeners:
        lst.proxy_targets = []
        lst.proxy_chain = None
    for lst in listeners:
        is_proxy = lst.identity.role in PROXY_ROLES or any(
            n in lst.name.lower() for n in _PROXY_PROCESS_NAMES
        )
        if not is_proxy:
            continue
        for remote in lst.remote_conns:
            if not _is_loopback(remote):
                continue
            port = _remote_port(remote)
            if port is None or port == lst.port or port not in by_port:
                continue
            if port not in lst.proxy_targets:
                lst.proxy_targets.append(port)
        # A proxy that is bound to a specific loopback address might also be
        # forwarded to from a docker proxy; keep it simple and best-effort.
    for lst in listeners:
        if lst.proxy_targets:
            lst.proxy_chain = build_chain(lst, by_port=by_port)


def build_chain(
    start: Listener, *, by_port: dict[int, Listener], depth: int = PROXY_CHAIN_DEPTH
) -> str:
    """``port → service`` hops from ``start`` through its proxy targets."""
    parts = [f"{start.port} → {start.identity.service}"]
    current = start
    seen = {start.port}
    for _ in range(depth):
        if not current.proxy_targets:
            break
        target_port = current.proxy_targets[0]
        if target_port in seen:
            break
        seen.add(target_port)
        target = by_port[target_port]
        parts.append(f"{target.port} → {target.identity.service}")
        current = target
    extra = len(start.proxy_targets) - 1
    chain = " → ".join(parts)
    if extra > 0:
        chain += f" (+{extra} more)"
    return chain


# ----- insights per listener -------------------------------------------------------


def analyse(
    listener: Listener,
    *,
    cfg: ConfigView,
    now: float | None = None,
    memory: PortMemory | None = None,
    alive: Callable[[int], bool] = psutil.pid_exists,
) -> None:
    """Populate ``listener.insights`` from the current state, its history and the port memory.

    ``alive`` is the process-liveness test of the orphan rule; the demo engine supplies its own.
    """
    now = now or time.time()
    listener.insights = []
    role = listener.identity.role

    # Port conflicts ---------------------------------------------------------------
    names = {p.name for p in listener.processes}
    if len(names) > 1:
        oldest = listener.primary
        newest = max(listener.processes, key=lambda p: (p.create_time or 0.0, p.pid))
        parts = []
        docker_proc = None
        for p in listener.processes[:CONFLICT_PROCESSES_SHOWN]:
            addr = "/".join(p.addresses) if p.addresses else "?"
            label = f"{p.name} (PID {p.pid}) on {addr}"
            if is_docker_proxy_name(p.name):
                docker_proc = p
                if listener.container:
                    label = f"container {listener.container.name} via {p.name} on {addr}"
            parts.append(label)
        detail = "; ".join(parts)
        suggestion = None
        action = None
        local = [p for p in listener.processes if not is_docker_proxy_name(p.name)]
        if docker_proc and local:
            victim = local[0]
            suggestion = (
                f"localhost connections go to whichever bound first; stop the container (x) "
                f"or kill {victim.name} (PID {victim.pid})"
            )
            action = f"kill:{victim.pid}"
        elif oldest and newest and oldest.pid != newest.pid:
            suggestion = f"kill the older process {oldest.name} (PID {oldest.pid}) if it is stale"
            action = f"kill:{oldest.pid}"
        listener.add_insight(
            Insight(Level.ERROR, "port-conflict", f"Port conflict: {detail}", suggestion, action)
        )
    elif listener.shared_reason and len(listener.processes) > 1:
        listener.add_insight(
            Insight(Level.INFO, "shared-port", f"Shared port: {listener.shared_reason}")
        )
    elif listener.shared_reason:
        listener.add_insight(Insight(Level.INFO, "shared-port", listener.shared_reason))

    # Zombies / dead processes -------------------------------------------------------
    for proc in listener.processes:
        if proc.status in _DEAD_PROCESS_STATES:
            listener.add_insight(
                Insight(
                    Level.ERROR,
                    "zombie",
                    f"{proc.name} (PID {proc.pid}) is a {proc.status} process",
                    "kill the parent process or reboot",
                    f"kill:{proc.pid}",
                )
            )

    # Docker state -------------------------------------------------------------------
    c = listener.container
    if c:
        if c.health == ContainerHealth.UNHEALTHY:
            listener.add_insight(
                Insight(
                    Level.ERROR,
                    "container-unhealthy",
                    f"Container {c.name} reports unhealthy",
                    "check the logs (l) or restart it (t)",
                    "restart-container",
                )
            )
        elif c.health == "starting":
            listener.add_insight(
                Insight(
                    Level.INFO, "container-starting", f"Container {c.name} health check is starting"
                )
            )
        if c.status not in ("running", "healthy"):
            listener.add_insight(
                Insight(
                    Level.ERROR,
                    "container-state",
                    f"Container {c.name} is {c.status}",
                    "restart it (t) or inspect the logs (l)",
                    "restart-container",
                )
            )
        if c.restart_count >= cfg.insights.restart_warning:
            listener.add_insight(
                Insight(
                    Level.WARNING,
                    "container-restarts",
                    f"Container restarted {c.restart_count} times",
                    "look for crash loops in the logs (l)",
                    "logs",
                )
            )
        if not listener.processes and listener.state == ListenerState.INTERNAL:
            listener.add_insight(
                Insight(Level.INFO, "unpublished", "Container port is not published to the host")
            )

    # Restart churn from history ------------------------------------------------------
    if listener.restarts_last_hour >= cfg.insights.restart_warning:
        listener.add_insight(
            Insight(
                Level.WARNING,
                "restarts",
                f"Restarted {listener.restarts_last_hour} times in the last hour "
                f"(times in the History tab, i)",
                (
                    "the service may be crash-looping; check its logs (l)"
                    if listener.container
                    else "the process keeps being replaced: a supervisor (launchd, systemd, "
                    "a watcher) relaunches it after a crash or an update; check its logs and "
                    "crash reports"
                ),
                "logs" if listener.container else None,
            )
        )

    # HTTP health ----------------------------------------------------------------------
    probe = listener.http
    if probe.attempted:
        hard_failure = probe.error in ("timeout",) or (probe.error or "").startswith(
            "connection refused"
        )
        if (
            not probe.ok
            and hard_failure
            and role in {Role.FRONTEND, Role.BACKEND, Role.PROXY}
            and listener.identity.confidence >= UNREACHABLE_MIN_CONFIDENCE
        ):
            listener.add_insight(
                Insight(
                    Level.WARNING,
                    "unreachable",
                    f"HTTP probe failed: {probe.error or 'no response'}",
                    "the port is open but does not answer HTTP; it may be starting or not a web service",
                    "logs" if listener.container else None,
                )
            )
        elif probe.ok and probe.status is not None and probe.status >= 500:
            listener.add_insight(
                Insight(
                    Level.WARNING,
                    "http-5xx",
                    f"HTTP {probe.status} from /",
                    "the service is up but failing requests; check its logs",
                    "logs" if listener.container else None,
                )
            )
        if probe.ok and probe.latency_ms is not None:
            latency_yellow, latency_red = cfg.thresholds.latency_ms
            if probe.latency_ms >= latency_red:
                listener.add_insight(
                    Insight(Level.WARNING, "latency", f"Slow response: {probe.latency_ms:.0f} ms")
                )
            elif probe.latency_ms >= latency_yellow:
                listener.add_insight(
                    Insight(Level.INFO, "latency", f"Elevated latency: {probe.latency_ms:.0f} ms")
                )

    # Universal health check ---------------------------------------------------------------
    h = listener.health
    if h.checked:
        if not h.tcp_ok:
            listener.add_insight(
                Insight(
                    Level.ERROR,
                    "unreachable-tcp",
                    f"Listening but not accepting connections ({h.tcp_error or 'no answer'})",
                    "the process may be hung or its backlog full; restart it (t) or check its logs",
                    "restart-container" if listener.container else "restart-process",
                )
            )
        elif h.http_status is not None and h.http_status >= 400:
            listener.add_insight(
                Insight(
                    Level.WARNING,
                    "health-failing",
                    f"Health endpoint {h.http_path} returned {h.http_status}",
                    "the service reports itself unhealthy; check its logs (l) and dependencies",
                    "logs" if listener.container else None,
                )
            )

    # Resource thresholds ----------------------------------------------------------------
    cpu_yellow, cpu_red = cfg.thresholds.cpu
    memory_yellow, memory_red = cfg.thresholds.memory_mb
    if listener.cpu_percent >= cpu_red:
        listener.add_insight(
            Insight(Level.WARNING, "cpu-high", f"High CPU: {listener.cpu_percent:.0f}%")
        )
    elif listener.cpu_percent >= cpu_yellow:
        listener.add_insight(
            Insight(Level.INFO, "cpu-elevated", f"Elevated CPU: {listener.cpu_percent:.0f}%")
        )
    if listener.memory_mb >= memory_red:
        listener.add_insight(
            Insight(Level.WARNING, "mem-high", f"High memory: {listener.memory_mb:.0f} MB")
        )
    elif listener.memory_mb >= memory_yellow:
        listener.add_insight(
            Insight(Level.INFO, "mem-elevated", f"Elevated memory: {listener.memory_mb:.0f} MB")
        )

    # Stale processes ---------------------------------------------------------------------
    stale_after = cfg.insights.stale_after_hours * SECONDS_PER_HOUR
    uptime = listener.uptime_seconds
    if (
        role in {Role.FRONTEND, Role.BACKEND}
        and not listener.is_docker
        and uptime is not None
        and uptime >= stale_after
        and listener.connections == 0
        and listener.activity == Activity.IDLE
        and (listener.last_active is None or now - listener.last_active >= stale_after)
        and listener.pid
    ):
        listener.add_insight(
            Insight(
                Level.WARNING,
                "stale",
                f"No activity for {format_duration(uptime)}; probably a stale {listener.identity.service}",
                f"kill it (k) if it is no longer needed: PID {listener.pid}",
                f"kill:{listener.pid}",
            )
        )

    # Inaccessible process (other user) ---------------------------------------------------
    if listener.processes and all(not p.accessible for p in listener.processes):
        listener.add_insight(
            Insight(Level.INFO, "no-access", "Process details need elevated privileges")
        )

    # What usually holds this port ----------------------------------------------------------
    if memory is not None:
        port_memory_insights(listener, memory=memory, now=now, alive=alive)
