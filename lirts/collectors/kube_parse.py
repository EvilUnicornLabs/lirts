"""Parsers for ``kubectl`` JSON output and for port-forward / ssh tunnel command lines."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from lirts.collectors.kube_models import KubeDeployment, KubePod, KubeService


def parse_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def owner_of(metadata: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(kind, name)`` of the workload behind a pod (ReplicaSet → Deployment)."""
    for ref in metadata.get("ownerReferences") or []:
        kind, name = ref.get("kind"), ref.get("name") or ""
        if kind == "ReplicaSet":
            base = name.rsplit("-", 1)[0] if "-" in name else name
            return "Deployment", base
        if kind:
            return kind, name
    return None, None


def parse_pods(payload: dict[str, Any]) -> list[KubePod]:
    pods: list[KubePod] = []
    for item in payload.get("items") or []:
        meta = item.get("metadata") or {}
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        statuses = status.get("containerStatuses") or []
        ready = sum(1 for c in statuses if c.get("ready"))
        restarts = sum(int(c.get("restartCount") or 0) for c in statuses)
        reason = None
        for c in statuses:
            state = c.get("state") or {}
            waiting = state.get("waiting") or {}
            terminated = state.get("terminated") or {}
            if waiting.get("reason"):
                reason = waiting["reason"]
                break
            if terminated.get("reason") and terminated["reason"] != "Completed":
                reason = terminated["reason"]
                break
        if not reason and status.get("phase") in ("Failed", "Unknown", "Pending"):
            reason = status.get("reason") or status.get("phase")
        containers = spec.get("containers") or []
        ports: list[int] = []
        for c in containers:
            for p in c.get("ports") or []:
                if p.get("containerPort") and p["containerPort"] not in ports:
                    ports.append(int(p["containerPort"]))
        kind, owner = owner_of(meta)
        pods.append(
            KubePod(
                name=meta.get("name", "?"),
                namespace=meta.get("namespace", "default"),
                phase=status.get("phase", "Unknown"),
                ready=ready,
                total=len(containers),
                restarts=restarts,
                created=parse_timestamp(meta.get("creationTimestamp")),
                node=spec.get("nodeName"),
                owner_kind=kind,
                owner=owner,
                images=[c.get("image", "") for c in containers],
                ports=ports,
                reason=reason,
                ip=status.get("podIP"),
            )
        )
    pods.sort(key=lambda p: (p.namespace, p.name))
    return pods


def parse_deployments(payload: dict[str, Any]) -> list[KubeDeployment]:
    out: list[KubeDeployment] = []
    for item in payload.get("items") or []:
        meta = item.get("metadata") or {}
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        out.append(
            KubeDeployment(
                name=meta.get("name", "?"),
                namespace=meta.get("namespace", "default"),
                desired=int(spec.get("replicas") or 0),
                ready=int(status.get("readyReplicas") or 0),
                updated=int(status.get("updatedReplicas") or 0),
                available=int(status.get("availableReplicas") or 0),
                created=parse_timestamp(meta.get("creationTimestamp")),
            )
        )
    out.sort(key=lambda d: (d.namespace, d.name))
    return out


def parse_services(payload: dict[str, Any]) -> list[KubeService]:
    out: list[KubeService] = []
    for item in payload.get("items") or []:
        meta = item.get("metadata") or {}
        spec = item.get("spec") or {}
        ports = []
        for p in spec.get("ports") or []:
            try:
                ports.append(
                    (
                        int(p.get("port")),
                        str(p.get("targetPort", "")),
                        str(p.get("protocol", "TCP")),
                    )
                )
            except (TypeError, ValueError):
                continue
        out.append(
            KubeService(
                name=meta.get("name", "?"),
                namespace=meta.get("namespace", "default"),
                type=spec.get("type", "ClusterIP"),
                cluster_ip=spec.get("clusterIP"),
                ports=ports,
            )
        )
    out.sort(key=lambda s: (s.namespace, s.name))
    return out


def parse_port_forward_cmdline(cmdline: list[str]) -> dict[str, Any] | None:
    """Understand ``kubectl [--context c] port-forward [-n ns] TARGET LOCAL:REMOTE ...``."""
    if not cmdline or "port-forward" not in cmdline:
        return None
    exe = os.path.basename(cmdline[0])
    if "kubectl" not in exe and not any("kubectl" in part for part in cmdline[:2]):
        return None
    namespace = context = None
    positional: list[str] = []
    args = cmdline[cmdline.index("port-forward") + 1 :]
    # global flags may appear before the subcommand
    head = cmdline[1 : cmdline.index("port-forward")]
    for i, arg in enumerate(head):
        if arg in ("-n", "--namespace") and i + 1 < len(head):
            namespace = head[i + 1]
        elif arg.startswith("--namespace="):
            namespace = arg.split("=", 1)[1]
        elif arg == "--context" and i + 1 < len(head):
            context = head[i + 1]
        elif arg.startswith("--context="):
            context = arg.split("=", 1)[1]
    skip = False
    for i, arg in enumerate(args):
        if skip:
            skip = False
            continue
        if arg in ("-n", "--namespace"):
            namespace = args[i + 1] if i + 1 < len(args) else namespace
            skip = True
        elif arg.startswith("--namespace="):
            namespace = arg.split("=", 1)[1]
        elif arg == "--context":
            context = args[i + 1] if i + 1 < len(args) else context
            skip = True
        elif arg.startswith("--context="):
            context = arg.split("=", 1)[1]
        elif arg in ("--address", "--kubeconfig", "--pod-running-timeout"):
            skip = True
        elif arg.startswith("-"):
            continue
        else:
            positional.append(arg)
    if not positional:
        return None
    target = positional[0]
    mappings: list[tuple[int, int]] = []
    for spec in positional[1:]:
        local_s, _, remote_s = spec.partition(":")
        try:
            local = int(local_s)
            remote = int(remote_s) if remote_s else local
        except ValueError:
            continue
        mappings.append((local, remote))
    kind, _, name = target.partition("/")
    if not name:
        kind, name = "pod", kind
    kind = {
        "deploy": "deployment",
        "deployments": "deployment",
        "svc": "service",
        "services": "service",
        "po": "pod",
        "pods": "pod",
    }.get(kind, kind)
    return {
        "context": context,
        "namespace": namespace,
        "kind": kind,
        "name": name,
        "mappings": mappings,
    }


def parse_ssh_tunnel_cmdline(cmdline: list[str]) -> dict[str, Any] | None:
    """Understand ``ssh -L [bind:]LOCAL:HOST:PORT user@server``."""
    if not cmdline or os.path.basename(cmdline[0]) != "ssh":
        return None
    forwards: list[tuple[int, str, int]] = []
    host = None
    args = cmdline[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "-L" and i + 1 < len(args):
            spec = args[i + 1]
            i += 2
        elif arg.startswith("-L") and len(arg) > 2:
            spec = arg[2:]
            i += 1
        else:
            if not arg.startswith("-") and host is None:
                host = arg
            elif arg in ("-p", "-i", "-o", "-F", "-J", "-l", "-R", "-D", "-W"):
                i += 1
            i += 1
            continue
        parts = spec.split(":")
        try:
            if len(parts) == 3:
                forwards.append((int(parts[0]), parts[1], int(parts[2])))
            elif len(parts) == 4:
                forwards.append((int(parts[1]), parts[2], int(parts[3])))
        except ValueError:
            continue
    if not forwards:
        return None
    return {"host": host, "forwards": forwards}
