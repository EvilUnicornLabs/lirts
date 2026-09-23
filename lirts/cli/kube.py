"""The ``lirts kube`` sub-app: pods, services, logs, shell, restarts and port-forwards."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from lirts.cli.common import ConfigOpt, console, err_console
from lirts.collectors.kube import KubeProvider
from lirts.collectors.kube_models import KubePod, KubeState
from lirts.config import load_config, state_dir
from lirts.config_view import ConfigView
from lirts.constants import AMBIGUOUS_POD_NAMES_SHOWN, LOG_TAIL_POD
from lirts.insights import format_duration

kube_app = typer.Typer(
    help="Kubernetes via kubectl: pods, services, logs, shell, restarts, port-forwards."
)

KubeCtxOpt = Annotated[
    str | None,
    typer.Option("--context", help="kubeconfig context (default: current).", show_default=False),
]
KubeNsOpt = Annotated[
    str | None,
    typer.Option(
        "--namespace", "-n", help="Namespace (default: the context's).", show_default=False
    ),
]
KubeAllOpt = Annotated[bool, typer.Option("--all-namespaces", "-A", help="All namespaces.")]


def _kube(
    config: Path | None,
    *,
    context: str | None,
    namespace: str | None,
    all_ns: bool = False,
) -> KubeProvider:
    """A kubectl provider for one command, with the flags overriding the config."""
    kube_cfg = ConfigView(load_config(config)).kubernetes
    provider = KubeProvider(
        enabled=True,
        context=context or kube_cfg.context,
        namespace=namespace or kube_cfg.namespace,
        all_namespaces=all_ns or kube_cfg.all_namespaces,
        timeout=kube_cfg.timeout,
        state_root=state_dir(),
    )
    if not provider.kubectl:
        err_console.print("[red]kubectl not found on PATH[/]")
        raise typer.Exit(code=1)
    return provider


def _kube_state(provider: KubeProvider) -> KubeState:
    """The cluster as kubectl sees it, or exit 1 with the reason it is unavailable."""
    state = provider.fetch()
    if not state.available:
        err_console.print(
            f"[red]Kubernetes not available: {escape(state.error or 'unknown error')}[/]"
        )
        raise typer.Exit(code=1)
    return state


@kube_app.command("pods")
def kube_pods(
    config: ConfigOpt = None,
    context: KubeCtxOpt = None,
    namespace: KubeNsOpt = None,
    all_ns: KubeAllOpt = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List pods with readiness, restarts, age and owner."""
    state = _kube_state(_kube(config, context=context, namespace=namespace, all_ns=all_ns))
    if as_json:
        console.print_json(json.dumps([asdict(p) for p in state.pods], default=str))
        return
    console.print(
        f"[bold]{escape(state.context or '')}[/]  namespace {escape(state.namespace or 'all')}  ({len(state.pods)} pods)"
    )
    table = Table(box=None, pad_edge=False, header_style="bold", show_edge=False)
    for col in ("NAMESPACE", "POD", "READY", "STATUS", "RESTARTS", "AGE", "OWNER", "PORTS"):
        table.add_column(col, no_wrap=True)
    for p in state.pods:
        style = "" if p.healthy else "bold red"
        table.add_row(
            p.namespace,
            p.name,
            f"{p.ready}/{p.total}",
            p.reason or p.phase,
            str(p.restarts),
            format_duration(p.age),
            f"{p.owner_kind}/{p.owner}" if p.owner else "-",
            ", ".join(str(x) for x in p.ports) or "-",
            style=style,
        )
    console.print(table)
    bad = state.unhealthy_pods
    if bad:
        console.print(
            f"\n[bold red]{len(bad)} pod(s) not healthy:[/] "
            + ", ".join(escape(p.name) for p in bad)
        )


@kube_app.command("services")
def kube_services(
    config: ConfigOpt = None,
    context: KubeCtxOpt = None,
    namespace: KubeNsOpt = None,
    all_ns: KubeAllOpt = False,
) -> None:
    """List services with their ports (the usual port-forward targets)."""
    state = _kube_state(_kube(config, context=context, namespace=namespace, all_ns=all_ns))
    table = Table(box=None, pad_edge=False, header_style="bold", show_edge=False)
    for col in ("NAMESPACE", "SERVICE", "TYPE", "CLUSTER-IP", "PORTS"):
        table.add_column(col, no_wrap=True)
    for s in state.services:
        table.add_row(
            s.namespace,
            s.name,
            s.type,
            s.cluster_ip or "-",
            ", ".join(f"{p}→{t}/{pr}" for p, t, pr in s.ports) or "-",
        )
    console.print(table)


@kube_app.command("logs")
def kube_logs(
    pod: Annotated[str, typer.Argument(help="Pod name (or a unique prefix).")],
    config: ConfigOpt = None,
    context: KubeCtxOpt = None,
    namespace: KubeNsOpt = None,
    tail: Annotated[int, typer.Option("--tail", help="Lines to show.")] = LOG_TAIL_POD,
    previous: Annotated[
        bool, typer.Option("--previous", "-p", help="Logs of the previous container instance.")
    ] = False,
    follow: Annotated[
        bool, typer.Option("--follow", "-f", help="Stream logs (hands over to kubectl).")
    ] = False,
) -> None:
    """Show a pod's logs."""
    provider = _kube(config, context=context, namespace=namespace)
    state = _kube_state(provider)
    match = _match_pod(state, pod)
    if follow:
        cmd = (
            [provider.kubectl or "kubectl"]
            + (["--context", provider.context] if provider.context else [])
            + ["logs", "-f", "-n", match.namespace, match.name, f"--tail={tail}"]
        )
        raise typer.Exit(code=subprocess.call(cmd))
    console.print(
        provider.logs(match.namespace, pod=match.name, tail=tail, previous=previous),
        markup=False,
        highlight=False,
    )


def _match_pod(state: KubeState, name: str) -> KubePod:
    """The pod called ``name``, or the only one starting with it; exit 1 otherwise."""
    exact = [p for p in state.pods if p.name == name]
    if exact:
        return exact[0]
    prefix = [p for p in state.pods if p.name.startswith(name)]
    if len(prefix) == 1:
        return prefix[0]
    if not prefix:
        err_console.print(f"[red]No pod named or starting with '{escape(name)}'[/]")
    else:
        shown = ", ".join(escape(p.name) for p in prefix[:AMBIGUOUS_POD_NAMES_SHOWN])
        err_console.print(f"[red]Ambiguous: {shown}[/]")
    raise typer.Exit(code=1)


@kube_app.command("exec")
def kube_exec(
    pod: Annotated[str, typer.Argument(help="Pod name (or a unique prefix).")],
    config: ConfigOpt = None,
    context: KubeCtxOpt = None,
    namespace: KubeNsOpt = None,
) -> None:
    """Open an interactive shell in a pod."""
    provider = _kube(config, context=context, namespace=namespace)
    match = _match_pod(_kube_state(provider), pod)
    raise typer.Exit(code=subprocess.call(provider.exec_command(match.namespace, pod=match.name)))


@kube_app.command("restart")
def kube_restart(
    name: Annotated[
        str, typer.Argument(help="Deployment name, or KIND/NAME (statefulset/x, daemonset/x).")
    ],
    config: ConfigOpt = None,
    context: KubeCtxOpt = None,
    namespace: KubeNsOpt = None,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    """Rollout-restart a deployment (or statefulset / daemonset)."""
    provider = _kube(config, context=context, namespace=namespace)
    kind, _, obj = name.partition("/")
    if not obj:
        kind, obj = "deployment", kind
    ns = namespace or provider.current_namespace() or "default"
    if not yes and not typer.confirm(f"Rollout restart {kind}/{obj} in {ns}?", default=False):
        raise typer.Exit(code=0)
    ok, msg = provider.rollout_restart(ns, kind=kind, name=obj)
    console.print(f"[{'green' if ok else 'red'}]{escape(msg or 'ok')}[/]")
    raise typer.Exit(code=0 if ok else 1)


@kube_app.command("forward")
def kube_forward(
    target: Annotated[str, typer.Argument(help="pod/NAME, service/NAME or deployment/NAME.")],
    ports: Annotated[str, typer.Argument(help="LOCAL:REMOTE, e.g. 5435:5432.")],
    config: ConfigOpt = None,
    context: KubeCtxOpt = None,
    namespace: KubeNsOpt = None,
) -> None:
    """Start a background port-forward that lirts tracks (stop with `lirts kube stop`)."""
    provider = _kube(config, context=context, namespace=namespace)
    local_s, _, remote_s = ports.partition(":")
    try:
        local = int(local_s)
        remote = int(remote_s) if remote_s else local
    except ValueError:
        err_console.print("[red]Ports must be LOCAL:REMOTE[/]")
        raise typer.Exit(code=1) from None
    ns = namespace or provider.current_namespace() or "default"
    if "/" not in target:
        target = f"pod/{target}"
    ok, msg = provider.start_forward(ns, target=target, local_port=local, remote_port=remote)
    console.print(f"[{'green' if ok else 'red'}]{escape(msg)}[/]")
    raise typer.Exit(code=0 if ok else 1)


@kube_app.command("forwards")
def kube_forwards(config: ConfigOpt = None) -> None:
    """List port-forwards started by lirts that are still running."""
    provider = _kube(config, context=None, namespace=None)
    items = provider.forwards()
    if not items:
        console.print(
            "No tracked port-forwards. (Forwards started elsewhere show up in `lirts list` as tunnels.)"
        )
        return
    for f in items:
        console.print(
            f"  :{f.local_port} → {escape(f.target)}:{f.remote_port}  ns {escape(f.namespace or '-')}  PID {f.pid}  since {format_duration(time.time() - f.started)}"
        )


@kube_app.command("stop")
def kube_stop(
    port: Annotated[int | None, typer.Argument(help="Local port of the forward to stop.")] = None,
    all_: Annotated[bool, typer.Option("--all", help="Stop every tracked forward.")] = False,
    config: ConfigOpt = None,
) -> None:
    """Stop a tracked port-forward (by local port) or all of them."""
    provider = _kube(config, context=None, namespace=None)
    if port is None and not all_:
        err_console.print("[red]Give a local port or --all[/]")
        raise typer.Exit(code=1)
    results = (
        provider.stop_forward(local_port=port) if port is not None else provider.stop_forward()
    )
    if not results:
        console.print("Nothing to stop.")
        return
    for lp, ok, msg in results:
        console.print(f"  :{lp}: [{'green' if ok else 'red'}]{escape(msg)}[/]")


@kube_app.command("contexts")
def kube_contexts(config: ConfigOpt = None) -> None:
    """List kubeconfig contexts (current one marked)."""
    provider = _kube(config, context=None, namespace=None)
    for name, current in provider.contexts():
        console.print(f"  {'*' if current else ' '} {escape(name)}")
