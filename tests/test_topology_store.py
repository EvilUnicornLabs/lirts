"""The learned star map: merging refreshes, usual relations, decay, persistence, clearing."""

from __future__ import annotations

import json
import time
from pathlib import Path

from typer.testing import CliRunner

from lirts.cli import app
from lirts.identity import ROLE_BACKEND, ROLE_DB, ROLE_SYSTEM
from lirts.models import Edge, Identity, Snapshot
from lirts.topology_build import build_graph
from lirts.topology_store import TopologyStore
from lirts.topology_text import render_topology_text
from lirts.tui.starmap_draw import Picture, draw_graph
from tests.conftest import make_listener, make_process

DAY = 86400.0


def _snapshot(*, with_db: bool = True) -> Snapshot:
    api = make_listener(
        port=8001,
        processes=[make_process(pid=8001, name="python")],
        identity=Identity("API", ROLE_BACKEND, 0.9, project="shop"),
    )
    listeners = [api]
    edges = []
    if with_db:
        listeners.append(
            make_listener(port=5432, identity=Identity("PostgreSQL", ROLE_DB, 0.9, project="shop"))
        )
        edges.append(Edge(8001, "python", 5432, count=2))
    return Snapshot(listeners=listeners, edges=edges)


def test_a_relation_becomes_usual_after_enough_days_and_stays_while_idle() -> None:
    store = TopologyStore(days=14, usual_days=3)
    key = ("svc:shop/API", "svc:shop/PostgreSQL", "connects")
    for day in range(3):
        store.observe(build_graph(_snapshot(), now=1.0 + day * DAY), now=1.0 + day * DAY)
    relation = store.graph.relations[key]
    assert len(relation.days_seen) == 3 and relation.usual
    assert store.graph.nodes["svc:shop/PostgreSQL"].usual
    assert relation.first_seen == 1.0

    store.observe(build_graph(_snapshot(with_db=False), now=1.0 + 3 * DAY), now=1.0 + 3 * DAY)
    assert key in store.graph.relations and not store.graph.relations[key].live
    assert not store.graph.nodes["svc:shop/PostgreSQL"].live
    assert store.graph.nodes["svc:shop/API"].live
    text = render_topology_text(store.graph)
    assert "PostgreSQL  :5432  database  not running" in text
    assert "(usual, idle now)" in text
    picture = draw_graph(store.graph, width=100, height=30)
    assert "svc:shop/PostgreSQL" in picture.placements


def test_a_relation_seen_once_is_not_drawn_while_idle() -> None:
    store = TopologyStore(days=14, usual_days=3)
    store.observe(build_graph(_snapshot(), now=1.0), now=1.0)
    store.observe(build_graph(_snapshot(with_db=False), now=2.0), now=2.0)
    relation = store.graph.relations[("svc:shop/API", "svc:shop/PostgreSQL", "connects")]
    assert not relation.live and not relation.usual
    assert "(idle now)" in render_topology_text(store.graph)
    with_lines = draw_graph(build_graph(_snapshot(), now=1.0), width=100, height=30)
    without = draw_graph(store.graph, width=100, height=30, cached=with_lines.placements)
    assert _braille_cells(without) < _braille_cells(with_lines)


def _braille_cells(picture: Picture) -> int:
    return sum(1 for row in picture.rows() for ch in row.plain if 0x2800 < ord(ch) <= 0x28FF)


def test_stars_are_forgotten_after_the_configured_days() -> None:
    store = TopologyStore(days=2, usual_days=3)
    store.observe(build_graph(_snapshot(), now=1.0), now=1.0)
    store.observe(build_graph(_snapshot(with_db=False), now=1.0 + 3 * DAY), now=1.0 + 3 * DAY)
    assert "svc:shop/PostgreSQL" not in store.graph.nodes
    assert store.graph.relations == {}
    assert "svc:shop/API" in store.graph.nodes


def test_store_persists_and_reloads_with_everything_idle(tmp_path: Path) -> None:
    path = tmp_path / "topology.json"
    store = TopologyStore(path, persist=True, days=14, usual_days=3)
    now = time.time()
    store.observe(build_graph(_snapshot(), now=now), now=now)
    assert store.save(force=True)
    data = json.loads(path.read_text())
    assert data["version"] == 1 and len(data["graph"]["nodes"]) == 3

    again = TopologyStore(path, persist=True, days=14, usual_days=3)
    again.load()
    assert set(again.graph.nodes) == set(store.graph.nodes)
    assert all(not n.live for n in again.graph.nodes.values())
    assert again.clear() is True
    assert not path.exists() and again.graph.nodes == {}
    assert again.clear() is False

    off = TopologyStore(path, persist=False)
    off.observe(build_graph(_snapshot(), now=now), now=now)
    assert off.save(force=True) is False


def test_graph_for_drops_system_daemons_when_the_table_hides_them() -> None:
    store = TopologyStore()
    snap = _snapshot()
    snap.listeners.append(make_listener(port=5353, identity=Identity("mDNS", ROLE_SYSTEM, 0.9)))
    store.observe(build_graph(snap, now=1.0), now=1.0)
    assert "proc:mDNS:5353" in store.graph_for().nodes
    assert "proc:mDNS:5353" not in store.graph_for(hide_system=True).nodes


def test_cli_topology_clear_forgets_the_learned_map() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["topology", "--demo", "--clear"])
    assert result.exit_code == 0, result.output
    assert "Nothing learned yet" in result.output
