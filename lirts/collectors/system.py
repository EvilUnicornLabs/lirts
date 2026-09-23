"""Machine-wide CPU, memory and network throughput for the top bar."""

from __future__ import annotations

import logging
import socket
import time
from collections import deque

import psutil

from lirts.constants import ADDRESS_CACHE_SECONDS, SYSTEM_HISTORY_SAMPLES
from lirts.models import SystemStats

log = logging.getLogger(__name__)

_GB = 1024**3
# Interfaces that never carry the machine's own traffic.
_IGNORED_INTERFACE_PREFIXES = (
    "lo",
    "utun",
    "awdl",
    "llw",
    "bridge",
    "docker",
    "veth",
    "gif",
    "stf",
)
# The usual primary interface names, preferred over anything else that is up.
_PREFERRED_INTERFACES = ("en0", "eth0", "wlan0")
# Shortest gap accepted between two throughput samples, to keep the division sane.
_MIN_SAMPLE_GAP = 1e-3


class SystemSampler:
    """Machine-wide CPU, memory and throughput, with a rolling history per metric."""

    def __init__(self, history: int = SYSTEM_HISTORY_SAMPLES) -> None:
        self._cpu: deque[float] = deque(maxlen=history)
        self._mem: deque[float] = deque(maxlen=history)
        self._up: deque[float] = deque(maxlen=history)
        self._down: deque[float] = deque(maxlen=history)
        self._last_io: tuple[float, int, int] | None = None
        self._addr: tuple[str, str] | None = None
        self._addr_at = 0.0
        psutil.cpu_percent(interval=None)  # prime the delta

    def own_address(self, now: float | None = None) -> tuple[str, str]:
        """(address, interface) of the primary interface; cached for a minute."""
        now = now or time.time()
        if self._addr is not None and now - self._addr_at < ADDRESS_CACHE_SECONDS:
            return self._addr
        found: tuple[str, str] | None = None
        try:
            stats = psutil.net_if_stats()
            candidates: list[tuple[int, str, str]] = []
            for name, addrs in psutil.net_if_addrs().items():
                st = stats.get(name)
                if st is not None and not st.isup:
                    continue
                if name.startswith(_IGNORED_INTERFACE_PREFIXES):
                    continue
                for a in addrs:
                    if a.family == socket.AF_INET and not a.address.startswith(
                        ("127.", "169.254.")
                    ):
                        rank = 0 if name in _PREFERRED_INTERFACES else 1
                        candidates.append((rank, name, a.address))
            if candidates:
                candidates.sort()
                found = (candidates[0][2], candidates[0][1])
        except (OSError, AttributeError):  # psutil's interface APIs differ per platform
            found = None
        self._addr = found or ("-", "-")
        self._addr_at = now
        return self._addr

    def sample(self) -> SystemStats:
        """One machine-wide sample, appended to the rolling histories."""
        now = time.time()
        cpu = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        up_bps = down_bps = 0.0
        sent = recv = 0
        try:
            io = psutil.net_io_counters()
            sent, recv = io.bytes_sent, io.bytes_recv
            if self._last_io is not None:
                dt = max(now - self._last_io[0], _MIN_SAMPLE_GAP)
                up_bps = max(0.0, (io.bytes_sent - self._last_io[1]) / dt)
                down_bps = max(0.0, (io.bytes_recv - self._last_io[2]) / dt)
            self._last_io = (now, io.bytes_sent, io.bytes_recv)
        except (OSError, AttributeError):  # net counters are missing on some platforms
            log.debug("net_io_counters unavailable", exc_info=True)
        self._cpu.append(cpu)
        self._mem.append(vm.percent)
        self._up.append(up_bps)
        self._down.append(down_bps)
        try:
            load = psutil.getloadavg()
        except (AttributeError, OSError):
            load = None
        return SystemStats(
            cpu_percent=cpu,
            mem_percent=vm.percent,
            mem_used_gb=(vm.total - vm.available) / _GB,
            mem_total_gb=vm.total / _GB,
            net_up_bps=up_bps,
            net_down_bps=down_bps,
            cpu_history=list(self._cpu),
            mem_history=list(self._mem),
            net_up_history=list(self._up),
            net_down_history=list(self._down),
            load_avg=load,
            net_bytes_sent=sent,
            net_bytes_recv=recv,
            address=self.own_address(now)[0],
            interface=self.own_address(now)[1],
        )
