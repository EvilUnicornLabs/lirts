"""Reachability of a remote host: DNS, one ICMP ping and a TCP connect.

On demand only (``R`` on an ssh row or in the Kubernetes screen, ``lirts reach``).
"""

from __future__ import annotations

import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field

from lirts.constants import PING_TIMEOUT, REACH_TIMEOUT, SSH_DEFAULT_PORT

# Extra seconds allowed for the ping command itself beyond the echo timeout.
_PING_COMMAND_GRACE = 2
# Error strings are shown in one table cell.
_ERROR_PREVIEW_CHARS = 80


@dataclass
class ReachResult:
    """What one reachability check found out about a host."""

    host: str
    port: int | None = None
    ip: str | None = None
    dns_error: str | None = None
    ping_ms: float | None = None
    ping_error: str | None = None
    tcp_ms: float | None = None
    tcp_error: str | None = None
    checked_at: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        """True when the port answered (or, without a port, when the host replied to ping)."""
        if self.dns_error:
            return False
        if self.port is not None:
            return self.tcp_error is None
        return self.ping_error is None

    def lines(self) -> list[tuple[str, str, bool | None]]:
        """(label, text, ok) rows for display."""
        rows: list[tuple[str, str, bool | None]] = []
        rows.append(("DNS", self.ip or self.dns_error or "-", self.dns_error is None))
        if self.ping_ms is not None:
            rows.append(("Ping", f"{self.ping_ms:.1f} ms", True))
        else:
            rows.append(
                ("Ping", self.ping_error or "not tried", None if not self.ping_error else False)
            )
        if self.port is not None:
            if self.tcp_ms is not None:
                rows.append((f"TCP {self.port}", f"connected in {self.tcp_ms:.1f} ms", True))
            else:
                rows.append((f"TCP {self.port}", self.tcp_error or "not tried", False))
        return rows


def resolve(host: str) -> tuple[str | None, str | None]:
    """``(ip, error)``: the host's address, preferring IPv4 for readability."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        return None, f"cannot resolve: {exc.strerror or exc}"
    if not infos:
        return None, "cannot resolve"
    # Prefer IPv4 for a readable address; either works for the checks.
    infos.sort(key=lambda i: i[0] != socket.AF_INET)
    return str(infos[0][4][0]), None


_RTT = re.compile(r"time[=<]([\d.]+) ?ms")


def ping(ip: str, timeout: float = PING_TIMEOUT) -> tuple[float | None, str | None]:
    """One ICMP echo through the system ``ping`` (unprivileged, portable)."""
    if sys.platform == "darwin":
        cmd = ["ping", "-c", "1", "-t", str(int(max(1, timeout))), ip]
    elif sys.platform.startswith("linux"):
        cmd = ["ping", "-c", "1", "-W", str(int(max(1, timeout))), ip]
    else:
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), ip]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + _PING_COMMAND_GRACE
        )
    except FileNotFoundError:
        return None, "ping not installed"
    except subprocess.TimeoutExpired:
        return None, "timeout"
    found = _RTT.search(proc.stdout)
    if proc.returncode == 0 and found:
        return float(found.group(1)), None
    err = (proc.stderr or proc.stdout).strip().splitlines()
    detail = err[-1] if err else f"exit code {proc.returncode}"
    if "100" in detail and "packet loss" in detail:
        detail = "no reply (100% packet loss; the host may block ICMP)"
    return None, detail[:_ERROR_PREVIEW_CHARS]


def tcp_connect(
    ip: str, *, port: int, timeout: float = PING_TIMEOUT
) -> tuple[float | None, str | None]:
    """``(milliseconds, error)`` of one TCP connect attempt."""
    start = time.perf_counter()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return (time.perf_counter() - start) * 1000, None
    except TimeoutError:
        return None, "timeout"
    except OSError as exc:
        return None, (exc.strerror or str(exc))[:_ERROR_PREVIEW_CHARS]


def ssh_target(alias: str) -> tuple[str, int] | None:
    """The host name and port ssh would use for ``alias`` (its config aliases), via ``ssh -G``."""
    try:
        proc = subprocess.run(
            ["ssh", "-G", alias],
            capture_output=True,
            text=True,
            timeout=REACH_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    host, port = None, SSH_DEFAULT_PORT
    for line in proc.stdout.splitlines():
        key, _, value = line.partition(" ")
        if key == "hostname" and value:
            host = value.strip()
        elif key == "port" and value.strip().isdigit():
            port = int(value)
    return (host, port) if host else None


def check_host(host: str, port: int | None = None, timeout: float = PING_TIMEOUT) -> ReachResult:
    """Resolve ``host``, ping it and, with a port, try a TCP connect."""
    result = ReachResult(host=host, port=port)
    result.ip, result.dns_error = resolve(host)
    if result.ip is None:
        # Maybe an ssh config alias ("Host unicorn-1"): ask ssh what it would connect to.
        target = ssh_target(host)
        if target and target[0] != host:
            real_host, ssh_port = target
            result.ip, result.dns_error = resolve(real_host)
            if result.ip is not None:
                result.host = f"{host} ({real_host})"
                if result.port is None or result.port == SSH_DEFAULT_PORT:
                    result.port = ssh_port if result.port is not None else None
    if result.ip is None:
        return result
    result.ping_ms, result.ping_error = ping(result.ip, timeout)
    if port is not None:
        result.tcp_ms, result.tcp_error = tcp_connect(result.ip, port=port, timeout=timeout)
    return result
