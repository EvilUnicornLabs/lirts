"""Health checks on demand: lirts never probes by itself, only when asked."""

from __future__ import annotations

import time

from lirts.engine.base import EngineBase
from lirts.insights import analyse
from lirts.models import Listener


class HealthMixin(EngineBase):
    """The only place a health check is started: lirts never probes by itself."""

    async def check_health_now(self, listeners: list[Listener] | None = None) -> list[Listener]:
        """Health check of the given (or all current) listeners.  The only way checks run."""
        targets = listeners if listeners is not None else list(self.snapshot.listeners)
        await self.health.check(targets)
        now = time.time()
        for lst in targets:
            analyse(lst, cfg=self.cfg, now=now, memory=self.port_memory, alive=self.pid_alive)
        return targets
