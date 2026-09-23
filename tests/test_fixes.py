"""Smart fix: which insight proposes what, and the F key running it after a confirmation."""

from __future__ import annotations

import asyncio

from lirts.fixes import Fix, FixKind, fix_for, parse_action
from lirts.identity import ROLE_BACKEND
from lirts.models import ContainerInfo, Identity, Insight, Level
from lirts.tui.app import LirtsApp
from lirts.tui.screens import FixScreen
from tests.conftest import make_listener, make_process, wait_rows


def test_the_worst_insight_with_an_action_wins() -> None:
    lst = make_listener(port=8000, processes=[make_process(pid=42, name="node")])
    lst.insights = [
        Insight(Level.INFO, "latency", "slow"),
        Insight(Level.WARNING, "stale", "stale", "kill it", "kill:42"),
        Insight(Level.ERROR, "unreachable-tcp", "hung", "restart", "restart-process"),
    ]
    fix = fix_for(lst)
    assert fix is not None and fix.kind == FixKind.RESTART_PROCESS
    assert fix.command == "terminate PID 42 and run its command again in its directory"
    assert fix.title == "Fix for Unknown on 8000/TCP"


def test_actions_that_cannot_apply_are_skipped() -> None:
    lst = make_listener(port=8000, processes=[make_process(pid=42, name="node")])
    lst.insights = [Insight(Level.ERROR, "port-conflict", "conflict", None, "kill:999")]
    assert fix_for(lst) is None
    lst.insights = [Insight(Level.ERROR, "container-state", "exited", None, "restart-container")]
    assert fix_for(lst) is None  # not a container
    lst.insights = [Insight(Level.ERROR, "x", "y", None, "kill:not-a-pid")]
    assert fix_for(lst) is None
    assert parse_action("logs", lst) is None
    assert fix_for(make_listener(port=1)) is None


def test_container_fixes_describe_docker_commands() -> None:
    ctr = ContainerInfo(
        "abc123", "abc123", "shop-worker-1", "worker:1", stack="shop", service="worker"
    )
    lst = make_listener(port=9100, container=ctr, identity=Identity("worker", ROLE_BACKEND, 0.9))
    lst.insights = [Insight(Level.WARNING, "container-restarts", "restarted 7 times", None, "logs")]
    fix = fix_for(lst)
    assert fix is not None and fix.kind == FixKind.LOGS
    assert fix.command == "show the logs of shop-worker-1"
    lst.insights.append(
        Insight(Level.ERROR, "container-unhealthy", "unhealthy", None, "restart-container")
    )
    fix = fix_for(lst)
    assert fix is not None and fix.kind == FixKind.RESTART_CONTAINER
    assert fix.command == "docker restart shop-worker-1"
    tunnel = make_listener(port=9090, tunnel={"type": "kubectl"})
    tunnel.insights = [Insight(Level.WARNING, "dead-forward", "gone", None, "stop-forward")]
    assert (
        Fix(FixKind.STOP_FORWARD, tunnel, tunnel.insights[0]).command
        == "stop the port-forward on :9090"
    )


def test_demo_insights_carry_fixes() -> None:
    from lirts.config import DEFAULT_CONFIG, validate
    from lirts.demo import DemoEngine

    engine = DemoEngine(validate(dict(DEFAULT_CONFIG)))
    try:
        snap = engine.refresh_sync()
    finally:
        engine.close()
    conflict = snap.by_key("5432/TCP")
    assert conflict is not None
    fix = fix_for(conflict)
    assert fix is not None and fix.kind == FixKind.KILL and fix.pid in conflict.pids


async def test_f_confirms_then_runs_the_kill_and_notes_it(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)
        row = app.snapshot.by_key("8000/TCP")
        assert row is not None
        row.insights.append(
            Insight(Level.WARNING, "stale", "No activity", "kill it", f"kill:{row.pid}")
        )
        table.move_cursor(row=app._row_order.index("8000/TCP"))
        await pilot.pause()

        await pilot.press("F")
        await pilot.pause()
        assert isinstance(app.screen, FixScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert app.engine.killed == []  # type: ignore[attr-defined]  # FakeEngine test double

        await pilot.press("F")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        await asyncio.sleep(0.1)
        assert app.engine.killed == [([row.pid], False)]  # type: ignore[attr-defined]  # FakeEngine test double
        assert any("fix run by you (F)" in e.message for e in app.engine.history.events)


async def test_f_says_so_when_there_is_nothing_to_fix(app: LirtsApp) -> None:
    toasts: list[str] = []
    app.notify = lambda message, **kw: toasts.append(str(message))  # type: ignore[method-assign]  # test spy
    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)
        table.move_cursor(row=app._row_order.index("8000/TCP"))
        await pilot.pause()
        assert app.check_action("fix", ()) is False  # hidden from the footer, not dimmed
        await pilot.press("F")  # a dimmed binding does nothing
        await pilot.pause()
        assert not isinstance(app.screen, FixScreen)
        app.action_fix()  # the palette still reaches the action; it explains itself
        await pilot.pause()
        assert not isinstance(app.screen, FixScreen)
        assert any("No fix proposed" in t for t in toasts)


def test_a_left_over_container_is_fixed_by_stopping_it() -> None:
    ctr = ContainerInfo("abc123", "abc123", "blog-wordpress-1", "wordpress:6", stack="blog")
    lst = make_listener(port=8080, container=ctr, identity=Identity("wordpress", ROLE_BACKEND, 0.9))
    lst.insights = [
        Insight(
            Level.WARNING,
            "orphaned",
            "Left over: the only container of stack blog still running",
            "stop it (x) if blog is no longer in use",
            "stop-container",
        )
    ]

    fix = fix_for(lst)

    assert fix is not None and fix.kind == FixKind.STOP_CONTAINER
    assert fix.command == "docker stop blog-wordpress-1"
    assert parse_action("stop-container", make_listener(port=8080)) is None


async def test_the_fix_key_stops_the_container_of_a_left_over_row(app: LirtsApp) -> None:
    async with app.run_test(size=(160, 45)) as pilot:
        table = await wait_rows(app, pilot)
        row = app.snapshot.by_key("6379/TCP")
        assert row is not None and row.container is not None
        row.insights = [
            Insight(Level.WARNING, "orphaned", "Left over", "stop it (x)", "stop-container")
        ]
        table.move_cursor(row=app._row_order.index("6379/TCP"))
        await pilot.pause()

        await pilot.press("F")
        await pilot.pause()
        assert isinstance(app.screen, FixScreen)
        await pilot.press("y")
        await pilot.pause()
        await asyncio.sleep(0.1)

    assert any("orphaned: docker stop app-redis-1" in e.message for e in app.engine.history.events)
