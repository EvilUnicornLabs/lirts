"""Providers standing in for the real collectors while the demo runs.

Each class has the same surface as the collector it replaces, so the engine
talks to them without knowing anything is pretend.
"""

from __future__ import annotations

import os
import time
from typing import Any

from lirts.collectors.kube import KubeForward, KubeState
from lirts.collectors.ports import ProcessSampler, _ProcSnapshot
from lirts.constants import LOG_TAIL_POD
from lirts.demo.world import DemoWorld
from lirts.models import ContainerInfo


class DemoSampler(ProcessSampler):
    """Stands in for :class:`lirts.collectors.ports.ProcessSampler`."""

    def __init__(self, world: DemoWorld) -> None:
        super().__init__()
        self.world = world

    def describe(self, pid: int) -> _ProcSnapshot:
        return self.world.snapshot_proc(pid)


class DemoDocker:
    """Stands in for :class:`lirts.collectors.docker.DockerProvider`."""

    def __init__(self, world: DemoWorld) -> None:
        self.world = world
        self.enabled = True

    @property
    def available(self) -> bool:
        return True

    def list_containers(self) -> list[ContainerInfo]:
        return list(self.world.containers)

    def stop(self, container_id: str) -> tuple[bool, str]:
        return self.world.stop_container(container_id)

    def restart(self, container_id: str) -> tuple[bool, str]:
        return self.world.restart_container(container_id)

    def logs(self, container_id: str, tail: int = 200) -> str:
        c = next((x for x in self.world.containers if x.id == container_id), None)
        name = c.name if c else container_id[:12]
        t = time.strftime("%Y-%m-%dT%H:%M:%S")
        lines = [f"{t} [{name}] demo log line {i + 1}" for i in range(min(tail, 40))]
        if c and c.service == "worker":
            lines.append(
                f"{t} [{name}] Traceback (most recent call last): RuntimeError: queue unavailable"
            )
            lines.append(f"{t} [{name}] exited with code 1; restarting")
        return "\n".join(lines)

    @staticmethod
    def stack_containers(containers: list[ContainerInfo], project: str) -> list[ContainerInfo]:
        return [c for c in containers if (c.stack or "-") == project]

    def stack_action(
        self, containers: list[ContainerInfo], *, project: str, action: str
    ) -> list[tuple[str, bool, str]]:
        fn = self.stop if action == "stop" else self.restart
        return [(c.name, *fn(c.id)) for c in self.stack_containers(containers, project)]

    def stack_logs(self, containers: list[ContainerInfo], *, project: str, tail: int = 100) -> str:
        chunks: list[str] = []
        for c in self.stack_containers(containers, project):
            chunks.extend(
                f"{(c.service or c.name):<14} | {line}"
                for line in self.logs(c.id, tail).splitlines()
            )
        return "\n".join(chunks) or f"(no running containers in project {project})"

    def exec_command(self, container_id: str) -> list[str]:
        return ["sh", "-c", "echo 'demo mode: no real container to enter'; sleep 1"]

    def sample_stats(self, containers: list[ContainerInfo]) -> None:
        return None

    def close(self) -> None:
        return None


class DemoKube:
    """Stands in for :class:`lirts.collectors.kube.KubeProvider`."""

    def __init__(self, world: DemoWorld) -> None:
        self.world = world
        self.kubectl = "kubectl (demo)"
        self.mode: Any = True
        self.context: str | None = "demo-cluster"
        self.namespace: str | None = "shop"
        self.all_namespaces = False

    @property
    def enabled(self) -> bool:
        return self.mode not in (False, "off", "false")

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def close(self) -> None:
        return None

    def refresh_soon(self) -> None:
        return None

    def current_context(self) -> str | None:
        return self.context

    def current_namespace(self) -> str | None:
        return self.namespace

    def contexts(self) -> list[tuple[str, bool]]:
        return [("demo-cluster", True), ("staging", False)]

    def api_server(self) -> tuple[str, int] | None:
        return ("k8s.demo.local", 6443)

    def namespaces(self) -> list[str]:
        return ["default", "kube-system", "monitoring", "shop"]

    def fetch(self) -> KubeState:
        return self.snapshot()

    def snapshot(self) -> KubeState:
        state = self.world.kube
        state.namespace = None if self.all_namespaces else self.namespace
        pods = [
            p
            for p in state.pods
            if self.all_namespaces or p.namespace == (self.namespace or "shop")
        ]
        return KubeState(
            available=True,
            context=self.context,
            namespace=state.namespace,
            server="https://k8s.demo.local:6443",
            pods=pods,
            deployments=[
                d
                for d in state.deployments
                if self.all_namespaces or d.namespace == (self.namespace or "shop")
            ],
            services=[
                s
                for s in state.services
                if self.all_namespaces or s.namespace == (self.namespace or "shop")
            ],
            fetched_at=state.fetched_at,
        )

    def logs(
        self,
        namespace: str,
        *,
        pod: str,
        tail: int = LOG_TAIL_POD,
        previous: bool = False,
        container: str | None = None,
    ) -> str:
        return "\n".join(
            f"{pod} {'(previous) ' if previous else ''}demo log line {i + 1}"
            for i in range(min(tail, 30))
        )

    def exec_command(self, namespace: str, *, pod: str, container: str | None = None) -> list[str]:
        return ["sh", "-c", f"echo 'demo mode: no shell in {pod}'; sleep 1"]

    def rollout_restart(self, namespace: str, *, kind: str, name: str) -> tuple[bool, str]:
        for p in self.world.kube.pods:
            if p.owner == name:
                p.created = self.world.now
                p.restarts = 0
                p.reason = None
                p.ready = p.total
        return True, f"{kind}/{name} restarted (demo)"

    def delete_pod(self, namespace: str, pod: str) -> tuple[bool, str]:
        self.world.kube.pods = [p for p in self.world.kube.pods if p.name != pod]
        return True, f"pod {pod} deleted (demo)"

    def forwards(self) -> list[KubeForward]:
        return list(self.world.kube_forwards)

    def start_forward(
        self, namespace: str, *, target: str, local_port: int, remote_port: int
    ) -> tuple[bool, str]:
        self.world.kube_forwards.append(
            KubeForward(
                os.getpid(),
                self.context,
                namespace,
                target,
                local_port,
                remote_port,
                self.world.now,
            )
        )
        return True, f"forwarding {local_port} → {target}:{remote_port} (demo)"

    def stop_forward(
        self, local_port: int | None = None, pid: int | None = None
    ) -> list[tuple[int, bool, str]]:
        out = []
        for f in list(self.world.kube_forwards):
            if local_port in (None, f.local_port):
                self.world.kube_forwards.remove(f)
                out.append((f.local_port, True, "stopped (demo)"))
        return out


class DemoBandwidth:
    """Stands in for :class:`lirts.collectors.bandwidth.BandwidthSampler`."""

    def __init__(self, world: DemoWorld, *, connections: bool) -> None:
        self.world = world
        self.connections = connections
        self.enabled = True

    @property
    def available(self) -> bool:
        return True

    @property
    def flows_supported(self) -> bool:
        return True

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def rates(self) -> dict[int, tuple[float, float]]:
        return self.world.rates()

    def flows(self) -> dict[tuple[int, int, int], tuple[float, float, int, int]]:
        return self.world.flows() if self.connections else {}


class DemoHosts:
    """Stands in for :class:`lirts.collectors.hosts.HostsMap`."""

    def __init__(self, world: DemoWorld) -> None:
        self.world = world
        self.mapping = {"127.0.0.1": ["localhost", *world.hosts_names]}

    def refresh(self) -> None:
        return None

    def loopback_names(self) -> list[str]:
        return list(self.world.hosts_names)
