"""The star map's data: nodes, edges and how one refresh becomes a graph.

A node is a service, container, tunnel, external host or cluster with a stable id, so the
same thing is the same star across refreshes and restarts.  An edge is an observed relation:
a client talking to a port, a proxy route, a tunnel, an ssh session.  Everything comes from
what lirts already collected; nothing is read from files.  The picture (:mod:`lirts.tui`)
and the text listing (:mod:`lirts.topology_text`) both consume :class:`Graph`; the
builder that turns a refresh into one is :mod:`lirts.topology_build`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from lirts.models import Status

_STATUS_RANK: dict[str, int] = {Status.ERROR: 2, Status.WARNING: 1}

MACHINE_ID = "machine:local"

# One glyph per node kind, shared by the map and the text listing.
NODE_GLYPHS: dict[str, str] = {
    "frontend": "○",
    "backend": "●",
    "db": "◆",
    "cache": "◆",
    "queue": "◆",
    "proxy": "⬢",
    "tool": "▫",
    "system": "·",
    "tunnel": "⇄",
    "host": "⌂",
    "cluster": "☁",
    "service": "▫",
    "machine": "⌂",
}


class NodeKind(StrEnum):
    """What a star on the map is."""

    FRONTEND = "frontend"
    BACKEND = "backend"
    DB = "db"
    CACHE = "cache"
    QUEUE = "queue"
    PROXY = "proxy"
    TOOL = "tool"
    SYSTEM = "system"
    TUNNEL = "tunnel"
    HOST = "host"
    CLUSTER = "cluster"
    SERVICE = "service"  # a Kubernetes service inside the cluster
    MACHINE = "machine"  # this computer, the source of ssh sessions


class EdgeKind(StrEnum):
    """What a line on the map means."""

    CONNECTS = "connects"  # a client process talks to a listening port
    PROXIES = "proxies"  # a reverse proxy sends a host name to a port
    TUNNELS = "tunnels"  # a local port leads to a remote host or cluster
    SESSION = "session"  # this machine has an ssh session open to a host


@dataclass
class Node:
    """One star: a service, container, tunnel, host or cluster."""

    id: str
    kind: str
    label: str
    project: str | None = None
    ports: list[int] = field(default_factory=list)
    source: str | None = None
    row_key: str | None = None  # the table row to jump to
    parent: str | None = None  # a Kubernetes service's cluster
    aliases: list[str] = field(default_factory=list)  # host names that reach it
    live: bool = True
    status: str = Status.HEALTHY
    first_seen: float = 0.0
    last_seen: float = 0.0
    days_seen: list[str] = field(default_factory=list)  # ISO dates, kept by the learned store
    usual: bool = False  # seen on enough different days to be part of the usual picture

    @property
    def port_text(self) -> str:
        return ", ".join(f":{p}" for p in self.ports)

    @property
    def status_rank(self) -> int:
        """Problems first: error 2, warning 1, everything else 0."""
        return _STATUS_RANK.get(self.status, 0)


@dataclass
class Relation:
    """One line: an observed relation between two nodes."""

    src: str
    dst: str
    kind: str
    count: int = 1
    label: str | None = None  # route names, forwarded ports
    rate_in: float | None = None
    rate_out: float | None = None
    live: bool = True
    first_seen: float = 0.0
    last_seen: float = 0.0
    days_seen: list[str] = field(default_factory=list)
    usual: bool = False

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.src, self.dst, self.kind)


@dataclass
class Graph:
    """Nodes and relations of one machine, as seen by lirts."""

    nodes: dict[str, Node] = field(default_factory=dict)
    relations: dict[tuple[str, str, str], Relation] = field(default_factory=dict)
    built_at: float = 0.0

    def add_node(self, node: Node) -> Node:
        """Keep the first node under an id; later sightings only add ports and aliases."""
        existing = self.nodes.get(node.id)
        if existing is None:
            self.nodes[node.id] = node
            return node
        for port in node.ports:
            if port not in existing.ports:
                existing.ports.append(port)
        for alias in node.aliases:
            if alias not in existing.aliases:
                existing.aliases.append(alias)
        if node.status_rank > existing.status_rank:
            existing.status = node.status
        return existing

    def add_relation(self, relation: Relation) -> Relation:
        """Merge a relation into the graph (counts add up when the same line is seen twice)."""
        if relation.src not in self.nodes or relation.dst not in self.nodes:
            return relation
        existing = self.relations.get(relation.key)
        if existing is None:
            self.relations[relation.key] = relation
            return relation
        existing.count += relation.count
        if relation.label and relation.label not in (existing.label or ""):
            existing.label = (
                f"{existing.label}, {relation.label}" if existing.label else relation.label
            )
        return existing

    def relations_of(self, node_id: str) -> list[Relation]:
        """Every line touching a node, outgoing first."""
        out = [r for r in self.relations.values() if r.src == node_id]
        out += [r for r in self.relations.values() if r.dst == node_id and r.src != node_id]
        return out

    def neighbours(self, node_id: str) -> set[str]:
        return {r.dst if r.src == node_id else r.src for r in self.relations_of(node_id)}

    def projects(self) -> list[str]:
        """Project names in display order; the unaffiliated wedge is the empty string, last."""
        names = sorted({n.project or "" for n in self.nodes.values() if n.kind != NodeKind.MACHINE})
        return [p for p in names if p] + ([""] if "" in names else [])

    def node_for_row(self, row_key: str) -> Node | None:
        return next((n for n in self.nodes.values() if n.row_key == row_key), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "built_at": self.built_at,
            "nodes": [asdict(n) for n in self.nodes.values()],
            "relations": [asdict(r) for r in self.relations.values()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Graph:
        graph = cls(built_at=float(data.get("built_at") or 0.0))
        for raw in data.get("nodes", []):
            graph.nodes[str(raw["id"])] = Node(**raw)
        for raw in data.get("relations", []):
            relation = Relation(**raw)
            graph.relations[relation.key] = relation
        return graph
