"""Configuration loading, defaults, validation and persistence.

Resolution order for the config file:

1. an explicit path (``--config`` / :func:`load_config` argument)
2. the ``LIRTS_CONFIG`` environment variable
3. ``$XDG_CONFIG_HOME/lirts/config.yaml`` (``~/.config/lirts/config.yaml``)

A missing file simply yields the defaults.  A partial file is deep-merged over
the defaults so that every key is always present.
"""

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Any

import yaml

from lirts.config_defaults import (
    _FREE_FORM_SECTIONS,
    _LEGACY_COLUMN_SETS,
    CONFIG_VERSION,
    DEFAULT_CONFIG,
    INTEGER_KEYS,
    MINIMUMS,
    SOURCE_NAMES,
)

log = logging.getLogger(__name__)

APP_NAME = "lirts"

__all__ = [
    "APP_NAME",
    "CONFIG_VERSION",
    "DEFAULT_CONFIG",
    "config_dir",
    "deep_merge",
    "default_config_path",
    "default_for",
    "is_masked",
    "load_config",
    "mask_env",
    "outdated_reason",
    "render_default_config",
    "resolve_config_path",
    "save_config",
    "state_dir",
    "strip_obsolete",
    "validate",
]


def strip_obsolete(user: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return ``user`` without keys the current schema does not know, plus their dotted names.

    Nothing is written anywhere: the caller decides what to do with the file.
    """

    def walk(node: dict[str, Any], *, default: dict[str, Any], prefix: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in node.items():
            dotted = f"{prefix}{key}"
            if key == "version" and not prefix:
                continue
            if key not in default:
                dropped.append(dotted)
                continue
            if (
                isinstance(value, dict)
                and isinstance(default[key], dict)
                and dotted not in _FREE_FORM_SECTIONS
            ):
                out[key] = walk(value, default=default[key], prefix=dotted + ".")
            else:
                out[key] = value
        return out

    dropped: list[str] = []
    return walk(user, default=DEFAULT_CONFIG, prefix=""), dropped


def config_dir() -> Path:
    """The directory lirts keeps its configuration in (XDG_CONFIG_HOME)."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / APP_NAME


def state_dir() -> Path:
    """The directory lirts keeps history, patterns and the log in (XDG_STATE_HOME)."""
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / APP_NAME


def default_config_path() -> Path:
    """Where the config file lives when neither an argument nor LIRTS_CONFIG says otherwise."""
    return config_dir() / "config.yaml"


def resolve_config_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    """The config file to use: ``explicit``, else LIRTS_CONFIG, else the default path."""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("LIRTS_CONFIG")
    if env:
        return Path(env).expanduser()
    return default_config_path()


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Return ``base`` updated recursively with ``override`` (neither is mutated)."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def default_for(path: str) -> Any:
    """The value :data:`DEFAULT_CONFIG` gives for a dotted key."""
    node: Any = DEFAULT_CONFIG
    for part in path.split("."):
        node = node[part]
    return node


def _coerce_number(value: Any, *, path: str) -> float:
    """``value`` as a number no smaller than the key's minimum, or its default when unusable."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        num = float(default_for(path))
    num = max(num, MINIMUMS[path])
    return int(num) if path in INTEGER_KEYS else num


def validate(config: dict[str, Any]) -> dict[str, Any]:
    """Coerce obviously wrong values back to something usable."""
    cfg = copy.deepcopy(config)
    cfg["refresh_interval"] = _coerce_number(cfg.get("refresh_interval"), path="refresh_interval")
    if not isinstance(cfg.get("aliases"), dict):
        cfg["aliases"] = {}
    if not isinstance(cfg.get("columns"), list) or not cfg["columns"]:
        cfg["columns"] = list(DEFAULT_CONFIG["columns"])
    cfg["columns"] = [str(c).lower() for c in cfg["columns"]]
    if cfg["columns"] in _LEGACY_COLUMN_SETS:
        # A config written by an older `config init`; upgrade to the current default.
        cfg["columns"] = list(DEFAULT_CONFIG["columns"])
    raw_sources = cfg.get("sources")
    sources = (
        [str(s).lower() for s in raw_sources if str(s).lower() in SOURCE_NAMES]
        if isinstance(raw_sources, list)
        else []
    )
    cfg["sources"] = sources or list(SOURCE_NAMES)
    if not isinstance(cfg.get("mask_env_vars"), list):
        cfg["mask_env_vars"] = list(DEFAULT_CONFIG["mask_env_vars"])
    probe = cfg.setdefault("http_probe", {})
    probe["interval"] = _coerce_number(probe.get("interval"), path="http_probe.interval")
    probe["timeout"] = _coerce_number(probe.get("timeout"), path="http_probe.timeout")
    for key in ("skip_ports", "force_ports"):
        ports: list[int] = []
        for p in probe.get(key) or []:
            try:
                ports.append(int(p))
            except (TypeError, ValueError):
                continue
        probe[key] = ports
    hist = cfg.setdefault("history", {})
    hist["window"] = _coerce_number(hist.get("window"), path="history.window")
    hist["retention_hours"] = _coerce_number(
        hist.get("retention_hours"), path="history.retention_hours"
    )
    ins = cfg.setdefault("insights", {})
    for key in (
        "stale_after_hours",
        "restart_warning",
        "show_stopped_minutes",
        "highlight_new_minutes",
        "pattern_days",
        "pattern_min",
    ):
        ins[key] = _coerce_number(ins.get(key), path=f"insights.{key}")
    cfg.pop("version", None)
    return cfg


def load_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Load the configuration, merged over :data:`DEFAULT_CONFIG`."""
    resolved = resolve_config_path(path)
    user: dict[str, Any] = {}
    if resolved.exists():
        try:
            with open(resolved, encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
            if isinstance(loaded, dict):
                user = loaded
            else:
                log.warning("Config %s is not a mapping; using defaults", resolved)
        except (OSError, yaml.YAMLError) as exc:
            log.warning("Could not read config %s: %s", resolved, exc)
    known, obsolete = strip_obsolete(user)
    if obsolete:
        log.warning(
            "Config %s: ignoring keys not in schema %s: %s", resolved, CONFIG_VERSION, obsolete
        )
    cfg = validate(deep_merge(DEFAULT_CONFIG, known))
    cfg["_path"] = str(resolved)
    cfg["_exists"] = resolved.exists()
    cfg["_obsolete_keys"] = obsolete
    cfg["_file_version"] = (
        int(user.get("version") or 0)
        if isinstance(user.get("version", 0), int | float | str)
        and str(user.get("version", "0")).isdigit()
        else 0
    )
    return cfg


def outdated_reason(config: dict[str, Any]) -> str | None:
    """Why the loaded file needs a manual upgrade, or None when it is current."""
    if not config.get("_exists"):
        return None
    reasons = []
    file_version = int(config.get("_file_version") or 0)
    if file_version < CONFIG_VERSION:
        reasons.append(f"schema {file_version} < {CONFIG_VERSION}")
    obsolete = list(config.get("_obsolete_keys") or [])
    if obsolete:
        reasons.append("ignored keys: " + ", ".join(obsolete))
    return "; ".join(reasons) or None


def save_config(config: dict[str, Any], path: str | os.PathLike[str] | None = None) -> bool:
    """Write the configuration (minus private keys) to disk.  Only called on the user's request."""
    target = Path(path).expanduser() if path else Path(config.get("_path") or default_config_path())
    data = {"version": CONFIG_VERSION}
    data.update({k: v for k, v in config.items() if not k.startswith("_") and k != "version"})
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)
        return True
    except OSError as exc:
        log.warning("Could not save config to %s: %s", target, exc)
        return False


def render_default_config() -> str:
    """The default configuration as commented YAML, for ``lirts config init``."""
    header = (
        "# lirts configuration\n"
        "# Every key is optional; missing keys fall back to these defaults.\n"
        "# Docs: README.md -> Configuration\n\n"
    )
    body = {"version": CONFIG_VERSION, **DEFAULT_CONFIG}
    return header + yaml.safe_dump(
        body, default_flow_style=False, sort_keys=False, allow_unicode=True
    )


def is_masked(name: str, patterns: list[str]) -> bool:
    """True when an environment variable name contains one of the patterns."""
    upper = name.upper()
    return any(p.upper() in upper for p in patterns)


def mask_env(env: dict[str, str], patterns: list[str]) -> dict[str, str]:
    """The environment with the value of every matching name replaced by stars."""
    return {k: ("********" if is_masked(k, patterns) else v) for k, v in env.items()}
