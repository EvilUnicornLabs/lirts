"""Kubernetes via ``kubectl``: read-only listings, pod actions and tracked port-forwards.

Everything goes through the ``kubectl`` binary so it works with any cluster
the user can already reach.  The kubeconfig is never modified: context and
namespace are passed as flags.  Listings are cached and refreshed by a
background thread because clusters are often remote and slow.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from lirts.collectors.kube_forwards import KubeForwardsMixin
from lirts.collectors.kube_models import (
    KubeDeployment,
    KubeForward,
    KubePod,
    KubeService,
    KubeState,
)
from lirts.collectors.kube_parse import (
    owner_of,
    parse_deployments,
    parse_pods,
    parse_port_forward_cmdline,
    parse_services,
    parse_ssh_tunnel_cmdline,
    parse_timestamp,
)
from lirts.constants import KUBECTL_CONFIG_TIMEOUT, LOG_TAIL_POD

__all__ = [
    "KubeDeployment",
    "KubeForward",
    "KubePod",
    "KubeProvider",
    "KubeService",
    "KubeState",
    "owner_of",
    "parse_deployments",
    "parse_pods",
    "parse_port_forward_cmdline",
    "parse_services",
    "parse_ssh_tunnel_cmdline",
    "parse_timestamp",
]

log = logging.getLogger(__name__)

# Background refreshes never run more often than this, whatever the config says.
MIN_KUBE_INTERVAL = 5.0
# Fetching logs is allowed to take longer than an ordinary listing.
LOGS_TIMEOUT_FACTOR = 2


class KubeProvider(KubeForwardsMixin):
    """Wraps ``kubectl``; safe to use when kubectl or a cluster is missing."""

    def __init__(
        self,
        enabled: str | bool = "auto",
        context: str | None = None,
        namespace: str | None = None,
        all_namespaces: bool = False,
        interval: float = 30.0,
        timeout: float = 10.0,
        state_root: Path | None = None,
    ) -> None:
        self.kubectl = shutil.which("kubectl")
        self.mode = enabled
        self.context = context or None
        self.namespace = namespace or None
        self.all_namespaces = all_namespaces
        self.interval = max(MIN_KUBE_INTERVAL, interval)
        self.timeout = timeout
        self.state_root = state_root
        self.state = KubeState()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()

    # ----- availability -------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """True when kubectl exists and the mode ("auto", "on", "off") allows using it."""
        if self.mode in (False, "off", "false"):
            return False
        if not self.kubectl:
            return False
        if self.mode in (True, "on", "true"):
            return True
        return self.current_context() is not None

    def _run(
        self,
        *args: str,
        timeout: float | None = None,
        namespace: str | None = None,
        all_namespaces: bool = False,
        context: str | None = None,
    ) -> tuple[bool, str]:
        if not self.kubectl:
            return False, "kubectl not found"
        cmd = [self.kubectl]
        ctx = context or self.context
        if ctx:
            cmd += ["--context", ctx]
        if all_namespaces:
            cmd.append("--all-namespaces")
        elif namespace:
            cmd += ["-n", namespace]
        cmd += list(args)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=False, timeout=timeout or self.timeout
            )
        except subprocess.TimeoutExpired:
            return False, f"kubectl timed out after {timeout or self.timeout:g}s"
        except OSError as exc:
            return False, str(exc)
        if result.returncode != 0:
            return (
                False,
                (result.stderr or result.stdout).strip() or f"kubectl exited {result.returncode}",
            )
        return True, result.stdout

    def current_context(self) -> str | None:
        """The configured context, or the kubeconfig's current one."""
        if self.context:
            return self.context
        ok, out = self._run("config", "current-context", timeout=KUBECTL_CONFIG_TIMEOUT)
        return out.strip() if ok and out.strip() else None

    def current_namespace(self) -> str | None:
        """The configured namespace, or the current context's one."""
        if self.namespace:
            return self.namespace
        ok, out = self._run(
            "config",
            "view",
            "--minify",
            "-o",
            "jsonpath={..namespace}",
            timeout=KUBECTL_CONFIG_TIMEOUT,
        )
        return out.strip() or "default" if ok else None

    def contexts(self) -> list[tuple[str, bool]]:
        """``(name, is_current)`` for every context in the kubeconfig."""
        ok, out = self._run("config", "get-contexts", "-o", "name", timeout=KUBECTL_CONFIG_TIMEOUT)
        if not ok:
            return []
        current = self.current_context()
        return [(name, name == current) for name in out.split() if name]

    def api_server(self) -> tuple[str, int] | None:
        """(host, port) of the current context's API server, from the kubeconfig."""
        ok, out = self._run(
            "config",
            "view",
            "--minify",
            "-o",
            "jsonpath={.clusters[0].cluster.server}",
            timeout=KUBECTL_CONFIG_TIMEOUT,
        )
        url = out.strip()
        if not ok or not url:
            return None
        parsed = urlparse(url if "://" in url else "https://" + url)
        if not parsed.hostname:
            return None
        return parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)

    def namespaces(self) -> list[str]:
        """Every namespace name the current context can list."""
        ok, out = self._run("get", "namespaces", "-o", "jsonpath={.items[*].metadata.name}")
        return out.split() if ok else []

    # ----- listings (cached) --------------------------------------------------------

    def fetch(self) -> KubeState:
        """Synchronous refresh of pods, deployments and services."""
        state = KubeState(available=False, context=self.current_context())
        if not self.kubectl:
            state.error = "kubectl not found"
            return self._store(state)
        if state.context is None:
            state.error = "no current kube context"
            return self._store(state)
        state.namespace = None if self.all_namespaces else self.current_namespace()
        ok, out = self._run(
            "config",
            "view",
            "--minify",
            "-o",
            "jsonpath={.clusters[0].cluster.server}",
            timeout=KUBECTL_CONFIG_TIMEOUT,
        )
        state.server = out.strip() if ok else None
        for kind, parser, attr in (
            ("pods", parse_pods, "pods"),
            ("deployments", parse_deployments, "deployments"),
            ("services", parse_services, "services"),
        ):
            ok, out = self._run(
                "get",
                kind,
                "-o",
                "json",
                namespace=state.namespace,
                all_namespaces=self.all_namespaces,
            )
            if not ok:
                state.error = out.splitlines()[0] if out else "kubectl failed"
                return self._store(state)
            try:
                setattr(state, attr, parser(json.loads(out)))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                state.error = f"could not parse {kind}: {exc}"
                return self._store(state)
        state.available = True
        state.fetched_at = time.time()
        return self._store(state)

    def _store(self, state: KubeState) -> KubeState:
        with self._lock:
            self.state = state
        return state

    def snapshot(self) -> KubeState:
        """The last fetched cluster state (never blocks on kubectl)."""
        with self._lock:
            return self.state

    def start(self) -> None:
        """Start the background refresh thread, if Kubernetes is enabled."""
        if self._thread is not None or not self.enabled:
            return
        self._thread = threading.Thread(target=self._loop, name="lirts-kube", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Ask the background thread to finish."""
        self._stop.set()
        self._wake.set()

    def refresh_soon(self) -> None:
        """Wake the background thread for one refresh now."""
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.fetch()
            except Exception as exc:  # the thread must survive whatever kubectl does
                log.debug("kube fetch failed: %s", exc)
            self._wake.clear()
            self._wake.wait(self.interval)

    # ----- actions -----------------------------------------------------------------

    def logs(
        self,
        namespace: str,
        *,
        pod: str,
        tail: int = LOG_TAIL_POD,
        previous: bool = False,
        container: str | None = None,
    ) -> str:
        """The last ``tail`` log lines of a pod, or the error that prevented it."""
        args = ["logs", pod, f"--tail={tail}"]
        if previous:
            args.append("--previous")
        if container:
            args += ["-c", container]
        ok, out = self._run(*args, namespace=namespace, timeout=self.timeout * LOGS_TIMEOUT_FACTOR)
        return out if ok else f"Error fetching logs: {out}"

    def exec_command(self, namespace: str, *, pod: str, container: str | None = None) -> list[str]:
        """The interactive shell command to run in a suspended terminal."""
        cmd = [self.kubectl or "kubectl"]
        if self.context:
            cmd += ["--context", self.context]
        cmd += ["exec", "-it", "-n", namespace, pod]
        if container:
            cmd += ["-c", container]
        cmd += [
            "--",
            "sh",
            "-c",
            "if command -v bash >/dev/null 2>&1; then exec bash; else exec sh; fi",
        ]
        return cmd

    def rollout_restart(self, namespace: str, *, kind: str, name: str) -> tuple[bool, str]:
        """Roll a deployment (or other workload) over; returns ``(ok, kubectl output)``."""
        ok, out = self._run("rollout", "restart", f"{kind.lower()}/{name}", namespace=namespace)
        return ok, out.strip()

    def delete_pod(self, namespace: str, pod: str) -> tuple[bool, str]:
        """Delete a pod without waiting; returns ``(ok, kubectl output)``."""
        ok, out = self._run("delete", "pod", pod, "--wait=false", namespace=namespace)
        return ok, out.strip()

    def close(self) -> None:
        """Stop the background thread; tracked forwards keep running."""
        self.stop()
