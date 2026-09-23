"""The actions that push a secondary screen."""

from __future__ import annotations

from lirts.constants import TOAST_DETAIL, TOAST_LONG, TOAST_NORMAL
from lirts.insights import explain_sections
from lirts.tui.app_base import LirtsAppBase
from lirts.tui.screens import (
    DetailsScreen,
    EventsScreen,
    ExplainScreen,
    GraphScreen,
    HealthScreen,
    HelpScreen,
    KubeScreen,
    ReachScreen,
    SettingsScreen,
    SetupScreen,
    StackScreen,
    StarMapScreen,
    open_menu,
)


class ScreensMixin(LirtsAppBase):
    """Opens the details, events, health, graph, Kubernetes, stack and help screens."""

    def action_events(self) -> None:
        """Open the notification centre with every recorded event (n)."""
        if isinstance(self.screen, EventsScreen):
            return
        since = self._events_seen_ts
        self._events_seen_ts = max(
            (e.timestamp for e in self.engine.history.all_events()), default=self._events_seen_ts
        )

        def done(port: int | None) -> None:
            if port is not None:
                self.set_filter(str(port))
            elif self._first_refresh_done:
                self.apply_snapshot(self.snapshot)

        self.push_screen(EventsScreen(self.engine, since), done)

    def action_menu(self) -> None:
        """Open the small Esc menu: settings, help, the star map or quit."""
        open_menu(self)

    def action_help(self) -> None:
        """Open the keyboard help (? or F1)."""
        self.push_screen(HelpScreen())

    def action_setup(self) -> None:
        """Step-by-step settings wizard (first start, or W any time)."""
        if isinstance(self.screen, SetupScreen):
            return

        def done(saved: bool | None) -> None:
            if saved:
                self.notify(f"Settings saved to {self.cfg.path}", timeout=TOAST_DETAIL)

        self.push_screen(SetupScreen(self), done)

    def action_settings(self) -> None:
        """Open the settings screen, where every option is editable in place (,)."""
        if isinstance(self.screen, SettingsScreen):
            return

        def done(_: None) -> None:
            self.refresh_data()

        self.push_screen(SettingsScreen(self), done)

    def action_show_details(self) -> None:
        """Open the tabbed details of the current row, or fold a group header (i / Enter)."""
        group = self.selected_group()
        if group:
            self.toggle_collapse(group[0])
            return
        lst = self.selected()
        if lst is None:
            return
        if isinstance(self.screen, DetailsScreen):
            return
        self.push_screen(DetailsScreen(lst, self.snapshot, self.cfg))

    def action_check_health(self) -> None:
        """One-off health check of the visible rows, shown in a results screen."""
        if isinstance(self.screen, HealthScreen):
            self.screen.action_rerun()
            return
        if self._marked:
            targets = [x for x in self.visible_listeners() if x.key in self._marked]
            scope = f"{len(targets)} marked row(s)"
        else:
            targets = self.visible_listeners()
            scope = f"all {len(targets)} visible row(s)"
        if not targets:
            self.notify("Nothing to check", timeout=TOAST_NORMAL)
            return

        def done(port: int | None) -> None:
            self._row_order = []  # rows may change status; rebuild
            self.update_table()
            self._sync_side_panel()
            if port is not None:
                self.set_filter(str(port))

        self.push_screen(HealthScreen(self.engine, targets, scope), done)

    def action_reach(self) -> None:
        """Ping + TCP connect for the ssh host under the cursor (local ports: use H)."""
        lst = self.selected()
        if lst is None:
            return
        if lst.ssh is None:
            self.notify(
                "Reachability is for ssh rows (remote hosts); H checks local ports",
                severity="warning",
                timeout=TOAST_DETAIL,
            )
            return
        # The session already knows the real address; aliases from ssh config need no lookup.
        remote_host = lst.ssh.remote.rsplit(":", 1)[0] if lst.ssh.remote else lst.ssh.host
        self.push_screen(ReachScreen(remote_host, lst.port, f"ssh host {lst.ssh.host}"))

    def action_graph(self) -> None:
        """Open the "who talks to whom" screen (w)."""
        if isinstance(self.screen, GraphScreen):
            return
        self.push_screen(GraphScreen(self.engine))

    def action_starmap(self) -> None:
        """Open the star map (M)."""
        if isinstance(self.screen, StarMapScreen):
            return
        self.push_screen(StarMapScreen(self.engine, app_ref=self))

    def action_kubernetes(self) -> None:
        """Open the Kubernetes screen: pods, services and port-forwards (K)."""
        if isinstance(self.screen, KubeScreen):
            return
        if not self.engine.kube.kubectl:
            self.notify("kubectl not found on PATH", severity="warning", timeout=TOAST_DETAIL)
            return
        if not self.engine.kube.enabled:
            self.notify(
                "Kubernetes is off: no current context in the kubeconfig "
                "(or kubernetes.enabled is false)",
                severity="warning",
                timeout=TOAST_LONG,
            )
            return

        def done(_: None) -> None:
            self.refresh_data()

        self.push_screen(KubeScreen(self.engine), done)

    def action_stacks(self) -> None:
        """Open the compose-stack screen (g)."""
        lst = self.selected()
        preselect = lst.container.stack if lst and lst.container else None

        def done(project: str | None) -> None:
            if project:
                self.set_filter(project)
            self.refresh_data()

        self.push_screen(StackScreen(self.engine, preselect), done)

    def action_explain(self) -> None:
        """Explain the whole machine in tabs (E)."""
        sections = explain_sections(self.snapshot, self.engine.last_session_diff)
        self.push_screen(ExplainScreen(sections))
