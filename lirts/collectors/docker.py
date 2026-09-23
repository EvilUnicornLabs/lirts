"""Docker container discovery and control.

Primary path: the Docker SDK's low-level API (one HTTP call for the list, a
cached ``inspect`` per container).  Fallback: the ``docker`` CLI producing the
same JSON, so the same parsers apply.  Everything degrades to "no Docker"
silently; the UI simply shows local processes only.
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from lirts.collectors.docker_parse import (
    COMPOSE_PROJECT,
    COMPOSE_SERVICE,
    COMPOSE_WORKDIR,
    apply_inspect,
    compute_stats_delta,
    parse_cli_ports,
    parse_list_entry,
    parse_started_at,
)
from lirts.constants import (
    DOCKER_ACTION_TIMEOUT,
    DOCKER_CLI_TIMEOUT,
    DOCKER_CLIENT_RETRY_SECONDS,
    DOCKER_CLIENT_TIMEOUT,
    DOCKER_STATS_WORKERS,
    DOCKER_STOP_TIMEOUT,
)
from lirts.models import ContainerInfo

__all__ = [
    "COMPOSE_PROJECT",
    "COMPOSE_SERVICE",
    "COMPOSE_WORKDIR",
    "DockerException",
    "DockerProvider",
    "apply_inspect",
    "compute_stats_delta",
    "parse_cli_ports",
    "parse_list_entry",
    "parse_started_at",
]

log = logging.getLogger(__name__)

try:  # the SDK is a hard dependency but keep the import guarded for slim installs
    import docker as docker_sdk
    from docker.errors import DockerException
except Exception:  # pragma: no cover - a broken SDK install must not stop lirts starting
    docker_sdk = None  # type: ignore[assignment]  # the "no SDK" sentinel

    class DockerException(Exception):  # type: ignore[no-redef]  # stand-in for the SDK's error
        pass


class DockerProvider:
    """Lists containers and executes control actions."""

    def __init__(
        self,
        enabled: bool = True,
        inspect_interval: float = 15.0,
        mask_patterns: list[str] | None = None,
        stats: bool = True,
    ) -> None:
        self.enabled = enabled
        self.inspect_interval = inspect_interval
        self.mask_patterns = mask_patterns or []
        self.stats_enabled = stats
        self._client: Any = None
        self._client_failed_at = 0.0
        self._inspect_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._stats_prev: dict[str, tuple[float, dict[str, Any]]] = {}
        self._stats_last: dict[str, dict[str, float | None]] = {}
        self.available = False
        self.last_error: str | None = None
        self._cli = shutil.which("docker")

    # ----- client management -------------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if docker_sdk is None:
            return None
        # Do not hammer the socket if the daemon is down.
        if time.time() - self._client_failed_at < DOCKER_CLIENT_RETRY_SECONDS:
            return None
        try:
            client = docker_sdk.from_env(timeout=DOCKER_CLIENT_TIMEOUT)
            client.ping()
            self._client = client
            return client
        except Exception as exc:  # the SDK wraps socket, TLS and daemon errors in its own types
            self.last_error = str(exc)
            self._client_failed_at = time.time()
            return None

    def _reset_client(self) -> None:
        if self._client is not None:
            # A client that failed is being replaced; how its close() fails does not matter.
            with contextlib.suppress(Exception):
                self._client.close()
        self._client = None

    # ----- listing -----------------------------------------------------------------

    def _list_raw(self) -> list[dict[str, Any]] | None:
        client = self._get_client()
        if client is not None:
            try:
                return list(client.api.containers())
            except Exception as exc:  # any SDK failure falls back to the CLI below
                self.last_error = str(exc)
                self._reset_client()
        return self._list_raw_cli()

    def _list_raw_cli(self) -> list[dict[str, Any]] | None:
        if not self._cli:
            return None
        try:
            result = subprocess.run(
                [self._cli, "ps", "--format", "{{json .}}"],
                capture_output=True,
                text=True,
                check=False,
                timeout=DOCKER_CLI_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.last_error = str(exc)
            return None
        if result.returncode != 0:
            self.last_error = result.stderr.strip() or "docker ps failed"
            return None
        entries: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            entries.append(
                {
                    "Id": row.get("ID", ""),
                    "Names": [row.get("Names", "")],
                    "Image": row.get("Image", ""),
                    "State": row.get("State", "running"),
                    "Status": row.get("Status", ""),
                    "Ports": parse_cli_ports(row.get("Ports", "")),
                    "Labels": row.get("Labels", ""),
                }
            )
        return entries

    def _inspect(self, container_id: str) -> dict[str, Any] | None:
        now = time.time()
        cached = self._inspect_cache.get(container_id)
        if cached and now - cached[0] < self.inspect_interval:
            return cached[1]
        data: dict[str, Any] | None = None
        client = self._get_client()
        if client is not None:
            try:
                data = client.api.inspect_container(container_id)
            except Exception as exc:  # any SDK failure falls back to the CLI below
                log.debug("inspect via SDK failed: %s", exc)
        if data is None and self._cli:
            try:
                result = subprocess.run(
                    [self._cli, "inspect", container_id],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=DOCKER_CLI_TIMEOUT,
                )
                if result.returncode == 0:
                    parsed = json.loads(result.stdout)
                    if parsed:
                        data = parsed[0]
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
                data = None
        if data is not None:
            self._inspect_cache[container_id] = (now, data)
        return data

    def list_containers(self) -> list[ContainerInfo]:
        """Running containers with port mappings and (cached) inspect details."""
        if not self.enabled:
            self.available = False
            return []
        raw = self._list_raw()
        if raw is None:
            self.available = False
            return []
        self.available = True
        containers: list[ContainerInfo] = []
        for entry in raw:
            try:
                info = parse_list_entry(entry)
            except (KeyError, TypeError, ValueError) as exc:
                log.debug("could not parse container entry: %s", exc)
                continue
            inspect = self._inspect(info.id) if info.id else None
            if inspect:
                apply_inspect(info, inspect=inspect, mask_patterns=self.mask_patterns)
            containers.append(info)
        live = {c.id for c in containers}
        for cid in list(self._inspect_cache):
            if cid not in live:
                del self._inspect_cache[cid]
        for cid in list(self._stats_prev):
            if cid not in live:
                del self._stats_prev[cid]
                self._stats_last.pop(cid, None)
        if self.stats_enabled:
            self.sample_stats(containers)
        return containers

    def _one_shot_stats(self, container_id: str) -> tuple[str, dict[str, Any] | None]:
        client = self._client
        if client is None:
            return container_id, None
        try:
            return container_id, client.api.stats(container_id, stream=False, one_shot=True)
        except TypeError:  # very old SDK without one_shot
            try:
                return container_id, client.api.stats(container_id, stream=False)
            except Exception:  # stats are a nice-to-have; a container without them shows none
                return container_id, None
        except Exception as exc:  # the SDK raises its own error types for a dead container
            log.debug("stats failed for %s: %s", container_id[:12], exc)
            return container_id, None

    def sample_stats(self, containers: list[ContainerInfo]) -> None:
        """Attach CPU / memory / network figures (needs two samples for rates)."""
        if self._client is None or not containers:
            for c in containers:
                self._apply_stats(c)
            return
        now = time.time()
        with ThreadPoolExecutor(max_workers=min(DOCKER_STATS_WORKERS, len(containers))) as pool:
            results = list(pool.map(self._one_shot_stats, [c.id for c in containers]))
        for cid, payload in results:
            if payload is None:
                continue
            prev = self._stats_prev.get(cid)
            dt = now - prev[0] if prev else 0.0
            self._stats_last[cid] = compute_stats_delta(
                prev[1] if prev else None, cur=payload, dt=dt
            )
            self._stats_prev[cid] = (now, payload)
        for c in containers:
            self._apply_stats(c)

    def _apply_stats(self, c: ContainerInfo) -> None:
        s = self._stats_last.get(c.id)
        if not s:
            return
        c.cpu_percent = s.get("cpu_percent")
        c.memory_mb = s.get("memory_mb")
        c.memory_limit_mb = s.get("memory_limit_mb")
        c.net_rx_rate = s.get("net_rx_rate")
        c.net_tx_rate = s.get("net_tx_rate")
        rx_total, tx_total = s.get("net_rx_bytes"), s.get("net_tx_bytes")
        c.net_rx_bytes = int(rx_total) if rx_total is not None else None
        c.net_tx_bytes = int(tx_total) if tx_total is not None else None
        pids = s.get("pids")
        c.pids_current = int(pids) if pids is not None else None

    # ----- actions -----------------------------------------------------------------

    def _cli_action(self, *args: str, timeout: float = DOCKER_ACTION_TIMEOUT) -> tuple[bool, str]:
        if not self._cli:
            return False, "docker CLI not found"
        try:
            result = subprocess.run(
                [self._cli, *args], capture_output=True, text=True, check=False, timeout=timeout
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)
        if result.returncode != 0:
            return False, result.stderr.strip() or f"docker {args[0]} failed"
        return True, result.stdout

    def stop(self, container_id: str) -> tuple[bool, str]:
        """Stop a container through the SDK, falling back to the CLI."""
        client = self._get_client()
        if client is not None:
            try:
                client.api.stop(container_id, timeout=DOCKER_STOP_TIMEOUT)
                return True, "stopped"
            except Exception as exc:  # any SDK failure falls back to the CLI below
                log.debug("stop via SDK failed: %s", exc)
        return self._cli_action("stop", container_id)

    def restart(self, container_id: str) -> tuple[bool, str]:
        """Restart a container through the SDK, falling back to the CLI."""
        client = self._get_client()
        if client is not None:
            try:
                client.api.restart(container_id, timeout=DOCKER_STOP_TIMEOUT)
                return True, "restarted"
            except Exception as exc:  # any SDK failure falls back to the CLI below
                log.debug("restart via SDK failed: %s", exc)
        return self._cli_action("restart", container_id)

    def logs(self, container_id: str, tail: int = 200) -> str:
        """The last ``tail`` log lines of a container, or the error that prevented it."""
        client = self._get_client()
        if client is not None:
            try:
                raw = client.api.logs(
                    container_id, tail=tail, timestamps=False, stdout=True, stderr=True
                )
                return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            except Exception as exc:  # any SDK failure falls back to the CLI below
                log.debug("logs via SDK failed: %s", exc)
        ok, out = self._cli_action("logs", "--tail", str(tail), container_id)
        return out if ok else f"Error fetching logs: {out}"

    # ----- compose stacks --------------------------------------------------------

    @staticmethod
    def stack_containers(containers: list[ContainerInfo], project: str) -> list[ContainerInfo]:
        """The containers of one compose project."""
        return [c for c in containers if c.stack == project]

    def stack_action(
        self, containers: list[ContainerInfo], *, project: str, action: str
    ) -> list[tuple[str, bool, str]]:
        """Stop or restart every container of a compose project; returns per-container results."""
        fn = self.stop if action == "stop" else self.restart
        results: list[tuple[str, bool, str]] = []
        for c in self.stack_containers(containers, project):
            ok, msg = fn(c.id)
            results.append((c.name, ok, msg))
        return results

    def stack_logs(self, containers: list[ContainerInfo], *, project: str, tail: int = 100) -> str:
        """Interleaved logs of every container in the project, prefixed by service name."""
        chunks: list[str] = []
        for c in self.stack_containers(containers, project):
            label = c.service or c.name
            text = self.logs(c.id, tail=tail)
            chunks.extend(f"{label:<14} | {line}" for line in text.splitlines())
        return "\n".join(chunks) if chunks else f"(no running containers in project {project})"

    def exec_command(self, container_id: str) -> list[str]:
        """The interactive shell command to run in a suspended terminal."""
        cli = self._cli or "docker"
        shell = "if command -v bash >/dev/null 2>&1; then exec bash; else exec sh; fi"
        return [cli, "exec", "-it", container_id, "sh", "-c", shell]

    def close(self) -> None:
        """Close the Docker client, if one was opened."""
        self._reset_client()
