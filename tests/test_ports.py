from __future__ import annotations

import socket
from types import SimpleNamespace

import psutil

from lirts.collectors import ports as pmod
from lirts.collectors.ports import (
    ProcessSampler,
    _ProcSnapshot,
    collect_listeners,
    explain_shared_port,
)
from tests.conftest import make_listener, make_process


def conn(
    port: int,
    ip: str = "127.0.0.1",
    status: str = psutil.CONN_LISTEN,
    type_=socket.SOCK_STREAM,
    raddr=None,
):
    return SimpleNamespace(
        laddr=SimpleNamespace(ip=ip, port=port),
        raddr=raddr,
        status=status,
        type=type_,
        family=socket.AF_INET6 if ":" in ip else socket.AF_INET,
    )


def established(local_port: int, rip: str, rport: int):
    return conn(
        local_port, status=psutil.CONN_ESTABLISHED, raddr=SimpleNamespace(ip=rip, port=rport)
    )


def fake_describe(snapshots: dict[int, _ProcSnapshot]):
    def describe(self, pid):
        return snapshots.get(pid, _ProcSnapshot(name="?", accessible=False))

    return describe


def test_collect_groups_dual_stack_and_workers(monkeypatch) -> None:
    pairs = [
        (conn(8080, "0.0.0.0"), 1),
        (conn(8080, "::"), 1),
        (conn(8080, "::"), 2),
        (conn(3000), 3),
        (established(3000, "127.0.0.1", 50000), 3),
        (established(45000, "1.2.3.4", 443), 3),
        (conn(5353, "0.0.0.0", psutil.CONN_NONE, socket.SOCK_DGRAM), 4),
        (conn(9, "0.0.0.0", psutil.CONN_LISTEN), None),
    ]
    by_pid = {}
    for c, pid in pairs:
        if pid:
            by_pid.setdefault(pid, []).append(c)
    monkeypatch.setattr(pmod, "_gather_sockets", lambda: (pairs, by_pid))
    snaps = {
        1: _ProcSnapshot(
            name="httpd", ppid=0, create_time=1.0, cmdline=["httpd"], connections=by_pid[1]
        ),
        2: _ProcSnapshot(
            name="httpd", ppid=1, create_time=2.0, cmdline=["httpd"], connections=by_pid[2]
        ),
        3: _ProcSnapshot(
            name="node",
            ppid=9,
            create_time=3.0,
            cmdline=["node", "server.js"],
            connections=by_pid[3],
            cpu_percent=1.5,
            memory_mb=42.0,
        ),
    }
    monkeypatch.setattr(ProcessSampler, "describe", fake_describe(snaps))

    listeners = collect_listeners(ProcessSampler(), include_udp=False)
    keys = [x.key for x in listeners]
    assert keys == ["9/TCP", "3000/TCP", "8080/TCP"]

    apache = listeners[2]
    assert apache.pids == [1, 2]
    assert apache.addresses == ["0.0.0.0", "::"]
    assert "workers sharing one socket" in (apache.shared_reason or "")
    assert apache.primary is not None and apache.primary.pid == 1

    node = listeners[1]
    assert node.connections == 1  # inbound established on 3000
    assert node.remote_conns == ["1.2.3.4:443"]
    assert node.cpu_percent == 1.5 and node.memory_mb == 42.0

    unknown = listeners[0]
    assert unknown.processes == [] and unknown.pid is None

    with_udp = collect_listeners(ProcessSampler(), include_udp=True)
    assert "5353/UDP" in [x.key for x in with_udp]
    udp = next(x for x in with_udp if x.protocol == "UDP")
    assert udp.state == "BOUND"


def test_explain_shared_port() -> None:
    single = make_listener(processes=[make_process(pid=1, addresses=["127.0.0.1", "::1"])])
    assert "dual-stack" in (explain_shared_port(single) or "")
    plain = make_listener(processes=[make_process(pid=1)])
    assert explain_shared_port(plain) is None
    conflict = make_listener(
        processes=[make_process(pid=1, name="node"), make_process(pid=2, name="python")]
    )
    assert "conflict" in (explain_shared_port(conflict) or "")
    reuseport = make_listener(
        processes=[make_process(pid=1, name="app", ppid=7), make_process(pid=2, name="app", ppid=8)]
    )
    assert "SO_REUSEPORT" in (explain_shared_port(reuseport) or "")
    assert explain_shared_port(make_listener(processes=[])) is None


def test_sampler_prunes_dead_pids() -> None:
    sampler = ProcessSampler()
    sampler._procs[999999] = object()  # type: ignore[assignment]  # stand-in for a vanished process
    sampler.begin_cycle({})
    sampler.end_cycle(set())
    assert sampler._procs == {}


def test_sampler_describe_nonexistent_pid() -> None:
    sampler = ProcessSampler()
    sampler.begin_cycle({})
    snap = sampler.describe(2**22 + 12345)
    assert snap.accessible is False
