"""Typer runner tests for the commands the other CLI test files do not reach.

The dashboard commands (``lirts``, ``tui``, ``setup``) are checked up to the point
where the Textual app would start; ``routes``, ``doctor`` and ``kube exec`` run on
fakes so that no proxy, no kubectl and no real machine is touched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from lirts import cli
from lirts.cli import kube as kube_cmd
from lirts.cli import listing, root
from lirts.collectors.proxies_route import Route

runner = CliRunner()


@pytest.fixture
def launched(monkeypatch) -> list[tuple[Any, dict[str, Any], bool]]:
    """Records every dashboard start instead of opening Textual."""
    calls: list[tuple[Any, dict[str, Any], bool]] = []

    def fake_run_app(engine: Any, *, config: dict[str, Any], setup: bool = False) -> None:
        calls.append((engine, config, setup))

    monkeypatch.setattr("lirts.tui.app.run_app", fake_run_app)
    return calls


def test_root_command_opens_the_dashboard_with_the_flags_applied(launched) -> None:
    result = runner.invoke(cli.app, ["--demo", "--udp", "--theme", "nord", "--refresh", "0.1"])
    assert result.exit_code == 0, result.output
    engine, config, setup = launched[0]
    assert engine.__class__.__name__ == "DemoEngine"
    assert config["show_udp"] is True
    assert config["theme"] == "nord"
    assert config["refresh_interval"] == 0.5  # clamped to the minimum
    assert setup is False


def test_tui_command_opens_the_dashboard(launched) -> None:
    result = runner.invoke(cli.app, ["tui", "--demo", "--no-docker", "--no-probe"])
    assert result.exit_code == 0, result.output
    _, config, setup = launched[0]
    assert config["docker"]["enabled"] is False
    assert config["http_probe"]["enabled"] is False
    assert setup is False


def test_setup_command_opens_the_wizard(launched, monkeypatch) -> None:
    class FakeEngine:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config

    monkeypatch.setattr(root, "Engine", FakeEngine)
    result = runner.invoke(cli.app, ["setup"])
    assert result.exit_code == 0, result.output
    engine, _, setup = launched[0]
    assert isinstance(engine, FakeEngine) and setup is True


def test_demo_and_replay_cannot_be_combined(tmp_path: Path) -> None:
    recording = tmp_path / "r.jsonl"
    recording.write_text("")
    result = runner.invoke(cli.app, ["list", "--demo", "--replay", str(recording)])
    assert result.exit_code == 2
    assert "cannot be combined" in result.output


def test_replay_of_an_unreadable_file_exits_two(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["list", "--replay", str(tmp_path / "missing.jsonl")])
    assert result.exit_code == 2
    assert "Cannot replay" in result.output


def test_record_rejects_a_bad_duration() -> None:
    result = runner.invoke(cli.app, ["record", "soon", "--demo"])
    assert result.exit_code == 2
    assert "bad duration" in result.output


def test_routes_command_prints_virtual_hosts(monkeypatch) -> None:
    routes = [
        Route(
            source="nginx",
            listen_port=80,
            names=["shop.test"],
            upstream="127.0.0.1:5173",
            upstream_port=5173,
        ),
        Route(source="httpd", listen_port=8080, names=[], doc_root="/srv/www"),
    ]
    monkeypatch.setattr(listing, "discover_routes", lambda: (routes, ["nginx -T ok"]))
    result = runner.invoke(cli.app, ["routes"])
    assert result.exit_code == 0, result.output
    assert "shop.test" in result.output and "127.0.0.1:5173" in result.output
    assert "(default server)" in result.output and "/srv/www" in result.output

    result = runner.invoke(cli.app, ["routes", "--json"])
    assert result.exit_code == 0, result.output
    assert '"listen_port": 80' in result.output and '"notes"' in result.output


def test_routes_command_without_a_proxy(monkeypatch) -> None:
    monkeypatch.setattr(listing, "discover_routes", lambda: ([], ["nginx not installed"]))
    result = runner.invoke(cli.app, ["routes"])
    assert result.exit_code == 0 and "No routes found. nginx not installed" in result.output


def test_doctor_reports_checks_and_exits_on_failures(monkeypatch) -> None:
    from lirts.doctor import Check

    checks = [
        Check(name="python", status="ok", detail="3.11.8"),
        Check(name="docker", status="warn", detail="not running", hint="start Docker Desktop"),
        Check(name="sockets", status="fail", detail="no permission"),
    ]
    monkeypatch.setattr(listing, "run_all", lambda config: checks)
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 1
    assert "python" in result.output and "start Docker Desktop" in result.output

    monkeypatch.setattr(listing, "run_all", lambda config: checks[:1])
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0 and "3.11.8" in result.output


class FakeKubeProvider:
    """Just enough of KubeProvider for the commands that only build a command line."""

    def __init__(self) -> None:
        self.kubectl = "/usr/bin/kubectl"
        self.context = "ctx1"
        self.exec_calls: list[tuple[str, str]] = []

    def fetch(self) -> Any:
        from lirts.collectors.kube_models import KubePod, KubeState

        pod = KubePod(
            name="api-7d9f-abc12",
            namespace="shop",
            phase="Running",
            ready=1,
            total=1,
            restarts=0,
            created=None,
            node="node-1",
            owner_kind="ReplicaSet",
            owner="api-7d9f",
        )
        return KubeState(available=True, context="ctx1", namespace="shop", pods=[pod])

    def exec_command(self, namespace: str, *, pod: str, container: str | None = None) -> list[str]:
        self.exec_calls.append((namespace, pod))
        return ["kubectl", "exec", "-it", "-n", namespace, pod, "--", "sh"]


def test_kube_exec_hands_over_to_kubectl(monkeypatch) -> None:
    provider = FakeKubeProvider()
    monkeypatch.setattr(kube_cmd, "_kube", lambda config, **kw: provider)
    monkeypatch.setattr(kube_cmd.subprocess, "call", lambda cmd: 0)
    result = runner.invoke(cli.app, ["kube", "exec", "api-7d9f"])
    assert result.exit_code == 0, result.output
    assert provider.exec_calls == [("shop", "api-7d9f-abc12")]


def test_kube_exec_on_an_unknown_pod_exits_one(monkeypatch) -> None:
    monkeypatch.setattr(kube_cmd, "_kube", lambda config, **kw: FakeKubeProvider())
    monkeypatch.setattr(kube_cmd.subprocess, "call", lambda cmd: 0)
    result = runner.invoke(cli.app, ["kube", "exec", "nope"])
    assert result.exit_code == 1
    assert "No pod named or starting with 'nope'" in result.output


def test_who_names_the_holder_of_a_port_on_the_demo_machine() -> None:
    result = runner.invoke(cli.app, ["who", "5432", "--demo"])

    assert result.exit_code == 0, result.output
    assert "5432/TCP  PostgreSQL  (shop)" in result.output
    assert "Holder     postgres" in result.output
    assert "Container  shop-db-1 (stack shop)" in result.output
    assert "Usually    shop/PostgreSQL · 5 days · last today" in result.output
    assert "also used by lab/Postgres 14 (seen 3 days)" in result.output
    assert "Port conflict" in result.output


def test_who_reports_a_left_over_process_and_its_insight() -> None:
    result = runner.invoke(cli.app, ["who", "8090", "--demo"])

    assert result.exit_code == 0, result.output
    assert "Left over  parent gone (python, PID 8090)" in result.output
    assert "nothing supervises it any more" in result.output


def test_who_exits_one_when_nothing_listens() -> None:
    result = runner.invoke(cli.app, ["who", "4321", "--demo"])

    assert result.exit_code == 1
    assert "nothing on 4321" in result.output


def test_free_shows_the_plan_and_refuses_to_act_on_the_demo_machine() -> None:
    result = runner.invoke(cli.app, ["free", "8090", "--demo", "--yes"])

    assert result.exit_code == 2
    assert "8090/TCP  Python static server" in result.output
    assert "terminate python (PID 8090)" in result.output
    assert "demo mode: actions are disabled" in result.output


def test_free_stops_the_container_behind_a_published_port() -> None:
    result = runner.invoke(cli.app, ["free", "3000", "--demo", "--yes"])

    assert result.exit_code == 2
    assert "docker stop shop-web-1" in result.output


def test_free_says_so_when_the_port_is_already_free() -> None:
    result = runner.invoke(cli.app, ["free", "4321", "--demo", "--yes"])

    assert result.exit_code == 0, result.output
    assert "4321/TCP is already free" in result.output
