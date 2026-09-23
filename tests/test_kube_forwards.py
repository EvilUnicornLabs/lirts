from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import psutil

from lirts.identity import identify
from lirts.insights import explain
from lirts.models import Snapshot
from tests.conftest import make_listener, make_process
from tests.test_engine import make_engine
from tests.test_kube_provider import make_provider


def test_forward_tracking(monkeypatch, tmp_path: Path) -> None:
    provider, _ = make_provider(monkeypatch, tmp_path)
    assert provider.forwards() == []

    # Pretend kubectl is a long-running process so the forward stays "alive".
    real_popen = subprocess.Popen

    def fake_popen(cmd, **kw):
        return real_popen([sys.executable, "-c", "import time; time.sleep(30)"])

    monkeypatch.setattr("lirts.collectors.kube.subprocess.Popen", fake_popen)
    ok, msg = provider.start_forward(
        "dev", target="service/postgres", local_port=35432, remote_port=5432
    )
    assert ok, msg
    fwd = provider.forwards()
    assert len(fwd) == 1 and fwd[0].local_port == 35432 and fwd[0].alive
    assert (tmp_path / "kube-forwards.json").exists()
    ok, msg = provider.start_forward(
        "dev", target="service/postgres", local_port=35432, remote_port=5432
    )
    assert not ok and "already forwarded" in msg
    results = provider.stop_forward(local_port=35432)
    assert results and results[0][1] is True
    time.sleep(0.3)
    assert provider.forwards() == []
    assert (
        not psutil.pid_exists(fwd[0].pid)
        or psutil.Process(fwd[0].pid).status() == psutil.STATUS_ZOMBIE
    )
    assert provider.stop_forward() == []


def test_engine_flags_missing_forward_targets(tmp_path: Path, monkeypatch) -> None:
    engine = make_engine(tmp_path)
    provider, _ = make_provider(monkeypatch, tmp_path)
    engine.kube = provider
    provider.fetch()
    good = make_listener(
        port=5435,
        processes=[
            make_process(
                name="kubectl",
                cmdline=["kubectl", "port-forward", "-n", "dev", "service/postgres", "5435:5432"],
            )
        ],
    )
    bad = make_listener(
        port=5436,
        processes=[
            make_process(
                name="kubectl",
                cmdline=["kubectl", "port-forward", "-n", "dev", "deployment/gone", "5436:5432"],
            )
        ],
    )
    other_ns = make_listener(
        port=5437,
        processes=[
            make_process(
                name="kubectl",
                cmdline=["kubectl", "port-forward", "-n", "prod", "deployment/gone", "5437:5432"],
            )
        ],
    )
    for lst in (good, bad, other_ns):
        lst.identity = identify(lst)
    engine._check_tunnels([good, bad, other_ns])
    assert good.insights == []
    assert bad.insights and bad.insights[0].code == "tunnel-target-missing"
    assert other_ns.insights == []  # unknown namespace: no verdict
    snap = Snapshot(listeners=[good, bad], kube=provider.snapshot())
    text = explain(snap)
    assert (
        "Kubernetes: context ctx1, namespace dev: 3 pods" in text and "2 pod(s) not healthy" in text
    )
    assert "Tunnels: 5435 → deployment/postgres" not in text and "5435 →" in text
    engine.close()
