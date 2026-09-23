"""State of the pretend machine and the actions that change it.

:class:`DemoMachine` holds the processes, containers, cluster and routes that
the demo world is seeded with (from :mod:`lirts.demo.catalog` and
:mod:`lirts.demo.cluster`) and answers the kill / restart / stop actions the
demo engine forwards to it.
"""

from __future__ import annotations

import dataclasses
import time

from lirts.collectors.kube import KubeForward, KubeState
from lirts.collectors.proxies import Route
from lirts.constants import SECONDS_PER_HOUR
from lirts.demo.catalog import DOCKER_PID, DemoProcess, demo_containers, demo_processes
from lirts.demo.cluster import (
    demo_deployments,
    demo_forwards,
    demo_pods,
    demo_routes,
    demo_services,
)
from lirts.models import ContainerHealth, ContainerInfo, HealthResult, SshSession

# Seeded processes look as if they were started a working day ago.
DEMO_PROCESS_AGE_HOURS = 5


class DemoMachine:
    """State of the pretend machine and the actions the demo engine forwards to it."""

    def __init__(self, started: float | None = None) -> None:
        self.started = started or time.time()
        self.now = self.started
        self.next_pid = 60000
        self.procs: dict[int, DemoProcess] = {}
        self.containers: list[ContainerInfo] = []
        self.ssh_sessions: list[SshSession] = []
        self.kube = KubeState(available=True, context="demo-cluster", namespace="shop")
        self.kube_forwards: list[KubeForward] = []
        self.routes: list[Route] = []
        self.hosts_names = ["shop.local", "api.shop.local", "blog.local"]
        self.health: dict[str, HealthResult] = {}
        self.log: list[str] = []
        self._fired: set[str] = set()
        self._build()

    # ----- building -----------------------------------------------------------------

    def _pid(self) -> int:
        self.next_pid += 1
        return self.next_pid

    def _add(self, proc: DemoProcess) -> DemoProcess:
        if not proc.created:
            proc.created = self.started - DEMO_PROCESS_AGE_HOURS * SECONDS_PER_HOUR
        self.procs[proc.pid] = proc
        return proc

    def _build(self) -> None:
        t0 = self.started
        for proc in demo_processes(t0):
            self._add(proc)
        self.containers = demo_containers(t0)
        self.kube.pods = demo_pods(t0)
        self.kube.deployments = demo_deployments(t0)
        self.kube.services = demo_services()
        self.kube.fetched_at = t0
        self.kube_forwards = demo_forwards(t0)
        self.routes = demo_routes()

    # ----- actions ----------------------------------------------------------------------

    def kill(self, pids: list[int]) -> list[tuple[int, bool, str]]:
        """Remove the pretend processes, refusing the Docker proxy as the real engine does."""
        out: list[tuple[int, bool, str]] = []
        for pid in pids:
            if pid == DOCKER_PID:
                out.append((pid, False, "the Docker proxy: stop the container instead (x)"))
            elif pid in self.procs:
                del self.procs[pid]
                out.append((pid, True, "terminated"))
            else:
                out.append((pid, False, "no such process"))
        return out

    def restart_port(self, port: int) -> int | None:
        """Replace the process serving ``port`` with a fresh one; returns its new PID."""
        for pid, p in list(self.procs.items()):
            if any(pt == port for pt, _ in p.ports):
                del self.procs[pid]
                new = self._pid()
                self.procs[new] = dataclasses.replace(
                    p,
                    pid=new,
                    created=self.now,
                    cmdline=list(p.cmdline),
                    ports=list(p.ports),
                    addresses=list(p.addresses),
                    remotes=list(p.remotes),
                )
                return new
        return None

    def stop_container(self, container_id: str) -> tuple[bool, str]:
        """Remove a pretend container and the ports the Docker proxy published for it."""
        for c in self.containers:
            if c.id == container_id:
                self.containers.remove(c)
                docker = self.procs.get(DOCKER_PID)
                if docker:
                    docker.ports = [pp for pp in docker.ports if pp[0] not in c.host_ports]
                return True, "stopped"
        return False, "no such container"

    def restart_container(self, container_id: str) -> tuple[bool, str]:
        """Bump a pretend container's restart count and clear an unhealthy check."""
        for c in self.containers:
            if c.id == container_id:
                c.restart_count += 1
                c.started_at = self.now
                if c.health == ContainerHealth.UNHEALTHY:
                    c.health = "healthy"
                return True, "restarted"
        return False, "no such container"
