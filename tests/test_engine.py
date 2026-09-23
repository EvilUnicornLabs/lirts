from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from lirts.collectors.docker import DockerProvider
from lirts.collectors.http import HttpProber
from lirts.collectors.proxies import Route
from lirts.collectors.system import SystemSampler
from lirts.config import load_config
from lirts.engine import Engine
from lirts.engine import pipeline as pipeline_mod
from lirts.history import save_last_snapshot
from lirts.identity import ROLE_BACKEND
from lirts.models import Identity, SystemStats
from tests.conftest import make_container, make_listener, make_process


@pytest.fixture
def fake_world(monkeypatch):
    """Engine with all collectors replaced by in-memory fixtures."""
    state: dict = {"listeners": [], "containers": []}

    def fake_collect(sampler, include_udp=False):
        return [copy_listener(x) for x in state["listeners"] if include_udp or x.protocol == "TCP"]

    monkeypatch.setattr(pipeline_mod, "collect_listeners", fake_collect)

    def fake_containers(self):
        self.available = True
        return list(state["containers"])

    monkeypatch.setattr(DockerProvider, "list_containers", fake_containers)
    monkeypatch.setattr(
        SystemSampler, "sample", lambda self: SystemStats(cpu_percent=12.0, mem_percent=50.0)
    )

    async def no_probe(self, listeners):
        for lst in listeners:
            lst.http.attempted = False

    monkeypatch.setattr(HttpProber, "enrich", no_probe)
    return state


def copy_listener(lst):
    import copy

    return copy.deepcopy(lst)


def make_engine(tmp_path: Path, **overrides) -> Engine:
    cfg = load_config()
    cfg["bandwidth"]["mode"] = "off"
    cfg.update(overrides)
    return Engine(cfg, state_root=tmp_path / "state")


async def test_refresh_pipeline(fake_world, tmp_path: Path) -> None:
    fake_world["listeners"] = [
        make_listener(port=5173, name="node", cmdline=["node", "vite"]),
        make_listener(port=6379, name="com.docker.backend", cmdline=["com.docker.backend"]),
        make_listener(port=5353, protocol="UDP", name="mDNSResponder", cmdline=["mDNSResponder"]),
    ]
    fake_world["containers"] = [
        make_container(
            name="app-redis-1", image="redis:7", host_ports=[6379, 9999], service="redis"
        ),
        make_container(
            name="app-internal-1",
            image="ghcr.io/x/y",
            host_ports=[],
            service="worker",
            internal_ports=[8080],
        ),
    ]
    engine = make_engine(tmp_path)
    snap = await engine.refresh()
    keys = [x.key for x in snap.listeners]
    assert keys == ["5173/TCP", "6379/TCP", "9999/TCP"]  # UDP hidden, docker-only 9999 added
    redis = snap.by_port(6379)
    assert redis is not None and redis.is_docker and redis.container.name == "app-redis-1"
    assert redis.identity.service == "Redis"
    docker_only = snap.by_port(9999)
    assert (
        docker_only is not None and docker_only.processes == [] and docker_only.source == "docker"
    )
    vite = snap.by_port(5173)
    assert vite is not None and vite.identity.service == "Vite dev server"
    assert snap.system.cpu_percent == 12.0
    assert snap.docker_available
    assert snap.container_count == 2
    assert snap.refresh_ms >= 0
    assert engine.last_session_diff == {}

    engine.show_udp = True
    snap = await engine.refresh()
    assert "5353/UDP" in [x.key for x in snap.listeners]

    engine.hide_system = True
    snap = await engine.refresh()
    assert "5353/UDP" not in [x.key for x in snap.listeners]

    # Unpublished container ports appear only when configured.
    engine.config["docker"]["show_unpublished"] = True
    snap = await engine.refresh()
    internal = snap.by_key("8080/TCP")
    assert internal is not None and internal.state == "INTERNAL"
    engine.close()


async def test_events_and_session_diff(fake_world, tmp_path: Path) -> None:
    fake_world["listeners"] = [make_listener(port=3000, name="node", cmdline=["node", "server.js"])]
    engine = make_engine(tmp_path)
    await engine.refresh()
    fake_world["listeners"] = []
    snap = await engine.refresh()
    assert any("stopped on 3000/TCP" in e.message for e in snap.events)
    engine.close()  # persists "last" snapshot (empty) and history
    assert (tmp_path / "state" / "history.json").exists()

    save_last_snapshot(
        tmp_path / "state",
        [make_listener(port=4000, identity=Identity("Python app", ROLE_BACKEND, 0.5))],
    )
    fake_world["listeners"] = [make_listener(port=3000, name="node", cmdline=["node", "server.js"])]
    engine2 = make_engine(tmp_path)
    await engine2.refresh()
    assert engine2.last_session_diff is not None
    assert engine2.last_session_diff["missing"] == ["Python app on 4000/TCP"]
    assert engine2.last_session_diff["added"] == ["Node.js app on 3000/TCP"]
    engine2.close()


async def test_concurrent_refresh_returns_same_snapshot(fake_world, tmp_path: Path) -> None:
    fake_world["listeners"] = [make_listener(port=3000)]
    engine = make_engine(tmp_path)
    a, b = await asyncio.gather(engine.refresh(), engine.refresh())
    assert a is b or len(a.listeners) == len(b.listeners)
    engine.close()


def test_open_url(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    plain = make_listener(port=3000)
    assert engine.open_url(plain) == "http://localhost:3000/"
    tls = make_listener(port=443)
    tls.hosts = ["local.example.test"]
    assert engine.open_url(tls) == "https://local.example.test/"
    http80 = make_listener(port=80)
    assert engine.open_url(http80) == "http://localhost/"
    bound = make_listener(port=8080, processes=[make_process(addresses=["192.168.1.5"])])
    assert engine.open_url(bound) == "http://192.168.1.5:8080/"
    v6 = make_listener(port=8080, processes=[make_process(addresses=["fe80::1"])])
    assert engine.open_url(v6) == "http://[fe80::1]:8080/"
    from lirts.models import HttpProbe

    https = make_listener(port=8443, http=HttpProbe(attempted=True, ok=True, scheme="https"))
    assert engine.open_url(https) == "https://localhost:8443/"


def test_kill_nonexistent_and_denied(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    results = engine.kill_and_wait([2**22 + 4321])
    assert results == [(2**22 + 4321, True, "already gone")]
    denied = engine.kill([1])  # launchd / init: permission denied for a normal user
    assert denied[0][1] is False
    assert engine.signal_name(True) == "SIGKILL" and engine.signal_name(False) == "SIGTERM"


def test_refresh_sync(fake_world, tmp_path: Path) -> None:
    fake_world["listeners"] = [make_listener(port=3000)]
    engine = make_engine(tmp_path)
    snap = engine.refresh_sync()
    assert len(snap.listeners) == 1
    engine.close()


async def test_stopped_rows_linger(fake_world, tmp_path: Path) -> None:
    fake_world["listeners"] = [
        make_listener(port=3000, name="node", cmdline=["node", "vite"]),
        make_listener(port=4000, name="python", cmdline=["python", "app.py"]),
    ]
    engine = make_engine(tmp_path)
    await engine.refresh()
    fake_world["listeners"] = fake_world["listeners"][:1]
    snap = await engine.refresh()
    ghost = snap.by_key("4000/TCP")
    assert ghost is not None and ghost.state == "STOPPED"
    assert ghost.identity.service == "Python app"
    assert ghost.status == "warning" and ghost.insights[0].code == "stopped"
    assert ghost.processes == [] and ghost.pid is None
    engine.config["insights"]["show_stopped_minutes"] = 0
    snap = await engine.refresh()
    assert snap.by_key("4000/TCP") is None
    engine.close()


def test_ssh_sessions_become_rows(tmp_path) -> None:
    import os

    from lirts.config import DEFAULT_CONFIG
    from lirts.engine import Engine
    from lirts.models import SshSession

    engine = Engine(dict(DEFAULT_CONFIG), state_root=tmp_path)
    engine.sampler.begin_cycle({})
    sessions = [
        SshSession(
            pid=os.getpid(),
            host="box",
            remote="10.0.0.5:22",
            user="me",
            tty="ttys001",
            cmd="ssh me@box",
        ),
        SshSession(
            pid=os.getpid(),
            host="db",
            remote="10.0.0.6:2222",
            forwards=["5433:localhost:5432"],
            cmd="ssh -L 5433:localhost:5432 db",
        ),
    ]
    rows = engine._ssh_rows(sessions)
    assert [r.port for r in rows] == [22, 2222]
    assert all(r.state == "SSH" and r.source == "ssh" and r.ssh is not None for r in rows)
    assert rows[0].key == f"ssh:{os.getpid()}" and rows[0].identity.service == "ssh me@box"
    assert (
        rows[0].identity.role == "ssh" and rows[0].pid == os.getpid() and rows[0].connections == 1
    )
    assert rows[1].identity.role == "tunnel" and "5433:localhost:5432" in rows[1].identity.service
    assert "box" in rows[0].search_blob() and "ssh" in rows[0].search_blob()
    engine.close()


def test_sources_gate_collection(tmp_path) -> None:
    from lirts.config import DEFAULT_CONFIG, validate
    from lirts.engine import Engine

    cfg = validate({**DEFAULT_CONFIG, "sources": ["local", "bogus"]})
    assert cfg["sources"] == ["local"]
    engine = Engine(cfg, state_root=tmp_path)
    assert engine.docker.enabled is False and engine.kube.mode == "off"
    engine.set_sources(["local", "docker", "kubernetes"])
    assert engine.docker.enabled is True and engine.kube.mode == "auto"
    engine.set_sources(["docker"])
    assert engine.docker.enabled is True and engine.kube.mode == "off"
    engine.close()


def test_open_path_uses_the_configured_editor(tmp_path, _no_real_machine) -> None:
    from lirts.config import DEFAULT_CONFIG
    from lirts.engine import Engine

    calls = _no_real_machine  # the conftest stub records every launch instead of running it
    engine = Engine({**DEFAULT_CONFIG, "editor": "python3 -c pass"}, state_root=tmp_path)
    ok, msg = engine.open_path("/tmp/proj")
    assert ok and calls[-1] == "python3 -c pass /tmp/proj"
    engine.config["editor"] = "no-such-editor-xyz"
    ok, msg = engine.open_path("/tmp/proj")
    assert not ok and "not found on PATH" in msg
    engine.config["editor"] = ""
    ok, msg = engine.open_path("/tmp/proj")
    assert ok and calls[-1].endswith(" /tmp/proj")
    engine.close()


def test_doctor_editor_check(tmp_path) -> None:
    from lirts.doctor import check_editor

    path = tmp_path / "c.yaml"
    path.write_text("editor: python3\n")
    assert check_editor(path).status == "ok"  # type: ignore[union-attr]  # check_editor returns a result for a set editor
    path.write_text("editor: no-such-editor-xyz\n")
    assert check_editor(path).status == "warn"  # type: ignore[union-attr]  # check_editor returns a result for a set editor
    path.write_text("editor: ''\n")
    assert check_editor(path) is None


def test_all_namespaces_active_follows_the_config(tmp_path: Path) -> None:
    cfg = load_config()
    cfg["kubernetes"] = {**cfg["kubernetes"], "all_namespaces": True}
    engine = Engine(cfg, state_root=tmp_path)

    assert engine.all_namespaces_active() is True

    engine.config["kubernetes"]["all_namespaces"] = False
    assert engine.all_namespaces_active() is False
    engine.close()


def test_can_sudo_hint_is_empty_for_root(monkeypatch, tmp_path: Path) -> None:
    engine = Engine(load_config(), state_root=tmp_path)

    monkeypatch.setattr("os.geteuid", lambda: 0)
    assert engine.can_sudo_hint() == ""

    monkeypatch.setattr("os.geteuid", lambda: 501)
    assert "sudo" in engine.can_sudo_hint()
    engine.close()


def test_refresh_routes_asks_the_proxies_only_when_they_are_enabled(
    monkeypatch, tmp_path: Path
) -> None:
    from lirts.collectors.proxies_route import ProxyKind
    from lirts.engine import routes as routes_mod

    route = Route(ProxyKind.NGINX, 80, ["shop.local"], "127.0.0.1:5173", 5173)
    monkeypatch.setattr(routes_mod, "discover_routes", lambda: ([route], ["nginx -T: 1 route(s)"]))

    cfg = load_config()
    engine = Engine(cfg, state_root=tmp_path)
    engine.refresh_routes()
    assert engine.routes == [route] and engine.route_notes == ["nginx -T: 1 route(s)"]
    engine.close()

    cfg["proxies"] = {"enabled": False}
    off = Engine(cfg, state_root=tmp_path)
    off.refresh_routes()
    assert off.routes == [] and off.route_notes == []
    off.close()


async def test_the_refresh_builds_a_port_memory_and_the_squatter_follows_it(
    fake_world, tmp_path: Path
) -> None:
    fake_world["listeners"] = [make_listener(port=3000, name="node", cmdline=["node", "server.js"])]
    engine = make_engine(tmp_path)
    try:
        await engine.refresh()
        assert engine.port_memory.usual(3000) is None  # one refresh proves nothing

        node = engine.topology.graph.node_for_row("3000/TCP")
        assert node is not None
        node.days_seen = ["2026-09-18", "2026-09-19", "2026-09-20"]

        fake_world["listeners"] = [
            make_listener(port=3000, name="python", cmdline=["python3", "-m", "http.server"])
        ]
        snap = await engine.refresh()
    finally:
        engine.close()

    usual = engine.port_memory.usual(3000)
    assert usual is not None and usual.label == "Node.js app"
    row = snap.by_port(3000)
    assert row is not None
    squatter = next(i for i in row.insights if i.code == "squatter")
    assert squatter.message.startswith("3000 is usually Node.js app; now Python static server")
    assert squatter.suggestion == "check whether Node.js app failed to start"


async def test_the_history_remembers_which_service_held_a_port(fake_world, tmp_path: Path) -> None:
    fake_world["listeners"] = [make_listener(port=5432, name="postgres", cmdline=["postgres"])]
    engine = make_engine(tmp_path)
    try:
        await engine.refresh()
        await engine.refresh()
    finally:
        engine.close()

    assert engine.history.entries["5432/TCP"].services_seen == {"PostgreSQL": 2}
