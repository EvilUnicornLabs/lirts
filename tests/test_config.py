from __future__ import annotations

from pathlib import Path

import yaml

from lirts import config as cfgmod
from lirts.config import (
    DEFAULT_CONFIG,
    deep_merge,
    default_config_path,
    is_masked,
    load_config,
    mask_env,
    render_default_config,
    resolve_config_path,
    save_config,
    validate,
)


def test_defaults_when_file_missing() -> None:
    cfg = load_config()
    assert cfg["refresh_interval"] == DEFAULT_CONFIG["refresh_interval"]
    assert cfg["thresholds"]["cpu"]["red"] == 80.0
    assert cfg["_path"] == str(default_config_path())
    assert not Path(cfg["_path"]).exists()


def test_partial_file_is_merged(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        "refresh_interval: 5\nthresholds:\n  cpu:\n    red: 95\naliases:\n  3000: Frontend\n"
    )
    cfg = load_config(path)
    assert cfg["refresh_interval"] == 5.0
    assert cfg["thresholds"]["cpu"]["red"] == 95
    assert cfg["thresholds"]["cpu"]["yellow"] == 40.0  # default kept
    assert cfg["thresholds"]["memory_mb"]["red"] == 1000.0
    assert cfg["aliases"] == {3000: "Frontend"}
    assert cfg["http_probe"]["enabled"] is True


def test_invalid_values_are_coerced(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        "refresh_interval: fast\ncolumns: []\naliases: nope\nhttp_probe:\n  skip_ports: [22, 'x', 80]\n"
    )
    cfg = load_config(path)
    assert cfg["refresh_interval"] == 2.0
    assert cfg["columns"] == DEFAULT_CONFIG["columns"]
    assert cfg["aliases"] == {}
    assert cfg["http_probe"]["skip_ports"] == [22, 80]


def test_refresh_interval_minimum() -> None:
    assert validate({"refresh_interval": 0.01})["refresh_interval"] == 0.5


def test_non_mapping_file_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("- just\n- a list\n")
    assert load_config(path)["theme"] == "dracula"


def test_broken_yaml_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("refresh_interval: [unclosed\n")
    assert load_config(path)["refresh_interval"] == 2.0


def test_env_var_path(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "env.yaml"
    path.write_text("theme: nord\n")
    monkeypatch.setenv("LIRTS_CONFIG", str(path))
    assert resolve_config_path() == path
    assert load_config()["theme"] == "nord"


def test_save_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "c.yaml"
    cfg = load_config(path)
    cfg["theme"] = "monokai"
    assert save_config(cfg)
    data = yaml.safe_load(path.read_text())
    assert data["theme"] == "monokai"
    assert "_path" not in data


def test_deep_merge_does_not_mutate() -> None:
    base = {"a": {"b": 1, "c": 2}, "d": [1]}
    out = deep_merge(base, {"a": {"b": 9}, "d": [2]})
    assert out == {"a": {"b": 9, "c": 2}, "d": [2]}
    assert base["a"]["b"] == 1


def test_mask_env() -> None:
    env = {"DB_PASSWORD": "hunter2", "APP_ENV": "local", "MY_TOKEN": "x"}
    masked = mask_env(env, ["PASSWORD", "token"])
    assert masked == {"DB_PASSWORD": "********", "APP_ENV": "local", "MY_TOKEN": "********"}
    assert is_masked("SECRET_KEY", ["secret"])


def test_render_default_config_is_valid_yaml() -> None:
    text = render_default_config()
    assert text.startswith("# lirts configuration")
    assert yaml.safe_load(text)["refresh_interval"] == 2.0


def test_state_dir_uses_xdg(tmp_path: Path) -> None:
    assert str(cfgmod.state_dir()).startswith(str(tmp_path / "state"))


def test_legacy_default_columns_are_upgraded(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        "columns: [port, state, src, service, process, pids, cpu, mem, conns, activity, uptime, status]\n"
    )
    assert "project" in load_config(path)["columns"]
    path.write_text("columns: [port, service]\n")
    assert load_config(path)["columns"] == ["port", "service"]  # a custom list is kept


def test_obsolete_keys_are_ignored_and_reported_never_written(tmp_path: Path) -> None:
    from lirts.config import CONFIG_VERSION, outdated_reason, strip_obsolete

    path = tmp_path / "c.yaml"
    text = (
        "version: 2\nrefresh_interval: 5\nhealth:\n  enabled: true\n  interval: 15\n"
        "  timeout: 2.5\n  overrides: {8080: /st}\nreservations: {scan: true}\n"
        "aliases: {3000: Web}\n"
    )
    path.write_text(text)
    cfg = load_config(path)
    assert "enabled" not in cfg["health"] and "interval" not in cfg["health"]
    assert "reservations" not in cfg
    assert cfg["health"]["timeout"] == 2.5 and cfg["health"]["overrides"] == {8080: "/st"}
    assert cfg["aliases"] == {3000: "Web"}
    assert cfg["_obsolete_keys"] == ["health.enabled", "health.interval", "reservations"]
    assert cfg["_file_version"] == 2
    reason = outdated_reason(cfg)
    assert reason and f"schema 2 < {CONFIG_VERSION}" in reason and "health.enabled" in reason
    assert path.read_text() == text  # never rewritten by itself
    assert strip_obsolete({"version": 3, "theme": "x"}) == ({"theme": "x"}, [])
    assert outdated_reason(load_config(tmp_path / "missing.yaml")) is None


def test_config_upgrade_is_manual(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from lirts import cli
    from lirts.config import CONFIG_VERSION

    path = tmp_path / "c.yaml"
    path.write_text("version: 2\nhealth:\n  enabled: true\n")
    runner = CliRunner()
    result = runner.invoke(cli.app, ["config", "upgrade", "--config", str(path)])
    assert result.exit_code == 0, result.output
    assert "Upgraded" in result.output and "health.enabled" in result.output
    cfg = load_config(path)
    assert cfg["_file_version"] == CONFIG_VERSION and cfg["_obsolete_keys"] == []
    assert "enabled" not in cfg["health"]
    result = runner.invoke(cli.app, ["config", "upgrade", "--config", str(path)])
    assert "already" in result.output
