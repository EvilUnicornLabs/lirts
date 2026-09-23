"""Pods, services and port-forwards of the current kube context."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Static, TabbedContent, TabPane

from lirts.collectors.kube_models import KubePod, KubeService
from lirts.constants import LOG_TAIL_POD, TOAST_DETAIL, TOAST_LONG, TOAST_NORMAL, TOAST_SHORT
from lirts.engine import Engine
from lirts.tui.screens.help_confirm import ConfirmScreen
from lirts.tui.screens.kube_render import (
    add_forward_columns,
    add_pod_columns,
    add_service_columns,
    cluster_summary,
    fill_forward_rows,
    fill_pod_rows,
    fill_service_rows,
)
from lirts.tui.screens.log import LogScreen
from lirts.tui.screens.pane_focus import focus_active_pane
from lirts.tui.screens.prompt import PromptScreen
from lirts.tui.screens.reach_namespace import NamespaceScreen, ReachScreen


class KubeScreen(Screen[None]):
    """Pods, services and port-forwards of the current context; opened with `K`."""

    BINDINGS = [
        Binding("escape,q,K", "close", "Close"),
        Binding("r", "reload", "Reload"),
        Binding("l", "logs", "Logs"),
        Binding("p", "previous_logs", "Previous logs", show=False),
        Binding("e", "exec", "Shell"),
        Binding("t", "rollout_restart", "Rollout restart"),
        Binding("d", "delete_pod", "Delete pod", show=False),
        Binding("f", "forward", "Port-forward"),
        Binding("x", "stop_forward", "Stop forward"),
        Binding("N", "namespace", "Namespace", show=False),
        Binding("R", "reach", "Reach API", show=False),
        Binding("right", "next_tab", "Next tab", show=False, priority=True),
        Binding("left", "prev_tab", "Previous tab", show=False, priority=True),
        Binding("tab", "next_tab", "Next tab", show=False),
        Binding("shift+tab", "prev_tab", "Previous tab", show=False),
    ]

    KUBE_TABS = ["tab-pods", "tab-services", "tab-forwards"]

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine
        self.kube = engine.kube
        self._pods: list[KubePod] = []
        self._services: list[KubeService] = []
        self._forwards: list[tuple[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Static("Kubernetes", classes="screen-title", id="kube-title")
        yield Static(id="kube-summary")
        with TabbedContent(id="kube-tabs"):
            with TabPane("Pods", id="tab-pods"):
                yield DataTable(id="kube-pods", cursor_type="row", zebra_stripes=True)
            with TabPane("Services", id="tab-services"):
                yield DataTable(id="kube-services", cursor_type="row", zebra_stripes=True)
            with TabPane("Forwards", id="tab-forwards"):
                yield DataTable(id="kube-forwards", cursor_type="row", zebra_stripes=True)
        yield Static(
            "Esc close · ← → switch tabs · ↑ ↓ select · r reload · l logs (p previous) · e shell · "
            "t rollout restart · d delete pod · f port-forward · x stop forward · N namespace · "
            "R reach API server",
            classes="screen-footer",
        )

    def on_mount(self) -> None:
        pods = self.query_one("#kube-pods", DataTable)
        add_pod_columns(pods)
        svcs = self.query_one("#kube-services", DataTable)
        add_service_columns(svcs)
        fwds = self.query_one("#kube-forwards", DataTable)
        add_forward_columns(fwds)
        pods.focus()
        self.render_state()
        if not self.kube.snapshot().available:
            self.reload_in_background()

    # ----- data --------------------------------------------------------------------

    def render_state(self) -> None:
        """Redraw the summary and all three tabs from the provider's cached state."""
        state = self.kube.snapshot()
        self.query_one("#kube-summary", Static).update(cluster_summary(self.kube, state))

        pods = self.query_one("#kube-pods", DataTable)
        pods.clear()
        self._pods = list(state.pods)
        fill_pod_rows(pods, self._pods)

        svcs = self.query_one("#kube-services", DataTable)
        svcs.clear()
        self._services = list(state.services)
        fill_service_rows(svcs, self._services)

        fwds = self.query_one("#kube-forwards", DataTable)
        fwds.clear()
        self._forwards = fill_forward_rows(fwds, kube=self.kube, engine=self.engine)

    @work(thread=True, exclusive=True, group="kube")
    def reload_in_background(self) -> None:
        """Worker: fetch from the cluster off the UI thread, then redraw."""
        self.kube.fetch()
        self.app.call_from_thread(self.render_state)

    def action_reload(self) -> None:
        """Fetch pods, services and forwards from the cluster again (r)."""
        self.notify("Refreshing from the cluster…", timeout=TOAST_SHORT)
        self.reload_in_background()

    def _cycle_tab(self, step: int) -> None:
        tabs = self.query_one("#kube-tabs", TabbedContent)
        idx = self.KUBE_TABS.index(str(tabs.active)) if str(tabs.active) in self.KUBE_TABS else 0
        tabs.active = self.KUBE_TABS[(idx + step) % len(self.KUBE_TABS)]

    def action_next_tab(self) -> None:
        """Move to the tab on the right (→ / Tab)."""
        self._cycle_tab(1)

    def action_prev_tab(self) -> None:
        """Move to the tab on the left (← / Shift+Tab)."""
        self._cycle_tab(-1)

    @on(TabbedContent.TabActivated, "#kube-tabs")
    def _tab_activated(self, event: TabbedContent.TabActivated) -> None:
        focus_active_pane(self, "#kube-tabs")

    def action_close(self) -> None:
        """Leave the screen and refresh the dashboard (Esc / q / K)."""
        self.dismiss(None)

    # ----- selection helpers ---------------------------------------------------------

    def _active_tab(self) -> str:
        return str(self.query_one("#kube-tabs", TabbedContent).active)

    def _row_index(self, table_id: str) -> int | None:
        table = self.query_one(table_id, DataTable)
        if table.row_count == 0:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        return int(str(row_key.value))

    def current_pod(self) -> KubePod | None:
        """The pod under the cursor, when the Pods tab is the active one."""
        idx = self._row_index("#kube-pods")
        return self._pods[idx] if idx is not None and self._active_tab() == "tab-pods" else None

    def current_service(self) -> KubeService | None:
        """The service under the cursor, when the Services tab is the active one."""
        idx = self._row_index("#kube-services")
        return (
            self._services[idx]
            if idx is not None and self._active_tab() == "tab-services"
            else None
        )

    def _need_pod(self) -> KubePod | None:
        pod = self.current_pod()
        if pod is None:
            self.notify("Select a pod on the Pods tab", severity="warning", timeout=TOAST_NORMAL)
        return pod

    # ----- actions -----------------------------------------------------------------

    def _open_logs(self, previous: bool) -> None:
        pod = self._need_pod()
        if pod is None:
            return
        kube = self.kube
        title = f"{pod.namespace}/{pod.name}" + (" (previous container)" if previous else "")
        self.app.push_screen(
            LogScreen(
                self.engine,
                pod.name,
                title,
                tail=LOG_TAIL_POD,
                fetch=lambda tail: kube.logs(
                    pod.namespace, pod=pod.name, tail=tail, previous=previous
                ),
            )
        )

    def action_logs(self) -> None:
        """Show the logs of the selected pod (l)."""
        self._open_logs(False)

    def action_previous_logs(self) -> None:
        """Show the logs of the pod's previous container, after a crash (p)."""
        self._open_logs(True)

    def action_exec(self) -> None:
        """Suspend lirts and open a shell in the selected pod (e)."""
        pod = self._need_pod()
        if pod is None:
            return
        cmd = self.kube.exec_command(pod.namespace, pod=pod.name)
        try:
            with self.app.suspend():
                subprocess.call(cmd)
        except Exception as exc:  # SuspendNotSupported, or kubectl exec itself failed
            self.notify(f"Cannot open a shell here: {exc}", severity="error", timeout=TOAST_LONG)

    def action_rollout_restart(self) -> None:
        """Roll the pod's Deployment, StatefulSet or DaemonSet, after a confirmation (t)."""
        pod = self._need_pod()
        if pod is None:
            return
        if not pod.owner or pod.owner_kind not in ("Deployment", "StatefulSet", "DaemonSet"):
            self.notify(
                "This pod has no restartable owner (use d to delete it instead)",
                severity="warning",
                timeout=TOAST_DETAIL,
            )
            return
        kind, name, ns = pod.owner_kind, pod.owner, pod.namespace

        def done(ok: bool | None) -> None:
            if ok:
                self.run_kube_action(
                    lambda: self.kube.rollout_restart(ns, kind=kind, name=name),
                    f"rollout restart {kind}/{name}",
                )

        self.app.push_screen(
            ConfirmScreen(
                f"Rollout restart {kind}/{name}?",
                Text(f"All pods of {kind}/{name} in {ns} will be replaced."),
            ),
            done,
        )

    def action_delete_pod(self) -> None:
        """Delete the selected pod, after a confirmation (d)."""
        pod = self._need_pod()
        if pod is None:
            return
        ns, name = pod.namespace, pod.name

        def done(ok: bool | None) -> None:
            if ok:
                self.run_kube_action(lambda: self.kube.delete_pod(ns, name), f"delete pod {name}")

        self.app.push_screen(
            ConfirmScreen(
                f"Delete pod {name}?",
                Text(f"Its controller (if any) will create a replacement in {ns}."),
            ),
            done,
        )

    @work(thread=True, group="kube-action")
    def run_kube_action(self, fn: Callable[[], tuple[bool, str]], label: str) -> None:
        """Worker: run one kubectl action off the UI thread, then report and reload."""
        ok, msg = fn()
        self.app.call_from_thread(
            self.notify,
            f"{label}: {msg or 'ok'}",
            severity="information" if ok else "error",
            timeout=TOAST_LONG,
        )
        if ok:
            self.kube.refresh_soon()
            self.app.call_from_thread(self.reload_in_background)

    def action_forward(self) -> None:
        """Start a background port-forward to the selected pod or service (f)."""
        pod = self.current_pod()
        svc = self.current_service()
        if pod is not None:
            target = f"pod/{pod.name}"
            ns = pod.namespace
            default_remote = pod.ports[0] if pod.ports else None
        elif svc is not None:
            target = f"service/{svc.name}"
            ns = svc.namespace
            default_remote = svc.ports[0][0] if svc.ports else None
        else:
            self.notify("Select a pod or a service first", severity="warning", timeout=TOAST_NORMAL)
            return
        default = f"{default_remote}:{default_remote}" if default_remote else ""

        def done(value: str | None) -> None:
            if not value:
                return
            local_s, _, remote_s = value.partition(":")
            try:
                local = int(local_s)
                remote = int(remote_s) if remote_s else (default_remote or local)
            except ValueError:
                self.notify(
                    "Use LOCAL:REMOTE, e.g. 5435:5432", severity="error", timeout=TOAST_DETAIL
                )
                return
            self.run_kube_action(
                lambda: self.kube.start_forward(
                    ns, target=target, local_port=local, remote_port=remote
                ),
                f"port-forward {target}",
            )

        self.app.push_screen(
            PromptScreen(
                f"Port-forward {target}",
                placeholder="LOCAL:REMOTE",
                default=default,
                hint=f"localhost:LOCAL → {target}:REMOTE in {ns}; runs in the background until stopped (x) or lirts kube stop",
            ),
            done,
        )

    def action_stop_forward(self) -> None:
        """Stop the port-forward under the cursor on the Forwards tab (x)."""
        if self._active_tab() != "tab-forwards":
            self.notify(
                "Switch to the Forwards tab to stop one", severity="warning", timeout=TOAST_NORMAL
            )
            return
        idx = self._row_index("#kube-forwards")
        if idx is None:
            return
        source, item = self._forwards[idx]
        if source == "tracked":
            results = self.kube.stop_forward(pid=item.pid)
            msg = "; ".join(m for _, _, m in results) or "nothing to stop"
        else:
            results = self.engine.kill_and_wait([item.pid]) if item.pid else []
            msg = "; ".join(m for _, _, m in results) or "no process"
        self.notify(msg, timeout=TOAST_DETAIL)
        self.set_timer(0.5, self.render_state)

    def action_reach(self) -> None:
        """Ping + TCP connect to the current context's API server."""
        target = self.kube.api_server()
        if not target:
            self.notify(
                "No API server address in the kubeconfig",
                severity="warning",
                timeout=TOAST_DETAIL,
            )
            return
        host, port = target
        self.app.push_screen(
            ReachScreen(host, port, f"Kubernetes API of {self.kube.current_context() or 'context'}")
        )

    def action_namespace(self) -> None:
        """Pick another namespace for this session (N)."""
        state = self.kube.snapshot()

        def done(value: str | None) -> None:
            if value is None:
                return
            if value in ("*", "all"):
                self.kube.all_namespaces = True
                self.kube.namespace = None
            else:
                self.kube.all_namespaces = False
                self.kube.namespace = value
            self.reload_in_background()

        self.app.push_screen(
            NamespaceScreen(self.kube, state.namespace, bool(self.kube.all_namespaces)),
            done,
        )
