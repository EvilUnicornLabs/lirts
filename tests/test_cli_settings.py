"""Round trip for every entry of the settings registry.

Each setting is written with ``lirts config set``, read back through
:func:`lirts.config.load_config`, and the same value is put through the registry's
own :func:`lirts.settings.coerce` so the two ways of editing a setting agree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from lirts import cli
from lirts.config import load_config
from lirts.settings import SETTINGS, coerce, format_value, get_path

runner = CliRunner()

# path -> (the text both `lirts config set` and the Settings screen take, the stored value)
CASES: dict[str, tuple[str, Any]] = {
    "theme": ("nord", "nord"),
    "refresh_interval": ("5", 5.0),
    "sources": ("local, ssh", ["local", "ssh"]),
    "columns": ("port, cpu", ["port", "cpu"]),
    "editor": ("cursor", "cursor"),
    "group_by_stack": ("on", True),
    "side_panel": ("off", False),
    "net_panel": ("off", False),
    "show_udp": ("yes", True),
    "hide_system": ("on", True),
    "insights.highlight_new_minutes": ("7", 7.0),
    "insights.show_stopped_minutes": ("9", 9.0),
    "insights.pattern_days": ("14", 14),
    "topology.days": ("7", 7),
    "ui.style": ("compact", "compact"),
    "ui.layout": ("hosts", "hosts"),
    "ui.glyphs": ("braille", "braille"),
    "ui.rounded_corners": ("off", False),
    "ui.row_icons": ("off", False),
    "ui.history_panel": ("on", True),
    "ui.clock": ("%H:%M", "%H:%M"),
    "ui.truecolor": ("on", "on"),
    "cli.force_color": ("on", True),
    "topology.usual_days": ("2", 2),
    "insights.stale_after_hours": ("2.5", 2.5),
    "insights.restart_warning": ("5", 5),
    "thresholds.cpu.yellow": ("30", 30.0),
    "thresholds.cpu.red": ("90", 90.0),
    "thresholds.memory_mb.yellow": ("600", 600.0),
    "thresholds.memory_mb.red": ("1200", 1200.0),
    "http_probe.enabled": ("off", False),
    "http_probe.interval": ("20", 20.0),
    "proxies.enabled": ("off", False),
    "docker.enabled": ("off", False),
    "docker.show_unpublished": ("on", True),
    "kubernetes.enabled": ("false", False),
    "kubernetes.namespace": ("dev", "dev"),
    "kubernetes.all_namespaces": ("on", True),
    "bandwidth.mode": ("off", "off"),
    "bandwidth.connections": ("on", True),
    "history.persist": ("off", False),
    "daemon.idle_interval": ("30", 30.0),
}


def test_every_setting_has_a_round_trip_case() -> None:
    assert {s.path for s in SETTINGS} == set(CASES)


@pytest.mark.parametrize("path", sorted(CASES), ids=sorted(CASES))
def test_setting_round_trips_through_config_set(path: str, tmp_path: Path) -> None:
    target = tmp_path / "c.yaml"
    written, expected = CASES[path]

    result = runner.invoke(cli.app, ["config", "set", path, written, "--config", str(target)])

    assert result.exit_code == 0, result.output
    assert get_path(load_config(target), path) == expected


@pytest.mark.parametrize("path", sorted(CASES), ids=sorted(CASES))
def test_setting_is_accepted_by_the_registry(path: str) -> None:
    setting = next(s for s in SETTINGS if s.path == path)
    typed, expected = CASES[path]

    value = coerce(setting, typed)

    assert value == expected
    assert format_value(setting, value)


def test_setting_below_its_minimum_is_refused() -> None:
    setting = next(s for s in SETTINGS if s.path == "refresh_interval")
    with pytest.raises(ValueError, match=r"minimum is 0\.5"):
        coerce(setting, "0.1")


def test_config_set_refuses_a_value_below_the_minimum(tmp_path: Path) -> None:
    target = tmp_path / "c.yaml"

    result = runner.invoke(
        cli.app, ["config", "set", "refresh_interval", "0.1", "--config", str(target)]
    )

    assert result.exit_code == 1
    assert "refresh_interval: minimum is 0.5" in result.output
    assert not target.exists()  # nothing is written when the value is refused


def test_config_set_refuses_an_unknown_column(tmp_path: Path) -> None:
    target = tmp_path / "c.yaml"

    result = runner.invoke(
        cli.app, ["config", "set", "columns", "port, bogus", "--config", str(target)]
    )

    assert result.exit_code == 1
    assert "unknown column(s): bogus" in result.output


def test_config_set_keeps_yaml_for_keys_the_registry_does_not_know(tmp_path: Path) -> None:
    target = tmp_path / "c.yaml"

    result = runner.invoke(
        cli.app, ["config", "set", "health.overrides.8080", "/internal", "--config", str(target)]
    )

    assert result.exit_code == 0, result.output
    assert load_config(target)["health"]["overrides"] == {"8080": "/internal"}


def test_a_hand_edited_value_below_the_minimum_is_pulled_back_on_load(tmp_path: Path) -> None:
    target = tmp_path / "c.yaml"
    target.write_text("refresh_interval: 0.1\n")

    assert load_config(target)["refresh_interval"] == 0.5
