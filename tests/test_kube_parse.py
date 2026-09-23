from __future__ import annotations

import pytest

from lirts.collectors.kube import (
    KubeState,
    owner_of,
    parse_deployments,
    parse_pods,
    parse_port_forward_cmdline,
    parse_services,
    parse_ssh_tunnel_cmdline,
    parse_timestamp,
)
from lirts.identity import identify
from tests.conftest import make_listener, make_process

PODS = {
    "items": [
        {
            "metadata": {
                "name": "api-7d9f-abc12",
                "namespace": "dev",
                "creationTimestamp": "2026-09-10T10:00:00Z",
                "ownerReferences": [{"kind": "ReplicaSet", "name": "api-7d9f"}],
            },
            "spec": {
                "nodeName": "node1",
                "containers": [
                    {"name": "api", "image": "acme/api:1", "ports": [{"containerPort": 3000}]}
                ],
            },
            "status": {
                "phase": "Running",
                "podIP": "10.0.0.5",
                "containerStatuses": [{"ready": True, "restartCount": 2, "state": {"running": {}}}],
            },
        },
        {
            "metadata": {
                "name": "worker-0",
                "namespace": "dev",
                "creationTimestamp": "2026-09-19T10:00:00Z",
                "ownerReferences": [{"kind": "StatefulSet", "name": "worker"}],
            },
            "spec": {
                "containers": [
                    {"name": "w", "image": "acme/worker:1"},
                    {"name": "sidecar", "image": "envoy"},
                ]
            },
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {"ready": True, "restartCount": 0, "state": {"running": {}}},
                    {
                        "ready": False,
                        "restartCount": 7,
                        "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                    },
                ],
            },
        },
        {
            "metadata": {"name": "job-x", "namespace": "dev"},
            "spec": {"containers": [{"name": "j", "image": "busybox"}]},
            "status": {"phase": "Pending", "containerStatuses": []},
        },
    ]
}
DEPLOYMENTS = {
    "items": [
        {
            "metadata": {"name": "api", "namespace": "dev"},
            "spec": {"replicas": 2},
            "status": {"readyReplicas": 1, "updatedReplicas": 2, "availableReplicas": 1},
        }
    ]
}
SERVICES = {
    "items": [
        {
            "metadata": {"name": "postgres", "namespace": "dev"},
            "spec": {
                "type": "ClusterIP",
                "clusterIP": "10.43.0.9",
                "ports": [{"port": 5432, "targetPort": 5432, "protocol": "TCP"}],
            },
        }
    ]
}


def test_parsers() -> None:
    pods = parse_pods(PODS)
    api, job, worker = pods  # sorted by name
    assert api.owner_kind == "Deployment" and api.owner == "api"
    assert api.ready == 1 and api.total == 1 and api.restarts == 2 and api.healthy
    assert api.ports == [3000] and api.node == "node1" and api.age is not None
    assert worker.owner_kind == "StatefulSet" and worker.reason == "CrashLoopBackOff"
    assert worker.ready == 1 and worker.total == 2 and worker.restarts == 7 and not worker.healthy
    assert (
        job.phase == "Pending" and job.reason == "Pending" and not job.healthy and job.age is None
    )
    dep = parse_deployments(DEPLOYMENTS)[0]
    assert (dep.desired, dep.ready, dep.updated, dep.available) == (2, 1, 2, 1)
    svc = parse_services(SERVICES)[0]
    assert svc.ports == [(5432, "5432", "TCP")] and svc.cluster_ip == "10.43.0.9"
    state = KubeState(available=True, pods=pods)
    assert [p.name for p in state.unhealthy_pods] == ["job-x", "worker-0"]


def test_parse_port_forward_cmdline() -> None:
    assert parse_port_forward_cmdline(
        ["kubectl", "port-forward", "-n", "ns1", "deployment/pg", "5435:5432"]
    ) == {
        "context": None,
        "namespace": "ns1",
        "kind": "deployment",
        "name": "pg",
        "mappings": [(5435, 5432)],
    }
    pf = parse_port_forward_cmdline(
        [
            "/usr/local/bin/kubectl",
            "--context=prod",
            "port-forward",
            "svc/redis",
            "6380:6379",
            "8080",
            "--address",
            "0.0.0.0",
            "--namespace=x",
        ]
    )
    assert pf == {
        "context": "prod",
        "namespace": "x",
        "kind": "service",
        "name": "redis",
        "mappings": [(6380, 6379), (8080, 8080)],
    }
    assert parse_port_forward_cmdline(["kubectl", "port-forward", "mypod", "9000"])["kind"] == "pod"
    assert parse_port_forward_cmdline(["kubectl", "get", "pods"]) is None
    assert parse_port_forward_cmdline(["node", "port-forward"]) is None
    assert parse_port_forward_cmdline([]) is None


def test_parse_ssh_tunnel_cmdline() -> None:
    assert parse_ssh_tunnel_cmdline(
        ["ssh", "-N", "-L", "15432:db.internal:5432", "-p", "2222", "me@bastion"]
    ) == {"host": "me@bastion", "forwards": [(15432, "db.internal", 5432)]}
    assert parse_ssh_tunnel_cmdline(["ssh", "-L127.0.0.1:8080:web:80", "host"]) == {
        "host": "host",
        "forwards": [(8080, "web", 80)],
    }
    assert parse_ssh_tunnel_cmdline(["ssh", "host"]) is None
    assert parse_ssh_tunnel_cmdline(["scp", "-L", "1:2:3", "h"]) is None


def test_identity_of_tunnels() -> None:
    pf = make_listener(
        port=5435,
        processes=[
            make_process(
                name="kubectl",
                cmdline=[
                    "kubectl",
                    "port-forward",
                    "-n",
                    "dev",
                    "deployment/postgres",
                    "5435:5432",
                ],
            )
        ],
    )
    ident = identify(pf)
    assert ident.service == "port-forward → deployment/postgres:5432 (dev)"
    assert ident.role == "tunnel" and ident.project == "dev" and ident.confidence >= 0.95
    assert pf.tunnel and pf.tunnel["remote_port"] == 5432
    ssh = make_listener(
        port=15432,
        processes=[
            make_process(name="ssh", cmdline=["ssh", "-N", "-L", "15432:db:5432", "me@bastion"])
        ],
    )
    assert identify(ssh).service == "ssh tunnel → db:5432 via me@bastion"


def test_parse_timestamp_reads_kubernetes_iso_times() -> None:
    assert parse_timestamp("2026-09-19T21:21:29Z") == 1789852889.0
    assert parse_timestamp("2026-09-19T21:21:29.736032886Z") == pytest.approx(1789852889.736)
    assert parse_timestamp("not a time") is None
    assert parse_timestamp(None) is None
    assert parse_timestamp("") is None


def test_owner_of_walks_from_a_replica_set_to_its_deployment() -> None:
    replica_set = {"ownerReferences": [{"kind": "ReplicaSet", "name": "api-7d9f4c8b6d"}]}
    job = {"ownerReferences": [{"kind": "Job", "name": "migrate"}]}

    assert owner_of(replica_set) == ("Deployment", "api")
    assert owner_of(job) == ("Job", "migrate")
    assert owner_of({}) == (None, None)
