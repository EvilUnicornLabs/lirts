"""Starting the daemon at login: a launchd agent on macOS, a systemd user unit on Linux.

Installing writes one file under the user's home and asks the service manager to load it;
uninstalling reverses both.  Nothing else on the machine is touched, and lirts never does
any of this by itself: every step is a ``lirts daemon …`` command the user runs.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lirts.config import state_dir

LABEL = "fi.evilunicorn.lirts"
# Seconds the service manager may take to answer before the command is given up on.
_MANAGER_TIMEOUT = 10.0

Runner = Callable[[list[str]], tuple[int, str]]


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=_MANAGER_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return done.returncode, (done.stdout or done.stderr).strip()


@dataclass(frozen=True)
class Manager:
    """The service manager of this platform and the file it reads."""

    kind: str  # "launchd" or "systemd"
    file: Path

    @property
    def label(self) -> str:
        return LABEL if self.kind == "launchd" else "lirts"


def manager(system: str | None = None, home: Path | None = None) -> Manager | None:
    """launchd on macOS, systemd on Linux, None elsewhere."""
    system = system or platform.system()
    home = home or Path.home()
    if system == "Darwin":
        return Manager("launchd", home / "Library" / "LaunchAgents" / f"{LABEL}.plist")
    if system == "Linux":
        return Manager("systemd", home / ".config" / "systemd" / "user" / "lirts.service")
    return None


def executable() -> str | None:
    """The ``lirts`` command the service should run: the one on PATH, else this program."""
    found = shutil.which("lirts")
    if found:
        return found
    if sys.argv and sys.argv[0].endswith("lirts"):
        return str(Path(sys.argv[0]).resolve())
    return None


def render(kind: str, exe: str, log: Path) -> str:
    """The launchd plist or the systemd unit that runs ``exe daemon`` at login."""
    if kind == "launchd":
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
        <string>daemon</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ProcessType</key><string>Background</string>
    <key>StandardOutPath</key><string>{log}</string>
    <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""
    return f"""[Unit]
Description=lirts daemon: observes ports, processes and containers for the dashboard and coding agents

[Service]
ExecStart={exe} daemon
Restart=on-failure
StandardOutput=append:{log}
StandardError=append:{log}

[Install]
WantedBy=default.target
"""


def install(mgr: Manager, *, exe: str, run: Runner = _run) -> tuple[bool, str]:
    """Write the service file and load it, so the daemon runs now and at every login."""
    log = state_dir() / "daemon.log"
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        mgr.file.parent.mkdir(parents=True, exist_ok=True)
        mgr.file.write_text(render(mgr.kind, exe, log), encoding="utf-8")
    except OSError as exc:
        return False, f"cannot write {mgr.file}: {exc}"
    if mgr.kind == "launchd":
        code, out = run(["launchctl", "load", "-w", str(mgr.file)])
    else:
        code, out = run(["systemctl", "--user", "daemon-reload"])
        if code == 0:
            code, out = run(["systemctl", "--user", "enable", "--now", "lirts"])
    if code != 0:
        return False, f"wrote {mgr.file} but the service manager refused it: {out}"
    return True, f"installed {mgr.file}; the daemon starts now and at every login"


def uninstall(mgr: Manager, *, run: Runner = _run) -> tuple[bool, str]:
    """Unload the service and remove its file."""
    if not mgr.file.exists():
        return False, f"not installed ({mgr.file} does not exist)"
    if mgr.kind == "launchd":
        run(["launchctl", "unload", "-w", str(mgr.file)])
    else:
        run(["systemctl", "--user", "disable", "--now", "lirts"])
    try:
        mgr.file.unlink()
    except OSError as exc:
        return False, f"cannot remove {mgr.file}: {exc}"
    return True, f"removed {mgr.file}; the daemon no longer starts at login"


def start(mgr: Manager, *, run: Runner = _run) -> tuple[bool, str]:
    """Start the installed service now."""
    if not mgr.file.exists():
        return False, "not installed; run `lirts daemon install` first"
    if mgr.kind == "launchd":
        code, out = run(["launchctl", "start", LABEL])
    else:
        code, out = run(["systemctl", "--user", "start", "lirts"])
    return code == 0, out or "started"


def stop(mgr: Manager, *, run: Runner = _run) -> tuple[bool, str]:
    """Stop the installed service (it comes back at the next login)."""
    if not mgr.file.exists():
        return False, "not installed"
    if mgr.kind == "launchd":
        code, out = run(["launchctl", "stop", LABEL])
    else:
        code, out = run(["systemctl", "--user", "stop", "lirts"])
    return code == 0, out or "stopped"
