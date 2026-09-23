"""The star map as text: ``lirts topology`` and the fallback for narrow terminals."""

from __future__ import annotations

from lirts.identity import role_label
from lirts.insights import format_rate
from lirts.topology import MACHINE_ID, NODE_GLYPHS, EdgeKind, Graph, Node, NodeKind, Relation

_EXTERNAL_KINDS = {NodeKind.HOST, NodeKind.CLUSTER, NodeKind.SERVICE}
_STATUS_MARK = {"error": "● error", "warning": "● warn"}
# In front of the node the narrow-terminal listing is showing for the selection.
HIGHLIGHT_MARK = "▶ "
NO_MARK = "  "
_VERBS: dict[str, str] = {
    EdgeKind.CONNECTS: "",
    EdgeKind.PROXIES: " proxies",
    EdgeKind.TUNNELS: " tunnel",
    EdgeKind.SESSION: " ssh session",
}


def kind_label(node: Node) -> str:
    """What the node is in words: its role name, or "k8s service" for a cluster child."""
    return "k8s service" if node.kind == NodeKind.SERVICE else role_label(node.kind)


def describe_node(node: Node) -> str:
    """One line for a node: glyph, label, ports, kind, status and known names."""
    parts = [f"{NODE_GLYPHS.get(node.kind, '·')} {node.label}"]
    if node.ports:
        parts.append(node.port_text)
    parts.append(kind_label(node))
    if node.source == "docker":
        parts.append("container")
    if not node.live:
        parts.append("not running")
    mark = _STATUS_MARK.get(node.status)
    if mark:
        parts.append(mark)
    if node.aliases:
        parts.append(", ".join(node.aliases))
    return "  ".join(parts)


def describe_relation(graph: Graph, relation: Relation, *, seen_from: str) -> str:
    """One line for a relation as seen from ``seen_from``: arrow, the other node, details."""
    outgoing = relation.src == seen_from
    other = graph.nodes[relation.dst if outgoing else relation.src]
    arrow = "→" if outgoing else "←"
    text = f"{arrow} {other.label}"
    if other.ports and other.kind != NodeKind.MACHINE:
        text += f" {other.port_text}"
    text += _VERBS.get(relation.kind, "")
    if relation.label:
        text += f" {relation.label}"
    if relation.kind == EdgeKind.CONNECTS and relation.count > 1:
        text += f" ×{relation.count}"
    if relation.rate_in or relation.rate_out:
        text += f" ↓{format_rate(relation.rate_in or 0)} ↑{format_rate(relation.rate_out or 0)}"
    if not relation.live:
        text += "  (usual, idle now)" if relation.usual else "  (idle now)"
    return text


def render_topology_text(graph: Graph, *, highlight: str | None = None) -> str:
    """The whole graph as an indented list: wedges, their nodes, each node's relations.

    ``highlight`` is the node id the reader is on (the selected star on a narrow terminal);
    its line is marked so it can be found in a long listing.
    """

    def mark(node_id: str) -> str:
        return HIGHLIGHT_MARK if node_id == highlight else NO_MARK

    lines: list[str] = []
    nodes = [n for n in graph.nodes.values() if n.id != MACHINE_ID]
    if not nodes:
        return "Nothing on the map yet: no listening ports, containers, tunnels or sessions."
    local = [n for n in nodes if n.kind not in _EXTERNAL_KINDS]
    external = [n for n in nodes if n.kind in _EXTERNAL_KINDS and n.parent is None]
    for project in graph.projects():
        members = sorted(
            (n for n in local if (n.project or "") == project),
            key=lambda n: (n.ports[0] if n.ports else 10**6, n.label),
        )
        if not members:
            continue
        lines.append(project or "(no project)")
        for node in members:
            lines.append(f"{mark(node.id)}{describe_node(node)}")
            lines.extend(
                f"      {describe_relation(graph, r, seen_from=node.id)}"
                for r in graph.relations_of(node.id)
            )
    if external:
        lines.append("external")
        for node in sorted(external, key=lambda n: (n.kind, n.label)):
            lines.append(f"{mark(node.id)}{describe_node(node)}")
            lines.extend(
                f"      {describe_relation(graph, r, seen_from=node.id)}"
                for r in graph.relations_of(node.id)
            )
            children = [n for n in graph.nodes.values() if n.parent == node.id]
            lines.extend(f"    {mark(child.id)}{describe_node(child)}" for child in children)
    counts = f"{len(nodes)} nodes, {len(graph.relations)} relations"
    return "\n".join(lines) + f"\n\n{counts}"
