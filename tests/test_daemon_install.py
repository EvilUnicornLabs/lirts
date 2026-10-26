"""Starting the daemon at login: the service files, the fake service manager, the install
command and the environment the daemon runs with."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lirts import cli, daemon_install
from lirts.cli import daemon as daemon_cmd

runner = CliRunner()


@pytest.mark.parametrize("system", ["Darwin", "Linux"], ids=["launchd", "systemd"])
def test_install_writes_the_service_file_and_loads_it(tmp_path: Path, system: str) -> None:
    ran: list[list[str]] = []
    mgr = daemon_install.manager(system, home=tmp_path)
    assert mgr is not None

    ok, message = daemon_install.install(
        mgr, exe="/opt/bin/lirts", run=lambda c: ran.append(c) or (0, "")
    )
    text = mgr.file.read_text()
    gone, _ = daemon_install.uninstall(mgr, run=lambda c: ran.append(c) or (0, ""))

    assert ok and message.startswith(f"installed {mgr.file}")
    assert "/opt/bin/lirts" in text and "daemon" in text
    if system == "Darwin":
        assert mgr.file.name == "fi.evilunicorn.lirts.plist" and "<key>RunAtLoad</key>" in text
        assert ran[0] == ["launchctl", "load", "-w", str(mgr.file)]
        assert ran[1] == ["launchctl", "unload", "-w", str(mgr.file)]
    else:
        assert mgr.file.name == "lirts.service" and "WantedBy=default.target" in text
        assert ran[:2] == [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", "lirts"],
        ]
    assert gone and not mgr.file.exists()


def test_install_reports_a_refusing_service_manager(tmp_path: Path) -> None:
    mgr = daemon_install.manager("Darwin", home=tmp_path)
    assert mgr is not None

    ok, message = daemon_install.install(mgr, exe="/opt/bin/lirts", run=lambda c: (1, "nope"))

    assert ok is False and message.endswith("refused it: nope")


def test_start_and_stop_need_an_installed_service(tmp_path: Path) -> None:
    mgr = daemon_install.manager("Linux", home=tmp_path)
    assert mgr is not None and daemon_install.manager("Windows", home=tmp_path) is None

    assert daemon_install.start(mgr) == (False, "not installed; run `lirts daemon install` first")
    assert daemon_install.stop(mgr) == (False, "not installed")


def test_install_command_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mgr = daemon_install.manager("Darwin", home=tmp_path)
    ran: list[list[str]] = []
    monkeypatch.setattr(daemon_cmd.daemon_install, "manager", lambda: mgr)
    monkeypatch.setattr(daemon_cmd.daemon_install, "executable", lambda: "/opt/bin/lirts")
    # The real service manager is never reached: the stub records what would have run.
    monkeypatch.setattr(daemon_cmd.daemon_install, "_run", lambda c: ran.append(c) or (0, ""))

    result = runner.invoke(cli.app, ["daemon", "install"])

    assert result.exit_code == 0, result.output
    assert "the daemon starts now" in result.output  # Rich wraps the long line
    assert mgr is not None and mgr.file.exists()
    assert ran == [["launchctl", "load", "-w", str(mgr.file)]]


def test_executable_prefers_the_program_that_runs_the_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    program = tmp_path / "lirts"
    program.write_text("#!/bin/sh\n")
    monkeypatch.setattr("sys.argv", [str(program), "daemon", "install"])
    monkeypatch.setattr("shutil.which", lambda name: "/elsewhere/lirts")

    assert daemon_install.executable() == str(program.resolve())

    monkeypatch.setattr("sys.argv", ["python", "-m", "lirts"])
    assert daemon_install.executable() == "/elsewhere/lirts"


def test_strip_debug_env_drops_malloc_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MallocStackLogging", "0")
    monkeypatch.setenv("MallocNanoZone", "0")
    monkeypatch.setenv("PATH", "/usr/bin")

    daemon_cmd.strip_debug_env()

    import os

    assert "MallocStackLogging" not in os.environ and "MallocNanoZone" not in os.environ
    assert os.environ["PATH"] == "/usr/bin"


def test_install_command_waits_for_the_daemon_and_prints_its_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mgr = daemon_install.manager("Darwin", home=tmp_path)
    info = {
        "pid": 9,
        "version": "0.7.1",
        "started": 0.0,
        "refreshes": 1,
        "idle_interval": 10.0,
        "mcp_url": "http://127.0.0.1:7421/mcp",
        "allow_actions": False,
    }
    monkeypatch.setattr(daemon_cmd.daemon_install, "manager", lambda: mgr)
    monkeypatch.setattr(daemon_cmd.daemon_install, "executable", lambda: "/opt/homebrew/bin/lirts")
    monkeypatch.setattr(daemon_cmd.daemon_install, "from_homebrew", lambda exe=None: False)
    monkeypatch.setattr(daemon_cmd.daemon_install, "_run", lambda c: (0, ""))
    monkeypatch.setattr(daemon_cmd, "wait_for_daemon", lambda: info)

    result = runner.invoke(cli.app, ["daemon", "install"])

    assert result.exit_code == 0, result.output
    assert "registered /opt/homebrew/bin/lirts" in result.output
    assert "running  PID 9" in result.output
    assert "MCP: http://127.0.0.1:7421/mcp" in result.output


def test_a_cellar_binary_is_registered_through_the_prefix_link(tmp_path: Path) -> None:
    cellar = tmp_path / "Cellar" / "lirts" / "0.7.1" / "libexec" / "bin" / "lirts"
    cellar.parent.mkdir(parents=True)
    cellar.write_text("#!/bin/sh\n")
    link = tmp_path / "bin" / "lirts"
    link.parent.mkdir()
    link.symlink_to(cellar)

    assert daemon_install.stable_path(cellar) == link
    assert daemon_install.stable_path(tmp_path / "venv" / "bin" / "lirts") == (
        tmp_path / "venv" / "bin" / "lirts"
    )


def test_a_cellar_binary_without_the_link_is_registered_as_is(tmp_path: Path) -> None:
    cellar = tmp_path / "Cellar" / "lirts" / "0.7.1" / "bin" / "lirts"

    assert daemon_install.stable_path(cellar) == cellar


def test_executable_uses_the_stable_link_for_a_cellar_program(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cellar = tmp_path / "Cellar" / "lirts" / "0.7.1" / "libexec" / "bin" / "lirts"
    cellar.parent.mkdir(parents=True)
    cellar.write_text("#!/bin/sh\n")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "lirts").symlink_to(cellar)
    monkeypatch.setattr("sys.argv", [str(cellar), "daemon", "install"])

    assert daemon_install.executable() == str(tmp_path / "bin" / "lirts")


def test_from_homebrew_recognises_a_cellar_binary(tmp_path: Path) -> None:
    cellar = tmp_path / "Cellar" / "lirts" / "0.7.3" / "libexec" / "bin" / "lirts"
    cellar.parent.mkdir(parents=True)
    cellar.write_text("#!/bin/sh\n")

    assert daemon_install.from_homebrew(str(cellar)) is True
    assert daemon_install.from_homebrew(str(tmp_path / "venv" / "bin" / "lirts")) is False
    assert daemon_install.brew_service_file(tmp_path, "Darwin").name == "homebrew.mxcl.lirts.plist"
    assert daemon_install.brew_service_file(tmp_path, "Linux").name == "homebrew.lirts.service"


def test_install_and_start_defer_to_brew_services_for_a_homebrew_lirts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mgr = daemon_install.manager("Darwin", home=tmp_path)
    monkeypatch.setattr(daemon_cmd.daemon_install, "manager", lambda: mgr)
    monkeypatch.setattr(daemon_cmd.daemon_install, "executable", lambda: "/opt/homebrew/bin/lirts")
    monkeypatch.setattr(daemon_cmd.daemon_install, "from_homebrew", lambda exe=None: True)

    install = runner.invoke(cli.app, ["daemon", "install"])
    start = runner.invoke(cli.app, ["daemon", "start"])
    uninstall = runner.invoke(cli.app, ["daemon", "uninstall"])

    assert install.exit_code == 0 and "brew services start lirts" in install.output
    assert start.exit_code == 0 and "brew services start lirts" in start.output
    assert uninstall.exit_code == 0 and "brew services stop lirts" in uninstall.output
    assert mgr is not None and not mgr.file.exists()


def test_status_names_the_brew_service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mgr = daemon_install.manager("Darwin", home=tmp_path)
    brew_file = tmp_path / "homebrew.mxcl.lirts.plist"
    monkeypatch.setattr(daemon_cmd.daemon_install, "manager", lambda: mgr)
    monkeypatch.setattr(daemon_cmd.daemon_install, "from_homebrew", lambda exe=None: True)
    monkeypatch.setattr(daemon_cmd.daemon_install, "brew_service_file", lambda: brew_file)
    monkeypatch.setattr(daemon_cmd, "ping", lambda path: None)

    before = runner.invoke(cli.app, ["daemon", "status"])
    brew_file.write_text("plist")
    after = runner.invoke(cli.app, ["daemon", "status"])

    assert "not started: brew services start lirts" in before.output
    assert "brew services (started at login)" in after.output


def test_run_daemon_keeps_lirts_log_records_out_of_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    import logging

    from lirts.daemon import DaemonServer

    async def serve(self: DaemonServer) -> None:
        self.engine.close()

    monkeypatch.setattr(DaemonServer, "serve", serve)
    logging.getLogger("lirts").propagate = True

    result = runner.invoke(cli.app, ["daemon", "--demo", "--mcp-port", "0"])

    assert result.exit_code == 0, result.output
    assert logging.getLogger("lirts").propagate is False
