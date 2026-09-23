"""Demo mode: a synthetic machine for developers of lirts.

``lirts --demo`` runs the dashboard against this engine instead of the real
collectors.  Nothing on the host is read or touched: no sockets, no Docker,
no kubectl, no commands, no state files.  The world below has a bit of
everything lirts can show (every role, compose stacks, a crash-looping
container, an unhealthy healthcheck, a port conflict, ssh sessions and a
tunnel, a kubectl port-forward, proxy routes, hosts names, client edges,
traffic) and a scripted timeline so events happen while you watch.
"""

from __future__ import annotations

from lirts.demo.engine import DemoEngine, demo_config
from lirts.demo.world import DemoWorld

__all__ = ["DemoEngine", "DemoWorld", "demo_config"]
