"""Shared fixtures.  Every test runs with HOME-style directories redirected to tmp."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from textual.widgets import DataTable

from lirts.config import load_config
from lirts.identity import ROLE_BACKEND, ROLE_FRONTEND
from lirts.models import (
    ContainerInfo,
    HttpProbe,
    Identity,
    Listener,
    ListenerProcess,
    MountInfo,
    Snapshot,
    SystemStats,
)
from lirts.topology_build import build_graph
from lirts.topology_store import TopologyStore
from lirts.tui.app import LirtsApp


@pytest.fixture(autouse=True)
def _no_real_machine(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Tests never open a browser, an editor or a file manager on the developer's machine.

    Every such call is recorded here instead; a test that wants to assert on it reads the list.
    """
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: opened.append(str(url)) or True)
    monkeypatch.setattr(
        "webbrowser.open_new_tab", lambda url, *a, **k: opened.append(str(url)) or True
    )
    monkeypatch.setattr("webbrowser.open_new", lambda url, *a, **k: opened.append(str(url)) or True)
    monkeypatch.setattr(
        "lirts.engine.actions.launch_detached",
        lambda cmd: opened.append(" ".join(map(str, cmd))),
    )
    return opened


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("LIRTS_CONFIG", raising=False)


def make_process(
    pid: int = 100,
    name: str = "node",
    cmdline: list[str] | None = None,
    addresses: list[str] | None = None,
    age: float = 60.0,
    **kw,
) -> ListenerProcess:
    return ListenerProcess(
        pid=pid,
        name=name,
        cmdline=cmdline if cmdline is not None else [name],
        addresses=addresses or ["127.0.0.1"],
        create_time=time.time() - age,
        **kw,
    )


def make_listener(
    port: int = 3000,
    protocol: str = "TCP",
    processes: list[ListenerProcess] | None = None,
    name: str = "node",
    cmdline: list[str] | None = None,
    container: ContainerInfo | None = None,
    identity: Identity | None = None,
    http: HttpProbe | None = None,
    **kw,
) -> Listener:
    if processes is None:
        processes = [make_process(pid=port + 10000, name=name, cmdline=cmdline)]
    lst = Listener(port=port, protocol=protocol, processes=processes, container=container, **kw)
    if container is not None:
        lst.source = "docker"
    if identity is not None:
        lst.identity = identity
    if http is not None:
        lst.http = http
    return lst


def make_container(
    name: str = "app-web-1",
    image: str = "nginx:latest",
    host_ports: list[int] | None = None,
    stack: str | None = "app",
    service: str | None = "web",
    **kw,
) -> ContainerInfo:
    host_ports = host_ports if host_ports is not None else [8080]
    return ContainerInfo(
        id="a" * 64,
        short_id="a" * 12,
        name=name,
        image=image,
        stack=stack,
        service=service,
        host_ports=host_ports,
        port_map=dict.fromkeys(host_ports, "80/TCP"),
        started_at=time.time() - 3600,
        mounts=kw.pop(
            "mounts",
            [MountInfo(type="volume", source="app_data", destination="/data", name="app_data")],
        ),
        **kw,
    )


class FakeDocker:
    def logs(self, cid, tail=200):
        return "log line 1\nlog line 2\n"

    def exec_command(self, cid):
        return ["docker", "exec", "-it", cid, "sh"]

    def stop(self, cid):
        return True, "stopped"

    def restart(self, cid):
        return True, "restarted"


class FakeEngine:
    def __init__(self) -> None:
        self.show_udp = False
        self.hide_system = False
        self.docker = FakeDocker()
        self.last_session_diff: dict[str, list[str]] = {}
        self.killed: list[tuple[list[int], bool]] = []
        self.refreshes = 0
        self.listeners = [
            make_listener(
                port=5173,
                name="node",
                cmdline=["node", "vite"],
                identity=Identity("Vite dev server", ROLE_FRONTEND, 0.95, project="shop"),
            ),
            make_listener(
                port=8000,
                name="python",
                cmdline=["python", "app.py"],
                identity=Identity("FastAPI", ROLE_BACKEND, 0.9),
                processes=[make_process(pid=42, name="python", cpu_percent=55.0, memory_mb=700.0)],
            ),
            make_listener(
                port=6379,
                name="com.docker.backend",
                processes=[make_process(pid=7, name="com.docker.backend")],
                container=make_container(name="app-redis-1", image="redis:7", host_ports=[6379]),
                identity=Identity("Redis", "cache", 0.95),
            ),
        ]

        from lirts.history import HistoryStore
        from lirts.models import Event

        self.history = HistoryStore()
        self.topology = TopologyStore()
        self.snapshot = Snapshot()
        self.sources = {"local", "docker", "ssh", "kubernetes"}
        self.prober = SimpleNamespace(enabled=True, interval=10.0)
        self.kube = SimpleNamespace(enabled=False, kubectl=None, snapshot=lambda: None)
        self.history.events.append(Event(1.0, "error", "[!] port conflict on 5432/TCP (old)", 5432))

    def start(self) -> None:
        pass

    def close(self) -> None:
        pass

    async def refresh(self) -> Snapshot:
        self.refreshes += 1
        self.snapshot = Snapshot(
            listeners=list(self.listeners),
            system=SystemStats(cpu_percent=10.0, mem_percent=40.0),
            docker_available=True,
            container_count=1,
            events=self.history.recent_events(12),
        )
        self.topology.observe(build_graph(self.snapshot))
        return self.snapshot

    def kill_and_wait(self, pids, force=False, timeout=3.0):
        self.killed.append((list(pids), force))
        return [(pid, True, "terminated") for pid in pids]

    def signal_name(self, force: bool) -> str:
        return "SIGKILL" if force else "SIGTERM"

    def open_url(self, lst) -> str:
        return f"http://localhost:{lst.port}/"

    def set_sources(self, names):
        self.sources = set(names)

    async def check_health_now(self, listeners):
        from lirts.models import HealthResult

        for lst in listeners:
            if lst.port == 8000:
                lst.health = HealthResult(checked=True, tcp_ok=False, tcp_error="refused")
            elif lst.port == 5173:
                lst.health = HealthResult(
                    checked=True,
                    tcp_ok=True,
                    tcp_latency_ms=1.2,
                    http_path="/healthz",
                    http_status=200,
                )
            else:
                lst.health = HealthResult(
                    checked=True, tcp_ok=True, tcp_latency_ms=0.4, http_note="TCP only (cache)"
                )
        return listeners


@pytest.fixture
def app() -> LirtsApp:
    cfg = load_config()
    cfg["refresh_interval"] = 60.0
    cfg["insights"]["highlight_new_minutes"] = 0  # fixtures are "young"; opt in per test
    return LirtsApp(FakeEngine(), cfg)  # type: ignore[arg-type]  # FakeEngine stands in for Engine


async def wait_rows(app: LirtsApp, pilot: Any) -> DataTable:
    table = app.query_one("#table", DataTable)
    for _ in range(40):
        await pilot.pause()
        if table.row_count:
            break
        await asyncio.sleep(0.05)
    return table
