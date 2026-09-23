"""Where was this process started from?

Walks the parent chain to the launching shell and the terminal application
above it, resolves the tmux window when the shell runs inside tmux, and reads
the git branch of the working directory.  Results are cached: a process's
ancestry does not change, and branches are re-read every 30 s at most.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

from lirts.constants import ORIGIN_BRANCH_CACHE_SECONDS, ORIGIN_TIMEOUT

log = logging.getLogger(__name__)

SUPERVISORS = {"launchd (system)", "systemd", "container runtime", "Docker Desktop", "ssh session"}
SHELLS = {
    "zsh",
    "bash",
    "fish",
    "sh",
    "dash",
    "nu",
    "pwsh",
    "tcsh",
    "ksh",
    "-zsh",
    "-bash",
    "login",
}
TERMINALS = {
    "iterm2": "iTerm2",
    "terminal": "Terminal.app",
    "wezterm-gui": "WezTerm",
    "wezterm": "WezTerm",
    "kitty": "kitty",
    "alacritty": "Alacritty",
    "ghostty": "Ghostty",
    "warp": "Warp",
    "hyper": "Hyper",
    "tmux": "tmux",
    "tmux: server": "tmux",
    "screen": "screen",
    "code": "VS Code",
    "code helper": "VS Code",
    "code helper (plugin)": "VS Code",
    "cursor": "Cursor",
    "cursor helper": "Cursor",
    "cursor helper (plugin)": "Cursor",
    "electron": "Electron app",
    "pycharm": "PyCharm",
    "idea": "IntelliJ IDEA",
    "webstorm": "WebStorm",
    "phpstorm": "PhpStorm",
    "goland": "GoLand",
    "sshd": "ssh session",
    "sshd-session": "ssh session",
    "launchd": "launchd (system)",
    "systemd": "systemd",
    "containerd-shim": "container runtime",
    "com.docker.backend": "Docker Desktop",
}


@dataclass
class Origin:
    shell: str | None = None
    shell_pid: int | None = None
    terminal: str | None = None
    tty: str | None = None
    tmux: str | None = None  # "session:window.pane (window name)"
    git_branch: str | None = None
    git_root: str | None = None
    supervisor: str | None = None  # launchd / systemd / docker when no interactive shell

    @property
    def interactive(self) -> bool:
        """True when a person started it from a shell / terminal (worth showing)."""
        return bool(self.shell or self.terminal or self.tmux or self.git_branch)

    @property
    def short(self) -> str:
        """Compact form for a table cell: terminal or shell, plus the branch."""
        where = self.terminal or self.shell or self.supervisor or ""
        if self.tmux:
            where = f"tmux {self.tmux.split(' ')[0]}"
        return f"{where} @{self.git_branch}" if self.git_branch else where

    @property
    def summary(self) -> str:
        parts: list[str] = []
        if self.terminal:
            parts.append(self.terminal)
        if self.tmux:
            parts.append(f"tmux {self.tmux}")
        if self.shell:
            parts.append(self.shell)
        if self.tty:
            parts.append(self.tty)
        if not parts and self.supervisor:
            parts.append(self.supervisor)
        text = " · ".join(parts) if parts else "unknown"
        if self.git_branch:
            text += f" · branch {self.git_branch}"
        return text


def _terminal_name(name: str) -> str | None:
    low = name.lower()
    if low in TERMINALS:
        return TERMINALS[low]
    for key, label in TERMINALS.items():
        if low.startswith(key):
            return label
    return None


def git_branch(
    cwd: str | None, cache: dict[str, tuple[float, tuple[str | None, str | None]]] | None = None
) -> tuple[str | None, str | None]:
    """Return ``(branch, repo_root)`` for a working directory, reading .git/HEAD directly."""
    if not cwd:
        return None, None
    now = time.time()
    if cache is not None:
        hit = cache.get(cwd)
        if hit and now - hit[0] < ORIGIN_BRANCH_CACHE_SECONDS:
            return hit[1]
    result: tuple[str | None, str | None] = (None, None)
    path = Path(cwd)
    for folder in [path, *path.parents]:
        git = folder / ".git"
        if not git.exists():
            continue
        head_file = git / "HEAD"
        if git.is_file():  # worktree: "gitdir: /path"
            with contextlib.suppress(OSError):
                target = git.read_text(encoding="utf-8").strip().removeprefix("gitdir: ")
                head_file = Path(target) / "HEAD"
        try:
            head = head_file.read_text(encoding="utf-8").strip()
        except OSError:
            break
        branch = head.removeprefix("ref: refs/heads/") if head.startswith("ref: ") else head[:10]
        result = (branch, str(folder))
        break
    if cache is not None:
        cache[cwd] = (now, result)
    return result


def tmux_windows() -> dict[str, str]:
    """Map pane tty -> "session:window.pane (window name)" for every tmux pane."""
    tmux = shutil.which("tmux")
    if not tmux:
        return {}
    try:
        result = subprocess.run(
            [
                tmux,
                "list-panes",
                "-a",
                "-F",
                "#{pane_tty}\t#{session_name}:#{window_index}.#{pane_index}\t#{window_name}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=ORIGIN_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    out: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            tty = parts[0].removeprefix("/dev/")
            out[tty] = f"{parts[1]} ({parts[2]})" if len(parts) > 2 and parts[2] else parts[1]
    return out


class OriginResolver:
    def __init__(self) -> None:
        self._cache: dict[int, Origin] = {}
        self._branches: dict[str, tuple[float, tuple[str | None, str | None]]] = {}
        self._tmux: dict[str, str] = {}
        self._tmux_at = 0.0

    def resolve(self, pid: int, cwd: str | None) -> Origin:
        origin = self._cache.get(pid)
        if origin is None:
            origin = self._walk(pid)
            self._cache[pid] = origin
        branch, root = git_branch(cwd, self._branches)
        origin.git_branch, origin.git_root = branch, root
        return origin

    def forget(self, live_pids: set[int]) -> None:
        for pid in list(self._cache):
            if pid not in live_pids:
                del self._cache[pid]

    def _tmux_map(self) -> dict[str, str]:
        if time.time() - self._tmux_at > 10:
            self._tmux = tmux_windows() if os.environ.get("TMUX") or shutil.which("tmux") else {}
            self._tmux_at = time.time()
        return self._tmux

    def _walk(self, pid: int) -> Origin:
        origin = Origin()
        try:
            proc = psutil.Process(pid)
            with contextlib.suppress(psutil.Error):
                origin.tty = proc.terminal().removeprefix("/dev/") if proc.terminal() else None
            chain = proc.parents()
        except psutil.Error:
            return origin
        for parent in chain[:12]:
            try:
                name = parent.name()
            except psutil.Error:
                break
            low = name.lower().lstrip("-")
            if origin.shell is None and low in SHELLS:
                origin.shell = low
                origin.shell_pid = parent.pid
                if origin.tty is None:
                    with contextlib.suppress(psutil.Error):
                        origin.tty = (
                            parent.terminal().removeprefix("/dev/") if parent.terminal() else None
                        )
                continue
            terminal = _terminal_name(name)
            if not terminal:
                continue
            if terminal in SUPERVISORS:
                # A supervisor above the chain: remember it, keep looking for a real terminal.
                origin.supervisor = origin.supervisor or terminal
                if terminal == "ssh session":
                    origin.terminal = terminal
                    break
                continue
            origin.terminal = terminal
            break
        if origin.tty:
            origin.tmux = self._tmux_map().get(origin.tty)
        return origin
