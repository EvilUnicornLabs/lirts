"""One listener as plain JSON values, shared by ``lirts list --json`` and the MCP server."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from lirts.models import ContainerInfo, Listener


def container_dict(c: ContainerInfo) -> dict[str, Any]:
    """One container as plain JSON values (the ``container`` part of a row, and ``stacks``)."""
    return {
        "id": c.short_id,
        "name": c.name,
        "image": c.image,
        "stack": c.stack,
        "service": c.service,
        "status": c.status,
        "health": c.health,
        "restart_count": c.restart_count,
        "ports": c.port_map,
        "cpu_percent": c.cpu_percent,
        "memory_mb": c.memory_mb,
        "mounts": [asdict(m) for m in c.mounts],
        "env": c.env,
    }


def listener_dict(lst: Listener) -> dict[str, Any]:
    """One listener as the JSON object ``--json`` prints and the MCP tools return."""
    return {
        "port": lst.port,
        "protocol": lst.protocol,
        "state": lst.state,
        "source": lst.source,
        "addresses": lst.addresses,
        "service": lst.identity.service,
        "role": lst.identity.role,
        "confidence": lst.identity.confidence,
        "reasons": lst.identity.reasons,
        "project": lst.identity.project,
        "process": lst.name,
        "pid": lst.pid,
        "pids": lst.pids,
        "processes": [
            {
                "pid": p.pid,
                "name": p.name,
                "command": p.command,
                "user": p.user,
                "status": p.status,
                "cpu_percent": round(p.cpu_percent, 1),
                "memory_mb": round(p.memory_mb, 1),
                "uptime_seconds": p.uptime_seconds,
                "cwd": p.cwd,
                "addresses": p.addresses,
            }
            for p in lst.processes
        ],
        "cpu_percent": round(lst.cpu_percent, 1),
        "memory_mb": round(lst.memory_mb, 1),
        "connections": lst.connections,
        "activity": lst.activity,
        "bytes_in_rate": lst.bytes_in_rate,
        "bytes_out_rate": lst.bytes_out_rate,
        "uptime_seconds": lst.uptime_seconds,
        "status": lst.status,
        "http": asdict(lst.http) if lst.http.attempted else None,
        "container": container_dict(lst.container) if lst.container else None,
        "proxy_targets": lst.proxy_targets,
        "proxy_chain": lst.proxy_chain,
        "hosts": lst.hosts,
        "shared_reason": lst.shared_reason,
        "insights": [asdict(i) for i in lst.insights],
    }
