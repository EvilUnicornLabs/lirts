"""Best-effort per-process network throughput, and (opt-in) per-connection throughput.

There is no portable, unprivileged way to attribute bytes to a process.  On
macOS ``nettop`` can report cumulative per-process byte counters, so a
background thread runs it periodically and derives rates from the deltas.  On
other platforms the sampler simply reports nothing and the UI falls back to
connection-count based activity.
"""

from __future__ import annotations

import logging
import platform
import re
import shutil
import subprocess
import threading
import time

from lirts.constants import BANDWIDTH_COMMAND_GRACE

log = logging.getLogger(__name__)

# Sampling never runs faster than this, and the loop never sleeps for less.
_MIN_BANDWIDTH_INTERVAL = 1.0
_MIN_BANDWIDTH_SLEEP = 0.5


def parse_nettop_csv(text: str) -> dict[int, tuple[int, int]]:
    """Parse ``nettop -P -x -L 1 -J bytes_in,bytes_out`` output into ``{pid: (in, out)}``."""
    result: dict[int, tuple[int, int]] = {}
    for line in text.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 3 or parts[0] in ("", "time"):
            continue
        name = parts[0]
        if name.startswith(","):
            continue
        # header line looks like ",bytes_in,bytes_out,"
        if name.endswith(".") or "." not in name:
            continue
        pid_text = name.rsplit(".", 1)[-1]
        try:
            pid = int(pid_text)
            bytes_in = int(parts[1] or 0)
            bytes_out = int(parts[2] or 0)
        except ValueError:
            continue
        result[pid] = (bytes_in, bytes_out)
    return result


FlowKey = tuple[int, int, int]  # (pid, local port, remote port)


def _port_of_endpoint(text: str) -> int | None:
    """Port of ``127.0.0.1:8080`` / ``::1.8080`` / ``*:*`` style endpoints (nettop, ss)."""
    text = text.strip()
    if text.endswith("*"):
        return None
    for sep in (":", "."):
        _, s, tail = text.rpartition(sep)
        if s and tail.isdigit():
            return int(tail)
    return None


def parse_nettop_connections(text: str) -> dict[FlowKey, tuple[int, int]]:
    """``nettop -x -L 1 -J bytes_in,bytes_out`` (without -P) → ``{(pid, lport, rport): (in, out)}``.

    Process lines (``name.pid,in,out``) set the owner of the connection lines that follow
    (``tcp4 127.0.0.1:55989<->127.0.0.1:8080,in,out``).
    """
    flows: dict[FlowKey, tuple[int, int]] = {}
    pid: int | None = None
    for line in text.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 3 or parts[0] in ("", "time"):
            continue
        head = parts[0]
        if head.startswith(("tcp", "udp", "quic")):
            if pid is None or "<->" not in head:
                continue
            endpoints = head.split(" ", 1)[1] if " " in head else ""
            local, _, remote = endpoints.partition("<->")
            lport, rport = _port_of_endpoint(local), _port_of_endpoint(remote)
            if lport is None or rport is None:
                continue
            try:
                flows[(pid, lport, rport)] = (int(parts[1] or 0), int(parts[2] or 0))
            except ValueError:
                continue
            continue
        tail = head.rsplit(".", 1)[-1]
        pid = int(tail) if tail.isdigit() else None
    return flows


def parse_ss_connections(text: str) -> dict[FlowKey, tuple[int, int]]:
    """``ss -tinpH`` output → ``{(pid, lport, rport): (bytes_received, bytes_acked)}`` (Linux)."""
    flows: dict[FlowKey, tuple[int, int]] = {}
    current: FlowKey | None = None
    for line in text.splitlines():
        if not line.startswith((" ", "\t")):
            current = None
            fields = line.split()
            if len(fields) < 5 or fields[0] != "ESTAB":
                continue
            lport, rport = _port_of_endpoint(fields[3]), _port_of_endpoint(fields[4])
            m = re.search(r"pid=(\d+)", line)
            if lport is None or rport is None or not m:
                continue
            current = (int(m.group(1)), lport, rport)
            continue
        if current is None:
            continue
        received = re.search(r"bytes_received:(\d+)", line)
        acked = re.search(r"bytes_acked:(\d+)", line)
        if received or acked:
            flows[current] = (
                int(received.group(1)) if received else 0,
                int(acked.group(1)) if acked else 0,
            )
    return flows


class BandwidthSampler:
    """Background sampler; ``rates()`` returns ``{pid: (in_bps, out_bps)}``."""

    def __init__(
        self, mode: str = "auto", interval: float = 5.0, connections: bool = False
    ) -> None:
        self.interval = max(_MIN_BANDWIDTH_INTERVAL, interval)
        system = platform.system()
        self._nettop = shutil.which("nettop") if system == "Darwin" else None
        self._ss = shutil.which("ss") if system == "Linux" else None
        self.enabled = mode != "off" and (self._nettop is not None or self._ss is not None)
        # Per-connection sampling is opt-in: one more command per sample when on.
        self.connections = bool(connections)
        self._lock = threading.Lock()
        self._rates: dict[int, tuple[float, float]] = {}
        self._last: dict[int, tuple[float, int, int]] = {}
        self._flow_rates: dict[FlowKey, tuple[float, float, int, int]] = {}
        self._flow_last: dict[FlowKey, tuple[float, int, int]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def available(self) -> bool:
        return self.enabled

    def start(self) -> None:
        """Start the background sampling thread, if a backend is available."""
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="lirts-bandwidth", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Ask the background thread to finish."""
        self._stop.set()

    def rates(self) -> dict[int, tuple[float, float]]:
        """``{pid: (in_bps, out_bps)}`` from the last sample."""
        with self._lock:
            return dict(self._rates)

    def flows(self) -> dict[FlowKey, tuple[float, float, int, int]]:
        """``{(pid, lport, rport): (in_bps, out_bps, in_total, out_total)}`` when ``connections`` is on."""
        with self._lock:
            return dict(self._flow_rates)

    @property
    def flows_supported(self) -> bool:
        return self._nettop is not None or self._ss is not None

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.time()
            try:
                self._sample_once()
            except Exception as exc:  # the thread must survive whatever nettop / ss does
                log.debug("bandwidth sample failed: %s", exc)
            elapsed = time.time() - started
            self._stop.wait(max(_MIN_BANDWIDTH_SLEEP, self.interval - elapsed))

    def _run(self, cmd: list[str]) -> str | None:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.interval + BANDWIDTH_COMMAND_GRACE,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("%s failed: %s", cmd[0], exc)
            return None
        if result.returncode != 0:
            log.debug("%s exited %s: %s", cmd[0], result.returncode, result.stderr[:200])
            return None
        return result.stdout

    def _sample_once(self) -> None:
        now = time.time()
        if self._nettop:
            out = self._run([self._nettop, "-P", "-x", "-L", "1", "-J", "bytes_in,bytes_out"])
            if out is None:
                self.enabled = False
                return
            self.ingest(parse_nettop_csv(out), now)
        if not self.connections:
            if self._flow_rates:
                with self._lock:
                    self._flow_rates = {}
                self._flow_last = {}
            return
        if self._nettop:
            out = self._run(
                [self._nettop, "-t", "loopback", "-x", "-L", "1", "-J", "bytes_in,bytes_out"]
            )
            if out is not None:
                self.ingest_flows(parse_nettop_connections(out), now)
        elif self._ss:
            out = self._run([self._ss, "-tinpH"])
            if out is not None:
                self.ingest_flows(parse_ss_connections(out), now)

    def ingest_flows(self, counters: dict[FlowKey, tuple[int, int]], now: float) -> None:
        """Turn cumulative per-connection counters into rates and totals."""
        rates: dict[FlowKey, tuple[float, float, int, int]] = {}
        for key, (bin_, bout) in counters.items():
            prev = self._flow_last.get(key)
            if prev is not None:
                dt = now - prev[0]
                if dt > 0 and bin_ >= prev[1] and bout >= prev[2]:
                    rates[key] = ((bin_ - prev[1]) / dt, (bout - prev[2]) / dt, bin_, bout)
            else:
                rates[key] = (0.0, 0.0, bin_, bout)
            self._flow_last[key] = (now, bin_, bout)
        for key in list(self._flow_last):
            if key not in counters:
                del self._flow_last[key]
        with self._lock:
            self._flow_rates = rates

    def ingest(self, counters: dict[int, tuple[int, int]], now: float) -> None:
        """Turn cumulative per-process counters into rates."""
        rates: dict[int, tuple[float, float]] = {}
        for pid, (bin_, bout) in counters.items():
            prev = self._last.get(pid)
            if prev is not None:
                dt = now - prev[0]
                if dt > 0 and bin_ >= prev[1] and bout >= prev[2]:
                    rates[pid] = ((bin_ - prev[1]) / dt, (bout - prev[2]) / dt)
            self._last[pid] = (now, bin_, bout)
        for pid in list(self._last):
            if pid not in counters:
                del self._last[pid]
        with self._lock:
            self._rates = rates
