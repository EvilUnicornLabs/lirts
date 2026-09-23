from __future__ import annotations

import pytest

from lirts.history_changes import pid_change_event, status_change_event
from lirts.history_entry import HistoryEntry
from lirts.models import Insight, Level, Status
from tests.conftest import make_listener, make_process


def _entry(**kw) -> HistoryEntry:
    """A history entry for 3000/TCP; keyword arguments override the defaults."""
    defaults = {
        "key": "3000/TCP",
        "port": 3000,
        "protocol": "TCP",
        "first_seen": 0.0,
        "last_seen": 100.0,
    }
    return HistoryEntry(**{**defaults, **kw})


def test_a_port_that_was_absent_comes_back_and_counts_as_a_restart() -> None:
    entry = _entry(pids=[11], names=["node"])
    listener = make_listener(port=3000, processes=[make_process(pid=12, name="node")])

    event = pid_change_event(listener, entry=entry, now=150.0, retention=3600.0, was_absent=True)

    assert event is not None
    assert event.message == "[+] Unknown back on 3000/TCP"
    assert event.level == Level.INFO
    assert entry.pid_changes == [150.0]
    assert entry.uptime_start == 150.0


def test_a_port_absent_longer_than_the_retention_window_is_not_a_restart() -> None:
    entry = _entry(pids=[11], names=["node"], last_seen=100.0)
    listener = make_listener(port=3000, processes=[make_process(pid=12, name="node")])

    event = pid_change_event(listener, entry=entry, now=100000.0, retention=3600.0, was_absent=True)

    assert event is None
    assert entry.pid_changes == []
    assert entry.uptime_start == 100000.0


def test_a_helper_pid_joining_is_reported_but_not_counted_as_a_restart() -> None:
    entry = _entry(pids=[11], names=["node"])
    listener = make_listener(
        port=3000,
        processes=[make_process(pid=11, name="node"), make_process(pid=12, name="node")],
    )

    event = pid_change_event(listener, entry=entry, now=150.0, retention=3600.0, was_absent=False)

    assert event is not None
    assert "helper PID [12] joined" in event.message
    assert "main PID [11] still running" in event.message
    assert entry.pid_changes == []


def test_all_new_pids_with_the_same_name_is_a_restart() -> None:
    entry = _entry(pids=[11], names=["node"])
    listener = make_listener(port=3000, processes=[make_process(pid=99, name="node")])

    event = pid_change_event(listener, entry=entry, now=150.0, retention=3600.0, was_absent=False)

    assert event is not None
    assert event.message == "[~] Unknown on 3000/TCP restarted (PID [11] → [99])"
    assert event.level == Level.INFO
    assert entry.pid_changes == [150.0]


def test_all_new_pids_with_another_name_is_a_replacement_and_warns() -> None:
    entry = _entry(pids=[11], names=["node"])
    listener = make_listener(port=3000, processes=[make_process(pid=99, name="python")])

    event = pid_change_event(listener, entry=entry, now=150.0, retention=3600.0, was_absent=False)

    assert event is not None
    assert "replaced (PID [11] → [99])" in event.message
    assert event.level == Level.WARNING


def test_unchanged_pids_produce_no_event() -> None:
    entry = _entry(pids=[11], names=["node"])
    listener = make_listener(port=3000, processes=[make_process(pid=11, name="node")])

    assert (
        pid_change_event(listener, entry=entry, now=150.0, retention=3600.0, was_absent=False)
        is None
    )


@pytest.mark.parametrize(
    ("was", "insight", "expected_level", "expected_text"),
    [
        (Status.HEALTHY, Insight(Level.ERROR, "boom", "it broke"), Level.ERROR, "is failing"),
        (Status.HEALTHY, Insight(Level.WARNING, "slow", "it is slow"), Level.WARNING, "degraded"),
        (Status.ERROR, None, Level.INFO, "recovered"),
    ],
    ids=["healthy_to_error", "healthy_to_warning", "error_to_healthy"],
)
def test_status_changes_are_reported_with_the_matching_level(
    was: str, insight: Insight | None, expected_level: str, expected_text: str
) -> None:
    entry = _entry(health=was)
    listener = make_listener(port=3000)
    listener.insights = [insight] if insight else []

    event = status_change_event(
        listener, entry=entry, status=listener.status, conflict=False, now=150.0
    )

    assert event is not None
    assert event.level == expected_level
    assert expected_text in event.message


def test_a_failure_caused_by_a_port_conflict_is_not_reported_twice() -> None:
    entry = _entry(health=Status.HEALTHY)
    listener = make_listener(port=3000)
    listener.insights = [Insight(Level.ERROR, "port-conflict", "Port conflict: two things")]

    assert (
        status_change_event(listener, entry=entry, status=Status.ERROR, conflict=True, now=150.0)
        is None
    )


def test_the_first_status_ever_seen_produces_no_event() -> None:
    entry = _entry(health="")
    listener = make_listener(port=3000)

    assert (
        status_change_event(listener, entry=entry, status=Status.HEALTHY, conflict=False, now=150.0)
        is None
    )
