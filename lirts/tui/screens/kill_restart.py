"""Kill and restart confirmation dialogs with a blast-radius preview."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from lirts.constants import GLYPH_WARNING
from lirts.models import Listener

# Names listed in the dialog before the rest are summarised as "… and N more".
MAX_TARGETS_LISTED = 10
MAX_PROCESSES_LISTED = 8


class KillScreen(ModalScreen[str | None]):
    """Kill confirmation with a blast-radius preview; opened with `k`.

    Dismisses with ``"term"``, ``"kill"`` or None.
    """

    BINDINGS = [
        Binding("escape,c,n", "cancel", "Cancel"),
        Binding("t,enter,y", "term", "Terminate"),
        Binding("k,K", "kill", "Kill -9"),
    ]

    def __init__(
        self, listener: Listener, impacts: list[str], others: list[Listener] | None = None
    ) -> None:
        super().__init__()
        self.listener = listener
        self.impacts = impacts
        self.others = others or []

    def compose(self) -> ComposeResult:
        lst = self.listener
        body = Table.grid(padding=(0, 1))
        body.add_column(width=12, style="bold cyan")
        body.add_column(overflow="fold")
        targets = [lst, *self.others]
        if self.others:
            names = Text()
            for t in targets[:MAX_TARGETS_LISTED]:
                names.append(f"{t.identity.service} on {t.key}\n")
            if len(targets) > MAX_TARGETS_LISTED:
                names.append(f"… and {len(targets) - MAX_TARGETS_LISTED} more")
            names.rstrip()
            body.add_row(f"{len(targets)} services", names)
        else:
            body.add_row("Service", f"{lst.identity.service} on {lst.key}")
        procs = Text()
        all_procs = [p for t in targets for p in t.processes]
        for p in all_procs[:MAX_PROCESSES_LISTED]:
            procs.append(f"{p.name} (PID {p.pid})\n")
        if len(all_procs) > MAX_PROCESSES_LISTED:
            procs.append(f"… and {len(all_procs) - MAX_PROCESSES_LISTED} more")
        procs.rstrip()
        body.add_row(
            "Processes", procs if all_procs else Text("none (docker-only port)", style="dim")
        )
        impact = Text()
        if self.impacts:
            for line in self.impacts:
                impact.append(f"{GLYPH_WARNING} ", style="yellow")
                impact.append(line + "\n")
        else:
            impact.append("no dependent services detected", style="green")
        impact.rstrip()
        body.add_row("Impact", impact)
        with Vertical(classes="dialog", id="kill-dialog"):
            yield Static("Kill process?", classes="dialog-title")
            yield Static(body, classes="dialog-body")
            with Horizontal(id="kill-buttons"):
                yield Button("Terminate (t)", id="term", variant="warning")
                yield Button("Kill -9 (k)", id="kill", variant="error")
                yield Button("Cancel (Esc)", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "term":
            self.dismiss("term")
        elif event.button.id == "kill":
            self.dismiss("kill")
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        """Leave everything running (Esc / c / n)."""
        self.dismiss(None)

    def action_term(self) -> None:
        """Send SIGTERM (t / Enter / y)."""
        self.dismiss("term")

    def action_kill(self) -> None:
        """Send SIGKILL (k)."""
        self.dismiss("kill")


class RestartScreen(ModalScreen[str | None]):
    """Confirm re-running a local process; opened with `t` on a row without a container.

    Dismisses with ``"term"``, ``"kill"`` or None.
    """

    BINDINGS = [
        Binding("escape,c,n", "cancel", "Cancel"),
        Binding("t,enter,y", "term", "Restart"),
        Binding("k,K", "kill", "Kill -9 then restart"),
    ]

    def __init__(
        self, listener: Listener, cmdline: list[str], cwd: str | None, impacts: list[str]
    ) -> None:
        super().__init__()
        self.listener = listener
        self.cmdline = cmdline
        self.cwd = cwd
        self.impacts = impacts

    def compose(self) -> ComposeResult:
        lst = self.listener
        body = Table.grid(padding=(0, 1))
        body.add_column(width=12, style="bold cyan")
        body.add_column(overflow="fold")
        body.add_row("Service", f"{lst.identity.service} on {lst.key}  (PID {lst.pid})")
        body.add_row("Command", Text(" ".join(self.cmdline)))
        body.add_row(
            "Directory",
            Text(self.cwd or "(unknown; inherits lirts' cwd)", style="" if self.cwd else "yellow"),
        )
        note = Text(
            "The process is stopped, then the same command is started again in that directory "
            "with the same environment. Its output goes to the lirts state directory "
            "(restarts/<port>.log). Best effort: wrappers such as `npm run dev` are not re-run.",
            style="dim",
        )
        body.add_row("Note", note)
        impact = Text()
        for line in self.impacts:
            impact.append(f"{GLYPH_WARNING} ", style="yellow")
            impact.append(line + "\n")
        impact.rstrip()
        if self.impacts:
            body.add_row("Impact", impact)
        with Vertical(classes="dialog", id="kill-dialog"):
            yield Static("Restart local process?", classes="dialog-title")
            yield Static(body, classes="dialog-body")
            with Horizontal(id="kill-buttons"):
                yield Button("Restart (t)", id="term", variant="warning")
                yield Button("Kill -9 + restart (k)", id="kill", variant="error")
                yield Button("Cancel (Esc)", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss({"term": "term", "kill": "kill"}.get(event.button.id or ""))

    def action_cancel(self) -> None:
        """Leave the process as it is (Esc / c / n)."""
        self.dismiss(None)

    def action_term(self) -> None:
        """Stop with SIGTERM, then start the command again (t / Enter / y)."""
        self.dismiss("term")

    def action_kill(self) -> None:
        """Stop with SIGKILL, then start the command again (k)."""
        self.dismiss("kill")
