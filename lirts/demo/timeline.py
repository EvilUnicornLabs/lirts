"""The scripted timeline: what happens on the demo machine while you watch.

``tick`` is called once per refresh.  The ``_once`` events fire at a fixed
number of seconds after the world started; the crash loop and the cpu / traffic
wiggle are continuous.
"""

from __future__ import annotations

import math

from lirts.collectors.origin import Origin
from lirts.demo.catalog import DemoProcess
from lirts.demo.machine import DemoMachine
from lirts.models import SshSession


class DemoTimelineMixin(DemoMachine):
    """The scripted events and the continuous wiggle of the demo machine."""

    def tick(self, now: float) -> None:
        """Advance the pretend machine to ``now``; called once per refresh."""
        self.now = now
        t = now - self.started
        once = self._once
        if once("ssh", t >= 12):
            self.ssh_sessions = [
                SshSession(
                    self._pid(),
                    "bastion",
                    "203.0.113.7:22",
                    user="dev",
                    tty="ttys009",
                    create_time=now,
                    cmd="ssh dev@bastion",
                ),
            ]
            tunnel = self._pid()
            self._add(
                DemoProcess(
                    tunnel,
                    "ssh",
                    ["ssh", "-N", "-L", "15432:db.internal:5432", "dev@bastion"],
                    [(15432, "TCP")],
                    cwd="/Users/dev",
                    cpu=0.0,
                    mem=6,
                    origin=Origin(shell="zsh", shell_pid=911, terminal="Ghostty", tty="ttys009"),
                    created=now,
                    addresses=["127.0.0.1"],
                )
            )
            self.ssh_sessions.append(
                SshSession(
                    tunnel,
                    "bastion",
                    "203.0.113.7:22",
                    user="dev",
                    tty="ttys009",
                    forwards=["15432:db.internal:5432"],
                    create_time=now,
                    cmd="ssh -N -L 15432:db.internal:5432 dev@bastion",
                )
            )
            self.log.append("ssh session to bastion opened, tunnel on 15432")
        if once("api-restart", t >= 25):
            self.restart_port(8001)
            self.log.append("uvicorn on 8001 restarted (new PID)")
        if once("admin-stop", t >= 40):
            self.kill([3001])
            self.log.append("admin node server on 3001 stopped")
        if once("admin-back", t >= 75):
            self._add(
                DemoProcess(
                    self._pid(),
                    "node",
                    ["node", "admin/server.js"],
                    [(3002, "TCP")],
                    cwd="/Users/dev/code/shop",
                    cpu=0.2,
                    mem=95,
                    created=now,
                    origin=Origin(
                        shell="zsh",
                        shell_pid=912,
                        terminal="Cursor",
                        tty="ttys007",
                        git_branch="feature/cart",
                    ),
                )
            )
            self.log.append("admin node server back on 3002")
        if once("mysql-unhealthy", t >= 30):
            for c in self.containers:
                if c.name == "blog-mysql-1":
                    c.health = "unhealthy"
            self.log.append("blog-mysql-1 healthcheck failing")
        # crash loop: the worker restarts every 45 s
        cycle = int(t // 45)
        worker = next(c for c in self.containers if c.name == "jobs-worker-1")
        worker.restart_count = 4 + cycle
        worker.status = "restarting" if (t % 45) < 6 else "running"
        worker.started_at = self.started + cycle * 45
        pod = next(p for p in self.kube.pods if p.name.startswith("worker-"))
        pod.restarts = 7 + cycle
        pod.reason = "CrashLoopBackOff" if (t % 45) >= 6 else None
        pod.ready = 1 if pod.reason is None else 0
        # cpu / traffic wiggle
        for p in self.procs.values():
            base = {"node": 1.5, "python": 3.0, "postgres": 0.4, "com.docker.backend": 2.5}.get(
                p.name, 0.2
            )
            wave = math.sin(t / 7.0 + p.pid % 5) * 0.5 + 0.5
            p.cpu = round(base * (0.6 + wave), 1)
            if p.spiky and 50 <= (t % 120) <= 65:
                p.cpu = 88.0
            if p.rate_in or p.rate_out:
                factor = 0.4 + 0.6 * (math.sin(t / 9.0 + p.pid % 7) * 0.5 + 0.5)
                p.rate_in = max(0.0, p.rate_in if p.rate_in else 0.0)
                p.factor = factor
        for i, c in enumerate(self.containers):
            wave = math.sin(t / 6.0 + i) * 0.5 + 0.5
            c.cpu_percent = round((0.3 + i * 0.2) * (0.5 + wave), 1)
            c.net_rx_rate = round(
                1500.0 * wave + (30000.0 if c.service == "api" else 0.0) * wave, 1
            )
            c.net_tx_rate = round(900.0 * wave + (12000.0 if c.service == "web" else 0.0) * wave, 1)
            c.net_rx_bytes = int((c.net_rx_bytes or 0) + c.net_rx_rate * 2)
            c.net_tx_bytes = int((c.net_tx_bytes or 0) + c.net_tx_rate * 2)
        self.kube.fetched_at = now

    def _once(self, name: str, condition: bool) -> bool:
        if condition and name not in self._fired:
            self._fired.add(name)
            return True
        return False
