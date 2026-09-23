"""Formatting helpers, the dependency map and the "explain my machine" summary."""

from __future__ import annotations

import time
from collections import Counter

from lirts.constants import EXPLAIN_ROUTES_SHOWN, EXPLAIN_SERVICES_PER_ROLE
from lirts.identity import Role, role_label
from lirts.models import (
    Activity,
    Level,
    Protocol,
    Snapshot,
)

# ----- helpers ---------------------------------------------------------------------


def format_duration(seconds: float | None) -> str:
    """``90`` → ``1m``, ``5400`` → ``1h 30m``; ``None`` → ``-``."""
    if seconds is None:
        return "-"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def format_rate(bps: float | None) -> str:
    """Bytes per second as B/s, KB/s or MB/s; ``None`` → ``-``."""
    if bps is None:
        return "-"
    if bps < 1024:
        return f"{bps:.0f} B/s"
    if bps < 1024**2:
        return f"{bps / 1024:.1f} KB/s"
    return f"{bps / 1024**2:.1f} MB/s"


# ----- who talks to whom -----------------------------------------------------------


_SPARK = "▁▂▃▄▅▆▇█"


def spark(values: list[float], width: int = 12) -> str:
    """Block-glyph sparkline of the last ``width`` values, scaled to their maximum."""
    data = [float(v) for v in values[-width:]]
    if not data:
        return ""
    top = max(data)
    if top <= 0:
        return _SPARK[0] * len(data)
    return "".join(_SPARK[min(len(_SPARK) - 1, round(v / top * (len(_SPARK) - 1)))] for v in data)


def render_graph_text(snapshot: Snapshot) -> str:
    """Plain-text dependency map: local clients → listeners, proxy chains, tunnels, ssh."""
    lines: list[str] = []
    with_clients = [x for x in snapshot.listeners if x.clients]
    lines.append("Local connections (client process → listening port)")
    if not with_clients:
        lines.append(
            "  none right now (connections inside Docker or the cluster are not visible from the host)"
        )
    for lst in sorted(with_clients, key=lambda x: -len(x.clients)):
        tag = f" [{lst.identity.project}]" if lst.identity.project else ""
        lines.append(f"  :{lst.port} {lst.identity.service}{tag}")
        for edge in sorted(lst.clients, key=lambda e: -e.count):
            proj = f" [{edge.client_project}]" if edge.client_project else ""
            times = f" ×{edge.count}" if edge.count > 1 else ""
            traffic = ""
            if edge.bytes_in_rate is not None or edge.bytes_out_rate is not None:
                traffic = (
                    f"  ↓{format_rate(edge.bytes_in_rate)} ↑{format_rate(edge.bytes_out_rate)}"
                    + (f"  {spark(edge.rate_history)}" if len(edge.rate_history) > 1 else "")
                )
            lines.append(
                f"      ◀── {edge.client_name}{proj} (PID {edge.client_pid}){times}{traffic}"
            )
    chains = [x.proxy_chain for x in snapshot.listeners if x.proxy_chain]
    if chains:
        lines.append("")
        lines.append("Proxy chains")
        lines.extend(f"  {c}" for c in chains)
    if snapshot.routes:
        lines.append("")
        lines.append("Proxy routes (virtual host → upstream, from nginx -T / httpd -S)")
        by_port = {x.port: x for x in snapshot.listeners if x.protocol == Protocol.TCP}
        for r in snapshot.routes:
            live = by_port.get(r.listen_port)
            state = "" if live else "  (nothing listening on that port now)"
            target = by_port.get(r.upstream_port) if r.upstream_port else None
            svc = f"  ({target.identity.service})" if target else ""
            lines.append(f"  {r.describe()}{svc}{state}")
    if snapshot.hosts_names:
        lines.append("")
        lines.append("Hosts file (names that resolve to this machine)")
        lines.append("  " + ", ".join(snapshot.hosts_names))
    tunnels = [x for x in snapshot.listeners if x.tunnel]
    if tunnels:
        lines.append("")
        lines.append("Tunnels (local port → remote)")
        for x in tunnels:
            lines.append(
                f"  :{x.port} {x.identity.service}" + (f"  (PID {x.pid})" if x.pid else "")
            )
    if snapshot.ssh_sessions:
        lines.append("")
        lines.append("SSH sessions (outbound)")
        for s in snapshot.ssh_sessions:
            who = f"{s.user}@" if s.user else ""
            fw = f"  forwards {', '.join(s.forwards)}" if s.forwards else ""
            tty = f"  {s.tty}" if s.tty else ""
            age = f"  up {format_duration(time.time() - s.create_time)}" if s.create_time else ""
            lines.append(f"  {who}{s.host} ({s.remote})  PID {s.pid}{tty}{fw}{age}")
    return "\n".join(lines)


# ----- explain my machine ----------------------------------------------------------


def infer_architecture(snapshot: Snapshot) -> str | None:
    """One-line stack guess like ``Vite (frontend) + FastAPI + PostgreSQL + Redis behind Nginx``."""
    by_role: dict[str, list[str]] = {}
    for lst in snapshot.listeners:
        role = lst.identity.role
        if role in {Role.SYSTEM, Role.DOCKER, Role.UNKNOWN}:
            continue
        name = lst.identity.service
        bucket = by_role.setdefault(role, [])
        if name not in bucket:
            bucket.append(name)
    if not by_role:
        return None
    parts: list[str] = []
    for role in (Role.FRONTEND, Role.BACKEND, Role.DB, Role.CACHE, Role.QUEUE):
        names = by_role.get(role)
        if names:
            parts.append(
                " / ".join(names[:3])
                + (f" ({role_label(role)})" if role in (Role.FRONTEND, Role.BACKEND) else "")
            )
    stack = " + ".join(parts) if parts else None
    proxies = by_role.get(Role.PROXY)
    if stack and proxies:
        stack += f", fronted by {' / '.join(proxies[:2])}"
    elif proxies and not stack:
        stack = f"{' / '.join(proxies[:2])} (proxy only)"
    tools = by_role.get(Role.TOOL)
    if tools:
        stack = (stack + "; " if stack else "") + f"tools: {', '.join(tools[:4])}"
    return stack


def explain_sections(
    snapshot: Snapshot, diff: dict[str, list[str]] | None = None
) -> list[tuple[str, str]]:
    """``(title, text)`` sections of the machine summary; empty sections are left out."""
    sections: list[tuple[str, str]] = []
    listeners = snapshot.listeners
    s = snapshot.summary

    # ----- Summary -----------------------------------------------------------------
    lines: list[str] = []
    tcp = [x for x in listeners if x.protocol == Protocol.TCP]
    udp = [x for x in listeners if x.protocol == Protocol.UDP]
    docker = [x for x in listeners if x.is_docker]
    lines.append(
        f"You have {len(tcp)} TCP listener(s)"
        + (f" and {len(udp)} UDP socket(s)" if udp else "")
        + (f", {len(docker)} of them published by Docker" if docker else "")
        + "."
    )
    roles = Counter(x.identity.role for x in listeners)
    interesting = [
        (Role.FRONTEND, "dev / frontend server"),
        (Role.BACKEND, "backend service"),
        (Role.DB, "database"),
        (Role.CACHE, "cache"),
        (Role.QUEUE, "queue / broker"),
        (Role.PROXY, "reverse proxy"),
        (Role.TOOL, "dev tool / desktop app"),
    ]
    for role, label in interesting:
        items = [x for x in listeners if x.identity.role == role]
        if not items:
            continue
        names = []
        for x in items[:EXPLAIN_SERVICES_PER_ROLE]:
            tag = f" [{x.identity.project}]" if x.identity.project else ""
            names.append(f"{x.identity.service} on {x.port}{tag}")
        hidden = len(items) - EXPLAIN_SERVICES_PER_ROLE
        more = f" and {hidden} more" if hidden > 0 else ""
        plural = "s" if len(items) != 1 else ""
        lines.append(f"- {len(items)} {label}{plural}: " + ", ".join(names) + more)
    if roles.get(Role.SYSTEM):
        lines.append(f"- {roles[Role.SYSTEM]} system service(s) (press h to hide them)")
    if s["hot"]:
        lines.append(
            "Hot right now: "
            + ", ".join(
                f"{x.identity.service} ({x.port})" for x in listeners if x.activity == Activity.HOT
            )
        )
    sections.append(("Summary", "\n".join(lines)))

    # ----- Stack ---------------------------------------------------------------------
    lines = []
    arch = infer_architecture(snapshot)
    if arch:
        lines.append(f"Inferred stack: {arch}")
    projects = Counter(x.identity.project for x in listeners if x.identity.project)
    if projects:
        lines.append("Projects: " + ", ".join(f"{p} ({n})" for p, n in projects.most_common(6)))
    origins: dict[str, int] = {}
    for x in listeners:
        p = x.primary
        if p is not None and p.origin is not None and p.origin.terminal:
            origins[p.origin.terminal] = origins.get(p.origin.terminal, 0) + 1
    if origins:
        lines.append(
            "Started from: "
            + ", ".join(f"{k} ({v})" for k, v in sorted(origins.items(), key=lambda kv: -kv[1]))
        )
    if lines:
        sections.append(("Stack", "\n".join(lines)))

    # ----- Network: proxies, routes, hosts, connections, ssh --------------------------
    lines = []
    chains = [x.proxy_chain for x in listeners if x.proxy_chain]
    if chains:
        lines.append("Proxy chains:")
        lines.extend(f"- {c}" for c in chains)
    if snapshot.routes:
        if lines:
            lines.append("")
        lines.append("Proxies (configured virtual hosts):")
        by_port = {x.port: x for x in listeners if x.protocol == Protocol.TCP}
        for r in snapshot.routes[:EXPLAIN_ROUTES_SHOWN]:
            target = by_port.get(r.upstream_port) if r.upstream_port else None
            svc = f" ({target.identity.service})" if target else ""
            down = "" if by_port.get(r.listen_port) else " — not running"
            lines.append(f"- {r.describe()}{svc}{down}")
        hidden_routes = len(snapshot.routes) - EXPLAIN_ROUTES_SHOWN
        if hidden_routes > 0:
            lines.append(f"- … and {hidden_routes} more (w shows all)")
    if snapshot.hosts_names:
        lines.append("Hosts file names: " + ", ".join(snapshot.hosts_names[:10]))
    edges = snapshot.edges
    if edges:
        talking: dict[int, set[str]] = {}
        for e in edges:
            talking.setdefault(e.dst_port, set()).add(e.client_name)
        if lines:
            lines.append("")
        lines.append(f"Local connections: {len(edges)} client → port link(s) (press w for the map)")
        for port, client_names in sorted(talking.items())[:6]:
            svc = next((x.identity.service for x in listeners if x.port == port), str(port))
            lines.append(f"- {svc} on {port} ← " + ", ".join(sorted(client_names)[:4]))
    if snapshot.ssh_sessions:
        if lines:
            lines.append("")
        lines.append(
            "SSH sessions: "
            + ", ".join(
                f"{(s.user + '@') if s.user else ''}{s.host}" for s in snapshot.ssh_sessions[:6]
            )
        )
    if lines:
        sections.append(("Network", "\n".join(lines)))

    # ----- Warnings ------------------------------------------------------------------
    lines = []
    problems = [
        (x, i) for x in listeners for i in x.insights if i.level in (Level.WARNING, Level.ERROR)
    ]
    if problems:
        lines.append(f"Warnings ({len(problems)}):")
        for x, i in problems[:12]:
            marker = "!!" if i.level == Level.ERROR else "! "
            lines.append(f"{marker} {x.key} {x.identity.service}: {i.message}")
        recommendations = [(x, i) for x, i in problems if i.suggestion]
        if recommendations:
            lines.append("")
            lines.append("Recommendations:")
            for x, i in recommendations[:8]:
                lines.append(f"- {x.key}: {i.suggestion}")
    else:
        lines.append("No warnings: everything looks healthy.")
    sections.append(("Warnings", "\n".join(lines)))

    # ----- Changes since the last session ----------------------------------------------
    if diff:
        missing = diff.get("missing") or []
        added = diff.get("added") or []
        changed = diff.get("changed") or []
        if missing or added or changed:
            lines = ["Since the last session:"]
            for m in missing:
                lines.append(f"- missing: {m}")
            for a in added:
                lines.append(f"- new: {a}")
            for c in changed:
                lines.append(f"- changed: {c}")
            sections.append(("Changes", "\n".join(lines)))

    # ----- Kubernetes --------------------------------------------------------------------
    kube = snapshot.kube
    if kube is not None:
        lines = []
        if kube.available:
            scope = "all namespaces" if kube.namespace is None else f"namespace {kube.namespace}"
            bad = kube.unhealthy_pods
            lines.append(
                f"Kubernetes: context {kube.context}, {scope}: {len(kube.pods)} pods, "
                f"{len(kube.deployments)} deployments, {len(kube.services)} services"
                + (
                    f"; {len(bad)} pod(s) not healthy: " + ", ".join(p.name for p in bad[:5])
                    if bad
                    else "; all pods healthy"
                )
            )
            tunnels = [x for x in listeners if x.identity.role == Role.TUNNEL]
            if tunnels:
                lines.append(
                    "Tunnels: "
                    + ", ".join(
                        f"{x.port} → {x.identity.service.split('→', 1)[-1].strip()}"
                        for x in tunnels
                    )
                )
        else:
            lines.append(f"Kubernetes: not available ({kube.error or 'no cluster'})")
        sections.append(("Kubernetes", "\n".join(lines)))

    # ----- Patterns: what keeps happening --------------------------------------------------
    if snapshot.patterns:
        lines = ["Recurring (remembered across runs):"]
        for level, message, suggestion in snapshot.patterns[:12]:
            marker = "!!" if level == Level.ERROR else ("! " if level == Level.WARNING else "- ")
            lines.append(f"{marker} {message}")
            if suggestion:
                lines.append(f"   → {suggestion}")
        sections.append(("Patterns", "\n".join(lines)))

    # ----- Events ------------------------------------------------------------------------
    if snapshot.events:
        lines = ["Recent events:"]
        for ev in snapshot.events[-6:]:
            lines.append(
                f"- {time.strftime('%H:%M:%S', time.localtime(ev.timestamp))} {ev.message}"
            )
        sections.append(("Events", "\n".join(lines)))
    return sections


def explain(snapshot: Snapshot, diff: dict[str, list[str]] | None = None) -> str:
    """Natural-language summary of the machine state with recommendations (all sections)."""
    return "\n\n".join(text for _, text in explain_sections(snapshot, diff))
