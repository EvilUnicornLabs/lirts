from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
from typer.testing import CliRunner

from lirts import cli
from lirts.cli import listing
from lirts.collectors.origin import Origin, OriginResolver, _terminal_name, git_branch, tmux_windows
from lirts.collectors.ports import (
    ProcessSampler,
    _ssh_host,
    collect_edges,
    collect_listeners,
    collect_ssh_sessions,
)
from lirts.identity import ROLE_BACKEND, ROLE_DB, ROLE_FRONTEND
from lirts.insights import blast_radius, explain, render_graph_text
from lirts.models import Edge, Identity, Snapshot, SshSession
from tests.conftest import make_listener, make_process

runner = CliRunner()


def test_git_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/feature/x\n")
    sub = repo / "src" / "deep"
    sub.mkdir(parents=True)
    cache: dict = {}
    assert git_branch(str(sub), cache) == ("feature/x", str(repo))
    assert git_branch(str(sub), cache) == ("feature/x", str(repo))  # cached
    (repo / ".git" / "HEAD").write_text("0123456789abcdef\n")
    assert git_branch(str(sub))[0] == "0123456789"  # detached head
    assert git_branch(str(tmp_path)) == (None, None)
    assert git_branch(None) == (None, None)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {repo / '.git'}\n")
    assert git_branch(str(wt))[0] == "0123456789"


def test_terminal_names_and_tmux(monkeypatch) -> None:
    assert _terminal_name("iTerm2") == "iTerm2"
    assert _terminal_name("Code Helper (Plugin)") == "VS Code"
    assert _terminal_name("tmux: server") == "tmux"
    assert _terminal_name("weird") is None
    monkeypatch.setattr("lirts.collectors.origin.shutil.which", lambda n: "/usr/bin/tmux")
    monkeypatch.setattr(
        "lirts.collectors.origin.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(
            a, 0, stdout="/dev/ttys003\tmain:1.0\teditor\n/dev/ttys004\tmain:2.0\t\n", stderr=""
        ),
    )
    assert tmux_windows() == {"ttys003": "main:1.0 (editor)", "ttys004": "main:2.0"}


def test_origin_resolver_on_spawned_process(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    shell = subprocess.Popen(
        ["sh", "-c", f"cd {repo} && exec {sys.executable} -c 'import time; time.sleep(20)'"]
    )
    try:
        time.sleep(0.5)
        resolver = OriginResolver()
        origin = resolver.resolve(shell.pid, str(repo))
        assert isinstance(origin, Origin)
        assert origin.git_branch == "main" and origin.git_root == str(repo)
        # the direct parent chain of this test runner includes a shell or a supervisor somewhere
        assert origin.summary != ""
        resolver.forget(set())
        assert resolver._cache == {}
    finally:
        shell.kill()


def test_summary_formatting() -> None:
    assert Origin().summary == "unknown"
    assert Origin(supervisor="launchd (system)").summary == "launchd (system)"
    o = Origin(
        shell="zsh", terminal="Ghostty", tty="ttys001", tmux="main:1.0 (x)", git_branch="dev"
    )
    assert o.summary == "Ghostty · tmux main:1.0 (x) · zsh · ttys001 · branch dev"


def test_edges_from_a_real_connection() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = server.accept()
    try:
        sampler = ProcessSampler()
        listeners = collect_listeners(sampler)
        mine = next(x for x in listeners if x.port == port)
        assert mine.pid == os.getpid()
        edges = [e for e in sampler.edges if e.dst_port == port]
        assert edges and edges[0].client_pid == os.getpid() and edges[0].count == 1
        assert any(n in edges[0].client_name.lower() for n in ("python", "pytest"))
        assert mine.connections == 1  # server side counts the inbound connection
    finally:
        conn.close()
        client.close()
        server.close()


def test_collect_edges_and_ssh_with_fake_sockets() -> None:
    laddr = SimpleNamespace(ip="127.0.0.1", port=54321)
    raddr = SimpleNamespace(ip="127.0.0.1", port=5432)
    est = SimpleNamespace(
        type=socket.SOCK_STREAM, status=psutil.CONN_ESTABLISHED, laddr=laddr, raddr=raddr
    )
    remote = SimpleNamespace(
        type=socket.SOCK_STREAM,
        status=psutil.CONN_ESTABLISHED,
        laddr=laddr,
        raddr=SimpleNamespace(ip="1.2.3.4", port=22),
    )
    sampler = ProcessSampler()
    sampler.begin_cycle({})

    class Snap:
        def __init__(self, name, cmd):
            self.name, self.cmdline, self.create_time = name, cmd, 1.0

    snaps = {
        11: Snap("node", ["node", "app.js"]),
        12: Snap("ssh", ["ssh", "-p", "2222", "-L", "15432:db:5432", "me@bastion"]),
    }
    sampler.describe = lambda pid: snaps[pid]  # type: ignore[method-assign]  # test spy
    edges = collect_edges({11: [est, est], 12: [remote]}, listening_ports={5432}, sampler=sampler)
    assert edges == [
        Edge(
            client_pid=11,
            client_name="node",
            dst_port=5432,
            count=2,
            client_cmd="node app.js",
            client_ports=[54321, 54321],
        )
    ]
    sessions = collect_ssh_sessions({11: [est], 12: [remote]}, sampler)
    assert len(sessions) == 1
    s = sessions[0]
    assert (s.pid, s.host, s.user, s.remote, s.forwards) == (
        12,
        "bastion",
        "me",
        "1.2.3.4:22",
        ["15432:db:5432"],
    )
    assert _ssh_host(["ssh", "-i", "key", "-o", "X=y", "host.example"]) == "host.example"
    assert _ssh_host(["ssh", "-N"]) is None


def test_graph_text_blast_radius_and_explain() -> None:
    db = make_listener(port=5432, identity=Identity("PostgreSQL", ROLE_DB, 0.9, project="shop"))
    api = make_listener(
        port=8000, name="python", identity=Identity("FastAPI", ROLE_BACKEND, 0.9, project="shop")
    )
    edge = Edge(
        client_pid=api.pid or 1,
        client_name="python [FastAPI]",
        dst_port=5432,
        count=3,
        client_project="shop",
    )
    db.clients = [edge]
    tunnel = make_listener(
        port=5435,
        processes=[
            make_process(name="kubectl", cmdline=["kubectl", "port-forward", "svc/pg", "5435:5432"])
        ],
    )
    from lirts.identity import identify

    tunnel.identity = identify(tunnel)
    snap = Snapshot(
        listeners=[db, api, tunnel],
        edges=[edge],
        ssh_sessions=[
            SshSession(
                pid=9,
                host="bastion",
                remote="1.2.3.4:22",
                user="me",
                tty="ttys001",
                forwards=["15432:db:5432"],
                create_time=time.time() - 60,
            )
        ],
    )
    text = render_graph_text(snap)
    assert (
        ":5432 PostgreSQL [shop]" in text
        and "◀── python [FastAPI] [shop] (PID" in text
        and "×3" in text
    )
    assert "Tunnels" in text and ":5435 port-forward" in text
    assert (
        "SSH sessions" in text
        and "me@bastion (1.2.3.4:22)" in text
        and "forwards 15432:db:5432" in text
    )
    impacts = blast_radius(db, snap)
    assert any(
        "1 local client process(es) will lose their connection: python [FastAPI]" in i
        for i in impacts
    )
    summary = explain(snap)
    assert "Local connections: 1 client" in summary and "SSH sessions: me@bastion" in summary
    api.processes[0].origin = Origin(terminal="Ghostty", shell="zsh")
    assert "Started from: Ghostty (1)" in explain(snap)
    empty = render_graph_text(Snapshot(listeners=[api]))
    assert "none right now" in empty


def test_cli_graph(monkeypatch) -> None:
    from tests.test_cli import FakeEngine

    db = make_listener(port=5432, identity=Identity("PostgreSQL", ROLE_DB, 0.9))
    db.clients = [Edge(client_pid=1, client_name="psql", dst_port=5432)]
    engine = FakeEngine(Snapshot(listeners=[db], edges=list(db.clients)))
    monkeypatch.setattr(listing, "_collect", lambda cfg, samples=1: (engine, engine.snap))
    result = runner.invoke(cli.app, ["graph"])
    assert result.exit_code == 0 and "psql" in result.output and ":5432 PostgreSQL" in result.output


async def test_graph_screen(app_factory=None) -> None:
    from lirts.tui.screens import GraphScreen
    from tests.conftest import FakeEngine, LirtsApp, load_config, wait_rows

    cfg = load_config()
    cfg["refresh_interval"] = 60.0
    app = LirtsApp(FakeEngine(), cfg)  # type: ignore[arg-type]  # FakeEngine stands in for Engine
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        await pilot.press("w")
        await pilot.pause()
        assert isinstance(app.screen, GraphScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, GraphScreen)
        await asyncio.sleep(0)


def test_origin_short_and_interactive() -> None:
    assert not Origin(supervisor="launchd (system)").interactive
    o = Origin(terminal="Ghostty", shell="zsh", git_branch="main")
    assert o.interactive and o.short == "Ghostty @main"
    assert Origin(shell="zsh", tmux="main:1.0 (x)").short == "tmux main:1.0"
    from lirts.tui.render import origin_text

    lst = make_listener()
    assert origin_text(lst).plain == "-"
    lst.processes[0].origin = o
    assert origin_text(lst).plain == "Ghostty @main"


def test_edges_get_flow_rates_when_enabled(tmp_path) -> None:
    from lirts.config import DEFAULT_CONFIG
    from lirts.engine import Engine
    from lirts.insights import render_graph_text

    engine = Engine(dict(DEFAULT_CONFIG), state_root=tmp_path)
    edge = Edge(
        client_pid=4740, client_name="chrome", dst_port=5173, count=2, client_ports=[55989, 55990]
    )
    engine.bandwidth.connections = False
    engine._attach_flows([edge])
    assert edge.bytes_in_rate is None
    engine.bandwidth.connections = True
    engine.bandwidth._flow_rates = {
        (4740, 55989, 5173): (1000.0, 200.0, 5000, 900),
        (4740, 55990, 5173): (24.0, 8.0, 100, 50),
    }
    engine._attach_flows([edge])
    engine._attach_flows([edge])
    assert edge.bytes_in_rate == 1024.0 and edge.bytes_out_rate == 208.0
    assert edge.bytes_in == 5100 and edge.bytes_out == 950 and len(edge.rate_history) == 2
    web = make_listener(port=5173, identity=Identity("Vite dev server", ROLE_FRONTEND, 0.9))
    web.clients = [edge]
    text = render_graph_text(Snapshot(listeners=[web]))
    assert ("↓1.0 KB/s ↑208 B/s" in text and "▁" in text) or "█" in text
    engine.close()
