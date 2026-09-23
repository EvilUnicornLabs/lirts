"""Listening-socket collection via psutil.

macOS refuses ``psutil.net_connections()`` for non-root users, so the
collector falls back to iterating processes.  Both paths produce the same
``(connection, pid)`` pairs which are then grouped by ``(port, protocol)``.

A :class:`ProcessSampler` keeps ``psutil.Process`` objects alive between
refreshes so that ``cpu_percent`` can measure a real delta instead of always
reporting ``0.0`` for a fresh object.
"""

from __future__ import annotations

import contextlib
import logging
import socket
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import psutil

from lirts.models import Edge, Listener, ListenerProcess, ListenerState, SshSession

log = logging.getLogger(__name__)

_MB = 1024 * 1024
# Command lines are kept for display only; the full text can be megabytes.
_CMDLINE_PREVIEW_CHARS = 200
# Process names that mean "an outbound ssh client".
_SSH_CLIENTS = ("ssh", "ssh.exe", "autossh", "mosh-client")


@dataclass
class _ProcSnapshot:
    """Everything we read from one process during one refresh."""

    name: str = "?"
    cmdline: list[str] = field(default_factory=list)
    ppid: int | None = None
    create_time: float | None = None
    cwd: str | None = None
    exe: str | None = None
    user: str | None = None
    status: str | None = None
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    threads: int = 0
    connections: list[Any] = field(default_factory=list)
    accessible: bool = True


class ProcessSampler:
    """Caches ``psutil.Process`` objects so CPU deltas are meaningful."""

    def __init__(self) -> None:
        self._procs: dict[int, psutil.Process] = {}
        self._cycle: dict[int, _ProcSnapshot] = {}
        self.edges: list[Edge] = []
        self.ssh_sessions: list[SshSession] = []

    def begin_cycle(self, connections_by_pid: dict[int, list[Any]] | None = None) -> None:
        """Start a refresh, optionally reusing a system-wide connection listing."""
        self._cycle = {}
        self._preloaded = connections_by_pid or {}

    def end_cycle(self, seen_pids: set[int]) -> None:
        """Forget the cached ``psutil.Process`` objects of processes that are gone."""
        for pid in list(self._procs):
            if pid not in seen_pids:
                del self._procs[pid]

    def _process(self, pid: int) -> psutil.Process:
        proc = self._procs.get(pid)
        if proc is None or not proc.is_running():
            proc = psutil.Process(pid)
            self._procs[pid] = proc
        return proc

    def describe(self, pid: int) -> _ProcSnapshot:
        """Everything readable about one process, cached for the rest of this refresh."""
        cached = self._cycle.get(pid)
        if cached is not None:
            return cached
        snap = _ProcSnapshot()
        try:
            proc = self._process(pid)
            with proc.oneshot():
                snap.name = proc.name() or "?"
                snap.ppid = proc.ppid()
                snap.create_time = proc.create_time()
                snap.status = proc.status()
                with contextlib.suppress(psutil.AccessDenied, psutil.ZombieProcess):
                    snap.cpu_percent = proc.cpu_percent(interval=None)
                with contextlib.suppress(psutil.AccessDenied, psutil.ZombieProcess):
                    snap.memory_mb = proc.memory_info().rss / _MB
                with contextlib.suppress(psutil.AccessDenied, psutil.ZombieProcess):
                    snap.threads = proc.num_threads()
                for attr in ("cmdline", "cwd", "exe", "username"):
                    try:
                        value = getattr(proc, attr)()
                        if attr == "username":
                            snap.user = value
                        else:
                            setattr(snap, attr, value)
                    except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
                        if attr == "cmdline":
                            snap.accessible = False
            if pid in self._preloaded:
                snap.connections = self._preloaded[pid]
            else:
                try:
                    snap.connections = proc.net_connections(kind="inet")
                except (psutil.AccessDenied, psutil.ZombieProcess):
                    snap.connections = []
        except psutil.NoSuchProcess:
            snap.accessible = False
        except psutil.AccessDenied:
            snap.accessible = False
        self._cycle[pid] = snap
        return snap


def _gather_sockets() -> tuple[list[tuple[Any, int | None]], dict[int, list[Any]]]:
    """Return ``[(conn, pid), ...]`` and per-pid connection lists."""
    pairs: list[tuple[Any, int | None]] = []
    by_pid: dict[int, list[Any]] = defaultdict(list)
    try:
        conns = psutil.net_connections(kind="inet")
        for conn in conns:
            pairs.append((conn, conn.pid))
            if conn.pid:
                by_pid[conn.pid].append(conn)
        return pairs, by_pid
    except psutil.AccessDenied:
        pass
    except (psutil.Error, OSError) as exc:  # the system-wide listing fails differently per OS
        log.debug("net_connections failed: %s", exc)

    for proc in psutil.process_iter(["pid"]):
        try:
            pconns: list[Any] = list(proc.net_connections(kind="inet"))
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        for conn in pconns:
            pairs.append((conn, proc.pid))
            by_pid[proc.pid].append(conn)
    return pairs, by_pid


def _is_listener(conn: Any, include_udp: bool) -> str | None:
    """Return the protocol name if ``conn`` is a listening/bound socket."""
    if not conn.laddr:
        return None
    if conn.type == socket.SOCK_STREAM:
        return "TCP" if conn.status == psutil.CONN_LISTEN else None
    if conn.type == socket.SOCK_DGRAM:
        if not include_udp or conn.raddr:
            return None
        return "UDP"
    return None


def _remote(conn: Any) -> str | None:
    raddr = getattr(conn, "raddr", None)
    if not raddr:
        return None
    ip = raddr.ip if hasattr(raddr, "ip") else raddr[0]
    port = raddr.port if hasattr(raddr, "port") else raddr[1]
    return f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"


def collect_listeners(sampler: ProcessSampler, include_udp: bool = False) -> list[Listener]:
    """Collect every listening socket, grouped by ``(port, protocol)``."""
    pairs, by_pid = _gather_sockets()
    sampler.begin_cycle(by_pid)

    groups: dict[tuple[int, str], dict[int | None, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for conn, pid in pairs:
        proto = _is_listener(conn, include_udp)
        if proto is None:
            continue
        port = conn.laddr.port
        addr = conn.laddr.ip
        if addr not in groups[(port, proto)][pid]:
            groups[(port, proto)][pid].append(addr)

    listeners: list[Listener] = []
    seen_pids: set[int] = set()
    for (port, proto), pid_map in groups.items():
        lst = Listener(
            port=port,
            protocol=proto,
            state=ListenerState.LISTEN if proto == "TCP" else ListenerState.BOUND,
        )
        for pid, addresses in pid_map.items():
            if not pid:
                continue
            seen_pids.add(pid)
            snap = sampler.describe(pid)
            inbound = 0
            remotes: list[str] = []
            for c in snap.connections:
                if c.type != socket.SOCK_STREAM or c.status != psutil.CONN_ESTABLISHED:
                    continue
                if c.laddr and c.laddr.port == port:
                    inbound += 1
                    continue
                rc = _remote(c)
                if rc and rc not in remotes:
                    remotes.append(rc)
            lst.processes.append(
                ListenerProcess(
                    pid=pid,
                    name=snap.name,
                    cmdline=list(snap.cmdline or []),
                    addresses=sorted(addresses),
                    ppid=snap.ppid,
                    create_time=snap.create_time,
                    cwd=snap.cwd,
                    exe=snap.exe,
                    user=snap.user,
                    status=snap.status,
                    cpu_percent=snap.cpu_percent,
                    memory_mb=snap.memory_mb,
                    threads=snap.threads,
                    inbound_connections=inbound,
                    remote_conns=remotes,
                    accessible=snap.accessible,
                )
            )
        lst.processes.sort(key=lambda p: (p.create_time or 0.0, p.pid))
        lst.shared_reason = explain_shared_port(lst)
        listeners.append(lst)

    listening_ports = {lst.port for lst in listeners if lst.protocol == "TCP"}
    sampler.edges = collect_edges(by_pid, listening_ports=listening_ports, sampler=sampler)
    sampler.ssh_sessions = collect_ssh_sessions(by_pid, sampler)
    sampler.end_cycle(
        seen_pids | {e.client_pid for e in sampler.edges} | {s.pid for s in sampler.ssh_sessions}
    )
    listeners.sort(key=lambda x: (x.port, x.protocol))
    return listeners


def collect_edges(
    by_pid: dict[int, list[Any]], *, listening_ports: set[int], sampler: ProcessSampler
) -> list[Edge]:
    """Client → local port edges: established connections whose peer is a local listener."""
    counts: dict[tuple[int, int], int] = {}
    local_ports: dict[tuple[int, int], list[int]] = {}
    for pid, conns in by_pid.items():
        if not pid:
            continue
        for c in conns:
            if c.type != socket.SOCK_STREAM or c.status != psutil.CONN_ESTABLISHED or not c.raddr:
                continue
            rip = c.raddr.ip if hasattr(c.raddr, "ip") else c.raddr[0]
            rport = c.raddr.port if hasattr(c.raddr, "port") else c.raddr[1]
            if not _is_local_ip(rip) or rport not in listening_ports:
                continue
            # A listener talking to another listener is still a client of the latter, but
            # the server side of the same connection must not be counted twice.
            if c.laddr and c.laddr.port == rport:
                continue
            counts[(pid, rport)] = counts.get((pid, rport), 0) + 1
            if c.laddr:
                lport = c.laddr.port if hasattr(c.laddr, "port") else c.laddr[1]
                local_ports.setdefault((pid, rport), []).append(int(lport))
    edges: list[Edge] = []
    for (pid, port), n in sorted(counts.items()):
        snap = sampler.describe(pid)
        edges.append(
            Edge(
                client_pid=pid,
                client_name=snap.name,
                dst_port=port,
                count=n,
                client_cmd=" ".join(snap.cmdline or [])[:_CMDLINE_PREVIEW_CHARS],
                client_ports=sorted(local_ports.get((pid, port), [])),
            )
        )
    return edges


def _is_local_ip(ip: str) -> bool:
    return ip.startswith("127.") or ip in ("::1", "0.0.0.0", "::") or ip.startswith("::ffff:127.")


def collect_ssh_sessions(by_pid: dict[int, list[Any]], sampler: ProcessSampler) -> list[SshSession]:
    """Outbound ssh clients with an established connection."""
    sessions: list[SshSession] = []
    for pid, conns in by_pid.items():
        if not pid:
            continue
        snap = sampler.describe(pid)
        if snap.name not in _SSH_CLIENTS:
            continue
        remotes = [
            c
            for c in conns
            if c.type == socket.SOCK_STREAM and c.status == psutil.CONN_ESTABLISHED and c.raddr
        ]
        if not remotes:
            continue
        r = remotes[0].raddr
        rip = r.ip if hasattr(r, "ip") else r[0]
        rport = r.port if hasattr(r, "port") else r[1]
        cmd = list(snap.cmdline or [])
        host = _ssh_host(cmd)
        user = None
        if host and "@" in host:
            user, host = host.split("@", 1)
        forwards = [
            cmd[i + 1] for i, a in enumerate(cmd) if a in ("-L", "-R", "-D") and i + 1 < len(cmd)
        ]
        forwards += [a[2:] for a in cmd if len(a) > 2 and a[:2] in ("-L", "-R", "-D")]
        tty = None
        try:
            tty = (psutil.Process(pid).terminal() or "").removeprefix("/dev/") or None
        except psutil.Error:
            tty = None
        sessions.append(
            SshSession(
                pid=pid,
                host=host or rip,
                remote=f"{rip}:{rport}",
                user=user,
                tty=tty,
                forwards=forwards,
                create_time=snap.create_time,
                cmd=" ".join(cmd)[:_CMDLINE_PREVIEW_CHARS],
            )
        )
    sessions.sort(key=lambda s: (s.host, s.pid))
    return sessions


def _ssh_host(cmd: list[str]) -> str | None:
    takes_value = {
        "-p",
        "-i",
        "-o",
        "-F",
        "-J",
        "-l",
        "-L",
        "-R",
        "-D",
        "-W",
        "-b",
        "-c",
        "-e",
        "-I",
        "-m",
        "-O",
        "-Q",
        "-S",
        "-w",
        "-E",
        "-B",
    }
    i = 1
    while i < len(cmd):
        arg = cmd[i]
        if arg in takes_value:
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        return arg
    return None


def explain_shared_port(listener: Listener) -> str | None:
    """Best-effort explanation for several sockets/processes on one port."""
    procs = listener.processes
    addresses = listener.addresses
    if not procs:
        return None
    families = {"v6" if ":" in a else "v4" for a in addresses}
    if len(procs) == 1:
        if len(families) > 1:
            return "IPv4 + IPv6 dual-stack binding (one process, two sockets)"
        if len(addresses) > 1:
            return f"one process bound on {len(addresses)} addresses"
        return None
    names = {p.name for p in procs}
    if len(names) == 1:
        pids = {p.pid for p in procs}
        parents = {p.ppid for p in procs}
        if parents & pids or len(parents) == 1:
            return f"{len(procs)} {next(iter(names))} workers sharing one socket (prefork/cluster)"
        return f"{len(procs)} {next(iter(names))} processes sharing the port (SO_REUSEPORT)"
    return "multiple unrelated processes bound to the same port (possible conflict)"
