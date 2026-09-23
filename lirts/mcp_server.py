"""``lirts mcp``: the engine as a Model Context Protocol server over stdio.

A coding agent (Claude Code, Cursor, anything that speaks MCP) starts ``lirts mcp`` and asks
it what is on a port, what usually runs there, who talks to whom and what the machine looks
like, instead of guessing from ``lsof``.  The server holds one engine open and refreshes it
at ``refresh_interval`` while the client keeps it running, exactly like an open dashboard:
history, port memory and the star map keep learning, and everything stops with the client.

The tools are read-only.  ``free_port``, ``fix`` and ``restart`` are registered only when the
server was started with ``--allow-actions``, so an agent cannot stop anything unless the user
chose that when wiring it up.  The ``mcp`` SDK is an optional extra (``lirts[mcp]``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from importlib import import_module
from importlib.util import find_spec
from typing import Any

from lirts import __version__
from lirts.engine import Engine
from lirts.mcp_tools import (
    doctor_payload,
    events_payload,
    explain_text,
    fix_payload,
    free_port_payload,
    graph_text,
    health_payload,
    kube_payload,
    listeners_payload,
    patterns_payload,
    restart_payload,
    routes_payload,
    stacks_payload,
    topology_payload,
    who_payload,
)

log = logging.getLogger(__name__)

SERVER_NAME = "lirts"
# Events a client gets by default when it does not say how many.
DEFAULT_EVENT_LIMIT = 20
INSTRUCTIONS = (
    "lirts observes what runs on this machine: ports, processes, containers, compose stacks, "
    "Kubernetes, ssh sessions, proxies, traffic, health and history. Ask `who` before starting "
    "a server on a port, `list_listeners` for the table, `explain` for the machine in words, "
    "`topology` for what talks to what. Health checks run only when `health` is called."
)


def mcp_available() -> bool:
    """Whether the optional ``mcp`` SDK is installed (``pip install 'lirts[mcp]'``)."""
    return find_spec("mcp") is not None


class ServerState:
    """The open engine and the refresh loop behind one server run."""

    def __init__(self, engine: Engine, *, refresh_interval: float) -> None:
        self.engine = engine
        self.refresh_interval = refresh_interval
        self.refreshes = 0
        self.ready = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.engine.refresh()
                self.refreshes += 1
            except Exception:  # one bad cycle must not stop the server
                log.exception("refresh failed")
            finally:
                self.ready.set()
            await asyncio.sleep(self.refresh_interval)

    def start(self) -> None:
        """Start the collectors and the refresh loop, like an opening dashboard."""
        self.engine.start()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Stop the loop and close the engine; the state files are persisted here."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.engine.close()

    async def wait_ready(self) -> Engine:
        """The engine once the first refresh has been attempted."""
        await self.ready.wait()
        return self.engine


def build_server(engine: Engine, *, refresh_interval: float, allow_actions: bool = False) -> Any:
    """The MCP server around an engine; returns an ``mcp.server.mcpserver.MCPServer``."""
    mcpserver = import_module("mcp.server.mcpserver")
    state = ServerState(engine, refresh_interval=refresh_interval)

    @contextlib.asynccontextmanager
    async def lifespan(_server: Any) -> AsyncIterator[ServerState]:
        state.start()
        try:
            yield state
        finally:
            await state.stop()

    # The SDK echoes the root logger to stderr; lirts's own log already goes to the state
    # directory, so only warnings are worth a line in the client's log.
    server = mcpserver.MCPServer(
        SERVER_NAME,
        version=__version__,
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
        log_level="WARNING",
    )
    _register_read_tools(server, state)
    if allow_actions:
        _register_action_tools(server, state)
    server.lirts_state = state
    return server


def _register_read_tools(server: Any, state: ServerState) -> None:
    @server.tool(
        description="The current listeners: every port with its process, container, "
        "identity (service, role, project), activity, status and insights. `filter` "
        "takes the dashboard filter syntax (words, key:value, -term); `port` narrows to one port."
    )
    async def list_listeners(filter: str = "", port: int | None = None) -> dict[str, Any]:
        engine = await state.wait_ready()
        return listeners_payload(engine, filter_text=filter, port=port)

    @server.tool(
        description="Who holds a port, since when, from which project, whether it looks "
        "left over, what usually runs there and which other projects use it. Ask this "
        "before starting a server on a port."
    )
    async def who(port: int, protocol: str = "TCP") -> dict[str, Any]:
        engine = await state.wait_ready()
        return who_payload(engine, port, protocol)

    @server.tool(
        description="The machine in words: stacks, network, warnings, recommendations, "
        "changes since the last session, Kubernetes, patterns and events."
    )
    async def explain() -> str:
        return explain_text(await state.wait_ready())

    @server.tool(
        description="Who talks to whom: client processes per port, proxy chains, proxy "
        "routes, hosts-file names, tunnels and ssh sessions, as text."
    )
    async def graph() -> str:
        return graph_text(await state.wait_ready())

    @server.tool(
        description="The learned star map: services, containers, databases, proxies, "
        "tunnels, hosts and clusters as nodes, observed relations as edges, with "
        "first / last seen and whether they run now."
    )
    async def topology() -> dict[str, Any]:
        return topology_payload(await state.wait_ready())

    @server.tool(description="The newest start / stop / restart / health events.")
    async def events(limit: int = DEFAULT_EVENT_LIMIT) -> list[dict[str, Any]]:
        return events_payload(await state.wait_ready(), limit)

    @server.tool(
        description="Recurring issues remembered across runs: conflicts, restarts, kills per port."
    )
    async def patterns() -> list[dict[str, Any]]:
        return patterns_payload(await state.wait_ready())

    @server.tool(description="Virtual hosts of local nginx / httpd and where they send requests.")
    async def routes() -> list[dict[str, Any]]:
        return routes_payload(await state.wait_ready())

    @server.tool(description="Docker compose projects and their containers.")
    async def stacks() -> dict[str, list[dict[str, Any]]]:
        return stacks_payload(await state.wait_ready())

    @server.tool(
        description="The Kubernetes cluster as kubectl last reported it: pods, "
        "deployments, services; or why it is unavailable."
    )
    async def kube() -> dict[str, Any]:
        return kube_payload(await state.wait_ready())

    @server.tool(
        description="Check health now (TCP connect, health endpoint, Docker health) "
        "of one port or of every listening row. lirts never checks by itself; this "
        "call is the check."
    )
    async def health(port: int | None = None) -> list[dict[str, Any]]:
        return await health_payload(await state.wait_ready(), port)

    @server.tool(
        description="The environment checks of `lirts doctor`: Python, config, state, "
        "Docker, kubectl, bandwidth sampling, notifications, terminal, editor, MCP."
    )
    async def doctor() -> list[dict[str, Any]]:
        return doctor_payload()


def _register_action_tools(server: Any, state: ServerState) -> None:
    @server.tool(
        description="Hand a port back: terminate the processes holding it and stop "
        "its container. Irreversible; refused in replay."
    )
    async def free_port(port: int, protocol: str = "TCP", force: bool = False) -> dict[str, Any]:
        engine = await state.wait_ready()
        return free_port_payload(engine, port, protocol=protocol, force=force)

    @server.tool(
        description="Run the fix the row's worst insight proposes (what `F` does in "
        "the dashboard): kill the newer process of a conflict, a stale or left-over "
        "server, restart or stop a container, restart a hung process, or return the "
        "logs of a crash-looping container. Refused in replay."
    )
    async def fix(port: int, protocol: str = "TCP") -> dict[str, Any]:
        engine = await state.wait_ready()
        return fix_payload(engine, port, protocol=protocol)

    @server.tool(
        description="Restart what is on a port: a container through Docker, a local "
        "process by re-running its command in its directory. Refused in replay."
    )
    async def restart(port: int, protocol: str = "TCP") -> dict[str, Any]:
        engine = await state.wait_ready()
        return restart_payload(engine, port, protocol=protocol)


async def serve_stdio(server: Any) -> None:
    """Run the server over stdin / stdout until the client closes the pipe."""
    await server.run_stdio_async()
