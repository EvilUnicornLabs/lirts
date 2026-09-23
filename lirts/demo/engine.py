"""The demo engine: the real pipeline over the pretend machine.

:func:`demo_config` switches off everything that would touch the host and
:class:`DemoEngine` swaps the collectors for the demo providers.
"""

from __future__ import annotations

import copy
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from lirts.config_view import ConfigView
from lirts.constants import KILL_WAIT_SECONDS, SECONDS_PER_DAY
from lirts.demo.catalog import DOCKER_PID, SHELL_PID, demo_response
from lirts.demo.providers import DemoBandwidth, DemoDocker, DemoHosts, DemoKube, DemoSampler
from lirts.demo.world import DemoWorld
from lirts.engine import Engine
from lirts.insights import analyse
from lirts.models import ContainerInfo, HttpProbe, Listener, SystemStats
from lirts.topology import Node, NodeKind


def demo_config(config: dict[str, Any]) -> dict[str, Any]:
    """The user's config with everything that would touch the host switched off."""
    cfg = copy.deepcopy(config)
    cfg.setdefault("docker", {})["enabled"] = True
    cfg.setdefault("kubernetes", {})["enabled"] = "off"
    cfg.setdefault("bandwidth", {})["mode"] = "off"
    cfg.setdefault("http_probe", {})["enabled"] = False
    cfg.setdefault("proxies", {})["enabled"] = True
    cfg.setdefault("history", {})["persist"] = False
    cfg["_demo"] = True
    return cfg


class DemoEngine(Engine):
    """The real pipeline (identity, insights, history, patterns) over a pretend machine."""

    def __init__(self, config: dict[str, Any], world: DemoWorld | None = None) -> None:
        self._tmp = tempfile.mkdtemp(prefix="lirts-demo-")
        super().__init__(demo_config(config), state_root=Path(self._tmp))
        self.world = world or DemoWorld()
        self.sampler = DemoSampler(self.world)
        self.pid_alive = self._pretend_pid_alive
        self._seed_port_memory()
        # The demo providers only mimic the collectors' surface; mypy sees the real types.
        self.docker = DemoDocker(self.world)  # type: ignore[assignment]  # stand-in provider
        self.kube = DemoKube(self.world)  # type: ignore[assignment]  # stand-in provider
        self.bandwidth = DemoBandwidth(  # type: ignore[assignment]  # stand-in provider
            self.world, connections=ConfigView(config).bandwidth.connections
        )
        self.hosts = DemoHosts(self.world)  # type: ignore[assignment]  # stand-in provider
        self.demo = True

    # lifecycle: no threads, no files
    def start(self) -> None:
        """Nothing to start: the demo world has no collectors."""
        self._started = True

    def close(self) -> None:
        """Remove the throwaway state directory of this demo run."""
        shutil.rmtree(self._tmp, ignore_errors=True)

    # collection steps
    async def _collect(self) -> tuple[list[Listener], list[ContainerInfo]]:
        self.world.tick(time.time())
        listeners = self.world.listeners()
        self.sampler.edges = self.world.edges()
        self.sampler.ssh_sessions = list(self.world.ssh_sessions)
        return listeners, list(self.world.containers)

    async def _enrich_origins(self, listeners: list[Listener]) -> None:
        return None  # origins are part of the world

    async def _probe(self, listeners: list[Listener]) -> None:
        for lst in listeners:
            if lst.protocol != "TCP":
                continue
            if lst.port in (3000, 5173, 8000, 8001, 8025, 8080, 80, 8090, 9090, 3002, 3001):
                server = {
                    80: "nginx/1.27",
                    8000: "uvicorn",
                    8001: "uvicorn",
                    8080: "Apache/2.4",
                    8090: "SimpleHTTP/0.6",
                }.get(lst.port)
                status = 503 if lst.port == 8080 else 200
                answer = demo_response(lst.port)
                lst.http = HttpProbe(
                    attempted=True,
                    ok=True,
                    scheme="http",
                    status=status,
                    latency_ms=3.0 + (lst.port % 11),
                    server=server,
                    content_type=answer.content_type,
                    kind=answer.kind,
                    dev_server=answer.dev_server,
                    probed_at=self.world.now,
                )
            elif lst.port in (5432, 6379, 3306, 1025, 15432):
                lst.http = HttpProbe(attempted=False)
            else:
                lst.http = HttpProbe(
                    attempted=True, ok=False, error="not HTTP", probed_at=self.world.now
                )

    def _health_attach(self, listeners: list[Listener]) -> None:
        for lst in listeners:
            r = self.world.health.get(f"{lst.key}:{lst.pid or 0}")
            if r is not None:
                lst.health = r

    async def _load_routes(self) -> None:
        self._routes_loaded = True
        self.routes = list(self.world.routes)
        self.route_notes = ["demo: 3 nginx routes"]

    def refresh_routes(self) -> None:
        """Re-read the pretend nginx routes."""
        self._routes_loaded = True
        self.routes = list(self.world.routes)
        self.route_notes = ["demo: 3 nginx routes"]

    async def _sample_system(self) -> SystemStats:
        return self.world.system()

    def _kube_state(self) -> Any:
        return self.kube.snapshot() if self.kube.enabled else None

    # actions
    def kill(self, pids: list[int], force: bool = False) -> list[tuple[int, bool, str]]:
        """Kill pretend processes in the demo world."""
        return self.world.kill(list(pids))

    def kill_and_wait(
        self, pids: list[int], *, force: bool = False, timeout: float = KILL_WAIT_SECONDS
    ) -> list[tuple[int, bool, str]]:
        """Kill pretend processes; nothing to wait for in the demo world."""
        return self.world.kill(list(pids))

    def restart_process(self, listener: Listener, force: bool = False) -> tuple[bool, str]:
        """Replace the pretend process behind a row with a fresh one."""
        new = self.world.restart_port(listener.port)
        if new is None:
            return False, "no such process (demo)"
        return True, f"started PID {new} (demo)"

    async def check_health_now(self, listeners: list[Listener] | None = None) -> list[Listener]:
        """Fill in pretend health results and re-run the insight rules over them."""
        targets = listeners if listeners is not None else list(self.snapshot.listeners)
        self.world.health_check(targets)
        now = time.time()
        for lst in targets:
            analyse(lst, cfg=self.cfg, now=now, memory=self.port_memory, alive=self.pid_alive)
        return targets

    def open_path(self, path: str) -> tuple[bool, str]:
        """Refuse to open anything: the demo never touches the host."""
        return False, f"demo mode: would open {path}"

    def set_sources(self, names: list[str]) -> None:
        """Apply a new ``sources`` list to the pretend providers."""
        self.sources = set(names)
        self.docker.enabled = "docker" in self.sources
        self.kube.mode = "on" if "kubernetes" in self.sources else "off"

    def _pretend_pid_alive(self, pid: int) -> bool:
        """Liveness on the pretend machine: its shell, the docker backend and its own processes."""
        return pid in (SHELL_PID, DOCKER_PID) or pid in self.world.procs

    def _seed_port_memory(self) -> None:
        """Give the pretend machine a past, so "what usually runs here" has something to say."""
        now = time.time()
        days = [
            time.strftime("%Y-%m-%d", time.localtime(now - n * SECONDS_PER_DAY))
            for n in (4, 3, 2, 1)
        ]
        remembered = [
            Node(
                id="ctr:shop/db",
                kind=NodeKind.DB,
                label="PostgreSQL",
                project="shop",
                ports=[5432],
                live=False,
                first_seen=now - 6 * SECONDS_PER_DAY,
                last_seen=now - SECONDS_PER_DAY,
                days_seen=days,
                usual=True,
            ),
            Node(
                id="svc:shop/gateway",
                kind=NodeKind.BACKEND,
                label="gateway",
                project="shop",
                ports=[8080],
                live=False,
                first_seen=now - 6 * SECONDS_PER_DAY,
                last_seen=now - SECONDS_PER_DAY,
                days_seen=days,
                usual=True,
            ),
            Node(
                id="ctr:lab/pg",
                kind=NodeKind.DB,
                label="Postgres 14",
                project="lab",
                ports=[5432],
                live=False,
                first_seen=now - 6 * SECONDS_PER_DAY,
                last_seen=now - 2 * SECONDS_PER_DAY,
                days_seen=days[:3],
                usual=True,
            ),
        ]
        for node in remembered:
            self.topology.graph.nodes[node.id] = node
