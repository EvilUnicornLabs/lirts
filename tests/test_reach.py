from __future__ import annotations

import socket
import subprocess

from typer.testing import CliRunner

from lirts import cli
from lirts.collectors import reach


def test_check_host_with_fake_ping(monkeypatch) -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def fake_run(cmd, **kw):
        assert cmd[0] == "ping" and cmd[-1] == "127.0.0.1"
        return subprocess.CompletedProcess(
            cmd, 0, "64 bytes from 127.0.0.1: icmp_seq=0 ttl=64 time=0.123 ms\n", ""
        )

    monkeypatch.setattr(reach.subprocess, "run", fake_run)
    try:
        result = reach.check_host("localhost", port)
        assert result.ip in ("127.0.0.1", "::1") and result.dns_error is None
        assert result.ping_ms == 0.123 and result.tcp_ms is not None and result.ok
        labels = [label for label, _, _ in result.lines()]
        assert labels == ["DNS", "Ping", f"TCP {port}"]
    finally:
        server.close()
    dead = reach.check_host("127.0.0.1", port)  # closed now
    assert dead.tcp_error and not dead.ok

    def no_reply(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 2, "", "1 packets transmitted, 0 packets received, 100.0% packet loss"
        )

    monkeypatch.setattr(reach.subprocess, "run", no_reply)
    r = reach.check_host("127.0.0.1")
    assert r.ping_error and "packet loss" in r.ping_error and not r.ok


def test_unresolvable_host() -> None:
    r = reach.check_host("no-such-host.invalid", 22)
    assert r.dns_error and not r.ok and r.lines()[0][2] is False


def test_cli_reach(monkeypatch) -> None:
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, "time=1.5 ms", "")

    monkeypatch.setattr(reach.subprocess, "run", fake_run)
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        result = CliRunner().invoke(cli.app, ["reach", "127.0.0.1", str(port)])
        assert result.exit_code == 0 and "reachable" in result.output and "1.5 ms" in result.output
    finally:
        server.close()
    result = CliRunner().invoke(cli.app, ["reach", "no-such-host.invalid"])
    assert result.exit_code == 1


def test_tcp_connect_reports_the_latency_of_an_open_port() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    ms, error = reach.tcp_connect("127.0.0.1", port=port, timeout=1.0)

    server.close()
    assert error is None and ms is not None and ms >= 0.0


def test_tcp_connect_reports_a_closed_port() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.close()

    ms, error = reach.tcp_connect("127.0.0.1", port=port, timeout=1.0)

    assert ms is None and error


def test_ssh_target_reads_the_hostname_and_port_ssh_would_use(monkeypatch) -> None:
    def fake_run(cmd, **kw):
        assert cmd[:2] == ["ssh", "-G"]
        return subprocess.CompletedProcess(cmd, 0, "hostname db.internal\nport 2222\n", "")

    monkeypatch.setattr(reach.subprocess, "run", fake_run)

    assert reach.ssh_target("bastion") == ("db.internal", 2222)


def test_ssh_target_is_none_when_ssh_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        reach.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 255, "", "no")
    )

    assert reach.ssh_target("nope") is None
