"""Row actions: kill, restart, stop, logs, shell, open and copy."""

from __future__ import annotations

import contextlib
import subprocess
import webbrowser

from rich.text import Text
from textual import work

from lirts.constants import LOG_TAIL_STACK, TOAST_LONG, TOAST_NORMAL
from lirts.fixes import Fix, FixKind, fix_for
from lirts.insights import blast_radius
from lirts.models import Level, Listener
from lirts.tui.app_base import LirtsAppBase
from lirts.tui.screens import ConfirmScreen, KillScreen, LogScreen, RestartScreen
from lirts.tui.screens.fix import FixScreen


class ActionsMixin(LirtsAppBase):
    """Everything a key press does to the process, container or stack under the cursor."""

    def action_kill(self) -> None:
        """Confirm and kill the marked or current process(es), with a blast-radius preview (k)."""
        targets = [t for t in self.targets() if t.processes]
        if not targets:
            lst = self.selected()
            if lst and lst.container and not lst.processes:
                self.notify("No host process; use x to stop the container", severity="warning")
            return
        first, *others = targets
        impacts: list[str] = []
        for t in targets:
            prefix = f"{t.key}: " if others else ""
            impacts.extend(prefix + line for line in blast_radius(t, self.snapshot))
        pids = sorted({pid for t in targets for pid in t.pids})

        def done(choice: str | None) -> None:
            if not choice:
                return
            force = choice == "kill"
            results = self.engine.kill_and_wait(pids, force=force)
            failed = [f"{pid}: {msg}" for pid, ok, msg in results if not ok]
            if failed:
                self.notify(
                    "Kill failed — " + "; ".join(failed), severity="error", timeout=TOAST_LONG
                )
            else:
                where = f"{len(targets)} services" if others else first.key
                self.notify(
                    f"Sent {self.engine.signal_name(force)} to {len(results)} process(es) on {where}"
                )
                for t in targets:
                    self.engine.history.note(
                        f"[-] {t.identity.service} on {t.key}: {self.engine.signal_name(force)} "
                        f"sent by you (k) to PID {t.pids}",
                        port=t.port,
                    )
            self.clear_marks()
            self.refresh_data()

        self.push_screen(KillScreen(first, impacts, others), done)

    def _require_container(self) -> Listener | None:
        lst = self.selected()
        if lst is None:
            return None
        if not lst.container:
            self.notify("Not a Docker container", severity="warning", timeout=TOAST_NORMAL)
            return None
        return lst

    def action_docker_logs(self) -> None:
        """Show the logs of the container, or of the whole stack on a group header (l)."""
        group = self.selected_group()
        if group and any(m.container for m in group[1]):
            name = group[0]
            engine = self.engine
            self.push_screen(
                LogScreen(
                    engine,
                    name,
                    f"stack {name}",
                    tail=LOG_TAIL_STACK,
                    fetch=lambda tail: engine.stack_logs(name, tail),
                )
            )
            return
        lst = self._require_container()
        if lst and lst.container:
            self.push_screen(LogScreen(self.engine, lst.container.id, lst.container.name))

    def _target_containers(self) -> list[tuple[str, str]]:
        """Unique (id, name) of containers behind the marked rows, the current row, or a group header."""
        seen: dict[str, str] = {}
        group = self.selected_group()
        targets = group[1] if group and not self._marked else self.targets()
        for t in targets:
            if t.container and t.container.id not in seen:
                seen[t.container.id] = t.container.name
        return list(seen.items())

    def action_docker_stop(self) -> None:
        """Confirm and stop the marked or current container(s) (x)."""
        containers = self._target_containers()
        if not containers:
            self._require_container()
            return
        names = ", ".join(name for _, name in containers)

        def done(ok: bool | None) -> None:
            if ok:
                self.run_container_action("stop", containers)

        self.push_screen(
            ConfirmScreen(
                (
                    "Stop container?"
                    if len(containers) == 1
                    else f"Stop {len(containers)} containers?"
                ),
                Text(f"Stop {names}?"),
            ),
            done,
        )

    def action_restart(self) -> None:
        """Restart containers through Docker, or re-run one local process' command (t)."""
        containers = self._target_containers()
        if containers:
            self.run_container_action("restart", containers)
            return
        lst = self.selected()
        if lst is None:
            return
        if len(self.targets()) > 1:
            self.notify(
                "Local restart works on one row at a time",
                severity="warning",
                timeout=TOAST_NORMAL,
            )
            return
        plan = self.engine.restart_plan(lst)
        if plan is None:
            self.notify(
                "No command line recorded for this process",
                severity="warning",
                timeout=TOAST_NORMAL,
            )
            return
        cmdline, cwd = plan

        def done(choice: str | None) -> None:
            if not choice:
                return
            self.run_local_restart(lst, choice == "kill")

        self.push_screen(RestartScreen(lst, cmdline, cwd, blast_radius(lst, self.snapshot)), done)

    @work(thread=True, group="docker-action")
    def run_local_restart(self, lst: Listener, force: bool) -> None:
        """Worker: stop the process and start its command again, then report and refresh."""
        ok, msg = self.engine.restart_process(lst, force=force)
        self.call_from_thread(
            self.notify,
            f"Restarted {lst.identity.service}: {msg}" if ok else f"Restart failed: {msg}",
            severity="information" if ok else "error",
            timeout=TOAST_LONG,
        )
        self.engine.history.note(
            f"[~] {lst.identity.service} on {lst.key} restarted by you (t): {msg}"
            + ("; it may come back on another port" if ok else ""),
            port=lst.port,
            level=Level.INFO if ok else Level.ERROR,
        )
        self.call_from_thread(self.refresh_data)

    @work(thread=True, group="docker-action")
    def run_container_action(self, action: str, containers: list[tuple[str, str]]) -> None:
        """Worker: run `stop` or `restart` on every container, then report and refresh."""
        fn = self.engine.docker.stop if action == "stop" else self.engine.docker.restart
        failed: list[str] = []
        for container_id, name in containers:
            ok, msg = fn(container_id)
            if not ok:
                failed.append(f"{name}: {msg}")
        if failed:
            self.call_from_thread(
                self.notify,
                f"{action} failed: " + "; ".join(failed),
                severity="error",
                timeout=TOAST_LONG,
            )
        else:
            names = ", ".join(name for _, name in containers)
            self.call_from_thread(self.notify, f"{action.capitalize()}ed {names}")
        self.call_from_thread(self.clear_marks)
        self.call_from_thread(self.refresh_data)

    def action_open_project(self) -> None:
        """Open the project folder of the current row in the configured editor (O)."""
        lst = self.selected()
        if lst is None:
            return
        folder = self.engine.project_dir(lst)
        if not folder:
            self.notify(
                "No project folder known for this row", severity="warning", timeout=TOAST_NORMAL
            )
            return
        ok, msg = self.engine.open_path(folder)
        self.notify(
            f"Opening {msg}" if ok else f"Could not open folder: {msg}",
            severity="information" if ok else "error",
        )

    def action_docker_exec(self) -> None:
        """Suspend lirts and open an interactive shell inside the container (e)."""
        lst = self._require_container()
        if not lst or not lst.container:
            return
        cmd = self.engine.docker.exec_command(lst.container.id)
        rc = 0
        try:
            with self.suspend():
                rc = subprocess.call(cmd)
                if rc != 0:
                    # Leave the error on screen instead of flashing back to the dashboard.
                    print(
                        f"\ndocker exec exited with code {rc} "
                        f"(container {lst.container.name}). Press Enter to return to lirts."
                    )
                    with contextlib.suppress(EOFError):
                        input()
        except Exception as exc:  # SuspendNotSupported, or the docker CLI itself failed
            self._copy(" ".join(cmd), f"Cannot open a shell here ({exc}); command copied")
        if rc != 0:
            self.notify(
                f"Shell in {lst.container.name} exited with code {rc}",
                severity="warning",
                timeout=TOAST_LONG,
            )
        self.refresh_data()

    def action_fix(self) -> None:
        """Run the fix the row's worst insight proposes, after a confirmation that shows it (F)."""
        lst = self.selected()
        if lst is None:
            return
        if getattr(self.engine, "replay", False):
            self.notify(
                "replay mode: actions are disabled", severity="warning", timeout=TOAST_NORMAL
            )
            return
        fix = fix_for(lst)
        if fix is None:
            self.notify("No fix proposed for this row", timeout=TOAST_NORMAL)
            return

        def done(confirmed: bool | None) -> None:
            if confirmed:
                self.run_fix(fix)

        self.push_screen(FixScreen(fix), done)

    def run_fix(self, fix: Fix) -> None:
        """Carry out a confirmed fix through the same paths the manual keys use."""
        lst = fix.listener
        note = f"{fix.insight.code}: {fix.command}"
        if fix.kind == FixKind.KILL and fix.pid is not None:
            results = self.engine.kill_and_wait([fix.pid], force=False)
            failed = [f"{pid}: {msg}" for pid, ok, msg in results if not ok]
            if failed:
                self.notify(
                    "Fix failed — " + "; ".join(failed), severity="error", timeout=TOAST_LONG
                )
                return
            self.notify(f"Sent SIGTERM to PID {fix.pid}")
        elif fix.kind == FixKind.RESTART_CONTAINER and lst.container is not None:
            self.run_container_action("restart", [(lst.container.id, lst.container.name)])
        elif fix.kind == FixKind.STOP_CONTAINER and lst.container is not None:
            self.run_container_action("stop", [(lst.container.id, lst.container.name)])
        elif fix.kind == FixKind.RESTART_PROCESS:
            self.run_local_restart(lst, False)
        elif fix.kind == FixKind.LOGS and lst.container is not None:
            self.push_screen(LogScreen(self.engine, lst.container.id, lst.container.name))
            return
        elif fix.kind == FixKind.STOP_FORWARD:
            for _port, ok, msg in self.engine.kube.stop_forward(local_port=lst.port):
                self.notify(msg, severity="information" if ok else "error", timeout=TOAST_NORMAL)
        self.engine.history.note(
            f"[~] {lst.identity.service} on {lst.key}: fix run by you (F), {note}", port=lst.port
        )
        self.refresh_data()

    def action_open_browser(self) -> None:
        """Open the service's URL in the default browser (o)."""
        lst = self.selected()
        if lst is None:
            return
        if lst.protocol != "TCP":
            self.notify("Not a TCP port", severity="warning", timeout=TOAST_NORMAL)
            return
        url = self.engine.open_url(lst)
        try:
            webbrowser.open(url)
            self.notify(f"Opening {url}", timeout=TOAST_NORMAL)
        except Exception as exc:  # no browser, or the platform refuses to open one
            self.notify(f"Could not open browser: {exc}", severity="error")

    def action_copy(self) -> None:
        """Copy the URL, or the docker exec command for a container, to the clipboard (c)."""
        lst = self.selected()
        if lst is None:
            return
        if lst.container:
            self._copy(
                " ".join(self.engine.docker.exec_command(lst.container.id)),
                "docker exec command copied",
            )
        else:
            self._copy(self.engine.open_url(lst), "URL copied")

    def _copy(self, text: str, message: str) -> None:
        try:
            # Optional dependency: without it the terminal's own clipboard is used instead.
            import pyperclip

            pyperclip.copy(text)
            self.notify(message, timeout=TOAST_NORMAL)
        except Exception:  # pyperclip is missing, or found no clipboard tool on this machine
            self.copy_to_clipboard(text)
            self.notify(f"{message} (terminal clipboard): {text}", timeout=TOAST_LONG)
