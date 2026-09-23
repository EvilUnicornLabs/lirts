"""How one refresh becomes a star map graph.

Rows become nodes (one per service, container, tunnel, ssh host, cluster), the observed
client connections, proxy routes, tunnels and ssh sessions become relations.  Only what the
refresh already holds is used; nothing is read from files.
"""

from __future__ import annotations

import time

from lirts.identity import Role
from lirts.models import Edge, Listener, ListenerState, Snapshot, is_docker_proxy_name
from lirts.topology import MACHINE_ID, EdgeKind, Graph, Node, NodeKind, Relation

_ROLE_KINDS: dict[str, NodeKind] = {
    Role.FRONTEND: NodeKind.FRONTEND,
    Role.BACKEND: NodeKind.BACKEND,
    Role.DB: NodeKind.DB,
    Role.CACHE: NodeKind.CACHE,
    Role.QUEUE: NodeKind.QUEUE,
    Role.PROXY: NodeKind.PROXY,
    Role.TOOL: NodeKind.TOOL,
    Role.SYSTEM: NodeKind.SYSTEM,
    Role.TUNNEL: NodeKind.TUNNEL,
    Role.DOCKER: NodeKind.BACKEND,
    Role.UNKNOWN: NodeKind.BACKEND,
}


def node_id_for(listener: Listener) -> str | None:
    """The stable id of the star a table row belongs to; None for rows that are never stars."""
    if listener.ssh is not None:
        return f"host:{listener.ssh.host}"
    if listener.tunnel:
        return f"tunnel:{listener.port}"
    if listener.container is not None:
        c = listener.container
        return f"ctr:{c.stack or 'docker'}/{c.service or c.name}"
    if not listener.container and is_docker_proxy_name(listener.name):
        return None
    project = listener.identity.project
    service = listener.identity.service or listener.name
    if project:
        return f"svc:{project}/{service}"
    return f"proc:{service}:{listener.port}"


def _kind_for(listener: Listener) -> NodeKind:
    if listener.tunnel:
        return NodeKind.TUNNEL
    return _ROLE_KINDS.get(listener.identity.role, NodeKind.BACKEND)


def _listener_node(listener: Listener, now: float) -> Node | None:
    if listener.ssh is not None:
        return Node(
            id=f"host:{listener.ssh.host}",
            kind=NodeKind.HOST,
            label=listener.ssh.host,
            source=listener.source,
            row_key=listener.key,
            live=True,
            first_seen=listener.first_seen or now,
            last_seen=now,
        )
    node_id = node_id_for(listener)
    if node_id is None:
        return None
    project = listener.identity.project
    if listener.container is not None and listener.container.stack:
        project = listener.container.stack
    return Node(
        id=node_id,
        kind=_kind_for(listener),
        label=listener.identity.service or listener.name,
        project=project,
        ports=[listener.port],
        source=listener.source,
        row_key=listener.key,
        live=listener.state != ListenerState.STOPPED,
        status=listener.status,
        first_seen=listener.first_seen or now,
        last_seen=now,
    )


def _client_node(edge: Edge, now: float) -> Node:
    return Node(
        id=f"client:{edge.client_name}",
        kind=NodeKind.TOOL,
        label=edge.client_name,
        project=edge.client_project,
        first_seen=now,
        last_seen=now,
    )


def build_graph(
    snapshot: Snapshot, *, hide_system: bool = False, now: float | None = None
) -> Graph:
    """The graph of one refresh: rows become nodes, observed relations become lines."""
    now = now or snapshot.timestamp or time.time()
    graph = Graph(built_at=now)
    graph.add_node(
        Node(
            id=MACHINE_ID,
            kind=NodeKind.MACHINE,
            label="this machine",
            first_seen=now,
            last_seen=now,
        )
    )
    by_port: dict[int, str] = {}
    by_pid: dict[int, str] = {}
    for lst in snapshot.listeners:
        if hide_system and lst.identity.role == Role.SYSTEM:
            continue
        node = _listener_node(lst, now)
        if node is None:
            continue
        graph.add_node(node)
        if lst.ssh is not None:
            graph.add_relation(
                Relation(
                    MACHINE_ID,
                    node.id,
                    EdgeKind.SESSION,
                    label=", ".join(lst.ssh.forwards) or None,
                    first_seen=now,
                    last_seen=now,
                )
            )
            continue
        if lst.protocol == "TCP":
            by_port[lst.port] = node.id
        for pid in lst.pids:
            by_pid[pid] = node.id
    _add_connections(graph, snapshot, by_port=by_port, by_pid=by_pid, now=now)
    _add_routes(graph, snapshot, by_port=by_port, now=now)
    _add_cluster(graph, snapshot, by_port=by_port, now=now)
    _add_tunnels(graph, snapshot, by_port=by_port, now=now)
    return graph


def _add_connections(
    graph: Graph, snapshot: Snapshot, *, by_port: dict[int, str], by_pid: dict[int, str], now: float
) -> None:
    for edge in snapshot.edges:
        dst = by_port.get(edge.dst_port)
        if dst is None:
            continue
        src = by_pid.get(edge.client_pid)
        if src is None:
            if is_docker_proxy_name(edge.client_name):
                continue
            src = graph.add_node(_client_node(edge, now)).id
        if src == dst:
            continue
        graph.add_relation(
            Relation(
                src,
                dst,
                EdgeKind.CONNECTS,
                count=edge.count,
                rate_in=edge.bytes_in_rate,
                rate_out=edge.bytes_out_rate,
                first_seen=now,
                last_seen=now,
            )
        )


def _add_routes(graph: Graph, snapshot: Snapshot, *, by_port: dict[int, str], now: float) -> None:
    for route in snapshot.routes:
        src = by_port.get(route.listen_port)
        dst = by_port.get(route.upstream_port) if route.upstream_port else None
        if src is None or dst is None or src == dst:
            continue
        graph.add_relation(
            Relation(src, dst, EdgeKind.PROXIES, label=route.label, first_seen=now, last_seen=now)
        )
        for name in route.names:
            for node_id in (src, dst):
                if name not in graph.nodes[node_id].aliases:
                    graph.nodes[node_id].aliases.append(name)


def _add_cluster(graph: Graph, snapshot: Snapshot, *, by_port: dict[int, str], now: float) -> None:
    kube = snapshot.kube
    if kube is None or not getattr(kube, "available", False):
        return
    context = kube.context or "cluster"
    cluster = graph.add_node(
        Node(
            id=f"k8s:{context}",
            kind=NodeKind.CLUSTER,
            label=context,
            aliases=[kube.server] if kube.server else [],
            first_seen=now,
            last_seen=now,
        )
    )
    for svc in kube.services:
        graph.add_node(
            Node(
                id=f"k8s:{context}/{svc.namespace}/{svc.name}",
                kind=NodeKind.SERVICE,
                label=svc.name,
                project=svc.namespace,
                ports=[int(p[0]) for p in svc.ports],
                parent=cluster.id,
                first_seen=now,
                last_seen=now,
            )
        )


def _add_tunnels(graph: Graph, snapshot: Snapshot, *, by_port: dict[int, str], now: float) -> None:
    for lst in snapshot.listeners:
        if not lst.tunnel:
            continue
        src = by_port.get(lst.port)
        if src is None:
            continue
        info = lst.tunnel
        if info.get("type") == "ssh":
            host = str(info.get("host") or "?")
            dst = graph.add_node(
                Node(
                    id=f"host:{host}", kind=NodeKind.HOST, label=host, first_seen=now, last_seen=now
                )
            ).id
            label = f"→ {info.get('remote_port')}" if info.get("remote_port") else None
        else:
            context = str(
                info.get("context") or (snapshot.kube.context if snapshot.kube else "") or "cluster"
            )
            cluster = graph.add_node(
                Node(
                    id=f"k8s:{context}",
                    kind=NodeKind.CLUSTER,
                    label=context,
                    first_seen=now,
                    last_seen=now,
                )
            )
            target = f"k8s:{context}/{info.get('namespace')}/{info.get('name')}"
            dst = target if target in graph.nodes else cluster.id
            label = f"{info.get('kind')}/{info.get('name')}" if info.get("name") else None
        graph.add_relation(
            Relation(src, dst, EdgeKind.TUNNELS, label=label, first_seen=now, last_seen=now)
        )
