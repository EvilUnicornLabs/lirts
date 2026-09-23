from __future__ import annotations

import socket

import pytest
from aiohttp import web
from typer.testing import CliRunner

from lirts import cli
from lirts.cli import health as health_cmd
from lirts.collectors.health import HealthChecker
from lirts.config import CONFIG_VERSION, DEFAULT_CONFIG
from lirts.config_view import ConfigView
from lirts.doctor import Check, check_config, check_state, run_all
from lirts.insights import analyse
from lirts.models import HealthResult, HttpProbe, Snapshot
from tests.conftest import make_listener, make_process

runner = CliRunner()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
async def health_server():
    state = {"healthy": True}

    async def index(request):
        return web.Response(text="home")

    async def healthz(request):
        return web.Response(
            status=200 if state["healthy"] else 503, text="ok" if state["healthy"] else "down"
        )

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/healthz", healthz)
    runner_ = web.AppRunner(app)
    await runner_.setup()
    port = free_port()
    await web.TCPSite(runner_, "127.0.0.1", port).start()
    yield port, state
    await runner_.cleanup()


async def test_tcp_and_learned_health_path(health_server) -> None:
    port, state = health_server
    checker = HealthChecker()
    lst = make_listener(
        port=port,
        processes=[make_process(addresses=["127.0.0.1"])],
        http=HttpProbe(attempted=True, ok=True, status=200, scheme="http"),
    )
    await checker.check([lst])
    h = lst.health
    assert h.checked and h.tcp_ok and h.tcp_latency_ms is not None
    assert h.http_path == "/healthz" and h.http_status == 200 and h.ok
    assert "tcp" in h.summary and "/healthz" in h.summary
    # a fresh listener object with the same key gets the last result back without a check
    lst2 = make_listener(
        port=port,
        processes=[make_process(addresses=["127.0.0.1"])],
        http=HttpProbe(attempted=True, ok=True, status=200),
    )
    lst2.processes[0].pid = lst.processes[0].pid
    checker.attach_cached([lst2])
    assert lst2.health is h
    # every check is a real check: the endpoint failing is reported
    state["healthy"] = False
    await checker.check([lst])
    assert (
        lst.health.http_path == "/healthz" and lst.health.http_status == 503 and not lst.health.ok
    )
    analyse(lst, cfg=ConfigView(DEFAULT_CONFIG))
    assert any(i.code == "health-failing" for i in lst.insights)


async def test_tcp_refused_and_no_health_path(health_server) -> None:
    port, _ = health_server
    checker = HealthChecker(paths=["/nope"])
    lst = make_listener(
        port=port,
        processes=[make_process(addresses=["127.0.0.1"])],
        http=HttpProbe(attempted=True, ok=True, status=200),
    )
    await checker.check([lst])
    assert lst.health.tcp_ok and lst.health.http_path is None and lst.health.ok
    assert checker._learned[checker._key(lst)] == ""
    dead = make_listener(port=free_port(), processes=[make_process(addresses=["127.0.0.1"])])
    await checker.check([dead])
    assert dead.health.checked and not dead.health.tcp_ok and dead.health.tcp_error == "refused"
    analyse(dead, cfg=ConfigView(DEFAULT_CONFIG))
    assert any(i.code == "unreachable-tcp" for i in dead.insights) and dead.status == "error"
    udp = make_listener(port=53, protocol="UDP", state="BOUND")
    await checker.check([udp])
    assert udp.health.checked is False and udp.health.summary == "-"


def test_overrides_and_paths_normalised() -> None:
    checker = HealthChecker(paths=["health", "/x"], overrides={"8080": "status"})
    assert checker.paths == ["/health", "/x"] and checker.overrides == {8080: "/status"}


def test_health_result_summary() -> None:
    assert HealthResult().summary == "-" and HealthResult().ok
    r = HealthResult(checked=True, tcp_ok=False, tcp_error="timeout")
    assert r.summary == "✗ timeout" and not r.ok


def test_cli_health(monkeypatch) -> None:
    from tests.test_cli import FakeEngine

    good = make_listener(port=3000)
    good.health = HealthResult(
        checked=True,
        tcp_ok=True,
        tcp_latency_ms=1.0,
        http_path="/healthz",
        http_status=200,
        http_latency_ms=3.0,
    )
    bad = make_listener(port=4000)
    bad.health = HealthResult(checked=True, tcp_ok=False, tcp_error="refused")
    engine = FakeEngine(Snapshot(listeners=[good, bad]))

    async def no_check(listeners):
        return listeners

    engine.check_health_now = no_check  # type: ignore[attr-defined]  # FakeEngine test double
    monkeypatch.setattr(health_cmd, "_collect", lambda cfg, samples=1: (engine, engine.snap))
    result = runner.invoke(cli.app, ["health"])
    assert result.exit_code == 1 and "refused" in result.output and "/healthz" in result.output
    result = runner.invoke(cli.app, ["health", "--filter", "port:3000"])
    assert result.exit_code == 0
    result = runner.invoke(cli.app, ["health", "--json"])
    import json

    rows = json.loads(result.output)
    assert rows[1]["ok"] is False and rows[0]["http_path"] == "/healthz"


def test_doctor(tmp_path, monkeypatch) -> None:
    checks = run_all(tmp_path / "missing.yaml")
    names = [c.name for c in checks]
    assert "config file" in names and "state directory" in names and "docker" in names
    assert all(isinstance(c, Check) for c in checks)
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"version: {CONFIG_VERSION}\nrefresh_interval: 2\nbogus: 1\n")
    results = check_config(cfg)
    assert (
        results[0].status == "ok"
        and results[1].name == "config keys"
        and "bogus" in results[1].detail
    )
    cfg.write_text("theme: nord\n")
    assert check_config(cfg)[0].status == "warn"
    cfg.write_text("- list\n")
    assert check_config(cfg)[0].status == "fail"
    assert check_state().status == "ok"
    result = runner.invoke(cli.app, ["doctor", "--json"])
    assert result.exit_code in (0, 1)
    import json

    assert any(c["name"] == "lirts / Python" for c in json.loads(result.output))


def test_no_background_health_anywhere() -> None:
    from lirts.settings import SETTINGS

    assert "enabled" not in DEFAULT_CONFIG["health"] and "interval" not in DEFAULT_CONFIG["health"]
    assert not any(s.path in ("health.enabled", "health.interval") for s in SETTINGS)
    assert not hasattr(HealthChecker(), "enabled") and not hasattr(HealthChecker(), "interval")
    assert "health" not in DEFAULT_CONFIG["columns"]


async def test_on_demand_health_key(monkeypatch) -> None:
    import asyncio

    from tests.conftest import FakeEngine, LirtsApp, load_config, wait_rows

    engine = FakeEngine()
    calls: list[int] = []

    async def check_now(listeners):
        calls.append(len(listeners))
        for lst in listeners:
            lst.health = HealthResult(checked=True, tcp_ok=True, tcp_latency_ms=1.0)
        return listeners

    engine.check_health_now = check_now  # type: ignore[attr-defined]  # FakeEngine test double
    cfg = load_config()
    cfg["refresh_interval"] = 60.0
    app = LirtsApp(engine, cfg)  # type: ignore[arg-type]  # FakeEngine stands in for Engine
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        await pilot.press("H")
        await pilot.pause()
        await asyncio.sleep(0.2)
        await pilot.pause()
        assert calls == [3]


async def test_health_notes_and_cached_results(health_server) -> None:
    port, _ = health_server
    checker = HealthChecker(paths=["/nope", "/alsonope"])
    lst = make_listener(
        port=port, name="python", http=HttpProbe(attempted=True, ok=True, status=200)
    )
    await checker.check([lst])
    assert lst.health.http_path is None and lst.health.ok
    assert lst.health.http_note is not None and "2 paths tried" in lst.health.http_note
    assert "404" in lst.health.http_note and "no health endpoint" in lst.health.summary

    cache = make_listener(port=port, name="redis", http=HttpProbe(attempted=False))
    await checker.check([cache])
    assert cache.health.http_note == "TCP only (unknown, not probed for HTTP)"

    # A fresh listener object with the same key gets the previous result back without a check.
    again = make_listener(port=port, name="python", http=HttpProbe(attempted=True, ok=True))
    checker.attach_cached([again])
    assert again.health is cache.health and not again.health.http_path
