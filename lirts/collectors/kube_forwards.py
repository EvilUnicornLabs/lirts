"""Tracked ``kubectl port-forward`` processes.

lirts starts them on request, writes them to ``kube-forwards.json`` in its state
directory and stops them again; the forwards themselves are ordinary detached
``kubectl`` processes, so they survive lirts closing.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

import psutil

from lirts.collectors.kube_models import KubeForward
from lirts.constants import KUBECTL_SETTLE_SECONDS

log = logging.getLogger(__name__)


class KubeForwardsMixin:
    """Starting, listing and stopping the port-forwards lirts is responsible for."""

    # Set by KubeProvider.__init__.
    kubectl: str | None
    context: str | None
    state_root: Path | None

    def _forwards_file(self) -> Path | None:
        return (self.state_root / "kube-forwards.json") if self.state_root else None

    def forwards(self) -> list[KubeForward]:
        """The port-forwards lirts started that are still running."""
        path = self._forwards_file()
        if not path or not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        out: list[KubeForward] = []
        for raw in data:
            try:
                fwd = KubeForward(**raw)
            except TypeError:
                continue
            if fwd.alive:
                out.append(fwd)
        if len(out) != len(data):
            self._save_forwards(out)
        return out

    def _save_forwards(self, forwards: list[KubeForward]) -> None:
        path = self._forwards_file()
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps([f.__dict__ for f in forwards]), encoding="utf-8")
        except OSError as exc:
            log.debug("could not save forwards: %s", exc)

    def start_forward(
        self, namespace: str, *, target: str, local_port: int, remote_port: int
    ) -> tuple[bool, str]:
        """Start ``kubectl port-forward`` in the background and remember it."""
        if not self.kubectl:
            return False, "kubectl not found"
        for fwd in self.forwards():
            if fwd.local_port == local_port:
                return (
                    False,
                    f"local port {local_port} is already forwarded to {fwd.target} (PID {fwd.pid})",
                )
        cmd = [self.kubectl]
        if self.context:
            cmd += ["--context", self.context]
        cmd += ["port-forward", "-n", namespace, target, f"{local_port}:{remote_port}"]
        log_dir = (self.state_root / "forwards") if self.state_root else None
        try:
            if log_dir:
                log_dir.mkdir(parents=True, exist_ok=True)
                with open(log_dir / f"{local_port}.log", "ab") as log_file:
                    child = subprocess.Popen(
                        cmd,
                        stdin=subprocess.DEVNULL,
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
            else:
                child = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except OSError as exc:
            return False, str(exc)
        time.sleep(KUBECTL_SETTLE_SECONDS)
        if child.poll() is not None:
            hint = ""
            if log_dir:
                try:
                    hint = (
                        ": "
                        + (log_dir / f"{local_port}.log")
                        .read_text(errors="replace")
                        .strip()
                        .splitlines()[-1]
                    )
                except (OSError, IndexError):
                    hint = ""
            return False, f"kubectl port-forward exited immediately{hint}"
        fwd = KubeForward(
            child.pid, self.context, namespace, target, local_port, remote_port, time.time()
        )
        self._save_forwards([*self.forwards(), fwd])
        return True, f"forwarding localhost:{local_port} → {target}:{remote_port} (PID {child.pid})"

    def stop_forward(
        self, local_port: int | None = None, pid: int | None = None
    ) -> list[tuple[int, bool, str]]:
        """Stop tracked forwards matching ``local_port`` / ``pid``, or all when both are None."""
        results: list[tuple[int, bool, str]] = []
        keep: list[KubeForward] = []
        for fwd in self.forwards():
            match = (
                (local_port is None and pid is None)
                or fwd.local_port == local_port
                or fwd.pid == pid
            )
            if not match:
                keep.append(fwd)
                continue
            try:
                psutil.Process(fwd.pid).terminate()
                results.append((fwd.local_port, True, f"stopped {fwd.target} (PID {fwd.pid})"))
            except psutil.NoSuchProcess:
                results.append((fwd.local_port, True, "already gone"))
            except psutil.Error as exc:
                results.append((fwd.local_port, False, str(exc)))
                keep.append(fwd)
        self._save_forwards(keep)
        return results
