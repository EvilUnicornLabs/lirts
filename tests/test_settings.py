from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lirts.config import (
    CONFIG_VERSION,
    DEFAULT_CONFIG,
    load_config,
    render_default_config,
    save_config,
)
from lirts.settings import SETTINGS, SettingKind, coerce, format_value, get_path, set_path


def _setting(path: str):
    return next(s for s in SETTINGS if s.path == path)


def test_paths_and_formatting() -> None:
    cfg = {"a": {"b": 1}}
    assert (
        get_path(cfg, "a.b") == 1
        and get_path(cfg, "a.x") is None
        and get_path(cfg, "a.b.c") is None
    )
    set_path(cfg, path="a.c.d", value=True)
    assert cfg["a"]["c"]["d"] is True
    assert format_value(_setting("show_udp"), True) == "on"
    assert format_value(_setting("columns"), ["port", "cpu"]) == "port, cpu"
    assert format_value(_setting("refresh_interval"), 2.0) == "2"
    assert format_value(_setting("kubernetes.namespace"), None) == "(default)"
    assert format_value(_setting("kubernetes.enabled"), True) == "true"


def test_coerce() -> None:
    assert (
        coerce(_setting("show_udp"), "yes") is True and coerce(_setting("show_udp"), "off") is False
    )
    with pytest.raises(ValueError):
        coerce(_setting("show_udp"), "maybe")
    assert coerce(_setting("refresh_interval"), "3") == 3.0
    with pytest.raises(ValueError):
        coerce(_setting("refresh_interval"), "0.1")
    assert coerce(_setting("insights.restart_warning"), "4") == 4
    assert coerce(_setting("kubernetes.enabled"), "true") is True
    assert coerce(_setting("kubernetes.enabled"), "auto") == "auto"
    with pytest.raises(ValueError):
        coerce(_setting("kubernetes.enabled"), "sometimes")
    assert coerce(_setting("columns"), "port, cpu") == ["port", "cpu"]
    with pytest.raises(ValueError):
        coerce(_setting("columns"), "port, bogus")
    assert coerce(_setting("kubernetes.namespace"), "  ") is None


def test_every_setting_has_a_default() -> None:
    for setting in SETTINGS:
        assert (
            setting.path == "theme"
            or get_path(DEFAULT_CONFIG, setting.path) is not None
            or setting.kind == SettingKind.TEXT
        ), setting.path


def test_config_version_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    cfg = load_config(path)
    assert cfg["_exists"] is False and cfg["_file_version"] == 0
    assert save_config(cfg)
    data = yaml.safe_load(path.read_text())
    assert data["version"] == CONFIG_VERSION and next(iter(data)) == "version"
    again = load_config(path)
    assert again["_exists"] and again["_file_version"] == CONFIG_VERSION
    assert "version" not in {k for k in again if not k.startswith("_")}  # not a setting
    assert yaml.safe_load(render_default_config())["version"] == CONFIG_VERSION
    path.write_text("theme: nord\n")
    assert load_config(path)["_file_version"] == 0  # older file without a version


async def test_settings_screen(tmp_path: Path, monkeypatch) -> None:
    from textual.widgets import DataTable, TabbedContent

    from lirts.settings import SETTING_TABS, settings_on_tab
    from lirts.tui.screens import ColumnsScreen, SettingsScreen
    from tests.conftest import FakeEngine, LirtsApp, wait_rows
    from tests.conftest import load_config as _lc

    cfg = _lc(tmp_path / "c.yaml")
    cfg["refresh_interval"] = 60.0
    app = LirtsApp(FakeEngine(), cfg)  # type: ignore[arg-type]  # a fake stands in for Engine
    async with app.run_test(size=(170, 45)) as pilot:
        table = await wait_rows(app, pilot)
        await pilot.press("comma")
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        tabs = app.screen.query_one("#settings-tabs", TabbedContent)
        assert [str(pane.id) for pane in tabs.query("TabPane")] == [
            "settings-tab-general",
            "settings-tab-layout",
            "settings-tab-collection",
            "settings-tab-memory",
            "settings-tab-insights",
        ]
        assert tabs.active == "settings-tab-general"
        assert sum(
            app.screen.query_one(f"#settings-{name.lower()}", DataTable).row_count
            for name in SETTING_TABS
        ) == len(SETTINGS)
        # → from the General list moves one tab right
        await pilot.press("right")
        await pilot.pause()
        assert tabs.active == "settings-tab-layout"
        layout = app.screen.query_one("#settings-layout", DataTable)
        paths = [s.path for s in settings_on_tab("Layout")]
        # toggle group_by_stack live
        layout.move_cursor(row=paths.index("group_by_stack"))
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.grouped is True and app.config["group_by_stack"] is True
        assert app.screen.dirty
        # columns picker: drop "state"
        layout.move_cursor(row=paths.index("columns"))
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ColumnsScreen)
        cols = app.screen.query_one("#columns-table", DataTable)
        cols.move_cursor(row=1)  # state
        await pilot.pause()
        await pilot.press("space", "enter")
        await pilot.pause()
        assert (
            "state" not in app.config["columns"]
            and [c.key for c in app.columns] == app.config["columns"]
        )
        # save
        await pilot.press("s")
        await pilot.pause()
        saved = yaml.safe_load((tmp_path / "c.yaml").read_text())
        assert (
            saved["group_by_stack"] is True
            and "state" not in saved["columns"]
            and saved["version"] == CONFIG_VERSION
        )
        assert not app.screen.dirty
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, SettingsScreen)
        assert table.row_count >= 3


def test_every_setting_is_on_exactly_one_tab() -> None:
    from lirts.settings import SETTING_TABS, settings_on_tab, tab_for

    assert SETTING_TABS == ("General", "Layout", "Collection", "Memory", "Insights")
    assert tab_for(_setting("theme")) == "Layout"
    assert tab_for(_setting("ui.style")) == "Layout"
    assert tab_for(_setting("cli.force_color")) == "General"
    assert tab_for(_setting("docker.enabled")) == "Collection"
    assert tab_for(_setting("topology.days")) == "Memory"
    assert tab_for(_setting("insights.pattern_days")) == "Memory"
    assert tab_for(_setting("thresholds.cpu.red")) == "Insights"
    on_tabs = [s.path for tab in SETTING_TABS for s in settings_on_tab(tab)]
    assert sorted(on_tabs) == sorted(s.path for s in SETTINGS)


async def _open_layout_row(app, pilot, path: str):
    """Open Settings, switch to the Layout tab and put the cursor on one setting's row."""
    from textual.widgets import DataTable

    from lirts.settings import settings_on_tab

    await pilot.press("comma")
    await pilot.pause()
    await pilot.press("right")  # General -> Layout
    await pilot.pause()
    layout = app.screen.query_one("#settings-layout", DataTable)
    layout.move_cursor(row=[s.path for s in settings_on_tab("Layout")].index(path))
    await pilot.pause()
    return layout


async def test_value_column_cuts_a_long_value_with_an_ellipsis(tmp_path: Path) -> None:
    from lirts.tui.screens import SettingsScreen
    from tests.conftest import FakeEngine, LirtsApp, wait_rows
    from tests.conftest import load_config as _lc

    cfg = _lc(tmp_path / "c.yaml")
    cfg["refresh_interval"] = 60.0
    app = LirtsApp(FakeEngine(), cfg)  # type: ignore[arg-type]  # a fake stands in for Engine
    async with app.run_test(size=(170, 45)) as pilot:
        await wait_rows(app, pilot)
        layout = await _open_layout_row(app, pilot, "columns")
        assert isinstance(app.screen, SettingsScreen)
        shown = str(layout.get_cell("columns", "value"))
        assert len(", ".join(app.config["columns"])) > 28  # the value itself is far too long
        assert len(shown) == 28 and shown.endswith("…")
        assert shown.startswith("port, state, src, project")
        # a short value is left alone, and a restart-only setting keeps its marker
        assert str(layout.get_cell("ui.style", "value")) == "classic"
        assert str(layout.get_cell("ui.truecolor", "value")) == "auto  (restart)"


async def test_arrow_on_the_theme_row_only_switches_the_tab(tmp_path: Path) -> None:
    from textual.widgets import TabbedContent

    from lirts.tui.screens import SettingsScreen
    from tests.conftest import FakeEngine, LirtsApp, wait_rows
    from tests.conftest import load_config as _lc

    cfg = _lc(tmp_path / "c.yaml")
    cfg["refresh_interval"] = 60.0
    app = LirtsApp(FakeEngine(), cfg)  # type: ignore[arg-type]  # a fake stands in for Engine
    async with app.run_test(size=(170, 45)) as pilot:
        await wait_rows(app, pilot)
        opened_with = str(app.theme)
        await _open_layout_row(app, pilot, "theme")
        await pilot.press("right")
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        tabs = app.screen.query_one("#settings-tabs", TabbedContent)
        assert tabs.active == "settings-tab-collection"
        assert str(app.theme) == opened_with and not app.screen.dirty
        await pilot.press("left")
        await pilot.pause()
        assert tabs.active == "settings-tab-layout"
        assert str(app.theme) == opened_with and not app.screen.dirty


async def test_theme_picker_previews_while_moving_and_escape_restores(tmp_path: Path) -> None:
    from lirts.tui.screens import PickerScreen, SettingsScreen
    from tests.conftest import FakeEngine, LirtsApp, wait_rows
    from tests.conftest import load_config as _lc

    cfg = _lc(tmp_path / "c.yaml")
    cfg["refresh_interval"] = 60.0
    app = LirtsApp(FakeEngine(), cfg)  # type: ignore[arg-type]  # a fake stands in for Engine
    async with app.run_test(size=(170, 45)) as pilot:
        await wait_rows(app, pilot)
        opened_with = str(app.theme)
        await _open_layout_row(app, pilot, "theme")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, PickerScreen)
        assert app.screen.choices == sorted(app.available_themes)
        assert app.screen.current == opened_with
        await pilot.press("down")
        await pilot.pause()
        previewed = str(app.theme)
        assert previewed != opened_with
        assert app.config["theme"] == opened_with  # a preview is not a choice
        await pilot.press("j")  # j moves like the down arrow
        await pilot.pause()
        assert str(app.theme) not in (opened_with, previewed)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        assert str(app.theme) == opened_with and not app.screen.dirty


async def test_theme_picker_enter_applies_and_saving_keeps_it(tmp_path: Path) -> None:
    from lirts.tui.screens import PickerScreen, SettingsScreen
    from tests.conftest import FakeEngine, LirtsApp, wait_rows
    from tests.conftest import load_config as _lc

    cfg = _lc(tmp_path / "c.yaml")
    cfg["refresh_interval"] = 60.0
    app = LirtsApp(FakeEngine(), cfg)  # type: ignore[arg-type]  # a fake stands in for Engine
    async with app.run_test(size=(170, 45)) as pilot:
        await wait_rows(app, pilot)
        opened_with = str(app.theme)
        layout = await _open_layout_row(app, pilot, "theme")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, PickerScreen)
        await pilot.press("down", "enter")
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        picked = str(app.theme)
        assert picked != opened_with
        assert app.config["theme"] == picked and app.screen.dirty
        assert str(layout.get_cell("theme", "value")) == picked
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert str(app.theme) == picked
        assert yaml.safe_load((tmp_path / "c.yaml").read_text())["theme"] == picked


async def test_glyph_picker_draws_a_preview_line_for_every_set(tmp_path: Path) -> None:
    from textual.widgets import OptionList

    from lirts.tui.screens import PickerScreen
    from tests.conftest import FakeEngine, LirtsApp, wait_rows
    from tests.conftest import load_config as _lc

    cfg = _lc(tmp_path / "c.yaml")
    cfg["refresh_interval"] = 60.0
    app = LirtsApp(FakeEngine(), cfg)  # type: ignore[arg-type]  # a fake stands in for Engine
    async with app.run_test(size=(170, 45)) as pilot:
        await wait_rows(app, pilot)
        await _open_layout_row(app, pilot, "ui.glyphs")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, PickerScreen)
        options = app.screen.query_one("#picker-options", OptionList)
        assert options.option_count == 3 and options.highlighted == 0  # block is the default
        lines = [str(options.get_option_at_index(i).prompt) for i in range(3)]
        assert [line.split()[0] for line in lines] == ["block", "braille", "demoscene"]
        assert lines[0].startswith("block         ")  # the name is padded, the preview follows
        assert "▂▄▆█" in lines[0] and "██████░░░" in lines[0]
        assert "⣀⣤⣶⣿" in lines[1] and "━━━━━━───" in lines[1]
        assert "░▒▓█" in lines[2] and "██████▒▒▒" in lines[2]
        assert all(line.endswith("○ ● ◆") for line in lines)  # frontend, backend, database
        await pilot.press("escape")
        await pilot.pause()
        assert app.config["ui"]["glyphs"] == "block"


async def test_style_picker_lists_the_styles_and_applies_the_pick(tmp_path: Path) -> None:
    from textual.widgets import OptionList

    from lirts.tui.screens import PickerScreen, SettingsScreen
    from tests.conftest import FakeEngine, LirtsApp, wait_rows
    from tests.conftest import load_config as _lc

    cfg = _lc(tmp_path / "c.yaml")
    cfg["refresh_interval"] = 60.0
    app = LirtsApp(FakeEngine(), cfg)  # type: ignore[arg-type]  # a fake stands in for Engine
    async with app.run_test(size=(170, 45)) as pilot:
        await wait_rows(app, pilot)
        layout = await _open_layout_row(app, pilot, "ui.style")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, PickerScreen)
        options = app.screen.query_one("#picker-options", OptionList)
        assert [
            str(options.get_option_at_index(i).prompt) for i in range(options.option_count)
        ] == [
            "classic",
            "compact",
            "tight",
            "cracktro",
            "phosphor",
        ]
        assert options.highlighted == 0  # the value in use is where the cursor starts
        await pilot.press("k")  # k moves like the up arrow: wraps to the last style
        await pilot.pause()
        assert options.highlighted == 4
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        assert app.config["ui"]["style"] == "phosphor" and app.screen.dirty
        assert str(layout.get_cell("ui.style", "value")) == "phosphor"


def test_sources_setting() -> None:
    s = _setting("sources")
    assert s.kind == SettingKind.MULTI and coerce(s, "ssh, local") == ["local", "ssh"]
    assert format_value(s, ["local", "docker"]) == "local, docker"
    with pytest.raises(ValueError):
        coerce(s, "mars")
    with pytest.raises(ValueError):
        coerce(s, "")
