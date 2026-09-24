"""The daemon: one engine served over a Unix socket, and everything that attaches to it.

The server runs the demo engine in-process over a real socket in a short temp path (macOS
caps socket paths at 104 bytes, so not under pytest's tmp_path); the client side is the
real :class:`RemoteEngine`.  Login-start installation is tested with a fake service manager.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from lirts import cli, daemon_install
from lirts.cli import common
from lirts.cli import daemon as daemon_cmd
from lirts.config import DEFAULT_CONFIG, validate
from lirts.daemon import DaemonServer
from lirts.daemon_client import DaemonClient, DaemonError, ping
from lirts.demo import DemoEngine
from lirts.doctor import OK, check_daemon
from lirts.mcp_tools import health_payload, who_payload
from lirts.models import Level
from lirts.ports import PortMemory, UsualHolder
from lirts.remote import RemoteEngine

runner = CliRunner()


def _cfg(**over: Any) -> dict[str, Any]:
    cfg = validate(dict(DEFAULT_CONFIG))
    cfg["refresh_interval"] = 60.0
    cfg.update(over)
    return cfg


@pytest.fixture
def sock() -> Path:
    """A socket path short enough for macOS."""
    folder = Path(tempfile.mkdtemp(prefix="lirts-t-", dir="/tmp"))
    return folder / "daemon.sock"


@pytest.fixture
async def served(sock: Path) -> AsyncIterator[DaemonServer]:
    """A running daemon over the demo machine; stopped and cleaned up afterwards."""
    # The daemon sees everything (UDP, system rows); each client filters for itself.
    server = DaemonServer(DemoEngine(_cfg(show_udp=True)), socket_path=sock, idle_interval=60.0)
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.refreshes and await asyncio.to_thread(ping, sock):
            break
        await asyncio.sleep(0.02)
    yield server
    await server.stop({})
    await task


# ----- protocol ------------------------------------------------------------------------


async def test_hello_says_who_answers(served: DaemonServer, sock: Path) -> None:
    # The server shares this test's event loop, so the blocking client calls run in a thread.
    info = await asyncio.to_thread(ping, sock)

    assert info is not None
    assert info["version"] and info["idle_interval"] == 60.0 and info["demo"] is True
    assert info["refreshes"] >= 1


async def test_unknown_and_broken_requests_are_answered_not_fatal(served: DaemonServer) -> None:
    unknown = await served.dispatch(b'{"method": "explode"}\n')
    broken = await served.dispatch(b"not json\n")

    assert unknown == {"ok": False, "error": "unknown method 'explode'"}
    assert broken["ok"] is False and broken["error"].startswith("bad request")


async def test_snapshot_refreshes_when_the_frame_is_older_than_max_age(
    served: DaemonServer,
) -> None:
    before = served.refreshes

    stale_ok = await served.dispatch(b'{"method": "snapshot", "params": {"max_age": 3600}}\n')
    after_stale = served.refreshes
    fresh = await served.dispatch(b'{"method": "snapshot", "params": {"max_age": 0}}\n')
    after_fresh = served.refreshes

    assert stale_ok["ok"] and after_stale == before
    assert fresh["ok"] and after_fresh == before + 1
    frame = fresh["result"]
    assert set(frame) == {
        "snapshot",
        "topology",
        "port_memory",
        "patterns",
        "last_session_diff",
        "route_notes",
        "daemon",
    }
    assert len(frame["snapshot"]["listeners"]) == 17


def test_ping_is_none_without_a_daemon(sock: Path) -> None:
    assert ping(sock) is None
    with pytest.raises(DaemonError):
        DaemonClient(sock).call_sync("hello")


def test_port_memory_survives_the_wire() -> None:
    memory = PortMemory(
        [UsualHolder(3000, "web", "shop", days=4, refreshes=2, last_seen=5.0)],
        young=False,
        stacks={"shop": 3},
    )

    back = PortMemory.from_dict(json.loads(json.dumps(memory.to_dict())))

    assert back.usual(3000) == UsualHolder(3000, "web", "shop", days=4, refreshes=2, last_seen=5.0)
    assert back.young is False and back.stack_size("shop") == 3


# ----- the remote engine --------------------------------------------------------------


async def test_remote_engine_shows_the_daemons_machine_with_its_own_filters(
    served: DaemonServer, sock: Path
) -> None:
    engine = RemoteEngine(_cfg(), DaemonClient(sock), await asyncio.to_thread(ping, sock) or {})

    snap = await engine.refresh()
    engine.show_udp = True
    with_udp = await engine.refresh()
    engine.hide_system = True
    filtered = await engine.refresh()
    engine.close()

    assert engine.remote and engine.position.startswith("via daemon (PID ")
    assert len(snap.listeners) == 16  # UDP hidden by default on this side
    assert {x.protocol for x in with_udp.listeners} == {"TCP", "UDP"}
    assert all(x.identity.role != "system" for x in filtered.listeners)
    assert len(filtered.listeners) < len(with_udp.listeners)
    assert len(engine.topology.graph_for().nodes) == 23
    assert who_payload(engine, 8080)["usually"]["label"] == "shop/gateway"
    assert engine.stacks().keys() == {"blog", "jobs", "shop"}
    assert not Path(engine._tmp).exists()


async def test_health_runs_in_the_daemon_and_stays_in_later_frames(
    served: DaemonServer, sock: Path
) -> None:
    engine = RemoteEngine(_cfg(), DaemonClient(sock), {})
    await engine.refresh()

    result = await health_payload(engine, 9100)
    later = await engine.refresh()
    engine.close()

    assert result[0]["ok"] is False and result[0]["tcp_error"] == "refused"
    assert later.by_port(9100).health.checked is True
    assert served.engine.snapshot.by_port(9100).health.checked is True


async def test_notes_and_clears_reach_the_daemons_history(served: DaemonServer, sock: Path) -> None:
    engine = RemoteEngine(_cfg(), DaemonClient(sock), {})
    await engine.refresh()

    event = await asyncio.to_thread(
        engine.history.note, "[-] killed by you (k)", port=5173, level=Level.WARNING
    )
    on_daemon = [e.message for e in served.engine.history.all_events()]
    await asyncio.to_thread(engine.history.clear_events)
    engine.close()

    assert event.message == "[-] killed by you (k)" and event.port == 5173
    assert "[-] killed by you (k)" in on_daemon
    assert served.engine.history.all_events() == [] and engine.history.all_events() == []


async def test_reload_routes_is_forwarded(served: DaemonServer, sock: Path) -> None:
    engine = RemoteEngine(_cfg(), DaemonClient(sock), {})
    served.engine._routes_loaded = True

    await asyncio.to_thread(engine.reload_routes_on_next_refresh)
    engine.close()

    assert served.engine._routes_loaded is False


async def test_make_engine_attaches_to_a_running_daemon(
    served: DaemonServer, sock: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(common, "socket_path", lambda: sock)

    attached = await asyncio.to_thread(common._make_engine, _cfg())
    standalone = await asyncio.to_thread(common._make_engine, _cfg(), standalone=True)
    attached.close()
    standalone.close()

    assert type(attached).__name__ == "RemoteEngine"
    assert type(standalone).__name__ == "Engine"


# ----- commands -------------------------------------------------------------------------


def test_daemon_status_and_stop_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    info = {"pid": 4242, "version": "0.6.0", "started": 0.0, "refreshes": 9, "idle_interval": 10.0}
    calls: list[str] = []
    monkeypatch.setattr(daemon_cmd, "ping", lambda path: info)
    monkeypatch.setattr(
        daemon_cmd.DaemonClient, "call_sync", lambda self, method, **kw: calls.append(method)
    )

    status = runner.invoke(cli.app, ["daemon", "status"])
    stop = runner.invoke(cli.app, ["daemon", "stop"])

    assert status.exit_code == 0 and "running  PID 4242" in status.output
    assert "9 refreshes" in status.output and "login start:" in status.output
    assert stop.exit_code == 0 and stop.output.strip().endswith("stopped PID 4242")
    assert calls == ["stop"]


def test_daemon_status_exits_1_when_nothing_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daemon_cmd, "ping", lambda path: None)

    result = runner.invoke(cli.app, ["daemon", "status"])

    assert result.exit_code == 1 and "not running" in result.output


def test_lirts_dash_d_and_lirts_daemon_run_the_foreground_daemon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs: list[dict[str, Any]] = []

    def fake_serve(self: DaemonServer) -> None:
        runs.append({"idle": self.idle_interval, "demo": getattr(self.engine, "demo", False)})
        self.engine.close()

    async def serve(self: DaemonServer) -> None:
        fake_serve(self)

    monkeypatch.setattr(DaemonServer, "serve", serve)

    short = runner.invoke(cli.app, ["-d", "--demo"])
    long = runner.invoke(cli.app, ["daemon", "--demo", "--idle", "3"])

    assert short.exit_code == 0, short.output
    assert long.exit_code == 0, long.output
    assert runs == [{"idle": 10.0, "demo": True}, {"idle": 3.0, "demo": True}]


def test_idle_interval_setting_round_trips(tmp_path: Path) -> None:
    cfg_file = tmp_path / "c.yaml"

    result = runner.invoke(
        cli.app, ["config", "set", "daemon.idle_interval", "30", "--config", str(cfg_file)]
    )
    shown = runner.invoke(cli.app, ["config", "show", "--config", str(cfg_file)])

    assert result.exit_code == 0, result.output
    assert "idle_interval: 30" in shown.output


@pytest.mark.parametrize("system", ["Darwin", "Linux"], ids=["launchd", "systemd"])
def test_install_writes_the_service_file_and_loads_it(tmp_path: Path, system: str) -> None:
    ran: list[list[str]] = []
    mgr = daemon_install.manager(system, home=tmp_path)
    assert mgr is not None

    ok, message = daemon_install.install(
        mgr, exe="/opt/bin/lirts", run=lambda c: ran.append(c) or (0, "")
    )
    text = mgr.file.read_text()
    gone, _ = daemon_install.uninstall(mgr, run=lambda c: ran.append(c) or (0, ""))

    assert ok and message.startswith(f"installed {mgr.file}")
    assert "/opt/bin/lirts" in text and "daemon" in text
    if system == "Darwin":
        assert mgr.file.name == "fi.evilunicorn.lirts.plist" and "<key>RunAtLoad</key>" in text
        assert ran[0] == ["launchctl", "load", "-w", str(mgr.file)]
        assert ran[1] == ["launchctl", "unload", "-w", str(mgr.file)]
    else:
        assert mgr.file.name == "lirts.service" and "WantedBy=default.target" in text
        assert ran[:2] == [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", "lirts"],
        ]
    assert gone and not mgr.file.exists()


def test_install_reports_a_refusing_service_manager(tmp_path: Path) -> None:
    mgr = daemon_install.manager("Darwin", home=tmp_path)
    assert mgr is not None

    ok, message = daemon_install.install(mgr, exe="/opt/bin/lirts", run=lambda c: (1, "nope"))

    assert ok is False and message.endswith("refused it: nope")


def test_start_and_stop_need_an_installed_service(tmp_path: Path) -> None:
    mgr = daemon_install.manager("Linux", home=tmp_path)
    assert mgr is not None and daemon_install.manager("Windows", home=tmp_path) is None

    assert daemon_install.start(mgr) == (False, "not installed; run `lirts daemon install` first")
    assert daemon_install.stop(mgr) == (False, "not installed")


def test_install_command_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mgr = daemon_install.manager("Darwin", home=tmp_path)
    ran: list[list[str]] = []
    monkeypatch.setattr(daemon_cmd.daemon_install, "manager", lambda: mgr)
    monkeypatch.setattr(daemon_cmd.daemon_install, "executable", lambda: "/opt/bin/lirts")
    # The real service manager is never reached: the stub records what would have run.
    monkeypatch.setattr(daemon_cmd.daemon_install, "_run", lambda c: ran.append(c) or (0, ""))

    result = runner.invoke(cli.app, ["daemon", "install"])

    assert result.exit_code == 0, result.output
    assert "the daemon starts now" in result.output  # Rich wraps the long line
    assert mgr is not None and mgr.file.exists()
    assert ran == [["launchctl", "load", "-w", str(mgr.file)]]


def test_doctor_reports_the_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lirts.doctor.ping", lambda path: None)
    idle = check_daemon()
    monkeypatch.setattr(
        "lirts.doctor.ping", lambda path: {"pid": 7, "started": 0.0, "refreshes": 3}
    )
    running = check_daemon()

    assert idle.status == OK and idle.detail.startswith("not running; every lirts command")
    assert running.status == OK and running.detail.startswith("running, PID 7, up ")
