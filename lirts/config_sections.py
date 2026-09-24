"""The collector sections of the configuration: docker, probes, health, proxies, Kubernetes,
bandwidth and history, plus the base class every section shares.

:mod:`lirts.config_view` composes them into :class:`lirts.config_view.ConfigView`.
"""

from __future__ import annotations

from typing import Any

from lirts.config_defaults import DEFAULT_CONFIG


class _Section:
    """Typed reads of one top-level section of the config."""

    name: str = ""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def _get(self, key: str) -> Any:
        section = self._data.get(self.name) or {}
        value = section.get(key)
        return DEFAULT_CONFIG[self.name][key] if value is None else value

    def _float(self, key: str) -> float:
        return float(self._get(key))

    def _int(self, key: str) -> int:
        return int(self._get(key))

    def _bool(self, key: str) -> bool:
        return bool(self._get(key))

    def _str(self, key: str) -> str:
        return str(self._get(key))


class DockerSection(_Section):
    name = "docker"

    @property
    def enabled(self) -> bool:
        return self._bool("enabled")

    @property
    def show_unpublished(self) -> bool:
        return self._bool("show_unpublished")

    @property
    def inspect_interval(self) -> float:
        return self._float("inspect_interval")

    @property
    def stats(self) -> bool:
        return self._bool("stats")


class HttpProbeSection(_Section):
    name = "http_probe"

    @property
    def enabled(self) -> bool:
        return self._bool("enabled")

    @property
    def interval(self) -> float:
        return self._float("interval")

    @property
    def timeout(self) -> float:
        return self._float("timeout")

    @property
    def skip_ports(self) -> list[int]:
        return [int(p) for p in self._get("skip_ports")]

    @property
    def force_ports(self) -> list[int]:
        return [int(p) for p in self._get("force_ports")]


class HealthSection(_Section):
    name = "health"

    @property
    def timeout(self) -> float:
        return self._float("timeout")

    @property
    def paths(self) -> list[str]:
        return [str(p) for p in self._get("paths")]

    @property
    def overrides(self) -> dict[int, str]:
        return {int(k): str(v) for k, v in dict(self._get("overrides")).items()}


class ProxiesSection(_Section):
    name = "proxies"

    @property
    def enabled(self) -> bool:
        return self._bool("enabled")


class KubernetesSection(_Section):
    name = "kubernetes"

    @property
    def enabled(self) -> str | bool:
        """``"auto"``, ``"off"``, ``True`` or ``False`` as written by the user."""
        value = self._get("enabled")
        return value if isinstance(value, bool) else str(value)

    @property
    def context(self) -> str | None:
        value = self._data.get(self.name, {}).get("context")
        return str(value) if value else None

    @property
    def namespace(self) -> str | None:
        value = self._data.get(self.name, {}).get("namespace")
        return str(value) if value else None

    @property
    def all_namespaces(self) -> bool:
        return self._bool("all_namespaces")

    @property
    def interval(self) -> float:
        return self._float("interval")

    @property
    def timeout(self) -> float:
        return self._float("timeout")


class BandwidthSection(_Section):
    name = "bandwidth"

    @property
    def mode(self) -> str:
        return self._str("mode")

    @property
    def interval(self) -> float:
        return self._float("interval")

    @property
    def connections(self) -> bool:
        return self._bool("connections")


class HistorySection(_Section):
    name = "history"

    @property
    def persist(self) -> bool:
        return self._bool("persist")

    @property
    def window(self) -> int:
        return self._int("window")

    @property
    def retention_hours(self) -> float:
        return self._float("retention_hours")
