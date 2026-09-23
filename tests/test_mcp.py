"""The MCP server: tool payloads on the demo engine, a round trip over an in-memory client,
the refresh loop that keeps the engine open, and the ``lirts mcp`` command."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mcp.client import Client
from typer.testing import CliRunner

from lirts import cli
from lirts.cli import mcp as mcp_cmd
from lirts.config import DEFAULT_CONFIG, validate
from lirts.demo import DemoEngine
from lirts.doctor import OK, WARN, check_mcp, run_all
from lirts.mcp_server import build_server, mcp_available
from lirts.mcp_tools import (
    REPLAY_REFUSAL,
    events_payload,
    fix_payload,
    free_port_payload,
    kube_payload,
    listeners_payload,
    restart_payload,
    stacks_payload,
    topology_payload,
    who_payload,
)

runner = CliRunner()

READ_TOOLS = [
    "list_listeners",
    "who",
    "explain",
    "graph",
    "topology",
    "events",
    "patterns",
    "routes",
    "stacks",
    "kube",
    "health",
    "doctor",
]
ACTION_TOOLS = ["free_port", "fix", "restart"]


def _engine() -> DemoEngine:
    cfg = validate(dict(DEFAULT_CONFIG))
    cfg["refresh_interval"] = 60.0
    engine = DemoEngine(cfg)
    engine.refresh_sync()
    return engine


# ----- payloads -----------------------------------------------------------------------


def test_listeners_payload_is_the_list_json_shape_and_takes_the_filter_syntax() -> None:
    engine = _engine()

    everything = listeners_payload(engine)
    containers = listeners_payload(engine, filter_text="src:docker")
    one = listeners_payload(engine, port=5432)

    assert set(everything) == {"timestamp", "docker_available", "summary", "listeners"}
    assert len(everything["listeners"]) == 17
    assert {row["source"] for row in containers["listeners"]} == {"docker"}
    assert [row["port"] for row in one["listeners"]] == [5432]
    assert one["listeners"][0]["service"] == "PostgreSQL"
    assert one["listeners"][0]["container"]["name"] == "shop-db-1"


def test_who_payload_names_the_holder_and_what_usually_runs_there() -> None:
    engine = _engine()

    taken = who_payload(engine, 5432)
    squatted = who_payload(engine, 8080)
    free = who_payload(engine, 4444)

    assert taken["free"] is False and taken["service"] == "PostgreSQL"
    assert taken["project"] == "shop" and taken["left_over"] is None
    assert taken["usually"]["label"] == "shop/PostgreSQL" and taken["usually"]["days"] == 4
    assert [h["label"] for h in taken["also_used_by"]] == ["lab/Postgres 14"]
    assert squatted["usually"]["label"] == "shop/gateway"
    assert any(i["code"] == "squatter" for i in squatted["listener"]["insights"])
    assert free == {
        "port": 4444,
        "protocol": "TCP",
        "free": True,
        "usually": None,
        "also_used_by": [],
    }


def test_who_payload_flags_the_leftover_dev_server() -> None:
    engine = _engine()

    orphan = who_payload(engine, 8090)

    assert orphan["left_over"] == "parent gone (python, PID 8090)"


def test_topology_stacks_events_and_kube_payloads_are_plain_json() -> None:
    engine = _engine()

    graph = topology_payload(engine)
    stacks = stacks_payload(engine)
    events = events_payload(engine, 5)
    kube = kube_payload(engine)

    assert set(graph) == {"built_at", "nodes", "relations"} and len(graph["nodes"]) == 23
    assert list(stacks) == ["blog", "jobs", "shop"]
    assert [c["name"] for c in stacks["jobs"]] == ["jobs-worker-1"]
    assert [e["message"] for e in events] == ["[!] port conflict on 5432/TCP"]
    assert set(events[0]) == {"timestamp", "level", "message", "port"}
    assert kube["available"] is True and len(kube["pods"]) == 4


def test_kube_payload_says_why_when_kubernetes_is_off() -> None:
    engine = _engine()
    engine.snapshot.kube = None

    assert kube_payload(engine) == {
        "available": False,
        "error": "Kubernetes is off or kubectl is not available",
    }


def test_actions_run_through_the_engine_and_leave_a_note() -> None:
    engine = _engine()

    freed = free_port_payload(engine, 5173)
    fixed = fix_payload(engine, 9100)
    restarted = restart_payload(engine, 3000)
    nothing = fix_payload(engine, 4444)

    assert freed["ok"] is True and freed["steps"][0]["target"] == "PID 5173"
    assert (
        fixed["fix"] == "restart-container" and fixed["command"] == "docker restart jobs-worker-1"
    )
    assert restarted == {"ok": True, "port": 3000, "message": "restarted"}
    assert nothing == {"ok": False, "error": "nothing on 4444/TCP"}
    notes = [e.message for e in engine.history.all_events() if "(mcp)" in e.message]
    assert notes == [
        "[~] Vite dev server on 5173/TCP: freed (mcp)",
        "[~] worker on 9100/TCP: fix run, container-state: docker restart jobs-worker-1 (mcp)",
        "[~] web on 3000/TCP: restarted (mcp)",
    ]


def test_actions_are_refused_in_replay() -> None:
    engine = _engine()
    engine.replay = True  # type: ignore[attr-defined]  # what ReplayEngine sets

    assert free_port_payload(engine, 5173) == {"ok": False, "error": REPLAY_REFUSAL}
    assert fix_payload(engine, 9100) == {"ok": False, "error": REPLAY_REFUSAL}
    assert restart_payload(engine, 3000) == {"ok": False, "error": REPLAY_REFUSAL}
    assert not any("(mcp)" in e.message for e in engine.history.all_events())


# ----- the server over an in-memory client ----------------------------------------------


def _server(*, allow_actions: bool = False, refresh_interval: float = 60.0) -> Any:
    cfg = validate(dict(DEFAULT_CONFIG))
    cfg["refresh_interval"] = refresh_interval
    return build_server(
        DemoEngine(cfg), refresh_interval=refresh_interval, allow_actions=allow_actions
    )


async def test_read_only_server_lists_no_action_tools() -> None:
    server = _server()

    async with Client(server) as client:
        tools = await client.list_tools()

    assert [t.name for t in tools.tools] == READ_TOOLS
    assert server.name == "lirts" and "Ask `who`" in (server.instructions or "")


async def test_allow_actions_adds_the_three_action_tools() -> None:
    server = _server(allow_actions=True)

    async with Client(server) as client:
        tools = await client.list_tools()

    assert [t.name for t in tools.tools] == READ_TOOLS + ACTION_TOOLS


async def test_who_over_the_wire_answers_from_the_open_engine() -> None:
    server = _server()

    async with Client(server) as client:
        result = await client.call_tool("who", {"port": 5432})
        text = await client.call_tool("explain", {})

    assert result.is_error is False
    assert result.structured_content["service"] == "PostgreSQL"
    assert result.structured_content["usually"]["label"] == "shop/PostgreSQL"
    assert text.content[0].text.startswith("You have 16 TCP listener(s)")


async def test_health_tool_is_the_only_thing_that_checks() -> None:
    server = _server()
    engine = server.lirts_state.engine

    async with Client(server) as client:
        before = await client.call_tool("list_listeners", {"port": 9100})
        checked = await client.call_tool("health", {"port": 9100})

    row = before.structured_content["listeners"][0]
    assert row["status"] == "error"
    assert checked.structured_content["result"] == [
        {
            "port": 9100,
            "service": "worker",
            "project": "jobs",
            "ok": False,
            "tcp_ok": False,
            "tcp_latency_ms": 0.6,
            "tcp_error": "refused",
            "http_path": None,
            "http_status": None,
            "http_latency_ms": None,
            "docker_health": None,
        }
    ]
    assert [x.port for x in engine.snapshot.listeners if x.health.checked] == [9100]


async def test_server_holds_the_engine_open_and_closes_it_with_the_client() -> None:
    server = _server(refresh_interval=0.02)
    state = server.lirts_state

    async with Client(server) as client:
        await client.call_tool("events", {"limit": 1})
        await asyncio.sleep(0.15)
        during = state.refreshes

    assert during >= 3
    assert state.engine.snapshot.listeners  # the last snapshot survives the close
    assert not state.engine.state_root.exists()  # DemoEngine.close removed its temp dir


async def test_fix_over_the_wire_runs_only_with_allow_actions() -> None:
    server = _server(allow_actions=True)

    async with Client(server) as client:
        result = await client.call_tool("fix", {"port": 9100})

    assert result.structured_content["ok"] is True
    assert result.structured_content["command"] == "docker restart jobs-worker-1"


# ----- the command ---------------------------------------------------------------------


def test_mcp_command_builds_the_server_and_serves_stdio(monkeypatch) -> None:
    served: list[Any] = []

    async def fake_serve(server: Any) -> None:
        served.append(server)

    monkeypatch.setattr(mcp_cmd, "serve_stdio", fake_serve)

    result = runner.invoke(cli.app, ["mcp", "--demo", "--allow-actions", "--refresh", "0.7"])

    assert result.exit_code == 0, result.output
    (server,) = served
    state = server.lirts_state
    assert state.engine.__class__.__name__ == "DemoEngine"
    assert state.refresh_interval == 0.7
    assert state.engine.cfg.bandwidth.mode == "off"
    assert "free_port" in [t.name for t in asyncio.run(server.list_tools())]


def test_mcp_command_refuses_to_run_without_the_sdk(monkeypatch) -> None:
    monkeypatch.setattr(mcp_cmd, "mcp_available", lambda: False)

    result = runner.invoke(cli.app, ["mcp", "--demo"])

    assert result.exit_code == 2
    assert "pip install 'lirts[mcp]'" in result.output


@pytest.mark.parametrize("installed", [True, False], ids=["installed", "missing"])
def test_doctor_reports_whether_the_mcp_sdk_is_there(monkeypatch, installed: bool) -> None:
    monkeypatch.setattr("lirts.doctor.find_spec", lambda name: object() if installed else None)
    monkeypatch.setattr("lirts.doctor.version", lambda name: "2.2.0")

    check = check_mcp()

    assert check.name == "mcp"
    if installed:
        assert (
            check.status == OK and check.detail == "mcp SDK 2.2.0; `lirts mcp` serves coding agents"
        )
    else:
        assert (
            check.status == WARN
            and check.hint == "pip install 'lirts[mcp]'  (or: pipx inject lirts mcp)"
        )


def test_doctor_runs_the_mcp_check_last() -> None:
    assert mcp_available()
    assert run_all()[-1].name == "mcp"
