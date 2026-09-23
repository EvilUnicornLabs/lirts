from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from lirts import cli
from lirts.cli import kube as kube_cmd
from lirts.collectors.kube import KubeProvider
from tests.test_kube_parse import DEPLOYMENTS, PODS, SERVICES

runner = CliRunner()


class FakeKubectl:
    """Stands in for subprocess.run: answers kubectl invocations from fixtures."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.fail: set[str] = set()

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        args = cmd[1:]
        joined = " ".join(args)
        out = ""
        code = 0
        if "current-context" in joined:
            out = "ctx1\n"
        elif "get-contexts" in joined:
            out = "ctx1\nctx2\n"
        elif "jsonpath={..namespace}" in joined:
            out = "dev"
        elif "cluster.server" in joined:
            out = "https://k8s.example:6443"
        elif "get pods" in joined:
            out = json.dumps(PODS)
        elif "get deployments" in joined:
            out = json.dumps(DEPLOYMENTS)
        elif "get services" in joined:
            out = json.dumps(SERVICES)
        elif "get namespaces" in joined:
            out = "dev prod"
        elif args[0] == "logs" or "logs" in args[:4]:
            out = "log line\n"
        elif "rollout" in joined:
            out = "deployment.apps/api restarted\n"
        elif "delete pod" in joined:
            out = 'pod "api" deleted\n'
        if any(word in joined for word in self.fail):
            code = 1
            out = ""
        return subprocess.CompletedProcess(cmd, code, stdout=out, stderr="boom" if code else "")


def make_provider(monkeypatch, tmp_path: Path) -> tuple[KubeProvider, FakeKubectl]:
    fake = FakeKubectl()
    monkeypatch.setattr("lirts.collectors.kube.subprocess.run", fake)
    provider = KubeProvider(enabled="auto", state_root=tmp_path)
    provider.kubectl = "/fake/kubectl"
    return provider, fake


def test_provider_fetch_and_actions(monkeypatch, tmp_path: Path) -> None:
    provider, fake = make_provider(monkeypatch, tmp_path)
    assert provider.enabled
    assert provider.current_context() == "ctx1" and provider.current_namespace() == "dev"
    assert provider.contexts() == [("ctx1", True), ("ctx2", False)]
    assert provider.namespaces() == ["dev", "prod"]
    state = provider.fetch()
    assert state.available and state.context == "ctx1" and state.namespace == "dev"
    assert state.server == "https://k8s.example:6443"
    assert len(state.pods) == 3 and len(state.deployments) == 1 and len(state.services) == 1
    assert provider.snapshot() is state
    assert provider.logs("dev", pod="api-7d9f-abc12", tail=5, previous=True) == "log line\n"
    assert any("--previous" in c for c in fake.calls)
    assert provider.rollout_restart("dev", kind="Deployment", name="api") == (
        True,
        "deployment.apps/api restarted",
    )
    assert provider.delete_pod("dev", "api")[0] is True
    cmd = provider.exec_command("dev", pod="api-7d9f-abc12", container="api")
    assert (
        cmd[:6] == ["/fake/kubectl", "exec", "-it", "-n", "dev", "api-7d9f-abc12"] and "-c" in cmd
    )
    fake.fail.add("get pods")
    state = provider.fetch()
    assert not state.available and "boom" in (state.error or "")


def test_provider_disabled_states(monkeypatch, tmp_path: Path) -> None:
    provider = KubeProvider(enabled="off", state_root=tmp_path)
    assert provider.enabled is False
    provider = KubeProvider(enabled="auto", state_root=tmp_path)
    provider.kubectl = None
    assert provider.enabled is False
    assert provider.fetch().error == "kubectl not found"
    provider.start()  # no thread when disabled
    assert provider._thread is None
    provider.close()


def _cli_provider(monkeypatch, tmp_path: Path) -> tuple[KubeProvider, FakeKubectl]:
    provider, fake = make_provider(monkeypatch, tmp_path)
    monkeypatch.setattr(
        kube_cmd, "_kube", lambda config, context, namespace, all_ns=False: provider
    )
    return provider, fake


def test_cli_kube(monkeypatch, tmp_path: Path) -> None:
    _cli_provider(monkeypatch, tmp_path)
    result = runner.invoke(cli.app, ["kube", "pods"])
    assert result.exit_code == 0, result.output
    assert (
        "api-7d9f-abc12" in result.output
        and "CrashLoopBackOff" in result.output
        and "2 pod(s) not healthy" in result.output
    )
    result = runner.invoke(cli.app, ["kube", "pods", "--json"])
    assert json.loads(result.output)[0]["name"] == "api-7d9f-abc12"
    result = runner.invoke(cli.app, ["kube", "services"])
    assert "postgres" in result.output and "5432→5432/TCP" in result.output
    result = runner.invoke(cli.app, ["kube", "logs", "api-7d9f", "--tail", "5"])
    assert result.exit_code == 0 and "log line" in result.output
    assert runner.invoke(cli.app, ["kube", "logs", "nope"]).exit_code == 1
    assert runner.invoke(cli.app, ["kube", "restart", "api"], input="n\n").exit_code == 0
    result = runner.invoke(cli.app, ["kube", "restart", "api", "--yes"])
    assert result.exit_code == 0 and "restarted" in result.output
    assert "* ctx1" in runner.invoke(cli.app, ["kube", "contexts"]).output
    assert "No tracked" in runner.invoke(cli.app, ["kube", "forwards"]).output
    assert runner.invoke(cli.app, ["kube", "stop"]).exit_code == 1
    assert runner.invoke(cli.app, ["kube", "forward", "service/postgres", "bad"]).exit_code == 1


async def test_kube_screen(monkeypatch, tmp_path: Path) -> None:
    from lirts.config import load_config
    from lirts.tui.app import LirtsApp
    from lirts.tui.screens import KubeScreen
    from tests.conftest import FakeEngine

    provider, fake = make_provider(monkeypatch, tmp_path)
    engine: Any = FakeEngine()
    engine.kube = provider
    cfg = load_config()
    cfg["refresh_interval"] = 60.0
    app = LirtsApp(engine, cfg)
    async with app.run_test(size=(170, 45)) as pilot:
        await pilot.pause()
        await pilot.press("K")
        await pilot.pause()
        assert isinstance(app.screen, KubeScreen)
        for _ in range(40):
            await pilot.pause()
            if app.screen.query_one("#kube-pods").row_count:
                break
            await __import__("asyncio").sleep(0.05)
        assert app.screen.query_one("#kube-pods").row_count == 3
        assert app.screen.query_one("#kube-services").row_count == 1
        await pilot.press("l")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "LogScreen"
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "ConfirmScreen"
        await pilot.press("y")
        await pilot.pause()
        await __import__("asyncio").sleep(0.3)
        assert any("rollout" in " ".join(c) for c in fake.calls)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, KubeScreen)


async def test_kube_arrows_and_namespace_picker(monkeypatch, tmp_path: Path) -> None:
    import asyncio

    from textual.widgets import OptionList, TabbedContent

    from lirts.config import load_config
    from lirts.tui.app import LirtsApp
    from lirts.tui.screens import KubeScreen, NamespaceScreen
    from tests.conftest import FakeEngine, wait_rows

    provider, _fake = make_provider(monkeypatch, tmp_path)
    engine: Any = FakeEngine()
    engine.kube = provider
    cfg = load_config()
    cfg["refresh_interval"] = 60.0
    app = LirtsApp(engine, cfg)  # type: ignore[arg-type]  # FakeEngine stands in for Engine
    async with app.run_test(size=(160, 45)) as pilot:
        await wait_rows(app, pilot)
        assert app.check_action("kubernetes", ()) is True
        await pilot.press("K")
        for _ in range(20):
            await pilot.pause()
            await asyncio.sleep(0.1)
            if isinstance(app.screen, KubeScreen):
                break
        assert isinstance(app.screen, KubeScreen)
        tabs = app.screen.query_one("#kube-tabs", TabbedContent)
        await pilot.press("right")
        await pilot.pause()
        assert tabs.active == "tab-services"
        await pilot.press("left")
        await pilot.pause()
        assert tabs.active == "tab-pods"
        await pilot.press("N")
        for _ in range(20):
            await pilot.pause()
            await asyncio.sleep(0.1)
            if isinstance(app.screen, NamespaceScreen):
                options = app.screen.query_one("#namespace-list", OptionList)
                if options.option_count == 3:
                    break
        assert isinstance(app.screen, NamespaceScreen)
        assert [str(options.get_option_at_index(i).id) for i in range(3)] == ["*", "dev", "prod"]
        assert options.highlighted == 1  # the current namespace ("dev") is preselected
        await pilot.press("enter")
        for _ in range(10):
            await pilot.pause()
            await asyncio.sleep(0.05)
        assert isinstance(app.screen, KubeScreen)
        assert engine.kube.namespace == "dev" and engine.kube.all_namespaces is False


def test_refresh_soon_wakes_the_background_thread(tmp_path: Path) -> None:
    provider = KubeProvider(enabled="off", state_root=tmp_path)

    assert provider._wake.is_set() is False
    provider.refresh_soon()
    assert provider._wake.is_set() is True


def test_stop_sets_both_flags_so_the_loop_ends(tmp_path: Path) -> None:
    provider = KubeProvider(enabled="off", state_root=tmp_path)

    provider.close()

    assert provider._stop.is_set() and provider._wake.is_set()


def test_the_background_refresh_interval_has_a_floor(tmp_path: Path) -> None:
    assert KubeProvider(interval=0.1, state_root=tmp_path).interval == 5.0
    assert KubeProvider(interval=45.0, state_root=tmp_path).interval == 45.0
