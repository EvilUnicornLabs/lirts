"""The ``lirts config`` sub-app: where the config lives, what it says, how to change it."""

from __future__ import annotations

from typing import Annotated, Any

import typer
import yaml

from lirts.cli.common import ConfigOpt, console, err_console
from lirts.config import (
    CONFIG_VERSION,
    default_config_path,
    load_config,
    outdated_reason,
    render_default_config,
    resolve_config_path,
    save_config,
    state_dir,
)
from lirts.config_view import ConfigView
from lirts.settings import coerce, setting_for

config_app = typer.Typer(help="Configuration helpers.")


@config_app.command("path")
def config_path(config: ConfigOpt = None) -> None:
    """Print the path of the configuration file in use."""
    path = resolve_config_path(config)
    console.print(f"{path}  ({'exists' if path.exists() else 'not created yet; defaults apply'})")
    console.print(f"state: {state_dir()}")


@config_app.command("show")
def config_show(config: ConfigOpt = None) -> None:
    """Print the effective configuration (defaults merged with the file)."""
    cfg = load_config(config)
    cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
    console.print(
        yaml.safe_dump(cfg, default_flow_style=False, sort_keys=False, allow_unicode=True)
    )


@config_app.command("init")
def config_init(
    config: ConfigOpt = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
) -> None:
    """Write a commented default configuration file."""
    path = resolve_config_path(config) if config else default_config_path()
    if path.exists() and not force:
        err_console.print(f"[yellow]{path} already exists (use --force to overwrite)[/]")
        raise typer.Exit(code=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_default_config(), encoding="utf-8")
    console.print(f"Wrote {path}")


@config_app.command("upgrade")
def config_upgrade(config: ConfigOpt = None) -> None:
    """Rewrite the config file for the current schema (drops keys that no longer exist)."""
    cfg = load_config(config)
    view = ConfigView(cfg)
    if not view.exists:
        console.print(f"No config file at {view.path}; nothing to upgrade.")
        return
    reason = outdated_reason(cfg)
    if not reason:
        console.print(f"{view.path} is already at schema {CONFIG_VERSION}.")
        return
    if save_config(cfg, config):
        console.print(f"Upgraded {view.path} to schema {CONFIG_VERSION} ({reason}).")
    else:
        raise typer.Exit(code=1)


def _parse_value(key: str, raw: str) -> Any:
    """``raw`` as the value the key holds, refusing what the settings registry rejects.

    A known setting is parsed by the registry, so the command and the Settings screen
    accept exactly the same text; YAML would read ``off`` as ``False`` and a column list
    as one string.  Free-form keys (aliases, health.overrides) stay YAML.
    """
    setting = setting_for(key)
    if setting is None:
        return yaml.safe_load(raw)
    try:
        return coerce(setting, raw)
    except ValueError as exc:
        err_console.print(f"[red]{key}: {exc}[/]")
        raise typer.Exit(code=1) from exc


@config_app.command("set")
def config_set(
    key: Annotated[
        str, typer.Argument(help="Dotted key, e.g. refresh_interval or http_probe.interval")
    ],
    value: Annotated[
        str, typer.Argument(help="Value, as the Settings screen takes it: 5, on, 'port, cpu'.")
    ],
    config: ConfigOpt = None,
) -> None:
    """Set one configuration value."""
    cfg = load_config(config)
    parsed = _parse_value(key, value)
    node: Any = cfg
    parts = key.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
        if not isinstance(node, dict):
            err_console.print(f"[red]{key}: '{part}' is not a section[/]")
            raise typer.Exit(code=1)
    node[parts[-1]] = parsed
    if save_config(cfg, config):
        console.print(f"Set {key} = {node[parts[-1]]!r} in {ConfigView(cfg).path}")
    else:
        raise typer.Exit(code=1)


@config_app.command("themes")
def config_themes() -> None:
    """List available colour themes."""
    # Kept lazy: Textual is a heavy import and this is the only command that needs it.
    from textual.theme import BUILTIN_THEMES

    console.print(", ".join(sorted(BUILTIN_THEMES)))
