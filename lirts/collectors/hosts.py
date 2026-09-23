"""Local domain awareness from ``/etc/hosts``."""

from __future__ import annotations

import os
from pathlib import Path

LOOPBACK = {"127.0.0.1", "::1", "0.0.0.0", "127.0.1.1"}
_IGNORED_NAMES = {
    "localhost",
    "broadcasthost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
}


def parse_hosts(text: str) -> dict[str, list[str]]:
    """Return ``{ip: [hostname, ...]}`` for every non-comment line."""
    mapping: dict[str, list[str]] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        ip, names = parts[0], parts[1:]
        bucket = mapping.setdefault(ip, [])
        for name in names:
            if name not in bucket:
                bucket.append(name)
    return mapping


class HostsMap:
    """Cached view of the hosts file, reloaded when its mtime changes."""

    def __init__(self, path: str | os.PathLike[str] = "/etc/hosts") -> None:
        self.path = Path(path)
        self._mtime: float | None = None
        self.mapping: dict[str, list[str]] = {}

    def refresh(self) -> None:
        """Re-read the hosts file if it changed since the last call."""
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self.mapping = {}
            return
        if mtime == self._mtime:
            return
        try:
            self.mapping = parse_hosts(self.path.read_text(encoding="utf-8", errors="replace"))
            self._mtime = mtime
        except OSError:
            self.mapping = {}

    def loopback_names(self) -> list[str]:
        """Custom hostnames that resolve to this machine."""
        names: list[str] = []
        for ip, hosts in self.mapping.items():
            if ip in LOOPBACK:
                for h in hosts:
                    if h not in _IGNORED_NAMES and h not in names:
                        names.append(h)
        return names
