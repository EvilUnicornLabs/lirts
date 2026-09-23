"""Value objects for the Kubernetes collector: pods, deployments, services, forwards, state."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import psutil


@dataclass
class KubePod:
    name: str
    namespace: str
    phase: str
    ready: int
    total: int
    restarts: int
    created: float | None
    node: str | None
    owner_kind: str | None
    owner: str | None
    images: list[str] = field(default_factory=list)
    ports: list[int] = field(default_factory=list)
    reason: str | None = None  # CrashLoopBackOff, ImagePullBackOff, ...
    ip: str | None = None

    @property
    def healthy(self) -> bool:
        return (
            self.phase in ("Running", "Succeeded") and self.ready == self.total and not self.reason
        )

    @property
    def age(self) -> float | None:
        return None if self.created is None else max(0.0, time.time() - self.created)


@dataclass
class KubeDeployment:
    name: str
    namespace: str
    desired: int
    ready: int
    updated: int
    available: int
    created: float | None


@dataclass
class KubeService:
    name: str
    namespace: str
    type: str
    cluster_ip: str | None
    ports: list[tuple[int, str, str]] = field(default_factory=list)  # (port, targetPort, protocol)


@dataclass
class KubeForward:
    """A ``kubectl port-forward`` started (and tracked) by lirts."""

    pid: int
    context: str | None
    namespace: str | None
    target: str
    local_port: int
    remote_port: int
    started: float

    @property
    def alive(self) -> bool:
        return psutil.pid_exists(self.pid)


@dataclass
class KubeState:
    available: bool = False
    context: str | None = None
    namespace: str | None = None
    server: str | None = None
    pods: list[KubePod] = field(default_factory=list)
    deployments: list[KubeDeployment] = field(default_factory=list)
    services: list[KubeService] = field(default_factory=list)
    error: str | None = None
    fetched_at: float = 0.0

    @property
    def unhealthy_pods(self) -> list[KubePod]:
        return [p for p in self.pods if not p.healthy]
