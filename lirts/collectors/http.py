"""Silent HTTP health probing.

Each port is probed at most once per ``interval`` seconds with a ``HEAD``
request (falling back to ``GET`` when the server rejects ``HEAD``), first over
plain HTTP and then over HTTPS if the plain attempt looks like a TLS handshake
failure.  Results are cached per ``(port, pid)`` so a restarted service is
re-probed immediately.

Besides status and latency the probe records what the answer was (its content
type and :class:`~lirts.identity_rules_web.ProbeKind`) and which framework gave
itself away in the headers.  Only an HTML page costs a second, capped ``GET``,
and only when the headers said nothing.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import time
from collections.abc import Mapping

import aiohttp

from lirts.constants import TLS_PORTS
from lirts.identity import Role
from lirts.identity_rules_web import ProbeKind, detect_web_signal, probe_kind
from lirts.models import HttpProbe, Listener

log = logging.getLogger(__name__)

# Roles that never speak HTTP; a confident identity is enough to skip the probe.
_NON_HTTP_ROLES = {Role.DB, Role.CACHE, Role.QUEUE, Role.SYSTEM}
_NON_HTTP_MIN_CONFIDENCE = 0.8
# Statuses that mean "this server dislikes HEAD"; retry the same URL with GET.
_HEAD_REJECTED = (405, 501)
# Error strings end up in one table cell.
_ERROR_PREVIEW_CHARS = 80
_TLS_DETAIL_CHARS = 60
# Never read more of an HTML page than this; the dev-server markers are in the head.
_BODY_LIMIT_BYTES = 64 * 1024

# Errors that suggest the port speaks TLS rather than plain HTTP.
_TLS_HINTS = (
    "ssl",
    "tls",
    "certificate",
    "handshake",
    "bad status line",
    "invalid",
    "disconnected",
    "reset",
)


def _header_lines(headers: Mapping[str, str]) -> str:
    """The response headers as ``name: value`` lines, for the dev-server patterns."""
    return "\n".join(f"{name}: {value}" for name, value in headers.items())


async def _read_html_body(resp: aiohttp.ClientResponse) -> str:
    """The first :data:`_BODY_LIMIT_BYTES` of an HTML response; empty for anything else."""
    if probe_kind(resp.headers.get("Content-Type")) != ProbeKind.HTML:
        return ""
    return (await resp.content.read(_BODY_LIMIT_BYTES)).decode("utf-8", "replace")


class HttpProber:
    """Silent HTTP probing of listening ports, cached per ``(port, pid)``."""

    def __init__(
        self,
        enabled: bool = True,
        interval: float = 10.0,
        timeout: float = 1.0,
        skip_ports: list[int] | None = None,
        force_ports: list[int] | None = None,
        concurrency: int = 16,
    ) -> None:
        self.enabled = enabled
        self.interval = interval
        self.timeout = timeout
        self.skip_ports = set(skip_ports or [])
        self.force_ports = set(force_ports or [])
        self.concurrency = concurrency
        self._cache: dict[str, HttpProbe] = {}
        self._extra_skip: set[str] = set()  # listener keys identified as non-HTTP

    # ----- policy ------------------------------------------------------------------

    def should_probe(self, listener: Listener) -> bool:
        """Whether this listener is worth a silent HTTP request right now."""
        if not self.enabled or listener.protocol != "TCP":
            return False
        if listener.port in self.force_ports:
            return True
        if listener.port in self.skip_ports or self._cache_key(listener) in self._extra_skip:
            return False
        role = listener.identity.role
        return not (
            role in _NON_HTTP_ROLES and listener.identity.confidence >= _NON_HTTP_MIN_CONFIDENCE
        )

    def mark_non_http(self, key: str) -> None:
        """Remember that this listener answered garbage, so it is not probed again."""
        self._extra_skip.add(key)

    def cached(self, listener: Listener) -> HttpProbe | None:
        """The last probe result for this listener, if there is one."""
        return self._cache.get(self._cache_key(listener))

    @staticmethod
    def _cache_key(listener: Listener) -> str:
        return f"{listener.key}:{listener.pid or 0}"

    @staticmethod
    def target_host(listener: Listener) -> str:
        """The address to probe: a specific bind address when there is one, else loopback."""
        addrs = listener.addresses
        if addrs and all(":" in a for a in addrs) and "::" not in addrs:
            return "[::1]"
        if addrs and all(a not in ("0.0.0.0", "", "127.0.0.1", "::", "::1") for a in addrs):
            # Bound to a specific interface address only.
            a = addrs[0]
            return f"[{a}]" if ":" in a else a
        return "127.0.0.1"

    # ----- probing -----------------------------------------------------------------

    async def _request(self, session: aiohttp.ClientSession, url: str) -> HttpProbe:
        probe = HttpProbe(attempted=True, probed_at=time.time(), scheme=url.split(":", 1)[0])
        start = time.perf_counter()
        try:
            body = ""
            async with session.head(url, allow_redirects=False) as resp:
                probe.status = resp.status
                headers = resp.headers
            if probe.status in _HEAD_REJECTED:
                async with session.get(url, allow_redirects=False) as resp:
                    probe.status = resp.status
                    headers = resp.headers
                    body = await _read_html_body(resp)
            probe.latency_ms = (time.perf_counter() - start) * 1000
            probe.ok = True
            probe.server = headers.get("Server")
            probe.powered_by = headers.get("X-Powered-By")
            probe.content_type = headers.get("Content-Type")
            probe.kind = probe_kind(probe.content_type)
            probe.dev_server = detect_web_signal(_header_lines(headers))
            if probe.dev_server is None and probe.kind == ProbeKind.HTML:
                # The markers are in the page itself: one small GET, HTML only.
                if not body:
                    async with session.get(url, allow_redirects=False) as resp:
                        body = await _read_html_body(resp)
                probe.dev_server = detect_web_signal("", body)
        except TimeoutError:
            probe.error = "timeout"
        except aiohttp.ClientConnectorError as exc:
            os_error = exc.os_error
            if (
                isinstance(os_error, OSError) and os_error.errno == errno.ECONNREFUSED
            ) or "refused" in str(exc).lower():
                probe.error = "connection refused"
            else:
                detail = (
                    os_error.strerror
                    if isinstance(os_error, OSError) and os_error.strerror
                    else str(exc)
                )
                probe.error = detail[:_ERROR_PREVIEW_CHARS]
        except aiohttp.ClientError as exc:
            probe.error = type(exc).__name__.replace("Client", "").replace(
                "Error", ""
            ).lower() or str(exc)
            detail = str(exc).lower()
            if any(h in detail for h in _TLS_HINTS):
                probe.error = f"{probe.error}: {str(exc)[:_TLS_DETAIL_CHARS]}"
        except (OSError, ValueError) as exc:
            probe.error = str(exc)[:_ERROR_PREVIEW_CHARS]
        return probe

    async def probe(self, session: aiohttp.ClientSession, listener: Listener) -> HttpProbe:
        """Probe one listener over HTTP and HTTPS as needed; returns what was learned."""
        host = self.target_host(listener)
        schemes = ["http", "https"] if listener.port not in TLS_PORTS else ["https", "http"]
        result: HttpProbe | None = None
        for scheme in schemes:
            result = await self._request(session, f"{scheme}://{host}:{listener.port}/")
            if result.ok:
                return result
            # Only try the other scheme when the failure looks protocol-related,
            # not when nothing is listening at all.
            if result.error and ("refused" in result.error or result.error == "timeout"):
                break
        result = result or HttpProbe(attempted=True, probed_at=time.time(), error="unknown")
        if result.error and "refused" not in result.error and result.error != "timeout":
            # Both schemes answered with garbage: this port does not speak HTTP.
            result.error = "not HTTP"
            self.mark_non_http(self._cache_key(listener))
        return result

    async def enrich(self, listeners: list[Listener]) -> None:
        """Attach cached or fresh probe results to ``listeners``."""
        if not self.enabled:
            return
        now = time.time()
        due: list[Listener] = []
        for lst in listeners:
            if not self.should_probe(lst):
                cached = self._cache.get(self._cache_key(lst))
                lst.http = (
                    cached if cached and cached.error == "not HTTP" else HttpProbe(attempted=False)
                )
                continue
            cached = self._cache.get(self._cache_key(lst))
            if cached and now - cached.probed_at < self.interval:
                lst.http = cached
            else:
                due.append(lst)
        if not due:
            return
        sem = asyncio.Semaphore(self.concurrency)
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        connector = aiohttp.TCPConnector(ssl=False, limit=self.concurrency, force_close=True)
        headers = {"User-Agent": "lirts-health-probe"}

        async def run(lst: Listener) -> None:
            async with sem:
                lst.http = await self.probe(session, lst)
                self._cache[self._cache_key(lst)] = lst.http

        async with aiohttp.ClientSession(
            timeout=timeout, connector=connector, headers=headers
        ) as session:
            await asyncio.gather(*(run(lst) for lst in due), return_exceptions=True)

        live = {self._cache_key(lst) for lst in listeners}
        for key in list(self._cache):
            if key not in live:
                del self._cache[key]
        self._extra_skip &= live
