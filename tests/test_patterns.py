from __future__ import annotations

import json
import time
from pathlib import Path

from lirts.models import Event, Identity, Snapshot
from lirts.patterns import Pattern, PatternStore, classify
from tests.conftest import make_listener


def _events(now: float) -> list[Event]:
    return [
        Event(now - 3 * 86400, "error", "[!] port conflict on 5432/TCP", 5432),
        Event(now - 2 * 86400, "error", "[!] port conflict on 5432/TCP", 5432),
        Event(now - 60, "error", "[!] port conflict on 5432/TCP", 5432),
        Event(now - 50, "info", "[-] Redis on 6379/TCP: SIGTERM sent by you (k) to PID [1]", 6379),
        Event(now - 40, "info", "[~] Redis on 6379/TCP restarted by you (t): started PID 2", 6379),
        Event(now - 30, "info", "[~] API on 8000/TCP restarted (PID [1] → [2])", 8000),
        Event(now - 40 * 86400, "error", "[!] port conflict on 9999/TCP", 9999),  # too old
    ]


def test_classify() -> None:
    now = time.time()
    kinds = [classify(e) for e in _events(now)]
    assert kinds == [
        "conflict",
        "conflict",
        "conflict",
        "killed",
        "user-restart",
        "restarted",
        "conflict",
    ]
    assert classify(Event(now, "info", "[+] X started on 1/TCP", 1)) is None


def test_ingest_recurring_and_persistence(tmp_path: Path) -> None:
    now = time.time()
    path = tmp_path / "patterns.json"
    store = PatternStore(path=path, persist=True, days=30, min_count=3)
    pg = make_listener(port=5432, name="postgres", identity=Identity("PostgreSQL", "db", 0.9))
    store.ingest(_events(now), listeners=[pg], now=now)
    store.ingest(
        _events(now), listeners=[pg], now=now
    )  # the same events again are not double counted
    rec = store.recurring()
    assert [(p.key, p.kind, p.count) for p in rec] == [("5432/TCP", "conflict", 3)]
    assert (
        rec[0].active_days == 3
        and "PostgreSQL" in rec[0].services
        and "postgres" in rec[0].services
    )
    assert ("9999/TCP", "conflict") not in store.patterns  # pruned: older than the window
    assert store.for_key("6379/TCP") and {p.kind for p in store.for_key("6379/TCP")} == {
        "killed",
        "user-restart",
    }
    text = store.describe(rec[0], now)
    assert text.startswith(
        "5432/TCP (PostgreSQL, postgres): port conflict 3× in 30 days on 3 day(s), last "
    )
    summary = store.summarise(now)
    assert (
        len(summary) == 1
        and summary[0][0] == "warning"
        and "pin one of them" in (summary[0][2] or "")
    )
    assert store.save(force=True) and json.loads(path.read_text())["patterns"]
    again = PatternStore(path=path, persist=True, days=30, min_count=2)
    again.load()
    assert [(p.key, p.kind) for p in again.recurring()] == [("5432/TCP", "conflict")]
    again.clear()
    assert not again.patterns and json.loads(path.read_text())["patterns"] == []


def test_event_insights_include_patterns() -> None:
    from lirts.insights import event_insights

    snap = Snapshot(patterns=[("warning", "pattern: 5432/TCP: port conflict 5× in 30 days", None)])
    items = event_insights([], snapshot=snap)
    assert items and items[-1][1].startswith("pattern: 5432/TCP")


def test_engine_learns_patterns(tmp_path: Path) -> None:
    from lirts.config import DEFAULT_CONFIG, validate
    from lirts.engine import Engine

    cfg = validate({**DEFAULT_CONFIG, "insights": {**DEFAULT_CONFIG["insights"], "pattern_min": 2}})
    engine = Engine(cfg, state_root=tmp_path)
    now = time.time()
    lst = make_listener(port=5432, name="postgres", identity=Identity("PostgreSQL", "db", 0.9))
    engine.patterns.ingest(
        [
            Event(now - 10, "error", "[!] port conflict on 5432/TCP", 5432),
            Event(now - 5, "error", "[!] port conflict on 5432/TCP", 5432),
        ],
        listeners=[lst],
        now=now,
    )
    assert engine.patterns.recurring()[0].count == 2
    engine.close()
    assert (tmp_path / "patterns.json").exists()


def test_cli_patterns(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from lirts import cli

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(cli.app, ["patterns"])
    assert result.exit_code == 0 and "Nothing recurring" in result.output
    store = PatternStore(
        path=tmp_path / "lirts" / "patterns.json", persist=True, days=30, min_count=2
    )
    now = time.time()
    store.ingest(
        [
            Event(now - 10, "error", "[!] port conflict on 5432/TCP", 5432),
            Event(now - 5, "error", "[!] port conflict on 5432/TCP", 5432),
            Event(now - 1, "error", "[!] port conflict on 5432/TCP", 5432),
        ],
        listeners=[],
        now=now,
    )
    store.save(force=True)
    result = runner.invoke(cli.app, ["patterns"])
    assert (
        result.exit_code == 0 and "5432/TCP" in result.output and "port conflict" in result.output
    )
    result = runner.invoke(cli.app, ["patterns", "--json"])
    assert json.loads(result.output)[0]["count"] == 3
    result = runner.invoke(cli.app, ["patterns", "--clear"])
    assert "Forgot" in result.output


def test_pattern_survives_a_json_round_trip() -> None:
    pattern = Pattern(
        key="5432/TCP",
        kind="conflict",
        first=100.0,
        last=200.0,
        days={"2026-09-20": 2, "2026-09-21": 1},
        services=["PostgreSQL", "postgres"],
        samples=["a", "b", "c", "d"],
    )

    back = Pattern.from_dict(json.loads(json.dumps(pattern.to_dict())))

    assert back.key == "5432/TCP" and back.kind == "conflict"
    assert back.days == {"2026-09-20": 2, "2026-09-21": 1} and back.count == 3
    assert back.services == ["PostgreSQL", "postgres"]
    assert back.samples == ["b", "c", "d"]  # only the newest samples are written
    assert back.label == "port conflict" and back.active_days == 2


def test_prune_drops_day_tallies_that_fell_out_of_the_window() -> None:
    now = time.time()
    store = PatternStore(days=2, min_count=1)
    recent = time.strftime("%Y-%m-%d", time.localtime(now))
    old = time.strftime("%Y-%m-%d", time.localtime(now - 30 * 86400))
    store.patterns[("5432/TCP", "conflict")] = Pattern(
        key="5432/TCP", kind="conflict", days={recent: 1, old: 5}
    )
    store.patterns[("6379/TCP", "stopped")] = Pattern(key="6379/TCP", kind="stopped", days={old: 3})

    store.prune(now)

    assert store.patterns[("5432/TCP", "conflict")].days == {recent: 1}
    assert ("6379/TCP", "stopped") not in store.patterns
