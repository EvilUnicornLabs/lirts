"""Where each star goes: rings by role, a wedge per project, stable between refreshes.

Rings from the centre out: databases, caches and queues; backends, tunnels and the
Kubernetes services; frontends, proxies and tools; external hosts, clusters and system
services on the rim.  Every project (or stack) owns an angular wedge, ordered by name, so a
project reads as one constellation and the picture is the same after a restart.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from lirts.topology import MACHINE_ID, Graph, Node, NodeKind

RING_OF_KIND: dict[str, int] = {
    NodeKind.DB: 0,
    NodeKind.CACHE: 0,
    NodeKind.QUEUE: 0,
    NodeKind.BACKEND: 1,
    NodeKind.TUNNEL: 1,
    NodeKind.SERVICE: 1,
    NodeKind.FRONTEND: 2,
    NodeKind.PROXY: 2,
    NodeKind.TOOL: 2,
    NodeKind.HOST: 3,
    NodeKind.CLUSTER: 3,
    NodeKind.SYSTEM: 3,
}
# Radius of each ring as a fraction of the half-width / half-height of the map.
RING_RADII = (0.2, 0.45, 0.7, 0.95)
# Wedges never get narrower than this many degrees, whatever their node count.
MIN_WEDGE_DEGREES = 18.0
# Free angle kept at both edges of a wedge so neighbouring projects do not touch.
WEDGE_PADDING = 0.12


@dataclass(frozen=True)
class Placement:
    """A star's cell on the map and the wedge it belongs to."""

    x: int
    y: int
    ring: int
    project: str
    angle: float  # radians, 0 at the top, clockwise


def visible_nodes(graph: Graph, *, focus: str | None = None, hide_idle: bool = False) -> list[Node]:
    """The nodes drawn: no machine node, cluster children only when focused, idle ones optional.

    ``focus`` is a project name or a node id (the cluster); only that wedge is drawn then.
    """
    out: list[Node] = []
    for node in graph.nodes.values():
        if node.id == MACHINE_ID:
            continue
        if hide_idle and not node.live:
            continue
        if node.parent is not None and node.parent != focus:
            continue
        if (
            focus is not None
            and node.id != focus
            and (node.project or "") != focus
            and node.parent != focus
        ):
            continue
        out.append(node)
    return out


def wedges(nodes: list[Node]) -> list[tuple[str, float, float]]:
    """``(project, start, end)`` angles in radians, ordered by project name, unaffiliated last."""
    counts: dict[str, int] = {}
    for node in nodes:
        counts[node.project or ""] = counts.get(node.project or "", 0) + 1
    names = sorted(p for p in counts if p) + ([""] if "" in counts else [])
    if not names:
        return []
    minimum = math.radians(MIN_WEDGE_DEGREES)
    weights = {p: max(minimum, math.sqrt(counts[p])) for p in names}
    total = sum(weights.values())
    out: list[tuple[str, float, float]] = []
    start = 0.0
    for name in names:
        span = 2 * math.pi * weights[name] / total
        out.append((name, start, start + span))
        start += span
    return out


def layout(
    graph: Graph,
    *,
    width: int,
    height: int,
    focus: str | None = None,
    hide_idle: bool = False,
    scale: float = 1.0,
    cached: dict[str, Placement] | None = None,
) -> dict[str, Placement]:
    """Cell positions for every visible node inside a ``width`` by ``height`` map.

    ``scale`` widens or narrows the rings; ``cached`` positions (from an earlier draw at the
    same size) are kept so a refresh never moves a star, new stars take a free cell.
    """
    nodes = visible_nodes(graph, focus=focus, hide_idle=hide_idle)
    if not nodes or width < 4 or height < 4:
        return {}
    cx, cy = (width - 1) / 2, (height - 1) / 2
    half_w, half_h = max(1.0, cx - 1) * scale, max(1.0, cy - 1) * scale
    placements: dict[str, Placement] = {}
    taken: set[tuple[int, int]] = set()
    for node in nodes:
        if cached and node.id in cached:
            placements[node.id] = cached[node.id]
            taken.add((cached[node.id].x, cached[node.id].y))
    for project, start, end in wedges(nodes):
        members = [n for n in nodes if (n.project or "") == project]
        pad = (end - start) * WEDGE_PADDING
        inner_start, inner_end = start + pad, end - pad
        for ring in range(len(RING_RADII)):
            on_ring = sorted(
                (n for n in members if RING_OF_KIND.get(n.kind, 1) == ring),
                key=lambda n: (n.ports[0] if n.ports else 10**6, n.label),
            )
            for index, node in enumerate(on_ring):
                if node.id in placements:
                    continue
                fraction = (index + 1) / (len(on_ring) + 1)
                angle = inner_start + (inner_end - inner_start) * fraction
                radius = RING_RADII[ring]
                x = min(width - 1, max(0, round(cx + half_w * radius * math.sin(angle))))
                y = min(height - 1, max(0, round(cy - half_h * radius * math.cos(angle))))
                while (x, y) in taken and y < height - 1:
                    y += 1
                taken.add((x, y))
                placements[node.id] = Placement(x, y, ring, project, angle)
    return placements


# ----- moving the selection ------------------------------------------------------------------

# A row is about twice as tall as a column is wide; distances weigh rows accordingly.
ROW_WEIGHT = 2.0


def nearest(placements: dict[str, Placement], current: str | None, direction: str) -> str | None:
    """The star nearest to ``current`` in ``direction`` (up, down, left, right); None if none."""
    if not placements:
        return None
    if current is None or current not in placements:
        return cycle(placements, None)
    here = placements[current]
    best: tuple[float, str] | None = None
    for node_id, place in placements.items():
        if node_id == current:
            continue
        dx, dy = place.x - here.x, place.y - here.y
        ahead = {
            "right": dx > 0 and abs(dy) * ROW_WEIGHT <= dx * 1.5,
            "left": dx < 0 and abs(dy) * ROW_WEIGHT <= -dx * 1.5,
            "down": dy > 0 and abs(dx) <= dy * ROW_WEIGHT * 1.5,
            "up": dy < 0 and abs(dx) <= -dy * ROW_WEIGHT * 1.5,
        }.get(direction, False)
        if not ahead:
            continue
        distance = abs(dx) + abs(dy) * ROW_WEIGHT
        if best is None or distance < best[0]:
            best = (distance, node_id)
    return best[1] if best else None


def cycle(
    placements: dict[str, Placement], current: str | None, *, backwards: bool = False
) -> str | None:
    """The next star in reading order (wedge, ring, angle); wraps around."""
    order = sorted(placements, key=lambda n: (placements[n].angle, placements[n].ring))
    if not order:
        return None
    if current not in order:
        return order[-1] if backwards else order[0]
    index = order.index(current) + (-1 if backwards else 1)
    return order[index % len(order)]
