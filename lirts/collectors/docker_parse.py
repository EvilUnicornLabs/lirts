"""Parsers and derivations for Docker payloads: list entries, inspect output and stats.

The same functions serve the SDK's API responses and the ``docker`` CLI JSON,
which is shaped to match.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from lirts.config import mask_env
from lirts.models import ContainerInfo, MountInfo

COMPOSE_PROJECT = "com.docker.compose.project"
COMPOSE_SERVICE = "com.docker.compose.service"
COMPOSE_WORKDIR = "com.docker.compose.project.working_dir"


def parse_started_at(value: str | None) -> float | None:
    """Parse Docker's RFC3339 timestamps (nanosecond precision) to epoch seconds."""
    if not value or value.startswith("0001-01-01"):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Trim to microseconds: Python's fromisoformat accepts at most 6 fractional digits.
    if "." in text:
        head, tail = text.split(".", 1)
        frac = ""
        rest = ""
        for i, ch in enumerate(tail):
            if ch.isdigit():
                frac += ch
            else:
                rest = tail[i:]
                break
        text = f"{head}.{frac[:6].ljust(6, '0')}{rest}"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def parse_list_entry(entry: dict[str, Any]) -> ContainerInfo:
    """Build a :class:`ContainerInfo` from a ``GET /containers/json`` element."""
    names = entry.get("Names") or []
    name = names[0].lstrip("/") if names else entry.get("Id", "")[:12]
    labels = entry.get("Labels") or {}
    if isinstance(labels, str):  # `docker ps` CLI output uses "k=v,k=v"
        labels = _parse_label_string(labels)
    host_ports: list[int] = []
    port_map: dict[int, str] = {}
    internal: list[int] = []
    for p in entry.get("Ports") or []:
        private = p.get("PrivatePort")
        public = p.get("PublicPort")
        ptype = (p.get("Type") or "tcp").upper()
        if public:
            public = int(public)
            if public not in host_ports:
                host_ports.append(public)
            port_map[public] = f"{private}/{ptype}"
        elif private and int(private) not in internal:
            internal.append(int(private))
    full_id = entry.get("Id") or entry.get("ID") or ""
    status_text = entry.get("Status") or ""
    health = None
    if "(healthy)" in status_text:
        health = "healthy"
    elif "(unhealthy)" in status_text:
        health = "unhealthy"
    elif "(health: starting)" in status_text:
        health = "starting"
    return ContainerInfo(
        id=full_id,
        short_id=full_id[:12],
        name=name,
        image=entry.get("Image") or "?",
        status=entry.get("State") or "running",
        health=health,
        stack=labels.get(COMPOSE_PROJECT),
        service=labels.get(COMPOSE_SERVICE),
        working_dir=labels.get(COMPOSE_WORKDIR),
        host_ports=sorted(host_ports),
        port_map=port_map,
        internal_ports=sorted(internal),
        labels=labels,
    )


def _parse_label_string(text: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for part in text.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            labels[k.strip()] = v.strip()
    return labels


def parse_cli_ports(text: str) -> list[dict[str, Any]]:
    """Convert ``docker ps`` port strings into API-shaped dicts.

    Example input: ``0.0.0.0:6380->6379/tcp, [::]:6380->6379/tcp, 8080/tcp``
    """
    out: list[dict[str, Any]] = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "->" in chunk:
            left, right = chunk.split("->", 1)
            host_port = left.rsplit(":", 1)[-1]
            ip = left[: -len(host_port) - 1] if ":" in left else ""
            private, _, ptype = right.partition("/")
            try:
                out.append(
                    {
                        "IP": ip.strip("[]"),
                        "PublicPort": int(host_port),
                        "PrivatePort": int(private),
                        "Type": ptype or "tcp",
                    }
                )
            except ValueError:
                continue
        else:
            private, _, ptype = chunk.partition("/")
            try:
                out.append({"PrivatePort": int(private), "Type": ptype or "tcp"})
            except ValueError:
                continue
    return out


def apply_inspect(
    info: ContainerInfo, *, inspect: dict[str, Any], mask_patterns: list[str]
) -> None:
    """Fill in details from ``docker inspect`` output."""
    state = inspect.get("State") or {}
    info.status = state.get("Status") or info.status
    info.started_at = parse_started_at(state.get("StartedAt"))
    health = state.get("Health") or {}
    if health.get("Status"):
        info.health = health["Status"]
    info.restart_count = int(inspect.get("RestartCount") or 0)
    env: dict[str, str] = {}
    for item in (inspect.get("Config") or {}).get("Env") or []:
        key, _, value = item.partition("=")
        env[key] = value
    info.env = mask_env(env, mask_patterns)
    mounts: list[MountInfo] = []
    for m in inspect.get("Mounts") or []:
        mounts.append(
            MountInfo(
                type=m.get("Type") or "?",
                source=m.get("Source") or m.get("Name") or "?",
                destination=m.get("Destination") or "?",
                name=m.get("Name"),
                rw=bool(m.get("RW", True)),
            )
        )
    info.mounts = mounts
    config = inspect.get("Config") or {}
    if config.get("Image") and info.image.startswith("sha256:"):
        info.image = config["Image"]


def compute_stats_delta(
    prev: dict[str, Any] | None, *, cur: dict[str, Any], dt: float
) -> dict[str, float | None]:
    """Derive CPU %, memory and network rates from two one-shot stats payloads."""
    out: dict[str, float | None] = {
        "cpu_percent": None,
        "memory_mb": None,
        "memory_limit_mb": None,
        "net_rx_rate": None,
        "net_tx_rate": None,
        "net_rx_bytes": None,
        "net_tx_bytes": None,
        "pids": None,
    }
    mem = cur.get("memory_stats") or {}
    usage = mem.get("usage")
    if usage is not None:
        stats = mem.get("stats") or {}
        cache = stats.get("inactive_file")
        if cache is None:
            cache = stats.get("cache") or 0
        out["memory_mb"] = max(0.0, (usage - cache) / (1024 * 1024))
    limit = mem.get("limit")
    if limit:
        out["memory_limit_mb"] = limit / (1024 * 1024)
    pids = (cur.get("pids_stats") or {}).get("current")
    if pids is not None:
        out["pids"] = float(pids)
    cpu = cur.get("cpu_stats") or {}
    total = (cpu.get("cpu_usage") or {}).get("total_usage")
    system = cpu.get("system_cpu_usage")
    online = (
        cpu.get("online_cpus") or len((cpu.get("cpu_usage") or {}).get("percpu_usage") or []) or 1
    )
    rx = tx = 0
    for net in (cur.get("networks") or {}).values():
        rx += int(net.get("rx_bytes") or 0)
        tx += int(net.get("tx_bytes") or 0)
    if cur.get("networks"):
        out["net_rx_bytes"] = float(rx)
        out["net_tx_bytes"] = float(tx)
    if prev is not None:
        pcpu = prev.get("cpu_stats") or {}
        ptotal = (pcpu.get("cpu_usage") or {}).get("total_usage")
        psystem = pcpu.get("system_cpu_usage")
        if total is not None and system is not None and ptotal is not None and psystem is not None:
            dcpu = int(total) - int(ptotal)
            dsys = int(system) - int(psystem)
            if dsys > 0 and dcpu >= 0:
                out["cpu_percent"] = round(dcpu / dsys * online * 100.0, 1)
            elif dsys <= 0:
                out["cpu_percent"] = 0.0
        prx = ptx = 0
        for net in (prev.get("networks") or {}).values():
            prx += int(net.get("rx_bytes") or 0)
            ptx += int(net.get("tx_bytes") or 0)
        if dt > 0 and rx >= prx and tx >= ptx:
            out["net_rx_rate"] = (rx - prx) / dt
            out["net_tx_rate"] = (tx - ptx) / dt
    return out
