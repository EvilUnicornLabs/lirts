"""`lirts doctor`: environment and configuration checks with hints."""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import version
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import psutil
import yaml

from lirts import __version__
from lirts.config import (
    CONFIG_VERSION,
    load_config,
    resolve_config_path,
    state_dir,
    strip_obsolete,
)
from lirts.constants import (
    DOCKER_CLIENT_TIMEOUT,
    KUBECTL_READY_TIMEOUT,
    SIDE_PANEL_MIN_TERMINAL_WIDTH,
)
from lirts.daemon_client import ping, socket_path
from lirts.insights_explain import format_duration


class CheckStatus(StrEnum):
    """Outcome of one environment check."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


# Kept as ``str`` so callers can index plain dicts with a Check's status.
OK: str = CheckStatus.OK
WARN: str = CheckStatus.WARN
FAIL: str = CheckStatus.FAIL

# The lowest Python lirts runs on.
MIN_PYTHON = (3, 11)
# Seconds before a check's command is given up on.
_COMMAND_TIMEOUT = 5.0
# Below this width the table itself gets cramped, whatever the side panel does.
_NARROW_TERMINAL_COLUMNS = 120
# Bytes per kilobyte, for the history file size.
_KB = 1024


@dataclass
class Check:
    """One environment check: what was looked at, how it went and what to do."""

    name: str
    status: str
    detail: str
    hint: str | None = None


def _run(cmd: list[str], timeout: float = _COMMAND_TIMEOUT) -> tuple[int, str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
        return result.returncode, (result.stdout or result.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def check_python() -> Check:
    """lirts' own version, the Python it runs on and whether it is an editable install."""
    version = platform.python_version()
    editable = (
        "src"
        if Path(__file__).resolve().parent.parent.joinpath("pyproject.toml").exists()
        else "site-packages"
    )
    status = OK if sys.version_info >= MIN_PYTHON else FAIL
    return Check(
        "lirts / Python",
        status,
        f"lirts {__version__} on Python {version} ({editable}, {platform.system()} {platform.release()})",
        None if status == OK else f"lirts needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer",
    )


def check_config(path: Path | None = None) -> list[Check]:
    """Whether the config file parses, matches the current schema and has no stale keys."""
    resolved = resolve_config_path(path)
    out: list[Check] = []
    if not resolved.exists():
        out.append(
            Check(
                "config file",
                WARN,
                f"{resolved} does not exist (defaults in use)",
                "run `lirts config init` or press , in the dashboard and save",
            )
        )
        return out
    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        out.append(
            Check(
                "config file",
                FAIL,
                f"{resolved} cannot be parsed: {exc}",
                "fix the YAML or recreate it with `lirts config init --force`",
            )
        )
        return out
    if not isinstance(raw, dict):
        out.append(
            Check(
                "config file",
                FAIL,
                f"{resolved} is not a mapping",
                "recreate it with `lirts config init --force`",
            )
        )
        return out
    version = int(raw.get("version") or 0)
    if version < CONFIG_VERSION:
        out.append(
            Check(
                "config file",
                WARN,
                f"{resolved} has schema {version} < {CONFIG_VERSION}",
                "run `lirts config upgrade` (or Settings , then s); nothing is migrated by itself",
            )
        )
    else:
        out.append(Check("config file", OK, f"{resolved} (schema {version})"))
    _, unknown = strip_obsolete(raw)
    if unknown:
        out.append(
            Check(
                "config keys",
                WARN,
                "keys not in the current schema (ignored): " + ", ".join(unknown),
                "removed options or typos; `lirts config upgrade` drops them",
            )
        )
    return out


def check_state() -> Check:
    """Whether the state directory exists and can be written to."""
    folder = state_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".doctor"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return Check(
            "state directory",
            FAIL,
            f"{folder}: {exc}",
            "history, patterns and logs cannot be saved",
        )
    history = folder / "history.json"
    size = history.stat().st_size if history.exists() else 0
    return Check("state directory", OK, f"{folder} (history {size // _KB} KB)")


def check_psutil() -> Check:
    """Whether socket listings work system-wide or fall back to the per-process scan."""
    try:
        psutil.net_connections(kind="inet")
        return Check("socket access", OK, "psutil.net_connections works system-wide")
    except psutil.AccessDenied:
        return Check(
            "socket access",
            OK,
            "system-wide listing denied; using the per-process scan (normal on macOS)",
            "run with sudo to see command lines of other users' processes",
        )
    except (psutil.Error, OSError) as exc:  # psutil fails differently per platform
        return Check("socket access", FAIL, str(exc))


def check_docker() -> Check:
    """Whether the Docker daemon is reachable through the SDK or the CLI."""
    cli = shutil.which("docker")
    try:
        # Optional dependency: a slim install without the SDK must still run doctor.
        import docker as sdk

        client = sdk.from_env(timeout=DOCKER_CLIENT_TIMEOUT)
        client.ping()
        count = len(client.api.containers())
        client.close()
        return Check(
            "docker",
            OK,
            f"daemon reachable, {count} running container(s)"
            + ("" if cli else "; docker CLI not on PATH (exec unavailable)"),
        )
    except Exception as exc:  # the SDK wraps socket, TLS and daemon errors in its own types
        if cli:
            code, out = _run([cli, "ps", "-q"])
            if code == 0:
                return Check(
                    "docker",
                    OK,
                    f"CLI works ({len(out.split())} running); SDK failed: {str(exc)[:60]}",
                )
            return Check(
                "docker",
                WARN,
                f"docker CLI present but daemon not reachable: {out[:80]}",
                "start Docker Desktop / dockerd, or set docker.enabled: false",
            )
        return Check(
            "docker",
            WARN,
            "docker not found",
            "install Docker or set docker.enabled: false to silence this",
        )


def check_kubectl() -> Check:
    """Whether kubectl exists, has a current context and can reach the cluster."""
    cli = shutil.which("kubectl")
    if not cli:
        return Check(
            "kubectl",
            WARN,
            "kubectl not found",
            "install kubectl for the Kubernetes screen, or ignore",
        )
    code, ctx = _run([cli, "config", "current-context"])
    if code != 0 or not ctx:
        return Check(
            "kubectl",
            WARN,
            "no current context",
            "kubectl config use-context <name>; lirts never changes it for you",
        )
    code, out = _run([cli, "get", "--raw", "/readyz"], timeout=KUBECTL_READY_TIMEOUT)
    if code == 0:
        return Check("kubectl", OK, f"context {ctx} reachable")
    return Check(
        "kubectl",
        WARN,
        f"context {ctx} not reachable: {out.splitlines()[0][:80] if out else 'timeout'}",
        "VPN / credentials? lirts keeps working without the cluster",
    )


def check_bandwidth() -> Check:
    """Whether per-process throughput can be measured on this platform."""
    if platform.system() == "Darwin":
        if shutil.which("nettop"):
            return Check("bandwidth", OK, "nettop available (per-process throughput)")
        return Check("bandwidth", WARN, "nettop missing", "per-process throughput unavailable")
    return Check(
        "bandwidth",
        WARN,
        "per-process throughput is macOS-only (nettop)",
        "activity falls back to connections and CPU",
    )


def check_notify() -> Check:
    """Which desktop notification backend ``lirts watch --notify`` can use."""
    if platform.system() == "Darwin":
        backend = (
            "terminal-notifier"
            if shutil.which("terminal-notifier")
            else ("osascript" if shutil.which("osascript") else None)
        )
    else:
        backend = "notify-send" if shutil.which("notify-send") else None
    if backend:
        return Check("notifications", OK, f"{backend} available for `lirts watch --notify`")
    return Check(
        "notifications",
        WARN,
        "no notification backend",
        "install terminal-notifier (macOS) or libnotify (Linux)",
    )


def check_terminal() -> Check:
    """The terminal type and whether the window is wide enough for the full table."""
    term = os.environ.get("TERM", "")
    color = os.environ.get("COLORTERM", "")
    size = shutil.get_terminal_size((0, 0))
    if not size.columns:
        return Check("terminal", OK, "not attached to a terminal (fine for scripts)")
    detail = f"TERM={term or '?'} COLORTERM={color or '-'} size={size.columns}x{size.lines}"
    if size.columns < _NARROW_TERMINAL_COLUMNS:
        return Check(
            "terminal",
            WARN,
            detail,
            f"below {SIDE_PANEL_MIN_TERMINAL_WIDTH} columns the side panel auto-hides; "
            "widen the window for the full table",
        )
    return Check("terminal", OK, detail)


def run_all(config: Path | None = None) -> list[Check]:
    """Every environment check, in the order `lirts doctor` prints them."""
    checks = [
        check_python(),
        *check_config(config),
        check_state(),
        check_psutil(),
        check_docker(),
        check_kubectl(),
        check_bandwidth(),
        check_notify(),
        check_terminal(),
    ]
    editor_check = check_editor(config)
    if editor_check is not None:
        checks.append(editor_check)
    checks.append(check_daemon())
    checks.append(check_mcp())
    return checks


def check_daemon() -> Check:
    """Whether a `lirts daemon` is running; dashboards, the CLI and `lirts mcp` attach to it."""
    info = ping(socket_path())
    if info is None:
        return Check(
            "daemon",
            OK,
            "not running; every lirts command runs its own engine "
            "(`lirts daemon install` starts one at login)",
        )
    up = format_duration(time.time() - float(info.get("started", time.time())))
    return Check(
        "daemon",
        OK,
        f"running, PID {info.get('pid')}, up {up}, {info.get('refreshes')} refreshes; "
        "dashboards, the CLI and lirts mcp attach to it",
    )


def check_mcp() -> Check:
    """Whether `lirts mcp` can run: the optional mcp SDK must be installed."""
    if find_spec("mcp") is None:
        return Check(
            "mcp",
            WARN,
            "mcp SDK not installed; `lirts mcp` (the server for coding agents) is unavailable",
            "pip install 'lirts[mcp]'  (or: pipx inject lirts mcp)",
        )
    return Check("mcp", OK, f"mcp SDK {version('mcp')}; `lirts mcp` serves coding agents")


def check_editor(config: Path | None = None) -> Check | None:
    """The `editor` setting, when set, must name a command on PATH."""
    editor = str(load_config(config).get("editor") or "").strip()
    if not editor:
        return None
    try:
        cmd = shlex.split(editor)
    except ValueError:
        return Check(
            "editor", FAIL, f"'{editor}' is not a valid command line", "fix the editor setting"
        )
    if cmd and shutil.which(cmd[0]):
        return Check("editor", OK, f"{cmd[0]} ({shutil.which(cmd[0])})")
    return Check(
        "editor",
        WARN,
        f"'{cmd[0] if cmd else editor}' not on PATH",
        "O falls back to an error until it is",
    )


def as_dict(check: Check) -> dict[str, Any]:
    """One check as plain JSON values, for ``lirts doctor --json``."""
    return {"name": check.name, "status": check.status, "detail": check.detail, "hint": check.hint}
