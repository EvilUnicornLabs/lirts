from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from lirts import cli
from lirts.cli import actions, common, listing
from lirts.cli.common import console, err_console
from lirts.config import state_dir
from lirts.identity import ROLE_BACKEND, ROLE_DB
from lirts.models import Identity, Insight, Snapshot
from tests.conftest import make_container, make_listener, make_process

runner = CliRunner()


class FakeEngine:
    def __init__(self, snap: Snapshot) -> None:
        self.snap = snap
        self.last_session_diff = {"missing": [], "added": [], "changed": []}
        self.closed = False
        self.killed: list[tuple[list[int], bool]] = []
        self.kube = SimpleNamespace(enabled=False, kubectl=None, snapshot=lambda: None)

    def close(self) -> None:
        self.closed = True

    def kill_and_wait(self, pids, force=False):
        self.killed.append((list(pids), force))
        return [(pid, True, "terminated") for pid in pids]


@pytest.fixture
def fake_collect(monkeypatch):
    api = make_listener(
        port=8000,
        name="python",
        cmdline=["python", "app.py"],
        identity=Identity("FastAPI", ROLE_BACKEND, 0.9, project="shop"),
        processes=[make_process(pid=4242, name="python", inbound_connections=2)],
    )
    api.insights.append(Insight("warning", "stale", "No activity for 7h", "kill it", "kill:4242"))
    db = make_listener(
        port=5432,
        name="com.docker.backend",
        identity=Identity("PostgreSQL", ROLE_DB, 0.95),
        container=make_container(name="shop-db-1", image="postgres:16", host_ports=[5432]),
        processes=[make_process(pid=7, name="com.docker.backend")],
    )
    snap = Snapshot(listeners=[api, db], docker_available=True, container_count=1)
    engine = FakeEngine(snap)
    # Each command module holds its own reference to _collect, so patch them all.
    for module in (actions, listing):
        monkeypatch.setattr(module, "_collect", lambda cfg, samples=1, **kw: (engine, engine.snap))
    return engine


def test_version() -> None:
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0 and "lirts" in result.output


def test_list_table_and_json(fake_collect) -> None:
    result = runner.invoke(cli.app, ["list", "--columns", "port,service,status"])
    assert result.exit_code == 0, result.output
    assert "FastAPI" in result.output and "PostgreSQL" in result.output
    assert "No activity for 7h" in result.output
    assert fake_collect.closed

    result = runner.invoke(cli.app, ["list", "--json", "--filter", "fastapi"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["total"] == 1
    row = payload["listeners"][0]
    assert row["port"] == 8000 and row["service"] == "FastAPI" and row["project"] == "shop"
    assert row["insights"][0]["code"] == "stale"

    result = runner.invoke(cli.app, ["list", "--json", "--port", "5432"])
    row = json.loads(result.output)["listeners"][0]
    assert row["container"]["name"] == "shop-db-1"


def test_kill_command(fake_collect) -> None:
    result = runner.invoke(cli.app, ["kill", "9999"])
    assert result.exit_code == 1 and "Nothing is listening" in result.output
    result = runner.invoke(cli.app, ["kill", "8000"], input="n\n")
    assert result.exit_code == 0 and fake_collect.killed == []
    result = runner.invoke(cli.app, ["kill", "8000", "--yes", "--force"])
    assert result.exit_code == 0, result.output
    assert fake_collect.killed == [([4242], True)]
    assert "terminated" in result.output


def test_explain_command(fake_collect) -> None:
    result = runner.invoke(cli.app, ["explain"])
    assert result.exit_code == 0, result.output
    assert "FastAPI on 8000 [shop]" in result.output
    assert "kill it" in result.output


def test_config_commands(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["config", "path"])
    assert result.exit_code == 0 and "defaults apply" in result.output
    target = tmp_path / "cfg.yaml"
    result = runner.invoke(cli.app, ["config", "init", "--config", str(target)])
    assert result.exit_code == 0 and target.exists()
    assert runner.invoke(cli.app, ["config", "init", "--config", str(target)]).exit_code == 1
    result = runner.invoke(
        cli.app, ["config", "set", "http_probe.interval", "42", "--config", str(target)]
    )
    assert result.exit_code == 0, result.output
    assert (
        "interval: 42" in runner.invoke(cli.app, ["config", "show", "--config", str(target)]).output
    )
    assert (
        runner.invoke(
            cli.app, ["config", "set", "refresh_interval.x", "1", "--config", str(target)]
        ).exit_code
        == 1
    )
    assert "dracula" in runner.invoke(cli.app, ["config", "themes"]).output
    assert str(state_dir()).startswith(str(tmp_path))


@pytest.mark.parametrize(
    ("mode", "colorterm", "color_system"),
    [("on", "truecolor", "truecolor"), ("off", None, "256")],
    ids=["truecolor-on", "truecolor-off"],
)
def test_truecolor_setting_forces_the_colour_depth_before_textual_starts(
    mode: str, colorterm: str | None, color_system: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLORTERM", "something")
    monkeypatch.delenv("TEXTUAL_COLOR_SYSTEM", raising=False)

    assert common.apply_color_depth({"ui": {"truecolor": mode}}) == mode
    assert os.environ.get("COLORTERM") == colorterm
    assert os.environ["TEXTUAL_COLOR_SYSTEM"] == color_system


def test_auto_leaves_the_colour_depth_to_the_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLORTERM", "something")
    monkeypatch.delenv("TEXTUAL_COLOR_SYSTEM", raising=False)

    assert common.apply_color_depth({"ui": {"truecolor": "auto"}}) == "auto"
    assert os.environ["COLORTERM"] == "something"
    assert "TEXTUAL_COLOR_SYSTEM" not in os.environ


def test_force_color_keeps_the_colours_when_the_output_is_piped() -> None:
    # Under pytest the shared consoles write to a captured pipe, not to a terminal.
    assert not common.console.is_terminal
    plain = [(con, con._force_terminal, con._color_system) for con in (console, err_console)]

    try:
        assert common.apply_force_color({"cli": {"force_color": False}}) is False
        assert common.apply_force_color({"cli": {"force_color": True}}) is True
        assert common.console.is_terminal and common.err_console.is_terminal
        assert common.console.color_system is not None
    finally:
        for con, forced, color_system in plain:
            con._force_terminal, con._color_system = forced, color_system
    assert not common.console.is_terminal
