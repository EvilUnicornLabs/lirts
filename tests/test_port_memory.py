"""Port memory: what lirts folds out of the star map and the history about a port.

The store is synthetic in every test: a hand-built graph and hand-built history entries,
never the real machine and never a real process table.
"""

from __future__ import annotations

import io
import time

import pytest
from rich.console import Console, RenderableType

from lirts.constants import SECONDS_PER_DAY
from lirts.history_entry import HistoryEntry
from lirts.models import Identity, ListenerProcess
from lirts.ports import (
    PortMemory,
    UsualHolder,
    holder_label,
    last_seen_text,
    listener_holder,
    parent_is_gone,
    split_label,
)
from lirts.topology import Graph, Node, NodeKind
from lirts.tui.widgets import Section, render_details
from tests.conftest import make_container, make_listener, make_process

NOW = 1_800_000_000.0


def render_text(renderable: RenderableType, width: int = 120) -> str:
    console = Console(width=width, file=io.StringIO(), record=True, legacy_windows=False)
    console.print(renderable)
    return console.export_text()


def days_before(*offsets: int) -> list[str]:
    """ISO day strings the star map would have recorded that many days ago."""
    return [time.strftime("%Y-%m-%d", time.localtime(NOW - n * SECONDS_PER_DAY)) for n in offsets]


def star(
    node_id: str,
    *,
    label: str,
    project: str | None,
    ports: list[int],
    days: list[str],
    last_seen: float = NOW,
    first_seen: float = NOW - 5 * SECONDS_PER_DAY,
) -> Node:
    return Node(
        id=node_id,
        kind=NodeKind.BACKEND,
        label=label,
        project=project,
        ports=ports,
        days_seen=days,
        first_seen=first_seen,
        last_seen=last_seen,
    )


def graph_of(*nodes: Node) -> Graph:
    graph = Graph(built_at=NOW)
    for node in nodes:
        graph.nodes[node.id] = node
    return graph


def entry_for(
    port: int, *, services: dict[str, int], first_seen: float = NOW - 600, last_seen: float = NOW
) -> HistoryEntry:
    entry = HistoryEntry(
        key=f"{port}/TCP", port=port, protocol="TCP", first_seen=first_seen, last_seen=last_seen
    )
    entry.services_seen = dict(services)
    return entry


# ----- what "usual" means ------------------------------------------------------------


def test_a_service_seen_on_several_days_is_the_usual_holder() -> None:
    graph = graph_of(
        star(
            "svc:shop/web", label="web", project="shop", ports=[3000], days=days_before(4, 3, 2, 1)
        )
    )

    memory = PortMemory.build(graph=graph, now=NOW)

    usual = memory.usual(3000)
    assert usual is not None
    assert usual.service == "web"
    assert usual.project == "shop"
    assert usual.days == 4
    assert usual.label == "shop/web"
    assert usual.seen_text == "4 days"


def test_one_day_is_usual_only_while_the_store_is_young_and_often_enough_seen() -> None:
    today = graph_of(
        star(
            "svc:shop/web",
            label="web",
            project="shop",
            ports=[3000],
            days=days_before(0),
            first_seen=NOW - 600,
        )
    )
    young = PortMemory.build(
        graph=today, entries=[entry_for(3000, services={"shop/web": 6})], now=NOW
    )
    assert young.young is True
    assert young.usual(3000) is not None

    too_few = PortMemory.build(
        graph=today, entries=[entry_for(3000, services={"shop/web": 5})], now=NOW
    )
    assert too_few.usual(3000) is None

    old_store = PortMemory.build(
        graph=today,
        entries=[entry_for(3000, services={"shop/web": 40}, first_seen=NOW - 3 * SECONDS_PER_DAY)],
        now=NOW,
    )
    assert old_store.young is False
    assert old_store.usual(3000) is None


def test_an_empty_memory_knows_nothing() -> None:
    memory = PortMemory.build(graph=Graph(), now=NOW)

    assert bool(memory) is False
    assert memory.usual(3000) is None
    assert memory.also_used_by(3000) == []
    assert memory.holders(3000) == []


def test_other_projects_on_the_same_port_are_listed_apart_from_the_usual_one() -> None:
    graph = graph_of(
        star("svc:shop/api", label="api", project="shop", ports=[8080], days=days_before(5, 4, 3)),
        star(
            "ctr:blog/wordpress",
            label="wordpress",
            project="blog",
            ports=[8080],
            days=days_before(2, 1),
            last_seen=NOW - SECONDS_PER_DAY,
        ),
        star("svc:shop/once", label="once", project="lab", ports=[8080], days=days_before(1)),
    )

    memory = PortMemory.build(graph=graph, now=NOW)

    usual = memory.usual(8080)
    assert usual is not None and usual.label == "shop/api"
    others = memory.also_used_by(8080)
    assert [h.label for h in others] == ["blog/wordpress"]  # "lab/once" was seen only once
    assert others[0].seen_text == "2 days"


def test_the_history_adds_refresh_counts_to_what_the_map_saw() -> None:
    graph = graph_of(
        star(
            "svc:shop/db", label="PostgreSQL", project="shop", ports=[5432], days=days_before(1, 0)
        )
    )

    memory = PortMemory.build(
        graph=graph, entries=[entry_for(5432, services={"shop/PostgreSQL": 12})], now=NOW
    )

    usual = memory.usual(5432)
    assert usual is not None
    assert usual.days == 2
    assert usual.refreshes == 12


def test_machines_clusters_and_tunnels_are_not_port_holders() -> None:
    machine = Node(id="machine:local", kind=NodeKind.MACHINE, label="this machine", ports=[3000])
    tunnel = Node(id="tunnel:15432", kind=NodeKind.TUNNEL, label="bastion", ports=[15432])

    memory = PortMemory.build(graph=graph_of(machine, tunnel), now=NOW)

    assert bool(memory) is False


def test_stack_size_counts_the_running_containers_of_a_project() -> None:
    memory = PortMemory.build(
        graph=Graph(),
        containers=[
            make_container(name="blog-wp-1", stack="blog"),
            make_container(name="shop-web-1", stack="shop"),
            make_container(name="shop-api-1", stack="shop"),
        ],
        now=NOW,
    )

    assert memory.stack_size("shop") == 2
    assert memory.stack_size("blog") == 1
    assert memory.stack_size("gone") == 0


# ----- the small helpers -------------------------------------------------------------


@pytest.mark.parametrize(
    ("service", "project", "expected"),
    [("web", "shop", "shop/web"), ("nginx", None, "nginx")],
    ids=["with-project", "without-project"],
)
def test_a_holder_label_round_trips(service: str, project: str | None, expected: str) -> None:
    label = holder_label(service, project)

    assert label == expected
    assert split_label(label) == (service, project)


def test_the_compose_stack_wins_over_the_identity_project() -> None:
    row = make_listener(
        port=8080,
        container=make_container(name="blog-wp-1", stack="blog"),
        identity=Identity("wordpress", "backend", 0.9, project="guessed"),
    )

    assert listener_holder(row) == ("wordpress", "blog")


@pytest.mark.parametrize(
    ("offset_days", "expected"),
    [(0, "today"), (1, "yesterday"), (4, "4 days ago")],
    ids=["today", "yesterday", "older"],
)
def test_last_seen_is_said_in_days(offset_days: int, expected: str) -> None:
    assert last_seen_text(NOW - offset_days * SECONDS_PER_DAY, NOW) == expected


def test_last_seen_of_something_never_seen() -> None:
    assert last_seen_text(0.0, NOW) == "unknown"


# ----- orphan detection against a fake process table ---------------------------------


def test_a_process_is_orphaned_when_its_parent_is_not_in_the_process_table() -> None:
    process_table = {400, 500}
    alive = process_table.__contains__

    assert parent_is_gone(ListenerProcess(pid=1, ppid=400), alive=alive) is False
    assert parent_is_gone(ListenerProcess(pid=2, ppid=999), alive=alive) is True
    assert parent_is_gone(ListenerProcess(pid=3, ppid=1), alive=alive) is True
    assert parent_is_gone(ListenerProcess(pid=4, ppid=0), alive=alive) is True
    assert parent_is_gone(ListenerProcess(pid=5, ppid=None), alive=alive) is False


# ----- what the history entry remembers ----------------------------------------------


def test_an_entry_remembers_at_most_eight_service_names() -> None:
    entry = entry_for(3000, services={})
    for n in range(12):
        entry.note_service(f"proj{n}/service")
    entry.note_service("shop/web")
    entry.note_service("shop/web")

    assert len(entry.services_seen) == 8
    assert entry.services_seen["shop/web"] == 2
    assert HistoryEntry.from_dict(entry.to_dict(), 120).services_seen == entry.services_seen


# ----- the side panel line -----------------------------------------------------------


def test_the_side_panel_names_the_usual_holder() -> None:
    row = make_listener(port=3000, processes=[make_process(pid=11, name="node")])
    memory = PortMemory.build(
        graph=graph_of(
            star(
                "svc:shop/web",
                label="web",
                project="shop",
                ports=[3000],
                days=days_before(4, 3, 2, 1, 0),
            )
        ),
        now=time.time(),
    )

    out = render_text(render_details(row, sections=(Section.RUNTIME,), memory=memory))

    assert "Usually" in out
    assert "shop/web · 5 days · last today" in out


def test_the_side_panel_says_when_a_port_is_new() -> None:
    row = make_listener(port=4321, processes=[make_process(pid=11, name="node")])
    memory = PortMemory.build(
        graph=graph_of(
            star("svc:shop/web", label="web", project="shop", ports=[3000], days=days_before(1, 0))
        ),
        now=time.time(),
    )

    out = render_text(render_details(row, sections=(Section.RUNTIME,), memory=memory))

    assert "this is the first time" in out


def test_the_side_panel_says_nothing_when_nothing_is_remembered() -> None:
    row = make_listener(port=4321, processes=[make_process(pid=11, name="node")])

    out = render_text(render_details(row, sections=(Section.RUNTIME,), memory=PortMemory()))

    assert "Usually" not in out
    assert "this is the first time" not in out


def test_holders_are_sorted_with_the_best_known_first() -> None:
    memory = PortMemory(
        [
            UsualHolder(3000, "once", "lab", days=1),
            UsualHolder(3000, "web", "shop", days=6, refreshes=90),
            UsualHolder(3000, "old", "blog", days=3),
        ]
    )

    assert [h.service for h in memory.holders(3000)] == ["web", "old", "once"]
