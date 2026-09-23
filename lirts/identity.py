"""Service identity engine: "what is this actually?".

Signals, strongest first:

1. user aliases from the config (port number or substring match)
2. Docker image / compose service name
3. process name + command line (specific frameworks)
4. HTTP ``Server`` / ``X-Powered-By`` headers
5. generic process name
6. well-known port numbers

Each signal contributes a candidate ``(service, role, confidence, reason)``;
the winner is the highest-confidence candidate and every corroborating signal
nudges confidence upwards.  The reasons are kept so the UI can explain the
verdict.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lirts.collectors.kube_parse import parse_port_forward_cmdline, parse_ssh_tunnel_cmdline
from lirts.constants import IDENTITY_PROCESSES_CHECKED
from lirts.identity_rules import (
    _NOT_PROJECTS,
    _PROJECT_PARENTS,
    HEADER_RULES,
    IMAGE_RULES,
    KNOWN_PORTS,
    PROCESS_RULES,
    ROLE_BACKEND,
    ROLE_CACHE,
    ROLE_DB,
    ROLE_DOCKER,
    ROLE_FRONTEND,
    ROLE_LABELS,
    ROLE_PROXY,
    ROLE_QUEUE,
    ROLE_SYSTEM,
    ROLE_TOOL,
    ROLE_TUNNEL,
    ROLE_UNKNOWN,
    Role,
)
from lirts.identity_rules_web import (
    NAME_ROLE_RULES,
    WEB_PROCESS_RULES,
    WEB_SIGNAL_BY_LABEL,
    ProbeKind,
)
from lirts.models import Identity, Listener

__all__ = [
    "HEADER_RULES",
    "IMAGE_RULES",
    "KNOWN_PORTS",
    "NAME_ROLE_RULES",
    "PROCESS_RULES",
    "ROLE_BACKEND",
    "ROLE_CACHE",
    "ROLE_DB",
    "ROLE_DOCKER",
    "ROLE_FRONTEND",
    "ROLE_LABELS",
    "ROLE_PROXY",
    "ROLE_QUEUE",
    "ROLE_SYSTEM",
    "ROLE_TOOL",
    "ROLE_TUNNEL",
    "ROLE_UNKNOWN",
    "Candidate",
    "ProbeKind",
    "Role",
    "guess_project",
    "guess_role_from_name",
    "identify",
    "role_label",
]


@dataclass
class Candidate:
    """One signal's answer to "what is this?", with how strongly it believes it.

    An empty ``service`` makes it a role-only candidate: the signal knows what
    kind of thing answered (a page, an API) but not what to call it, so it
    argues for the role and leaves the name to a signal that has one.
    """

    service: str
    role: str
    confidence: float
    reason: str


# How strongly the shape of an HTTP answer alone argues for a role.
PROBE_HTML_CONFIDENCE = 0.6
PROBE_JSON_CONFIDENCE = 0.7

_compiled_process = [
    (re.compile(n, re.I), re.compile(c, re.I) if c else None, s, r, conf)
    for n, c, s, r, conf in [*WEB_PROCESS_RULES, *PROCESS_RULES]
]
_compiled_image = [(re.compile(p, re.I), s, r) for p, s, r in IMAGE_RULES]
_compiled_header = [(re.compile(p, re.I), s, r) for p, s, r in HEADER_RULES]


def guess_project(paths: list[str | None], home: str | None = None) -> str | None:
    """Derive a project name from working directories / paths under the home dir."""
    home = home or os.path.expanduser("~")
    home_path = Path(home)
    for raw in paths:
        if not raw:
            continue
        try:
            path = Path(raw)
        except (TypeError, ValueError):
            continue
        try:
            rel = path.relative_to(home_path)
        except ValueError:
            continue
        parts = [p for p in rel.parts if p]
        if not parts:
            continue
        # Skip through common container directories (~/Development/<project>).
        idx = 0
        while idx < len(parts) - 1 and parts[idx].lower() in _PROJECT_PARENTS:
            idx += 1
        candidate = parts[idx]
        if candidate.lower() in _NOT_PROJECTS or candidate.startswith("."):
            continue
        return candidate
    return None


def _paths_from_cmdline(cmdline: list[str]) -> list[str]:
    out: list[str] = []
    for arg in cmdline:
        for piece in re.split(r"[=:]", arg):
            if piece.startswith("/") and len(piece) > 1:
                out.append(piece)
    return out


def _alias_candidates(listener: Listener, aliases: dict[Any, Any]) -> list[Candidate]:
    out: list[Candidate] = []
    haystack = " ".join(
        [
            listener.name,
            *[p.command for p in listener.processes],
            listener.container.name if listener.container else "",
            listener.container.image if listener.container else "",
        ]
    ).lower()
    for key, value in aliases.items():
        label = str(value)
        try:
            port = int(key)
        except (TypeError, ValueError):
            port = None
        if port is not None:
            if port == listener.port:
                out.append(Candidate(label, Role.UNKNOWN, 1.0, f"alias for port {port}"))
            continue
        needle = str(key).lower()
        if needle and needle in haystack:
            out.append(Candidate(label, Role.UNKNOWN, 1.0, f'alias matching "{key}"'))
    return out


def _container_candidates(listener: Listener) -> list[Candidate]:
    c = listener.container
    if not c:
        return []
    out: list[Candidate] = []
    image = c.image or ""
    for pattern, service, role in _compiled_image:
        if pattern.search(image):
            out.append(Candidate(service, role, 0.9, f"container image {image}"))
            break
    label = c.service or c.name
    if label:
        role = guess_role_from_name(label) or guess_role_from_name(c.name) or Role.UNKNOWN
        conf = 0.75 if role != Role.UNKNOWN else 0.6
        reason = f"compose service '{label}'" if c.service else f"container {c.name}"
        if role != Role.UNKNOWN:
            reason += f" (name suggests {role_label(role)})"
        out.append(Candidate(label, role, conf, reason))
    return out


_compiled_name_roles = [(re.compile(p, re.I), r) for p, r in NAME_ROLE_RULES]


def guess_role_from_name(name: str | None) -> str | None:
    """Infer a role from a compose service / container name."""
    if not name:
        return None
    for pattern, role in _compiled_name_roles:
        if pattern.search(name):
            return role
    return None


def _process_candidates(listener: Listener) -> list[Candidate]:
    out: list[Candidate] = []
    for proc in listener.processes[:IDENTITY_PROCESSES_CHECKED]:
        name = proc.name or ""
        cmd = proc.command
        for name_re, cmd_re, service, role, conf in _compiled_process:
            if not name_re.search(name):
                continue
            if cmd_re is not None and not cmd_re.search(cmd):
                continue
            reason = f"process {name}" + (f" with '{cmd_re.pattern}' in command" if cmd_re else "")
            out.append(Candidate(service, role, conf, reason))
            break
    return out


def _header_candidates(listener: Listener) -> list[Candidate]:
    probe = listener.http
    if not probe.ok:
        return []
    out: list[Candidate] = []
    for header_name, value in (("Server", probe.server), ("X-Powered-By", probe.powered_by)):
        if not value:
            continue
        for pattern, service, role in _compiled_header:
            if pattern.search(value):
                out.append(Candidate(service, role, 0.7, f"{header_name} header '{value}'"))
                break
        else:
            out.append(
                Candidate(value.split("/")[0], Role.BACKEND, 0.5, f"{header_name} header '{value}'")
            )
    return out


def _probe_candidates(listener: Listener, so_far: list[Candidate]) -> list[Candidate]:
    """What the HTTP probe saw: the framework it recognised, or at least the kind of answer."""
    probe = listener.http
    if not probe.ok:
        return []
    signal = WEB_SIGNAL_BY_LABEL.get(probe.dev_server or "")
    if signal is not None:
        return [
            Candidate(
                signal.service,
                signal.role,
                signal.confidence,
                f"HTTP probe found {signal.label}",
            )
        ]
    if probe.kind == ProbeKind.JSON:
        return [Candidate("", Role.BACKEND, PROBE_JSON_CONFIDENCE, "answers with JSON")]
    if probe.kind == ProbeKind.HTML and not any(c.role == Role.PROXY for c in so_far):
        return [Candidate("", Role.FRONTEND, PROBE_HTML_CONFIDENCE, "answers with HTML")]
    return []


def _port_candidates(listener: Listener) -> list[Candidate]:
    known = KNOWN_PORTS.get(listener.port)
    if not known:
        return []
    return [Candidate(known[0], known[1], 0.35, f"well-known port {listener.port}")]


def _tunnel_candidates(listener: Listener) -> list[Candidate]:
    """kubectl port-forward and ssh -L tunnels, recognised from their command line."""
    listener.tunnel = None
    for proc in listener.processes:
        pf = parse_port_forward_cmdline(proc.cmdline)
        if pf:
            remote = next((r for lo, r in pf["mappings"] if lo == listener.port), None)
            target = f"{pf['kind']}/{pf['name']}" + (f":{remote}" if remote else "")
            ns = f" ({pf['namespace']})" if pf.get("namespace") else ""
            listener.tunnel = {"type": "kubectl", **pf, "remote_port": remote}
            return [
                Candidate(
                    f"port-forward → {target}{ns}",
                    Role.TUNNEL,
                    0.97,
                    "kubectl port-forward command line",
                )
            ]
        ssh = parse_ssh_tunnel_cmdline(proc.cmdline)
        if ssh:
            hop = next(((h, r) for lo, h, r in ssh["forwards"] if lo == listener.port), None)
            target = f"{hop[0]}:{hop[1]}" if hop else "remote"
            via = f" via {ssh['host']}" if ssh.get("host") else ""
            listener.tunnel = {"type": "ssh", **ssh}
            return [
                Candidate(f"ssh tunnel → {target}{via}", Role.TUNNEL, 0.95, "ssh -L command line")
            ]
    return []


def identify(listener: Listener, *, aliases: dict[Any, Any] | None = None) -> Identity:
    """Compute the identity of a listener from all available signals."""
    candidates: list[Candidate] = []
    candidates += _tunnel_candidates(listener)
    candidates += _alias_candidates(listener, aliases or {})
    candidates += _container_candidates(listener)
    candidates += _process_candidates(listener)
    candidates += _header_candidates(listener)
    candidates += _port_candidates(listener)
    candidates += _probe_candidates(listener, candidates)

    project: str | None = None
    if listener.tunnel and listener.tunnel.get("namespace"):
        project = str(listener.tunnel["namespace"])
    elif listener.container and listener.container.stack:
        project = listener.container.stack
    else:
        paths: list[str | None] = []
        for proc in listener.processes:
            paths.append(proc.cwd)
            paths.extend(_paths_from_cmdline(proc.cmdline))
        project = guess_project(paths)

    named = [c for c in candidates if c.service]
    with_role = [c for c in candidates if c.role != Role.UNKNOWN]
    if not named:
        service = listener.name if listener.processes else "Unknown"
        role: str = max(with_role, key=lambda c: c.confidence).role if with_role else Role.UNKNOWN
        if role == Role.UNKNOWN and listener.is_docker:
            role = Role.DOCKER
        return Identity(
            service=service,
            role=role,
            confidence=0.1 if listener.processes else 0.0,
            reasons=[c.reason for c in candidates] or ["no matching rule"],
            project=project,
        )

    best = max(named, key=lambda c: c.confidence)
    role = best.role
    if with_role:
        # A signal that knows the role better than the one that knows the name wins the role.
        strongest_role = max(with_role, key=lambda c: c.confidence)
        if strongest_role.confidence > best.confidence or role == Role.UNKNOWN:
            role = strongest_role.role
    if role == Role.UNKNOWN and listener.is_docker:
        role = Role.DOCKER
    confidence = best.confidence
    corroborating = [
        c for c in candidates if c is not best and (c.role == role or c.service == best.service)
    ]
    confidence = min(0.99, confidence + 0.05 * len(corroborating))
    reasons = [best.reason] + [c.reason for c in candidates if c is not best]
    service = best.service
    # A generic "Docker port proxy" verdict is useless when we know the container.
    if listener.container and role == Role.DOCKER:
        alt = [c for c in named if c.role != Role.DOCKER]
        if alt:
            best_alt = max(alt, key=lambda c: c.confidence)
            service, role = best_alt.service, best_alt.role
            others = [
                c
                for c in candidates
                if c is not best_alt and (c.role == role or c.service == service)
            ]
            confidence = min(0.99, best_alt.confidence + 0.05 * len(others))
            reasons = [best_alt.reason] + [c.reason for c in candidates if c is not best_alt]
    return Identity(
        service=service,
        role=role,
        confidence=round(confidence, 2),
        reasons=reasons,
        project=project,
    )


def role_label(role: str) -> str:
    """Human label for a role, e.g. ``db`` → ``database``."""
    return ROLE_LABELS.get(role, role)
