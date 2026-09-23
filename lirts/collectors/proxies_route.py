"""The route value object shared by the nginx and httpd parsers and the discovery code."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]", "0.0.0.0", "host.docker.internal"}


class ProxyKind(StrEnum):
    """Which local proxy a route was read from."""

    NGINX = "nginx"
    HTTPD = "httpd"


@dataclass
class Route:
    """One virtual host of a local proxy and where it sends requests."""

    source: str  # a ProxyKind member
    listen_port: int
    names: list[str] = field(default_factory=list)
    upstream: str | None = None  # "host:port" or a URL as written
    upstream_port: int | None = None  # set when the upstream is on this machine
    path: str = "/"
    doc_root: str | None = None
    config: str | None = None

    @property
    def label(self) -> str:
        """The virtual host names, or ``(default server)`` when it has none."""
        return ", ".join(self.names) if self.names else "(default server)"

    def describe(self) -> str:
        """One line: which proxy listens where, for which names, and where it forwards."""
        where = f"{self.source} :{self.listen_port} {self.label}"
        if self.upstream:
            target = f"127.0.0.1:{self.upstream_port}" if self.upstream_port else self.upstream
            path = f" {self.path}" if self.path not in ("/", "") else ""
            return f"{where}{path} → {target}"
        if self.doc_root:
            return f"{where} → files in {self.doc_root}"
        return where
