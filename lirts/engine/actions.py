"""What the user can do to what lirts sees: kill, restart, stacks, open.

Everything here is triggered by a key or a command; nothing runs on the refresh
timer.
"""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import signal
import subprocess

import psutil

from lirts.constants import (
    KILL_WAIT_SECONDS,
    RESTART_KILL_WAIT_SECONDS,
    STANDARD_WEB_PORTS,
    TLS_PORTS,
)
from lirts.engine.base import EngineBase
from lirts.models import ContainerInfo, Listener


def launch_detached(cmd: list[str]) -> None:
    """Start an editor or a file manager and forget about it (tests replace this one function)."""
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class ActionsMixin(EngineBase):
    """Everything the user can do to what lirts sees; nothing runs on the refresh timer."""

    def kill(self, pids: list[int], force: bool = False) -> list[tuple[int, bool, str]]:
        """Terminate (or SIGKILL) processes; returns per-pid results."""
        results: list[tuple[int, bool, str]] = []
        for pid in pids:
            try:
                proc = psutil.Process(pid)
                if force:
                    proc.kill()
                else:
                    proc.terminate()
                results.append((pid, True, "killed" if force else "terminated"))
            except psutil.NoSuchProcess:
                results.append((pid, True, "already gone"))
            except psutil.AccessDenied:
                results.append((pid, False, "permission denied (try sudo)"))
            except (psutil.Error, OSError) as exc:  # a dying process can fail in many ways
                results.append((pid, False, str(exc)))
        return results

    def kill_and_wait(
        self, pids: list[int], *, force: bool = False, timeout: float = KILL_WAIT_SECONDS
    ) -> list[tuple[int, bool, str]]:
        """Kill ``pids`` and wait up to ``timeout`` for them to disappear."""
        results = self.kill(pids, force=force)
        procs = []
        for pid, ok, _ in results:
            if ok:
                try:
                    procs.append(psutil.Process(pid))
                except psutil.NoSuchProcess:
                    continue
        if procs:
            _, alive = psutil.wait_procs(procs, timeout=timeout)
            for p in alive:
                results = [
                    (pid, ok, ("still running after " + f"{timeout:.0f}s") if pid == p.pid else msg)
                    for pid, ok, msg in results
                ]
        return results

    def signal_name(self, force: bool) -> str:
        """Name of the signal a kill would send, for the confirmation text."""
        return signal.SIGKILL.name if force else signal.SIGTERM.name

    def open_url(self, listener: Listener) -> str:
        """Smart URL for the listener (scheme, hostname and port mapping aware)."""
        scheme = listener.http.scheme or ("https" if listener.port in TLS_PORTS else "http")
        host = "localhost"
        if listener.hosts and listener.port in STANDARD_WEB_PORTS:
            host = listener.hosts[0]
        elif listener.addresses and all(
            a not in ("0.0.0.0", "::", "127.0.0.1", "::1", "") for a in listener.addresses
        ):
            a = listener.addresses[0]
            host = f"[{a}]" if ":" in a else a
        default_port = 443 if scheme == "https" else 80
        port_part = "" if listener.port == default_port else f":{listener.port}"
        return f"{scheme}://{host}{port_part}/"

    @staticmethod
    def can_sudo_hint() -> str:
        """Suffix reminding the user that some processes need elevated privileges."""
        return "" if os.geteuid() == 0 else " (some processes need sudo)"

    # ----- stacks ------------------------------------------------------------------

    def stacks(self) -> dict[str, list[ContainerInfo]]:
        """Running containers grouped by compose project (unlabelled ones under '-')."""
        out: dict[str, list[ContainerInfo]] = {}
        for c in self.containers:
            out.setdefault(c.stack or "-", []).append(c)
        return dict(sorted(out.items()))

    def stack_action(self, project: str, action: str) -> list[tuple[str, bool, str]]:
        """Stop or restart every container of a compose project."""
        return self.docker.stack_action(self.containers, project=project, action=action)

    def stack_logs(self, project: str, tail: int = 100) -> str:
        """Interleaved logs of every container of a compose project."""
        return self.docker.stack_logs(self.containers, project=project, tail=tail)

    def project_dir(self, listener: Listener) -> str | None:
        """Best guess of the project folder behind a listener."""
        if listener.container and listener.container.working_dir:
            return listener.container.working_dir
        for proc in listener.processes:
            if proc.cwd and proc.cwd not in ("/", os.path.expanduser("~")):
                return proc.cwd
        return None

    def open_path(self, path: str) -> tuple[bool, str]:
        """Open a folder in the configured editor, or in the desktop file manager."""
        editor = self.cfg.editor
        if editor:
            try:
                cmd = shlex.split(editor)
            except ValueError as exc:
                return False, f"editor setting is not a valid command: {exc}"
            if not cmd or not shutil.which(cmd[0]):
                return False, f"editor '{editor}' not found on PATH (Settings: Editor)"
            try:
                launch_detached([*cmd, path])
                return True, f"{path} in {cmd[0]}"
            except OSError as exc:
                return False, str(exc)
        opener = shutil.which("open") if platform.system() == "Darwin" else shutil.which("xdg-open")
        if not opener:
            return False, "no opener available (open / xdg-open)"
        try:
            launch_detached([opener, path])
            return True, path
        except OSError as exc:
            return False, str(exc)

    # ----- restart a local process -------------------------------------------------

    def restart_plan(self, listener: Listener) -> tuple[list[str], str | None] | None:
        """The command and working directory a local restart would use, or None."""
        proc = listener.primary
        if proc is None or listener.container or not proc.cmdline:
            return None
        return list(proc.cmdline), proc.cwd

    def restart_process(self, listener: Listener, force: bool = False) -> tuple[bool, str]:
        """Kill the listener's process(es) and re-run the primary command in its cwd.

        Output of the new process goes to ``<state>/restarts/<port>.log``.
        """
        plan = self.restart_plan(listener)
        if plan is None:
            return False, "no command line recorded for this listener"
        cmdline, cwd = plan
        proc = listener.primary
        assert proc is not None
        env: dict[str, str] | None = None
        try:
            env = psutil.Process(proc.pid).environ()
        except (psutil.Error, OSError):
            env = None
        results = self.kill_and_wait(listener.pids, force=force, timeout=RESTART_KILL_WAIT_SECONDS)
        still = [pid for pid, ok, msg in results if not ok or "still running" in msg]
        if still:
            return False, f"could not stop PID(s) {still}"
        log_dir = self.state_root / "restarts"
        log_path = log_dir / f"{listener.port}.log"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_path, "ab") as log_file:
                child = subprocess.Popen(
                    cmdline,
                    cwd=cwd if cwd and os.path.isdir(cwd) else None,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except OSError as exc:
            return False, f"could not start '{cmdline[0]}': {exc}"
        return True, f"started PID {child.pid} (output in {log_path})"
