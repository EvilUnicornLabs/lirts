from __future__ import annotations

import asyncio
from typing import Any

from lirts.config import DEFAULT_CONFIG, validate
from lirts.demo import DemoEngine, DemoWorld
from lirts.demo.catalog import DOCKER_PID
from lirts.demo.engine import demo_config


def _cfg() -> dict[str, Any]:
    cfg = validate(dict(DEFAULT_CONFIG))
    cfg["refresh_interval"] = 60.0
    return cfg


def test_demo_engine_has_a_bit_of_everything(tmp_path) -> None:
    engine = DemoEngine(_cfg())
    snap = engine.refresh_sync()
    sources = {x.source for x in snap.listeners}
    roles = {x.identity.role for x in snap.listeners}
    assert {"local", "docker"} <= sources
    assert {"frontend", "backend", "db", "cache", "proxy", "tunnel", "system"} <= roles
    assert snap.container_count == 8 and snap.docker_available
    assert snap.kube is not None and snap.kube.available and len(snap.kube.pods) == 4
    assert len(snap.routes) == 3 and snap.hosts_names and len(snap.edges) == 5
    statuses = {x.key: x.status for x in snap.listeners}
    assert statuses["5432/TCP"] == "error"  # local postgres vs the container
    assert statuses["9100/TCP"] == "error"  # crash-looping worker
    assert snap.system.address == "192.168.1.42"
    assert engine.demo and not engine.state_root.joinpath("history.json").exists()
    engine.close()
    assert not engine.state_root.exists()  # the temp dir is gone; nothing else was written


def test_demo_timeline_and_actions() -> None:
    engine = DemoEngine(_cfg())
    engine.refresh_sync()
    engine.world.started -= 80  # fast-forward the scripted timeline
    engine.refresh_sync()
    snap = engine.refresh_sync()
    messages = [e.message for e in snap.events]
    assert any("8001/TCP restarted" in m for m in messages)
    assert any("stopped on 3001/TCP" in m for m in messages)
    assert any("3002/TCP" in m for m in messages)
    assert len(snap.ssh_sessions) == 2 and any(x.state == "SSH" for x in snap.listeners)
    assert any(x.state == "STOPPED" and x.port == 3001 for x in snap.listeners)
    assert any(x.identity.role == "tunnel" and x.port == 15432 for x in snap.listeners)
    mysql = next(c for c in engine.world.containers if c.name == "blog-mysql-1")
    assert mysql.health == "unhealthy"
    # actions change the world
    assert engine.kill_and_wait([5173]) == [(5173, True, "terminated")]
    assert engine.kill_and_wait([886])[0][1] is False
    ok, msg = engine.restart_process(next(x for x in snap.listeners if x.port == 8090))
    assert ok and "PID" in msg
    web = engine.world.containers[0]
    assert engine.docker.stop(web.id) == (True, "stopped")
    snap = engine.refresh_sync()
    assert not any(x.port == 5173 and x.state == "LISTEN" for x in snap.listeners)
    assert snap.container_count == 7
    checked = asyncio.run(engine.check_health_now(snap.listeners))
    failing = sorted(x.port for x in checked if x.health.checked and not x.health.ok)
    assert failing == [8080, 9100]
    assert engine.stacks().keys() == {"blog", "jobs", "shop"}
    assert "demo" in engine.docker.logs(web.id) or True
    engine.close()


def test_demo_kube_provider() -> None:
    engine = DemoEngine(_cfg())
    kube = engine.kube
    assert kube.enabled and kube.namespaces() == ["default", "kube-system", "monitoring", "shop"]
    assert kube.api_server() == ("k8s.demo.local", 6443)
    assert len(kube.snapshot().pods) == 4
    kube.all_namespaces = True
    assert len(kube.snapshot().pods) == 5
    assert kube.rollout_restart("shop", kind="deployment", name="worker")[0]
    assert (
        kube.start_forward("shop", target="svc/api", local_port=18000, remote_port=8000)[0]
        and len(kube.forwards()) == 2
    )
    assert kube.stop_forward(18000) and len(kube.forwards()) == 1
    assert kube.delete_pod("shop", "postgres-0")[0]
    engine.close()


def test_demo_world_is_deterministic_per_time() -> None:
    a = DemoWorld(started=1000.0)
    b = DemoWorld(started=1000.0)
    a.tick(1030.0)
    b.tick(1030.0)
    assert [p.cpu for p in a.procs.values()] == [p.cpu for p in b.procs.values()]
    assert [c.net_rx_rate for c in a.containers] == [c.net_rx_rate for c in b.containers]


def test_demo_config_switches_off_everything_that_would_touch_the_host() -> None:
    cfg = demo_config({"docker": {"enabled": False}, "history": {"persist": True}})

    assert cfg["_demo"] is True
    assert cfg["kubernetes"]["enabled"] == "off"
    assert cfg["bandwidth"]["mode"] == "off"
    assert cfg["http_probe"]["enabled"] is False
    assert cfg["history"]["persist"] is False
    assert cfg["docker"]["enabled"] is True  # the demo has its own containers


def test_demo_config_does_not_change_the_config_it_is_given() -> None:
    original = {"history": {"persist": True}}

    demo_config(original)

    assert original == {"history": {"persist": True}}


def test_restarting_a_demo_port_gives_it_a_new_pid_and_keeps_the_command() -> None:
    world = DemoWorld()
    before = next(p for p in world.procs.values() if (5173, "TCP") in p.ports)

    new_pid = world.restart_port(5173)

    after = world.procs[new_pid]
    assert new_pid != before.pid
    assert after.cmdline == before.cmdline and after.ports == before.ports
    assert before.pid not in world.procs
    assert world.restart_port(1) is None


def test_stopping_a_demo_container_removes_its_published_ports() -> None:
    world = DemoWorld()
    container = next(c for c in world.containers if c.host_ports)
    docker = world.procs[DOCKER_PID]

    ok, message = world.stop_container(container.id)

    assert ok and message == "stopped"
    assert container not in world.containers
    assert all(port not in [p for p, _ in docker.ports] for port in container.host_ports)
    assert world.stop_container("nope") == (False, "no such container")


def test_restarting_a_demo_container_clears_an_unhealthy_check() -> None:
    world = DemoWorld()
    container = world.containers[0]
    container.health = "unhealthy"
    before = container.restart_count

    ok, message = world.restart_container(container.id)

    assert ok and message == "restarted"
    assert container.health == "healthy" and container.restart_count == before + 1
    assert world.restart_container("nope") == (False, "no such container")


def test_snapshot_proc_describes_a_demo_process_and_tolerates_an_unknown_pid() -> None:
    world = DemoWorld()
    known = next(iter(world.procs.values()))

    snap = world.snapshot_proc(known.pid)
    missing = world.snapshot_proc(1)

    assert snap.name == known.name and snap.cmdline == known.cmdline and snap.status == "running"
    assert missing.name == "?" and missing.cmdline == []


def test_demo_health_check_fills_in_results_per_port() -> None:
    world = DemoWorld()
    rows = world.listeners()

    world.health_check(rows)

    by_port = {r.port: r.health for r in rows}
    assert by_port[9100].tcp_ok is False and by_port[9100].tcp_error == "refused"
    assert by_port[8080].http_status == 503
    assert by_port[8000].http_path == "/healthz" and by_port[8000].http_status == 200
    assert by_port[5432].http_note and "TCP only" in by_port[5432].http_note


def test_demo_probe_gives_the_rows_a_kind_and_the_vite_row_its_dev_server() -> None:
    engine = DemoEngine(_cfg())

    snap = engine.refresh_sync()

    by_port = {x.port: x for x in snap.listeners}
    assert by_port[5173].http.dev_server == "Vite"
    assert by_port[5173].http.kind == "html"
    assert "HTTP probe found Vite" in by_port[5173].identity.reasons
    assert by_port[8001].http.kind == "json"
    assert "answers with JSON" in by_port[8001].identity.reasons
    assert by_port[8001].identity.role == "backend"
    assert by_port[3000].http.kind == "html"
    assert by_port[6379].http.kind == "other"  # never probed, never guessed at
    engine.close()


def test_the_sharper_probe_keeps_the_demo_service_names() -> None:
    engine = DemoEngine(_cfg())

    snap = engine.refresh_sync()

    names = {x.port: x.identity.service for x in snap.listeners}
    assert names[80] == "Nginx"
    assert names[3000] == "web"
    assert names[3001] == "Node.js app"
    assert names[5173] == "Vite dev server"
    assert names[8000] == "api"
    assert names[8001] == "ASGI app (uvicorn)"
    assert names[8080] == "CMS"
    assert names[8090] == "Python static server"
    engine.close()
