"""The port-memory insight rules: squatter, left over, and a port two projects want.

Every rule is checked on a hand-built listener and a hand-built memory; the orphan rule
gets a fake process table, never the real one.
"""

from __future__ import annotations

import time

from lirts.collectors.origin import Origin
from lirts.config import DEFAULT_CONFIG
from lirts.config_view import ConfigView
from lirts.identity import ROLE_BACKEND, ROLE_FRONTEND
from lirts.insights import analyse
from lirts.insights_ports import port_memory_insights
from lirts.models import Identity
from lirts.ports import PortMemory, UsualHolder
from tests.conftest import make_container, make_listener, make_process

CFG = ConfigView(DEFAULT_CONFIG)


def codes(lst):
    return {i.code: i for i in lst.insights}


def memory_of(*holders: UsualHolder, stacks: dict[str, int] | None = None) -> PortMemory:
    """A port memory built straight from holders, so the rules are tested on their own."""
    return PortMemory(holders, young=False, stacks=stacks)


def test_a_different_project_on_the_usual_port_is_a_squatter() -> None:
    row = make_listener(
        port=3000,
        processes=[make_process(pid=77, name="node", age=600)],
        identity=Identity("node", ROLE_FRONTEND, 0.9, project="blog"),
    )

    analyse(
        row,
        cfg=CFG,
        now=time.time(),
        memory=memory_of(UsualHolder(3000, "web", "shop", days=5, last_seen=time.time())),
    )

    squatter = codes(row)["squatter"]
    assert squatter.level == "warning"
    assert squatter.message.startswith("3000 is usually shop/web; now node (blog) since ")
    assert squatter.suggestion == "check whether shop/web failed to start"
    assert squatter.action == "kill:77"


def test_the_usual_holder_itself_is_not_a_squatter() -> None:
    row = make_listener(
        port=3000,
        processes=[make_process(pid=77, name="node")],
        identity=Identity("web", ROLE_FRONTEND, 0.9, project="shop"),
    )

    analyse(
        row, cfg=CFG, now=time.time(), memory=memory_of(UsualHolder(3000, "web", "shop", days=5))
    )

    assert "squatter" not in codes(row)


def test_a_container_squatter_is_offered_a_docker_stop() -> None:
    row = make_listener(
        port=8080,
        processes=[make_process(pid=9, name="com.docker.backend")],
        container=make_container(name="blog-wp-1", stack="blog", host_ports=[8080]),
        identity=Identity("wordpress", ROLE_BACKEND, 0.9),
    )

    analyse(
        row,
        cfg=CFG,
        now=time.time(),
        memory=memory_of(UsualHolder(8080, "api", "shop", days=4), stacks={"blog": 3}),
    )

    assert codes(row)["squatter"].action == "stop-container"


def test_a_process_whose_parent_is_gone_is_left_over() -> None:
    row = make_listener(
        port=5173,
        processes=[make_process(pid=42, name="node", ppid=1)],
        identity=Identity("Vite dev server", ROLE_FRONTEND, 0.95, project="shop"),
    )

    analyse(row, cfg=CFG, now=time.time(), memory=memory_of())

    orphan = codes(row)["orphaned"]
    assert orphan.level == "warning"
    assert orphan.message == "Left over: parent gone (node, PID 42)"
    assert orphan.action == "kill:42"


def test_a_supervised_process_and_a_live_parent_are_not_left_over() -> None:
    supervised = make_listener(
        port=5432,
        processes=[
            make_process(pid=42, name="postgres", ppid=1, origin=Origin(supervisor="launchd"))
        ],
        identity=Identity("PostgreSQL", ROLE_BACKEND, 0.95),
    )
    started_from_a_shell = make_listener(
        port=5173,
        processes=[make_process(pid=43, name="node", ppid=911)],
        identity=Identity("Vite dev server", ROLE_FRONTEND, 0.95),
    )

    analyse(supervised, cfg=CFG, now=time.time(), memory=memory_of())
    port_memory_insights(
        started_from_a_shell, memory=memory_of(), now=time.time(), alive={911}.__contains__
    )

    assert "orphaned" not in codes(supervised)
    assert "orphaned" not in codes(started_from_a_shell)


def test_the_last_container_of_a_stack_is_left_over() -> None:
    row = make_listener(
        port=8080,
        processes=[make_process(pid=9, name="com.docker.backend")],
        container=make_container(name="blog-wp-1", stack="blog", host_ports=[8080]),
        identity=Identity("wordpress", ROLE_BACKEND, 0.9),
    )

    analyse(row, cfg=CFG, now=time.time(), memory=memory_of(stacks={"blog": 1}))

    orphan = codes(row)["orphaned"]
    assert orphan.message == "Left over: the only container of stack blog still running"
    assert orphan.action == "stop-container"
    assert orphan.suggestion == "stop it (x) if blog is no longer in use"


def test_a_container_of_a_running_stack_is_not_left_over() -> None:
    row = make_listener(
        port=8080,
        processes=[make_process(pid=9, name="com.docker.backend")],
        container=make_container(name="shop-web-1", stack="shop", host_ports=[8080]),
        identity=Identity("web", ROLE_FRONTEND, 0.9),
    )

    analyse(row, cfg=CFG, now=time.time(), memory=memory_of(stacks={"shop": 4}))

    assert "orphaned" not in codes(row)


def test_another_project_that_usually_wants_the_same_port_is_mentioned() -> None:
    row = make_listener(
        port=8080,
        processes=[make_process(pid=9, name="com.docker.backend")],
        container=make_container(name="shop-api-1", stack="shop", host_ports=[8080]),
        identity=Identity("api", ROLE_BACKEND, 0.9),
    )

    analyse(
        row,
        cfg=CFG,
        now=time.time(),
        memory=memory_of(
            UsualHolder(8080, "api", "shop", days=6),
            UsualHolder(8080, "wordpress", "blog", days=3),
            stacks={"shop": 4},
        ),
    )

    shared = codes(row)["port-shared-projects"]
    assert shared.level == "info"
    assert shared.message == "8080 is also used by blog/wordpress (seen 3 days)"


def test_a_stopped_row_and_an_empty_memory_say_nothing_about_the_port() -> None:
    ghost = make_listener(port=3000, processes=[], state="STOPPED")
    ghost.identity = Identity("web", ROLE_FRONTEND, 0.9, project="shop")

    analyse(
        ghost, cfg=CFG, now=time.time(), memory=memory_of(UsualHolder(3000, "api", "blog", days=9))
    )

    assert "squatter" not in codes(ghost)
