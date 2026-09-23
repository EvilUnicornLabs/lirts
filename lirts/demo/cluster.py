"""The Kubernetes state and the proxy routes the demo machine is seeded with.

Pure data, like :mod:`lirts.demo.catalog`: pods, deployments, services, the
kubectl port-forward and the nginx virtual hosts.
"""

from __future__ import annotations

import os

from lirts.collectors.kube import KubeDeployment, KubeForward, KubePod, KubeService
from lirts.collectors.proxies import ProxyKind, Route


def demo_pods(t0: float) -> list[KubePod]:
    return [
        KubePod(
            "api-7d9f8b6c-x2k9p",
            "shop",
            "Running",
            1,
            1,
            0,
            t0 - 86400 * 4,
            "node-a",
            "Deployment",
            "api",
            ["shop/api:1.4.2"],
            [8000],
            ip="10.42.0.12",
        ),
        KubePod(
            "api-7d9f8b6c-qm4tz",
            "shop",
            "Running",
            1,
            1,
            0,
            t0 - 86400 * 4,
            "node-b",
            "Deployment",
            "api",
            ["shop/api:1.4.2"],
            [8000],
            ip="10.42.1.7",
        ),
        KubePod(
            "worker-5c9d7-l2vhs",
            "shop",
            "Running",
            0,
            1,
            7,
            t0 - 3600 * 3,
            "node-a",
            "Deployment",
            "worker",
            ["shop/worker:1.4.2"],
            [],
            reason="CrashLoopBackOff",
            ip="10.42.0.31",
        ),
        KubePod(
            "postgres-0",
            "shop",
            "Running",
            1,
            1,
            0,
            t0 - 86400 * 20,
            "node-b",
            "StatefulSet",
            "postgres",
            ["postgres:16"],
            [5432],
            ip="10.42.1.3",
        ),
        KubePod(
            "prometheus-0",
            "monitoring",
            "Running",
            2,
            2,
            0,
            t0 - 86400 * 20,
            "node-a",
            "StatefulSet",
            "prometheus",
            ["prom/prometheus:v2.53"],
            [9090],
            ip="10.42.0.5",
        ),
    ]


def demo_deployments(t0: float) -> list[KubeDeployment]:
    return [
        KubeDeployment("api", "shop", 2, 2, 2, 2, t0 - 86400 * 4),
        KubeDeployment("worker", "shop", 1, 0, 1, 0, t0 - 3600 * 3),
    ]


def demo_services() -> list[KubeService]:
    return [
        KubeService("api", "shop", "ClusterIP", "10.43.0.10", [(80, "8000", "TCP")]),
        KubeService("postgres", "shop", "ClusterIP", "10.43.0.11", [(5432, "5432", "TCP")]),
        KubeService("prometheus", "monitoring", "ClusterIP", "10.43.0.20", [(9090, "9090", "TCP")]),
    ]


def demo_forwards(t0: float) -> list[KubeForward]:
    """The kubectl port-forward that the local kubectl process on 9090 stands for."""
    return [
        KubeForward(
            os.getpid(), "demo-cluster", "monitoring", "svc/prometheus", 9090, 9090, t0 - 900
        )
    ]


def demo_routes() -> list[Route]:
    return [
        Route(ProxyKind.NGINX, 80, ["shop.local"], "127.0.0.1:5173", 5173),
        Route(ProxyKind.NGINX, 80, ["api.shop.local"], "127.0.0.1:8001", 8001, path="/api/"),
        Route(ProxyKind.NGINX, 80, ["blog.local"], "127.0.0.1:8080", 8080),
    ]
