"""Universal health checks, on demand only.

Every TCP listener gets a plain connect check (does the port accept a
connection, and how fast).  Listeners that speak HTTP additionally get a
health endpoint: the checker tries a list of conventional paths and reports
the first one that answers 2xx/3xx.  Nothing runs unless :meth:`check` is
called (``H`` in the dashboard, ``lirts health``); the last results are kept
per ``(port, pid)`` so the dashboard can keep showing them until the next check.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import time

import aiohttp

from lirts.models import HealthResult, Listener, ListenerState, Protocol

log = logging.getLogger(__name__)

# Error strings end up in one table cell.
_ERROR_PREVIEW_CHARS = 60

DEFAULT_PATHS = [
    "/health",
    "/healthz",
    "/readyz",
    "/livez",
    "/api/health",
    "/api/healthz",
    "/-/health",
    "/-/ready",
    "/actuator/health",
    "/status",
    "/ping",
    "/up",
]


class HealthChecker:
    """TCP connect plus a learned HTTP health endpoint, run only when asked."""

    def __init__(
        self,
        timeout: float = 1.5,
        paths: list[str] | None = None,
        overrides: dict[int, str] | None = None,
        concurrency: int = 16,
    ) -> None:
        self.timeout = timeout
        self.paths = [p if p.startswith("/") else "/" + p for p in (paths or DEFAULT_PATHS)]
        self.overrides = {
            int(k): (v if v.startswith("/") else "/" + v) for k, v in (overrides or {}).items()
        }
        self.concurrency = concurrency
        self._cache: dict[str, HealthResult] = {}
        self._learned: dict[str, str | None] = {}  # cache key -> health path ("" = none found)
        self._notes: dict[str, str] = {}  # cache key -> why no endpoint was found

    @staticmethod
    def _key(listener: Listener) -> str:
        return f"{listener.key}:{listener.pid or 0}"

    @staticmethod
    def _host(listener: Listener) -> str:
        addrs = listener.addresses
        if addrs and all(":" in a for a in addrs) and "::" not in addrs:
            return "::1"
        if addrs and all(a not in ("0.0.0.0", "", "127.0.0.1", "::", "::1") for a in addrs):
            return addrs[0]
        return "127.0.0.1"

    async def _tcp(self, host: str, port: int, result: HealthResult) -> None:
        start = time.perf_counter()
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=self.timeout
            )
            result.tcp_ok = True
            result.tcp_latency_ms = (time.perf_counter() - start) * 1000
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        except TimeoutError:
            result.tcp_error = "timeout"
        except OSError as exc:
            result.tcp_error = (
                "refused"
                if getattr(exc, "errno", None) == errno.ECONNREFUSED
                else (exc.strerror or str(exc))[:_ERROR_PREVIEW_CHARS]
            )

    async def _http(
        self, session: aiohttp.ClientSession, listener: Listener, result: HealthResult
    ) -> None:
        key = self._key(listener)
        scheme = listener.http.scheme or "http"
        host = self._host(listener)
        base = f"{scheme}://{'[' + host + ']' if ':' in host else host}:{listener.port}"
        learned = self._learned.get(key)
        if learned is None:
            candidates = (
                [self.overrides[listener.port]] if listener.port in self.overrides else self.paths
            )
        elif learned == "":
            candidates = []
        else:
            candidates = [learned]
        statuses: list[str] = []
        for path in candidates:
            start = time.perf_counter()
            try:
                async with session.get(base + path, allow_redirects=False) as resp:
                    latency = (time.perf_counter() - start) * 1000
                    if resp.status < 400:
                        result.http_path = path
                        result.http_status = resp.status
                        result.http_latency_ms = latency
                        self._learned[key] = path
                        return
                    if learned and learned == path:
                        # A previously healthy endpoint now fails: report it.
                        result.http_path = path
                        result.http_status = resp.status
                        result.http_latency_ms = latency
                        return
                    statuses.append(str(resp.status))
            except TimeoutError:
                statuses.append("timeout")
            except (aiohttp.ClientError, OSError) as exc:
                statuses.append(type(exc).__name__)
        if learned is None:
            self._learned[key] = ""  # nothing conventional; stop looking
        if candidates:
            seen = ", ".join(sorted(set(statuses))) or "no answer"
            result.http_note = f"no health endpoint ({len(candidates)} paths tried: {seen})"
            self._notes[key] = result.http_note
        else:
            result.http_note = self._notes.get(key, "no health endpoint")

    def attach_cached(self, listeners: list[Listener]) -> None:
        """Re-attach the last result to fresh listener objects (no new checks)."""
        for lst in listeners:
            cached = self._cache.get(self._key(lst))
            if (
                cached is not None
                and lst.protocol == Protocol.TCP
                and lst.state == ListenerState.LISTEN
            ):
                lst.health = cached

    async def check(self, listeners: list[Listener]) -> None:
        """Check every TCP listener in ``listeners`` now (health paths are tried afresh)."""
        due: list[Listener] = []
        for lst in listeners:
            if lst.protocol != "TCP" or lst.state != ListenerState.LISTEN:
                lst.health = HealthResult()
                continue
            due.append(lst)
        if not due:
            return
        sem = asyncio.Semaphore(self.concurrency)
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        connector = aiohttp.TCPConnector(ssl=False, limit=self.concurrency, force_close=True)

        async def run(lst: Listener) -> None:
            async with sem:
                result = HealthResult(checked=True, checked_at=time.time())
                await self._tcp(self._host(lst), lst.port, result)
                if result.tcp_ok and lst.http.ok:
                    await self._http(session, lst, result)
                elif result.tcp_ok:
                    if not lst.http.attempted:
                        result.http_note = f"TCP only ({lst.identity.role}, not probed for HTTP)"
                    elif lst.http.error == "not HTTP":
                        result.http_note = "TCP only (not HTTP)"
                    else:
                        result.http_note = f"TCP only (HTTP: {lst.http.error or 'no answer'})"
                lst.health = result
                self._cache[self._key(lst)] = result

        async with aiohttp.ClientSession(
            timeout=timeout, connector=connector, headers={"User-Agent": "lirts-health"}
        ) as session:
            await asyncio.gather(*(run(lst) for lst in due), return_exceptions=True)
        live = {self._key(lst) for lst in listeners}
        for key in list(self._cache):
            if key not in live:
                del self._cache[key]
                self._learned.pop(key, None)
                self._notes.pop(key, None)
