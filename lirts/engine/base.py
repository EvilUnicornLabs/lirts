"""Engine construction, lifecycle and the state every refresh step reads.

:class:`EngineBase` owns the collectors and the stores.  The mixins in this
package (pipeline, enrichment, routes, health, actions) build on it and are
composed into :class:`lirts.engine.Engine`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil

from lirts.collectors.bandwidth import BandwidthSampler
from lirts.collectors.docker import DockerProvider
from lirts.collectors.health import HealthChecker
from lirts.collectors.hosts import HostsMap
from lirts.collectors.http import HttpProber
from lirts.collectors.kube import KubeProvider
from lirts.collectors.origin import OriginResolver
from lirts.collectors.ports import ProcessSampler
from lirts.collectors.proxies import Route
from lirts.collectors.system import SystemSampler
from lirts.config import state_dir
from lirts.config_view import ConfigView
from lirts.history import HistoryStore, save_last_snapshot
from lirts.models import ContainerInfo, Snapshot
from lirts.patterns import PatternStore
from lirts.ports import PortMemory
from lirts.topology_store import TopologyStore

log = logging.getLogger(__name__)


class EngineBase:
    """Owns the collectors and the stores that every refresh step reads."""

    def __init__(self, config: dict[str, Any], state_root: Path | None = None) -> None:
        self.config = config
        self.cfg = ConfigView(config)
        cfg = self.cfg
        self.state_root = state_root or state_dir()
        self.sampler = ProcessSampler()
        self.system = SystemSampler()
        self.sources: set[str] = set(cfg.sources)
        self.docker = DockerProvider(
            enabled=cfg.docker.enabled and "docker" in self.sources,
            inspect_interval=cfg.docker.inspect_interval,
            mask_patterns=list(cfg.mask_env_vars),
            stats=cfg.docker.stats,
        )
        self.prober = HttpProber(
            enabled=cfg.http_probe.enabled,
            interval=cfg.http_probe.interval,
            timeout=cfg.http_probe.timeout,
            skip_ports=cfg.http_probe.skip_ports,
            force_ports=cfg.http_probe.force_ports,
        )
        self.health = HealthChecker(
            timeout=cfg.health.timeout,
            paths=cfg.health.paths or None,
            overrides=cfg.health.overrides,
        )
        self.bandwidth = BandwidthSampler(
            mode=cfg.bandwidth.mode,
            interval=cfg.bandwidth.interval,
            connections=cfg.bandwidth.connections,
        )
        self._edge_history: dict[tuple[int, int], deque[float]] = {}
        self._row_totals: dict[str, tuple[float, float]] = {}
        self._last_refresh: float = 0.0
        self.hosts = HostsMap()
        self.origins = OriginResolver()
        self.kube = KubeProvider(
            enabled=cfg.kubernetes.enabled if "kubernetes" in self.sources else "off",
            context=cfg.kubernetes.context,
            namespace=cfg.kubernetes.namespace,
            all_namespaces=cfg.kubernetes.all_namespaces,
            interval=cfg.kubernetes.interval,
            timeout=cfg.kubernetes.timeout,
            state_root=self.state_root,
        )
        self.history = HistoryStore(
            path=self.state_root / "history.json",
            persist=cfg.history.persist,
            window=cfg.history.window,
            retention_hours=cfg.history.retention_hours,
        )
        self.proxies_enabled = cfg.proxies.enabled
        self.routes: list[Route] = []
        self.route_notes: list[str] = []
        self._routes_loaded = False
        self.patterns = PatternStore(
            path=self.state_root / "patterns.json",
            persist=cfg.history.persist and cfg.insights.pattern_days > 0,
            days=cfg.insights.pattern_days,
            min_count=cfg.insights.pattern_min,
        )
        self.patterns.load()
        if self.patterns.persist and not self.patterns.patterns and self.history.all_events():
            self.patterns.ingest(self.history.all_events(), listeners=[], now=time.time())
        self.topology = TopologyStore(
            path=self.state_root / "topology.json",
            persist=cfg.history.persist,
            days=cfg.topology.days,
            usual_days=cfg.topology.usual_days,
        )
        self.topology.load()
        # The orphan rule asks whether a parent process still exists; the demo answers itself.
        self.pid_alive: Callable[[int], bool] = psutil.pid_exists
        self.show_udp = cfg.show_udp
        self.hide_system = cfg.hide_system
        self.snapshot: Snapshot = Snapshot()
        self.containers: list[ContainerInfo] = []
        # What usually runs on each port; rebuilt from the stores on every refresh.
        self.port_memory = PortMemory()
        self.last_session_diff: dict[str, list[str]] | None = None
        self._lock = asyncio.Lock()
        self._started = False

    # ----- lifecycle ---------------------------------------------------------------

    def start(self) -> None:
        """Start the background collectors once."""
        if self._started:
            return
        self._started = True
        self.bandwidth.start()
        self.kube.start()

    def close(self) -> None:
        """Stop the background collectors and persist the state, best effort."""
        self.bandwidth.stop()
        self.kube.close()
        try:
            if self.cfg.history.persist and self.snapshot.listeners:
                save_last_snapshot(self.state_root, self.snapshot.listeners)
            self.history.save(force=True)
            self.patterns.save(force=True)
            self.topology.save(force=True)
        except OSError as exc:  # a full or read-only state directory must not break the exit
            log.debug("could not persist state: %s", exc)
        self.docker.close()

    # ----- sources -----------------------------------------------------------------

    def set_sources(self, names: list[str]) -> None:
        """Apply a new ``sources`` list at runtime (docker / kubernetes stay within their own flags)."""
        self.sources = set(names)
        self.docker.enabled = self.cfg.docker.enabled and "docker" in self.sources
        self.kube.mode = self.cfg.kubernetes.enabled if "kubernetes" in self.sources else "off"
        if self.kube.enabled:
            self.kube.start()

    def all_namespaces_active(self) -> bool:
        """True when the Kubernetes collector watches every namespace."""
        return self.cfg.kubernetes.all_namespaces
