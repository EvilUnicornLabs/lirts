from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from lirts import cli
from lirts.config import DEFAULT_CONFIG, validate
from lirts.demo import DemoEngine
from lirts.replay import (
    ReplayEngine,
    parse_duration,
    read_recording,
    record,
    snapshot_from_dict,
    snapshot_to_dict,
    to_jsonable,
    write_frame,
    write_header,
)


def _cfg() -> dict[str, Any]:
    cfg = validate(dict(DEFAULT_CONFIG))
    cfg["refresh_interval"] = 60.0
    return cfg


def test_snapshot_round_trip() -> None:
    engine = DemoEngine(_cfg())
    engine.refresh_sync()
    engine.world.started -= 80
    engine.refresh_sync()
    snap = engine.refresh_sync()
    data = json.loads(json.dumps(snapshot_to_dict(snap)))
    back = snapshot_from_dict(data)
    assert len(back.listeners) == len(snap.listeners)
    for a, b in zip(snap.listeners, back.listeners, strict=True):
        assert (a.key, a.state, a.source, a.identity, a.health, a.http) == (
            b.key,
            b.state,
            b.source,
            b.identity,
            b.health,
            b.http,
        )
        assert [p.pid for p in a.processes] == [p.pid for p in b.processes]
        assert (a.container is None) == (b.container is None)
        if a.container is not None and b.container is not None:
            assert a.container == b.container
        assert a.insights == b.insights and a.clients == b.clients and a.ssh == b.ssh
        if a.processes and a.processes[0].origin is not None:
            assert b.processes[0].origin == a.processes[0].origin
    assert back.kube is not None and back.kube.pods == snap.kube.pods
    assert back.routes == snap.routes and back.edges == snap.edges
    assert back.system == snap.system and back.events == snap.events
    assert back.ssh_sessions == snap.ssh_sessions and back.hosts_names == snap.hosts_names
    engine.close()


def test_record_and_replay(tmp_path: Path) -> None:
    engine = DemoEngine(_cfg())
    out = tmp_path / "rec.jsonl"
    frames = record(engine, seconds=0.6, interval=0.3, out=out)
    engine.close()
    assert frames >= 2
    header, loaded = read_recording(out)
    assert header["interval"] == 0.3 and len(loaded) == frames
    cfg = _cfg()
    replay = ReplayEngine(cfg, out)
    assert cfg["refresh_interval"] == 0.5  # clamped recorded interval
    first = replay.refresh_sync()
    assert len(first.listeners) == len(loaded[0].listeners)
    assert replay.position == f"replay 1/{frames}"
    for _ in range(frames):
        replay.refresh_sync()
    assert replay.position == f"replay 1/{frames}"  # looped
    assert replay.kill_and_wait([1])[0][1] is False
    assert replay.restart_process(first.listeners[0]) == (
        False,
        "replay mode: actions are disabled",
    )
    assert replay.containers and replay.stacks()
    replay.close()
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n")
    try:
        ReplayEngine(_cfg(), bad)
    except ValueError as exc:
        assert "not JSON" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_parse_duration() -> None:
    assert parse_duration("30") == 30 and parse_duration("30s") == 30
    assert parse_duration("5m") == 300 and parse_duration("1h") == 3600


def test_cli_record_list_explain_demo(tmp_path: Path) -> None:
    runner = CliRunner()
    out = tmp_path / "demo.jsonl"
    result = runner.invoke(cli.app, ["record", "1s", "--interval", "0.5", "-o", str(out), "--demo"])
    assert result.exit_code == 0, result.output
    assert out.exists() and "Play it back" in result.output
    result = runner.invoke(cli.app, ["list", "--replay", str(out)])
    assert result.exit_code == 0 and "PostgreSQL" in result.output
    result = runner.invoke(cli.app, ["explain", "--demo"])
    assert result.exit_code == 0 and "listener(s)" in result.output
    result = runner.invoke(cli.app, ["list", "--demo", "--replay", str(out)])
    assert result.exit_code == 2
    result = runner.invoke(cli.app, ["list", "--replay", str(tmp_path / "missing.jsonl")])
    assert result.exit_code == 2


def test_to_jsonable_flattens_dataclasses_tuples_deques_and_bad_floats() -> None:
    from collections import deque

    from lirts.models import Edge

    edge = Edge(client_pid=7, client_name="chrome", dst_port=5173, client_ports=[1, 2])

    assert to_jsonable(edge)["client_ports"] == [1, 2]
    assert to_jsonable(deque([1, 2])) == [1, 2]
    assert to_jsonable((1, "a")) == [1, "a"]
    assert to_jsonable({"a": {3}}) == {"a": [3]}
    assert to_jsonable(float("inf")) is None
    assert to_jsonable(float("nan")) is None
    assert to_jsonable("plain") == "plain"


def test_write_header_and_write_frame_produce_one_json_line_each(tmp_path: Path) -> None:
    engine = DemoEngine(_cfg())
    snapshot = engine.refresh_sync()
    engine.close()
    out = tmp_path / "rec.jsonl"

    with open(out, "w", encoding="utf-8") as fh:
        write_header(fh, 2.5)
        write_frame(fh, snapshot)

    header_line, frame_line = out.read_text(encoding="utf-8").splitlines()
    header = json.loads(header_line)
    frame = json.loads(frame_line)
    assert header["interval"] == 2.5 and header["recorded_at"] > 0 and header["lirts"]
    assert frame["t"] == snapshot.timestamp
    assert len(frame["snapshot"]["listeners"]) == len(snapshot.listeners)
