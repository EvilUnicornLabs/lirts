"""Table columns and row rendering for the Kubernetes screen."""

from __future__ import annotations

import time

from rich.text import Text
from textual.widgets import DataTable

from lirts.collectors.kube import KubeProvider
from lirts.collectors.kube_models import KubeForward, KubePod, KubeService, KubeState
from lirts.engine import Engine
from lirts.insights import format_duration
from lirts.models import Listener


def add_pod_columns(table: DataTable) -> None:
    """Add the columns of the Pods tab."""
    for label, key in (
        ("NAMESPACE", "ns"),
        ("POD", "name"),
        ("READY", "ready"),
        ("STATUS", "status"),
        ("RESTARTS", "restarts"),
        ("AGE", "age"),
        ("NODE", "node"),
        ("OWNER", "owner"),
        ("PORTS", "ports"),
    ):
        table.add_column(label, key=key)


def add_service_columns(table: DataTable) -> None:
    """Add the columns of the Services tab."""
    for label, key in (
        ("NAMESPACE", "ns"),
        ("SERVICE", "name"),
        ("TYPE", "type"),
        ("CLUSTER-IP", "ip"),
        ("PORTS", "ports"),
    ):
        table.add_column(label, key=key)


def add_forward_columns(table: DataTable) -> None:
    """Add the columns of the Forwards tab."""
    for label, key in (
        ("LOCAL", "local"),
        ("TARGET", "target"),
        ("REMOTE", "remote"),
        ("NAMESPACE", "ns"),
        ("PID", "pid"),
        ("SINCE", "since"),
        ("SOURCE", "source"),
    ):
        table.add_column(label, key=key)


def cluster_summary(kube: KubeProvider, state: KubeState) -> Text:
    """The context / namespace / pod-health line above the tabs."""
    summary = Text()
    if not kube.enabled:
        summary.append(
            "kubectl not found or no current context (set kubernetes.enabled in the config)",
            style="yellow",
        )
    elif not state.available:
        summary.append(f"loading… {state.error or ''}", style="dim")
    else:
        bad = state.unhealthy_pods
        summary.append(f"context {state.context}", style="bold")
        summary.append(f"  namespace {state.namespace or 'all'}", style="cyan")
        if state.server:
            summary.append(f"  {state.server}", style="dim")
        summary.append(
            f"\n{len(state.pods)} pods, {len(state.deployments)} deployments, {len(state.services)} services"
        )
        summary.append(
            f"  ·  {len(bad)} not healthy" if bad else "  ·  all pods healthy",
            style="bold red" if bad else "green",
        )
        age = time.time() - state.fetched_at
        summary.append(f"  ·  refreshed {format_duration(age)} ago", style="dim")
    return summary


def fill_pod_rows(table: DataTable, pods: list[KubePod]) -> None:
    """One row per pod, unhealthy ones in red."""
    for i, p in enumerate(pods):
        style = "" if p.healthy else "bold red"
        status = p.reason or p.phase
        table.add_row(
            Text(p.namespace, style="dim"),
            Text(p.name, style=style),
            Text(
                f"{p.ready}/{p.total}",
                style="green" if p.ready == p.total and p.total else "yellow",
            ),
            Text(status, style=style or ("green" if p.phase == "Running" else "")),
            Text(str(p.restarts), style="yellow" if p.restarts else ""),
            Text(format_duration(p.age)),
            Text(p.node or "-", style="dim"),
            Text(f"{p.owner_kind}/{p.owner}" if p.owner else "-", style="dim"),
            Text(", ".join(str(x) for x in p.ports) or "-"),
            key=str(i),
        )


def fill_service_rows(table: DataTable, services: list[KubeService]) -> None:
    """One row per service, with its port mapping."""
    for i, s in enumerate(services):
        ports = ", ".join(f"{port}→{target}/{proto}" for port, target, proto in s.ports) or "-"
        table.add_row(
            Text(s.namespace, style="dim"),
            Text(s.name, style="bold"),
            s.type,
            s.cluster_ip or "-",
            ports,
            key=str(i),
        )


def fill_forward_rows(
    table: DataTable, *, kube: KubeProvider, engine: Engine
) -> list[tuple[str, KubeForward | Listener]]:
    """Port-forwards lirts started, then tunnels detected among the listeners."""
    forwards: list[tuple[str, KubeForward | Listener]] = []
    tracked_pids = set()
    for f in kube.forwards():
        tracked_pids.add(f.pid)
        forwards.append(("tracked", f))
        table.add_row(
            Text(str(f.local_port), style="bold"),
            f.target,
            str(f.remote_port),
            f.namespace or "-",
            str(f.pid),
            format_duration(time.time() - f.started),
            Text("lirts", style="cyan"),
            key=str(len(forwards) - 1),
        )
    for lst in engine.snapshot.listeners:
        t = lst.tunnel
        if not t or lst.pid in tracked_pids:
            continue
        forwards.append(("detected", lst))
        if t.get("type") == "kubectl":
            target = f"{t['kind']}/{t['name']}"
            remote = str(t.get("remote_port") or "?")
            ns = t.get("namespace") or "-"
        else:
            hop = next(((h, r) for lo, h, r in t.get("forwards", []) if lo == lst.port), None)
            target = f"ssh {t.get('host') or ''} → {hop[0]}" if hop else "ssh tunnel"
            remote = str(hop[1]) if hop else "?"
            ns = "-"
        table.add_row(
            Text(str(lst.port), style="bold"),
            target,
            remote,
            ns,
            str(lst.pid or "-"),
            format_duration(lst.uptime_seconds),
            Text("process", style="dim"),
            key=str(len(forwards) - 1),
        )
    return forwards
