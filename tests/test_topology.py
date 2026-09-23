"""The star map data: nodes and relations built from a refresh, and the text listing."""

from __future__ import annotations

import json
from typing import Any

from typer.testing import CliRunner

from lirts.cli import app
from lirts.collectors.proxies import Route
from lirts.config import DEFAULT_CONFIG, validate
from lirts.demo import DemoEngine
from lirts.identity import ROLE_BACKEND, ROLE_DB, ROLE_PROXY, ROLE_SYSTEM
from lirts.models import ContainerInfo, Edge, Identity, Snapshot, SshSession
from lirts.topology import MACHINE_ID, EdgeKind, Graph, Node, NodeKind, Relation
from lirts.topology_build import build_graph, node_id_for
from lirts.topology_text import render_topology_text
from tests.conftest import make_listener, make_process


def _demo_graph() -> Graph:
    cfg: dict[str, Any] = validate(dict(DEFAULT_CONFIG))
    cfg["refresh_interval"] = 60.0
    engine = DemoEngine(cfg)
    try:
        return build_graph(engine.refresh_sync())
    finally:
        engine.close()


def test_node_ids_are_stable_per_service_container_tunnel_and_host() -> None:
    api = make_listener(port=8000, identity=Identity("API", ROLE_BACKEND, 0.9, project="shop"))
    assert node_id_for(api) == "svc:shop/API"
    loose = make_listener(port=3001, identity=Identity("Node.js app", ROLE_BACKEND, 0.5))
    assert node_id_for(loose) == "proc:Node.js app:3001"
    ctr = ContainerInfo("abc", "abc", "shop-db-1", "postgres:16", stack="shop", service="db")
    assert node_id_for(make_listener(port=5432, container=ctr)) == "ctr:shop/db"
    tunnel = make_listener(port=9090, tunnel={"type": "ssh", "host": "bastion", "remote_port": 80})
    assert node_id_for(tunnel) == "tunnel:9090"
    session = make_listener(port=22, ssh=SshSession(1, "bastion", "10.0.0.1:22"))
    assert node_id_for(session) == "host:bastion"


def test_docker_proxy_process_is_never_a_node() -> None:
    proxy = make_listener(port=5000, name="com.docker.backend")
    assert node_id_for(proxy) is None
    graph = build_graph(Snapshot(listeners=[proxy]), now=1.0)
    assert list(graph.nodes) == [MACHINE_ID]


def test_one_node_per_service_lists_every_port() -> None:
    vite = Identity("Vite dev server", ROLE_BACKEND, 0.9, project="web")
    snap = Snapshot(
        listeners=[
            make_listener(port=5173, identity=vite),
            make_listener(port=24678, identity=vite),
        ]
    )
    graph = build_graph(snap, now=1.0)
    node = graph.nodes["svc:web/Vite dev server"]
    assert node.ports == [5173, 24678]
    assert node.port_text == ":5173, :24678"


def test_connections_routes_and_sessions_become_relations() -> None:
    nginx = make_listener(port=80, name="nginx", identity=Identity("Nginx", ROLE_PROXY, 0.9))
    api = make_listener(
        port=8001,
        name="python",
        processes=[make_process(pid=8001, name="python")],
        identity=Identity("API", ROLE_BACKEND, 0.9, project="shop"),
    )
    db = make_listener(port=5432, identity=Identity("PostgreSQL", ROLE_DB, 0.9, project="shop"))
    session = make_listener(
        port=22, ssh=SshSession(1, "bastion", "10.0.0.1:22", forwards=["-L 9090"])
    )
    snap = Snapshot(
        listeners=[nginx, api, db, session],
        edges=[
            Edge(8001, "python", 5432, count=2),
            Edge(4242, "Google Chrome Helper", 8001, count=3),
        ],
        routes=[Route("nginx", 80, ["api.local"], "127.0.0.1:8001", 8001)],
    )
    graph = build_graph(snap, now=1.0)
    kinds = {(r.src, r.dst): r.kind for r in graph.relations.values()}
    assert kinds[("svc:shop/API", "svc:shop/PostgreSQL")] == EdgeKind.CONNECTS
    assert kinds[("client:Google Chrome Helper", "svc:shop/API")] == EdgeKind.CONNECTS
    assert kinds[("proc:Nginx:80", "svc:shop/API")] == EdgeKind.PROXIES
    assert kinds[(MACHINE_ID, "host:bastion")] == EdgeKind.SESSION
    assert graph.relations[("svc:shop/API", "svc:shop/PostgreSQL", "connects")].count == 2
    assert graph.nodes["proc:Nginx:80"].aliases == ["api.local"]
    assert graph.nodes["svc:shop/API"].aliases == ["api.local"]
    assert graph.neighbours("svc:shop/API") == {
        "svc:shop/PostgreSQL",
        "client:Google Chrome Helper",
        "proc:Nginx:80",
    }
    assert graph.projects() == ["shop", ""]


def test_hide_system_drops_system_daemons() -> None:
    mdns = make_listener(port=5353, identity=Identity("mDNS", ROLE_SYSTEM, 0.9))
    assert "proc:mDNS:5353" in build_graph(Snapshot(listeners=[mdns]), now=1.0).nodes
    assert (
        "proc:mDNS:5353"
        not in build_graph(Snapshot(listeners=[mdns]), hide_system=True, now=1.0).nodes
    )


def test_graph_survives_a_json_round_trip() -> None:
    graph = _demo_graph()
    again = Graph.from_dict(json.loads(json.dumps(graph.to_dict())))
    assert set(again.nodes) == set(graph.nodes)
    assert set(again.relations) == set(graph.relations)
    assert again.nodes["ctr:shop/db"].status == "error"


def test_demo_machine_has_a_full_constellation() -> None:
    graph = _demo_graph()
    kinds = {n.id: n.kind for n in graph.nodes.values()}
    assert kinds["proc:Nginx:80"] == NodeKind.PROXY
    assert kinds["ctr:shop/db"] == NodeKind.DB
    assert kinds["ctr:shop/redis"] == NodeKind.CACHE
    assert kinds["tunnel:9090"] == NodeKind.TUNNEL
    assert kinds["k8s:demo-cluster"] == NodeKind.CLUSTER
    assert graph.nodes["k8s:demo-cluster/shop/api"].parent == "k8s:demo-cluster"
    assert ("proc:ASGI app (uvicorn):8001", "ctr:shop/db", "connects") in graph.relations
    assert ("proc:Nginx:80", "ctr:blog/wordpress", "proxies") in graph.relations
    assert ("tunnel:9090", "k8s:demo-cluster", "tunnels") in graph.relations
    assert graph.node_for_row("5432/TCP") is graph.nodes["ctr:shop/db"]


def test_text_listing_groups_by_project_and_shows_relations() -> None:
    text = render_topology_text(_demo_graph())
    assert text.startswith("blog\n")
    assert "shop\n" in text
    assert "  ◆ PostgreSQL  :5432  database  container  ● error" in text
    assert "      ← ASGI app (uvicorn) :8001 ×2" in text
    assert "      → CMS :8080 proxies blog.local" in text
    assert "external\n  ☁ demo-cluster  cluster" in text
    assert text.rstrip().endswith("relations")


def test_text_listing_for_an_empty_machine() -> None:
    graph = build_graph(Snapshot(), now=1.0)
    assert render_topology_text(graph).startswith("Nothing on the map yet")
    assert Node("x", NodeKind.DB, "x").status_rank == 0
    assert Relation("a", "b", EdgeKind.CONNECTS).key == ("a", "b", "connects")


def test_cli_topology_prints_text_and_json() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["topology", "--demo"])
    assert result.exit_code == 0, result.output
    assert "shop" in result.output and "PostgreSQL" in result.output
    result = runner.invoke(app, ["topology", "--demo", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert any(n["id"] == "ctr:shop/db" for n in data["nodes"])


def test_the_text_listing_marks_the_highlighted_node() -> None:
    graph = _demo_graph()

    text = render_topology_text(graph, highlight="ctr:shop/db")

    assert "▶ ◆ PostgreSQL  :5432" in text
    assert text.count("▶") == 1
    assert "  ⬢ Nginx" in text
    assert "▶" not in render_topology_text(graph)
    child = render_topology_text(graph, highlight="k8s:demo-cluster/shop/api")
    assert "    ▶ " in child
