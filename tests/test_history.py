from __future__ import annotations

import json
import time
from pathlib import Path

from lirts.history import (
    HistoryEntry,
    HistoryStore,
    last_snapshot_path,
    load_last_snapshot,
    save_last_snapshot,
)
from lirts.identity import ROLE_BACKEND
from lirts.models import ACTIVITY_ACTIVE, Identity, Insight
from tests.conftest import make_listener, make_process


def test_events_started_stopped_restarted() -> None:
    store = HistoryStore()
    a = make_listener(
        port=3000,
        identity=Identity("API", ROLE_BACKEND, 0.9),
        processes=[make_process(pid=1, name="node")],
    )
    b = make_listener(
        port=4000,
        identity=Identity("Web", ROLE_BACKEND, 0.9),
        processes=[make_process(pid=2, name="node")],
    )
    assert store.update([a, b], now=1000.0) == []  # first refresh is silent
    assert a.cpu_history == [0.0] and a.first_seen == 1000.0

    c = make_listener(
        port=5000,
        identity=Identity("New", ROLE_BACKEND, 0.9),
        processes=[make_process(pid=3, name="python")],
    )
    a2 = make_listener(
        port=3000,
        identity=Identity("API", ROLE_BACKEND, 0.9),
        processes=[make_process(pid=9, name="node")],
    )
    events = store.update([a2, c], now=1010.0)
    messages = [e.message for e in events]
    assert any("[+] New started on 5000/TCP" in m for m in messages)
    assert any("[-] Web stopped on 4000/TCP" in m for m in messages)
    assert any("restarted" in m and "3000/TCP" in m for m in messages)
    assert a2.restarts_last_hour == 1
    assert len(a2.cpu_history) == 2

    # Port comes back after being gone counts as a restart too.
    b2 = make_listener(
        port=4000,
        identity=Identity("Web", ROLE_BACKEND, 0.9),
        processes=[make_process(pid=2, name="node")],
    )
    events = store.update([a2, c, b2], now=1020.0)
    assert any("back on 4000/TCP" in e.message for e in events)
    assert b2.restarts_last_hour == 1
    assert len(store.recent_events(2)) == 2


def test_conflict_event_and_activity_tracking() -> None:
    store = HistoryStore()
    lst = make_listener(port=3000)
    store.update([lst], now=1.0)
    lst.insights.append(Insight("error", "port-conflict", "Port conflict"))
    lst.activity = ACTIVITY_ACTIVE
    events = store.update([lst], now=2.0)
    assert any("port conflict" in e.message for e in events)
    assert lst.last_active == 2.0
    assert store.update([lst], now=3.0) == []  # conflict not re-reported


def test_persistence_round_trip_and_retention(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    store = HistoryStore(path=path, persist=True, window=5, retention_hours=1)
    lst = make_listener(port=3000, identity=Identity("API", ROLE_BACKEND, 0.9))
    base = time.time() - 100
    for i in range(8):
        store.update([lst], now=base + i)
    assert store.save(force=True)
    data = json.loads(path.read_text())
    assert data["entries"][0]["key"] == "3000/TCP"
    assert len(data["entries"][0]["cpu"]) == 5  # window respected

    reloaded = HistoryStore(path=path, persist=True, window=5, retention_hours=1)
    assert "3000/TCP" in reloaded.entries
    # first update after reload emits no events (nothing was "currently seen")
    assert reloaded.update([lst], now=base + 100) == []

    stale_store = HistoryStore(path=path, persist=True, window=5, retention_hours=0.00001)
    assert stale_store.entries == {}  # pruned on load


def test_save_is_throttled(tmp_path: Path) -> None:
    store = HistoryStore(path=tmp_path / "h.json", persist=True)
    assert store.save() is False  # nothing dirty
    store.update([make_listener()], now=1.0)
    assert store.save() is True
    store.update([make_listener()], now=2.0)
    assert store.save() is False  # within 30 s
    assert store.save(force=True) is True


def test_the_last_session_is_saved_loaded_and_diffed(tmp_path: Path) -> None:
    root = tmp_path / "state"
    a = make_listener(port=3000, identity=Identity("API", ROLE_BACKEND, 0.9))
    b = make_listener(port=6379, identity=Identity("Redis", "cache", 0.9))
    save_last_snapshot(root, [a, b])
    old = load_last_snapshot(root)
    assert old is not None
    a_changed = make_listener(port=3000, identity=Identity("Other", ROLE_BACKEND, 0.9))
    c = make_listener(port=9000, identity=Identity("New", ROLE_BACKEND, 0.9))
    diff = HistoryStore.diff_snapshots(old, HistoryStore.snapshot_payload([a_changed, c]))
    assert diff["missing"] == ["Redis on 6379/TCP"]
    assert diff["added"] == ["New on 9000/TCP"]
    assert diff["changed"] == ["3000/TCP: API (local) → Other (local)"]
    assert load_last_snapshot(tmp_path / "missing") is None


def test_corrupt_history_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "h.json"
    path.write_text("{not json")
    store = HistoryStore(path=path, persist=True)
    assert store.entries == {}


def test_untracked_protocols_are_not_reported_stopped() -> None:
    store = HistoryStore()
    tcp = make_listener(port=3000)
    udp = make_listener(port=5353, protocol="UDP")
    store.update([tcp, udp], now=1.0)
    events = store.update([tcp], now=2.0, protocols={"TCP"})
    assert events == []
    events = store.update([tcp], now=3.0, protocols={"TCP", "UDP"})
    assert any("5353/UDP" in e.message for e in events)


def test_helper_processes_are_not_restarts() -> None:
    from lirts.history import HistoryStore
    from lirts.models import Identity
    from tests.conftest import make_listener, make_process

    store = HistoryStore()
    now = 1000.0
    main = make_process(pid=852, name="logioptionsplus_agent")
    lst = make_listener(
        port=59869, processes=[main], identity=Identity("Logitech Options+", "system", 0.9)
    )
    store.update([lst], now=now, protocols={"TCP"})
    store.update([lst], now=now + 1, protocols={"TCP"})
    helper = make_process(pid=26886, name="logioptionsplus_updater")
    with_helper = make_listener(port=59869, processes=[main, helper], identity=lst.identity)
    events = store.update([with_helper], now=now + 2, protocols={"TCP"})
    assert any("helper PID [26886] joined" in e.message and "852" in e.message for e in events)
    events = store.update([lst], now=now + 5, protocols={"TCP"})
    assert any("PID [26886] left" in e.message for e in events)
    assert lst.restarts_last_hour == 0
    # a real restart: every previous PID gone
    replaced = make_listener(
        port=59869,
        processes=[make_process(pid=999, name="logioptionsplus_agent")],
        identity=lst.identity,
    )
    events = store.update([replaced], now=now + 8, protocols={"TCP"})
    assert any(" restarted " in e.message for e in events) and replaced.restarts_last_hour == 1
    ev = store.note(
        "[~] Logitech Options+ on 59869/TCP restarted by you (t): started PID 1", port=59869
    )
    assert ev in store.all_events() and ev.kind == "restarted"


def test_false_restarts_from_older_versions_are_dropped_on_load(tmp_path) -> None:
    import json

    from lirts.history import HistoryStore

    now = time.time()  # retention is measured against the real clock
    state = tmp_path / "history.json"
    state.write_text(
        json.dumps(
            {
                "version": 1,
                "saved_at": now,
                "entries": [
                    {
                        "key": "59869/TCP",
                        "port": 59869,
                        "protocol": "TCP",
                        "first_seen": 1.0,
                        "last_seen": now,
                        "pids": [852],
                        "names": ["agent"],
                        "service": "Logitech Options+",
                        "pid_changes": [now - 1000, now - 998, now - 996, now - 500],
                    }
                ],
                "events": [
                    {
                        "timestamp": now - 1000,
                        "level": "info",
                        "message": "[~] Logitech Options+ on 59869/TCP restarted (PID [852] → [852, 1, 2])",
                        "port": 59869,
                    },
                    {
                        "timestamp": now - 998,
                        "level": "info",
                        "message": "[~] Logitech Options+ on 59869/TCP restarted (PID [852, 1, 2] → [852, 3, 4])",
                        "port": 59869,
                    },
                    {
                        "timestamp": now - 996,
                        "level": "info",
                        "message": "[~] Logitech Options+ on 59869/TCP restarted (PID [852, 3, 4] → [852])",
                        "port": 59869,
                    },
                    {
                        "timestamp": now - 500,
                        "level": "info",
                        "message": "[~] Logitech Options+ on 59869/TCP restarted (PID [852] → [999])",
                        "port": 59869,
                    },
                ],
            }
        )
    )
    store = HistoryStore(path=state, persist=True)
    store.load()
    assert store.entries["59869/TCP"].pid_changes == [now - 500]  # only the real one remains
    assert store.entries["59869/TCP"].restarts_within(3600, now) == 1


def test_traffic_history_is_kept_and_persisted(tmp_path) -> None:
    from lirts.history import HistoryStore

    store = HistoryStore(path=tmp_path / "h.json", persist=True)
    lst = make_listener(port=3000, name="node")
    now = time.time()  # retention is measured against the real clock
    lst.bytes_in_rate, lst.bytes_out_rate = 1500.0, 200.0
    store.update([lst], now=now - 2, protocols={"TCP"})
    lst.bytes_in_rate, lst.bytes_out_rate = None, None
    store.update([lst], now=now - 1, protocols={"TCP"})
    assert lst.bytes_in_history == [1500, 0] and lst.bytes_out_history == [200, 0]
    assert store.save(force=True)
    again = HistoryStore(path=tmp_path / "h.json", persist=True)
    assert list(again.entries["3000/TCP"].bytes_in) == [1500, 0]


def test_the_last_session_file_sits_directly_in_the_state_root(tmp_path: Path) -> None:
    assert last_snapshot_path(tmp_path) == tmp_path / "last.json"


def test_history_entry_survives_a_json_round_trip() -> None:
    entry = HistoryEntry(
        key="3000/TCP",
        port=3000,
        protocol="TCP",
        first_seen=1.0,
        last_seen=2.0,
        pids=[11, 12],
        names=["node"],
        service="Vite",
        pid_changes=[1.5],
        errors=[(1.6, "it broke")],
        conflict=True,
        health="warning",
    )
    entry.cpu.extend([1.0, 2.0])
    entry.activity.extend(["idle", "hot"])

    back = HistoryEntry.from_dict(json.loads(json.dumps(entry.to_dict())), 5)

    assert back.key == "3000/TCP" and back.pids == [11, 12] and back.service == "Vite"
    assert back.pid_changes == [1.5] and back.errors == [(1.6, "it broke")]
    assert back.conflict is True and back.health == "warning"
    assert list(back.cpu) == [1.0, 2.0] and list(back.activity) == ["idle", "hot"]
    assert back.cpu.maxlen == 5


def test_restarts_within_counts_only_the_recent_pid_changes() -> None:
    entry = HistoryEntry(
        key="3000/TCP",
        port=3000,
        protocol="TCP",
        first_seen=0.0,
        last_seen=10_000.0,
        pid_changes=[1_000.0, 9_500.0, 9_900.0],
    )

    assert entry.restarts_within(3600, 10_000.0) == 2
    assert entry.restarts_within(100, 10_000.0) == 1
    assert entry.restarts_within(100_000, 10_000.0) == 3
