"""Record what lirts sees and play it back anywhere.

``lirts record 5m -o machine.jsonl`` runs the real engine and writes one
snapshot per refresh as a JSON line; ``lirts --replay machine.jsonl`` runs
the dashboard over those frames instead of the live collectors.  Frames
carry everything the UI shows (rows, containers, edges, routes, Kubernetes
state, events, system numbers), so a recording from another machine looks
exactly like sitting at it.  Actions are disabled while replaying.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import dataclasses
import json
import shutil
import tempfile
import time
import types
import typing
from pathlib import Path
from typing import Any

from lirts import __version__
from lirts.collectors.kube import KubeDeployment, KubePod, KubeService, KubeState
from lirts.collectors.origin import Origin
from lirts.collectors.proxies import Route
from lirts.constants import (
    KILL_WAIT_SECONDS,
    MIN_REFRESH_INTERVAL,
    RECORD_MIN_SLEEP,
    SECONDS_PER_HOUR,
    SECONDS_PER_MINUTE,
)
from lirts.engine import Engine
from lirts.models import ContainerInfo, Event, Listener, Snapshot, SystemStats

# Fields typed ``Any`` in the models and the class behind them.
_ANY_FIELDS: dict[tuple[str, str], Any] = {
    ("ListenerProcess", "origin"): Origin,
    ("Snapshot", "kube"): KubeState,
    ("Snapshot", "routes"): Route,  # list items
}


def to_jsonable(obj: Any) -> Any:
    """Dataclasses, tuples, deques and sets → plain JSON values."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set | frozenset) or type(obj).__name__ == "deque":
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
        return None
    return obj


def _from_hint(hint: Any, *, value: Any, owner: str = "", name: str = "") -> Any:
    if value is None:
        return None
    origin = typing.get_origin(hint)
    if hint is Any:
        cls = _ANY_FIELDS.get((owner, name))
        if cls is None:
            return value
        if isinstance(value, list):
            return [from_dict(cls, v) for v in value]
        return from_dict(cls, value) if isinstance(value, dict) else value
    if origin is types.UnionType or origin is typing.Union:
        members = [a for a in typing.get_args(hint) if a is not type(None)]
        return _from_hint(members[0], value=value, owner=owner, name=name) if members else value
    if origin in (list, set, frozenset) or hint is list:
        (item,) = typing.get_args(hint) or (Any,)
        return [_from_hint(item, value=v, owner=owner, name=name) for v in value]
    if origin is tuple or hint is tuple:
        targs = typing.get_args(hint)
        if targs and targs[-1] is not Ellipsis and len(targs) == len(value):
            return tuple(
                _from_hint(a, value=v, owner=owner, name=name)
                for a, v in zip(targs, value, strict=True)
            )
        item = targs[0] if targs else Any
        return tuple(_from_hint(item, value=v, owner=owner, name=name) for v in value)
    if origin is dict or hint is dict:
        dargs = typing.get_args(hint)
        key_t, val_t = dargs if len(dargs) == 2 else (Any, Any)
        out: dict[Any, Any] = {}
        for k, v in value.items():
            key: Any = k
            if key_t is int:
                with contextlib.suppress(TypeError, ValueError):
                    key = int(k)
            out[key] = _from_hint(val_t, value=v, owner=owner, name=name)
        return out
    if isinstance(hint, type) and dataclasses.is_dataclass(hint):
        return from_dict(hint, value) if isinstance(value, dict) else value
    if hint is float and isinstance(value, int | float):
        return float(value)
    return value


def from_dict(cls: Any, data: dict[str, Any]) -> Any:
    """Rebuild a dataclass (recursively) from what :func:`to_jsonable` produced."""
    hints = typing.get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        kwargs[f.name] = _from_hint(
            hints.get(f.name, Any), value=data[f.name], owner=cls.__name__, name=f.name
        )
    return cls(**kwargs)


def snapshot_to_dict(snapshot: Snapshot) -> dict[str, Any]:
    """One recorded frame as plain JSON values."""
    return to_jsonable(snapshot)


def snapshot_from_dict(data: dict[str, Any]) -> Snapshot:
    """Rebuild a snapshot from what :func:`snapshot_to_dict` produced."""
    snap: Snapshot = from_dict(Snapshot, data)
    snap.patterns = [(str(p[0]), str(p[1]), p[2]) for p in snap.patterns if len(p) == 3]
    return snap


# ----- files ---------------------------------------------------------------------------


def write_header(fh: Any, interval: float) -> None:
    """Write the first line of a recording: version, time and refresh interval."""
    fh.write(
        json.dumps({"lirts": __version__, "recorded_at": time.time(), "interval": interval}) + "\n"
    )


def write_frame(fh: Any, snapshot: Snapshot) -> None:
    """Append one snapshot to a recording as a JSON line."""
    fh.write(json.dumps({"t": snapshot.timestamp, "snapshot": snapshot_to_dict(snapshot)}) + "\n")


def read_recording(path: str | Path) -> tuple[dict[str, Any], list[Snapshot]]:
    """(header, frames) of a recording; raises ValueError when the file is not one."""
    header: dict[str, Any] = {}
    frames: list[Snapshot] = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {n + 1} is not JSON: {exc}") from exc
            if "snapshot" in raw:
                frames.append(snapshot_from_dict(raw["snapshot"]))
            elif "lirts" in raw:
                header = raw
    if not frames:
        raise ValueError("no frames in the recording")
    return header, frames


def record(
    engine: Engine, *, seconds: float, interval: float, out: str | Path, progress: Any = None
) -> int:
    """Refresh ``engine`` every ``interval`` seconds for ``seconds`` and write the frames."""
    frames = 0
    deadline = time.time() + seconds
    with open(out, "w", encoding="utf-8") as fh:
        write_header(fh, interval)
        while True:
            started = time.time()
            snapshot = engine.refresh_sync()
            write_frame(fh, snapshot)
            frames += 1
            if progress is not None:
                progress(frames, snapshot)
            if time.time() >= deadline:
                break
            time.sleep(max(RECORD_MIN_SLEEP, interval - (time.time() - started)))
    return frames


def parse_duration(text: str) -> float:
    """``30``, ``30s``, ``5m``, ``1h`` → seconds."""
    text = text.strip().lower()
    units = {"s": 1.0, "m": float(SECONDS_PER_MINUTE), "h": float(SECONDS_PER_HOUR)}
    if text and text[-1] in units:
        return float(text[:-1]) * units[text[-1]]
    return float(text)


# ----- engine --------------------------------------------------------------------------


class ReplayEngine(Engine):
    """Plays recorded frames through the dashboard; every action is refused."""

    def __init__(self, config: dict[str, Any], path: str | Path, loop: bool = True) -> None:
        self._tmp = tempfile.mkdtemp(prefix="lirts-replay-")
        cfg = copy.deepcopy(config)
        cfg.setdefault("docker", {})["enabled"] = False
        cfg.setdefault("kubernetes", {})["enabled"] = "off"
        cfg.setdefault("bandwidth", {})["mode"] = "off"
        cfg.setdefault("http_probe", {})["enabled"] = False
        cfg.setdefault("proxies", {})["enabled"] = False
        cfg.setdefault("history", {})["persist"] = False
        cfg["_replay"] = str(path)
        super().__init__(cfg, state_root=Path(self._tmp))
        self.header, self.frames = read_recording(path)
        self.loop = loop
        self.index = -1
        self.replay = True
        if self.header.get("interval"):
            config["refresh_interval"] = max(MIN_REFRESH_INTERVAL, float(self.header["interval"]))

    @property
    def position(self) -> str:
        """``replay 3/120``: which frame is on screen."""
        return f"replay {max(self.index, 0) + 1}/{len(self.frames)}"

    def start(self) -> None:
        """Nothing to start: a recording has no collectors."""
        self._started = True

    def close(self) -> None:
        """Remove the throwaway state directory of this replay."""
        shutil.rmtree(self._tmp, ignore_errors=True)

    async def refresh(self) -> Snapshot:
        """Advance to the next recorded frame (looping when ``loop`` is set)."""
        if self.index + 1 >= len(self.frames):
            if not self.loop:
                return self.snapshot
            self.index = -1
        self.index += 1
        frame = self.frames[self.index]
        seen = {(e.timestamp, e.message) for e in self.history.events}
        for ev in frame.events:
            if (ev.timestamp, ev.message) not in seen:
                self.history.events.append(Event(ev.timestamp, ev.level, ev.message, ev.port))
        containers: list[ContainerInfo] = []
        seen_ids: set[str] = set()
        for x in frame.listeners:
            if x.container is not None and x.container.id not in seen_ids:
                seen_ids.add(x.container.id)
                containers.append(x.container)
        self.containers = containers
        self.routes = list(frame.routes)
        self.snapshot = frame
        if self.last_session_diff is None:
            self.last_session_diff = {}
        return frame

    def refresh_sync(self) -> Snapshot:
        """Advance one frame from synchronous code."""
        return asyncio.run(self.refresh())

    # actions are meaningless on a recording
    def _refuse(self) -> tuple[bool, str]:
        return False, "replay mode: actions are disabled"

    def kill(self, pids: list[int], force: bool = False) -> list[tuple[int, bool, str]]:
        return [(pid, False, "replay mode: actions are disabled") for pid in pids]

    def kill_and_wait(
        self, pids: list[int], *, force: bool = False, timeout: float = KILL_WAIT_SECONDS
    ) -> list[tuple[int, bool, str]]:
        return self.kill(pids, force)

    def restart_process(self, listener: Listener, force: bool = False) -> tuple[bool, str]:
        return self._refuse()

    async def check_health_now(self, listeners: list[Listener] | None = None) -> list[Listener]:
        return listeners if listeners is not None else list(self.snapshot.listeners)

    def open_path(self, path: str) -> tuple[bool, str]:
        return False, f"replay mode: would open {path}"

    def stack_action(self, project: str, action: str) -> list[tuple[str, bool, str]]:
        return [(project, False, "replay mode: actions are disabled")]

    def stack_logs(self, project: str, tail: int = 100) -> str:
        return "replay mode: logs are not recorded"

    def refresh_routes(self) -> None:
        return None


__all__ = [
    "ContainerInfo",
    "KubeDeployment",
    "KubePod",
    "KubeService",
    "ReplayEngine",
    "SystemStats",
    "from_dict",
    "parse_duration",
    "read_recording",
    "record",
    "snapshot_from_dict",
    "snapshot_to_dict",
    "to_jsonable",
    "write_frame",
    "write_header",
]
