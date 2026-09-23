from __future__ import annotations

import pytest

from lirts.models import (
    Activity,
    Event,
    EventKind,
    Insight,
    Level,
    Snapshot,
    Status,
    is_docker_proxy_name,
)
from tests.conftest import make_container, make_listener, make_process


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("com.docker.backend", True),
        ("docker-proxy", True),
        ("vpnkit", True),
        ("OrbStack Helper", True),
        ("node", False),
        ("", False),
        (None, False),
    ],
    ids=["docker_backend", "docker_proxy", "vpnkit", "orbstack", "node", "empty", "none"],
)
def test_docker_proxy_names_are_recognised(name: str | None, expected: bool) -> None:
    assert is_docker_proxy_name(name) is expected


def test_real_processes_leave_out_the_docker_proxy() -> None:
    listener = make_listener(
        port=5432,
        processes=[
            make_process(pid=1, name="com.docker.backend"),
            make_process(pid=2, name="postgres"),
        ],
    )

    assert [p.name for p in listener.real_processes] == ["postgres"]


def test_container_stats_stand_in_when_only_the_docker_proxy_is_visible() -> None:
    container = make_container(name="db", cpu_percent=12.5, memory_mb=64.0)
    listener = make_listener(
        port=5432,
        processes=[make_process(pid=1, name="com.docker.backend", cpu_percent=0.3)],
        container=container,
    )

    assert listener.uses_container_stats is True
    assert listener.cpu_percent == 12.5
    assert listener.memory_mb == 64.0


def test_a_listener_with_its_own_process_does_not_use_container_stats() -> None:
    container = make_container(name="db", cpu_percent=12.5)
    listener = make_listener(
        port=5432,
        processes=[make_process(pid=1, name="postgres", cpu_percent=3.0)],
        container=container,
    )

    assert listener.uses_container_stats is False
    assert listener.cpu_percent == 3.0


def test_is_web_is_true_for_web_roles_and_for_anything_answering_http() -> None:
    from lirts.models import HttpProbe, Identity

    frontend = make_listener(port=5173, identity=Identity("Vite", "frontend", 0.9))
    database = make_listener(port=5432, identity=Identity("PostgreSQL", "db", 0.9))
    answering = make_listener(
        port=9999, identity=Identity("?", "unknown", 0.0), http=HttpProbe(attempted=True, ok=True)
    )

    assert frontend.is_web is True
    assert database.is_web is False
    assert answering.is_web is True


def test_add_insight_ignores_a_duplicate_code_and_message() -> None:
    listener = make_listener(port=3000)

    listener.add_insight(Insight(Level.WARNING, "cpu-high", "High CPU: 90%"))
    listener.add_insight(Insight(Level.WARNING, "cpu-high", "High CPU: 90%"))
    listener.add_insight(Insight(Level.WARNING, "cpu-high", "High CPU: 95%"))

    assert [i.message for i in listener.insights] == ["High CPU: 90%", "High CPU: 95%"]


def test_level_rank_orders_info_warning_error() -> None:
    assert Level.INFO.rank == 0
    assert Level.WARNING.rank == 1
    assert Level.ERROR.rank == 2


def test_status_is_the_level_of_the_worst_insight() -> None:
    listener = make_listener(port=3000)
    assert listener.status == Status.HEALTHY

    listener.add_insight(Insight(Level.INFO, "shared-port", "shared"))
    assert listener.status == Status.HEALTHY

    listener.add_insight(Insight(Level.WARNING, "cpu-high", "High CPU"))
    assert listener.status == Status.WARNING

    listener.add_insight(Insight(Level.ERROR, "zombie", "zombie process"))
    assert listener.status == Status.ERROR


def test_a_row_with_neither_process_nor_container_is_unknown() -> None:
    assert make_listener(port=3000, processes=[]).status == Status.UNKNOWN


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("[+] Vite started on 5173/TCP", EventKind.STARTED),
        ("[+] Vite back on 5173/TCP", EventKind.BACK),
        ("[-] Vite stopped on 5173/TCP", EventKind.STOPPED),
        ("[~] Vite on 5173/TCP restarted (PID [1] → [2])", EventKind.RESTARTED),
        ("[~] Vite on 5173/TCP replaced (PID [1] → [2])", EventKind.REPLACED),
        ("[~] Vite on 5173/TCP degraded: slow", EventKind.DEGRADED),
        ("[~] Vite on 5173/TCP changed: helper PID [2] joined", EventKind.CHANGED),
        ("[!] port conflict on 5173/TCP", EventKind.CONFLICT),
        ("[!] Vite on 5173/TCP is failing", EventKind.FAILING),
        ("[✓] Vite on 5173/TCP recovered", EventKind.RECOVERED),
        ("something else entirely", EventKind.OTHER),
    ],
    ids=[
        "started",
        "back",
        "stopped",
        "restarted",
        "replaced",
        "degraded",
        "changed",
        "conflict",
        "failing",
        "recovered",
        "other",
    ],
)
def test_event_kind_is_read_from_the_message(message: str, expected: EventKind) -> None:
    assert Event(1.0, Level.INFO, message).kind == expected


def test_event_key_is_the_port_the_message_names() -> None:
    assert Event(1.0, Level.INFO, "[-] Vite stopped on 5173/TCP").key == "5173/TCP"
    assert Event(1.0, Level.INFO, "nothing to see here").key is None


def test_snapshot_summary_counts_rows_by_activity_status_and_source() -> None:
    idle = make_listener(port=3000)
    hot = make_listener(port=5173)
    hot.activity = Activity.HOT
    broken = make_listener(port=8000, container=make_container(name="api"))
    broken.source = "docker"
    broken.add_insight(Insight(Level.ERROR, "zombie", "zombie process"))

    summary = Snapshot(listeners=[idle, hot, broken]).summary

    assert summary["total"] == 3
    assert summary["idle"] == 2
    assert summary["hot"] == 1
    assert summary["errors"] == 1
    assert summary["docker"] == 1


def test_snapshot_lookup_by_key_and_by_port() -> None:
    listener = make_listener(port=3000)
    snapshot = Snapshot(listeners=[listener])

    assert snapshot.by_key("3000/TCP") is listener
    assert snapshot.by_port(3000) is listener
    assert snapshot.by_port(3000, "UDP") is None
    assert snapshot.by_key("nope") is None
