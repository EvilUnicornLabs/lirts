"""Tests for the 0.6.0 features: health events, stacks, local restart, watch, notify."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
from typer.testing import CliRunner

from lirts import cli
from lirts import notify as notify_mod
from lirts.cli import actions, listing, stack
from lirts.collectors.docker import DockerProvider
from lirts.history import HistoryStore
from lirts.identity import ROLE_BACKEND
from lirts.models import Identity, Insight, Listener, ListenerProcess, Snapshot
from tests.conftest import make_container, make_listener, make_process
from tests.test_engine import make_engine

runner = CliRunner()


# ----- history: health transitions -----------------------------------------------------


def test_health_transition_events() -> None:
    store = HistoryStore()
    lst = make_listener(port=8000, identity=Identity("API", ROLE_BACKEND, 0.9))
    store.update([lst], now=1.0)
    lst.insights = [Insight("warning", "unreachable", "HTTP probe failed: timeout")]
    events = store.update([lst], now=2.0)
    assert any("degraded: HTTP probe failed" in e.message for e in events)
    lst.insights = [Insight("error", "zombie", "python is a zombie process")]
    events = store.update([lst], now=3.0)
    assert any("is failing: python is a zombie" in e.message for e in events)
    assert store.update([lst], now=4.0) == []  # no repeat while unchanged
    lst.insights = []
    events = store.update([lst], now=5.0)
    assert any("recovered" in e.message for e in events)


# ----- docker stacks ----------------------------------------------------------------------


class _Api:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def stop(self, cid, timeout=10):
        self.calls.append(("stop", cid))

    def restart(self, cid, timeout=10):
        self.calls.append(("restart", cid))
        if cid.startswith("bad"):
            raise RuntimeError("nope")

    def logs(self, cid, **kw):
        return f"log of {cid}\nsecond\n".encode()


class _Client:
    def __init__(self) -> None:
        self.api = _Api()

    def ping(self):
        return True

    def close(self):
        pass


def test_stack_actions_and_logs() -> None:
    provider = DockerProvider()
    provider._client = _Client()
    provider._cli = None
    a = make_container(name="shop-web-1", service="web", stack="shop")
    a.id = "aaa"
    b = make_container(name="shop-db-1", service="db", stack="shop")
    b.id = "bad-bbb"
    other = make_container(name="x-y-1", service="y", stack="x")
    other.id = "ccc"
    containers = [a, b, other]
    assert [c.name for c in provider.stack_containers(containers, "shop")] == [
        "shop-web-1",
        "shop-db-1",
    ]
    results = provider.stack_action(containers, project="restart", action="restart")
    assert results == []  # unknown project
    results = provider.stack_action(containers, project="shop", action="restart")
    assert results[0] == ("shop-web-1", True, "restarted")
    assert results[1][0] == "shop-db-1" and results[1][1] is False
    results = provider.stack_action(containers, project="shop", action="stop")
    assert all(ok for _, ok, _ in results)
    logs = provider.stack_logs(containers, project="shop", tail=5)
    assert "web            | log of aaa" in logs
    assert "db             | second" in logs
    assert "no running containers" in provider.stack_logs(containers, project="nope")


# ----- engine helpers ----------------------------------------------------------------------


def test_engine_stacks_project_dir_and_restart_plan(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    a = make_container(name="shop-web-1", service="web", stack="shop", working_dir="/tmp/shop")
    b = make_container(name="lonely", service=None, stack=None)
    engine.containers = [a, b]
    stacks = engine.stacks()
    assert list(stacks) == ["-", "shop"]
    docker_row = make_listener(
        port=8080, container=a, processes=[make_process(name="com.docker.backend")]
    )
    assert engine.project_dir(docker_row) == "/tmp/shop"
    local = make_listener(port=3000, processes=[make_process(cwd=str(tmp_path))])
    assert engine.project_dir(local) == str(tmp_path)
    assert engine.project_dir(make_listener(port=1, processes=[make_process(cwd="/")])) is None
    assert engine.restart_plan(docker_row) is None
    assert engine.restart_plan(make_listener(port=2, processes=[])) is None
    plan = engine.restart_plan(local)
    assert plan == (["node"], str(tmp_path))


def test_engine_restart_process_really_restarts(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path)
    try:
        proc = ListenerProcess(
            pid=child.pid,
            name="python",
            cmdline=[sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=str(tmp_path),
            create_time=time.time(),
        )
        lst = Listener(port=54321, protocol="TCP", processes=[proc])
        ok, msg = engine.restart_process(lst)
        assert ok, msg
        assert "started PID" in msg
        child.wait(timeout=5)  # old one is gone
        new_pid = int(msg.split("PID ")[1].split()[0])
        assert psutil.pid_exists(new_pid)
        assert (tmp_path / "state" / "restarts" / "54321.log").exists()
        psutil.Process(new_pid).kill()
    finally:
        if child.poll() is None:
            child.kill()


def test_restart_process_refuses_without_plan(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    ok, msg = engine.restart_process(make_listener(port=1, processes=[]))
    assert ok is False and "no command line" in msg


# ----- notify -----------------------------------------------------------------------------


def test_notify_desktop_uses_available_backend(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(notify_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        notify_mod.shutil,
        "which",
        lambda name: "/usr/bin/osascript" if name == "osascript" else None,
    )
    monkeypatch.setattr(notify_mod.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    assert notify_mod.notify_desktop("lirts", 'port "3000" down')
    assert calls and calls[0][0] == "/usr/bin/osascript"
    assert '\\"3000\\"' in calls[0][2]
    monkeypatch.setattr(notify_mod.shutil, "which", lambda name: None)
    assert notify_mod.notify_desktop("t", "m") is False


# ----- CLI --------------------------------------------------------------------------------


class FakeEngine:
    def __init__(self, snap: Snapshot) -> None:
        self.snap = snap
        self.last_session_diff: dict[str, list[str]] = {}
        self.closed = False
        self.web = make_container(
            name="shop-web-1",
            service="web",
            stack="shop",
            host_ports=[8080],
            working_dir="/tmp/shop",
        )
        self.web.id = "web-id"
        self.containers = [self.web]
        self.docker = self
        self.restarted: list[str] = []
        self.stack_calls: list[tuple[str, str]] = []
        self.local_restarts = 0

    def close(self) -> None:
        self.closed = True

    def stacks(self):
        return {"shop": [self.web]}

    def stack_action(self, project, action):
        self.stack_calls.append((project, action))
        return [("shop-web-1", True, action)]

    def stack_logs(self, project, tail=100):
        return f"web | hello ({tail})"

    def restart(self, cid):
        self.restarted.append(cid)
        return True, "restarted"

    def restart_plan(self, lst):
        return (["node", "server.js"], "/tmp/shop") if lst.processes else None

    def restart_process(self, lst, force=False):
        self.local_restarts += 1
        return True, "started PID 4242"


def _snapshot() -> Snapshot:
    local = make_listener(
        port=3000,
        name="node",
        cmdline=["node", "server.js"],
        identity=Identity("Node.js app", ROLE_BACKEND, 0.6),
    )
    web = make_container(
        name="shop-web-1", service="web", stack="shop", host_ports=[8080], working_dir="/tmp/shop"
    )
    web.id = "web-id"
    docker = make_listener(
        port=8080,
        container=web,
        processes=[make_process(pid=7, name="com.docker.backend")],
        identity=Identity("Nginx", "proxy", 0.9),
    )
    return Snapshot(listeners=[local, docker], docker_available=True, container_count=1)


def test_cli_restart(monkeypatch) -> None:
    engine = FakeEngine(_snapshot())
    monkeypatch.setattr(actions, "_collect", lambda cfg, samples=1: (engine, engine.snap))
    assert runner.invoke(cli.app, ["restart", "9"]).exit_code == 1
    result = runner.invoke(cli.app, ["restart", "8080", "--yes"])
    assert result.exit_code == 0, result.output
    assert engine.restarted == ["web-id"]
    result = runner.invoke(cli.app, ["restart", "3000"], input="n\n")
    assert result.exit_code == 0 and engine.local_restarts == 0
    assert "node server.js" in result.output
    result = runner.invoke(cli.app, ["restart", "3000", "--yes"])
    assert result.exit_code == 0, result.output
    assert engine.local_restarts == 1 and "started PID 4242" in result.output


def test_cli_stack(monkeypatch) -> None:
    engine = FakeEngine(_snapshot())
    monkeypatch.setattr(stack, "_collect", lambda cfg, samples=1: (engine, engine.snap))
    result = runner.invoke(cli.app, ["stack", "list"])
    assert result.exit_code == 0 and "shop" in result.output and "8080" in result.output
    assert runner.invoke(cli.app, ["stack", "restart", "nope", "--yes"]).exit_code == 1
    result = runner.invoke(cli.app, ["stack", "restart", "shop", "--yes"])
    assert result.exit_code == 0, result.output
    assert engine.stack_calls == [("shop", "restart")]
    result = runner.invoke(cli.app, ["stack", "stop", "shop"], input="y\n")
    assert result.exit_code == 0 and engine.stack_calls[-1] == ("shop", "stop")
    result = runner.invoke(cli.app, ["stack", "logs", "shop", "--tail", "7"])
    assert result.exit_code == 0 and "hello (7)" in result.output


def test_cli_watch(monkeypatch, tmp_path: Path) -> None:
    from lirts.models import Event

    class WatchEngine:
        def __init__(self, cfg):
            self.n = 0

        def refresh_sync(self):
            self.n += 1
            events = [
                Event(
                    100.0 + self.n,
                    "warning",
                    f"[-] thing stopped on {3000 + self.n}/TCP",
                    3000 + self.n,
                )
            ]
            return Snapshot(listeners=[make_listener(port=3000 + self.n)], events=events)

        def close(self):
            pass

    sent: list[str] = []
    monkeypatch.setattr(listing, "Engine", WatchEngine)
    monkeypatch.setattr(listing, "notify_desktop", lambda title, msg: sent.append(msg) or True)
    monkeypatch.setattr(listing.time, "sleep", lambda s: None)
    result = runner.invoke(cli.app, ["watch", "--count", "3", "--notify"])
    assert result.exit_code == 0, result.output
    assert "stopped on 3002/TCP" in result.output and "stopped on 3003/TCP" in result.output
    assert "3001/TCP" not in result.output  # first check only establishes the baseline
    assert len(sent) == 2
    result = runner.invoke(cli.app, ["watch", "--count", "3", "--filter", "3003"])
    assert "3003/TCP" in result.output and "3002/TCP" not in result.output


def test_completion_flag_exists() -> None:
    import re

    result = runner.invoke(
        cli.app, ["--help"], env={"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"}
    )
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "--install-completion" in plain


def test_install_script_is_executable() -> None:
    path = Path(__file__).resolve().parent.parent / "install.sh"
    assert os.access(path, os.X_OK)
    assert "pipx install" in path.read_text()


def test_history_clear_and_stale_events_pruned(tmp_path: Path) -> None:
    import json

    from lirts.models import Event

    path = tmp_path / "h.json"
    store = HistoryStore(path=path, persist=True, retention_hours=1)
    store.events.append(Event(time.time() - 7200, "warning", "old", 1))
    store.events.append(Event(time.time(), "warning", "fresh", 2))
    store.update([make_listener()], now=time.time())
    store.save(force=True)
    assert len(json.loads(path.read_text())["events"]) == 2
    reloaded = HistoryStore(path=path, persist=True, retention_hours=1)
    assert [e.message for e in reloaded.all_events()] == ["fresh"]
    reloaded.clear_events()
    assert reloaded.all_events() == []
