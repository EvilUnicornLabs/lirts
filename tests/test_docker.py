from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from typing import Any

from lirts.collectors import docker as dmod
from lirts.collectors.docker import (
    DockerProvider,
    apply_inspect,
    compute_stats_delta,
    parse_cli_ports,
    parse_list_entry,
    parse_started_at,
)

LIST_ENTRY: dict[str, Any] = {
    "Id": "2b6149cdc90783b36df88cbbe0b5e0ba4181dcf49ff0246129cb418db5d0ecd6",
    "Names": ["/basskult-redis-1"],
    "Image": "redis:7-alpine",
    "State": "running",
    "Status": "Up 24 minutes (healthy)",
    "Ports": [
        {"IP": "0.0.0.0", "PrivatePort": 6379, "PublicPort": 6380, "Type": "tcp"},
        {"IP": "::", "PrivatePort": 6379, "PublicPort": 6380, "Type": "tcp"},
        {"PrivatePort": 9999, "Type": "tcp"},
    ],
    "Labels": {
        "com.docker.compose.project": "basskult",
        "com.docker.compose.service": "redis",
        "com.docker.compose.project.working_dir": "/Users/me/Development/basskult",
    },
}

INSPECT: dict[str, Any] = {
    "State": {
        "Status": "running",
        "StartedAt": "2026-09-19T21:21:29.736032886Z",
        "Health": {"Status": "healthy"},
    },
    "RestartCount": 2,
    "Config": {
        "Env": ["PATH=/bin", "DB_PASSWORD=secret", "APP_ENV=local"],
        "Image": "redis:7-alpine",
    },
    "Mounts": [
        {
            "Type": "volume",
            "Name": "basskult_redis_data",
            "Source": "/var/lib/docker/volumes/x",
            "Destination": "/data",
            "RW": True,
        },
        {"Type": "bind", "Source": "/Users/me/app", "Destination": "/app", "RW": False},
    ],
}


def test_parse_started_at() -> None:
    ts = parse_started_at("2026-09-19T21:21:29.736032886Z")
    expected = datetime(2026, 9, 19, 21, 21, 29, 736032, tzinfo=UTC).timestamp()
    assert ts is not None and abs(ts - expected) < 0.001
    assert parse_started_at("0001-01-01T00:00:00Z") is None
    assert parse_started_at(None) is None
    assert parse_started_at("garbage") is None
    assert parse_started_at("2026-09-19T21:21:29Z") is not None


def test_parse_list_entry() -> None:
    info = parse_list_entry(LIST_ENTRY)
    assert info.name == "basskult-redis-1"
    assert info.short_id == "2b6149cdc907"
    assert info.host_ports == [6380]
    assert info.port_map == {6380: "6379/TCP"}
    assert info.internal_ports == [9999]
    assert info.stack == "basskult"
    assert info.service == "redis"
    assert info.working_dir == "/Users/me/Development/basskult"
    assert info.health == "healthy"


def test_parse_list_entry_with_cli_labels() -> None:
    entry = dict(LIST_ENTRY, Labels="com.docker.compose.project=x,com.docker.compose.service=y")
    info = parse_list_entry(entry)
    assert info.stack == "x" and info.service == "y"


def test_parse_cli_ports() -> None:
    ports = parse_cli_ports(
        "0.0.0.0:6380->6379/tcp, [::]:6380->6379/tcp, 8080/tcp, 127.0.0.1:53->53/udp"
    )
    assert ports[0] == {"IP": "0.0.0.0", "PublicPort": 6380, "PrivatePort": 6379, "Type": "tcp"}
    assert ports[1]["IP"] == "::"
    assert ports[2] == {"PrivatePort": 8080, "Type": "tcp"}
    assert ports[3] == {"IP": "127.0.0.1", "PublicPort": 53, "PrivatePort": 53, "Type": "udp"}
    assert parse_cli_ports("") == []


def test_apply_inspect_masks_env_and_reads_mounts() -> None:
    info = parse_list_entry(LIST_ENTRY)
    apply_inspect(info, inspect=INSPECT, mask_patterns=["PASSWORD"])
    assert info.env == {"PATH": "/bin", "DB_PASSWORD": "********", "APP_ENV": "local"}
    assert info.restart_count == 2
    assert info.health == "healthy"
    assert info.started_at is not None and info.uptime_seconds is not None
    assert [m.type for m in info.mounts] == ["volume", "bind"]
    assert info.mounts[0].name == "basskult_redis_data"
    assert info.mounts[1].rw is False
    assert info.has_persistence


def _stats(
    cpu_total: int, system: int, rx: int, tx: int, mem: int = 100 * 1024 * 1024
) -> dict[str, Any]:
    return {
        "cpu_stats": {
            "cpu_usage": {"total_usage": cpu_total},
            "system_cpu_usage": system,
            "online_cpus": 4,
        },
        "memory_stats": {
            "usage": mem,
            "limit": 8 * 1024**3,
            "stats": {"inactive_file": 10 * 1024 * 1024},
        },
        "networks": {"eth0": {"rx_bytes": rx, "tx_bytes": tx}},
        "pids_stats": {"current": 5},
    }


def test_compute_stats_delta() -> None:
    first = compute_stats_delta(None, cur=_stats(1000, 100000, 0, 0), dt=0.0)
    assert first["cpu_percent"] is None
    assert first["memory_mb"] == 90.0
    assert first["memory_limit_mb"] == 8192.0
    assert first["pids"] == 5.0
    second = compute_stats_delta(
        _stats(1000, 100000, 0, 0), cur=_stats(1500, 101000, 2048, 1024), dt=2.0
    )
    # 500 / 1000 * 4 cpus * 100 = 200 %
    assert second["cpu_percent"] == 200.0
    assert second["net_rx_rate"] == 1024.0
    assert second["net_tx_rate"] == 512.0
    # counters that went backwards (container restarted) yield no rate
    third = compute_stats_delta(
        _stats(1500, 101000, 2048, 1024), cur=_stats(10, 101500, 0, 0), dt=1.0
    )
    assert third["net_rx_rate"] is None
    assert third["cpu_percent"] is None


class FakeApi:
    def __init__(self) -> None:
        self.stats_calls = 0

    def containers(self):
        return [LIST_ENTRY]

    def inspect_container(self, cid):
        assert cid == LIST_ENTRY["Id"]
        return INSPECT

    def stats(self, cid, stream=False, one_shot=False):
        self.stats_calls += 1
        return _stats(1000 * self.stats_calls, 100000 * self.stats_calls, 100 * self.stats_calls, 0)

    def stop(self, cid, timeout=10):
        return None

    def restart(self, cid, timeout=10):
        raise RuntimeError("boom")

    def logs(self, cid, **kw):
        return b"line1\nline2\n"


class FakeClient:
    def __init__(self) -> None:
        self.api = FakeApi()

    def ping(self):
        return True

    def close(self):
        pass


def test_provider_with_fake_client() -> None:
    provider = DockerProvider(mask_patterns=["PASSWORD"])
    provider._client = FakeClient()
    provider._cli = None
    containers = provider.list_containers()
    assert provider.available
    assert len(containers) == 1
    c = containers[0]
    assert c.env["DB_PASSWORD"] == "********"
    assert c.cpu_percent is None  # first sample
    containers = provider.list_containers()
    assert containers[0].cpu_percent is not None
    assert containers[0].memory_mb == 90.0
    assert provider.stop("x") == (True, "stopped")
    ok, msg = provider.restart("x")
    assert ok is False and "docker CLI not found" in msg
    assert provider.logs("x") == "line1\nline2\n"
    assert provider.exec_command("abc")[:4] == ["docker", "exec", "-it", "abc"]


def test_provider_disabled() -> None:
    provider = DockerProvider(enabled=False)
    assert provider.list_containers() == []
    assert provider.available is False


def test_provider_cli_fallback(monkeypatch) -> None:
    provider = DockerProvider()
    provider._cli = "/usr/bin/docker"
    monkeypatch.setattr(dmod, "docker_sdk", None)

    def fake_run(cmd, **kw):
        if cmd[1] == "ps":
            row = {
                "ID": "abc123def456",
                "Names": "web",
                "Image": "nginx",
                "State": "running",
                "Status": "Up",
                "Ports": "0.0.0.0:8080->80/tcp",
                "Labels": "com.docker.compose.project=p",
            }
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(row) + "\n", stderr="")
        if cmd[1] == "inspect":
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps([INSPECT]), stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr(subprocess, "run", fake_run)
    containers = provider.list_containers()
    assert provider.available
    assert containers[0].name == "web"
    assert containers[0].host_ports == [8080]
    assert containers[0].stack == "p"
    assert containers[0].restart_count == 2


def test_provider_cli_failure(monkeypatch) -> None:
    provider = DockerProvider()
    provider._cli = "/usr/bin/docker"
    monkeypatch.setattr(dmod, "docker_sdk", None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Cannot connect"),
    )
    assert provider.list_containers() == []
    assert provider.available is False
    assert "Cannot connect" in (provider.last_error or "")


def test_compute_stats_delta_keeps_network_totals() -> None:
    first = compute_stats_delta(None, cur=_stats(1000, 100000, 500, 200), dt=0.0)
    assert first["net_rx_bytes"] == 500.0 and first["net_tx_bytes"] == 200.0
    assert first["net_rx_rate"] is None
    second = compute_stats_delta(
        _stats(1000, 100000, 500, 200), cur=_stats(2000, 200000, 1524, 200), dt=2.0
    )
    assert second["net_rx_rate"] == 512.0 and second["net_rx_bytes"] == 1524.0


def test_sample_stats_applies_the_last_delta_without_a_client() -> None:
    provider = DockerProvider(stats=True)
    provider._client = None
    container = parse_list_entry(LIST_ENTRY)
    provider._stats_last[container.id] = {
        "cpu_percent": 4.5,
        "memory_mb": 128.0,
        "memory_limit_mb": 512.0,
        "net_rx_rate": 10.0,
        "net_tx_rate": 20.0,
        "net_rx_bytes": 1000.0,
        "net_tx_bytes": 2000.0,
        "pids": 7.0,
    }

    provider.sample_stats([container])

    assert container.cpu_percent == 4.5 and container.memory_mb == 128.0
    assert container.net_rx_bytes == 1000 and container.net_tx_bytes == 2000
    assert container.pids_current == 7


def test_sample_stats_leaves_a_container_alone_when_nothing_was_sampled() -> None:
    provider = DockerProvider(stats=True)
    provider._client = None
    container = parse_list_entry(LIST_ENTRY)

    provider.sample_stats([container])

    assert container.cpu_percent is None and container.pids_current is None
