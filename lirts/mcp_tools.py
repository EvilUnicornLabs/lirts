"""What the MCP tools answer, built from an open engine.

Plain functions over :class:`lirts.engine.Engine` so they can be tested without the MCP
SDK; :mod:`lirts.mcp_server` registers them as tools.  Everything here reads the engine's
latest snapshot and stores; the only calls that do work on demand are ``health`` (a check,
as ``H`` does) and the three actions, which exist only when the server was started with
``--allow-actions``.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from lirts.doctor import as_dict, run_all
from lirts.engine import Engine
from lirts.filtering import apply_filter
from lirts.fixes import FixKind, fix_for
from lirts.insights import explain, render_graph_text
from lirts.insights_ports import leftover_reason
from lirts.listener_json import container_dict, listener_dict
from lirts.models import Level, Listener, ListenerState
from lirts.ports import UsualHolder, listener_holder
from lirts.replay import to_jsonable

REPLAY_REFUSAL = "replay mode: actions are disabled"


def _holder_dict(holder: UsualHolder) -> dict[str, Any]:
    return {
        "service": holder.service,
        "project": holder.project,
        "label": holder.label,
        "days": holder.days,
        "refreshes": holder.refreshes,
        "last_seen": holder.last_seen,
    }


def _row(engine: Engine, port: int, protocol: str) -> Listener | None:
    return engine.snapshot.by_port(port, protocol.upper())


# ----- read-only ---------------------------------------------------------------------


def listeners_payload(
    engine: Engine, *, filter_text: str = "", port: int | None = None
) -> dict[str, Any]:
    """The table as ``lirts list --json`` prints it, optionally narrowed to a filter or a port."""
    snap = engine.snapshot
    rows = apply_filter(snap.listeners, filter_text) if filter_text else list(snap.listeners)
    if port is not None:
        rows = [x for x in rows if x.port == port]
    return {
        "timestamp": snap.timestamp,
        "docker_available": snap.docker_available,
        "summary": snap.summary,
        "listeners": [listener_dict(x) for x in rows],
    }


def who_payload(engine: Engine, port: int, protocol: str = "TCP") -> dict[str, Any]:
    """Who holds a port, what usually holds it and whether it looks left over (``lirts who``)."""
    memory = engine.port_memory
    usual = memory.usual(port)
    others = [_holder_dict(h) for h in memory.also_used_by(port)]
    lst = _row(engine, port, protocol)
    if lst is None:
        return {
            "port": port,
            "protocol": protocol.upper(),
            "free": True,
            "usually": _holder_dict(usual) if usual else None,
            "also_used_by": others,
        }
    service, project = listener_holder(lst)
    return {
        "port": port,
        "protocol": protocol.upper(),
        "free": False,
        "service": service,
        "project": project,
        "left_over": leftover_reason(lst, memory=memory),
        "usually": _holder_dict(usual) if usual else None,
        "also_used_by": others,
        "listener": listener_dict(lst),
    }


def explain_text(engine: Engine) -> str:
    """The machine in words, the same text as ``lirts explain`` and ``E``."""
    return explain(engine.snapshot, engine.last_session_diff)


def graph_text(engine: Engine) -> str:
    """Who talks to whom: clients per port, proxy chains, routes, tunnels, ssh (``w``)."""
    return render_graph_text(engine.snapshot)


def topology_payload(engine: Engine) -> dict[str, Any]:
    """The learned star map: nodes and relations with first / last seen (``lirts topology``)."""
    return engine.topology.graph_for(hide_system=engine.hide_system).to_dict()


def events_payload(engine: Engine, limit: int) -> list[dict[str, Any]]:
    """The newest start / stop / restart / health events, oldest first."""
    return [asdict(e) for e in engine.history.recent_events(limit)]


def patterns_payload(engine: Engine) -> list[dict[str, Any]]:
    """Recurring issues remembered across runs (``lirts patterns``)."""
    return [p.to_dict() | {"count": p.count} for p in engine.patterns.recurring()]


def routes_payload(engine: Engine) -> list[dict[str, Any]]:
    """Virtual hosts of local nginx / httpd and their upstreams (``lirts routes``)."""
    return [asdict(r) for r in engine.snapshot.routes]


def stacks_payload(engine: Engine) -> dict[str, list[dict[str, Any]]]:
    """Compose projects and their containers (``lirts stack list``)."""
    return {project: [container_dict(c) for c in cs] for project, cs in engine.stacks().items()}


def kube_payload(engine: Engine) -> dict[str, Any]:
    """The cluster as kubectl last reported it, or why it is unavailable."""
    state = engine.snapshot.kube
    if state is None:
        return {"available": False, "error": "Kubernetes is off or kubectl is not available"}
    return to_jsonable(state)


async def health_payload(engine: Engine, port: int | None = None) -> list[dict[str, Any]]:
    """Check health now, of one port or every listening row; the only way checks run."""
    rows = [x for x in engine.snapshot.listeners if x.state == ListenerState.LISTEN]
    if port is not None:
        rows = [x for x in rows if x.port == port]
    await engine.check_health_now(rows)
    return [
        {
            "port": x.port,
            "service": x.identity.service,
            "project": x.identity.project,
            "ok": x.health.ok and (not x.container or x.container.health != "unhealthy"),
            "tcp_ok": x.health.tcp_ok,
            "tcp_latency_ms": x.health.tcp_latency_ms,
            "tcp_error": x.health.tcp_error,
            "http_path": x.health.http_path,
            "http_status": x.health.http_status,
            "http_latency_ms": x.health.http_latency_ms,
            "docker_health": x.container.health if x.container else None,
        }
        for x in rows
    ]


def doctor_payload() -> list[dict[str, Any]]:
    """The environment checks of ``lirts doctor``."""
    return [as_dict(c) for c in run_all()]


# ----- actions (only with --allow-actions) ------------------------------------------


def _refusal(engine: Engine, port: int, protocol: str) -> dict[str, Any] | None:
    """Why an action cannot run, or None when it may."""
    if getattr(engine, "replay", False):
        return {"ok": False, "error": REPLAY_REFUSAL}
    if _row(engine, port, protocol) is None:
        return {"ok": False, "error": f"nothing on {port}/{protocol.upper()}"}
    return None


def _note(engine: Engine, lst: Listener, text: str, *, ok: bool) -> None:
    engine.history.note(
        f"[~] {lst.identity.service} on {lst.key}: {text} (mcp)",
        port=lst.port,
        level=Level.INFO if ok else Level.WARNING,
    )


def free_port_payload(
    engine: Engine, port: int, *, protocol: str = "TCP", force: bool = False
) -> dict[str, Any]:
    """Hand a port back: terminate its processes and stop its container (``lirts free``)."""
    refusal = _refusal(engine, port, protocol)
    if refusal is not None:
        return refusal
    lst = _row(engine, port, protocol)
    assert lst is not None
    steps: list[dict[str, Any]] = []
    pids = [p.pid for p in lst.real_processes]
    if pids:
        for pid, ok, msg in engine.kill_and_wait(pids, force=force):
            steps.append({"target": f"PID {pid}", "ok": ok, "message": msg})
    if lst.container is not None:
        ok, msg = engine.docker.stop(lst.container.id)
        steps.append({"target": lst.container.name, "ok": ok, "message": msg})
    if not steps:
        return {"ok": False, "error": f"nothing on {port} that lirts can stop"}
    all_ok = all(s["ok"] for s in steps)
    _note(engine, lst, "freed", ok=all_ok)
    return {"ok": all_ok, "port": port, "steps": steps}


def fix_payload(engine: Engine, port: int, *, protocol: str = "TCP") -> dict[str, Any]:
    """Run the fix the row's worst insight proposes (``F``); a logs fix returns the logs."""
    refusal = _refusal(engine, port, protocol)
    if refusal is not None:
        return refusal
    lst = _row(engine, port, protocol)
    assert lst is not None
    fix = fix_for(lst)
    if fix is None:
        return {"ok": False, "error": "no fix proposed for this row"}
    result: dict[str, Any] = {"fix": fix.kind, "command": fix.command, "insight": fix.insight.code}
    container = lst.container
    if fix.kind == FixKind.KILL and fix.pid is not None:
        outcome = engine.kill_and_wait([fix.pid], force=False)
        ok = all(done for _pid, done, _msg in outcome)
        result["message"] = "; ".join(msg for _pid, _ok, msg in outcome)
    elif fix.kind == FixKind.RESTART_CONTAINER and container is not None:
        ok, result["message"] = engine.docker.restart(container.id)
    elif fix.kind == FixKind.STOP_CONTAINER and container is not None:
        ok, result["message"] = engine.docker.stop(container.id)
    elif fix.kind == FixKind.RESTART_PROCESS:
        ok, result["message"] = engine.restart_process(lst)
    elif fix.kind == FixKind.LOGS and container is not None:
        ok, result["logs"] = True, engine.docker.logs(container.id)
        return result | {"ok": ok}
    elif fix.kind == FixKind.STOP_FORWARD:
        stopped = engine.kube.stop_forward(local_port=lst.port)
        ok = all(done for _port, done, _msg in stopped)
        result["message"] = "; ".join(msg for _port, _ok, msg in stopped)
    else:
        return {"ok": False, "error": f"fix {fix.kind} cannot run here"}
    _note(engine, lst, f"fix run, {fix.insight.code}: {fix.command}", ok=ok)
    return result | {"ok": ok}


def restart_payload(engine: Engine, port: int, *, protocol: str = "TCP") -> dict[str, Any]:
    """Restart: a container through Docker, a local process by re-running its command."""
    refusal = _refusal(engine, port, protocol)
    if refusal is not None:
        return refusal
    lst = _row(engine, port, protocol)
    assert lst is not None
    if lst.container is not None:
        ok, msg = engine.docker.restart(lst.container.id)
    else:
        ok, msg = engine.restart_process(lst)
    _note(engine, lst, "restarted", ok=ok)
    return {"ok": ok, "port": port, "message": msg}
