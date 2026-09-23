"""What the demo providers read out of the world: rows, edges, flows, stats.

Each method turns the world's state into the shape a real collector would
return, so the engine pipeline cannot tell the difference.
"""

from __future__ import annotations

import math

from lirts.collectors.ports import _ProcSnapshot
from lirts.demo.catalog import CHROME_PID, SHELL_PID, DemoProcess
from lirts.demo.machine import DemoMachine
from lirts.models import (
    Edge,
    HealthResult,
    Listener,
    ListenerProcess,
    ListenerState,
    Protocol,
    SystemStats,
)


class DemoViewsMixin(DemoMachine):
    """Turns the world's state into the shapes the real collectors return."""

    def listeners(self) -> list[Listener]:
        """One row per ``(port, protocol)`` of the pretend machine."""
        groups: dict[tuple[int, str], list[DemoProcess]] = {}
        for p in self.procs.values():
            for port, proto in p.ports:
                groups.setdefault((port, proto), []).append(p)
        out: list[Listener] = []
        for (port, proto), procs in sorted(groups.items()):
            lst = Listener(
                port=port,
                protocol=proto,
                state=ListenerState.LISTEN if proto == Protocol.TCP else ListenerState.BOUND,
            )
            for p in procs:
                lst.processes.append(
                    ListenerProcess(
                        pid=p.pid,
                        name=p.name,
                        cmdline=list(p.cmdline),
                        addresses=list(p.addresses),
                        ppid=1 if p.orphan else SHELL_PID,
                        create_time=p.created,
                        cwd=p.cwd,
                        exe=p.cmdline[0] if p.cmdline else None,
                        user=p.user,
                        status="running",
                        cpu_percent=p.cpu,
                        memory_mb=p.mem,
                        threads=p.threads,
                        inbound_connections=p.inbound if port == p.ports[0][0] else 0,
                        remote_conns=list(p.remotes),
                        origin=p.origin,
                    )
                )
            out.append(lst)
        return out

    def edges(self) -> list[Edge]:
        """The client → listening-port links of the pretend machine."""
        chrome = "Google Chrome Helper"
        return [
            Edge(
                CHROME_PID,
                chrome,
                5173,
                count=3,
                client_cmd="Google Chrome Helper (Renderer)",
                client_ports=[54001, 54002, 54003],
            ),
            Edge(
                CHROME_PID,
                chrome,
                3000,
                count=1,
                client_cmd="Google Chrome Helper (Renderer)",
                client_ports=[54010],
            ),
            Edge(
                8001,
                "python",
                5432,
                count=2,
                client_cmd="python -m uvicorn app:app --port 8001",
                client_ports=[54020, 54021],
            ),
            Edge(
                8001,
                "python",
                6379,
                count=1,
                client_cmd="python -m uvicorn app:app --port 8001",
                client_ports=[54030],
            ),
            Edge(
                80,
                "nginx",
                5173,
                count=2,
                client_cmd="nginx: worker process",
                client_ports=[54040, 54041],
            ),
        ]

    def flows(self) -> dict[tuple[int, int, int], tuple[float, float, int, int]]:
        """Per-connection rates and totals, as the bandwidth sampler would report them."""
        t = self.now - self.started
        wave = math.sin(t / 5.0) * 0.5 + 0.5
        out: dict[tuple[int, int, int], tuple[float, float, int, int]] = {}
        for e in self.edges():
            for i, lport in enumerate(e.client_ports):
                rate_in = 800.0 * wave + 200.0 * i
                rate_out = 300.0 * wave + 50.0 * i
                out[(e.client_pid, lport, e.dst_port)] = (
                    rate_in,
                    rate_out,
                    int(rate_in * t),
                    int(rate_out * t),
                )
        return out

    def rates(self) -> dict[int, tuple[float, float]]:
        """Per-process throughput, as the bandwidth sampler would report it."""
        out: dict[int, tuple[float, float]] = {}
        for p in self.procs.values():
            if p.rate_in or p.rate_out:
                out[p.pid] = (p.rate_in * p.factor, p.rate_out * p.factor)
        return out

    def snapshot_proc(self, pid: int) -> _ProcSnapshot:
        """What the process sampler would read about one pretend process."""
        snap = _ProcSnapshot()
        p = self.procs.get(pid)
        if p is None:
            if pid == CHROME_PID:
                snap.name = "Google Chrome Helper"
                snap.cmdline = ["Google Chrome Helper (Renderer)", "--type=renderer"]
            return snap
        snap.name, snap.cmdline, snap.create_time = p.name, list(p.cmdline), p.created
        snap.ppid = 1 if p.orphan else SHELL_PID
        snap.cwd, snap.user, snap.status, snap.cpu_percent = p.cwd, p.user, "running", p.cpu
        snap.memory_mb, snap.threads = p.mem, p.threads
        return snap

    def system(self) -> SystemStats:
        """Machine-wide numbers for the top bar, with a gentle wave over time."""
        t = self.now - self.started
        n = int(t // 2) + 1
        cpu = [
            round(18 + 12 * (math.sin(i / 4.0) * 0.5 + 0.5), 1) for i in range(max(0, n - 60), n)
        ]
        mem = [round(61 + 3 * (math.sin(i / 9.0) * 0.5 + 0.5), 1) for i in range(max(0, n - 60), n)]
        down = [
            round(90000 * (math.sin(i / 3.0) * 0.5 + 0.5) + 4000, 0)
            for i in range(max(0, n - 60), n)
        ]
        up = [
            round(30000 * (math.sin(i / 5.0 + 1) * 0.5 + 0.5) + 1500, 0)
            for i in range(max(0, n - 60), n)
        ]
        return SystemStats(
            cpu_percent=cpu[-1],
            mem_percent=mem[-1],
            mem_used_gb=22.4,
            mem_total_gb=36.0,
            net_up_bps=up[-1],
            net_down_bps=down[-1],
            cpu_history=cpu,
            mem_history=mem,
            net_up_history=up,
            net_down_history=down,
            load_avg=(3.1, 2.8, 2.4),
            net_bytes_sent=int(3.3 * 1024**3 + sum(up) * 2),
            net_bytes_recv=int(2.3 * 1024**3 + sum(down) * 2),
            address="192.168.1.42",
            interface="en0",
        )

    def health_check(self, listeners: list[Listener]) -> None:
        """Fill in pretend health results, including one refused port and one 503."""
        for lst in listeners:
            if lst.protocol != "TCP" or lst.state != ListenerState.LISTEN:
                lst.health = HealthResult()
                continue
            r = HealthResult(
                checked=True,
                tcp_ok=True,
                tcp_latency_ms=0.6 + (lst.port % 7) * 0.3,
                checked_at=self.now,
            )
            if lst.port == 9100:
                r.tcp_ok, r.tcp_error = False, "refused"
            elif lst.port == 8080:
                r.http_path, r.http_status, r.http_latency_ms = "/wp-admin/install.php", 503, 41.0
            elif lst.port in (8000, 8001):
                r.http_path, r.http_status, r.http_latency_ms = "/healthz", 200, 3.2
            elif lst.port in (3000, 5173):
                r.http_note = "no health endpoint (12 paths tried: 404)"
            elif lst.port == 8025:
                r.http_path, r.http_status, r.http_latency_ms = "/api/v2/messages", 200, 5.0
            elif lst.port in (5432, 3306, 6379, 1025):
                r.http_note = f"TCP only ({lst.identity.role}, not probed for HTTP)"
            else:
                r.http_note = "TCP only (not HTTP)"
            lst.health = r
            self.health[f"{lst.key}:{lst.pid or 0}"] = r
